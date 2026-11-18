"""Logic for the AX_sample_color_from_image HDA.

The HDA only stores the UI. Parm callbacks call sample_image_colors_to_ramp(),
the Preview button calls show_image_preview_with_markers(). Both use
sample_colors() so the preview markers are the exact pixels written to the ramp.
"""

import colorsys
import os
import random

import hou
from PIL import Image, ImageQt
from PySide6 import QtCore, QtGui, QtWidgets

CANDIDATE_COUNT = 10000
DISPLAY_MAX_SIZE = 2048  # longest side of the preview pixmap

# path -> PIL RGB image. The Reload Image button drops an entry; clear_cache() drops all.
_image_cache = {}


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _parm_value(node, name, as_string=False):
    """Evaluate a parm, or return None if this HDA instance doesn't have it."""
    parm = node.parm(name)
    if parm is None:
        return None
    return parm.evalAsString() if as_string else parm.eval()


def _read_settings(node):
    path = _parm_value(node, "image_path") or ""
    samples = _parm_value(node, "samples")
    seed = _parm_value(node, "seed")
    mode = _parm_value(node, "filter_mode", as_string=True)
    return {
        "image_path": os.path.normpath(hou.text.expandString(path)) if path else "",
        "samples": max(1, int(samples if samples is not None else 6)),
        "seed": int(seed if seed is not None else 0),
        "filter_mode": mode or "random",
    }


def _load_image(path):
    """Load an image as RGB, cached by path. reload_image() re-reads it from disk."""
    if not path or not os.path.isfile(path):
        raise hou.NodeError(f"Image not found:\n{path}")
    img = _image_cache.get(path)
    if img is None:
        try:
            img = Image.open(path).convert("RGB")
        except Exception as e:
            raise hou.NodeError(f"Failed to load image:\n{path}\n{e}")
        _image_cache[path] = img
    return img


def clear_cache():
    """Drop all cached images."""
    _image_cache.clear()


def reload_image(hda_node):
    """Reload button: drop this node's image from the cache and rebuild the ramp."""
    _image_cache.pop(_read_settings(hda_node)["image_path"], None)
    sample_image_colors_to_ramp(hda_node)


def _passes_filter(rgb, mode):
    h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in rgb))
    if mode == "bright":
        return v > 0.7
    if mode == "dark":
        return v < 0.3
    if mode == "muted":
        return s < 0.3
    if mode == "deep":
        return s > 0.6 and v < 0.5
    return True


def _report_warnings(node, warnings):
    """Show each unique soft problem once.

    node.addWarning() only works while a node cooks, and this HDA never
    cooks anything, so the messages go to one dialog (or stdout without a UI).
    """
    unique = list(dict.fromkeys(warnings))
    if not unique:
        return
    text = f"{node.path()}:\n" + "\n".join(unique)
    if hou.isUIAvailable():
        hou.ui.displayMessage(text, severity=hou.severityType.Warning)
    else:
        print("Warning: " + text)


# -----------------------------------------------------------------------------
# Core logic
# -----------------------------------------------------------------------------

def sample_colors(image, samples, seed, filter_mode):
    """Pick pixels from an image.

    Returns (picked, warnings). picked is a list of (x, y, (r, g, b)) with 0-255
    channels, sorted from dark to bright. The result depends only on the
    arguments, so the ramp and the preview always agree.
    """
    rng = random.Random(seed)
    width, height = image.size
    warnings = []

    candidates = []
    for _ in range(CANDIDATE_COUNT):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        rgb = image.getpixel((x, y))
        if _passes_filter(rgb, filter_mode):
            candidates.append((x, y, rgb))

    if not candidates:
        warnings.append(f"No pixels matched filter '{filter_mode}'. Ramp left unchanged.")
        return [], warnings

    if len(candidates) < samples:
        warnings.append(
            f"Only {len(candidates)} pixels matched filter '{filter_mode}'; "
            f"the ramp has fewer than {samples} keys."
        )

    picked = rng.sample(candidates, min(samples, len(candidates)))
    picked.sort(key=lambda item: sum(item[2]))
    return picked, warnings


def sample_image_colors_to_ramp(hda_node):
    """Rebuild the HDA's ramp parm from the current settings."""
    settings = _read_settings(hda_node)
    if not settings["image_path"]:
        return  # nothing picked yet; not an error
    image = _load_image(settings["image_path"])
    picked, warnings = sample_colors(
        image, settings["samples"], settings["seed"], settings["filter_mode"]
    )

    ramp_parm = hda_node.parm("ramp")
    if picked and ramp_parm is not None:
        colors = [tuple(c / 255.0 for c in rgb) for _, _, rgb in picked]
        count = len(colors)
        positions = [i / (count - 1) if count > 1 else 0.5 for i in range(count)]
        ramp_parm.set(hou.Ramp([hou.rampBasis.Linear] * count, positions, colors))

    _report_warnings(hda_node, warnings)


# -----------------------------------------------------------------------------
# Preview window
# -----------------------------------------------------------------------------

_preview_windows = {}


class ImagePreview(QtWidgets.QDialog):
    def __init__(self, hda_node):
        super().__init__(hou.qt.mainWindow())
        self.setWindowTitle(f"Image Preview: {hda_node.path()}")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(500, 500)

        self.hda_node = hda_node
        self.session_id = hda_node.sessionId()
        self.image = None
        self.display_pixmap = None
        self.picked = []
        self.last_settings = None
        self.message = ""

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QtWidgets.QLabel()
        self.label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        # Ignored lets the window shrink below the pixmap size.
        self.label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Ignored)
        layout.addWidget(self.label)

        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.check_node_params)
        self.timer.start(300)
        self.destroyed.connect(lambda *_: _preview_windows.pop(self.session_id, None))

        self.check_node_params()

    def resizeEvent(self, event):
        self.draw_preview()
        super().resizeEvent(event)

    def check_node_params(self):
        try:
            settings = _read_settings(self.hda_node)
        except hou.ObjectWasDeleted:
            self.timer.stop()
            self.close()
            return
        if settings == self.last_settings:
            return
        self.last_settings = settings
        previous = self.image
        self.image, self.picked, self.message = None, [], ""
        try:
            self.image = _load_image(settings["image_path"])
            if self.image is not previous:
                self._build_display_pixmap()
            self.picked, warnings = sample_colors(
                self.image, settings["samples"], settings["seed"], settings["filter_mode"]
            )
            self.message = "\n".join(warnings)
        except hou.NodeError as e:
            self.message = str(e)
        self.draw_preview()

    def _build_display_pixmap(self):
        """Convert the image to a QPixmap once, capped at DISPLAY_MAX_SIZE.

        Converting a full-resolution image on every resize is slow
        (~140 ms for 8K), so resizes only scale this smaller pixmap.
        """
        small = self.image.copy()
        small.thumbnail((DISPLAY_MAX_SIZE, DISPLAY_MAX_SIZE))
        self.display_pixmap = QtGui.QPixmap.fromImage(ImageQt.ImageQt(small))

    def draw_preview(self):
        if self.image is None:
            self.label.setText(self.message or "No Image Loaded")
            return
        w, h = self.label.width(), self.label.height()
        if w <= 0 or h <= 0:
            return

        pixmap = self.display_pixmap.scaled(
            w, h,
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        scale = pixmap.width() / self.image.size[0]
        canvas = pixmap.toImage()

        painter = QtGui.QPainter(canvas)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        outline = QtGui.QPen(QtGui.QColor(255, 255, 255))
        outline.setWidth(2)
        for x, y, rgb in self.picked:
            center = QtCore.QPoint(int(x * scale), int(y * scale))
            painter.setPen(outline)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 6, 6)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(*rgb))
            painter.drawEllipse(center, 4, 4)
        painter.end()

        self.label.setPixmap(QtGui.QPixmap.fromImage(canvas))
        self.label.setToolTip(self.message)


def show_image_preview_with_markers(hda_node):
    """Open (or raise) the preview window for this node."""
    window = _preview_windows.get(hda_node.sessionId())
    if window is not None and window.isVisible():
        window.raise_()
        window.activateWindow()
        return
    window = ImagePreview(hda_node)
    _preview_windows[hda_node.sessionId()] = window
    window.show()
