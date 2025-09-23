import hou
import os
import colorsys
import random
from PIL import Image, ImageDraw
from functools import partial
from PySide2 import QtCore, QtGui, QtWidgets

def resolve_path(path):
    try:
        expanded = hou.expandString(path)
        return os.path.normpath(expanded)
    except Exception:
        return os.path.normpath(path)

def sample_image_colors(image_path, num_samples=9, mode="default", seed=1):
    random.seed(seed)
    img = Image.open(image_path).convert("RGB")
    width, height = img.size

    sample_count = max(300, num_samples * 10)
    sampled = []
    positions_sampled = []

    for _ in range(sample_count):
        x = random.randint(0, width - 1)
        y = random.randint(0, height - 1)
        r, g, b = img.getpixel((x, y))
        rgb = (r / 255.0, g / 255.0, b / 255.0)

        if mode != "random":
            h, s, v = colorsys.rgb_to_hsv(*rgb)
            if mode == "dark" and v >= 0.4:
                continue
            elif mode == "bright" and v <= 0.6:
                continue
            elif mode == "muted" and s >= 0.4:
                continue
            elif mode == "deep" and not (s > 0.6 and v < 0.5):
                continue

        sampled.append(rgb)
        positions_sampled.append((x, y))

    if not sampled:
        sampled = [(r / 255.0, g / 255.0, b / 255.0) for _ in range(num_samples)
                   for r, g, b in [img.getpixel((random.randint(0, width - 1), random.randint(0, height - 1)))]]
        positions_sampled = [(random.randint(0, width - 1), random.randint(0, height - 1)) for _ in range(num_samples)]

    unique = []
    unique_positions = []
    for i, c in enumerate(sampled):
        if not any(all(abs(a - b) < 0.05 for a, b in zip(c, u)) for u in unique):
            unique.append(c)
            unique_positions.append(positions_sampled[i])
        if len(unique) >= num_samples:
            break

    while len(unique) < num_samples:
        unique += unique
        unique_positions += unique_positions
    unique = unique[:num_samples]
    unique_positions = unique_positions[:num_samples]

    positions = [i / (num_samples - 1) if num_samples > 1 else 0.5 for i in range(num_samples)]
    return list(zip(positions, unique)), unique_positions

def create_color_ramp(colors):
    network = hou.ui.curDesktop().paneTabOfType(hou.paneTabType.NetworkEditor)
    if not network:
        raise Exception("No network editor found")

    parent = network.pwd()
    if not parent:
        raise Exception("Could not find parent context")

    existing = None
    for node in hou.selectedNodes():
        if node.type().name() == "color":
            existing = node
            break

    if existing:
        color_node = existing
    else:
        old = parent.node("image_color_ramp")
        if old:
            old.destroy()

        color_node = parent.createNode("color", "image_color_ramp")
        color_node.parm("colortype").set(3)

        hda_node = parent.parent()
        if hda_node and hda_node.inputs():
            input_node = hda_node.inputs()[0]
            if input_node:
                color_node.setInput(0, input_node)

        color_node.moveToGoodPosition()
        color_node.setDisplayFlag(True)
        color_node.setRenderFlag(True)
        color_node.setSelected(True)

    basis = [hou.rampBasis.Linear] * len(colors)
    positions = [p for p, c in colors]
    values = [c for p, c in colors]
    ramp = hou.Ramp(basis, positions, values)

    color_node.parm("ramp").set(ramp)
    return color_node

class ImageRampUI(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(ImageRampUI, self).__init__(parent or hou.ui.mainQtWindow())
        self.setWindowTitle("Image to Color Ramp")
        self.setMinimumWidth(400)

        self.image_path = ""
        self.num_samples = 9
        self.mode = "default"
        self.seed = 1
        self.sampled_colors = []
        self.sample_positions = []
        self.original_pixmap = None

        self.image_preview = QtWidgets.QLabel()
        self.image_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.image_preview.setScaledContents(False)
        self.image_preview.setMinimumSize(400, 400)  # Force 400x400 minimum
        self.image_preview.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        self.color_swatches_layout = QtWidgets.QHBoxLayout()

        self.build_ui()

    def build_ui(self):
        layout = QtWidgets.QVBoxLayout()

        self.slider_label = QtWidgets.QLabel("Number of Samples: 9")
        layout.addWidget(self.slider_label)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setMinimum(3)
        self.slider.setMaximum(20)
        self.slider.setValue(9)
        self.slider.valueChanged.connect(self.update_slider_label)
        layout.addWidget(self.slider)

        self.seed_label = QtWidgets.QLabel("Random Seed: 1")
        layout.addWidget(self.seed_label)

        self.seed_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.seed_slider.setMinimum(0)
        self.seed_slider.setMaximum(9999)
        self.seed_slider.setValue(1)
        self.seed_slider.valueChanged.connect(self.update_seed_label)
        layout.addWidget(self.seed_slider)

        mode_layout = QtWidgets.QHBoxLayout()
        self.mode_buttons = {}
        for label in ["Default", "Dark", "Bright", "Muted", "Deep", "Random"]:
            btn = QtWidgets.QPushButton(label)
            btn.clicked.connect(partial(self.set_mode, label.lower()))
            self.mode_buttons[label.lower()] = btn
            mode_layout.addWidget(btn)
        layout.addLayout(mode_layout)

        file_layout = QtWidgets.QHBoxLayout()
        file_btn = QtWidgets.QPushButton("Select Image")
        file_btn.clicked.connect(self.select_image)
        self.image_path_field = QtWidgets.QLineEdit()
        self.image_path_field.editingFinished.connect(self.handle_manual_path_input)
        file_layout.addWidget(file_btn)
        file_layout.addWidget(self.image_path_field)
        layout.addLayout(file_layout)

        layout.addWidget(self.image_preview)

        swatch_container = QtWidgets.QWidget()
        swatch_container.setLayout(self.color_swatches_layout)
        layout.addWidget(swatch_container)

        self.generate_btn = QtWidgets.QPushButton("Generate Ramp")
        self.generate_btn.clicked.connect(self.generate_ramp)
        layout.addWidget(self.generate_btn)

        self.setLayout(layout)

    def update_slider_label(self, value):
        self.num_samples = value
        self.slider_label.setText(f"Number of Samples: {value}")
        self.update_preview()

    def update_seed_label(self, value):
        self.seed = value
        self.seed_label.setText(f"Random Seed: {value}")
        self.update_preview()

    def set_mode(self, mode):
        self.mode = mode
        for m, btn in self.mode_buttons.items():
            btn.setStyleSheet("background: none")
        self.mode_buttons[mode].setStyleSheet("background-color: lightblue")
        self.update_preview()

    def select_image(self):
        path = hou.ui.selectFile(
            title="Select Image File",
            file_type=hou.fileType.Image,
            pattern="*.png *.jpg *.jpeg *.tif *.tiff *.exr"
        )
        if path:
            resolved = resolve_path(path)
            self.image_path = resolved
            self.image_path_field.setText(path)
            self.update_preview()

    def handle_manual_path_input(self):
        path = self.image_path_field.text().strip()
        if not path:
            return
        resolved = resolve_path(path)
        if not os.path.exists(resolved):
            hou.ui.displayMessage(f"File does not exist:\n{resolved}", severity=hou.severityType.Warning)
            return
        self.image_path = resolved
        self.update_preview()

    def update_preview(self):
        if not self.image_path:
            return
        try:
            img = Image.open(self.image_path).convert("RGB")
            self.sampled_colors, self.sample_positions = sample_image_colors(
                self.image_path, self.num_samples, self.mode, self.seed
            )

            img_qt = img.copy()
            max_width = 400
            max_height = 400
            img_qt.thumbnail((max_width, max_height), Image.LANCZOS)
            radius = 30
            stroke = 35
            scale_x = img_qt.width / img.width
            scale_y = img_qt.height / img.height

            draw = ImageDraw.Draw(img_qt)
            for (x, y), (_, rgb) in zip(self.sample_positions, self.sampled_colors):
                px = int(x * scale_x)
                py = int(y * scale_y)
                fill_color = tuple(int(c * 255) for c in rgb)
                draw.ellipse((px - stroke, py - stroke, px + stroke, py + stroke), fill="white")        # outer
                draw.ellipse((px - radius, py - radius, px + radius, py + radius), fill=fill_color)     # inner

            data = img_qt.tobytes("raw", "RGB")
            qimg = QtGui.QImage(data, img_qt.width, img_qt.height, QtGui.QImage.Format_RGB888)
            pixmap = QtGui.QPixmap.fromImage(qimg)
            self.original_pixmap = pixmap

            scaled_pixmap = pixmap.scaled(
                self.image_preview.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            )
            self.image_preview.setPixmap(scaled_pixmap)

            while self.color_swatches_layout.count():
                child = self.color_swatches_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            for _, color in self.sampled_colors:
                swatch = QtWidgets.QLabel()
                swatch.setFixedSize(24, 24)
                swatch.setStyleSheet(
                    f"background-color: rgb({int(color[0]*255)}, {int(color[1]*255)}, {int(color[2]*255)}); "
                    "border: 1px solid black;"
                )
                self.color_swatches_layout.addWidget(swatch)

        except Exception as e:
            hou.ui.displayMessage(f"Error loading preview: {e}", severity=hou.severityType.Error)

    def resizeEvent(self, event):
        super(ImageRampUI, self).resizeEvent(event)
        if self.original_pixmap:
            scaled_pixmap = self.original_pixmap.scaled(
                self.image_preview.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation
            )
            self.image_preview.setPixmap(scaled_pixmap)

    def generate_ramp(self):
        if not self.image_path or not self.sampled_colors:
            hou.ui.displayMessage("No image or sampled colors", severity=hou.severityType.Warning)
            return
        try:
            create_color_ramp(self.sampled_colors)
            hou.ui.displayMessage("Color ramp created successfully.")
        except Exception as e:
            hou.ui.displayMessage(f"Failed to create ramp: {e}", severity=hou.severityType.Error)

def show_image_ramp_ui():
    ui = ImageRampUI()
    ui.show()

if __name__ == "__main__" or "hou" in globals():
    show_image_ramp_ui()
