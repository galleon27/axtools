import hou
import os
import re
import fnmatch
from PySide6 import QtWidgets, QtCore, QtGui

# ==============================================================================
# CONFIGURATION
# ==============================================================================
NODE_MAPPING = {
    'file': ['file', 'filename'],    
    'alembic': 'fileName',           
    'filecache': 'file',             
    'mtlximage': 'file',             
    'mtlxunknown': 'file',           
    'texture::2.0': 'map',           
    'rs_texture': 'tex0',            
    'arnold_image': 'filename',      
    'usdimport': 'filepath1',        
    'sublayer': 'filepath1',         
    'bonemaptarget': 'file',         
    'octane::NT_TEX_IMAGE': 'A_FILENAME', 
}

# ==============================================================================
# CUSTOM DELEGATE
# ==============================================================================
class AssetDelegate(QtWidgets.QStyledItemDelegate):
    def __init__(self, parent=None):
        super(AssetDelegate, self).__init__(parent)
        self.regex_pattern = ""

    def set_regex(self, regex_str):
        self.regex_pattern = regex_str

    def paint(self, painter, option, index):
        self.initStyleOption(option, index)
        option.text = "" 
        painter.save()
        painter.setClipRect(option.rect) 
        
        style = option.widget.style() if option.widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.CE_ItemViewItem, option, painter, option.widget)

        text = index.data(QtCore.Qt.DisplayRole)
        if text:
            fg_brush = index.data(QtCore.Qt.ForegroundRole)
            if option.state & QtWidgets.QStyle.State_Selected:
                text_color = "#ffffff"
            else:
                text_color = fg_brush.color().name() if fg_brush else "#dadada"

            html_text = text
            if self.regex_pattern:
                try:
                    def hl(match):
                        return f"<span style='background-color: #d4aa00; color: #000000; font-weight:bold;'>{match.group(0)}</span>"
                    highlighted = re.sub(self.regex_pattern, hl, text, flags=re.IGNORECASE)
                    html_text = f"<div style='color: {text_color}; white-space: pre;'>{highlighted}</div>"
                except:
                    html_text = f"<div style='color: {text_color}; white-space: pre;'>{text}</div>"
            else:
                 html_text = f"<div style='color: {text_color}; white-space: pre;'>{text}</div>"

            text_rect = option.rect.adjusted(5, 0, -5, 0)
            doc = QtGui.QTextDocument()
            doc.setDefaultStyleSheet("div { font-family: Source Sans Pro, Segoe UI, sans-serif; font-size: 13px; }")
            doc.setHtml(html_text)
            painter.translate(text_rect.left(), text_rect.top() + (text_rect.height() - doc.size().height()) / 2)
            doc.drawContents(painter)

        painter.restore()

# ==============================================================================
# MAIN PANEL WIDGET
# ==============================================================================
class AssetManagerPanel(QtWidgets.QWidget):
    def __init__(self):
        super(AssetManagerPanel, self).__init__()
        
        self.setStyleSheet("""
            QGroupBox { font-weight: bold; border: 1px solid #444; margin-top: 6px; padding-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px; color: #ccc; }
            QPushButton { padding: 5px; }
            QComboBox { padding: 5px; }
        """)
        
        layout = QtWidgets.QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        self.setLayout(layout)
        
        # --- TOOLBAR ---
        toolbar_layout = QtWidgets.QHBoxLayout()
        
        self.btn_refresh = QtWidgets.QPushButton(" Refresh List")
        self.btn_refresh.clicked.connect(self.refresh_assets)
        self.btn_refresh.setStyleSheet("background-color: #d48806; color: white; font-weight: bold;")
        
        self.cmb_status_filter = QtWidgets.QComboBox()
        self.cmb_status_filter.addItems(["All Statuses", "MISSING", "OK", "VAR"])
        self.cmb_status_filter.setToolTip("Filter by status.\nNote: 'MISSING' includes 'VAR'.")
        self.cmb_status_filter.currentIndexChanged.connect(self.on_top_filter_changed)
        self.cmb_status_filter.setFixedWidth(100)
        
        self.le_filter = QtWidgets.QLineEdit()
        self.le_filter.setPlaceholderText("Filter name/path... (Use * for wildcards)")
        self.le_filter.textChanged.connect(self.on_top_filter_changed)
        
        self.btn_localize = QtWidgets.QPushButton("Make Relative")
        self.btn_localize.clicked.connect(self.make_paths_relative)
        
        self.btn_globalize = QtWidgets.QPushButton("Make Absolute")
        self.btn_globalize.clicked.connect(self.make_paths_absolute)
        
        toolbar_layout.addWidget(self.btn_refresh)
        toolbar_layout.addWidget(self.cmb_status_filter)
        toolbar_layout.addWidget(self.le_filter)
        toolbar_layout.addWidget(QtWidgets.QLabel("|"))
        toolbar_layout.addWidget(self.btn_localize)
        toolbar_layout.addWidget(self.btn_globalize)
        
        layout.addLayout(toolbar_layout)
        
        # --- TABLE VIEW ---
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Status", "Node Name", "Node Type", "Current Path", "Full Node Path"])
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.EditKeyPressed)
        
        self.table.cellDoubleClicked.connect(self.on_double_click)
        self.table.itemChanged.connect(self.on_item_changed)
        
        self.delegate = AssetDelegate(self.table)
        self.table.setItemDelegateForColumn(3, self.delegate)
        
        self.table.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.open_context_menu)
        
        layout.addWidget(self.table)
        
        # --- BOTTOM: SMART REPLACE ---
        fr_group = QtWidgets.QGroupBox("Find & Replace (Selected Assets)")
        fr_layout = QtWidgets.QHBoxLayout()
        fr_group.setLayout(fr_layout)
        
        self.le_find_wild = QtWidgets.QLineEdit()
        self.le_find_wild.setPlaceholderText("Find (e.g. Path/To/File or v00?)")
        self.le_find_wild.textChanged.connect(self.on_bottom_replace_changed)
        
        self.le_replace_wild = QtWidgets.QLineEdit()
        self.le_replace_wild.setPlaceholderText("Replace with...")
        self.le_replace_wild.textChanged.connect(self.on_bottom_replace_changed)
        
        self.btn_replace = QtWidgets.QPushButton("Replace")
        self.btn_replace.clicked.connect(self.batch_replace_wildcard)
        
        fr_layout.addWidget(QtWidgets.QLabel("Find:"))
        fr_layout.addWidget(self.le_find_wild)
        fr_layout.addWidget(QtWidgets.QLabel("Replace:"))
        fr_layout.addWidget(self.le_replace_wild)
        fr_layout.addWidget(self.btn_replace)
        
        layout.addWidget(fr_group)
        
        self.cached_nodes =[] 
        self.refresh_assets()
        
        # --- SYNCHRONIZE WITH HOUDINI SELECTION ---
        self._last_selection = ()
        self.selection_timer = QtCore.QTimer(self)
        self.selection_timer.timeout.connect(self.sync_selection_from_houdini)
        self.selection_timer.start(250) # Polling 4 times a second

    # ==========================================================================
    # LOGIC: SELECTION SYNC
    # ==========================================================================
    def sync_selection_from_houdini(self):
        """ Monitors Houdini's network pane and highlights the matching rows here. """
        if not self.isVisible(): 
            return
            
        try:
            curr_sel = tuple(hou.selectedNodes())
        except:
            return
            
        if curr_sel == self._last_selection:
            return
            
        self._last_selection = curr_sel
        sel_set = set(curr_sel)
        
        self.table.blockSignals(True)
        self.table.clearSelection()
        
        for i, (node, parm) in enumerate(self.cached_nodes):
            try:
                # If node is currently selected in Houdini and the row is not hidden
                if node in sel_set and not self.table.isRowHidden(i):
                    for col in range(self.table.columnCount()):
                        item = self.table.item(i, col)
                        if item:
                            item.setSelected(True)
            except hou.ObjectWasDeleted:
                pass
                
        self.table.blockSignals(False)

    # ==========================================================================
    # LOGIC: CORE
    # ==========================================================================
    def check_file_status(self, raw_path, expanded_path):
        if not raw_path or raw_path.strip() == "": return "EMPTY", QtGui.QColor(100, 100, 100)
        seq_tokens =["$F", "<UDIM>", "<uvtile>", "%(UDIM)", "%04d", "$T", "`"]
        if any(token in raw_path for token in seq_tokens): return "VAR", QtGui.QColor(255, 170, 0)
        if os.path.exists(expanded_path): return "OK", QtGui.QColor(100, 200, 100)
        return "MISSING", QtGui.QColor(255, 60, 60)

    def update_row(self, row, node, parm):
        raw_val = parm.rawValue()
        try: expanded_val = parm.eval()
        except: expanded_val = raw_val
        
        status_text, text_color = self.check_file_status(raw_val, expanded_val)
        
        item_status = QtWidgets.QTableWidgetItem(status_text)
        item_status.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        item_status.setForeground(QtGui.QBrush(text_color))
        font = item_status.font(); font.setBold(True); item_status.setFont(font)
        item_status.setFlags(item_status.flags() ^ QtCore.Qt.ItemIsEditable)
        self.table.setItem(row, 0, item_status)
        
        for idx, txt in enumerate([node.name(), node.type().name()]):
            item = QtWidgets.QTableWidgetItem(txt)
            item.setForeground(QtGui.QBrush(text_color))
            item.setFlags(item.flags() ^ QtCore.Qt.ItemIsEditable)
            self.table.setItem(row, idx+1, item)
        
        item_path = QtWidgets.QTableWidgetItem(raw_val)
        item_path.setForeground(QtGui.QBrush(text_color))
        item_path.setToolTip(raw_val)
        item_path.setFlags(item_path.flags() | QtCore.Qt.ItemIsEditable)
        self.table.setItem(row, 3, item_path)
        
        item_full = QtWidgets.QTableWidgetItem(node.path())
        item_full.setForeground(QtGui.QBrush(text_color))
        item_full.setToolTip(node.path())
        item_full.setFlags(item_full.flags() ^ QtCore.Qt.ItemIsEditable)
        self.table.setItem(row, 4, item_full)

    def on_item_changed(self, item):
        if item.column() != 3: return
        row = item.row()
        if row < 0 or row >= len(self.cached_nodes): return
        self.table.blockSignals(True)
        try:
            node, parm = self.cached_nodes[row]
            parm.set(item.text())
            self.update_row(row, node, parm)
        except: pass
        self.table.blockSignals(False)

    # ==========================================================================
    # LOGIC: SLASH-AGNOSTIC REGEX
    # ==========================================================================
    def get_smart_regex(self, find_pat, repl_pat=""):
        if not find_pat: return ""
        
        if find_pat.startswith('*') and repl_pat.startswith('*'):
            find_pat = find_pat[1:]
        if find_pat.endswith('*') and repl_pat.endswith('*'):
            find_pat = find_pat[:-1]

        find_pat = find_pat.replace('*', '___STAR___')
        find_pat = find_pat.replace('?', '___QUES___')
        
        normalized_pat = find_pat.replace('\\', '/')
        parts = normalized_pat.split('/')
        escaped_parts =[re.escape(p) for p in parts]
        
        regex_str = r'[\\/]'.join(escaped_parts)
        regex_str = regex_str.replace('___STAR___', '.*')
        regex_str = regex_str.replace('___QUES___', '.')
        
        return regex_str

    # ==========================================================================
    # EVENTS
    # ==========================================================================
    def refresh_assets(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self.cached_nodes =[]
        all_nodes = hou.node("/").allSubChildren()
        row = 0
        for node in all_nodes:
            type_name = node.type().name()
            if type_name == 'filecache': continue

            if type_name in NODE_MAPPING:
                param_candidates = NODE_MAPPING[type_name]
                if not isinstance(param_candidates, list): param_candidates =[param_candidates]
                parm = None
                for p_name in param_candidates:
                    if node.parm(p_name): parm = node.parm(p_name); break
                
                if parm:
                    raw_val = parm.rawValue()
                    if not raw_val or raw_val.strip() == "": continue
                    if "`" in raw_val or "ch(" in raw_val or "chs(" in raw_val or "@" in raw_val: continue
                    if raw_val.startswith(".") or raw_val.startswith("op:"): continue

                    self.cached_nodes.append((node, parm))
                    self.table.insertRow(row)
                    self.update_row(row, node, parm)
                    self.table.item(row, 0).setData(QtCore.Qt.ItemDataRole.UserRole, row)
                    row += 1
        self.table.blockSignals(False)
        self.on_top_filter_changed() 

    def on_top_filter_changed(self):
        pattern = self.le_filter.text()
        status_filter = self.cmb_status_filter.currentText()
        
        regex = self.get_smart_regex(pattern) if pattern else ""
        self.delegate.set_regex(regex)
        self.table.viewport().update()
        
        for i in range(self.table.rowCount()):
            path_text = self.table.item(i, 3).text()
            status_text = self.table.item(i, 0).text()
            
            text_match = True
            if regex:
                text_match = re.search(regex, path_text, re.IGNORECASE) is not None
            
            status_match = True
            if status_filter == "MISSING":
                if status_text not in ["MISSING", "VAR"]: status_match = False
            elif status_filter != "All Statuses":
                if status_text != status_filter: status_match = False
            
            self.table.setRowHidden(i, not (text_match and status_match))

    def on_bottom_replace_changed(self):
        find_pat = self.le_find_wild.text()
        repl_pat = self.le_replace_wild.text()
        regex = self.get_smart_regex(find_pat, repl_pat)
        self.delegate.set_regex(regex)
        self.table.viewport().update()

    def batch_replace_wildcard(self):
        find_pat = self.le_find_wild.text()
        repl_pat = self.le_replace_wild.text()
        if not find_pat: return
        selected_rows = self.get_selected_indices()
        if not selected_rows: return
        
        final_repl = repl_pat
        if find_pat.startswith('*') and repl_pat.startswith('*'):
            final_repl = final_repl[1:]
        if find_pat.endswith('*') and repl_pat.endswith('*'):
            final_repl = final_repl[:-1]
            
        regex_str = self.get_smart_regex(find_pat, repl_pat)
        
        with hou.undos.group("Asset Manager Replace"):
            self.table.blockSignals(True)
            for i in selected_rows:
                node, parm = self.cached_nodes[i]
                current_raw = parm.rawValue()
                try:
                    new_raw = re.sub(regex_str, lambda m: final_repl, current_raw, flags=re.IGNORECASE)
                    if new_raw != current_raw:
                        parm.set(new_raw)
                        self.update_row(i, node, parm)
                except: pass
            self.table.blockSignals(False)
        hou.ui.setStatusMessage(f"Replaced text in {len(selected_rows)} selected nodes.")

    # ... (Path Utils) ...
    def make_paths_relative(self):
        self._batch_path_op(lambda p: p.replace(hou.getenv("HIP").replace("\\","/"), "$HIP") if p.startswith(hou.getenv("HIP").replace("\\","/")) else p)
    def make_paths_absolute(self):
        self._batch_path_op(lambda p: p) 
    def _batch_path_op(self, op_func):
        sel = self.get_selected_indices()
        if not sel: return
        self.table.blockSignals(True)
        with hou.undos.group("Path Op"):
            for i in sel:
                node, parm = self.cached_nodes[i]
                if op_func == (lambda p: p): val = parm.eval()
                else: val = op_func(parm.eval().replace("\\", "/"))
                parm.set(val)
                self.update_row(i, node, parm)
        self.table.blockSignals(False)

    def get_selected_indices(self):
        rows = set()
        for item in self.table.selectedItems(): rows.add(item.row())
        return list(rows)
    
    def open_context_menu(self, position):
        menu = QtWidgets.QMenu()
        menu.addAction("Search Filename in Directory...", self.search_in_directory)
        menu.addAction("Browse/Relink Manual...", self.browse_new_path)
        menu.addSeparator()
        menu.addAction("Select in Network", self.select_in_network)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def search_in_directory(self):
        rows = self.get_selected_indices()
        if not rows: return
        search_root = hou.ui.selectFile(title="Select Root", file_type=hou.fileType.Directory)
        if not search_root: return
        search_root = hou.expandString(search_root)
        file_map = {}
        for root, dirs, files in os.walk(search_root):
            for file in files:
                if file not in file_map:
                    file_map[file] = os.path.join(root, file).replace("\\", "/")
        with hou.undos.group("Smart Search"):
            self.table.blockSignals(True)
            for idx in rows:
                node, parm = self.cached_nodes[idx]
                fname = os.path.basename(parm.rawValue().replace("\\", "/"))
                if fname in file_map:
                    parm.set(file_map[fname])
                    self.update_row(idx, node, parm)
            self.table.blockSignals(False)

    def browse_new_path(self):
        rows = self.get_selected_indices()
        if not rows: return
        idx = rows[0]
        node, parm = self.cached_nodes[idx]
        start_dir = parm.eval() or hou.getenv("HIP")
        new_file = hou.ui.selectFile(start_directory=start_dir, title="Relink")
        if new_file:
            parm.set(new_file)
            self.table.blockSignals(True)
            self.update_row(idx, node, parm)
            self.table.blockSignals(False)

    def select_in_network(self):
        rows = self.get_selected_indices()
        if not rows: return
        
        # Block our own timer to prevent recursive selection interference
        self._last_selection = None 
        
        for n in hou.selectedNodes(): n.setSelected(False)
        first = None
        for idx in rows:
            node, _ = self.cached_nodes[idx]
            try:
                node.setSelected(True)
                if not first: first = node
            except hou.ObjectWasDeleted:
                pass
        if first:
            p = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
            if p: p.cd(first.parent().path()); p.homeToSelection()
    
    def on_double_click(self, row, col):
        if col != 3: self.select_in_network()

def onCreateInterface():
    return AssetManagerPanel()