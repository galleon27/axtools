import os
import struct
import hou
import json
import re
import math
import colorsys
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt

# --- GLOBAL CONSTANTS ---
RAMP_PARM_NAMES = ("ramp", "colorramp", "gradient", "vramp", "NT_TEX_GRADIENT", "rampcolordefault", "octane_gradient")
COLOR_PARM_NAMES = ("color", "singlevalue", "base_color", "NT_TEX_RGB")

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
    delete_requested = QtCore.Signal(QtWidgets.QTreeWidgetItem)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)

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

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Delete:
            selected = self.selectedItems()
            if selected:
                self.delete_requested.emit(selected[0])
                return
        super().keyPressEvent(event)

class SelectableLabel(QtWidgets.QLabel):
    """A base class for shared swatch/gradient selection and drag-and-drop logic."""
    selected_labels = set()
    last_clicked = None

    KARMA_CONTEXTS = ('materialbuilder', 'materiallibrary', 'karmamaterialbuilder')
    OCTANE_CONTEXTS = ('octane_vopnet', 'octane_solaris_material_builder')
    REDSHIFT_CONTEXTS = ('redshift_vopnet', 'rs_usd_material_builder')
    MATNET_CONTEXTS = ('matnet',)

    def __init__(self, name, viewer, size=100, parent=None):
        super().__init__(parent)
        self.name = name
        self.viewer = viewer
        self.setFixedSize(size, size)
        self.setToolTip(f"{self.name}")
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self._drag_active = False
        self._has_moved = False
        self._selected = False

    def set_selected(self, selected):
        raise NotImplementedError("Subclasses must implement set_selected")

    def handle_network_drop(self, pane, pos):
        raise NotImplementedError("Subclasses must implement handle_network_drop")

    def mousePressEvent(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return

        self._drag_active = True
        self._has_moved = False
        self._start_pos = event.pos()

        modifiers = QtWidgets.QApplication.keyboardModifiers()

        if modifiers == (QtCore.Qt.KeyboardModifier.ShiftModifier | QtCore.Qt.KeyboardModifier.ControlModifier):
            pass 
        elif modifiers == QtCore.Qt.KeyboardModifier.ShiftModifier:
            if SelectableLabel.last_clicked and SelectableLabel.last_clicked in self.viewer.swatch_widgets:
                start_index = self.viewer.swatch_widgets.index(SelectableLabel.last_clicked)
                end_index = self.viewer.swatch_widgets.index(self)
                start, end = min(start_index, end_index), max(start_index, end_index)
                if not (modifiers & QtCore.Qt.KeyboardModifier.ControlModifier):
                    for label in list(SelectableLabel.selected_labels):
                        label.set_selected(False)
                    SelectableLabel.selected_labels.clear()
                for i in range(start, end + 1):
                    widget = self.viewer.swatch_widgets[i]
                    widget.set_selected(True)
                    SelectableLabel.selected_labels.add(widget)
        elif modifiers == QtCore.Qt.KeyboardModifier.ControlModifier:
            self.set_selected(not self._selected)
            if self._selected:
                SelectableLabel.selected_labels.add(self)
                SelectableLabel.last_clicked = self
            else:
                SelectableLabel.selected_labels.discard(self)
        else:
            for label in list(SelectableLabel.selected_labels):
                label.set_selected(False)
            SelectableLabel.selected_labels.clear()
            self.set_selected(True)
            SelectableLabel.selected_labels.add(self)
            SelectableLabel.last_clicked = self

    def mouseMoveEvent(self, event):
        if self._drag_active and (event.pos() - self._start_pos).manhattanLength() > 5:
            self._has_moved = True
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        if not self._has_moved:
            self._drag_active = False
            return

        self._drag_active = False
        pane = hou.ui.paneTabUnderCursor()
        if not isinstance(pane, hou.NetworkEditor): return
        self.handle_network_drop(pane, pane.cursorPosition())

    def contextMenuEvent(self, event):
        self.viewer.show_context_menu(event.globalPos(), self)

    def create_swatches_in_geo(self):
        pane = next((p for p in hou.ui.paneTabs() if isinstance(p, hou.NetworkEditor)), None)
        if not pane:
            hou.ui.displayMessage("No active Network Editor found.")
            return
        try:
            self.handle_network_drop(pane, pane.visibleBounds().center())
        except Exception as e:
            hou.ui.displayMessage(f"Error during node creation: {e}")

class GradientLabel(SelectableLabel):
    """A custom QLabel to display a saved Gradient ramp."""
    def __init__(self, name, colors, viewer, size=100, parent=None):
        super().__init__(name, viewer, size, parent)
        self.colors = colors
        
        stops = []
        for i, rgb in enumerate(colors):
            pos = i / max(1, len(colors) - 1)
            r, g, b = [int(c * 255) for c in rgb]
            stops.append(f"stop:{pos} rgb({r},{g},{b})")
        
        grad_css = f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, {', '.join(stops)});"
        self.setStyleSheet(f"{grad_css} border: 1px solid black;")

    def set_selected(self, selected):
        self._selected = selected
        border = "3px solid #33AADD" if self._selected else "1px solid black"
        current_style = self.styleSheet()
        base_style = current_style.split('border:')[0]
        self.setStyleSheet(f"{base_style} border: {border};")

    def delete_gradient(self):
        def find_and_remove(data_dict, key_to_delete):
            if key_to_delete in data_dict:
                del data_dict[key_to_delete]
                return True
            for key, value in data_dict.items():
                if isinstance(value, dict) and find_and_remove(value, key_to_delete):
                    return True
            return False
        return find_and_remove(self.viewer.saved_gradients, self.name)

    def apply_to_node(self):
        selected_nodes = hou.selectedNodes()
        if not selected_nodes:
            hou.ui.displayMessage("No node selected in Houdini.")
            return
            
        target_node = selected_nodes[0]
        target_parm = None
        
        for name in RAMP_PARM_NAMES:
            parm = target_node.parm(name)
            if parm and isinstance(parm.parmTemplate(), hou.RampParmTemplate):
                target_parm = parm
                break
        
        if not target_parm:
            hou.ui.displayMessage(f"No suitable ramp parameter found on '{target_node.name()}'.")
            return

        num = len(self.colors)
        positions = [i / max(1, num - 1) for i in range(num)]
        safe_colors = [tuple(c) for c in self.colors]
        ramp = hou.Ramp([hou.rampBasis.Linear] * num, positions, safe_colors)
        with hou.undos.group("Apply Saved Gradient"):
            target_parm.set(ramp)
        self.viewer.log(f"Set gradient on '{target_node.path()}.{target_parm.name()}'.")

    def handle_network_drop(self, pane, pos):
        context = pane.pwd()
        selected_items = list(SelectableLabel.selected_labels or {self})
        selected_gradients = [item for item in selected_items if isinstance(item, GradientLabel)]
        
        created_nodes = []
        try:
            context_type = context.type().name()
            category = context.childTypeCategory().name()

            if category == 'Sop':
                created_nodes = self._create_sop_gradient(context, selected_gradients, pos)
            elif context_type in self.KARMA_CONTEXTS:
                created_nodes = self._create_karma_gradient(context, selected_gradients, pos)
            elif context_type in self.OCTANE_CONTEXTS:
                created_nodes = self._create_octane_gradient(context, selected_gradients, pos)
            elif context_type in self.REDSHIFT_CONTEXTS:
                created_nodes = self._create_redshift_gradient(context, selected_gradients, pos)
            elif context_type in self.MATNET_CONTEXTS or category == 'Vop':
                created_nodes = self._create_matnet_gradient(context, selected_gradients, pos)
            else:
                hou.ui.displayMessage(f"Unsupported network context for gradient: {context_type}")

            if created_nodes:
                created_nodes[-1].setSelected(True, clear_all_selected=True)
                if context.childTypeCategory().name() == 'Sop':
                    pane.setCurrentNode(created_nodes[-1])
        except Exception as e:
            hou.ui.displayMessage(f"Error creating gradient: {e}")

    def _create_gradient(self, context, gradients, pos, node_type, parm_name):
        colors = gradients[0].colors
        gradient_name = self.name if len(colors) > 1 else sanitize_name(self.name)
        node = context.createNode(node_type)
        node.setName(gradient_name, unique_name=True)
        node.setPosition(pos)
        num = len(colors)
        positions = [i / max(1, num - 1) for i in range(num)]
        safe_colors = [tuple(c) for c in colors]
        ramp = hou.Ramp([hou.rampBasis.Linear] * num, positions, safe_colors)
        node.parm(parm_name).set(ramp)
        return [node]

    def _create_sop_gradient(self, context, gradients, pos):
        node = self._create_gradient(context, gradients, pos, "color", "ramp")
        node[0].parm("colortype").set(3)
        return node
    def _create_karma_gradient(self, context, gradients, pos):
        return self._create_gradient(context, gradients, pos, "kma_rampconst", "vramp")
    def _create_octane_gradient(self, context, gradients, pos):
        return self._create_gradient(context, gradients, pos, "NT_TEX_GRADIENT", "octane_gradient")
    def _create_redshift_gradient(self, context, gradients, pos):
        return self._create_gradient(context, gradients, pos, "redshift::RSRamp", "ramp")
    def _create_matnet_gradient(self, context, gradients, pos):
        return self._create_gradient(context, gradients, pos, "rampparm", "rampcolordefault")

class SwatchLabel(SelectableLabel):
    """A custom QLabel to display a color swatch."""
    def __init__(self, name, rgb, viewer, size=100, parent=None):
            super().__init__(name, viewer, size, parent)
            self.rgb = rgb
            r, g, b = [int(c * 255) for c in rgb]
            self.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: 1px solid black;")
            self.setToolTip(f"{self.name}\nRGB: {self.rgb}")
            self._button = None

    def set_selected(self, selected):
        self._selected = selected
        border = "3px solid #33AADD" if self._selected else "1px solid black"
        r, g, b = [int(c * 255) for c in self.rgb]
        self.setStyleSheet(f"background-color: rgb({r},{g},{b}); border: {border};")

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self._button = event.button()

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if not pane:
                hou.ui.displayMessage("No active Network Editor found.")
                return

            context = pane.pwd()
            pos = pane.visibleBounds().center()
            try:
                self.handle_network_drop(pane, pos, from_context_menu='nodes')
            except Exception as e:
                hou.ui.displayMessage(f"Error creating node: {e}")

    @staticmethod
    def sort_colors_by_hue(swatches):
        """Sorts swatches based on their hue value using standard colorsys."""
        return sorted(swatches, key=lambda s: colorsys.rgb_to_hsv(*s.rgb)[0])

    def handle_network_drop(self, pane, pos, from_context_menu=None, context_override=None):
        if context_override:
            context, pos = context_override
        else:
            context, pos = pane.pwd(), pos if isinstance(pos, hou.Vector2) else pane.cursorPosition()

        if from_context_menu:
            swatches_to_create = list(SelectableLabel.selected_labels or {self})
        else:
            if self._button == QtCore.Qt.MouseButton.MiddleButton:
                if self in SelectableLabel.selected_labels:
                    swatches_to_create = list(SelectableLabel.selected_labels)
                else:
                    swatches_to_create = [self]
            else:
                swatches_to_create = list(SelectableLabel.selected_labels or {self})
        
        if not swatches_to_create: return []

        created_nodes = []
        try:
            context_type = context.type().name()
            category = context.childTypeCategory().name()

            if category == 'Sop':
                created_nodes = self._handle_node_or_gradient_creation(context, swatches_to_create, pos, self._create_sop_nodes, self._create_sop_gradient)
            elif context_type in self.KARMA_CONTEXTS:
                created_nodes = self._handle_node_or_gradient_creation(context, swatches_to_create, pos, self._create_karma_nodes, self._create_karma_gradient)
            elif context_type in self.OCTANE_CONTEXTS:
                created_nodes = self._handle_node_or_gradient_creation(context, swatches_to_create, pos, self._create_octane_nodes, self._create_octane_gradient)
            elif context_type in self.REDSHIFT_CONTEXTS:
                created_nodes = self._handle_node_or_gradient_creation(context, swatches_to_create, pos, self._create_redshift_nodes, self._create_redshift_gradient)
            elif context_type in self.MATNET_CONTEXTS or category == 'Vop':
                created_nodes = self._handle_node_or_gradient_creation(context, swatches_to_create, pos, self._create_matnet_nodes, self._create_matnet_gradient)
            elif category == 'Object':
                created_nodes = self._create_object_nodes(context, swatches_to_create, pos)
            else:
                hou.ui.displayMessage(f"Unsupported network context for drag & drop: {context_type}")

            if created_nodes:
                created_nodes[-1].setSelected(True, clear_all_selected=True)
                if context.childTypeCategory().name() == 'Sop' and not context_override:
                    pane.setCurrentNode(created_nodes[-1])
        except Exception as e:
            hou.ui.displayMessage(f"Error creating node(s): {e}")
        return created_nodes

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)

    def _handle_node_or_gradient_creation(self, context, selected, pos, node_creation_func, gradient_creation_func):
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

    def create_color_in_selected_node(self):
        selected_swatches = list(SelectableLabel.selected_labels or {self})
        if not selected_swatches: return

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
        target_parm = None
        for name in RAMP_PARM_NAMES:
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
        for parm_name in COLOR_PARM_NAMES:
            parm = node.parmTuple(parm_name)
            if parm and parm.parmTemplate().numComponents() == 3:
                try:
                    node.parmTuple(parm_name).set(swatch.rgb)
                    self.viewer.log(f"Set color on '{node.path()}.{parm_name}'.")
                    return 
                except hou.OperationFailed:
                    continue 
        hou.ui.displayMessage(f"No suitable color parameter found on '{node.name()}'.", title="Parameter Not Found")

class GridContainer(QtWidgets.QWidget):
    """A QWidget subclass to specifically handle mouse events for the grid background."""
    node_dropped = QtCore.Signal(hou.Node)

    def __init__(self, viewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        self.setAcceptDrops(True)

    def mousePressEvent(self, event):
        self.viewer.handle_background_click(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText():
            node_path = event.mimeData().text()
            node = hou.node(node_path)
            if node and self.viewer.get_ramp_parm_from_node(node):
                event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasText():
            node_path = event.mimeData().text()
            node = hou.node(node_path)
            if node and self.viewer.get_ramp_parm_from_node(node):
                event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasText():
            node_path = event.mimeData().text()
            node = hou.node(node_path)
            if node and self.viewer.get_ramp_parm_from_node(node):
                self.node_dropped.emit(node)
                event.acceptProposedAction()
                return
        event.ignore()

class SwatchViewer(QtWidgets.QWidget):
    """The main widget for the ASE Swatch Viewer."""
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ASE Swatch Viewer")
        self.setMinimumSize(800, 500)
        self.setStyleSheet(hou.qt.styleSheet())

        config_path = os.path.join(hou.expandString("$HOUDINI_USER_PREF_DIR"), "ase_swatch_viewer_config.json")
        self.config_manager = ConfigManager(config_path)
        config = self.config_manager.load_config()
        self.default_path = config.get("default_path", os.path.expanduser("~"))
        self.custom_folders = config.get("custom_folders", [])
        self.saved_gradients = config.get("saved_gradients", {})

        self.swatches = []
        self.swatch_widgets = []
        self.current_swatch_size = 100
        self.size_buttons = {}
        self.current_view_mode = 'swatches' 
        self.current_gradient_dict = {}
        
        self._resize_timer = QtCore.QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._delayed_relayout)

        self._init_ui()
        self.populate_folder_tree()
        self.populate_path_dropdown()
        self.load_first_ase_file()

    def get_item_path(self, item):
        path = []
        while item is not None:
            path.insert(0, item.text(0))
            item = item.parent()
        return path

    def create_gradient_folder(self):
        selected_items = self.folder_tree.selectedItems()
        if not selected_items:
            self.log("No folder selected in the tree.")
            return

        item = selected_items[0]
        item_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
        if item_type not in ("virtual_folder", "gradient_folder"):
            self.log("Please select 'Saved Gradients' or a subfolder to create a new folder in.")
            return

        name_tuple = hou.ui.readInput("Enter name for new folder:", buttons=("OK", "Cancel"), title="Create Gradient Folder")
        if name_tuple[0] == 1 or not name_tuple[1].strip(): return
        name = name_tuple[1].strip()

        path_parts = []
        temp_item = item
        while temp_item and temp_item.text(0) != "Saved Gradients":
            path_parts.insert(0, temp_item.text(0))
            temp_item = temp_item.parent()
        
        target_dict = self.saved_gradients
        for part in path_parts:
            target_dict = target_dict.get(part, {})

        target_dict[name] = {}
        self.save_config_state()
        self.log(f"Created gradient folder: '{name}'")
        self._add_gradient_folder_item(item, name)

    def handle_tree_delete(self, item):
        item_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
        if item_type == "gradient_folder":
            self.delete_gradient_folder(item)

    def _init_ui(self):
        self.tabs = QtWidgets.QTabWidget(self)
        self.library_tab, self.pref_tab = QtWidgets.QWidget(), QtWidgets.QWidget()
        self.tabs.addTab(self.library_tab, "Library")
        self.tabs.addTab(self.pref_tab, "Preference")

        main_layout = QtWidgets.QVBoxLayout(self)
        main_layout.addWidget(self.tabs)
        self.setLayout(main_layout)

        lib_layout = QtWidgets.QVBoxLayout(self.library_tab)
        
        top_bar_layout = QtWidgets.QHBoxLayout()
        self.path_dropdown = QtWidgets.QComboBox()
        self.path_dropdown.setEditable(True)
        self.path_dropdown.lineEdit().editingFinished.connect(self.on_path_edit_finished)
        self.path_dropdown.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

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

        self.folder_tree = FolderTree()
        self.folder_tree.itemSelectionChanged.connect(self.on_tree_selection)
        self.folder_tree.folder_dropped.connect(self.add_custom_folder)
        self.folder_tree.delete_requested.connect(self.handle_tree_delete)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        
        self.container = GridContainer(self)
        self.grid = QtWidgets.QGridLayout(self.container)
        self.grid.setContentsMargins(10, 10, 10, 10)
        self.grid.setSpacing(6)
        self.grid.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
        
        self.scroll_area.setWidget(self.container)
        self.container.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding)
        self.container.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.folder_tree)
        self.splitter.addWidget(self.scroll_area)
        self.splitter.setSizes([200, 600]) 
        self.folder_tree.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        lib_layout.addWidget(self.splitter, 1)

        console_container = QtWidgets.QWidget()
        console_container_layout = QtWidgets.QVBoxLayout(console_container)
        console_container_layout.setContentsMargins(0, 0, 0, 0)
        console_container_layout.setSpacing(0)

        console_toolbar = QtWidgets.QHBoxLayout()
        console_toolbar.setContentsMargins(0, 2, 4, 2)
        console_toolbar.addStretch()
        self.btn_clear_console = QtWidgets.QPushButton("Clear")
        self.btn_clear_console.setFixedSize(50, 20)
        console_toolbar.addWidget(self.btn_clear_console)

        self.console = QtWidgets.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumHeight(100)
        self.console.setStyleSheet("background-color: #111; color: #eee; font-family: Consolas;") 
        console_container_layout.addLayout(console_toolbar)
        console_container_layout.addWidget(self.console)
        lib_layout.addWidget(console_container)

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
        
        self.btn_clear_console.clicked.connect(self.console.clear)
        self.container.customContextMenuRequested.connect(lambda pos: self.show_context_menu(self.container.mapToGlobal(pos)))
        self.container.node_dropped.connect(self.save_ramp_from_node)
        self.folder_tree.customContextMenuRequested.connect(self.show_folder_tree_context_menu)

    def select_item_by_path(self, path):
        if not path: return
        def find_child(parent_item, text):
            for i in range(parent_item.childCount()):
                child = parent_item.child(i)
                if child.text(0) == text:
                    return child
            return None

        current_item = self.folder_tree.invisibleRootItem()
        for part in path:
            current_item = find_child(current_item, part)
            if not current_item: return
        self.folder_tree.setCurrentItem(current_item)

    def get_ramp_parm_from_node(self, node):
        for name in RAMP_PARM_NAMES:
            parm = node.parm(name)
            if parm and isinstance(parm.parmTemplate(), hou.RampParmTemplate):
                return parm
        return None

    def save_ramp_from_node(self, node):
        parm = self.get_ramp_parm_from_node(node)
        if not parm:
            self.log(f"No suitable ramp parameter found on dropped node: {node.path()}")
            return

        ramp = parm.eval()
        if not isinstance(ramp, hou.Ramp) or not ramp.values():
            self.log(f"Ramp '{parm.path()}' is empty or invalid.")
            return

        name_tuple = hou.ui.readInput("Enter name for new gradient:", buttons=("OK", "Cancel"), title="Save Gradient from Parm")
        if name_tuple[0] == 1 or not name_tuple[1].strip(): return
        name = name_tuple[1].strip()

        colors = [tuple(c) for c in ramp.values()]
        
        target_dict = self.saved_gradients
        selected_items = self.folder_tree.selectedItems()
        if selected_items:
            item = selected_items[0]
            item_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
            if item_type == "gradient_folder" or item_type == "virtual_folder":
                path_parts = []
                temp_item = item
                while temp_item and temp_item.text(0) != "Saved Gradients":
                    path_parts.insert(0, temp_item.text(0))
                    temp_item = temp_item.parent()
                
                for part in path_parts:
                    target_dict = target_dict.get(part, {})

        target_dict[name] = colors
        self.save_config_state()
        self.log(f"Saved gradient '{name}' from node '{node.path()}'")
        self.populate_grid()

    def load_first_ase_file(self):
        iterator = QtWidgets.QTreeWidgetItemIterator(self.folder_tree)
        while iterator.value():
            item = iterator.value()
            item_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
            if item_type == "file":
                self.folder_tree.setCurrentItem(item)
                return
            iterator += 1

    def handle_background_click(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if SelectableLabel.selected_labels:
                for label in list(SelectableLabel.selected_labels):
                    label.set_selected(False)
                SelectableLabel.selected_labels.clear()
                SelectableLabel.last_clicked = None
                self.log("Selection cleared.")

    def show_context_menu(self, global_pos, clicked_swatch=None):
        menu = QtWidgets.QMenu(self)

        active_swatch = clicked_swatch
        if not active_swatch:
            if SelectableLabel.selected_labels:
                active_swatch = next(iter(SelectableLabel.selected_labels))
            elif self.swatch_widgets and isinstance(self.swatch_widgets[0], SwatchLabel):
                active_swatch = self.swatch_widgets[0]

        if active_swatch:
            menu.addAction("Create Node(s) in Network...", active_swatch.create_swatches_in_geo)
            menu.addSeparator()

            if isinstance(active_swatch, SwatchLabel):
                menu.addAction("Create Color in Selected Node", active_swatch.create_color_in_selected_node)
            elif isinstance(active_swatch, GradientLabel):
                menu.addAction("Apply to Selected Node", active_swatch.apply_to_node)

            selected_gradients = [label for label in SelectableLabel.selected_labels if isinstance(label, GradientLabel)]
            if isinstance(active_swatch, GradientLabel) or selected_gradients:
                menu.addSeparator()
                num_to_delete = len(selected_gradients)
                if num_to_delete > 1:
                    del_action = menu.addAction(f"Delete {num_to_delete} Gradients")
                else:
                    del_action = menu.addAction("Delete Gradient")
                del_action.triggered.connect(self.delete_selected_gradients)
                
            menu.exec(global_pos)

    def delete_selected_gradients(self):
        selected_gradients = [label for label in SelectableLabel.selected_labels if isinstance(label, GradientLabel)]
        if not selected_gradients:
            return

        deleted_count = 0
        for grad_label in selected_gradients:
            if grad_label.delete_gradient():
                deleted_count += 1
        
        if deleted_count > 0:
            self.save_config_state()
            self.log(f"Deleted {deleted_count} gradient(s).")
            self.populate_grid()

    def save_selected_as_gradient(self):
        selected = list(SelectableLabel.selected_labels)
        if len(selected) < 2: return

        choice = hou.ui.displayMessage("Sort swatches by hue?", buttons=["Yes", "No", "Cancel"], default_choice=0, close_choice=2)
        if choice == 2: return
        if choice == 0:
            selected = SwatchLabel.sort_colors_by_hue(selected)

        name_tuple = hou.ui.readInput("Enter name for new gradient:", buttons=("OK", "Cancel"), title="Save Gradient")
        if name_tuple[0] == 1 or not name_tuple[1].strip(): return
        name = name_tuple[1].strip()

        selected_path = None
        if self.folder_tree.selectedItems():
            selected_path = self.get_item_path(self.folder_tree.selectedItems()[0])

        colors = [s.rgb for s in selected]
        
        target_dict = self.saved_gradients
        if selected_path and selected_path[0] == "Saved Gradients":
             current_level = self.saved_gradients
             for part in selected_path[1:]:
                 current_level = current_level.get(part, {})
             target_dict = current_level

        target_dict[name] = colors
        self.save_config_state()
        self.log(f"Saved custom gradient: '{name}'")
        self.populate_grid()

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
            "custom_folders": self.custom_folders,
            "saved_gradients": self.saved_gradients
        })

    def log(self, message):
        self.console.appendPlainText(str(message))

    def populate_folder_tree(self):
        self.folder_tree.clear()
        
        grad_root_item = QtWidgets.QTreeWidgetItem(self.folder_tree, ["Saved Gradients"])
        grad_root_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, "GRADIENTS_VIRTUAL_PATH")
        grad_root_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "virtual_folder")
        try:
            grad_root_item.setIcon(0, hou.qt.Icon("VOP_ramp"))
        except hou.OperationFailed:
            grad_root_item.setIcon(0, hou.qt.Icon("SOP_color"))

        def build_gradient_tree(parent_item, gradients_dict):
            for name, value in sorted(gradients_dict.items()):
                if isinstance(value, dict):
                    folder_item = self._add_gradient_folder_item(parent_item, name)
                    build_gradient_tree(folder_item, value)

        build_gradient_tree(grad_root_item, self.saved_gradients)

        if os.path.isdir(self.default_path):
            default_item = QtWidgets.QTreeWidgetItem(self.folder_tree, ["Default Library"])
            default_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, self.default_path)
            default_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "folder")
            default_item.setIcon(0, hou.qt.Icon("BUTTONS_folder"))
            default_item.setExpanded(True)
            self._build_tree_recursive(self.default_path, default_item)

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

    def _add_gradient_folder_item(self, parent_item, name):
        folder_item = QtWidgets.QTreeWidgetItem(parent_item, [name])
        folder_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, name)
        folder_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "gradient_folder")
        folder_item.setIcon(0, hou.qt.Icon("BUTTONS_folder"))
        return folder_item

    def _build_tree_recursive(self, path, parent_item):
        try:
            items = sorted(os.listdir(path))
            folders = []
            ase_files = []
            
            for item in items:
                full_path = os.path.join(path, item)
                if os.path.isdir(full_path):
                    folders.append((item, full_path))
                elif item.lower().endswith(".ase"):
                    ase_files.append((item, full_path))
            
            for item_name, full_path in folders:
                tree_item = QtWidgets.QTreeWidgetItem(parent_item, [item_name])
                tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, full_path)
                tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "folder")
                tree_item.setIcon(0, hou.qt.Icon("BUTTONS_folder"))
                self._build_tree_recursive(full_path, tree_item)
                
            for item_name, full_path in ase_files:
                tree_item = QtWidgets.QTreeWidgetItem(parent_item, [item_name])
                tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, full_path)
                tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "file")
                tree_item.setIcon(0, hou.qt.Icon("SOP_color")) 
                
        except OSError:
            pass

    def on_tree_selection(self):
        selected = self.folder_tree.selectedItems()
        if selected:
            item = selected[0]
            path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            item_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
            
            if item_type in ("virtual_folder", "gradient_folder"):
                self.current_view_mode = 'gradients'
                
                path_parts = []
                temp_item = item
                while temp_item and temp_item.text(0) != "Saved Gradients":
                    path_parts.insert(0, temp_item.text(0))
                    temp_item = temp_item.parent()
                
                current_level = self.saved_gradients
                for part in path_parts:
                    if isinstance(current_level, dict):
                        current_level = current_level.get(part)
                
                self.current_gradient_dict = current_level if isinstance(current_level, dict) else {}
                self.path_dropdown.setCurrentText(f"Saved Gradients/{'/'.join(path_parts)}")
                self.populate_grid()

            elif item_type == "folder":
                self.current_view_mode = 'swatches'
                self.path_dropdown.setCurrentText(path)
                self.populate_grid()
                
            elif item_type == "file":
                self.current_view_mode = 'swatches'
                parent_dir = os.path.dirname(path)
                self.path_dropdown.setCurrentText(parent_dir)
                self.load_selected_ase(path)

    def show_folder_tree_context_menu(self, pos):
        item = self.folder_tree.itemAt(pos)
        if not item: return

        item_type = item.data(0, QtCore.Qt.ItemDataRole.UserRole + 1)
        if item_type in ("virtual_folder", "gradient_folder"):
            menu = QtWidgets.QMenu(self)
            menu.addAction("New Folder...", self.create_gradient_folder)
            if item_type == "gradient_folder":
                menu.addSeparator()
                menu.addAction("Delete Folder", lambda: self.delete_gradient_folder(item))
            menu.exec(self.folder_tree.mapToGlobal(pos))

    def delete_gradient_folder(self, item):
        folder_name = item.text(0)
        
        reply = QtWidgets.QMessageBox.question(self, 'Confirm Deletion', 
            f"Are you sure you want to permanently delete the folder '{folder_name}' and all its contents?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No, 
            QtWidgets.QMessageBox.StandardButton.No)

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            def find_and_remove_folder(data_dict, folder_to_delete):
                if folder_to_delete in data_dict:
                    del data_dict[folder_to_delete]
                    return True
                for key, value in data_dict.items():
                    if isinstance(value, dict) and find_and_remove_folder(value, folder_to_delete):
                        return True
                return False
            
            if find_and_remove_folder(self.saved_gradients, folder_name):
                self.save_config_state()
                self.log(f"Deleted folder: {folder_name}")
                self.populate_folder_tree()

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

    def set_grid_size(self, size, force_update=False):
        if not force_update and self.current_swatch_size == size:
            self.size_buttons[size].setChecked(True)
            return
        self.current_swatch_size = size
        for s, btn in self.size_buttons.items():
            btn.setChecked(s == size)
            btn.setStyleSheet("background-color: #444444;" if s != size else "background-color: none;")
        self.populate_grid()

    def clear_grid(self):
            SelectableLabel.selected_labels.clear()
            SelectableLabel.last_clicked = None
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
        item_width = self.current_swatch_size + 10 
        viewport_width = self.scroll_area.viewport().width()
        
        max_cols = max(1, math.ceil(viewport_width / max(1, item_width)))

        if self.current_view_mode == 'swatches':
            if not self.swatches: return
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
                wrapper.setContentsMargins(0, 0, 0, 10) 
                self.grid.addWidget(wrapper, row, col)
                
        elif self.current_view_mode == 'gradients':
            items_to_display = []
            for name, value in self.current_gradient_dict.items():
                if isinstance(value, list):
                    items_to_display.append((name, value))

            for i, (name, colors) in enumerate(items_to_display):
                row, col = divmod(i, max_cols)
                grad_widget = GradientLabel(name, colors, self, size=self.current_swatch_size)
                self.swatch_widgets.append(grad_widget)

                name_label = QtWidgets.QLabel(name)
                name_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                name_label.setToolTip(name)
                name_label.setFixedWidth(self.current_swatch_size)
                name_label.setStyleSheet("text-overflow: ellipsis; white-space: nowrap; overflow: hidden;")

                wrapper = QtWidgets.QWidget()
                vbox = QtWidgets.QVBoxLayout(wrapper)
                vbox.setContentsMargins(0, 0, 0, 0)
                vbox.setSpacing(4)
                vbox.addWidget(grad_widget, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
                vbox.addWidget(name_label, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
                wrapper.setContentsMargins(0, 0, 0, 10) 
                self.grid.addWidget(wrapper, row, col)

        self.grid.setRowStretch(self.grid.rowCount(), 1)

    def keyPressEvent(self, event):
            if event.key() == Qt.Key.Key_Escape:
                for label in list(SelectableLabel.selected_labels):
                    label.set_selected(False)
                SelectableLabel.selected_labels.clear()
                SelectableLabel.last_clicked = None
            elif event.key() == Qt.Key.Key_Delete:
                self.delete_selected_gradients()
            else:
                super().keyPressEvent(event)
                
    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start(100)

    def _delayed_relayout(self):
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