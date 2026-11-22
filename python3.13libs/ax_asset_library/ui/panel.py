"""AX Asset Library main panel."""
import os
import time

import hou
from PySide6 import QtCore, QtGui, QtWidgets

from .. import importers, library, materials, prefs as _prefs, publish, schema, thumbnails
from . import details, dialogs, grid

Qt = QtCore.Qt
ScopeRole = Qt.ItemDataRole.UserRole + 10
RESCAN_AFTER_S = 60  # rescan when the panel is shown again after this long

SORT_MODES = (("name", "Name"), ("added", "Newest"), ("modified", "Recently changed"))
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


class _ScanJob(QtCore.QRunnable):
    """Runs library.scan_roots in a worker thread. No hou calls here."""

    def __init__(self, generation, roots, cache_dir, use_cache, signals):
        super().__init__()
        self.generation, self.roots, self.cache_dir = generation, roots, cache_dir
        self.use_cache, self.signals = use_cache, signals

    def run(self):
        try:
            records, errors = library.scan_roots(self.roots, self.cache_dir, self.use_cache)
        except Exception as e:  # keep the panel alive whatever the file system does
            records, errors = [], ["scan failed: %s" % e]
        self.signals.finished.emit(self.generation, records, errors)


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
        self._pool = QtCore.QThreadPool()
        self._pool.setMaxThreadCount(1)
        self._scan_signals = _ScanSignals()
        self._scan_signals.finished.connect(self._on_scan_finished)

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
        self.btn_add.setToolTip("Add geometry files to the library. You can also drop files or "
                                "folders onto the panel.")
        self.btn_add.clicked.connect(self._add_files)
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

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(140)
        self.tree.currentItemChanged.connect(lambda *_: self._apply_filter())
        self.splitter.addWidget(self.tree)

        self.stack = QtWidgets.QStackedWidget()
        self.model = grid.AssetModel(self._drag_payload, self)
        self.view = grid.AssetView()
        self.view.setModel(self.model)
        self.view.import_requested.connect(self._import_selected)
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
        s.set("sort", self.sort_combo.currentData(), save=False)
        s.set("type_filter", [t for t, b in self.type_buttons.items() if b.isChecked()], save=False)
        s.set("splitter", self.splitter.sizes(), save=False)
        s.save()

    # ---- scanning ----------------------------------------------------------------------------
    def _roots(self):
        return self.settings.roots()

    def _load_cached(self):
        """Show what the index cache knows straight away; the real scan follows."""
        cache = _prefs.cache_dir()
        recs = []
        for r in self._roots():
            recs.extend(library.records_from_index(cache, r["expanded"]))
        self._set_records(recs)

    def rescan(self, use_cache=True):
        roots = [r["expanded"] for r in self._roots()]
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
        self._set_records(records)

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
        if bad:
            add(None, "Errors / missing files (%d)" % bad, ("broken",))
        for r in self._roots():
            root = r["expanded"]
            label = r["path"] + ("  [read-only]" if r["read_only"] else "")
            ri = add(None, "%s (%d)" % (label, counts.get((root, None, ""), 0)),
                     ("scope", root, None, ""), root)
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

    def _apply_filter(self):
        scope = self._current_scope() or ("all",)
        types = {t for t, b in self.type_buttons.items() if b.isChecked()}
        query = library.Query(self.search.text())
        out = []
        for r in self.records:
            if scope[0] == "broken" and not (r.broken or r.missing):
                continue
            if scope[0] == "scope" and not library.in_scope(r, scope[1], scope[2], scope[3]):
                continue
            if types and r.type not in types:
                continue
            if query.matches(r):
                out.append(r)
        out = library.sort_records(out, self.sort_combo.currentData() or "name")

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
        rendering = len(recs) == 1 and thumbnails.karma_queue().is_pending(recs[0].asset_dir)
        self.details.set_records(recs, rendering)

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
        if not recs:
            return
        if renderer is None and any(r.type == "material" for r in recs):
            renderer = self._ask_renderer()
            if renderer is None:
                return
        try:
            nodes, warnings = importers.import_records(recs, self.settings,
                                                       self._picked_version(recs), renderer)
        except importers.AssetImportError as e:
            hou.ui.displayMessage(str(e), severity=hou.severityType.Warning,
                                  title="AX Asset Library")
            return
        except hou.Error as e:
            hou.ui.displayMessage("Import failed: %s" % e.instanceMessage(),
                                  severity=hou.severityType.Error, title="AX Asset Library")
            return
        msg = "Imported %s." % ", ".join(n.path() for n in nodes) if nodes else "Nothing imported."
        if warnings:
            msg += "  " + "  ".join(warnings)
        self._message(msg, warning=bool(warnings))

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
        menu.addAction("Save selected node to library...", lambda: self._publish())
        menu.addAction("Add files...", self._add_files)
        menu.addAction("Rescan", self.rescan)
        menu.exec(self.view.viewport().mapToGlobal(pos))

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

    def _on_render_finished(self, asset_dir, ok, message):
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

    def _ingest(self, paths):
        if not self.settings.writable_roots():
            hou.ui.displayMessage("Add a library folder that is not read-only in Settings first.",
                                  severity=hou.severityType.Warning, title="AX Asset Library")
            return
        plan = publish.plan_ingest(paths)
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
        self.settings.set("copy_on_ingest", v["copy"], save=False)
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

    def hideEvent(self, event):
        self._save_state()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._save_state()
        super().closeEvent(event)
