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

class FolderTree(QtWidgets.QTreeWidget):
    """Custom tree widget that accepts folder drag-and-drops from the OS."""
    folder_dropped = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.setStyleSheet("""
            QTreeWidget { background-color: #333333; color: #EEEEEE; border: none; }
            QTreeWidget::item:selected { background-color: #555555; }
        """)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isdir(path):
                self.folder_dropped.emit(path)
        event.acceptProposedAction()

class SwatchLabel(QtWidgets.QLabel):
    """A custom QLabel to display a color swatch."""
    selected_labels = set()
    last_clicked = None

    KARMA_CONTEXTS = ('materialbuilder', 'materiallibrary', 'karmamaterialbuilder', 'subnet')
    OCTANE_CONTEXTS = ('octane_vopnet', 'octane_solaris_material_builder')
    REDSHIFT_CONTEXTS = ('redshift_vopnet', 'rs_usd_material_builder')
    MATNET_CONTEXTS = ('matnet',)

    def __init__(self, name, rgb, viewer, size=100, parent=None):
            super().__init__(parent)
            self.name = name
            self.rgb = rgb
            self.viewer = viewer
            self.setFixedSize(size, size)
            r, g, b = [int(c * 255) for c in rgb]
            self.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid black;")
            self.setToolTip(f"{self.name}\nRGB: {self.rgb}")
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            self._drag_active = False
            self._has_moved = False
            self._selected = False
            self._button = None

    def set_selected(self, selected):
        self._selected = selected
        border = "3px solid #33AADD" if self._selected else "1px solid black"
        r, g, b = [int(c * 255) for c in self.rgb]
        self.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: {border};")

    def mousePressEvent(self, event):
        if event.button() not in [QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.MiddleButton]:
            return

        self._drag_active = True
        self._has_moved = False
        self._start_pos = event.pos()
        self._button = event.button()

        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            modifiers = QtWidgets.QApplication.keyboardModifiers()
            
            if modifiers == (QtCore.Qt.KeyboardModifier.ShiftModifier | QtCore.Qt.KeyboardModifier.ControlModifier):
                if SwatchLabel.last_clicked and SwatchLabel.last_clicked in self.viewer.swatch_widgets:
                    start_index = self.viewer.swatch_widgets.index(SwatchLabel.last_clicked)
                    end_index = self.viewer.swatch_widgets.index(self)
                    start, end = min(start_index, end_index), max(start_index, end_index)
                    for i in range(start, end + 1):
                        self.viewer.swatch_widgets[i].set_selected(True)
                        SwatchLabel.selected_labels.add(self.viewer.swatch_widgets[i])

            elif modifiers == QtCore.Qt.KeyboardModifier.ShiftModifier:
                if SwatchLabel.last_clicked and SwatchLabel.last_clicked in self.viewer.swatch_widgets:
                    for label in list(SwatchLabel.selected_labels):
                        label.set_selected(False)
                    SwatchLabel.selected_labels.clear()

                    start_index = self.viewer.swatch_widgets.index(SwatchLabel.last_clicked)
                    end_index = self.viewer.swatch_widgets.index(self)
                    start, end = min(start_index, end_index), max(start_index, end_index)
                    for i in range(start, end + 1):
                        self.viewer.swatch_widgets[i].set_selected(True)
                        SwatchLabel.selected_labels.add(self.viewer.swatch_widgets[i])
                else:
                    self.set_selected(True); SwatchLabel.selected_labels.add(self); SwatchLabel.last_clicked = self
            
            elif modifiers == QtCore.Qt.KeyboardModifier.ControlModifier:
                self.set_selected(not self._selected)
                if self._selected:
                    SwatchLabel.selected_labels.add(self)
                    SwatchLabel.last_clicked = self
                else:
                    SwatchLabel.selected_labels.discard(self)
            
            else:
                if not self._selected:
                    for label in list(SwatchLabel.selected_labels):
                        label.set_selected(False)
                    SwatchLabel.selected_labels.clear()
                    self.set_selected(True)
                    SwatchLabel.selected_labels.add(self)
                SwatchLabel.last_clicked = self

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
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
                elif context_type in self.MATNET_CONTEXTS:
                    created_nodes = self._create_matnet_nodes(context, swatch_to_create, pos)
                elif context.childTypeCategory().name() == 'Object':
                    created_nodes = self._create_object_nodes(context, swatch_to_create, pos)
                else:
                    hou.ui.displayMessage(f"Unsupported network context for swatch creation: {context_type}")

                if created_nodes:
                    created_nodes[-1].setSelected(True, clear_all_selected=True)

            except Exception as e:
                hou.ui.displayMessage(f"Error creating node: {e}")

    def contextMenuEvent(self, event):
        self.viewer.show_context_menu(event.globalPos(), self)

    @staticmethod
    def sort_colors_by_hue(swatches):
        def rgb_to_hsv(rgb):
            r, g, b = rgb; mx, mn = max(rgb), min(rgb); diff = mx - mn
            h = 0
            if diff > 1e-6:
                if mx == r: h = (g - b) / diff
                elif mx == g: h = 2.0 + (b - r) / diff
                elif mx == b: h = 4.0 + (r - g) / diff
            h *= 60
            if h < 0: h += 360
            return (h, mx)
        return sorted(swatches, key=lambda s: rgb_to_hsv(s.rgb))

    def mouseMoveEvent(self, event):
        if self._drag_active and (event.pos() - self._start_pos).manhattanLength() > 5:
            self._has_moved = True
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event, from_context_menu=None, context_override=None):
        # If called from a real mouse event, check the button.
        if event and event.button() not in [QtCore.Qt.MouseButton.LeftButton, QtCore.Qt.MouseButton.MiddleButton]:
            return
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)

        # If called from context menu, we force the 'dragged' state.
        # Otherwise, check if the mouse actually moved.
        is_drag_action = from_context_menu is not None or self._has_moved

        if not is_drag_action:
            self._drag_active = False
            if self._button == QtCore.Qt.MouseButton.LeftButton and self._selected and len(SwatchLabel.selected_labels) > 1:
                modifiers = QtWidgets.QApplication.keyboardModifiers()
                if modifiers not in [QtCore.Qt.KeyboardModifier.ShiftModifier, QtCore.Qt.KeyboardModifier.ControlModifier, (QtCore.Qt.KeyboardModifier.ShiftModifier | QtCore.Qt.KeyboardModifier.ControlModifier)]:
                    for label in list(SwatchLabel.selected_labels):
                        label.set_selected(False)
                    SwatchLabel.selected_labels.clear()
                    self.set_selected(True)
                    SwatchLabel.selected_labels.add(self)
            return

        self._drag_active = False

        # Determine context and position
        if context_override:
            context, pos = context_override
        else:
            pane = hou.ui.paneTabUnderCursor()
            if not isinstance(pane, hou.NetworkEditor): return
            context, pos = pane.pwd(), pane.cursorPosition()

        # Determine which swatches to create
        if from_context_menu:
            swatches_to_create = list(SwatchLabel.selected_labels or {self})
        else: # From a drag/drop
            if self._button == QtCore.Qt.MouseButton.MiddleButton:
                if self in SwatchLabel.selected_labels:
                    swatches_to_create = list(SwatchLabel.selected_labels)
                else:
                    swatches_to_create = [self]
            else:
                swatches_to_create = list(SwatchLabel.selected_labels or {self})

        if not swatches_to_create: return

        created_nodes = []
        try:
            context_type = context.type().name()
            
            creation_mode = from_context_menu if from_context_menu else 'nodes'
            
            if context.childTypeCategory().name() == 'Sop':
                created_nodes = self._handle_sop_creation(context, swatches_to_create, pos, self._create_sop_nodes, self._create_sop_gradient)
            elif context_type in self.KARMA_CONTEXTS:
                created_nodes = self._handle_material_creation(context, swatches_to_create, pos, self._create_karma_nodes, self._create_karma_gradient)
            elif context_type in self.OCTANE_CONTEXTS:
                created_nodes = self._handle_material_creation(context, swatches_to_create, pos, self._create_octane_nodes, self._create_octane_gradient)
            elif context_type in self.REDSHIFT_CONTEXTS:
                created_nodes = self._handle_material_creation(context, swatches_to_create, pos, self._create_redshift_nodes, self._create_redshift_gradient)
            elif context_type in self.MATNET_CONTEXTS:
                created_nodes = self._handle_matnet_creation(context, swatches_to_create, pos, self._create_matnet_nodes, self._create_matnet_gradient)
            elif context.childTypeCategory().name() == 'Object':
                created_nodes = self._create_object_nodes(context, swatches_to_create, pos)
            else:
                hou.ui.displayMessage(f"Unsupported network context for drag & drop: {context_type}")

            if created_nodes:
                created_nodes[-1].setSelected(True, clear_all_selected=True)
                if context.childTypeCategory().name() == 'Sop' and not context_override:
                    pane.setCurrentNode(created_nodes[-1])
        except Exception as e:
            hou.ui.displayMessage(f"Error creating node(s): {e}")

    def _handle_sop_creation(self, context, selected, pos, node_creation_func, gradient_creation_func):
        if len(selected) > 1:
            choice = hou.ui.displayMessage("Create individual nodes or a gradient?", buttons=["Nodes", "Gradient", "Cancel"], default_choice=0, close_choice=2)
            if choice == 0: return node_creation_func(context, selected, pos)
            elif choice == 1:
                sort_choice = hou.ui.displayMessage("Sort swatches by hue?", buttons=["Yes", "No", "Cancel"], default_choice=0, close_choice=2)
                if sort_choice == 2: return []
                swatches_to_use = self.sort_colors_by_hue(selected) if sort_choice == 0 else selected
                return gradient_creation_func(context, swatches_to_use, pos)
            else: return []
        else:
            return node_creation_func(context, selected, pos)

    def _handle_material_creation(self, context, selected, pos, node_creation_func, gradient_creation_func):
        return self._handle_sop_creation(context, selected, pos, node_creation_func, gradient_creation_func)

    def _handle_matnet_creation(self, context, selected, pos, node_creation_func, gradient_creation_func):
        return self._handle_sop_creation(context, selected, pos, node_creation_func, gradient_creation_func)

    def _create_nodes(self, context, selected, pos, node_type, parm_names):
        created = []
        spacing = hou.Vector2(0, -1.0)
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
        spacing = hou.Vector2(0, -1.0)
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
    
    def _create_matnet_nodes(self, context, selected, pos):
        created = []
        spacing = hou.Vector2(0, -1.0)
        for i, swatch in enumerate(selected):
            node = context.createNode("constant")
            node.setName(sanitize_name(swatch.name), unique_name=True)
            node.parm("consttype").set("color")
            node.parm("colordefr").set(swatch.rgb[0])
            node.parm("colordefg").set(swatch.rgb[1])
            node.parm("colordefb").set(swatch.rgb[2])
            node.setPosition(pos + spacing * i)
            created.append(node)
        return created

    def _create_object_nodes(self, context, selected, pos):
        created = []
        spacing = hou.Vector2(0, -1.0)
        for i, swatch in enumerate(selected):
            geo = context.createNode("geo", sanitize_name(swatch.name))
            if file_node := geo.node("file1"): file_node.destroy()
            color = geo.createNode("color", sanitize_name(swatch.name))
            color.parmTuple("color").set(swatch.rgb)
            color.moveToGoodPosition(); color.setDisplayFlag(True); color.setRenderFlag(True)
            geo.setPosition(pos + spacing * i)
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
    
    def _create_matnet_gradient(self, context, selected, pos):
        return self._create_gradient(context, selected, pos, "rampparm", "rampcolordefault")

    def _execute_creation(self, creation_logic):
        """Generic helper to execute a node creation function in the current context."""
        pane = next((p for p in hou.ui.paneTabs() if isinstance(p, hou.NetworkEditor)), None)
        if not pane:
            hou.ui.displayMessage("No active Network Editor found.")
            return

        try:
            context, pos = pane.pwd(), pane.visibleBounds().center()
            creation_logic(context, pos)
        except Exception as e:
            hou.ui.displayMessage(f"Error during node creation: {e}")

    def create_swatches_in_geo(self):
        """Context-aware creation of individual color nodes from selected swatches."""
        self._execute_creation(lambda context, pos: self.mouseReleaseEvent(None, from_context_menu='nodes', context_override=(context, pos)))

    def create_color_in_selected_node(self):
        """Creates a color or gradient in the currently selected Houdini node."""
        selected_swatches = list(SwatchLabel.selected_labels or {self})
        if not selected_swatches:
            return

        selected_nodes = hou.selectedNodes()
        if not selected_nodes:
            hou.ui.displayMessage("No node selected in Houdini.", title="Selection Error")
            return
        
        target_node = selected_nodes[0]

        with hou.undos.group("Set Color from Swatch Panel"):
            if len(selected_swatches) > 1:
                self.create_gradient_in_node(target_node, selected_swatches)
            elif len(selected_swatches) == 1:
                self.create_single_color_in_node(target_node, selected_swatches[0])

    def create_gradient_in_node(self, node, swatches):
        """Finds a suitable ramp parameter on the node and applies the selected swatches as a gradient."""
        ramp_parm_names = ["ramp", "colorramp", "gradient", "vramp", "NT_TEX_GRADIENT", "rampcolordefault"]
        target_parm = None
        for name in ramp_parm_names:
            parm = node.parm(name)
            if parm and isinstance(parm.parmTemplate(), hou.RampParmTemplate):
                target_parm = parm
                break
        
        if not target_parm:
            hou.ui.displayMessage(f"No suitable ramp parameter found on '{node.name()}'.", title="Parameter Not Found")
            return

        sort_choice = hou.ui.displayMessage("Sort swatches by hue for the gradient?", buttons=["Yes", "No", "Cancel"], default_choice=0, close_choice=2)
        if sort_choice == 2: return
        swatches_to_use = self.sort_colors_by_hue(swatches) if sort_choice == 0 else swatches

        num = len(swatches_to_use)
        positions = [i / max(1, num - 1) for i in range(num)]
        colors = [s.rgb for s in swatches_to_use]
        ramp = hou.Ramp([hou.rampBasis.Linear] * num, positions, colors)
        target_parm.set(ramp)
        self.viewer.log(f"Set gradient on '{node.path()}.{target_parm.name()}'.")

    def create_single_color_in_node(self, node, swatch):
        """Finds a suitable color parameter on the node and applies the selected swatch color."""
        # List of common color parameter names to check for.
        color_parm_names = ["color", "singlevalue", "base_color", "NT_TEX_RGB"]

        for parm_name in color_parm_names:
            parm = node.parmTuple(parm_name)
            # Check if the parameter exists and is a 3-component (RGB) parameter.
            if parm and parm.parmTemplate().numComponents() == 3:
                try:
                    node.parmTuple(parm_name).set(swatch.rgb)
                    self.viewer.log(f"Set color on '{node.path()}.{parm_name}'.")
                    return # Exit after successfully setting the first found parameter.
                except hou.OperationFailed:
                    continue # Try the next parameter name if setting fails.
        
        hou.ui.displayMessage(f"No suitable color parameter found on '{node.name()}'.", title="Parameter Not Found")

class GridContainer(QtWidgets.QWidget):
    """A QWidget subclass to specifically handle mouse events for the grid background."""
    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer

    def mousePressEvent(self, event):
        self.viewer.handle_background_click(event)

class SwatchViewer(QtWidgets.QWidget):
    """The main widget for the ASE Swatch Viewer."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ASE Swatch Viewer")
        self.setMinimumSize(800, 500)

        config_path = os.path.join(hou.expandString("$HOUDINI_USER_PREF_DIR"), "ase_swatch_viewer_config.json")
        self.config_manager = ConfigManager(config_path)
        config = self.config_manager.load_config()
        self.default_path = config.get("default_path", os.path.expanduser("~"))
        self.custom_folders = config.get("custom_folders", [])

        self.swatches = []
        self.swatch_widgets = []
        self.current_swatch_size = 100
        self.size_buttons = {}
        
        self._resize_timer = QtCore.QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._delayed_relayout)

        self._init_ui()
        self.populate_folder_tree()
        self.populate_path_dropdown()
        self.load_first_ase_file()

    def _init_ui(self):
        self.tabs = QtWidgets.QTabWidget(self)
        self.library_tab, self.pref_tab = QtWidgets.QWidget(), QtWidgets.QWidget()
        self.tabs.addTab(self.library_tab, "Library")
        self.tabs.addTab(self.pref_tab, "Preference")

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.addWidget(self.tabs)
        self.setLayout(main_layout)

        lib_layout = QtWidgets.QVBoxLayout(self.library_tab)
        
        # --- Top Toolbar Layout ---
        top_bar_layout = QtWidgets.QHBoxLayout()
        
        self.path_dropdown = QtWidgets.QComboBox()
        self.path_dropdown.setEditable(True)
        self.path_dropdown.lineEdit().editingFinished.connect(self.on_path_edit_finished)
        self.path_dropdown.setStyleSheet("QComboBox { padding-right: 7px; }")
        self.path_dropdown.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

        # Size Controls (Using standard Houdini grid icons)
        size_layout = QtWidgets.QHBoxLayout()
        self.btn_small = QtWidgets.QPushButton()
        self.btn_med = QtWidgets.QPushButton()
        self.btn_large = QtWidgets.QPushButton()
        
        self.size_buttons = {50: self.btn_small, 100: self.btn_med, 150: self.btn_large}
        
        self.btn_small.setIcon(hou.qt.Icon("BUTTONS_grid_small"))
        self.btn_med.setIcon(hou.qt.Icon("BUTTONS_grid_medium"))
        self.btn_large.setIcon(hou.qt.Icon("BUTTONS_grid_large"))

        self.btn_small.setToolTip("small grid size")
        self.btn_med.setToolTip("medium grid size")
        self.btn_large.setToolTip("large grid size")

        for size, btn in self.size_buttons.items():
            btn.setFixedWidth(30)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, s=size: self.set_grid_size(s))
        
        size_layout.addWidget(self.btn_small)
        size_layout.addWidget(self.btn_med)
        size_layout.addWidget(self.btn_large)
        top_bar_layout.addWidget(self.path_dropdown)
        top_bar_layout.addLayout(size_layout)
        lib_layout.addLayout(top_bar_layout)

        # Left Panel (Folder Tree)
        self.folder_tree = FolderTree()
        self.folder_tree.itemSelectionChanged.connect(self.on_tree_selection)
        self.folder_tree.folder_dropped.connect(self.add_custom_folder)

        # Right Panel (Swatches Grid)
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        
        self.container = GridContainer(self)
        self.grid = QtWidgets.QGridLayout(self.container)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.grid.setVerticalSpacing(20)
        self.scroll_area.setWidget(self.container)
        self.container.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.container.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        
        # --- Splitter Setup ---
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.folder_tree)
        self.splitter.addWidget(self.scroll_area)
        self.splitter.setSizes([200, 600]) # Set default splitter sizes
        lib_layout.addWidget(self.splitter, 1) # Add splitter and give it stretch factor

        # --- Console with Toolbar ---
        console_container = QtWidgets.QWidget()
        console_container_layout = QtWidgets.QVBoxLayout(console_container)
        console_container_layout.setContentsMargins(0, 0, 0, 0)
        console_container_layout.setSpacing(0)

        console_toolbar = QtWidgets.QHBoxLayout()
        console_toolbar.setContentsMargins(0, 2, 4, 2)
        console_toolbar.addStretch()
        self.btn_clear_console = QtWidgets.QPushButton("Clear")
        self.btn_clear_console.setFixedSize(50, 20)
        self.btn_clear_console.setStyleSheet("font-size: 11px;")
        console_toolbar.addWidget(self.btn_clear_console)

        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(100)
        self.console.setStyleSheet("background-color: #111; color: #eee; font-family: Consolas;")
        console_container_layout.addLayout(console_toolbar)
        console_container_layout.addWidget(self.console)
        lib_layout.addWidget(console_container)

        # Preference Tab
        pref_layout = QtWidgets.QVBoxLayout(self.pref_tab)
        self.pref_edit = QtWidgets.QLineEdit(self.default_path)
        self.pref_edit.setPlaceholderText("Default ASE directory path")
        self.pref_edit.editingFinished.connect(self.save_preference)
        pref_layout.addWidget(QtWidgets.QLabel("Default ASE Path:"))
        pref_layout.addWidget(self.pref_edit)
        
        self.btn_clear_folders = QtWidgets.QPushButton("Clear Custom Dropped Folders")
        self.btn_clear_folders.clicked.connect(self.clear_custom_folders)
        pref_layout.addWidget(self.btn_clear_folders)
        pref_layout.addStretch()
        
        self.set_grid_size(self.current_swatch_size, force_update=True)
        
        # --- Connections ---
        self.btn_clear_console.clicked.connect(self.console.clear)
        self.container.customContextMenuRequested.connect(lambda pos: self.show_context_menu(self.container.mapToGlobal(pos)))

    def load_first_ase_file(self):
        """Finds and loads the first .ase file it can find in the tree."""
        iterator = QtWidgets.QTreeWidgetItemIterator(self.folder_tree)
        while iterator.value():
            item = iterator.value()
            item_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
            if item_type == "file":
                # Setting the current item will trigger the on_tree_selection
                # method, which handles loading the swatches and updating the UI.
                self.folder_tree.setCurrentItem(item)
                return
            iterator += 1

    def handle_background_click(self, event):
        """Handles left-clicks on the grid background to deselect swatches."""
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            if SwatchLabel.selected_labels:
                for label in list(SwatchLabel.selected_labels):
                    label.set_selected(False)
                SwatchLabel.selected_labels.clear()
                SwatchLabel.last_clicked = None
                self.log("Selection cleared.")

    def show_context_menu(self, global_pos, clicked_swatch=None):
        """Creates and shows the right-click context menu."""
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("""
            QMenu { background-color: #3C3C3C; color: #DDDDDD; border: 1px solid #2A2A2A; }
            QMenu::item:selected { background-color: #555555; }
        """)

        # Use the clicked swatch if available, otherwise find the first selected one or the first in the grid
        active_swatch = clicked_swatch
        if not active_swatch:
            if SwatchLabel.selected_labels:
                active_swatch = next(iter(SwatchLabel.selected_labels))
            elif self.swatch_widgets:
                active_swatch = self.swatch_widgets[0]

        if active_swatch:
            menu.addAction("Create Node(s) in Network...", active_swatch.create_swatches_in_geo)
            menu.addSeparator()
            menu.addAction("Create Color in Selected Node", active_swatch.create_color_in_selected_node)
            menu.exec(global_pos)

    def save_preference(self):
        path = self.pref_edit.text().strip()
        if os.path.isdir(path):
            self.default_path = path
            self.save_config_state()
            self.log(f"Default path saved: {path}")
            self.populate_path_dropdown()
            self.populate_folder_tree()
        else:
            self.log(f"Invalid path: {path}")

    def save_config_state(self):
        self.config_manager.save_config({
            "default_path": self.default_path,
            "custom_folders": self.custom_folders
        })

    def log(self, message):
        self.console.appendPlainText(str(message))

    # --- Folder Tree Methods ---
    def populate_folder_tree(self):
        self.folder_tree.clear()
        
        # Add Default Path
        if os.path.isdir(self.default_path):
            default_item = QtWidgets.QTreeWidgetItem(self.folder_tree, ["Default Library"])
            default_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, self.default_path)
            default_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "folder")
            default_item.setIcon(0, hou.qt.Icon("BUTTONS_folder"))
            default_item.setExpanded(True)
            self._build_tree_recursive(self.default_path, default_item)

        # Add Custom Dropped Folders
        if self.custom_folders:
            custom_root = QtWidgets.QTreeWidgetItem(self.folder_tree, ["Custom Folders"])
            custom_root.setExpanded(True)
            for folder in self.custom_folders:
                if os.path.isdir(folder):
                    item = QtWidgets.QTreeWidgetItem(custom_root, [os.path.basename(folder)])
                    item.setData(0, QtCore.Qt.ItemDataRole.UserRole, folder)
                    item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "folder")
                    item.setIcon(0, hou.qt.Icon("BUTTONS_folder"))
                    self._build_tree_recursive(folder, item)

    def _build_tree_recursive(self, path, parent_item):
        try:
            items = sorted(os.listdir(path))
            folders = []
            ase_files = []
            
            # Sort items into folders and ASE files
            for item in items:
                full_path = os.path.join(path, item)
                if os.path.isdir(full_path):
                    folders.append((item, full_path))
                elif item.lower().endswith(".ase"):
                    ase_files.append((item, full_path))
            
            # Populate folders first
            for item_name, full_path in folders:
                tree_item = QtWidgets.QTreeWidgetItem(parent_item, [item_name])
                tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, full_path)
                tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "folder")
                tree_item.setIcon(0, hou.qt.Icon("BUTTONS_folder"))
                
                # RECURSIVE CALL: Actually look inside the nested folders
                self._build_tree_recursive(full_path, tree_item)
                
            # Then populate .ase files at this level
            for item_name, full_path in ase_files:
                tree_item = QtWidgets.QTreeWidgetItem(parent_item, [item_name])
                tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, full_path)
                tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "file")
                tree_item.setIcon(0, hou.qt.Icon("SOP_color")) # Changed from DATATYPES_color to SOP_color
                
        except OSError:
            pass

    def on_tree_selection(self):
        selected = self.folder_tree.selectedItems()
        if selected:
            item = selected[0]
            path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            item_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
            
            if item_type == "folder":
                self.path_dropdown.setCurrentText(path)
            elif item_type == "file":
                # Update dropdown to show the parent directory of the file
                parent_dir = os.path.dirname(path)
                self.path_dropdown.setCurrentText(parent_dir)
                
                # Load the swatches for the selected .ase file
                self.load_selected_ase(path)

    def add_custom_folder(self, path):
        if path not in self.custom_folders and path != self.default_path:
            self.custom_folders.append(path)
            self.save_config_state()
            self.populate_folder_tree()
            self.populate_path_dropdown()
            self.log(f"Added custom folder: {path}")

    def clear_custom_folders(self):
        self.custom_folders = []
        self.save_config_state()
        self.populate_folder_tree()
        self.populate_path_dropdown()
        self.log("Cleared custom dropped folders.")

    # --- Grid Methods ---
    def set_grid_size(self, size, force_update=False):
        if not force_update and self.current_swatch_size == size:
            self.size_buttons[size].setChecked(True) # Keep it checked if clicked again
            return
        self.current_swatch_size = size
        for s, btn in self.size_buttons.items():
            btn.setChecked(s == size)
            btn.setStyleSheet("background-color: #444444;" if s != size else "background-color: none;")
        self.populate_grid()

    def clear_grid(self):
            SwatchLabel.selected_labels.clear()
            SwatchLabel.last_clicked = None
            self.swatch_widgets = []
            while self.grid.count():
                if item := self.grid.takeAt(0):
                    if widget := item.widget():
                        widget.hide()
                        widget.setParent(None)
                        widget.deleteLater()

    def on_path_edit_finished(self):
        new_path = self.path_dropdown.currentText().strip()
        if os.path.isdir(new_path):
            if new_path not in [self.path_dropdown.itemText(i) for i in range(self.path_dropdown.count())]:
                self.path_dropdown.addItem(new_path)
            self.path_dropdown.setCurrentText(new_path)
        else:
            self.log(f"Invalid folder: {new_path}")

    def load_selected_ase(self, filepath):
        if os.path.exists(filepath):
            self.log(f"Loading ASE file: {filepath}")
            self.swatches = self.parse_ase(filepath)
            self.populate_grid()
            self.log(f"Loaded {len(self.swatches)} swatches.")

    def populate_path_dropdown(self):
            self.path_dropdown.blockSignals(True)
            self.path_dropdown.clear()
            
            paths_to_scan = [self.default_path] + self.custom_folders
            all_found_paths = set()

            for base_path in paths_to_scan:
                if os.path.isdir(base_path):
                    all_found_paths.add(base_path)
                    try:
                        paths = [dp for dp, _, fns in os.walk(base_path) if any(f.lower().endswith(".ase") for f in fns)]
                        all_found_paths.update(paths)
                    except OSError as e:
                        self.log(f"Error scanning directory {base_path}: {e}")

            if all_found_paths:
                self.path_dropdown.addItems(sorted(list(all_found_paths)))
            else:
                self.log("No valid paths found.")
                self.path_dropdown.addItem(self.default_path)
                
            self.path_dropdown.blockSignals(False)

    def populate_grid(self):
        self.clear_grid()
        if not self.swatches: return

        item_width = self.current_swatch_size + 10
        max_cols = max(1, self.scroll_area.viewport().width() // item_width)
        
        for i, (name, rgb) in enumerate(self.swatches):
            row, col = divmod(i, max_cols)
            swatch = SwatchLabel(name, rgb, self, size=self.current_swatch_size)
            self.swatch_widgets.append(swatch)

            name_label = QtWidgets.QLabel(name)
            name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            name_label.setToolTip(name)
            name_label.setFixedWidth(self.current_swatch_size)
            name_label.setStyleSheet("text-overflow: ellipsis; white-space: nowrap; overflow: hidden;")

            wrapper = QtWidgets.QWidget()
            vbox = QtWidgets.QVBoxLayout(wrapper)
            vbox.setContentsMargins(0, 0, 0, 0)
            vbox.setSpacing(4)
            vbox.addWidget(swatch, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
            vbox.addWidget(name_label, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(wrapper, row, col)
        
        self.grid.setRowStretch(self.grid.rowCount(), 1)

    def keyPressEvent(self, event):
            if event.key() == QtCore.Qt.Key.Key_Escape:
                for label in list(SwatchLabel.selected_labels):
                    label.set_selected(False)
                SwatchLabel.selected_labels.clear()
                SwatchLabel.last_clicked = None
            else:
                super().keyPressEvent(event)
                
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