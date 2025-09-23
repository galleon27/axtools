import hou
import struct
from PySide2 import QtWidgets, QtGui, QtCore

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
            # Toggle selection
            self.set_selected(not self._selected)
            if self._selected:
                SwatchLabel.selected_labels.add(self)
            else:
                SwatchLabel.selected_labels.discard(self)
        else:
            # Clear all others
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
        if not isinstance(pane, hou.NetworkEditor):
            return  # Not over a network editor

        network = pane.pwd()
        pos = pane.cursorPosition()
        selected = list(SwatchLabel.selected_labels or {self})

        def unique_name(parent, base):
            existing = {child.name() for child in parent.children()}
            if base not in existing:
                return base
            i = 1
            while f"{base}_{i}" in existing:
                i += 1
            return f"{base}_{i}"

        spacing = hou.Vector2(1.5, -1.5)
        base_pos = pos - hou.Vector2(len(selected) / 2.0, 0.0)

        try:
            created = []
            for i, swatch in enumerate(selected):
                if network.childTypeCategory().name() == "Sop":
                    # We are inside SOP context, create color nodes here
                    color_base = swatch.name.lower().replace(" ", "_") + "_color"
                    color_name = unique_name(network, color_base)
                    node = network.createNode("color", color_name)
                    node.parmTuple("color").set(swatch.rgb)
                    node.setPosition(base_pos + spacing * i)
                elif network.path() == "/obj":
                    # In /obj context, create geo node + color node inside it
                    geo_base = swatch.name.lower().replace(" ", "_")
                    geo_name = unique_name(hou.node("/obj"), geo_base)
                    geo_node = hou.node("/obj").createNode("geo", geo_name)
                    geo_node.moveToGoodPosition()

                    color_base = swatch.name.lower().replace(" ", "_") + "_color"
                    color_name = unique_name(geo_node, color_base)
                    node = geo_node.createNode("color", color_name)
                    node.parmTuple("color").set(swatch.rgb)
                    node.moveToGoodPosition()
                else:
                    hou.ui.displayMessage(f"Unsupported network context: {network.path()}")
                    return

                created.append(node)

            # Connect created nodes linearly
            for a, b in zip(created[:-1], created[1:]):
                b.setNextInput(a)

            if created:
                created[-1].setSelected(True, clear_all_selected=True)
                pane.setCurrentNode(created[-1])
        except Exception as e:
            hou.ui.displayMessage(f"Error creating color node(s): {e}")



class SwatchViewer(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ASE Swatch Viewer")
        self.setMinimumSize(500, 400)

        layout = QtWidgets.QVBoxLayout(self)
        self.setLayout(layout)

        self.load_btn = QtWidgets.QPushButton("Load .ASE File")
        self.load_btn.clicked.connect(self.load_ase)
        layout.addWidget(self.load_btn)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        layout.addWidget(self.scroll_area)

        self.container = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.container)
        self.grid.setSpacing(6)
        self.scroll_area.setWidget(self.container)

        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(100)
        self.console.setStyleSheet("background-color: #111; color: #eee; font-family: Consolas; font-size: 11px;")
        layout.addWidget(self.console)

    def log(self, message):
        self.console.appendPlainText(str(message))

    def closeEvent(self, event):
        if hasattr(hou.session, "swatch_viewer"):
            hou.session.swatch_viewer = None
        event.accept()

    def clear_grid(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Clear selection set on reload
        SwatchLabel.selected_labels.clear()

    def load_ase(self):
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select ASE File", filter="*.ase")
        if not filepath:
            self.log("No file selected.")
            return

        self.log(f"Loading ASE: {filepath}")
        self.clear_grid()
        swatches = self.parse_ase(filepath)

        row = 0
        col = 0
        max_cols = 6

        for item in swatches:
            if isinstance(item, str):  # group label
                group_label = QtWidgets.QLabel(f"<b>{item}</b>")
                group_label.setAlignment(QtCore.Qt.AlignLeft)
                self.grid.addWidget(group_label, row, 0, 1, max_cols)
                row += 1
                col = 0
                continue

            name, rgb = item
            swatch_label = SwatchLabel(name, rgb)

            name_label = QtWidgets.QLabel(name)
            name_label.setAlignment(QtCore.Qt.AlignLeft)
            name_label.setFixedWidth(100)
            name_label.setToolTip(name)
            name_label.setStyleSheet("text-overflow: ellipsis; white-space: nowrap; overflow: hidden;")

            cell_widget = QtWidgets.QWidget()
            cell_layout = QtWidgets.QVBoxLayout(cell_widget)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            cell_layout.addWidget(name_label)
            cell_layout.addWidget(swatch_label)

            self.grid.addWidget(cell_widget, row, col)

            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        self.log(f"Loaded {len(swatches)} swatches.")


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
        major = read_u16()
        minor = read_u16()
        block_count = read_u32()
        self.log(f"ASE version: {major}.{minor}")
        self.log(f"ASE block count: {block_count}")

        block_index = 0

        while pos < len(data):
            try:
                block_type = read_u16()
                block_len = read_u32()
                block_end = pos + block_len
                block_index += 1

                if block_type == 0x0001:  # Color entry
                    name = read_utf16()
                    color_model = data[pos:pos+4].decode("ascii").strip()
                    pos += 4

                    if color_model == "RGB":
                        r = read_f32()
                        g = read_f32()
                        b = read_f32()
                        swatches.append((name, (r, g, b)))
                        self.log(f"RGB swatch: {name} → ({r:.2f}, {g:.2f}, {b:.2f})")
                    elif color_model == "CMYK":
                        c = read_f32()
                        m = read_f32()
                        y = read_f32()
                        k = read_f32()
                        r, g, b = cmyk_to_rgb(c, m, y, k)
                        swatches.append((name + " (CMYK)", (r, g, b)))
                        self.log(f"CMYK swatch: {name} → ({r:.2f}, {g:.2f}, {b:.2f})")
                    else:
                        self.log(f"Unsupported color model: {color_model}")

                    pos += 2  # skip color type
                else:
                    self.log(f"Skipping block type: {hex(block_type)}")

                pos = block_end
            except Exception as e:
                self.log(f"Error parsing block {block_index}: {e}")
                break

        return swatches

def launch_swatch_viewer():
    if not hasattr(hou.session, "swatch_viewer") or not isinstance(hou.session.swatch_viewer, QtWidgets.QWidget):
        hou.session.swatch_viewer = SwatchViewer()
    if hou.session.swatch_viewer is not None:
        hou.session.swatch_viewer.show()
        hou.session.swatch_viewer.raise_()

launch_swatch_viewer()
