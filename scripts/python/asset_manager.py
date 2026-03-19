"""
Houdini Asset Manager - Shelf Tool Script
==========================================
Paste this entire script into a Houdini Shelf Tool (RMB on shelf → New Tool → Script tab).

Scans the scene for all import/reference nodes (File SOP, Alembic SOP, MtlxImage, etc.)
and presents a UI to view and relink their file paths.
"""

import hou
import os
import re
import functools
from PySide6 import QtWidgets, QtCore, QtGui


# ---------------------------------------------------------------------------
# Node type → parameter name(s) that hold a file path
# ---------------------------------------------------------------------------
NODE_PARAM_MAP = {
    # Geometry / SOP
    "file":             ["file"],
    "alembic":          ["fileName"],
    "filecache":        ["file"],
    "rop_alembic":      ["filename"],
    "rop_geometry":     ["sopoutput"],
    "bgeo":             ["file"],

    # USD / LOP
    "reference":        ["filepath1"],
    "sublayer":         ["filepath1"],
    "usdimport":        ["filepath1"],

    # MaterialX / MTLX (inside Material networks)
    "mtlximage":        ["file"],
    "mtlxtiledimage":   ["file"],

    # COPs / texture nodes
    "file::2.0":        ["filename"],
    "cop2_file":        ["filename"],

    # Redshift
    "redshift::TextureSampler":  ["tex0"],
    "redshift::NormalMap":       ["tex0"],
    "redshift::Sprite":          ["tex0"],

    # Arnold
    "arnold::image":    ["filename"],

    # Karma / VEX
    "karma":            ["picture"],
    "usdrender_rop":    ["picture"],
}

# ---------------------------------------------------------------------------
# Blocklists — node types and parm names that are never asset paths
# ---------------------------------------------------------------------------

# Node type prefixes to skip entirely (schedulers, TOPs infra, fetch, etc.)
BLOCKED_NODE_TYPES = {
    "localscheduler", "hqueue_scheduler", "deadline_scheduler",
    "tractor_scheduler", "pdg_scheduler",
    "topnet", "taskgraph",
    "fetch", "null", "merge", "split", "switch", "output",
    "wedge", "partitionbyattribute", "partitionbyframe",
    "attributecreate", "attributedelete", "attributepromote",
    "waitforall", "genericgenerator", "pythonscript",
    "ropfetch", "ropgeometry", "invokepdg",
    # object-level infra
    "lopnet", "dopnet", "chopnet", "cop2net",
    "subnet", "subnetconnector",
}

# Individual parm names that are never external asset file paths
BLOCKED_PARM_NAMES = {
    # scheduler / PDG system parms
    "checkpointfile", "checkpointfiles", "checkpointpath",
    "blockpath", "blockpaths", "templatepath", "templatepaths",
    "pdgpath", "workitempath", "jobparms", "pdgattributes",
    "sopcache", "cachepath", "cachefile",
    "logfile", "logpath", "reportfile",
    "commandpath", "hqueueserver", "remotepath", "localpath",
    "tempdirectory", "tempdirectory2", "scratchpath",
    "pythonpath", "houdinipath", "hfs", "hip", "hipfile", "hipname",
    # ROP / render output that isn't a texture/geo input
    "soho_program", "soho_pipecmd",
    "vm_picture", "vm_dcmfilename", "vm_dsmfilename",
    "vm_cryptolayeroutput",
    # general UI / config paths that aren't assets
    "iconpath", "helppath", "assetpath",
    "colorpath", "presetpath", "gallerypath",
    # common false-positive parm names
    "shoppath", "vexsource", "shopclassname",
}

# The fallback only fires on parm names that *exactly* suggest an external
# file being READ IN (textures, geometry, VDB, etc.).  Deliberately narrow.
FALLBACK_PARM_RE = re.compile(
    r"^(file|filename|filepath|"
    r"tex\d*|texture\w*|"
    r"map\w*|normalmap|roughmap|heightmap|"
    r"image\w*|"
    r"abcfile|alembicfile|"
    r"vdbfile|vdb_file|"
    r"geodata|geofile|"
    r"usdfile|usdpath|"
    r"fur\w*file|"
    r"hdafile|otlfile)$",
    re.IGNORECASE,
)

# Values that look like paths but are actually Houdini-internal tokens
BLOCKED_VALUE_PATTERNS = re.compile(
    r"^(\$HFS|\$HH|\$HOUDINI_PATH|\$HOME/houdini|opdef:|oplib:|temp:|op:)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _looks_like_path(value: str) -> bool:
    """Return True if the string looks like an external file-system path."""
    if not value or len(value) < 4:
        return False
    if BLOCKED_VALUE_PATTERNS.match(value):
        return False
    # Must contain a slash/backslash OR a file extension
    has_sep = "/" in value or "\\" in value
    has_ext = bool(re.search(r'\.[a-zA-Z0-9]{2,6}$', value))
    return has_sep or has_ext


def _is_inside_locked_hda(node):
    """
    Return True if any ancestor of this node is a locked (compiled) HDA.
    Nodes buried inside packedcharacter, autorigbuilder, secondarymotion, etc.
    are internal implementation — not user-managed assets.
    """
    parent = node.parent()
    while parent is not None:
        if isinstance(parent, hou.OpNode):
            # isLockedHDA() is True for any locked digital asset
            try:
                if parent.isLockedHDA():
                    return True
            except AttributeError:
                pass
        parent = parent.parent() if hasattr(parent, "parent") else None
    return False


def collect_nodes(root=None):
    """
    Walk the entire node graph and return a list of dicts:
        {node, parm, parm_name, raw, resolved, expanded, exists}
    Only returns nodes/parms that represent external asset inputs.
    """
    if root is None:
        root = hou.node("/")

    results = []
    visited = set()

    def _walk(node, inside_locked=False):
        if node.path() in visited:
            return
        visited.add(node.path())

        # If this node is a locked HDA, don't scan it or any of its children
        try:
            if node.isLockedHDA():
                return
        except AttributeError:
            pass

        # If we've already determined we're inside a locked HDA, stop here
        if inside_locked:
            return

        type_name = node.type().name()

        # Skip blocked node types outright
        base_type = type_name.split("::")[0]
        if base_type in BLOCKED_NODE_TYPES:
            for child in node.children():
                _walk(child, inside_locked=False)
            return

        # --- 1. known explicit parm map ---
        known_parms = list(NODE_PARAM_MAP.get(type_name, []))
        for key in NODE_PARAM_MAP:
            if type_name.startswith(key) and NODE_PARAM_MAP[key] not in [known_parms]:
                for p in NODE_PARAM_MAP[key]:
                    if p not in known_parms:
                        known_parms.append(p)

        found_parms = set()

        for parm_name in known_parms:
            parm = node.parm(parm_name)
            if parm is None:
                continue
            try:
                raw = parm.rawValue()
                resolved = parm.eval()
                if isinstance(resolved, str) and _looks_like_path(resolved):
                    results.append(_make_entry(node, parm, raw, resolved))
                    found_parms.add(parm_name)
            except Exception:
                pass

        # --- 2. narrow fallback: only if no known parms matched ---
        if not found_parms:
            for parm in node.parms():
                pname = parm.name()
                if pname in found_parms:
                    continue
                if pname in BLOCKED_PARM_NAMES:
                    continue
                if not FALLBACK_PARM_RE.match(pname):
                    continue
                try:
                    tmpl = parm.parmTemplate()
                    if tmpl.type() != hou.parmTemplateType.String:
                        continue
                    # Only pick up file-chooser parms, not plain string fields
                    if tmpl.stringType() != hou.stringParmType.FileReference:
                        continue
                    raw = parm.rawValue()
                    resolved = parm.eval()
                    if isinstance(resolved, str) and _looks_like_path(resolved):
                        results.append(_make_entry(node, parm, raw, resolved))
                except Exception:
                    pass

        for child in node.children():
            # Mark children as inside_locked if this node is an unlocked HDA
            # that contains children (subnet-style). The locked case already
            # returned early above, so anything reaching here is unlocked.
            _walk(child, inside_locked=False)

    _walk(root, inside_locked=False)
    return results


def _make_entry(node, parm, raw, resolved):
    expanded = hou.expandString(resolved)
    exists = os.path.exists(expanded) if expanded else False
    return {
        "node":      node,
        "parm":      parm,
        "parm_name": parm.name(),
        "raw":       raw,
        "resolved":  resolved,
        "expanded":  expanded,
        "exists":    exists,
    }


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

# Houdini-matched palette
# These values are sampled from Houdini 20's default dark theme
DARK_BG    = "#323232"   # main window / pane background
PANEL_BG   = "#3a3a3a"   # slightly raised panels / toolbars
PANEL_MID  = "#2d2d2d"   # slightly sunken (table, inputs)
BORDER     = "#222222"   # hard shadow borders
BORDER_HI  = "#555555"   # lighter separator / bevel highlight
ACCENT     = "#cc7a00"   # Houdini orange
ACCENT_HI  = "#e08c00"
TEXT_MAIN  = "#cccccc"   # primary label text
TEXT_DIM   = "#888888"   # secondary / disabled text
OK_GREEN   = "#5a9e5a"
MISS_RED   = "#b05050"
WARN_YEL   = "#b09040"
SEL_BG     = "#4a6a8a"   # Houdini selection blue


STYLE = f"""
QWidget {{
    background: {DARK_BG};
    color: {TEXT_MAIN};
    font-family: 'Lucida Grande', 'Segoe UI', sans-serif;
    font-size: 12px;
}}

QMainWindow, QDialog {{
    background: {DARK_BG};
}}

/* ---- toolbar / header ---- */
#header {{
    background: {PANEL_BG};
    border-bottom: 1px solid {BORDER};
}}

/* ---- labels ---- */
QLabel {{
    background: transparent;
    color: {TEXT_MAIN};
}}

/* ---- line edit — matches Houdini parameter fields ---- */
QLineEdit {{
    background: {PANEL_MID};
    border: 1px solid {BORDER};
    border-top-color: {BORDER};
    border-left-color: {BORDER};
    border-right-color: {BORDER_HI};
    border-bottom-color: {BORDER_HI};
    padding: 2px 5px;
    color: {TEXT_MAIN};
    selection-background-color: {SEL_BG};
    border-radius: 0;
}}
QLineEdit:focus {{
    border-color: {ACCENT};
}}

/* ---- table ---- */
QTableWidget {{
    background: {PANEL_MID};
    border: 1px solid {BORDER};
    gridline-color: #282828;
    alternate-background-color: #303030;
    selection-background-color: {SEL_BG};
    selection-color: #ffffff;
    outline: none;
    border-radius: 0;
}}
QTableWidget::item {{
    padding: 2px 5px;
    border: none;
}}
QTableWidget::item:selected {{
    background: {SEL_BG};
    color: #ffffff;
}}
QHeaderView::section {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #484848, stop:1 #383838);
    color: {TEXT_DIM};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 3px 6px;
    font-size: 11px;
}}
QHeaderView::section:pressed {{
    background: {PANEL_MID};
}}

/* ---- buttons — flat Houdini style ---- */
QPushButton {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #484848, stop:1 #383838);
    border: 1px solid {BORDER};
    border-right-color: {BORDER_HI};
    border-bottom-color: {BORDER_HI};
    padding: 3px 10px;
    color: {TEXT_MAIN};
    border-radius: 0;
    min-height: 18px;
}}
QPushButton:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #525252, stop:1 #424242);
    color: #ffffff;
}}
QPushButton:pressed {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #303030, stop:1 #3a3a3a);
    border-top-color: {BORDER};
    border-left-color: {BORDER};
}}
QPushButton:disabled {{
    color: {TEXT_DIM};
    background: #383838;
}}
QPushButton:checked {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #303030, stop:1 #3a3a3a);
    border-top-color: {BORDER};
    border-left-color: {BORDER};
    color: {ACCENT};
}}
QPushButton#btn_accent {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #d98000, stop:1 {ACCENT});
    border-color: #7a4800;
    color: #ffffff;
    font-weight: bold;
}}
QPushButton#btn_accent:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #e89000, stop:1 #cc7a00);
}}
QPushButton#btn_green {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #528852, stop:1 #3d6e3d);
    border-color: #2a4a2a;
    color: #d0efd0;
}}
QPushButton#btn_green:hover {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #5e9e5e, stop:1 #4a7d4a);
}}

/* ---- scroll bars — thin Houdini style ---- */
QScrollBar:vertical {{
    background: {PANEL_MID};
    width: 12px;
    border: none;
    border-left: 1px solid {BORDER};
}}
QScrollBar::handle:vertical {{
    background: #555555;
    min-height: 20px;
    border: 1px solid {BORDER};
}}
QScrollBar::handle:vertical:hover {{ background: #686868; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    background: #404040;
    height: 12px;
    border: 1px solid {BORDER};
    subcontrol-origin: margin;
}}
QScrollBar::add-line:vertical {{ subcontrol-position: bottom; }}
QScrollBar::sub-line:vertical {{ subcontrol-position: top; }}
QScrollBar:horizontal {{
    background: {PANEL_MID};
    height: 12px;
    border: none;
    border-top: 1px solid {BORDER};
}}
QScrollBar::handle:horizontal {{
    background: #555555;
    min-width: 20px;
    border: 1px solid {BORDER};
}}
QScrollBar::handle:horizontal:hover {{ background: #686868; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    background: #404040;
    width: 12px;
    border: 1px solid {BORDER};
}}

/* ---- status bar ---- */
QStatusBar {{
    background: {PANEL_BG};
    border-top: 1px solid {BORDER};
    color: {TEXT_DIM};
    font-size: 11px;
}}
QStatusBar::item {{ border: none; }}

/* ---- combo box ---- */
QComboBox {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
                    stop:0 #484848, stop:1 #383838);
    border: 1px solid {BORDER};
    border-right-color: {BORDER_HI};
    border-bottom-color: {BORDER_HI};
    padding: 2px 5px;
    color: {TEXT_MAIN};
    border-radius: 0;
    min-height: 18px;
}}
QComboBox:hover {{ color: #ffffff; }}
QComboBox::drop-down {{
    border: none;
    width: 16px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    width: 0; height: 0;
}}
QComboBox QAbstractItemView {{
    background: #404040;
    border: 1px solid {BORDER};
    selection-background-color: {SEL_BG};
    color: {TEXT_MAIN};
    outline: none;
}}

/* ---- tab widget ---- */
QTabWidget::pane {{
    border: 1px solid {BORDER};
    background: {DARK_BG};
}}
QTabBar::tab {{
    background: #383838;
    color: {TEXT_DIM};
    padding: 4px 14px;
    border: 1px solid {BORDER};
    border-bottom: none;
    margin-right: 1px;
}}
QTabBar::tab:selected {{
    background: {DARK_BG};
    color: {TEXT_MAIN};
    border-bottom-color: {DARK_BG};
}}
QTabBar::tab:hover:!selected {{
    background: #424242;
    color: {TEXT_MAIN};
}}

/* ---- tooltip ---- */
QToolTip {{
    background: #404040;
    border: 1px solid {BORDER};
    color: {TEXT_MAIN};
    padding: 3px 5px;
}}

/* ---- form layout labels ---- */
QFormLayout QLabel {{
    color: {TEXT_DIM};
}}

/* ---- list widget ---- */
QListWidget {{
    background: {PANEL_MID};
    border: 1px solid {BORDER};
    color: {TEXT_MAIN};
    outline: none;
}}
QListWidget::item:selected {{
    background: {SEL_BG};
    color: #ffffff;
}}

/* ---- scroll area ---- */
QScrollArea {{
    border: 1px solid {BORDER};
    background: {PANEL_MID};
}}

/* ---- message box ---- */
QMessageBox {{
    background: {DARK_BG};
}}
QMessageBox QPushButton {{
    min-width: 70px;
}}
"""


COL_STATUS   = 0
COL_NODE     = 1
COL_TYPE     = 2
COL_PARM     = 3
COL_PATH     = 4
COL_ACTIONS  = 5
NUM_COLS     = 6
COL_HEADERS  = ["", "Node", "Type", "Parm", "Path", "Actions"]


class PathDelegate(QtWidgets.QStyledItemDelegate):
    """
    Draws the path cell with:
      - colour coding (green = found, red = missing)
      - orange highlight on find matches
      - when a replace string is present: struck-through match in red,
        replacement text in green, shown inline in the path
    Reads _find_pattern and _replace_str from the parent QMainWindow.
    """

    def _get_window(self):
        widget = self.parent()
        while widget is not None:
            if hasattr(widget, "_find_pattern"):
                return widget
            widget = widget.parent() if hasattr(widget, "parent") else None
        return None

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        style = option.widget.style() if option.widget else QtWidgets.QApplication.style()

        text        = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
        exists      = index.data(QtCore.Qt.ItemDataRole.UserRole)
        win         = self._get_window()
        pattern     = win._find_pattern if win else None
        replace_str = win._replace_str  if win else None

        # Base text colour
        if exists is True:
            base_color = QtGui.QColor(TEXT_MAIN)
        elif exists is False:
            base_color = QtGui.QColor(MISS_RED)
        else:
            base_color = QtGui.QColor(WARN_YEL)

        # Collect match spans
        spans = []
        if pattern and text:
            try:
                for m in pattern.finditer(text):
                    if m.start() < m.end():
                        spans.append((m.start(), m.end()))
            except Exception:
                pass

        painter.save()

        # Draw row background via style engine (selection, alternating, etc.)
        opt = QtWidgets.QStyleOptionViewItem(option)
        opt.text = ""
        style.drawControl(QtWidgets.QStyle.ControlElement.CE_ItemViewItem, opt, painter, option.widget)

        rect = style.subElementRect(
            QtWidgets.QStyle.SubElement.SE_ItemViewItemText, option, option.widget
        )
        rect = rect.adjusted(3, 0, -3, 0)

        fm        = QtGui.QFontMetrics(option.font)
        strike_fm = fm  # same font, we draw strike line manually
        y         = rect.y() + (rect.height() + fm.ascent() - fm.descent()) // 2 - 1
        x         = rect.x()

        doing_replace = pattern and replace_str and spans

        if not spans:
            # Fast path — plain coloured text, no matches
            painter.setFont(option.font)
            painter.setPen(base_color)
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignLeft,
                             fm.elidedText(text, QtCore.Qt.TextElideMode.ElideMiddle, rect.width()))
        elif not doing_replace:
            # Highlight-only mode (replace box is empty)
            highlight_bg = QtGui.QColor(ACCENT)
            highlight_bg.setAlpha(180)
            highlight_fg = QtGui.QColor("#ffffff")
            painter.setFont(option.font)
            i = 0
            while i < len(text):
                in_span = any(s <= i < e for s, e in spans)
                j = i + 1
                while j < len(text) and (any(s <= j < e for s, e in spans) == in_span):
                    j += 1
                segment = text[i:j]
                seg_w   = fm.horizontalAdvance(segment)
                if x + seg_w > rect.right():
                    painter.setPen(base_color)
                    painter.drawText(x, y, fm.elidedText(segment, QtCore.Qt.TextElideMode.ElideRight, rect.right() - x))
                    break
                if in_span:
                    painter.fillRect(QtCore.QRect(x, rect.y() + 1, seg_w, rect.height() - 2), highlight_bg)
                    painter.setPen(highlight_fg)
                else:
                    painter.setPen(base_color)
                painter.drawText(x, y, segment)
                x += seg_w
                i  = j
        else:
            # Replace preview mode — render the fully substituted string with
            # deleted parts struck in red and inserted parts in green
            ADD_COLOR = QtGui.QColor("#60b060")   # soft green for replacement
            painter.setFont(option.font)

            segments = []
            prev = 0
            for s, e in spans:
                if prev < s:
                    segments.append((text[prev:s], base_color))
                segments.append((replace_str if replace_str else "", ADD_COLOR))
                prev = e
            if prev < len(text):
                segments.append((text[prev:], base_color))

            for seg_text, color in segments:
                seg_w = fm.horizontalAdvance(seg_text)
                if x + seg_w > rect.right():
                    remaining = rect.right() - x
                    if remaining > fm.horizontalAdvance("…"):
                        painter.setPen(color)
                        painter.drawText(x, y, fm.elidedText(seg_text, QtCore.Qt.TextElideMode.ElideRight, remaining))
                    break
                painter.setPen(color)
                painter.drawText(x, y, seg_text)
                x += seg_w

        painter.restore()

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        exists = index.data(QtCore.Qt.ItemDataRole.UserRole)
        if exists is True:
            option.palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(TEXT_MAIN))
        elif exists is False:
            option.palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(MISS_RED))
        else:
            option.palette.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(WARN_YEL))


class AssetManagerWindow(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Houdini Asset Manager")
        self.resize(1200, 650)
        self.setStyleSheet(STYLE)

        self._entries = []
        self._filtered = []
        self._selected_rows = []
        self._find_pattern = None
        self._replace_str  = None
        self._last_hou_selection = set()
        self._show_absolute = False

        self._build_ui()
        self.refresh()

        # Poll Houdini's node selection every 300 ms and sync highlighting
        self._sel_timer = QtCore.QTimer(self)
        self._sel_timer.setInterval(300)
        self._sel_timer.timeout.connect(self._sync_houdini_selection)
        self._sel_timer.start()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root_layout = QtWidgets.QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ---- header toolbar ----
        header = QtWidgets.QWidget()
        header.setObjectName("header")
        header.setFixedHeight(52)
        h_lay = QtWidgets.QHBoxLayout(header)
        h_lay.setContentsMargins(12, 8, 12, 8)

        title = QtWidgets.QLabel("Asset Manager")
        title.setStyleSheet(f"color:{TEXT_MAIN}; font-size:13px; font-weight:bold;")
        h_lay.addWidget(title)
        h_lay.addSpacing(20)

        self.search_box = QtWidgets.QLineEdit()
        self.search_box.setPlaceholderText("Filter by node name, type, or path …")
        self.search_box.setFixedWidth(320)
        self.search_box.textChanged.connect(self._apply_filter)
        h_lay.addWidget(self.search_box)

        h_lay.addSpacing(8)

        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(["All nodes", "Missing only", "Found only"])
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        h_lay.addWidget(self.filter_combo)

        h_lay.addStretch()

        btn_refresh = QtWidgets.QPushButton("⟳  Refresh")
        btn_refresh.setToolTip("Re-scan the scene graph")
        btn_refresh.clicked.connect(self.refresh)
        h_lay.addWidget(btn_refresh)

        btn_relink_sel = QtWidgets.QPushButton("⤷  Relink Selected")
        btn_relink_sel.setObjectName("btn_green")
        btn_relink_sel.setToolTip("Pick a file for each selected row")
        btn_relink_sel.clicked.connect(self._relink_selected)
        h_lay.addWidget(btn_relink_sel)

        btn_search_dir = QtWidgets.QPushButton("🔍  Search in Dir")
        btn_search_dir.setToolTip("Search a directory tree and relink by filename")
        btn_search_dir.clicked.connect(self._search_in_directory)
        h_lay.addWidget(btn_search_dir)

        btn_make_abs = QtWidgets.QPushButton("Make Absolute")
        btn_make_abs.setToolTip("Expand $VARIABLES to full paths in selected rows")
        btn_make_abs.clicked.connect(self._make_absolute)
        h_lay.addWidget(btn_make_abs)

        btn_make_rel = QtWidgets.QPushButton("Make Relative")
        btn_make_rel.setToolTip("Replace path prefixes with the best matching $VARIABLE")
        btn_make_rel.clicked.connect(self._make_relative)
        h_lay.addWidget(btn_make_rel)

        self.btn_abs_view = QtWidgets.QPushButton("Show Absolute Paths")
        self.btn_abs_view.setCheckable(True)
        self.btn_abs_view.setChecked(False)
        self.btn_abs_view.setToolTip("Toggle between raw ($VAR) and expanded absolute paths")
        self.btn_abs_view.toggled.connect(self._on_abs_view_toggled)
        h_lay.addWidget(self.btn_abs_view)

        root_layout.addWidget(header)

        # ---- find / replace bar ----
        self._fr_bar = QtWidgets.QWidget()
        self._fr_bar.setObjectName("fr_bar")
        self._fr_bar.setStyleSheet(f"""
            #fr_bar {{
                background: {PANEL_BG};
                border-bottom: 1px solid {BORDER};
            }}
        """)
        fr_lay = QtWidgets.QHBoxLayout(self._fr_bar)
        fr_lay.setContentsMargins(10, 6, 10, 6)
        fr_lay.setSpacing(6)

        fr_lay.addWidget(QtWidgets.QLabel("Find:"))
        self.find_edit = QtWidgets.QLineEdit()
        self.find_edit.setPlaceholderText("search string  (wildcards: * and ?)")
        self.find_edit.setFixedWidth(280)
        self.find_edit.textChanged.connect(self._on_find_changed)
        fr_lay.addWidget(self.find_edit)

        fr_lay.addSpacing(4)
        fr_lay.addWidget(QtWidgets.QLabel("Replace:"))
        self.replace_edit = QtWidgets.QLineEdit()
        self.replace_edit.setPlaceholderText("replacement string")
        self.replace_edit.setFixedWidth(280)
        self.replace_edit.textChanged.connect(self._on_replace_changed)
        fr_lay.addWidget(self.replace_edit)

        self.match_case_cb = QtWidgets.QCheckBox("Case sensitive")
        self.match_case_cb.setStyleSheet(f"color:{TEXT_DIM};")
        self.match_case_cb.stateChanged.connect(self._on_find_changed)
        fr_lay.addWidget(self.match_case_cb)

        fr_lay.addSpacing(8)

        self._match_label = QtWidgets.QLabel("")
        self._match_label.setStyleSheet(f"color:{TEXT_DIM}; font-size:11px; min-width:80px;")
        fr_lay.addWidget(self._match_label)

        fr_lay.addStretch()

        btn_replace_sel = QtWidgets.QPushButton("Replace in Selected")
        btn_replace_sel.setToolTip("Apply find→replace to selected rows only")
        btn_replace_sel.clicked.connect(self._replace_selected)
        fr_lay.addWidget(btn_replace_sel)

        btn_replace_all = QtWidgets.QPushButton("Replace All")
        btn_replace_all.setObjectName("btn_accent")
        btn_replace_all.setToolTip("Apply find→replace to all visible rows")
        btn_replace_all.clicked.connect(self._replace_all)
        fr_lay.addWidget(btn_replace_all)

        root_layout.addWidget(self._fr_bar)

        # ---- table ----
        self.table = QtWidgets.QTableWidget(0, NUM_COLS)
        self.table.setHorizontalHeaderLabels(COL_HEADERS)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(COL_PATH, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_NODE, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL_TYPE, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL_PARM, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(COL_ACTIONS, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(COL_STATUS, 22)
        self.table.setColumnWidth(COL_ACTIONS, 130)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setItemDelegateForColumn(COL_PATH, PathDelegate(self.table))
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        root_layout.addWidget(self.table, 1)

        # ---- status bar (plain label — works in both QMainWindow and Python Panel) ----
        status_bar = QtWidgets.QWidget()
        status_bar.setFixedHeight(22)
        status_bar.setStyleSheet(f"background:{PANEL_BG}; border-top:1px solid {BORDER};")
        status_lay = QtWidgets.QHBoxLayout(status_bar)
        status_lay.setContentsMargins(8, 0, 8, 0)
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setStyleSheet(f"color:{TEXT_DIM}; font-size:11px;")
        status_lay.addWidget(self.status_label)
        root_layout.addWidget(status_bar)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def refresh(self):
        self.status_label.setText("Scanning scene …")
        QtWidgets.QApplication.processEvents()
        self._entries = collect_nodes()
        try:
            self._last_hou_selection = {n.path() for n in hou.selectedNodes()}
        except Exception:
            self._last_hou_selection = set()
        self._apply_filter()
        total = len(self._entries)
        missing = sum(1 for e in self._entries if not e["exists"])
        self.status_label.setText(
            f"  {total} import nodes found  ·  {missing} missing paths  ·  "
            f"{total - missing} OK"
        )

    def closeEvent(self, event):
        self._sel_timer.stop()
        super().closeEvent(event)

    def _on_abs_view_toggled(self, checked):
        self._show_absolute = checked
        self._populate_table()

    def _sync_houdini_selection(self):
        """Select table rows whose node is currently selected in Houdini."""
        try:
            selected_paths = {n.path() for n in hou.selectedNodes()}
        except Exception:
            return

        if selected_paths == self._last_hou_selection:
            return
        self._last_hou_selection = selected_paths

        self.table.blockSignals(True)
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            if row >= len(self._filtered):
                break
            if self._filtered[row]["node"].path() in selected_paths:
                self.table.selectRow(row)
        self.table.blockSignals(False)

    def _apply_filter(self):
        text = self.search_box.text().lower()
        mode = self.filter_combo.currentIndex()   # 0=all, 1=missing, 2=found

        self._filtered = []
        for e in self._entries:
            if mode == 1 and e["exists"]:
                continue
            if mode == 2 and not e["exists"]:
                continue
            if text:
                blob = " ".join([
                    e["node"].name(), e["node"].type().name(),
                    e["parm_name"], e["raw"], e["resolved"]
                ]).lower()
                if text not in blob:
                    continue
            self._filtered.append(e)

        self._populate_table()

    def _populate_table(self):
        v_scroll = self.table.verticalScrollBar().value()
        self.table.setRowCount(0)
        for row_idx, e in enumerate(self._filtered):
            self.table.insertRow(row_idx)

            # status dot
            dot = QtWidgets.QTableWidgetItem(
                "●" if e["exists"] else "●"
            )
            dot.setForeground(QtGui.QColor(OK_GREEN if e["exists"] else MISS_RED))
            dot.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            dot.setToolTip("File found" if e["exists"] else "File NOT found")
            self.table.setItem(row_idx, COL_STATUS, dot)

            # node path (click to select in Houdini)
            node_item = QtWidgets.QTableWidgetItem(e["node"].path())
            node_item.setForeground(QtGui.QColor(ACCENT))
            node_item.setToolTip("Double-click to select node in Houdini")
            self.table.setItem(row_idx, COL_NODE, node_item)

            # type
            self.table.setItem(row_idx, COL_TYPE,
                               QtWidgets.QTableWidgetItem(e["node"].type().name()))

            # parm name
            parm_item = QtWidgets.QTableWidgetItem(e["parm_name"])
            parm_item.setForeground(QtGui.QColor(TEXT_DIM))
            self.table.setItem(row_idx, COL_PARM, parm_item)

            # path — raw ($VAR/...) or expanded (absolute) depending on toggle
            path_display = e["expanded"] if self._show_absolute else e["raw"]
            path_item = QtWidgets.QTableWidgetItem(path_display)
            path_item.setData(QtCore.Qt.ItemDataRole.UserRole, e["exists"])
            path_item.setToolTip(e["raw"] if self._show_absolute else e["expanded"])
            self.table.setItem(row_idx, COL_PATH, path_item)

            # action buttons widget
            btn_widget = QtWidgets.QWidget()
            btn_layout = QtWidgets.QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(4, 2, 4, 2)
            btn_layout.setSpacing(4)

            btn_browse = QtWidgets.QPushButton("Browse")
            btn_browse.setFixedHeight(22)
            btn_browse.setToolTip("Pick a new file for this parameter")
            btn_browse.clicked.connect(functools.partial(self._browse_single, row_idx))
            btn_layout.addWidget(btn_browse)

            btn_reveal = QtWidgets.QPushButton("📂")
            btn_reveal.setFixedWidth(28)
            btn_reveal.setFixedHeight(22)
            btn_reveal.setToolTip("Open folder in file explorer")
            btn_reveal.clicked.connect(functools.partial(self._reveal_in_explorer, row_idx))
            btn_layout.addWidget(btn_reveal)

            self.table.setCellWidget(row_idx, COL_ACTIONS, btn_widget)
            self.table.setRowHeight(row_idx, 30)

        self.table.verticalScrollBar().setValue(v_scroll)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _on_double_click(self, index):
        """Select the node in Houdini's network editor."""
        row = index.row()
        if row >= len(self._filtered):
            return
        e = self._filtered[row]
        try:
            hou.clearAllSelected()
            e["node"].setSelected(True)
            # Try to jump the network editor to the node
            desk = hou.ui.curDesktop()
            pane = desk.paneTabOfType(hou.paneTabType.NetworkEditor)
            if pane:
                pane.cd(e["node"].parent().path())
                pane.homeToSelection()
        except Exception as ex:
            self.status_label.setText(f"Could not select node: {ex}")

    def _browse_single(self, row, *args):
        """Open file dialog to pick a new path for a single row."""
        if row >= len(self._filtered):
            return
        e = self._filtered[row]
        current = e["expanded"] or ""
        start_dir = os.path.dirname(current) if current else ""

        new_path = hou.ui.selectFile(
            start_directory=start_dir,
            title=f"Relink — {e['node'].path()} [{e['parm_name']}]",
            collapse_sequences=False,
            file_type=hou.fileType.Any,
            chooser_mode=hou.fileChooserMode.Read,
        )
        if not new_path:
            return

        with hou.undos.group("Asset Manager: Relink"):
            e["parm"].set(new_path)

        self.status_label.setText(f"Relinked {e['node'].path()} → {new_path}")
        self.refresh()

    def _relink_selected(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if not rows:
            QtWidgets.QMessageBox.information(self, "Nothing selected",
                "Select one or more rows first.")
            return
        entries = [self._filtered[r] for r in rows if r < len(self._filtered)]
        count = 0
        with hou.undos.group("Asset Manager: Relink Selected"):
            for e in entries:
                current = e["expanded"] or ""
                start_dir = os.path.dirname(current) if current else ""
                new_path = hou.ui.selectFile(
                    start_directory=start_dir,
                    title=f"Relink  {e['node'].path()}  [{e['parm_name']}]",
                    collapse_sequences=False,
                    file_type=hou.fileType.Any,
                    chooser_mode=hou.fileChooserMode.Read,
                )
                if not new_path:
                    continue
                e["parm"].set(new_path)
                count += 1
        if count:
            self.status_label.setText(f"  Relinked {count} path(s).")
            self.refresh()

    def _search_in_directory(self):
        """
        Pick a root directory, walk its entire subtree, and relink any entry
        whose filename matches a file found there.
        Scope: selected rows if any, otherwise all missing entries.
        """
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if rows:
            entries = [self._filtered[r] for r in rows if r < len(self._filtered)]
            scope_label = f"{len(entries)} selected row(s)"
        else:
            entries = [e for e in self._filtered if not e["exists"]]
            if not entries:
                QtWidgets.QMessageBox.information(self, "Nothing to search",
                    "Select rows or have missing files visible.")
                return
            scope_label = f"{len(entries)} missing file(s)"

        search_dir = hou.ui.selectFile(
            title=f"Search directory for {scope_label}",
            collapse_sequences=False,
            file_type=hou.fileType.Directory,
            chooser_mode=hou.fileChooserMode.Read,
        )
        if not search_dir:
            return
        search_dir = search_dir.rstrip("/\\")

        self.status_label.setText(f"  Searching {search_dir} …")
        QtWidgets.QApplication.processEvents()

        # Normalise the search root — hou.ui.selectFile may return forward slashes on Windows
        search_dir = os.path.normpath(search_dir)

        if not os.path.isdir(search_dir):
            QtWidgets.QMessageBox.warning(self, "Directory not found",
                f"Cannot access:\n{search_dir}")
            return

        # Index: lowercase filename → [absolute normalised paths]
        # Also build a "fuzzy" index: normalised-stem → [absolute normalised paths]
        # where normalised-stem replaces hyphens, spaces and underscores with a
        # single token so "Hot-Pink" == "hot_pink" == "hot pink"
        file_index       = {}   # exact lowercase filename
        file_index_fuzzy = {}   # fuzzy key (stem only, separators collapsed)

        def _fuzzy_key(name):
            """Lower-case, collapse hyphens/underscores/spaces, keep extension separate."""
            root, ext = os.path.splitext(name.lower())
            root = re.sub(r'[-_ ]+', '_', root)
            return root + ext

        for dirpath, _, filenames in os.walk(search_dir):
            for fname in filenames:
                full = os.path.normpath(os.path.join(dirpath, fname))
                file_index.setdefault(fname.lower(), []).append(full)
                file_index_fuzzy.setdefault(_fuzzy_key(fname), []).append(full)

        found    = []
        no_match = []
        debug_lines = []

        for e in entries:
            raw_resolved = e["resolved"] or ""
            raw_expanded = e["expanded"] or ""

            for raw in [raw_expanded, raw_resolved]:
                norm  = os.path.normpath(raw) if raw else ""
                fname = os.path.basename(norm)
                if not fname:
                    continue
                # 1. exact match
                candidates = file_index.get(fname.lower(), [])
                if candidates:
                    break
                # 2. fuzzy match (hyphens ↔ underscores ↔ spaces)
                candidates = file_index_fuzzy.get(_fuzzy_key(fname), [])
                if candidates:
                    break
            else:
                fname      = os.path.basename(os.path.normpath(raw_expanded or raw_resolved))
                candidates = []

            debug_lines.append(
                f"resolved={raw_resolved!r}\n"
                f"  expanded={raw_expanded!r}\n"
                f"  looking for={fname!r}  hits={len(candidates)}"
            )

            if len(candidates) == 1:
                found.append((e, candidates[0]))
            elif len(candidates) > 1:
                chosen, ok = QtWidgets.QInputDialog.getItem(
                    self,
                    f"Multiple matches — {fname}",
                    f"Choose the correct file for:\n{e['node'].path()}  [{e['parm_name']}]",
                    candidates, 0, False
                )
                if ok and chosen:
                    found.append((e, chosen))
                else:
                    no_match.append(e)
            else:
                no_match.append(e)

        if not found:
            searched = ", ".join(
                os.path.basename(os.path.normpath(e["expanded"] or e["resolved"]))
                for e in entries[:5]
            ) + ("…" if len(entries) > 5 else "")
            self.status_label.setText("  Search complete — no matches found.")
            QtWidgets.QMessageBox.information(self, "No Matches",
                f"None of the {len(entries)} filename(s) were found under:\n{search_dir}\n\n"
                f"Looked for: {searched}")
            return

        # Summary dialog
        dlg = SearchResultsDialog(found, no_match, search_dir, self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            self.status_label.setText("  Search cancelled.")
            return

        count = 0
        with hou.undos.group("Asset Manager: Search in Directory"):
            for e, new_path in found:
                try:
                    e["parm"].set(new_path)
                    count += 1
                except Exception:
                    pass

        self.status_label.setText(f"  Relinked {count} of {len(entries)} path(s).")
        self.refresh()

    # ------------------------------------------------------------------
    # Find / Replace
    # ------------------------------------------------------------------

    def _wildcard_to_regex(self, pattern, case_sensitive):
        """Convert a wildcard pattern (* = any chars, ? = one char) to a compiled re."""
        import fnmatch
        # fnmatch.translate produces a full-match regex; we want a search regex instead
        # so we strip the \Z anchor and wrap in a non-capturing group
        rx = fnmatch.translate(pattern)
        # fnmatch adds \Z at end — remove it so we can search anywhere in the string
        rx = rx.rstrip("\\Z").rstrip(r"\Z")
        # Also strip the leading (?s:  and trailing ) added by newer Python
        if rx.startswith("(?s:") and rx.endswith(")"):
            rx = rx[4:-1]
        flags = 0 if case_sensitive else re.IGNORECASE
        return re.compile(rx, flags)

    def _on_find_changed(self):
        """Recompile the find pattern and refresh highlights."""
        text = self.find_edit.text()
        case_sensitive = self.match_case_cb.isChecked()
        if not text:
            self._find_pattern = None
            self._match_label.setText("")
        else:
            try:
                self._find_pattern = self._wildcard_to_regex(text, case_sensitive)
            except re.error:
                self._find_pattern = None
                self._match_label.setStyleSheet(f"color:{MISS_RED}; font-size:11px;")
                self._match_label.setText("bad pattern")
                self.table.viewport().update()
                return

            count = sum(
                1 for e in self._filtered
                if self._find_pattern.search(e["raw"])
            )
            if count:
                self._match_label.setStyleSheet(f"color:{ACCENT}; font-size:11px;")
                self._match_label.setText(f"{count} match{'es' if count != 1 else ''}")
            else:
                self._match_label.setStyleSheet(f"color:{MISS_RED}; font-size:11px;")
                self._match_label.setText("no matches")

        self.table.viewport().update()

    def _on_replace_changed(self):
        """Update the live replace preview in the path column."""
        txt = self.replace_edit.text()
        # Store None when empty so the delegate knows not to show replace mode
        self._replace_str = txt if self.find_edit.text() else None
        self.table.viewport().update()

    def _do_replace(self, entries):
        """Apply find→replace directly to the given entries (no preview dialog)."""
        find_text    = self.find_edit.text()
        replace_text = self.replace_edit.text()

        if not find_text:
            QtWidgets.QMessageBox.warning(self, "Empty Find", "Enter a search string first.")
            return 0

        case_sensitive = self.match_case_cb.isChecked()
        try:
            pattern = self._wildcard_to_regex(find_text, case_sensitive)
        except re.error as err:
            QtWidgets.QMessageBox.warning(self, "Bad Pattern", f"Invalid pattern:\n{err}")
            return 0

        count = 0
        with hou.undos.group("Asset Manager: Find & Replace"):
            for e in entries:
                old_val = e["raw"]
                new_val = pattern.sub(replace_text, old_val)
                if new_val != old_val:
                    try:
                        e["parm"].set(new_val)
                        count += 1
                    except Exception:
                        pass

        if not count:
            self.status_label.setText("  No matches — nothing changed.")
            return 0

        self.refresh()
        return count

    def _replace_selected(self):
        rows = list({idx.row() for idx in self.table.selectedIndexes()})
        if not rows:
            QtWidgets.QMessageBox.information(self, "Nothing selected",
                "Select one or more rows first.")
            return
        entries = [self._filtered[r] for r in rows if r < len(self._filtered)]
        n = self._do_replace(entries)
        if n:
            self.status_label.setText(f"  Replaced {n} path(s).")

    def _replace_all(self):
        n = self._do_replace(self._filtered)
        if n:
            self.status_label.setText(f"  Replaced {n} path(s).")

    # ------------------------------------------------------------------
    # Absolute / Relative conversion
    # ------------------------------------------------------------------

    def _houdini_variables(self):
        """
        Collect all Houdini environment variables, expand them, keep only those
        that look like directory paths, then sort longest-expanded-value-first.
        The longest expanded path = most specific variable = highest priority.
        e.g.  $IN  → D:/Dropbox/.../IN   (50 chars)  beats
              $DROPBOX → D:/Dropbox/      (10 chars)
        """
        known = {}

        # Built-in Houdini variables
        for name in ("HIP", "JOB", "HFS", "HOME", "TEMP", "HSITE", "HIP_NAME"):
            expanded = hou.expandString(f"${name}").replace("\\", "/").rstrip("/")
            if expanded and expanded != f"${name}":
                known[name] = expanded

        # All hscript variables
        try:
            output, _ = hou.hscript("set")
            for line in output.splitlines():
                line = line.strip()
                if not line or "=" not in line:
                    continue
                name, _, val = line.partition("=")
                name = name.strip().lstrip("set").strip()
                val  = val.strip().strip("'\"")
                if not name or not val:
                    continue
                # Expand the value through Houdini so nested vars like $HIP/IN resolve
                expanded = hou.expandString(val).replace("\\", "/").rstrip("/")
                if not expanded or expanded == val:
                    # Try expanding with the $ prefix in case it's a bare token
                    expanded = hou.expandString(f"${name}").replace("\\", "/").rstrip("/")
                if not expanded or expanded == f"${name}":
                    continue
                if "/" not in expanded and not os.path.isdir(expanded):
                    continue
                known[name] = expanded   # overwrite — last definition wins
        except Exception:
            pass

        # Sort by expanded value length, longest first — most specific wins
        return sorted(known.items(), key=lambda kv: -len(kv[1]))

    def _selected_or_all(self):
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        if rows:
            return [self._filtered[r] for r in rows if r < len(self._filtered)]
        return list(self._filtered)

    def _make_absolute(self):
        """Expand all $VARIABLE tokens in selected (or all) paths to their full values."""
        entries = self._selected_or_all()
        variables = self._houdini_variables()
        count = 0
        with hou.undos.group("Asset Manager: Make Absolute"):
            for e in entries:
                raw = e["raw"]
                if "$" not in raw:
                    continue
                result = raw
                for name, val in variables:
                    result = result.replace(f"${name}", val)
                # let Houdini expand anything remaining
                result = hou.expandString(result).replace("\\", "/")
                if result != raw:
                    try:
                        e["parm"].set(result)
                        count += 1
                    except Exception:
                        pass
        self.status_label.setText(f"  Made absolute: {count} path(s).")
        if count:
            self.refresh()

    def _make_relative(self):
        """Replace the longest matching variable prefix in selected (or all) paths."""
        entries   = self._selected_or_all()
        variables = self._houdini_variables()   # sorted longest-value-first
        count = 0
        with hou.undos.group("Asset Manager: Make Relative"):
            for e in entries:
                raw      = e["raw"]
                absolute = e["expanded"].replace("\\", "/").rstrip("/")
                if not absolute:
                    continue
                for name, val in variables:
                    if absolute.lower().startswith(val.lower()):
                        rest = absolute[len(val):]
                        if rest and not rest.startswith("/"):
                            continue   # partial segment — skip
                        remainder = rest if rest.startswith("/") else ("/" + rest if rest else "")
                        new_val = f"${name}{remainder}"
                        if new_val != raw:
                            try:
                                e["parm"].set(new_val)
                                count += 1
                            except Exception:
                                pass
                        break   # stop at first (most specific) match
        self.status_label.setText(f"  Made relative: {count} path(s).")
        if count:
            self.refresh()


    def _reveal_in_explorer(self, row, *args):
        if row >= len(self._filtered):
            return
        e = self._filtered[row]
        path = e["expanded"]
        folder = os.path.dirname(path) if path else ""
        if not folder or not os.path.exists(folder):
            self.status_label.setText(f"Directory not found: {folder}")
            return
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(folder)
        )

    def _context_menu(self, pos):
        rows = list({idx.row() for idx in self.table.selectedIndexes()})
        if not rows:
            return

        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background:#404040; border:1px solid {BORDER}; color:{TEXT_MAIN}; padding:2px; }}
            QMenu::item {{ padding:4px 20px 4px 10px; }}
            QMenu::item:selected {{ background:{SEL_BG}; color:#ffffff; }}
            QMenu::separator {{ background:{BORDER}; height:1px; margin:3px 6px; }}
        """)

        act_copy_path = menu.addAction("Copy Path")
        act_select_node = menu.addAction("Select Node in Houdini")
        menu.addSeparator()
        act_relink = menu.addAction("Relink Selected …")
        act_search_dir = menu.addAction("Search in Directory …")
        act_make_abs = menu.addAction("Make Absolute")
        act_make_rel = menu.addAction("Make Relative")
        act_reveal = menu.addAction("Reveal in File Explorer")

        action = menu.exec(self.table.viewport().mapToGlobal(pos))

        if action == act_copy_path and rows:
            paths = "\n".join(self._filtered[r]["resolved"]
                              for r in rows if r < len(self._filtered))
            QtWidgets.QApplication.clipboard().setText(paths)

        elif action == act_select_node and rows:
            hou.clearAllSelected()
            for r in rows:
                if r < len(self._filtered):
                    self._filtered[r]["node"].setSelected(True)

        elif action == act_relink:
            self._relink_selected()

        elif action == act_search_dir:
            self._search_in_directory()

        elif action == act_make_abs:
            self._make_absolute()

        elif action == act_make_rel:
            self._make_relative()

        elif action == act_reveal and rows:
            self._reveal_in_explorer(rows[0])

# ---------------------------------------------------------------------------
# Search Results Dialog
# ---------------------------------------------------------------------------

class SearchResultsDialog(QtWidgets.QDialog):
    """Shows what was found (and what wasn't) before applying."""

    def __init__(self, found, not_found, search_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search Results")
        self.setStyleSheet(STYLE)
        self.resize(780, 460)
        self._build_ui(found, not_found, search_dir)

    def _build_ui(self, found, not_found, search_dir):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        summary = QtWidgets.QLabel(
            f"Found <b style='color:{OK_GREEN}'>{len(found)}</b> match(es)  ·  "
            f"<b style='color:{MISS_RED}'>{len(not_found)}</b> not found  ·  "
            f"in <span style='color:{TEXT_DIM}'>{search_dir}</span>"
        )
        summary.setTextFormat(QtCore.Qt.TextFormat.RichText)
        lay.addWidget(summary)

        table = QtWidgets.QTableWidget(len(found) + len(not_found), 3)
        table.setHorizontalHeaderLabels(["Node", "Original path", "New path"])
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        table.setShowGrid(False)

        for row, (e, new_path) in enumerate(found):
            n = QtWidgets.QTableWidgetItem(e["node"].path())
            n.setForeground(QtGui.QColor(ACCENT))
            table.setItem(row, 0, n)
            o = QtWidgets.QTableWidgetItem(e["resolved"])
            o.setForeground(QtGui.QColor(TEXT_DIM))
            table.setItem(row, 1, o)
            p = QtWidgets.QTableWidgetItem(new_path)
            p.setForeground(QtGui.QColor(OK_GREEN))
            table.setItem(row, 2, p)
            table.setRowHeight(row, 24)

        offset = len(found)
        for row, e in enumerate(not_found):
            n = QtWidgets.QTableWidgetItem(e["node"].path())
            n.setForeground(QtGui.QColor(TEXT_DIM))
            table.setItem(offset + row, 0, n)
            o = QtWidgets.QTableWidgetItem(e["resolved"])
            o.setForeground(QtGui.QColor(MISS_RED))
            table.setItem(offset + row, 1, o)
            nf = QtWidgets.QTableWidgetItem("— not found —")
            nf.setForeground(QtGui.QColor(MISS_RED))
            table.setItem(offset + row, 2, nf)
            table.setRowHeight(offset + row, 24)

        lay.addWidget(table, 1)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_cancel = QtWidgets.QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        btn_apply = QtWidgets.QPushButton(f"Apply {len(found)} relink(s)")
        btn_apply.setObjectName("btn_accent")
        btn_apply.clicked.connect(self.accept)
        btn_row.addWidget(btn_apply)
        lay.addLayout(btn_row)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def createInterface():
    """Python Panel entry point — Houdini calls this to embed the widget."""
    widget = AssetManagerWindow()
    return widget


def launch_asset_manager():
    """Shelf tool entry point — opens as a floating window."""
    win = AssetManagerWindow(parent=hou.qt.mainWindow())
    win.setWindowFlags(QtCore.Qt.WindowType.Window)
    win.show()
    hou.session.__asset_manager_win__ = win


# When run directly from a shelf tool, launch as floating window
if __name__ == "__main__":
    launch_asset_manager()