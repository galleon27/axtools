import os
import struct
import hou
import json
import re
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt

def cmyk_to_rgb(c, m, y, k):
    """Converts CMYK color values to RGB."""
    r = 1.0 - min(1.0, c * (1 - k) + k)
    g = 1.0 - min(1.0, m * (1 - k) + k)
    b = 1.0 - min(1.0, y * (1 - k) + k)
    return (r, g, b)

def sanitize_name(name):
    """Sanitize swatch names for Houdini node names"""
    sanitized = re.sub(r'[^\w\s-]', '', name)
    sanitized = re.sub(r'\s+', '_', sanitized)
    sanitized = sanitized.strip('_')
    if sanitized and not (sanitized[0].isalpha() or sanitized[0] == '_'):
        sanitized = 'swatch_' + sanitized
    if not sanitized:
        sanitized = 'unnamed_swatch'
    return sanitized

class ConfigManager:
    """Manages loading and saving of the JSON configuration file."""
    def __init__(self, config_file):
        self.config_file = config_file

    def load_config(self):
        if not os.path.exists(self.config_file): return {}
        try:
            with open(self.config_file, 'r') as f:
                return json.load(f)
        except (IOError, json.JSONDecodeError):
            return {}

    def save_config(self, config):
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=4)
        except IOError:
            pass

class SwatchLabel(QtWidgets.QLabel):
    """A custom QLabel to display a color swatch."""
    selected_labels = set()
    last_clicked = None

    KARMA_CONTEXTS = ('materialbuilder', 'materiallibrary', 'karmamaterialbuilder', 'subnet')
    OCTANE_CONTEXTS = ('octane_vopnet', 'octane_solaris_material_builder')
    REDSHIFT_CONTEXTS = ('redshift_vopnet', 'rs_usd_material_builder')

    def __init__(self, name, rgb, viewer, parent=None):
        super().__init__(parent)
        self.name = name
        self.rgb = rgb
        self.viewer = viewer
        self.setFixedSize(100, 100)
        r, g, b = [int(c * 255) for c in rgb]
        self.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid black;")
        self.setToolTip(f"{self.name}\nRGB: {self.rgb}")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._drag_active = False
        self._has_moved = False
        self._selected = False

    def set_selected(self, selected):
        self._selected = selected
        border = "3px solid #33AADD" if self._selected else "1px solid black"
        r, g, b = [int(c * 255) for c in self.rgb]
        self.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: {border};")

    def mousePressEvent(self, event):
        if event.button() not in [Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton]:
            return

        self._drag_active = True
        self._has_moved = False
        # PySide6: use position().toPoint() instead of pos()
        self._start_pos = event.position().toPoint()
        self._button = event.button()

        if event.button() == Qt.MouseButton.LeftButton:
            modifiers = QtWidgets.QApplication.keyboardModifiers()
            
            if modifiers == (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.ControlModifier):
                if SwatchLabel.last_clicked and SwatchLabel.last_clicked in self.viewer.swatch_widgets:
                    start_index = self.viewer.swatch_widgets.index(SwatchLabel.last_clicked)
                    end_index = self.viewer.swatch_widgets.index(self)
                    start, end = min(start_index, end_index), max(start_index, end_index)
                    for i in range(start, end + 1):
                        widget = self.viewer.swatch_widgets[i]
                        widget.set_selected(True)
                        SwatchLabel.selected_labels.add(widget)

            elif modifiers == Qt.KeyboardModifier.ShiftModifier:
                if SwatchLabel.last_clicked and SwatchLabel.last_clicked in self.viewer.swatch_widgets:
                    for label in list(SwatchLabel.selected_labels):
                        label.set_selected(False)
                    SwatchLabel.selected_labels.clear()

                    start_index = self.viewer.swatch_widgets.index(SwatchLabel.last_clicked)
                    end_index = self.viewer.swatch_widgets.index(self)
                    start, end = min(start_index, end_index), max(start_index, end_index)
                    for i in range(start, end + 1):
                        widget = self.viewer.swatch_widgets[i]
                        widget.set_selected(True)
                        SwatchLabel.selected_labels.add(widget)
                else:
                    self.set_selected(True); SwatchLabel.selected_labels.add(self); SwatchLabel.last_clicked = self
            
            elif modifiers == Qt.KeyboardModifier.ControlModifier:
                self.set_selected(not self._selected)
                if self._selected:
                    SwatchLabel.selected_labels.add(self)
                    SwatchLabel.last_clicked = self
                else:
                    SwatchLabel.selected_labels.discard(self)
            
            else:
                is_only_selected = self._selected and len(SwatchLabel.selected_labels) == 1
                if is_only_selected:
                    self.set_selected(False); SwatchLabel.selected_labels.clear(); SwatchLabel.last_clicked = None
                else:
                    for label in list(SwatchLabel.selected_labels):
                        label.set_selected(False)
                    SwatchLabel.selected_labels.clear()
                    self.set_selected(True); SwatchLabel.selected_labels.add(self); SwatchLabel.last_clicked = self

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if not pane:
                hou.ui.displayMessage("No active Network Editor found.")
                return

            context = pane.pwd()
            pos = pane.visibleBounds().center()
            created_nodes = []

            try:
                context_type = context.type().name()
                swatch_to_create = [self]

                if context.childTypeCategory().name() == 'Sop':
                    created_nodes = self._create_sop_nodes(context, swatch_to_create, pos)
                elif context_type in self.KARMA_CONTEXTS:
                    created_nodes = self._create_karma_nodes(context, swatch_to_create, pos)
                elif context_type in self.OCTANE_CONTEXTS:
                    created_nodes = self._create_octane_nodes(context, swatch_to_create, pos)
                elif context_type in self.REDSHIFT_CONTEXTS:
                    created_nodes = self._create_redshift_nodes(context, swatch_to_create, pos)
                elif context.childTypeCategory().name() == 'Object':
                    created_nodes = self._create_object_nodes(context, swatch_to_create)
                else:
                    hou.ui.displayMessage(f"Unsupported network context for swatch creation: {context_type}")

                if created_nodes:
                    created_nodes[-1].setSelected(True, clear_all_selected=True)

            except Exception as e:
                hou.ui.displayMessage(f"Error creating node: {e}")

    def contextMenuEvent(self, event):
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #3C3C3C; color: #DDDDDD; border: 1px solid #2A2A2A; }
            QMenu::item:selected { background-color: #555555; }
        """)
        
        grad_action = menu.addAction("Create Gradient from Colors")
        grad_action.triggered.connect(self.create_gradient_from_swatches)

        swatch_action = menu.addAction("Create Swatches in Geo")
        swatch_action.triggered.connect(self.create_swatches_in_geo)
        
        # PySide6: globalPos is deprecated, use globalPosition().toPoint(), exec_ is now exec
        menu.exec(event.globalPosition().toPoint())

    @staticmethod
    def sort_colors_by_hue(swatches):
        def rgb_to_hsv(rgb):
            r, g, b = rgb; mx, mn = max(rgb), min(rgb); diff = mx - mn
            h = 0
            if diff != 0:
                if mx == r: h = (60 * ((g - b) / diff) + 360) % 360
                elif mx == g: h = (60 * ((b - r) / diff) + 120) % 360
                elif mx == b: h = (60 * ((r - g) / diff) + 240) % 360
            return (h, mx)
        return sorted(swatches, key=lambda s: rgb_to_hsv(s.rgb))

    def mouseMoveEvent(self, event):
        # PySide6: pos() deprecated, use position().toPoint()
        current_pos = event.position().toPoint()
        if self._drag_active and (current_pos - self._start_pos).manhattanLength() > 5:
            self._has_moved = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if not self._drag_active or not self._has_moved: return
        self._drag_active = False

        pane = hou.ui.paneTabUnderCursor()
        if not isinstance(pane, hou.NetworkEditor): return

        swatches_to_create = []
        if self._button == Qt.MouseButton.MiddleButton:
            # If the middle-dragged swatch is part of a selection, use the selection.
            # Otherwise, just use the single swatch under the cursor.
            if self in SwatchLabel.selected_labels:
                swatches_to_create = list(SwatchLabel.selected_labels)
            else:
                swatches_to_create = [self]
        else: # Left-drag always uses the current selection
            swatches_to_create = list(SwatchLabel.selected_labels or {self})

        if not swatches_to_create: return

        context, pos = pane.pwd(), pane.cursorPosition()
        created_nodes = []
        try:
            context_type = context.type().name()
            
            if context.childTypeCategory().name() == 'Sop':
                created_nodes = self._handle_sop_creation(context, swatches_to_create, pos, self._create_sop_nodes, self._create_sop_gradient)
            elif context_type in self.KARMA_CONTEXTS:
                created_nodes = self._handle_material_creation(context, swatches_to_create, pos, self._create_karma_nodes, self._create_karma_gradient)
            elif context_type in self.OCTANE_CONTEXTS:
                created_nodes = self._handle_material_creation(context, swatches_to_create, pos, self._create_octane_nodes, self._create_octane_gradient)
            elif context_type in self.REDSHIFT_CONTEXTS:
                created_nodes = self._handle_material_creation(context, swatches_to_create, pos, self._create_redshift_nodes, self._create_redshift_gradient)
            elif context.childTypeCategory().name() == 'Object':
                created_nodes = self._create_object_nodes(context, swatches_to_create)
            else:
                hou.ui.displayMessage(f"Unsupported network context for drag & drop: {context_type}")

            if created_nodes:
                created_nodes[-1].setSelected(True, clear_all_selected=True)
                if context.childTypeCategory().name() == 'Sop':
                    pane.setCurrentNode(created_nodes[-1])
        except Exception as e:
            hou.ui.displayMessage(f"Error creating node(s): {e}")

    def _handle_sop_creation(self, context, selected, pos, node_creation_func, gradient_creation_func):
        if len(selected) > 1:
            choice = hou.ui.displayMessage("Create individual nodes or a gradient?", buttons=["Nodes", "Gradient", "Cancel"], default_choice=0, close_choice=2)
            if choice == 0:
                return node_creation_func(context, selected, pos)
            elif choice == 1:
                sort_choice = hou.ui.displayMessage("Sort swatches by hue?", buttons=["Yes", "No", "Cancel"], default_choice=0, close_choice=2)
                if sort_choice == 2: return []
                swatches_to_use = self.sort_colors_by_hue(selected) if sort_choice == 0 else selected
                return gradient_creation_func(context, swatches_to_use, pos)
            else: return []
        else:
            return node_creation_func(context, selected, pos)

    def _handle_material_creation(self, context, selected, pos, node_creation_func, gradient_creation_func):
        if len(selected) > 1:
            choice = hou.ui.displayMessage("Create individual nodes or a gradient?", buttons=["Nodes", "Gradient", "Cancel"], default_choice=0, close_choice=2)
            if choice == 0:
                return node_creation_func(context, selected, pos)
            elif choice == 1:
                sort_choice = hou.ui.displayMessage("Sort swatches by hue?", buttons=["Yes", "No", "Cancel"], default_choice=0, close_choice=2)
                if sort_choice == 2: return []
                swatches_to_use = self.sort_colors_by_hue(selected) if sort_choice == 0 else selected
                return gradient_creation_func(context, swatches_to_use, pos)
            else: return []
        else:
            return node_creation_func(context, selected, pos)

    def _create_nodes(self, context, selected, pos, node_type, parm_names):
        created = []
        spacing = hou.Vector2(1.5, -1.5)
        for i, swatch in enumerate(selected):
            node = context.createNode(node_type)
            node.setName(sanitize_name(swatch.name), unique_name=True)
            for j, parm in enumerate(parm_names):
                node.parm(parm).set(swatch.rgb[j])
            node.setPosition(pos + spacing * i)
            created.append(node)
        return created

    def _create_sop_nodes(self, context, selected, pos):
        nodes = self._create_nodes(context, selected, pos, "color", ("colorr", "colorg", "colorb"))
        for a, b in zip(nodes[:-1], nodes[1:]):
            b.setNextInput(a)
        return nodes

    def _create_karma_nodes(self, context, selected, pos):
        created = []
        spacing = hou.Vector2(1.5, -1.5)
        for i, swatch in enumerate(selected):
            node = context.createNode("mtlxconstant")
            node.setName(sanitize_name(swatch.name), unique_name=True)
            node.parm("signature").set("color3")
            node.parm("value_color3r").set(swatch.rgb[0])
            node.parm("value_color3g").set(swatch.rgb[1])
            node.parm("value_color3b").set(swatch.rgb[2])
            node.setPosition(pos + spacing * i)
            created.append(node)
        return created

    def _create_octane_nodes(self, context, selected, pos):
        return self._create_nodes(context, selected, pos, "NT_TEX_RGB", ("A_VALUEr", "A_VALUEg", "A_VALUEb"))

    def _create_redshift_nodes(self, context, selected, pos):
        return self._create_nodes(context, selected, pos, "redshift::RSColorConstant", ("colorr", "colorg", "colorb"))

    def _create_object_nodes(self, context, selected):
        created = []
        for swatch in selected:
            geo = context.createNode("geo", sanitize_name(swatch.name))
            geo.moveToGoodPosition()
            if file_node := geo.node("file1"): file_node.destroy()
            color = geo.createNode("color", sanitize_name(swatch.name))
            color.parmTuple("color").set(swatch.rgb)
            color.moveToGoodPosition(); color.setDisplayFlag(True); color.setRenderFlag(True)
            geo.layoutChildren()
            created.append(geo)
        return created

    def _create_gradient(self, context, selected, pos, node_type, parm_name):
        node = context.createNode(node_type)
        node.setName("swatch_gradient", unique_name=True)
        node.setPosition(pos)
        
        num = len(selected)
        positions = [i / max(1, num - 1) for i in range(num)]
        colors = [s.rgb for s in selected]
        ramp = hou.Ramp([hou.rampBasis.Linear] * num, positions, colors)
        node.parm(parm_name).set(ramp)
        return [node]

    def _create_sop_gradient(self, context, selected, pos):
        node = self._create_gradient(context, selected, pos, "color", "ramp")
        node[0].parm("colortype").set(3)
        return node

    def _create_karma_gradient(self, context, selected, pos):
        return self._create_gradient(context, selected, pos, "kma_rampconst", "vramp")
    
    def _create_octane_gradient(self, context, selected, pos):
        return self._create_gradient(context, selected, pos, "NT_TEX_GRADIENT", "octane_gradient")

    def _create_redshift_gradient(self, context, selected, pos):
        return self._create_gradient(context, selected, pos, "redshift::RSRamp", "ramp")

    def create_gradient_from_swatches(self):
        selected = list(SwatchLabel.selected_labels or {self})
        if not selected: return

        choice = hou.ui.displayMessage("Sort swatches by hue?", buttons=["Yes", "No", "Cancel"], default_choice=0, close_choice=2)
        if choice == 2: return
        if choice == 0:
            selected = self.sort_colors_by_hue(selected)

        pane = next((p for p in hou.ui.paneTabs() if isinstance(p, hou.NetworkEditor)), None)
        if not pane: return

        context = pane.pwd()
        if context.childTypeCategory().name() != 'Sop':
            hou.ui.displayMessage("Can only create a gradient in a SOP context.")
            return

        try:
            pos = pane.cursorPosition()
            nodes = self._create_sop_gradient(context, selected, pos)
            if nodes:
                nodes[0].setSelected(True, clear_all_selected=True)
                pane.setCurrentNode(nodes[0])
        except Exception as e:
            hou.ui.displayMessage(f"Error creating gradient: {e}")
            
    def create_swatches_in_geo(self):
        selected = list(SwatchLabel.selected_labels or {self})
        if not selected: return
        
        try:
            obj_context = hou.node("/obj")
            geo = obj_context.createNode("geo", "swatch_colors")
            geo.moveToGoodPosition()
            if file_node := geo.node("file1"): file_node.destroy()

            prev_node = None
            for swatch in selected:
                color = geo.createNode("color")
                color.setName(sanitize_name(swatch.name), unique_name=True)
                color.parmTuple("color").set(swatch.rgb)
                if prev_node: color.setInput(0, prev_node)
                prev_node = color
            if prev_node: prev_node.setDisplayFlag(True); prev_node.setRenderFlag(True)
            geo.layoutChildren()
            hou.ui.displayMessage(f"Created {len(selected)} color swatches inside {geo.path()}.")
        except Exception as e:
            hou.ui.displayMessage(f"Error creating swatches in Geo node: {e}")


class SwatchViewer(QtWidgets.QWidget):
    """The main widget for the ASE Swatch Viewer."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ASE Swatch Viewer")
        self.setMinimumSize(600, 500)

        config_path = os.path.join(hou.expandString("$HOUDINI_USER_PREF_DIR"), "ase_swatch_viewer_config.json")
        self.config_manager = ConfigManager(config_path)
        config = self.config_manager.load_config()
        self.default_path = config.get("default_path", os.path.expanduser("~"))

        self.swatches = []
        self.swatch_widgets = []
        self._resize_timer = QtCore.QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._delayed_relayout)

        self._init_ui()
        self.populate_path_dropdown()
        self.update_dropdown()

    def _init_ui(self):
        self.tabs = QtWidgets.QTabWidget(self)
        self.library_tab, self.pref_tab = QtWidgets.QWidget(), QtWidgets.QWidget()
        self.tabs.addTab(self.library_tab, "Library")
        self.tabs.addTab(self.pref_tab, "Preference")

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.addWidget(self.tabs)
        self.setLayout(main_layout)

        lib_layout = QtWidgets.QVBoxLayout(self.library_tab)
        path_layout = QtWidgets.QHBoxLayout()

        self.path_dropdown = QtWidgets.QComboBox()
        self.path_dropdown.currentIndexChanged.connect(self.update_dropdown)
        self.path_dropdown.setEditable(True)
        self.path_dropdown.lineEdit().editingFinished.connect(self.on_path_edit_finished)
        self.path_dropdown.setStyleSheet("QComboBox { padding-right: 7px; }")

        self.file_dropdown = QtWidgets.QComboBox()
        self.file_dropdown.currentIndexChanged.connect(self.load_selected_ase)
        self.file_dropdown.setMaximumWidth(250)

        path_layout.addWidget(self.path_dropdown)
        path_layout.addWidget(self.file_dropdown)
        lib_layout.addLayout(path_layout)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        lib_layout.addWidget(self.scroll_area)

        self.container = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.container)
        self.grid.setContentsMargins(0,0,0,0)
        self.grid.setSpacing(6)
        self.grid.setVerticalSpacing(20)
        self.scroll_area.setWidget(self.container)
        self.container.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(100)
        self.console.setStyleSheet("background-color: #111; color: #eee; font-family: Consolas;")
        lib_layout.addWidget(self.console)

        pref_layout = QtWidgets.QVBoxLayout(self.pref_tab)
        self.pref_edit = QtWidgets.QLineEdit(self.default_path)
        self.pref_edit.setPlaceholderText("Default ASE directory path")
        self.pref_edit.editingFinished.connect(self.save_preference)
        pref_layout.addWidget(QtWidgets.QLabel("Default ASE Path:"))
        pref_layout.addWidget(self.pref_edit)
        pref_layout.addStretch()

    def save_preference(self):
        path = self.pref_edit.text().strip()
        if os.path.isdir(path):
            self.default_path = path
            self.config_manager.save_config({"default_path": path})
            self.log(f"Default path saved: {path}")
            self.populate_path_dropdown()
        else:
            self.log(f"Invalid path: {path}")

    def log(self, message):
        self.console.appendPlainText(str(message))

    def clear_grid(self):
        SwatchLabel.selected_labels.clear()
        SwatchLabel.last_clicked = None
        self.swatch_widgets = []
        while self.grid.count():
            if item := self.grid.takeAt(0):
                if widget := item.widget():
                    widget.deleteLater()

    def on_path_edit_finished(self):
        new_path = self.path_dropdown.currentText().strip()
        if os.path.isdir(new_path):
            if new_path not in [self.path_dropdown.itemText(i) for i in range(self.path_dropdown.count())]:
                self.path_dropdown.addItem(new_path)
            self.path_dropdown.setCurrentText(new_path)
        else:
            self.log(f"Invalid folder: {new_path}")

    def update_dropdown(self):
        path = self.path_dropdown.currentText().strip()
        self.file_dropdown.clear()
        if not os.path.isdir(path):
            self.log(f"Invalid folder: {path}")
            return
        try:
            ase_files = [f for f in os.listdir(path) if f.lower().endswith(".ase")]
            if not ase_files:
                self.log("No .ase files found.")
                return
            self.file_dropdown.addItems(sorted(ase_files))
        except OSError as e:
            self.log(f"Error reading directory: {e}")

    def load_selected_ase(self):
        folder = self.path_dropdown.currentText().strip()
        filename = self.file_dropdown.currentText()
        if not filename: return

        filepath = os.path.join(folder, filename)
        if os.path.exists(filepath):
            self.log(f"Loading ASE file: {filepath}")
            self.swatches = self.parse_ase(filepath)
            self.populate_grid()
            self.log(f"Loaded {len(self.swatches)} swatches.")

    def populate_path_dropdown(self):
        """Populates dropdown with subdirectories containing .ase files."""
        self.path_dropdown.clear()
        if not os.path.isdir(self.default_path):
            self.log(f"Invalid default path: {self.default_path}")
            self.path_dropdown.addItem(self.default_path)
            return
        try:
            paths = [dp for dp, _, fns in os.walk(self.default_path) if any(f.lower().endswith(".ase") for f in fns)]
            if paths:
                self.path_dropdown.addItems(sorted(paths))
            else:
                self.log(f"No folders with .ase files found under {self.default_path}")
                self.path_dropdown.addItem(self.default_path)
        except OSError as e:
            self.log(f"Error scanning directories: {e}")

    def populate_grid(self):
        self.clear_grid()
        if not self.swatches: return

        max_cols = max(1, self.scroll_area.viewport().width() // 110)
        for i, (name, rgb) in enumerate(self.swatches):
            row, col = divmod(i, max_cols)
            swatch = SwatchLabel(name, rgb, self)
            self.swatch_widgets.append(swatch)

            name_label = QtWidgets.QLabel(name)
            name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            name_label.setToolTip(name)
            name_label.setFixedWidth(100)
            name_label.setStyleSheet("text-overflow: ellipsis; white-space: nowrap; overflow: hidden;")

            wrapper = QtWidgets.QWidget()
            vbox = QtWidgets.QVBoxLayout(wrapper)
            vbox.setContentsMargins(0, 0, 0, 0)
            vbox.setSpacing(4)
            vbox.addWidget(swatch, alignment=Qt.AlignmentFlag.AlignCenter)
            vbox.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(wrapper, row, col)
        
        self.grid.setRowStretch(self.grid.rowCount(), 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start(100)

    def _delayed_relayout(self):
        if self.swatches:
            self.populate_grid()

    def parse_ase(self, path):
        try:
            with open(path, "rb") as f: data = f.read()
        except IOError as e:
            self.log(f"Error reading file: {e}"); return []

        if data[0:4] != b"ASEF":
            self.log("Invalid ASE file header."); return []

        swatches, pos = [], 12
        try:
            while pos < len(data):
                block_type = struct.unpack(">H", data[pos:pos+2])[0]; pos += 2
                block_len = struct.unpack(">I", data[pos:pos+4])[0]; pos += 4
                block_end = pos + block_len

                if block_type == 0xc001: pos = block_end; continue
                if block_type == 0x0001:
                    name_len = struct.unpack(">H", data[pos:pos+2])[0]; pos += 2
                    name = data[pos:pos + (name_len - 1) * 2].decode('utf_16_be'); pos += name_len * 2
                    model = data[pos:pos+4].decode("ascii").strip(); pos += 4

                    if model == "RGB":
                        r,g,b = [struct.unpack(">f", data[pos+i*4:pos+(i+1)*4])[0] for i in range(3)]
                        swatches.append((name, (r, g, b)))
                    elif model == "CMYK":
                        c,m,y,k = [struct.unpack(">f", data[pos+i*4:pos+(i+1)*4])[0] for i in range(4)]
                        swatches.append((name, cmyk_to_rgb(c, m, y, k)))
                pos = block_end
        except (struct.error, IndexError, UnicodeDecodeError) as e:
            self.log(f"Error parsing ASE block: {e}")
        return swatches

def onCreateInterface():
    """Entry point for Houdini to create the interface."""
    return SwatchViewer()