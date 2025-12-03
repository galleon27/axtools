import hou
import os
import random
from PIL import Image, ImageQt, ImageDraw
import colorsys
from PySide6 import QtWidgets, QtGui, QtCore

# -----------------------------------------------------------------------------
# CORE LOGIC (No UI)
# -----------------------------------------------------------------------------

def sample_image_colors_to_ramp(
    hda_node,
    image_parm="image_path",
    ramp_parm="ramp",
    samples_parm="samples",
    seed_parm="seed",
    filter_parm="filter_mode"
):
    # (Same code as before for the ramp generation logic...)
    image_path = os.path.expandvars(hda_node.parm(image_parm).eval())
    num_samples = max(1, int(hda_node.parm(samples_parm).eval()))
    seed = int(hda_node.parm(seed_parm).eval())
    filter_mode = hda_node.parm(filter_parm).evalAsString()

    if not os.path.exists(image_path):
        hou.ui.displayMessage(f"Image not found:\n{image_path}")
        return

    try:
        image = Image.open(image_path).convert("RGB")
    except Exception as e:
        hou.ui.displayMessage(f"Failed to load image:\n{str(e)}")
        return

    width, height = image.size
    random.seed(seed)

    candidate_count = 10000
    sampled_pixels = [
        image.getpixel((random.randint(0, width - 1), random.randint(0, height - 1)))
        for _ in range(candidate_count)
    ]

    def rgb_to_hsv(rgb):
        return colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))

    def passes_filter(rgb):
        h, s, v = rgb_to_hsv(rgb)
        if filter_mode == "bright": return v > 0.7
        elif filter_mode == "dark": return v < 0.3
        elif filter_mode == "muted": return s < 0.3
        elif filter_mode == "deep": return s > 0.6 and v < 0.5
        else: return True

    filtered = [rgb for rgb in sampled_pixels if passes_filter(rgb)]

    if not filtered:
        hou.ui.displayMessage(f"No pixels matched filter '{filter_mode}'.")
        return

    final_colors = random.sample(filtered, min(num_samples, len(filtered)))
    final_colors = [[c / 255.0 for c in color] for color in final_colors]
    final_colors.sort(key=lambda rgb: sum(rgb))

    positions = [float(i) / (len(final_colors) - 1) if len(final_colors) > 1 else 0.5 for i in range(len(final_colors))]
    bases = [hou.rampBasis.Linear] * len(final_colors)
    ramp = hou.Ramp(bases, positions, final_colors)

    ramp_parm_node = hda_node.parm(ramp_parm)
    if ramp_parm_node:
        ramp_parm_node.set(ramp)


# -----------------------------------------------------------------------------
# UI LOGIC (Preview Window)
# -----------------------------------------------------------------------------

preview_window_instance = None

def show_image_preview_with_markers(hda_node):
    global preview_window_instance

    if preview_window_instance is not None and preview_window_instance.isVisible():
        preview_window_instance.raise_()
        preview_window_instance.activateWindow()
        return

    class ImagePreview(QtWidgets.QDialog):
        def __init__(self, hda_node):
            super().__init__()
            self.setWindowTitle("Image Preview")
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowType.WindowStaysOnTopHint)
            self.resize(500, 500) # Default starting size
            
            self.hda_node = hda_node

            # Data containers
            self.cached_image_path = None
            self.original_image = None # PIL Image
            self.sampled_data = []     # List of (x, y, rgb_tuple)

            # UI Setup
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0) # Edge to edge image
            
            self.label = QtWidgets.QLabel()
            self.label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            
            # CRITICAL: This allows the label to shrink. Otherwise, the label will 
            # lock the window size to the image size and prevent shrinking.
            self.label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
            
            layout.addWidget(self.label)

            # State tracking
            self.last_params = {
                "image_path": None, "seed": None, "samples": None, "filter_mode": None,
            }

            self.update_timer = QtCore.QTimer(self)
            self.update_timer.timeout.connect(self.check_node_params)
            self.update_timer.start(300)

            self.destroyed.connect(self.cleanup)

            # Initial load
            self.check_node_params()

        def cleanup(self):
            global preview_window_instance
            preview_window_instance = None

        # ---------------------------------------------------------------------
        # Event Overrides
        # ---------------------------------------------------------------------
        
        def resizeEvent(self, event):
            """
            Triggered automatically by Qt when the user resizes the window.
            We just re-draw the current data to fit the new size.
            """
            self.draw_preview()
            super().resizeEvent(event)

        # ---------------------------------------------------------------------
        # Logic
        # ---------------------------------------------------------------------

        def check_node_params(self):
            """
            Checks Houdini node parameters to see if we need to reload the image/data.
            """
            try:
                # Use raw string for path to avoid evaluation issues if not needed, 
                # but eval() is usually safer for env vars.
                image_path = os.path.expandvars(self.hda_node.parm("image_path").eval())
                seed = int(self.hda_node.parm("seed").eval())
                samples = int(self.hda_node.parm("samples").eval())
                filter_mode = self.hda_node.parm("filter_mode").evalAsString()
            except hou.ObjectWasDeleted:
                self.update_timer.stop()
                self.close()
                return

            changed = (
                image_path != self.last_params["image_path"] or
                seed != self.last_params["seed"] or
                samples != self.last_params["samples"] or
                filter_mode != self.last_params["filter_mode"]
            )

            if changed:
                self.last_params.update({
                    "image_path": image_path, "seed": seed, 
                    "samples": samples, "filter_mode": filter_mode,
                })
                # Reload data and redraw
                self.process_image_data()
                self.draw_preview()

        def process_image_data(self):
            """
            Heavy lifting: Loads image from disk and calculates sample points.
            Only runs when parameters change, NOT when resizing window.
            """
            path = self.last_params["image_path"]
            seed = self.last_params["seed"]
            samples = self.last_params["samples"]
            filter_mode = self.last_params["filter_mode"]

            if not path or not os.path.exists(path):
                self.original_image = None
                self.sampled_data = []
                return

            try:
                # Store the original PIL image
                self.original_image = Image.open(path).convert("RGB")
            except Exception:
                self.original_image = None
                return

            width, height = self.original_image.size
            random.seed(seed)

            # 1. Filter candidates
            candidate_count = 10000
            candidates = []
            for _ in range(candidate_count):
                cx, cy = random.randint(0, width - 1), random.randint(0, height - 1)
                candidates.append((cx, cy, self.original_image.getpixel((cx, cy))))

            def rgb_to_hsv(rgb):
                return colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))

            def passes_filter(rgb):
                h, s, v = rgb_to_hsv(rgb)
                if filter_mode == "bright": return v > 0.7
                elif filter_mode == "dark": return v < 0.3
                elif filter_mode == "muted": return s < 0.3
                elif filter_mode == "deep": return s > 0.6 and v < 0.5
                return True

            filtered_candidates = [item for item in candidates if passes_filter(item[2])]

            # 2. Pick Final Sample Points
            # We store the coordinate (x,y) and color so we can draw it later
            # regardless of image scale.
            self.sampled_data = []
            if filtered_candidates:
                # We need random positions from the filtered set, but we want 
                # new random positions for the visualization to match the ramp logic
                # Ideally, we should visualize exactly what the ramp sees.
                # For visualization purposes, let's just pick 'samples' amount of random spots
                # that pass the filter to show where colors COULD come from.
                
                # To be accurate to the ramp logic:
                # The ramp logic grabs pixel values. To visualize *location*, 
                # we just grab random valid coordinates.
                for _ in range(samples):
                     # Randomly pick a valid pixel location from the filtered list
                     # (In a real scenario, you might want spatial distribution, 
                     # but here we pick from the pool of valid colors)
                     import random as rnd
                     chosen = rnd.choice(filtered_candidates)
                     self.sampled_data.append(chosen)

        def draw_preview(self):
            """
            Rendering: Scales current image to window size and paints markers.
            Runs on every Resize event.
            """
            if not self.original_image:
                self.label.setText("No Image Loaded")
                return

            # 1. Get current available size from the label/window
            # We use the label size, which adjusts with the window
            target_w = self.label.width()
            target_h = self.label.height()

            if target_w <= 0 or target_h <= 0:
                return

            # 2. Convert PIL to QPixmap
            qim = ImageQt.ImageQt(self.original_image)
            pixmap = QtGui.QPixmap.fromImage(qim)

            # 3. Scale Pixmap keeping Aspect Ratio
            # This does the math to fit the image inside the window
            scaled_pixmap = pixmap.scaled(
                target_w, target_h,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation
            )

            # 4. Calculate coordinate translation
            # We need to know where (0,0) of the image ended up relative to the label center
            orig_w, orig_h = self.original_image.size
            final_w = scaled_pixmap.width()
            final_h = scaled_pixmap.height()

            scale_factor = final_w / orig_w # Uniform scale because of KeepAspectRatio

            # 5. Prepare to Draw Markers
            # We draw onto the Scaled Pixmap so the markers are crisp 
            # (or we could draw on an overlay widget, but painting on the image is easier here)
            image_for_painting = scaled_pixmap.toImage()
            painter = QtGui.QPainter()
            painter.begin(image_for_painting)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

            outer_radius = 6
            inner_radius = 4

            # 6. Draw Dots
            for x_orig, y_orig, rgb in self.sampled_data:
                # Project original coordinates to scaled coordinates
                sx = int(x_orig * scale_factor)
                sy = int(y_orig * scale_factor)

                color = QtGui.QColor(*rgb)

                # White Outline
                pen = QtGui.QPen(QtGui.QColor(255, 255, 255))
                pen.setWidth(2)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawEllipse(
                    QtCore.QPoint(sx, sy), 
                    outer_radius, outer_radius
                )

                # Color Fill
                painter.setPen(QtCore.Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(
                    QtCore.QPoint(sx, sy), 
                    inner_radius, inner_radius
                )

            painter.end()

            # 7. Update Label
            self.label.setPixmap(QtGui.QPixmap.fromImage(image_for_painting))


    preview_window_instance = ImagePreview(hda_node)
    preview_window_instance.show()

def ui():
    node = hou.pwd()
    sample_image_colors_to_ramp(node)