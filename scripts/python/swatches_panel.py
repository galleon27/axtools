"""ASE Swatch Viewer panel for Houdini.

Performance notes
-----------------
The swatch grid is ONE custom virtualized widget (SwatchGridView) instead of
3 QWidgets (plus several stylesheets) per swatch. Only the visible cells are
painted, so loading, scrolling, resizing and changing the grid size no longer
depend on how many swatches a file contains.
"""
import colorsys
import json
import os
import re
import struct
import time
from collections import OrderedDict

import hou
from PySide6 import QtWidgets, QtCore, QtGui
from PySide6.QtCore import Qt

# --- GLOBAL CONSTANTS ---
RAMP_PARM_NAMES = ("ramp", "colorramp", "gradient", "vramp", "NT_TEX_GRADIENT", "rampcolordefault", "octane_gradient")
COLOR_PARM_NAMES = ("color", "singlevalue", "base_color", "NT_TEX_RGB")

_NAME_STRIP_RE = re.compile(r'[^\w\s-]')
_WHITESPACE_RE = re.compile(r'\s+')


def cmyk_to_rgb(c, m, y, k):
    """Converts CMYK color values to RGB."""
    r = 1.0 - min(1.0, c * (1 - k) + k)
    g = 1.0 - min(1.0, m * (1 - k) + k)
    b = 1.0 - min(1.0, y * (1 - k) + k)
    return (r, g, b)


def sanitize_name(name):
    """Sanitize swatch names for Houdini node names"""
    sanitized = _NAME_STRIP_RE.sub('', name)
    sanitized = _WHITESPACE_RE.sub('_', sanitized)
    sanitized = sanitized.strip('_')
    if sanitized and not (sanitized[0].isalpha() or sanitized[0] == '_'):
        sanitized = 'swatch_' + sanitized
    if not sanitized:
        sanitized = 'unnamed_swatch'
    return sanitized


_ICON_CACHE = {}


def get_icon(name):
    """hou.qt.Icon() hits the icon system every call; cache the result."""
    icon = _ICON_CACHE.get(name)
    if icon is None:
        icon = _ICON_CACHE[name] = hou.qt.Icon(name)
    return icon


def sort_colors_by_hue(swatches):
    """Sorts swatches based on their hue value using standard colorsys."""
    return sorted(swatches, key=lambda s: colorsys.rgb_to_hsv(*s.rgb)[0])


def make_ramp(colors):
    num = len(colors)
    positions = [i / max(1, num - 1) for i in range(num)]
    return hou.Ramp([hou.rampBasis.Linear] * num, positions, [tuple(c) for c in colors])


def find_ramp_parm(node):
    for name in RAMP_PARM_NAMES:
        parm = node.parm(name)
        if parm and isinstance(parm.parmTemplate(), hou.RampParmTemplate):
            return parm
    return None


def find_network_editor():
    pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if pane:
        return pane
    return next((p for p in hou.ui.paneTabs() if isinstance(p, hou.NetworkEditor)), None)


# --- ASE parsing -------------------------------------------------------------
_U16 = struct.Struct(">H")
_BLOCK_HDR = struct.Struct(">HI")
_RGB = struct.Struct(">fff")
_CMYK = struct.Struct(">ffff")


def read_ase(path):
    """Parse an ASE file. Returns (list of (name, (r, g, b)), error_message_or_None).

    Uses precompiled structs + unpack_from (no per-field slicing/copying).
    Swatches read before an error are still returned.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except IOError as e:
        return [], f"Error reading file: {e}"

    if data[:4] != b"ASEF":
        return [], "Invalid ASE file header."

    swatches = []
    append = swatches.append
    pos, end = 12, len(data)
    try:
        while pos + 6 <= end:
            block_type, block_len = _BLOCK_HDR.unpack_from(data, pos)
            pos += 6
            block_end = pos + block_len
            if block_type == 0x0001:  # color entry
                name_len = _U16.unpack_from(data, pos)[0]
                name_start = pos + 2
                name = data[name_start:name_start + max(0, name_len - 1) * 2].decode("utf_16_be")
                model_pos = name_start + name_len * 2
                model = data[model_pos:model_pos + 4]
                values_pos = model_pos + 4
                if model == b"RGB ":
                    append((name, _RGB.unpack_from(data, values_pos)))
                elif model == b"CMYK":
                    append((name, cmyk_to_rgb(*_CMYK.unpack_from(data, values_pos))))
            pos = block_end
    except (struct.error, UnicodeDecodeError) as e:
        return swatches, f"Error parsing ASE block: {e}"
    return swatches, None

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

# --- Data items (plain Python objects - no widgets) ---------------------------
def _to_qcolor(rgb):
    r, g, b = (max(0, min(255, int(c * 255))) for c in rgb[:3])
    return QtGui.QColor(r, g, b)


class SwatchItem:
    __slots__ = ("name", "rgb", "color")

    def __init__(self, name, rgb):
        self.name = name
        self.rgb = rgb
        self.color = _to_qcolor(rgb)


class GradientItem:
    __slots__ = ("name", "colors", "stops")

    def __init__(self, name, colors):
        self.name = name
        self.colors = [tuple(c) for c in colors]
        num = len(self.colors)
        self.stops = [(i / max(1, num - 1), _to_qcolor(c)) for i, c in enumerate(self.colors)]


class SwatchGridView(QtWidgets.QAbstractScrollArea):
    """Virtualized swatch grid (replaces 3 widgets + stylesheets per swatch).

    * Cells have a FIXED size - they are never stretched.
    * Column count = ceil(viewport width / cell width), like the original panel: when a whole
      swatch doesn't fit, the last column is partly visible and reachable with the horizontal
      scrollbar, so there is no empty strip on the right.
    * Only the visible cells are painted, so cost is independent of the number of swatches.
    """
    drag_released = QtCore.Signal(int)                     # row dragged out of the view and released
    context_requested = QtCore.Signal(QtCore.QPoint, int)  # global pos, row (-1 = background)
    item_double_clicked = QtCore.Signal(int)
    delete_pressed = QtCore.Signal()
    node_dropped = QtCore.Signal(object)                   # hou.Node dropped onto the view

    SELECTED_COLOR = QtGui.QColor("#33AADD")
    BORDER_COLOR = QtGui.QColor(0, 0, 0)
    PAD = 4      # space around the swatch box
    GAP = 4      # box -> name
    BOTTOM = 10  # space under the name

    def __init__(self, ramp_checker, parent=None):
        super().__init__(parent)
        self._ramp_checker = ramp_checker
        self.items = []
        self._selected = set()
        self._anchor = -1                 # last plainly-clicked row (shift-click range start)
        self._swatch_size = 100
        self._cell_w, self._cell_h = 108, 130
        self._cols = 1
        self._press_row = -1
        self._press_pos = QtCore.QPoint()
        self._moved = False
        self._defer_select_row = -1       # pressed an already-selected item: decide on release
        self._hover_row = -1

        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.verticalScrollBar().setSingleStep(40)
        self.horizontalScrollBar().setSingleStep(40)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.viewport().setMouseTracking(True)
        self._bg_ref = QtWidgets.QWidget(self.viewport())   # never shown; only used to read the panel color
        self._bg_ref.hide()

    # ---- data ------------------------------------------------------------
    def set_items(self, items):
        self.items = items
        self._selected = set()
        self._anchor = -1
        self._hover_row = -1
        self.viewport().setToolTip("")
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self._update_layout()

    def item_at(self, row):
        return self.items[row] if 0 <= row < len(self.items) else None

    def selected_items(self):
        """Selected items in grid order."""
        return [self.items[r] for r in sorted(self._selected) if r < len(self.items)]

    def clear_selection(self):
        self._anchor = -1
        self._set_selection(set())

    def _set_selection(self, rows):
        if rows != self._selected:
            self._selected = rows
            self.viewport().update()

    # ---- geometry ----------------------------------------------------------
    def set_swatch_size(self, size):
        self._swatch_size = size
        self._cell_w = size + 2 * self.PAD
        self._cell_h = self.PAD + size + self.GAP + self.fontMetrics().height() + self.BOTTOM
        self._update_layout()

    @property
    def cell_size(self):
        return QtCore.QSize(self._cell_w, self._cell_h)

    def _update_layout(self):
        vw, vh = self.viewport().width(), self.viewport().height()
        self._cols = max(1, -(-vw // self._cell_w))               # ceil: allow a partly visible last column
        rows = -(-len(self.items) // self._cols)
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        hbar.setRange(0, max(0, self._cols * self._cell_w - vw))
        hbar.setPageStep(vw)
        vbar.setRange(0, max(0, rows * self._cell_h - vh))
        vbar.setPageStep(vh)
        self.viewport().update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_layout()

    def showEvent(self, event):
        super().showEvent(event)
        self.set_swatch_size(self._swatch_size)   # fonts are final once shown

    def visual_rect(self, row):
        r, c = divmod(row, self._cols)
        return QtCore.QRect(c * self._cell_w - self.horizontalScrollBar().value(),
                            r * self._cell_h - self.verticalScrollBar().value(),
                            self._cell_w, self._cell_h)

    def cell_at(self, pos):
        """Row of the whole cell (swatch + name + padding) under pos, or -1."""
        x = pos.x() + self.horizontalScrollBar().value()
        y = pos.y() + self.verticalScrollBar().value()
        if x < 0 or y < 0:
            return -1
        col, row = x // self._cell_w, y // self._cell_h
        if col >= self._cols:
            return -1
        idx = row * self._cols + col
        return idx if idx < len(self.items) else -1

    def row_at(self, pos):
        """Row whose swatch square is under pos, or -1. The gaps and name text count as
        background (like the original panel), so clicking them clears the selection."""
        idx = self.cell_at(pos)
        if idx < 0:
            return -1
        lx = (pos.x() + self.horizontalScrollBar().value()) % self._cell_w
        ly = (pos.y() + self.verticalScrollBar().value()) % self._cell_h
        lo, hi = self.PAD, self.PAD + self._swatch_size
        return idx if lo <= lx < hi and lo <= ly < hi else -1

    # ---- painting ------------------------------------------------------------
    def _background_color(self):
        """Houdini's stylesheet gives scroll-area viewports a lighter background. The original grid sat
        on a plain container QWidget, so take the color a plain (hidden) QWidget gets from the same
        stylesheet - that's the normal dark panel color."""
        ref = self._bg_ref
        ref.ensurePolished()
        return ref.palette().color(QtGui.QPalette.ColorRole.Window)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self.viewport())
        rect = event.rect()
        painter.fillRect(rect, self._background_color())
        cw, ch = self._cell_w, self._cell_h
        ox, oy = self.horizontalScrollBar().value(), self.verticalScrollBar().value()
        first_row, last_row = max(0, (rect.top() + oy) // ch), (rect.bottom() + oy) // ch
        first_col, last_col = max(0, (rect.left() + ox) // cw), min(self._cols - 1, (rect.right() + ox) // cw)

        fm = self.fontMetrics()
        painter.setFont(self.font())
        painter.setPen(self.palette().color(QtGui.QPalette.ColorRole.Text))
        n = len(self.items)
        for r in range(first_row, last_row + 1):
            base = r * self._cols
            if base >= n:
                break
            for c in range(first_col, last_col + 1):
                i = base + c
                if i >= n:
                    break
                cell = QtCore.QRect(c * cw - ox, r * ch - oy, cw, ch)
                self._paint_item(painter, fm, self.items[i], cell, i in self._selected)
        painter.end()

    @staticmethod
    def _draw_border(painter, r, w, color):
        painter.fillRect(r.x(), r.y(), r.width(), w, color)
        painter.fillRect(r.x(), r.bottom() - w + 1, r.width(), w, color)
        painter.fillRect(r.x(), r.y() + w, w, r.height() - 2 * w, color)
        painter.fillRect(r.right() - w + 1, r.y() + w, w, r.height() - 2 * w, color)

    def _paint_item(self, painter, fm, item, cell, selected):
        size = self._swatch_size
        box = QtCore.QRect(cell.x() + self.PAD, cell.y() + self.PAD, size, size)
        if isinstance(item, SwatchItem):
            painter.fillRect(box, item.color)
        else:
            grad = QtGui.QLinearGradient(box.left(), 0, box.right() + 1, 0)
            for pos, color in item.stops:
                grad.setColorAt(pos, color)
            painter.fillRect(box, QtGui.QBrush(grad))

        if selected:
            self._draw_border(painter, box, 3, self.SELECTED_COLOR)
        else:
            self._draw_border(painter, box, 1, self.BORDER_COLOR)

        text_rect = QtCore.QRect(cell.x(), box.bottom() + 1 + self.GAP, cell.width(), fm.height())
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter),
                         fm.elidedText(item.name, Qt.TextElideMode.ElideRight, size))

    # ---- mouse / keyboard -------------------------------------------------------
    def mousePressEvent(self, event):
        self.setFocus()
        if event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position().toPoint()
        row = self.row_at(pos)
        self._press_row, self._press_pos, self._moved = row, pos, False
        self._defer_select_row = -1
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)

        if row < 0:
            if not ctrl and not shift:
                self.clear_selection()
        elif ctrl and shift:
            pass
        elif shift:
            if self._anchor >= 0:
                lo, hi = sorted((self._anchor, row))
                self._set_selection(set(range(lo, hi + 1)))
            else:
                self._anchor = row
                self._set_selection({row})
        elif ctrl:
            rows = set(self._selected)
            rows.symmetric_difference_update({row})
            self._set_selection(rows)
            if row in rows:
                self._anchor = row
        elif row in self._selected and len(self._selected) > 1:
            self._defer_select_row = row        # may be the start of a multi-swatch drag
        else:
            self._anchor = row
            self._set_selection({row})

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        if self._press_row >= 0 and event.buttons() & Qt.MouseButton.LeftButton:
            if (pos - self._press_pos).manhattanLength() > 5:
                self._moved = True
                self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        cell = self.cell_at(pos)
        if cell != self._hover_row:
            self._hover_row = cell
            item = self.item_at(cell)
            if isinstance(item, SwatchItem):
                self.viewport().setToolTip(f"{item.name}\nRGB: {item.rgb}")
            else:
                self.viewport().setToolTip(item.name if item else "")
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor if self.row_at(pos) >= 0 else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        row, moved, deferred = self._press_row, self._moved, self._defer_select_row
        self._press_row, self._moved, self._defer_select_row = -1, False, -1
        if row < 0:
            return
        self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        if moved:
            self.drag_released.emit(row)
        elif deferred == row:
            self._anchor = row
            self._set_selection({row})

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            row = self.row_at(event.position().toPoint())
            if row >= 0:
                self.item_double_clicked.emit(row)

    def contextMenuEvent(self, event):
        row = self.row_at(event.pos())
        if row >= 0 and row not in self._selected:
            self._anchor = row
            self._set_selection({row})
        self.context_requested.emit(event.globalPos(), row)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.clear_selection()
        elif event.key() == Qt.Key.Key_Delete:
            self.delete_pressed.emit()
        else:
            super().keyPressEvent(event)

    # ---- dropping Houdini nodes (to save their ramp as a gradient) ------------------
    def _dragged_ramp_node(self, event):
        mime = event.mimeData()
        if not mime.hasText():
            return None
        node = hou.node(mime.text())
        return node if node and self._ramp_checker(node) else None

    def dragEnterEvent(self, event):
        if self._dragged_ramp_node(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._dragged_ramp_node(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        node = self._dragged_ramp_node(event)
        if node:
            self.node_dropped.emit(node)
            event.acceptProposedAction()
        else:
            event.ignore()


class NetworkOps:
    """Houdini-side actions: create nodes / ramps from swatches and gradients."""

    KARMA_CONTEXTS = ('materialbuilder', 'materiallibrary', 'karmamaterialbuilder', 'subnet')
    OCTANE_CONTEXTS = ('octane_vopnet', 'octane_solaris_material_builder')
    REDSHIFT_CONTEXTS = ('redshift_vopnet', 'rs_usd_material_builder')
    MATNET_CONTEXTS = ('matnet',)

    # kind -> (node type, (r, g, b) parm names) for plain color-constant nodes
    COLOR_NODE_SPECS = {
        'sop': ("color", ("colorr", "colorg", "colorb")),
        'octane': ("NT_TEX_RGB", ("A_VALUEr", "A_VALUEg", "A_VALUEb")),
        'redshift': ("redshift::RSColorConstant", ("colorr", "colorg", "colorb")),
    }
    # kind -> (node type, ramp parm name)
    GRADIENT_SPECS = {
        'sop': ("color", "ramp"),
        'karma': ("kma_rampconst", "vramp"),
        'octane': ("NT_TEX_GRADIENT", "octane_gradient"),
        'redshift': ("redshift::RSRamp", "ramp"),
        'matnet': ("rampparm", "rampcolordefault"),
    }

    def __init__(self, log):
        self.log = log

    def context_kind(self, context):
        context_type = context.type().name()
        category = context.childTypeCategory().name()
        if category == 'Sop':
            return 'sop'
        if context_type in self.KARMA_CONTEXTS:
            return 'karma'
        if context_type in self.OCTANE_CONTEXTS:
            return 'octane'
        if context_type in self.REDSHIFT_CONTEXTS:
            return 'redshift'
        if context_type in self.MATNET_CONTEXTS or category == 'Vop':
            return 'matnet'
        if category == 'Object':
            return 'object'
        return None

    @staticmethod
    def _select_created(pane, context, created):
        if created:
            created[-1].setSelected(True, clear_all_selected=True)
            if context.childTypeCategory().name() == 'Sop':
                pane.setCurrentNode(created[-1])

    # ---- swatches -> nodes -------------------------------------------------
    def create_swatches(self, pane, pos, swatches):
        if not swatches:
            return
        context = pane.pwd()
        if not isinstance(pos, hou.Vector2):
            pos = pane.cursorPosition()
        try:
            kind = self.context_kind(context)
            if kind is None:
                hou.ui.displayMessage(f"Unsupported network context for drag & drop: {context.type().name()}")
                return
            if kind == 'object':
                created = self._create_object_nodes(context, swatches, pos)
            else:
                created = self._nodes_or_gradient(context, kind, swatches, pos)
            self._select_created(pane, context, created)
        except Exception as e:
            hou.ui.displayMessage(f"Error creating node(s): {e}")

    def _nodes_or_gradient(self, context, kind, swatches, pos):
        if len(swatches) > 1:
            choice = hou.ui.displayMessage("Create individual nodes or a gradient?", buttons=["Nodes", "Gradient", "Cancel"], default_choice=0, close_choice=2)
            if choice == 1:
                sort_choice = hou.ui.displayMessage("Sort swatches by hue?", buttons=["Yes", "No", "Cancel"], default_choice=0, close_choice=2)
                if sort_choice == 2:
                    return []
                use = sort_colors_by_hue(swatches) if sort_choice == 0 else swatches
                return self._create_gradient_node(context, kind, [s.rgb for s in use], pos, "swatch_gradient")
            if choice != 0:
                return []
        return self._create_color_nodes(context, kind, swatches, pos)

    def _create_color_nodes(self, context, kind, swatches, pos):
        spacing = hou.Vector2(0, -1.0)
        created = []
        for i, swatch in enumerate(swatches):
            node = self._make_color_node(context, kind, swatch)
            node.setPosition(pos + spacing * i)
            created.append(node)
        if kind == 'sop':
            for a, b in zip(created[:-1], created[1:]):
                b.setNextInput(a)
        return created

    def _make_color_node(self, context, kind, swatch):
        name = sanitize_name(swatch.name)
        if kind == 'karma':
            node = context.createNode("mtlxconstant")
            node.setName(name, unique_name=True)
            node.parm("signature").set("color3")
            parm_names = ("value_color3r", "value_color3g", "value_color3b")
        elif kind == 'matnet':
            node = context.createNode("constant")
            node.setName(name, unique_name=True)
            node.parm("consttype").set("color")
            parm_names = ("colordefr", "colordefg", "colordefb")
        else:
            node_type, parm_names = self.COLOR_NODE_SPECS[kind]
            node = context.createNode(node_type)
            node.setName(name, unique_name=True)
        for parm_name, value in zip(parm_names, swatch.rgb):
            node.parm(parm_name).set(value)
        return node

    def _create_object_nodes(self, context, swatches, pos):
        created = []
        spacing = hou.Vector2(0, -1.0)
        for i, swatch in enumerate(swatches):
            geo = context.createNode("geo", sanitize_name(swatch.name))
            if file_node := geo.node("file1"):
                file_node.destroy()
            color = geo.createNode("color", sanitize_name(swatch.name))
            color.parmTuple("color").set(swatch.rgb)
            color.moveToGoodPosition()
            color.setDisplayFlag(True)
            color.setRenderFlag(True)
            geo.setPosition(pos + spacing * i)
            created.append(geo)
        return created

    # ---- gradients -> nodes ------------------------------------------------
    def _create_gradient_node(self, context, kind, colors, pos, name):
        node_type, parm_name = self.GRADIENT_SPECS[kind]
        node = context.createNode(node_type)
        node.setName(name, unique_name=True)
        node.setPosition(pos)
        node.parm(parm_name).set(make_ramp(colors))
        if kind == 'sop':
            node.parm("colortype").set(3)
        return [node]

    def create_gradient(self, pane, pos, gradient):
        context = pane.pwd()
        try:
            kind = self.context_kind(context)
            if kind is None or kind == 'object':
                hou.ui.displayMessage(f"Unsupported network context for gradient: {context.type().name()}")
                return
            created = self._create_gradient_node(context, kind, gradient.colors, pos, sanitize_name(gradient.name))
            self._select_created(pane, context, created)
        except Exception as e:
            hou.ui.displayMessage(f"Error creating gradient: {e}")

    def apply_gradient_to_selected_node(self, gradient):
        selected_nodes = hou.selectedNodes()
        if not selected_nodes:
            hou.ui.displayMessage("No node selected in Houdini.")
            return
        target_node = selected_nodes[0]
        target_parm = find_ramp_parm(target_node)
        if not target_parm:
            hou.ui.displayMessage(f"No suitable ramp parameter found on '{target_node.name()}'.")
            return
        with hou.undos.group("Apply Saved Gradient"):
            target_parm.set(make_ramp(gradient.colors))
        self.log(f"Set gradient on '{target_node.path()}.{target_parm.name()}'.")

    # ---- write into the currently selected node ---------------------------
    def set_color_in_selected_node(self, swatches):
        if not swatches:
            return
        selected_nodes = hou.selectedNodes()
        if not selected_nodes:
            hou.ui.displayMessage("No node selected in Houdini.", title="Selection Error")
            return
        node = selected_nodes[0]
        with hou.undos.group("Set Color from Swatch Panel"):
            if len(swatches) > 1:
                self._set_gradient_on_node(node, swatches)
            else:
                self._set_single_color_on_node(node, swatches[0])

    def _set_gradient_on_node(self, node, swatches):
        target_parm = find_ramp_parm(node)
        if not target_parm:
            hou.ui.displayMessage(f"No suitable ramp parameter found on '{node.name()}'.", title="Parameter Not Found")
            return
        sort_choice = hou.ui.displayMessage("Sort swatches by hue for the gradient?", buttons=["Yes", "No", "Cancel"], default_choice=0, close_choice=2)
        if sort_choice == 2:
            return
        use = sort_colors_by_hue(swatches) if sort_choice == 0 else swatches
        target_parm.set(make_ramp([s.rgb for s in use]))
        self.log(f"Set gradient on '{node.path()}.{target_parm.name()}'.")

    def _set_single_color_on_node(self, node, swatch):
        for parm_name in COLOR_PARM_NAMES:
            parm = node.parmTuple(parm_name)
            if parm and parm.parmTemplate().numComponents() == 3:
                try:
                    parm.set(swatch.rgb)
                    self.log(f"Set color on '{node.path()}.{parm_name}'.")
                    return
                except hou.OperationFailed:
                    continue
        hou.ui.displayMessage(f"No suitable color parameter found on '{node.name()}'.", title="Parameter Not Found")


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
        self.current_swatch_size = 100
        self.size_buttons = {}
        self.current_view_mode = 'swatches' 
        self.current_gradient_dict = {}
        self.ops = NetworkOps(self.log)
        self._ase_cache = OrderedDict()   # path -> ((mtime_ns, size), [SwatchItem])
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
        self.btn_small.setIcon(get_icon("BUTTONS_grid_small"))
        self.btn_med.setIcon(get_icon("BUTTONS_grid_medium"))
        self.btn_large.setIcon(get_icon("BUTTONS_grid_large"))

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

        self.view = SwatchGridView(self.get_ramp_parm_from_node)

        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.folder_tree)
        self.splitter.addWidget(self.view)
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
        self.view.context_requested.connect(self.show_context_menu)
        self.view.drag_released.connect(self.on_drag_released)
        self.view.item_double_clicked.connect(self.on_item_double_clicked)
        self.view.delete_pressed.connect(self.delete_selected_gradients)
        self.view.node_dropped.connect(self.save_ramp_from_node)
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
        return find_ramp_parm(node)

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

    # ---- selection / actions ------------------------------------------------
    def selected_items(self):
        """Selected items in model (display) order."""
        return self.view.selected_items()

    def _item_at_row(self, row):
        return self.view.item_at(row)

    def _create_in_pane(self, pane, pos, primary):
        if isinstance(primary, GradientItem):
            self.ops.create_gradient(pane, pos, primary)
        else:
            selected = [i for i in self.selected_items() if isinstance(i, SwatchItem)]
            self.ops.create_swatches(pane, pos, selected or [primary])

    def create_in_network(self, item):
        pane = find_network_editor()
        if not pane:
            hou.ui.displayMessage("No active Network Editor found.")
            return
        self._create_in_pane(pane, pane.visibleBounds().center(), item)

    def on_drag_released(self, row):
        pane = hou.ui.paneTabUnderCursor()
        if not isinstance(pane, hou.NetworkEditor):
            return
        item = self._item_at_row(row)
        if item is not None:
            self._create_in_pane(pane, pane.cursorPosition(), item)

    def on_item_double_clicked(self, row):
        item = self._item_at_row(row)
        if isinstance(item, SwatchItem):
            self.create_in_network(item)

    def show_context_menu(self, global_pos, row=-1):
        selected = self.selected_items()
        active = self._item_at_row(row) or (selected[0] if selected else None)
        if active is None:
            return

        menu = QtWidgets.QMenu(self)
        menu.addAction("Create Node(s) in Network...", lambda: self.create_in_network(active))
        menu.addSeparator()

        if isinstance(active, SwatchItem):
            menu.addAction("Create Color in Selected Node",
                           lambda: self.ops.set_color_in_selected_node(
                               [i for i in self.selected_items() if isinstance(i, SwatchItem)] or [active]))
        else:
            menu.addAction("Apply to Selected Node", lambda: self.ops.apply_gradient_to_selected_node(active))

        selected_gradients = [i for i in selected if isinstance(i, GradientItem)]
        if isinstance(active, GradientItem) or selected_gradients:
            menu.addSeparator()
            num_to_delete = len(selected_gradients)
            menu.addAction(f"Delete {num_to_delete} Gradients" if num_to_delete > 1 else "Delete Gradient",
                           self.delete_selected_gradients)
        menu.exec(global_pos)

    def delete_selected_gradients(self):
        selected_gradients = [i for i in self.selected_items() if isinstance(i, GradientItem)]
        if not selected_gradients:
            return

        deleted_count = 0
        for grad in selected_gradients:
            # current_gradient_dict is the live nested dict inside saved_gradients
            if self.current_gradient_dict.pop(grad.name, None) is not None:
                deleted_count += 1

        if deleted_count > 0:
            self.save_config_state()
            self.log(f"Deleted {deleted_count} gradient(s).")
            self.populate_grid()

    def save_selected_as_gradient(self):
        selected = [i for i in self.selected_items() if isinstance(i, SwatchItem)]
        if len(selected) < 2: return

        choice = hou.ui.displayMessage("Sort swatches by hue?", buttons=["Yes", "No", "Cancel"], default_choice=0, close_choice=2)
        if choice == 2: return
        if choice == 0:
            selected = sort_colors_by_hue(selected)

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
            grad_root_item.setIcon(0, get_icon("VOP_ramp"))
        except hou.OperationFailed:
            grad_root_item.setIcon(0, get_icon("SOP_color"))

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
            default_item.setIcon(0, get_icon("BUTTONS_folder"))
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
                    item.setIcon(0, get_icon("BUTTONS_folder"))
                    self._build_tree_recursive(folder, item)

    def _add_gradient_folder_item(self, parent_item, name):
        folder_item = QtWidgets.QTreeWidgetItem(parent_item, [name])
        folder_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, name)
        folder_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "gradient_folder")
        folder_item.setIcon(0, get_icon("BUTTONS_folder"))
        return folder_item

    def _build_tree_recursive(self, path, parent_item):
        # scandir: DirEntry.is_dir() reuses the directory listing (no extra stat per entry)
        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            return

        folders, ase_files = [], []
        for entry in entries:
            try:
                if entry.is_dir():
                    folders.append(entry)
                elif entry.name.lower().endswith(".ase"):
                    ase_files.append(entry)
            except OSError:
                continue

        folder_icon = get_icon("BUTTONS_folder")
        for entry in folders:
            tree_item = QtWidgets.QTreeWidgetItem(parent_item, [entry.name])
            tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, entry.path)
            tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "folder")
            tree_item.setIcon(0, folder_icon)
            self._build_tree_recursive(entry.path, tree_item)

        file_icon = get_icon("SOP_color")
        for entry in ase_files:
            tree_item = QtWidgets.QTreeWidgetItem(parent_item, [entry.name])
            tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole, entry.path)
            tree_item.setData(0, QtCore.Qt.ItemDataRole.UserRole + 1, "file")
            tree_item.setIcon(0, file_icon)

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
        self.apply_grid_size()

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
            start = time.perf_counter()
            self.swatches = self.parse_ase(filepath)
            self.populate_grid()
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.log(f"Loaded {len(self.swatches)} swatches in {elapsed_ms:.0f} ms.")

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

    def apply_grid_size(self):
        """Resize the cells. Only changes the layout metrics - nothing is rebuilt."""
        self.view.set_swatch_size(self.current_swatch_size)

    def populate_grid(self):
        """Hand the current items to the model. No widgets are created."""
        if self.current_view_mode == 'swatches':
            items = self.swatches
        else:
            items = [GradientItem(name, value)
                     for name, value in self.current_gradient_dict.items()
                     if isinstance(value, list)]
        self.view.set_items(items)   # also clears the selection

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.view.clear_selection()
        elif event.key() == Qt.Key.Key_Delete:
            self.delete_selected_gradients()
        else:
            super().keyPressEvent(event)

    def parse_ase(self, path):
        """Parse an ASE file into SwatchItems. Results are cached per (mtime, size)."""
        try:
            st = os.stat(path)
            stamp = (st.st_mtime_ns, st.st_size)
        except OSError:
            stamp = None

        cached = self._ase_cache.get(path)
        if cached is not None and stamp is not None and cached[0] == stamp:
            self._ase_cache.move_to_end(path)
            return cached[1]

        raw, error = read_ase(path)
        if error:
            self.log(error)
        swatches = [SwatchItem(name, rgb) for name, rgb in raw]

        if stamp is not None and not error:
            self._ase_cache[path] = (stamp, swatches)
            while len(self._ase_cache) > 32:
                self._ase_cache.popitem(last=False)
        return swatches

def onCreateInterface():
    """Entry point for Houdini to create the interface."""
    return SwatchViewer()