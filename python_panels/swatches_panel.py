import os
import struct
import hou
from PySide2 import QtWidgets, QtCore
from PySide2.QtCore import Qt
def cmyk_to_rgb(c, m, y, k):
    r = 1.0 - min(1.0, c * (1 - k) + k)
    g = 1.0 - min(1.0, m * (1 - k) + k)
    b = 1.0 - min(1.0, y * (1 - k) + k)
    return (r, g, b)

class SwatchLabel(QtWidgets.QLabel):
    selected_labels = set()

    def __init__(self, name, rgb, parent=None):
        super().__init__(parent)
        self.name = name
        self.rgb = rgb
        self.setFixedSize(100, 100)
        r, g, b = [int(c * 255) for c in rgb]
        self.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid black;")
        self.setToolTip(f"{name}\nRGB: {rgb}")
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self._drag_active = False
        self._selected = False

    def set_selected(self, selected):
        self._selected = selected
        border = "3px solid #33AADD" if self._selected else "1px solid black"
        r, g, b = [int(c * 255) for c in self.rgb]
        self.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: {border};")

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            return

        modifiers = QtWidgets.QApplication.keyboardModifiers()
        if modifiers in [QtCore.Qt.ControlModifier, QtCore.Qt.ShiftModifier]:
            self.set_selected(not self._selected)
            if self._selected:
                SwatchLabel.selected_labels.add(self)
            else:
                SwatchLabel.selected_labels.discard(self)
        else:
            for label in list(SwatchLabel.selected_labels):
                label.set_selected(False)
            self.set_selected(True)
            SwatchLabel.selected_labels = {self}

        self._drag_active = True
        self._start_pos = event.pos()

    def mouseMoveEvent(self, event):
        if self._drag_active and (event.pos() - self._start_pos).manhattanLength() > 5:
            self.setCursor(QtCore.Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        self.setCursor(QtCore.Qt.OpenHandCursor)
        if not self._drag_active:
            return
        self._drag_active = False

        pane = hou.ui.paneTabUnderCursor()
        if not pane:
            return

        selected = list(SwatchLabel.selected_labels or {self})
        spacing = hou.Vector2(1.5, -1.5)

        try:
            if isinstance(pane, hou.NetworkEditor):
                context = pane.pwd()
                pos = pane.cursorPosition()
                created = []

                if context.childTypeCategory().name() == 'Sop':
                    for i, swatch in enumerate(selected):
                        node = context.createNode("color")
                        node.setName(swatch.name.replace(" ", "_"), unique_name=True)
                        node.parmTuple("color").set(swatch.rgb)
                        node.setPosition(pos + spacing * i)
                        created.append(node)

                    for a, b in zip(created[:-1], created[1:]):
                        b.setNextInput(a)

                    if created:
                        created[-1].setSelected(True, clear_all_selected=True)
                        pane.setCurrentNode(created[-1])

                elif context.childTypeCategory().name() == 'Object':
                    for i, swatch in enumerate(selected):
                        geo_node = context.createNode("geo", swatch.name.replace(" ", "_"))
                        geo_node.moveToGoodPosition()
                        file_node = geo_node.node("file1")
                        if file_node: file_node.destroy()
                        color_node = geo_node.createNode("color", swatch.name.replace(" ", "_"))
                        color_node.parmTuple("color").set(swatch.rgb)
                        color_node.moveToGoodPosition()
                        color_node.setDisplayFlag(True)
                        color_node.setRenderFlag(True)
                        geo_node.layoutChildren()

            else:
                hou.ui.displayMessage("Drag target must be a Network Editor pane.")
        except Exception as e:
            hou.ui.displayMessage(f"Error creating node(s): {e}")

class SwatchViewer(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ASE Swatch Viewer")
        self.setMinimumSize(600, 500)
        self.swatches = []
        self._resize_timer = QtCore.QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._delayed_relayout)
        self.default_path = hou.getenv("ASE_SWATCH_PATH", os.path.expanduser("~"))

        self.tabs = QtWidgets.QTabWidget(self)
        self.library_tab = QtWidgets.QWidget()
        self.pref_tab = QtWidgets.QWidget()
        self.tabs.addTab(self.library_tab, "Library")
        self.tabs.addTab(self.pref_tab, "Preference")

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.addWidget(self.tabs)
        self.setLayout(main_layout)

        # Library Tab
        lib_layout = QtWidgets.QVBoxLayout(self.library_tab)
        path_layout = QtWidgets.QHBoxLayout()

        self.path_edit = QtWidgets.QLineEdit(self.default_path)
        self.path_edit.setPlaceholderText("Path to ASE directory")
        self.path_edit.editingFinished.connect(self.update_dropdown)

        self.file_dropdown = QtWidgets.QComboBox()
        self.file_dropdown.currentIndexChanged.connect(self.load_selected_ase)

        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(self.file_dropdown)
        lib_layout.addLayout(path_layout)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        lib_layout.addWidget(self.scroll_area)

        self.container = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.container)
        self.grid.setSpacing(6)
        self.grid.setVerticalSpacing(20)  # Fixed space between rows
        self.scroll_area.setWidget(self.container)
        self.scroll_area.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        # self.container.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)


        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(100)
        self.console.setStyleSheet("background-color: #111; color: #eee; font-family: Consolas; font-size: 11px;")
        lib_layout.addWidget(self.console)

        # Preference Tab
        pref_layout = QtWidgets.QVBoxLayout(self.pref_tab)
        self.pref_edit = QtWidgets.QLineEdit(self.default_path)
        self.pref_edit.setPlaceholderText("Default ASE directory path")
        self.pref_edit.editingFinished.connect(self.save_preference)
        pref_layout.addWidget(QtWidgets.QLabel("Default ASE Path:"))
        pref_layout.addWidget(self.pref_edit)
        pref_layout.addStretch()

        self.update_dropdown()

    def save_preference(self):
        path = self.pref_edit.text().strip()
        if os.path.isdir(path):
            hou.putenv("ASE_SWATCH_PATH", path)
            self.default_path = path
            self.path_edit.setText(path)
            self.update_dropdown()
        else:
            self.log(f"Invalid path: {path}")

    def log(self, message):
        self.console.appendPlainText(str(message))

    def clear_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        SwatchLabel.selected_labels.clear()

    def update_dropdown(self):
        path = self.path_edit.text().strip()
        self.file_dropdown.clear()

        if not os.path.isdir(path):
            self.log(f"Invalid folder: {path}")
            return

        ase_files = [f for f in os.listdir(path) if f.lower().endswith(".ase")]
        if not ase_files:
            self.log("No .ase files found in directory.")
            return

        self.file_dropdown.addItems(ase_files)
        self.log(f"Found {len(ase_files)} .ase files.")

    def load_selected_ase(self):
        folder = self.path_edit.text().strip()
        filename = self.file_dropdown.currentText()
        filepath = os.path.join(folder, filename)
        if os.path.exists(filepath):
            self.clear_grid()
            self.log(f"Loading ASE: {filepath}")
            self.swatches = self.parse_ase(filepath)
            self.populate_grid()
            self.log(f"Loaded {len(self.swatches)} swatches.")

    def populate_grid(self):
        self.clear_grid()

        if not self.swatches:
            return

        max_cols = max(1, self.scroll_area.viewport().width() // 110)
        row, col = 0, 0

        for name, rgb in self.swatches:
            swatch = SwatchLabel(name, rgb)

            name_label = QtWidgets.QLabel(name)
            name_label.setAlignment(QtCore.Qt.AlignCenter)
            name_label.setToolTip(name)
            name_label.setFixedWidth(100)
            name_label.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
            name_label.setStyleSheet("text-overflow: ellipsis; white-space: nowrap; overflow: hidden;")

            wrapper = QtWidgets.QWidget()
            wrapper.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

            vbox = QtWidgets.QVBoxLayout(wrapper)
            vbox.setContentsMargins(0, 0, 0, 0)
            vbox.setSpacing(4)  # Fixed spacing between swatch and label
            vbox.setAlignment(QtCore.Qt.AlignTop)
            vbox.addWidget(swatch, alignment=QtCore.Qt.AlignCenter)
            vbox.addWidget(name_label, alignment=QtCore.Qt.AlignCenter)

            self.grid.addWidget(wrapper, row, col)
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        # Prevent vertical stretching of rows
        for i in range(self.grid.rowCount()):
            self.grid.setRowStretch(i, 0)



    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start(250)

    def _delayed_relayout(self):
        if self.swatches:
            self.populate_grid()

    def parse_ase(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception as e:
            self.log(f"Error reading file: {e}")
            return []

        swatches = []
        pos = 0

        def read_u16():
            nonlocal pos
            val = struct.unpack(">H", data[pos:pos+2])[0]
            pos += 2
            return val

        def read_u32():
            nonlocal pos
            val = struct.unpack(">I", data[pos:pos+4])[0]
            pos += 4
            return val

        def read_f32():
            nonlocal pos
            val = struct.unpack(">f", data[pos:pos+4])[0]
            pos += 4
            return val

        def read_utf16():
            nonlocal pos
            strlen = read_u16()
            raw = data[pos:pos + strlen * 2]
            pos += strlen * 2
            return raw.decode('utf_16_be').rstrip('\0')

        if data[0:4] != b"ASEF":
            self.log("Invalid ASE file header.")
            return []

        pos = 4
        _major = read_u16()
        _minor = read_u16()
        _block_count = read_u32()

        while pos < len(data):
            try:
                block_type = read_u16()
                block_len = read_u32()
                block_end = pos + block_len

                if block_type == 0x0001:  # Color entry
                    name = read_utf16()
                    model = data[pos:pos+4].decode("ascii").strip()
                    pos += 4
                    if model == "RGB":
                        r, g, b = read_f32(), read_f32(), read_f32()
                        swatches.append((name, (r, g, b)))
                    elif model == "CMYK":
                        c, m, y, k = read_f32(), read_f32(), read_f32(), read_f32()
                        swatches.append((name, cmyk_to_rgb(c, m, y, k)))
                    pos += 2
                else:
                    self.log(f"Skipping block type: {hex(block_type)}")

                pos = block_end
            except Exception as e:
                self.log(f"Error parsing block: {e}")
                break

        return swatches

def onCreateInterface():
    return SwatchViewer()
