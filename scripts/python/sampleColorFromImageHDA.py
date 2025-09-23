import hou
import os
import random
from PIL import Image, ImageQt, ImageDraw
import colorsys
from PySide2 import QtWidgets, QtGui, QtCore
 
def sample_image_colors_to_ramp(
    hda_node,
    image_parm="image_path",
    ramp_parm="ramp",
    samples_parm="samples",
    seed_parm="seed",
    filter_parm="filter_mode"
):
    # Load parameters
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

    # Sample a subset of pixels (fast)
    candidate_count = 10000
    sampled_pixels = [
        image.getpixel((random.randint(0, width - 1), random.randint(0, height - 1)))
        for _ in range(candidate_count)
    ]

    def rgb_to_hsv(rgb):
        return colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))

    def passes_filter(rgb):
        h, s, v = rgb_to_hsv(rgb)
        if filter_mode == "bright":
            return v > 0.7
        elif filter_mode == "dark":
            return v < 0.3
        elif filter_mode == "muted":
            return s < 0.3
        elif filter_mode == "deep":
            return s > 0.6 and v < 0.5
        else:
            return True

    filtered = [rgb for rgb in sampled_pixels if passes_filter(rgb)]

    if not filtered:
        hou.ui.displayMessage(f"No pixels matched filter '{filter_mode}'.")
        return

    # Final color pick
    final_colors = random.sample(filtered, min(num_samples, len(filtered)))
    final_colors = [[c / 255.0 for c in color] for color in final_colors]
    final_colors.sort(key=lambda rgb: sum(rgb))

    # Build ramp
    positions = [float(i) / (len(final_colors) - 1) if len(final_colors) > 1 else 0.5 for i in range(len(final_colors))]
    bases = [hou.rampBasis.Linear] * len(final_colors)
    ramp = hou.Ramp(bases, positions, final_colors)

    # Apply ramp
    ramp_parm = hda_node.parm(ramp_parm)
    if ramp_parm:
        ramp_parm.set(ramp)

preview_window_instance = None  # Place this at the top of your PythonModule file

def show_image_preview_with_markers(hda_node):
    global preview_window_instance

    # If already open, just bring it to front or refresh
    if preview_window_instance is not None and preview_window_instance.isVisible():
        preview_window_instance.raise_()
        preview_window_instance.activateWindow()
        return

    class ImagePreview(QtWidgets.QDialog):
        def __init__(self, hda_node):
            super().__init__()
            self.setWindowTitle("Image Preview with Color Markers")
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.WindowStaysOnTopHint)
            self.setMinimumSize(400, 400)
            self.hda_node = hda_node

            self.label = QtWidgets.QLabel()
            self.label.setAlignment(QtCore.Qt.AlignCenter)

            layout = QtWidgets.QVBoxLayout(self)
            layout.addWidget(self.label)

            # Store last parameter values to detect changes
            self.last_params = {
                "image_path": None,
                "seed": None,
                "samples": None,
                "filter_mode": None,
            }

            self.update_timer = QtCore.QTimer(self)
            self.update_timer.timeout.connect(self.check_for_updates)
            self.update_timer.start(300)  # check every 300ms

            self.destroyed.connect(self.cleanup)

            # Initial update
            self.update_preview()

        def cleanup(self):
            global preview_window_instance
            preview_window_instance = None

        def check_for_updates(self):
            try:
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
                    "image_path": image_path,
                    "seed": seed,
                    "samples": samples,
                    "filter_mode": filter_mode,
                })
                self.update_preview()

        def update_preview(self):
            image_path = self.last_params["image_path"]
            seed = self.last_params["seed"]
            samples = self.last_params["samples"]
            filter_mode = self.last_params["filter_mode"]

            if not image_path or not os.path.exists(image_path):
                return

            try:
                original_image = Image.open(image_path).convert("RGB")
            except Exception:
                return

            width, height = original_image.size
            random.seed(seed)

            # Sample pixels
            candidate_count = 10000
            sampled_pixels = [
                original_image.getpixel((random.randint(0, width - 1), random.randint(0, height - 1)))
                for _ in range(candidate_count)
            ]

            def rgb_to_hsv(rgb):
                return colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))

            def passes_filter(rgb):
                h, s, v = rgb_to_hsv(rgb)
                if filter_mode == "bright":
                    return v > 0.7
                elif filter_mode == "dark":
                    return v < 0.3
                elif filter_mode == "muted":
                    return s < 0.3
                elif filter_mode == "deep":
                    return s > 0.6 and v < 0.5
                return True

            filtered = [rgb for rgb in sampled_pixels if passes_filter(rgb)]
            if not filtered:
                return

            sampled_positions = []
            for _ in range(samples):
                x = random.randint(0, width - 1)
                y = random.randint(0, height - 1)
                color = original_image.getpixel((x, y))
                sampled_positions.append((x, y, color))

            # Convert image for display
            qim = ImageQt.ImageQt(original_image)
            pixmap = QtGui.QPixmap.fromImage(qim)

            # Scale the image to fit the label
            scaled_pixmap = pixmap.scaled(
                self.label.width(),
                self.label.height(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation
            )

            # Compute scale factor and offset
            scaled_width = scaled_pixmap.width()
            scaled_height = scaled_pixmap.height()
            x_scale = scaled_width / width
            y_scale = scaled_height / height
            scale = min(x_scale, y_scale)

            x_offset = (self.label.width() - scaled_width) // 2
            y_offset = (self.label.height() - scaled_height) // 2

            # Convert to QImage for painting
            image_with_overlay = scaled_pixmap.toImage()
            painter = QtGui.QPainter()
            painter.begin(image_with_overlay)
            painter.setRenderHint(QtGui.QPainter.Antialiasing)

            outer_radius = 6  # Absolute size
            inner_radius = 4

            for x, y, rgb in sampled_positions:
                sx = int(x * scale)
                sy = int(y * scale)
                color = QtGui.QColor(*rgb)

                # Outer white ring
                pen = QtGui.QPen(QtGui.QColor(255, 255, 255))
                pen.setWidth(2)
                painter.setPen(pen)
                painter.setBrush(QtCore.Qt.NoBrush)
                painter.drawEllipse(sx - outer_radius, sy - outer_radius, outer_radius * 2, outer_radius * 2)

                # Inner fill
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(sx - inner_radius, sy - inner_radius, inner_radius * 2, inner_radius * 2)

            painter.end()

            final_pixmap = QtGui.QPixmap.fromImage(image_with_overlay)
            self.label.setPixmap(final_pixmap)



    # Create and show the window
    preview_window_instance = ImagePreview(hda_node)
    preview_window_instance.show()




def ui():
    node = hou.pwd()
    sample_image_colors_to_ramp(node)
