"""AX Asset Library main panel."""
import os
import time

import hou
from PySide6 import QtCore, QtGui, QtWidgets

from .. import (importers, library, manage, materials, prefs as _prefs, publish, scene,
               schema, thumbnails)
from . import details, dialogs, grid

Qt = QtCore.Qt
ScopeRole = Qt.ItemDataRole.UserRole + 10
RESCAN_AFTER_S = 60  # rescan when the panel is shown again after this long
RECENTLY_ADDED_DAYS = 14

SORT_MODES = (("name", "Name"), ("type", "Type"), ("category", "Category"),
              ("added", "Newest"), ("modified", "Recently changed"))
IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.tga *.tif *.tiff *.bmp *.webp *.exr *.hdr *.rat)"
GEO_FILTER = "Geometry (%s)" % " ".join("*" + e for e in sorted(schema.GEO_EXTS))
ASSET_FILTER = "Geometry and images (%s)" % " ".join(
    "*" + e for e in sorted(schema.GEO_EXTS | schema.IMAGE_EXTS))

_ICONS = {}


def icon(name):
    ic = _ICONS.get(name)
    if ic is None:
        try:
            ic = _ICONS[name] = hou.qt.Icon(name)
        except hou.Error:
            ic = _ICONS[name] = QtGui.QIcon()
    return ic


# --- background scan -----------------------------------------------------------------------
class _ScanSignals(QtCore.QObject):
    finished = QtCore.Signal(int, object, object)  # generation, records, errors
    cached = QtCore.Signal(int, object)            # generation, records from the index cache


class _ScanJob(QtCore.QRunnable):
    """Runs library.scan_roots in a worker thread, or with cache_only just reads the index
    caches (a big Megascans cache takes a moment to load). No hou calls here."""

    def __init__(self, generation, roots, cache_dir, use_cache, signals, cache_only=False):
        super().__init__()
        self.generation, self.roots, self.cache_dir = generation, roots, cache_dir
        self.use_cache, self.signals, self.cache_only = use_cache, signals, cache_only

    def run(self):
        if self.cache_only:
            records = []
            for root, kind in self.roots:
                try:
                    records.extend(library.records_from_index(self.cache_dir, root, kind))
                except Exception:  # a broken cache only means no instant first paint
                    pass
            self.signals.cached.emit(self.generation, library.dedupe_megascans(records))
            return
        try:
            records, errors = library.scan_roots(self.roots, self.cache_dir, self.use_cache)
        except Exception as e:  # keep the panel alive whatever the file system does
            records, errors = [], ["scan failed: %s" % e]
        self.signals.finished.emit(self.generation, records, errors)


# --- sidebar --------------------------------------------------------------------------------
class AssetTree(QtWidgets.QTreeWidget):
    """Sidebar. Dropping assets from the grid on a root, type or category moves them there."""

    assets_dropped = QtCore.Signal(list, object)   # record keys, ("scope", root, type, category)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DropOnly)

    def _target(self, event):
        if not event.mimeData().hasFormat(grid.ASSET_MIME):
            return None
        item = self.itemAt(event.position().toPoint())
        scope = item.data(0, ScopeRole) if item is not None else None
        return scope if scope and scope[0] == "scope" else None

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(grid.ASSET_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._target(event) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        scope = self._target(event)
        if scope is None:
            event.ignore()
            return
        keys = bytes(event.mimeData().data(grid.ASSET_MIME)).decode("utf-8").splitlines()
        event.acceptProposedAction()
        # Let the drag finish before dialogs open.
        QtCore.QTimer.singleShot(0, lambda: self.assets_dropped.emit(keys, scope))


# --- panel -----------------------------------------------------------------------------------
class AssetLibraryPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = _prefs.Prefs()
        self.records = []
        self._scan_generation = 0
        self._scanning = False
        self._last_scan = 0.0
        self._scan_errors = []
        self._select_after_scan = set()
        self._usage = {}          # record key -> scene.Usage
        self._hip_callback = None
        self._pool = QtCore.QThreadPool()
        self._pool.setMaxThreadCount(1)
        self._scan_signals = _ScanSignals()
        self._scan_signals.finished.connect(self._on_scan_finished)
        self._scan_signals.cached.connect(self._on_cache_loaded)
        self._cache_generation = 0

        try:
            self.setStyleSheet(hou.qt.styleSheet())
        except hou.Error:
            pass
        self.setAcceptDrops(True)
        self._build_ui()
        self._restore_state()

        self._filter_timer = QtCore.QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._apply_filter)

        self._load_cached()
        self.rescan()
        self._watch_hip()
        from .. import dragdrop
        dragdrop.register(self)

    # ---- layout -----------------------------------------------------------------------------
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QtWidgets.QHBoxLayout()
        bar.setContentsMargins(6, 4, 6, 4)
        bar.setSpacing(4)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search  (type:hdri  tag:rock  cat:trees  -tag:wip)")
        self.search.setClearButtonEnabled(True)
        self.search.setToolTip(
            "Words match name, tags, category and description; all words must match.\n"
            "Fields: type:geo|mat|hdri|img  tag:<tag>  cat:<category>  name:<text>  fmt:abc\n"
            "Prefix '-' to exclude (-tag:wip). Quote words with spaces (tag:\"red oak\").\n"
            "'broken' finds assets with errors or missing files.")
        self.search.textChanged.connect(lambda _t: self._filter_timer.start())
        bar.addWidget(self.search, 1)

        self.type_buttons = {}
        for t in schema.ASSET_TYPES:
            b = QtWidgets.QToolButton()
            b.setText(schema.TYPE_LABELS[t])
            b.setCheckable(True)
            b.setToolTip("Show only %s assets. With no type selected, all types are shown."
                         % schema.TYPE_LABELS[t].lower())
            b.toggled.connect(self._on_type_toggled)
            bar.addWidget(b)
            self.type_buttons[t] = b

        self.sort_combo = QtWidgets.QComboBox()
        for key, label in SORT_MODES:
            self.sort_combo.addItem(label, key)
        self.sort_combo.setToolTip("Sort order of the grid.")
        self.sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        bar.addWidget(self.sort_combo)

        self.size_slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(64, 320)
        self.size_slider.setFixedWidth(100)
        self.size_slider.setToolTip("Thumbnail size.")
        self.size_slider.valueChanged.connect(self._on_size_changed)
        bar.addWidget(self.size_slider)

        self.btn_list = QtWidgets.QToolButton()
        self.btn_list.setText("List")
        self.btn_list.setCheckable(True)
        self.btn_list.setToolTip("Show assets as a list with details instead of a thumbnail grid.")
        self.btn_list.toggled.connect(self._on_list_toggled)
        bar.addWidget(self.btn_list)

        self.btn_publish = QtWidgets.QToolButton()
        self.btn_publish.setText("Save selection")
        self.btn_publish.setIcon(icon("BUTTONS_save"))
        self.btn_publish.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_publish.setToolTip("Save the selected SOP node or Geometry object as an asset. "
                                    "An existing name in the same category saves a new version.")
        self.btn_publish.clicked.connect(lambda: self._publish())
        bar.addWidget(self.btn_publish)

        self.btn_add = QtWidgets.QToolButton()
        self.btn_add.setText("Add")  # shown if the icon is missing
        self.btn_add.setIcon(icon("BUTTONS_list_add"))
        self.btn_add.setToolTip("Add files, a folder or a Megascans library. You can also drop "
                                "files or folders onto the panel.")
        add_menu = QtWidgets.QMenu(self.btn_add)
        add_menu.addAction("Add files...", self._add_files)
        add_menu.addAction("Add folder...", self._add_folder)
        add_menu.addAction("Import Megascans library...", self._add_megascans)
        self.btn_add.setMenu(add_menu)
        self.btn_add.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        bar.addWidget(self.btn_add)

        self.btn_rescan = QtWidgets.QToolButton()
        self.btn_rescan.setText("Rescan")  # shown if the icon is missing
        self.btn_rescan.setIcon(icon("BUTTONS_reload"))
        self.btn_rescan.setToolTip("Rescan the library folders (F5).\n"
                                   "Shift+click: clear the cache and read every asset.json again.")
        self.btn_rescan.clicked.connect(self._on_rescan_clicked)
        bar.addWidget(self.btn_rescan)

        self.btn_settings = QtWidgets.QToolButton()
        self.btn_settings.setText("Settings")  # shown if the icon is missing
        self.btn_settings.setIcon(icon("BUTTONS_gear_mini"))
        self.btn_settings.setToolTip("Library roots and import options.")
        self.btn_settings.clicked.connect(self._open_settings)
        bar.addWidget(self.btn_settings)
        root.addLayout(bar)

        self.splitter = QtWidgets.QSplitter(Qt.Orientation.Horizontal)

        self.tree = AssetTree()
        self.tree.setMinimumWidth(140)
        self.tree.currentItemChanged.connect(self._on_scope_changed)
        self.tree.assets_dropped.connect(self._on_assets_dropped)
        self.splitter.addWidget(self.tree)

        self.stack = QtWidgets.QStackedWidget()
        self.model = grid.AssetModel(self._drag_payload, self)
        self.view = grid.AssetView()
        self.view.setModel(self.model)
        self.view.import_requested.connect(self._import_selected)
        self.view.delete_requested.connect(self._trash_selected)
        self.view.rename_requested.connect(self._rename_selected)
        self.view.copy_requested.connect(self._copy_paths)
        self.view.favorite_requested.connect(self._toggle_favorite_selected)
        self.view.customContextMenuRequested.connect(self._context_menu)
        self.view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.stack.addWidget(self.view)

        empty = QtWidgets.QWidget()
        el = QtWidgets.QVBoxLayout(empty)
        el.addStretch()
        self.empty_label = QtWidgets.QLabel()
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        el.addWidget(self.empty_label)
        b = QtWidgets.QPushButton("Settings...")
        b.clicked.connect(self._open_settings)
        el.addWidget(b, 0, Qt.AlignmentFlag.AlignHCenter)
        el.addStretch()
        self.stack.addWidget(empty)
        self.splitter.addWidget(self.stack)

        self.details = details.DetailsPane()
        self.details.setMinimumWidth(200)
        self.details.import_requested.connect(self._import_selected)
        self.details.copy_requested.connect(self._copy_paths)
        self.details.reveal_requested.connect(self._reveal_selected)
        self.details.thumbnail_requested.connect(self._set_thumbnail)
        self.details.render_requested.connect(self._render_selected)
        self.details.set_current_requested.connect(self._set_current_version)
        self.details.meta_save_requested.connect(self._save_meta)
        self.details.scene_update_requested.connect(self._scene_update_selected)
        self.details.scene_select_requested.connect(self._scene_select_selected)
        queue = thumbnails.karma_queue()
        queue.finished.connect(self._on_render_finished)
        queue.changed.connect(self._on_queue_changed)
        self.splitter.addWidget(self.details)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
        root.addWidget(self.splitter, 1)

        self.status = QtWidgets.QLabel()
        self.status.setContentsMargins(6, 2, 6, 3)
        self.status.setStyleSheet("color: #9aa0a8;")
        root.addWidget(self.status)

        for keys, fn in (("Ctrl+F", self.search.setFocus), ("F5", self.rescan)):
            sc = QtGui.QShortcut(QtGui.QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    def _restore_state(self):
        s = self.settings
        size = s.get("thumb_size")
        self.size_slider.setValue(size)
        self.view.set_thumb_size(size)
        self.btn_list.setChecked(bool(s.get("list_mode")))
        idx = self.sort_combo.findData(s.get("sort"))
        self.sort_combo.setCurrentIndex(max(0, idx))
        for t in s.get("type_filter"):
            if t in self.type_buttons:
                self.type_buttons[t].blockSignals(True)
                self.type_buttons[t].setChecked(True)
                self.type_buttons[t].blockSignals(False)
        sizes = s.get("splitter")
        self.splitter.setSizes(sizes if len(sizes) == 3 else [200, 800, 280])

    def _save_state(self):
        s = self.settings
        s.set("thumb_size", self.size_slider.value(), save=False)
        s.set("list_mode", self.btn_list.isChecked(), save=False)
        s.set("sort", self.sort_combo.currentData(), save=False)
        s.set("type_filter", [t for t, b in self.type_buttons.items() if b.isChecked()], save=False)
        s.set("splitter", self.splitter.sizes(), save=False)
        s.save()

    # ---- scanning ----------------------------------------------------------------------------
    def _roots(self):
        return self.settings.roots()

    def _load_cached(self):
        """Show what the index cache knows as soon as it is read (in the worker thread, before
        the scan that follows); the scan result then replaces it."""
        self._cache_generation += 1
        roots = [(r["expanded"], r["kind"]) for r in self._roots()]
        if not roots:
            self._set_records([])
            return
        self._pool.start(_ScanJob(self._cache_generation, roots, _prefs.cache_dir(), True,
                                  self._scan_signals, cache_only=True))

    def _on_cache_loaded(self, generation, records):
        # Only while the scan hasn't delivered yet: never replace fresher scan results.
        if generation == self._cache_generation and self._scanning:
            self._set_records(records)

    def _looks_like_megascans(self, root):
        cache = self.__dict__.setdefault("_ms_guess", {})
        if root not in cache:
            from .. import megascans
            cache[root] = megascans.looks_like_download(root)
        return cache[root]

    def rescan(self, use_cache=True):
        roots = [(r["expanded"], r["kind"]) for r in self._roots()]
        self._scan_generation += 1
        self._last_scan = time.time()
        if not roots:
            self._set_records([])
            return
        self._scanning = True
        self._update_status()
        self._pool.start(_ScanJob(self._scan_generation, roots, _prefs.cache_dir(), use_cache,
                                  self._scan_signals))

    def _on_rescan_clicked(self):
        full = bool(QtWidgets.QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        if full:
            grid.clear_cache()
        self.rescan(use_cache=not full)

    def _on_scan_finished(self, generation, records, errors):
        if generation != self._scan_generation:
            return  # a newer scan was started meanwhile
        self._scanning = False
        self._scan_errors = errors
        self.records = records
        self._refresh_scene()

    def _set_records(self, records):
        self.records = records
        self._rebuild_tree()
        self._apply_filter()

    # ---- sidebar -----------------------------------------------------------------------------
    def _current_scope(self):
        item = self.tree.currentItem()
        return item.data(0, ScopeRole) if item is not None else ("all",)

    def _rebuild_tree(self):
        prev = self._current_scope()
        self.tree.blockSignals(True)
        self.tree.clear()
        counts = library.tree_counts(self.records)
        bad = sum(1 for r in self.records if r.broken or r.missing)

        def add(parent, text, scope, tip=""):
            it = QtWidgets.QTreeWidgetItem([text])
            it.setData(0, ScopeRole, scope)
            if tip:
                it.setToolTip(0, tip)
            (parent.addChild if parent is not None else self.tree.addTopLevelItem)(it)
            return it

        add(None, "All assets (%d)" % len(self.records), ("all",))
        favs = _prefs.favorites(self.settings)
        recent = _prefs.recent(self.settings)
        n_fav = sum(1 for r in self.records if r.id in favs)
        n_recent = sum(1 for r in self.records if r.id in recent)
        n_added = sum(1 for r in self.records if self._recently_added(r))
        add(None, "Favorites (%d)" % n_fav, ("favorites",),
            "Assets marked with a star (right-click > Add to favorites). Stored per user.")
        add(None, "Recently used (%d)" % n_recent, ("recent",),
            "Assets you imported lately, newest first.")
        add(None, "Recently added (%d)" % n_added, ("added",),
            "Assets added to the library in the last %d days." % RECENTLY_ADDED_DAYS)
        add(None, "In this scene (%d)" % len(self._usage), ("scene",),
            "Assets with nodes in the open scene. Orange dot: a node uses an older version.")
        if bad:
            add(None, "Errors / missing files (%d)" % bad, ("broken",))
        for r in self._roots():
            root = r["expanded"]
            n_root = counts.get((root, None, ""), 0)
            tip = root
            if r["megascans"]:
                label = r["path"] + "  [Megascans]"
                tip += "\nMegascans download, browsed in place (read-only)."
            else:
                label = r["path"] + ("  [read-only]" if r["read_only"] else "")
                if n_root == 0 and self._looks_like_megascans(root):
                    label += "  (Megascans folder? tick Megascans in Settings)"
                    tip += ("\nThis folder looks like a Megascans download. Library roots "
                            "hold asset.json files; open Settings and tick Megascans for "
                            "this folder to browse it in place.")
            ri = add(None, "%s (%d)" % (label, n_root), ("scope", root, None, ""), tip)
            for t in schema.ASSET_TYPES:
                n = counts.get((root, t, ""), 0)
                if not n:
                    continue
                ti = add(ri, "%s (%d)" % (schema.TYPE_LABELS[t], n), ("scope", root, t, ""))
                nodes = {"": ti}
                for cat in library.categories(self.records, root, t):
                    parent_cat = cat.rsplit("/", 1)[0] if "/" in cat else ""
                    nodes[cat] = add(nodes.get(parent_cat, ti), "%s (%d)" % (
                        cat.rsplit("/", 1)[-1], counts.get((root, t, cat), 0)),
                        ("scope", root, t, cat))
            ri.setExpanded(True)

        # Restore the previous selection when it still exists.
        target = None
        it = QtWidgets.QTreeWidgetItemIterator(self.tree)
        while it.value():
            if it.value().data(0, ScopeRole) == prev:
                target = it.value()
                break
            it += 1
        self.tree.setCurrentItem(target or self.tree.topLevelItem(0))
        if target is not None:
            p = target.parent()
            while p is not None:
                p.setExpanded(True)
                p = p.parent()
        self.tree.blockSignals(False)

    # ---- filtering ---------------------------------------------------------------------------
    def _on_type_toggled(self, _checked):
        self._apply_filter()

    def _on_sort_changed(self, _i):
        self._apply_filter()

    def _on_size_changed(self, value):
        self.view.set_thumb_size(value)

    def _on_list_toggled(self, on):
        self.view.set_list_mode(on)
        self.size_slider.setEnabled(not on)

    def _apply_filter(self):
        scope = self._current_scope() or ("all",)
        types = {t for t, b in self.type_buttons.items() if b.isChecked()}
        favs = _prefs.favorites(self.settings)
        recent = _prefs.recent(self.settings)
        query = library.Query(self.search.text(), favs)
        out = []
        for r in self.records:
            if scope[0] == "broken" and not (r.broken or r.missing):
                continue
            if scope[0] == "favorites" and r.id not in favs:
                continue
            if scope[0] == "recent" and r.id not in recent:
                continue
            if scope[0] == "added" and not self._recently_added(r):
                continue
            if scope[0] == "scene" and r.key not in self._usage:
                continue
            if scope[0] == "scope" and not library.in_scope(r, scope[1], scope[2], scope[3]):
                continue
            if types and r.type not in types:
                continue
            if query.matches(r):
                out.append(r)
        out = library.sort_records(out, self.sort_combo.currentData() or "name")
        if scope[0] == "recent":
            out.sort(key=lambda r: recent.get(r.id, ""), reverse=True)
        self._refresh_marks(favs)

        # Freshly saved/added assets replace the selection; otherwise keep it.
        keep = self._select_after_scan or {r.key for r in self.view.selected_records()}
        self.model.set_records(out)
        self._reselect(keep)
        if self._select_after_scan and not self._scanning:
            self._select_after_scan = set()

        if not self._roots():
            self.empty_label.setText(
                "No library folder set.\nSet the %s environment variable or add a folder in "
                "Settings." % _prefs.ENV_VAR)
            self.stack.setCurrentIndex(1)
        else:
            self.stack.setCurrentIndex(0)
        self._update_status(len(out))

    def _reselect(self, keys):
        sel = self.view.selectionModel()
        sel.blockSignals(True)
        sel.clearSelection()
        first = None
        for key in keys:
            idx = self.model.index_of_key(key)
            if idx.isValid():
                sel.select(idx, QtCore.QItemSelectionModel.SelectionFlag.Select)
                first = first or idx
        sel.blockSignals(False)
        if first is not None:
            self.view.scrollTo(first)
        self._on_selection_changed()

    def _update_status(self, shown=None):
        shown = self.model.rowCount() if shown is None else shown
        parts = ["%d assets" % len(self.records)]
        if shown != len(self.records):
            parts.append("%d shown" % shown)
        if self._scanning:
            parts.append("scanning...")
        rendering = len(thumbnails.karma_queue())
        if rendering:
            parts.append("rendering %d thumbnail(s)" % rendering)
        if self._scan_errors:
            parts.append("%d scan problem(s)" % len(self._scan_errors))
            self.status.setToolTip("\n".join(self._scan_errors[:30]))
        else:
            self.status.setToolTip("")
        self.status.setText("  ·  ".join(parts))

    def _message(self, text, warning=False):
        self.status.setText(text)
        try:
            hou.ui.setStatusMessage("AX Asset Library: " + text, severity=(
                hou.severityType.Warning if warning else hou.severityType.Message))
        except hou.Error:
            pass

    # ---- selection / actions -------------------------------------------------------------
    def _on_selection_changed(self, *_):
        recs = self.view.selected_records()
        rendering = len(recs) == 1 and thumbnails.karma_queue().is_pending(recs[0].key)
        use = self._usage.get(recs[0].key) if len(recs) == 1 else None
        editable = len(recs) == 1 and not self._read_only(recs[0]) and not recs[0].broken
        self.details.set_records(recs, rendering, use, editable)

    def _picked_version(self, recs):
        """Version chosen in the details pane, when it shows exactly these records."""
        rec = self.details._rec
        if len(recs) == 1 and rec is not None and rec.key == recs[0].key:
            return self.details.selected_version()
        return None

    def _drag_payload(self, recs):
        paths, texts = [], []
        version = self._picked_version(recs)
        for r in recs:
            try:
                p, value = importers.file_for(r, self.settings, version)
            except importers.AssetImportError:
                continue
            paths.append(p)
            texts.append(value)
        return paths, "\n".join(texts)

    def _ask_renderer(self):
        """Menu at the cursor: which renderer to build materials for. None = cancelled."""
        menu = QtWidgets.QMenu(self)
        menu.addSection("Build material for")
        last = self.settings.get("material_renderer")
        choices = materials.available_renderers()
        if not choices:
            hou.ui.displayMessage("No Karma, Octane or Redshift material nodes found.",
                                  severity=hou.severityType.Warning, title="AX Asset Library")
            return None
        default = None  # last used renderer, else the first one
        for key, label in choices:
            act = menu.addAction(label)
            act.setData(key)
            if default is None or key == last:
                default = act
        menu.setActiveAction(default)
        chosen = menu.exec(QtGui.QCursor.pos(), default)
        if chosen is None:
            return None
        self.settings.set("material_renderer", chosen.data())
        return chosen.data()

    def _import_selected(self, renderer=None):
        recs = self.view.selected_records()
        if recs:
            self._import_records(recs, renderer)

    def _import_records(self, recs, renderer=None, target=None, position=None):
        """Import `recs` into the current network (or `target`); returns the created nodes."""
        if renderer is None and any(r.type == "material" for r in recs):
            renderer = self._ask_renderer()
            if renderer is None:
                return []
        try:
            nodes, warnings = importers.import_records(
                recs, self.settings, self._picked_version(recs), renderer,
                target=target, position=position)
        except importers.AssetImportError as e:
            hou.ui.displayMessage(str(e), severity=hou.severityType.Warning,
                                  title="AX Asset Library")
            return []
        except hou.Error as e:
            hou.ui.displayMessage("Import failed: %s" % e.instanceMessage(),
                                  severity=hou.severityType.Error, title="AX Asset Library")
            return []
        msg = "Imported %s." % ", ".join(n.path() for n in nodes) if nodes else "Nothing imported."
        if warnings:
            msg += "  " + "  ".join(warnings)
        self._message(msg, warning=bool(warnings))
        if nodes:
            _prefs.add_recent(self.settings, [r.id for r in recs if r.id], schema.now_iso())
            self._refresh_scene()
        return nodes

    def import_dropped(self, keys, editor, position, renderer):
        """Accept dropped asset keys for `editor`'s network. False when none are known.

        The import runs once Houdini has finished the drop: it can open dialogs (renderer
        menu, errors), which must not happen inside the drop dispatch."""
        by_key = {r.key: r for r in self.records}
        recs = [by_key[k] for k in keys if k in by_key]
        if not recs:
            self._message("Dropped assets are no longer in the library list. Rescan and try "
                          "again.", warning=True)
            return False
        network = editor.pwd()
        QtCore.QTimer.singleShot(0, lambda: self._import_records(
            recs, renderer, target=(editor, network), position=position))
        return True

    def _copy_paths(self):
        _paths, text = self._drag_payload(self.view.selected_records())
        if text:
            QtWidgets.QApplication.clipboard().setText(text)
            self._message("Copied %s" % text.replace("\n", ", "))

    def _reveal_selected(self):
        recs = self.view.selected_records()
        if len(recs) != 1:
            return
        rec = recs[0]
        target = rec.primary_file(self.settings.get("geo_format_order")) or rec.sidecar
        try:
            importers.reveal(target)
        except importers.AssetImportError as e:
            self._message(str(e), warning=True)

    def _set_thumbnail(self):
        recs = self.view.selected_records()
        if len(recs) != 1:
            return
        rec = recs[0]
        root = self.settings.root_for(rec.root)
        path, _f = QtWidgets.QFileDialog.getOpenFileName(self, "Thumbnail for %s" % rec.name,
                                                         rec.asset_dir, IMAGE_FILTER)
        if not path:
            return
        try:
            publish.set_thumbnail(rec, path, read_only=bool(root and root["read_only"]))
        except (hou.Error, schema.SidecarError, OSError) as e:
            msg = e.instanceMessage() if isinstance(e, hou.Error) else str(e)
            hou.ui.displayMessage(msg, severity=hou.severityType.Warning, title="AX Asset Library")
            return
        self._select_after_scan = {rec.key}
        self.rescan()

    def _context_menu(self, pos):
        idx = self.view.indexAt(pos)
        if idx.isValid() and not self.view.selectionModel().isSelected(idx):
            self.view.selectionModel().select(
                idx, QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect)
        recs = self.view.selected_records()
        menu = QtWidgets.QMenu(self)
        if recs:
            try:
                _e, _n, ctx = importers.current_target()
                label = "Import into %s" % importers.CONTEXT_LABELS[ctx]
            except importers.AssetImportError:
                label = "Import"
            if any(r.type == "material" for r in recs):
                for key, rlabel in materials.available_renderers():
                    menu.addAction("%s as %s material" % (label, rlabel),
                                   lambda k=key: self._import_selected(k))
            else:
                menu.addAction(label, lambda: self._import_selected())
            menu.addAction("Copy path", self._copy_paths)
            if len(recs) == 1:
                menu.addAction("Show in folder", self._reveal_selected)
                menu.addAction("Thumbnail from image...", self._set_thumbnail)
            if any(r.type in publish.RENDERABLE_TYPES for r in recs):
                menu.addAction("Render thumbnail (Karma)", self._render_selected)
            if len(recs) == 1 and recs[0].type == "geometry":
                menu.addAction("Save selected node as new version",
                               lambda: self._publish(recs[0]))
            menu.addSeparator()
            favs = _prefs.favorites(self.settings)
            all_fav = all(r.id in favs for r in recs if r.id)
            menu.addAction("Remove from favorites" if all_fav else "Add to favorites",
                           lambda: self._set_favorite(recs, not all_fav))
            used = [self._usage[r.key] for r in recs if r.key in self._usage]
            if used:
                menu.addAction("Select nodes in scene", self._scene_select_selected)
                if any(u.outdated for u in used):
                    menu.addAction("Update scene nodes to current version",
                                   self._scene_update_selected)
            editable = [r for r in recs if not self._read_only(r) and not r.broken]
            if editable:
                menu.addSeparator()
                if len(recs) == 1:
                    menu.addAction("Rename...  (F2)", self._rename_selected)
                menu.addAction("Move to...", self._move_selected)
                menu.addAction("Delete (move to trash)  (Del)", self._trash_selected)
            menu.addSeparator()
        menu.addAction("Save selected node to library...", lambda: self._publish())
        menu.addAction("Add files...", self._add_files)
        menu.addAction("Rescan", self.rescan)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    # ---- collections / scene ---------------------------------------------------------------
    def _recently_added(self, rec):
        if not rec.created:
            return False
        import datetime
        try:
            when = datetime.datetime.strptime(rec.created[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return False
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)  # created is UTC
        return (now - when).days < RECENTLY_ADDED_DAYS

    def _on_scope_changed(self, *_):
        scope = self._current_scope()
        if scope and scope[0] == "scene":
            self._refresh_scene(rebuild=False)
        self._apply_filter()

    def _refresh_marks(self, favs=None):
        d = self.view.delegate
        d.favorites = _prefs.favorites(self.settings) if favs is None else favs
        d.scene = {k: bool(u.outdated) for k, u in self._usage.items()}
        self.view.viewport().update()

    def _refresh_scene(self, rebuild=True):
        """Find library assets in the open scene (walks all nodes once)."""
        try:
            self._usage = scene.usage(self.records)
        except hou.Error:
            self._usage = {}
        self._refresh_marks()
        if rebuild:
            self._rebuild_tree()
            self._apply_filter()
        else:
            self._on_selection_changed()

    def _watch_hip(self):
        def on_hip_event(event_type):
            if event_type in (hou.hipFileEventType.AfterLoad, hou.hipFileEventType.AfterClear,
                              hou.hipFileEventType.AfterMerge):
                QtCore.QTimer.singleShot(0, self._refresh_scene)
        try:
            hou.hipFile.addEventCallback(on_hip_event)
            self._hip_callback = on_hip_event
        except (AttributeError, hou.Error):
            self._hip_callback = None

    def _unwatch_hip(self):
        if self._hip_callback is not None:
            try:
                hou.hipFile.removeEventCallback(self._hip_callback)
            except (hou.Error, ValueError):
                pass
            self._hip_callback = None

    def _toggle_favorite_selected(self):
        recs = [r for r in self.view.selected_records() if r.id]
        if recs:
            favs = _prefs.favorites(self.settings)
            self._set_favorite(recs, not all(r.id in favs for r in recs))

    def _set_favorite(self, recs, on):
        _prefs.set_favorite(self.settings, [r.id for r in recs if r.id], on)
        self._rebuild_tree()
        self._apply_filter()

    def _scene_select_selected(self):
        nodes = []
        for r in self.view.selected_records():
            if r.key in self._usage:
                nodes.extend(self._usage[r.key].nodes)
        scene.select_nodes(nodes)

    def _scene_update_selected(self):
        changed, warnings = 0, []
        for r in self.view.selected_records():
            use = self._usage.get(r.key)
            if use is None or not use.outdated:
                continue
            n, w = scene.update_nodes(r, use.outdated, self.settings)
            changed += n
            warnings.extend(w)
        self._refresh_scene()
        msg = "Updated %d node(s) to the current version." % changed
        if warnings:
            msg += "  " + "  ".join(warnings)
        self._message(msg, warning=bool(warnings))

    def _save_meta(self, tags, description):
        recs = self.view.selected_records()
        if len(recs) != 1 or self._read_only(recs[0]):
            return
        try:
            manage.set_meta(recs[0].asset_dir, tags, description)
        except manage.ManageError as e:
            self._error(e)
            return
        self._message("%s: tags saved." % recs[0].name)
        self._select_after_scan = {recs[0].key}
        self.rescan()

    def _relink(self, old_rec, new_dir):
        """Point scene nodes of a renamed/moved asset at its new folder. Returns warnings."""
        use = self._usage.get(old_rec.key)
        if use is None:
            return []
        try:
            new_rec = library.load_record(new_dir, self._root_of(new_dir) or old_rec.root)
        except (OSError, schema.SidecarError) as e:
            return ["%s: scene nodes not relinked (%s)" % (old_rec.name, e)]
        _n, warnings = scene.update_nodes(new_rec, use.nodes, self.settings, "keep")
        return warnings

    def _root_of(self, path):
        p = os.path.normcase(os.path.abspath(path))
        for r in self._roots():
            if p.startswith(os.path.normcase(os.path.abspath(r["expanded"])) + os.sep):
                return r["expanded"]
        return None

    def _rename_selected(self):
        recs = self.view.selected_records()
        if len(recs) != 1 or self._read_only(recs[0]) or recs[0].broken:
            return
        rec = recs[0]
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename %s" % rec.name,
            "New name (renames the asset folder; nodes in this scene are relinked):",
            text=rec.name)
        if not ok or not name.strip() or schema.sanitize_name(name) == rec.name:
            return
        try:
            new_dir = manage.rename(rec.asset_dir, name)
        except manage.ManageError as e:
            self._error(e)
            return
        warnings = self._relink(rec, new_dir)
        self._after_move([(rec, new_dir)], "Renamed to %s." % schema.sanitize_name(name), warnings)

    def _move_selected(self, target=None):
        recs = [r for r in self.view.selected_records() if not self._read_only(r) and not r.broken]
        if not recs:
            return
        if target is None:
            dlg = dialogs.MoveDialog(recs, self.settings, self.records, self)
            if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            root, category = dlg.values()
            if root is None:
                return
            target = (root["expanded"], category)
        dst_root, category = target
        moved, warnings = [], []
        for rec in recs:
            try:
                new_dir = manage.move(rec.asset_dir, rec.type, rec.root, dst_root, category)
            except manage.ManageError as e:
                warnings.append(str(e))
                continue
            if os.path.normcase(new_dir) != os.path.normcase(rec.asset_dir):
                warnings.extend(self._relink(rec, new_dir))
                moved.append((rec, new_dir))
        self._after_move(moved, "Moved %d asset(s)." % len(moved), warnings)

    def _on_assets_dropped(self, keys, scope):
        _s, root, asset_type, category = scope
        by_key = {r.key: r for r in self.records}
        recs = [by_key[k] for k in keys if k in by_key]
        dst = self.settings.root_for(root)
        if not recs or dst is None or dst["read_only"]:
            self._message("Can't move assets into a read-only library root.", warning=True)
            return
        if asset_type is not None and any(r.type != asset_type for r in recs):
            self._message("Assets can only move within their own type (%s)." %
                          schema.TYPE_LABELS.get(asset_type, asset_type), warning=True)
            return
        sel = self.view.selectionModel()
        sel.clearSelection()
        for r in recs:
            idx = self.model.index_of_key(r.key)
            if idx.isValid():
                sel.select(idx, QtCore.QItemSelectionModel.SelectionFlag.Select)
        self._move_selected((dst["expanded"], category))

    def _trash_selected(self):
        recs = [r for r in self.view.selected_records() if not self._read_only(r)]
        if not recs:
            return
        names = ", ".join(r.name for r in recs[:6]) + (" ..." if len(recs) > 6 else "")
        used = sum(len(self._usage[r.key].nodes) for r in recs if r.key in self._usage)
        text = "Move %d asset(s) to the library trash?\n\n%s" % (len(recs), names)
        if used:
            text += ("\n\n%d node(s) in this scene load these assets and will lose their "
                     "files." % used)
        text += "\n\nThe trash is the .trash folder in the library root; delete it there " \
                "to free the space."
        if hou.ui.displayMessage(text, buttons=("Move to trash", "Cancel"), default_choice=1,
                                 close_choice=1, severity=hou.severityType.Warning,
                                 title="AX Asset Library") != 0:
            return
        done, warnings = 0, []
        for rec in recs:
            try:
                manage.trash(rec.asset_dir, rec.root, rec.type)
                done += 1
            except manage.ManageError as e:
                warnings.append(str(e))
        msg = "Moved %d asset(s) to the trash." % done
        if warnings:
            msg += "  " + "  ".join(warnings)
        self._message(msg, warning=bool(warnings))
        self.rescan()

    def _after_move(self, moved, message, warnings):
        self._select_after_scan = {library.norm_path(os.path.join(d, schema.SIDECAR))
                                   for _r, d in moved}
        if warnings:
            message += "  " + "  ".join(warnings)
        self._message(message, warning=bool(warnings))
        self.rescan()

    # ---- versions / thumbnails --------------------------------------------------------------
    def _read_only(self, rec):
        root = self.settings.root_for(rec.root)
        return bool(root and root["read_only"])

    def _error(self, e):
        msg = e.instanceMessage() if isinstance(e, hou.Error) else str(e)
        hou.ui.displayMessage(msg, severity=hou.severityType.Warning, title="AX Asset Library")

    def _set_current_version(self, number):
        recs = self.view.selected_records()
        if len(recs) != 1 or not number:
            return
        try:
            publish.set_current_version(recs[0], number, self._read_only(recs[0]))
        except (hou.Error, schema.SidecarError, OSError) as e:
            self._error(e)
            return
        self._message("%s: %s is now current." % (recs[0].name, schema.version_label(number)))
        self.rescan()

    def _render_selected(self):
        recs = [r for r in self.view.selected_records() if r.type in publish.RENDERABLE_TYPES]
        queued, problems = 0, []
        for rec in recs:
            try:
                if publish.render_record_thumbnail(rec, self.settings):
                    queued += 1
            except (hou.Error, OSError, ImportError) as e:
                msg = e.instanceMessage() if isinstance(e, hou.Error) else str(e)
                problems.append("%s: %s" % (rec.name, msg))
        text = "Rendering %d thumbnail(s)." % queued if queued else "No thumbnails queued."
        if problems:
            text += "  " + "  ".join(problems)
        self._message(text, warning=bool(problems))
        self._on_selection_changed()

    def _on_queue_changed(self):
        self._update_status()
        self._on_selection_changed()

    def _on_render_finished(self, key, ok, message):
        first, _sep, details = message.partition("\n\n")
        self._message(first, warning=not ok)
        if not ok and self.isVisible():
            hou.ui.displayMessage("Thumbnail render failed. " + first,
                                  severity=hou.severityType.Warning, details=details or None,
                                  details_expanded=bool(details), title="AX Asset Library")
        self.rescan()

    # ---- publish -------------------------------------------------------------------------------
    def _publish(self, existing=None):
        node = publish.publishable_node()
        if node is None:
            hou.ui.displayMessage("Select a SOP node or a Geometry object first.",
                                  severity=hou.severityType.Message, title="AX Asset Library")
            return
        if not self.settings.writable_roots():
            hou.ui.displayMessage("Add a library folder that is not read-only in Settings first.",
                                  severity=hou.severityType.Warning, title="AX Asset Library")
            return
        if existing is not None and self._read_only(existing):
            self._error(hou.OperationFailed("%s is in a read-only library root." % existing.name))
            return
        name = existing.name if existing is not None else publish.default_name(node)
        dlg = dialogs.PublishDialog(node.path(), name, self.settings, self.records, existing, self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        s = self.settings
        if existing is None:
            s.set("last_category", v["category"], save=False)
        s.set("publish_formats", v["formats"], save=False)
        s.set("publish_center", v["center"], save=False)
        s.set("publish_ground", v["ground"], save=False)
        s.set("publish_unpack", v["unpack"], save=False)
        s.set("publish_thumbnail", v["thumbnail"])
        try:
            result = publish.publish_node(
                node, v["root"], v["name"], v["category"], v["tags"], v["description"],
                v["note"], v["formats"], v["center"], v["ground"], v["unpack"],
                thumbnail_image=v["image"] or None,
                asset_dir=existing.asset_dir if existing is not None else None)
        except (hou.Error, schema.SidecarError, OSError) as e:
            self._error(e)
            return
        warnings = list(result.warnings)
        if v["thumbnail"] == "karma":
            try:
                publish.render_thumbnail(result.asset_dir, schema.sanitize_name(v["name"]),
                                         result.geo_file, result.bounds, self.settings)
            except (hou.Error, OSError, ImportError) as e:
                warnings.append("thumbnail render not started: %s" % (
                    e.instanceMessage() if isinstance(e, hou.Error) else e))
        self._select_after_scan = {library.norm_path(os.path.join(result.asset_dir,
                                                                  schema.SIDECAR))}
        what = "new asset" if result.created else schema.version_label(result.version)
        msg = "Saved %s (%s)." % (schema.sanitize_name(v["name"]), what)
        if warnings:
            msg += "  " + "  ".join(warnings)
        self._message(msg, warning=bool(warnings))
        self.rescan()

    # ---- adding files ----------------------------------------------------------------------
    def _add_files(self):
        start = self.settings.get("last_ingest_dir") or hou.text.expandString("$HIP")
        paths, _f = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Add to library", start,
            ";;".join((ASSET_FILTER, GEO_FILTER, IMAGE_FILTER, "All files (*)")))
        if paths:
            self.settings.set("last_ingest_dir", os.path.dirname(paths[0]))
            self._ingest(paths)

    def _add_folder(self):
        start = self.settings.get("last_ingest_dir") or hou.text.expandString("$HIP")
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Add folder to library", start)
        if d:
            self.settings.set("last_ingest_dir", d)
            self._ingest([d])

    def _add_megascans(self):
        start = self.settings.get("megascans_dir") or os.environ.get("MEGASCANS", "")
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Megascans library (the Downloaded folder, or any folder inside it)", start)
        if not d:
            return
        self.settings.set("megascans_dir", d)
        plan = publish.plan_ingest([d])
        if not plan.megascans:
            hou.ui.displayMessage(
                "No Megascans assets found in %s.\n\nPick the Bridge download folder "
                "(usually 'Downloaded'): each asset folder in it has an <id>.json file." % d,
                severity=hou.severityType.Message, title="AX Asset Library")
            return
        self._ingest([d], plan)

    def _ingest(self, paths, plan=None):
        if not self.settings.writable_roots():
            hou.ui.displayMessage("Add a library folder that is not read-only in Settings first.",
                                  severity=hou.severityType.Warning, title="AX Asset Library")
            return
        plan = plan or publish.plan_ingest(paths)
        if not plan.items:
            hou.ui.displayMessage(
                "No geometry or image files found.", severity=hou.severityType.Message,
                title="AX Asset Library")
            return
        dlg = dialogs.IngestDialog(plan, self.settings, self.records, self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        self.settings.set("last_category", v["category"], save=False)
        self.settings.set("megascans_copy" if plan.megascans else "copy_on_ingest", v["copy"],
                          save=False)
        self.settings.set("read_geo_info", v["read_info"], save=False)
        self.settings.set("ingest_render", v["render"])
        try:
            report = publish.ingest(plan, v["root"], v["category"], v["tags"],
                                    copy=v["copy"], read_info=v["read_info"], types=v["types"])
        except hou.Error as e:
            hou.ui.displayMessage(e.instanceMessage(), severity=hou.severityType.Error,
                                  title="AX Asset Library")
            return
        self._select_after_scan = {library.norm_path(os.path.join(d, schema.SIDECAR))
                                   for _n, d in report.created}
        if v["render"]:
            publish.render_created(report, self.settings)
        if report.skipped or report.failed or report.warnings:
            dialogs.ReportDialog(report, self).exec()
        self._message("Add to library: " + report.summary,
                      warning=bool(report.failed or report.warnings))
        self.rescan()

    # ---- drops onto the panel ------------------------------------------------------------
    def _external_urls(self, event):
        mime = event.mimeData()
        if event.source() is self.view or mime.hasFormat(grid.ASSET_MIME) or not mime.hasUrls():
            return []
        return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]

    def dragEnterEvent(self, event):
        if self._external_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._external_urls(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._external_urls(event)
        if paths:
            event.acceptProposedAction()
            # Let the drag finish before the modal dialog opens.
            QtCore.QTimer.singleShot(0, lambda: self._ingest(paths))

    # ---- settings / lifecycle -----------------------------------------------------------
    def _open_settings(self):
        if dialogs.SettingsDialog(self.settings, self).exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._load_cached()
            self.rescan()

    def showEvent(self, event):
        super().showEvent(event)
        if time.time() - self._last_scan > RESCAN_AFTER_S and not self._scanning:
            self.rescan()
        elif self.records:
            self._refresh_scene()

    def hideEvent(self, event):
        self._save_state()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._save_state()
        self._unwatch_hip()
        from .. import dragdrop
        dragdrop.remove_panel(self)
        super().closeEvent(event)
