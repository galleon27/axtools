"""Ingest, report and settings dialogs."""
import os

from PySide6 import QtCore, QtGui, QtWidgets

from .. import karma, library, prefs as _prefs, publish, schema

Qt = QtCore.Qt


class IngestDialog(QtWidgets.QDialog):
    """Options for adding files: which asset types, target root, category, tags, copy or
    reference, and Karma thumbnails."""

    def __init__(self, plan, settings, records, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add to library")
        self.settings = settings
        self.records = records
        self.roots = settings.writable_roots()

        lay = QtWidgets.QVBoxLayout(self)
        counts = plan.counts()
        self.type_checks = {}
        box = QtWidgets.QGroupBox("Found")
        grid = QtWidgets.QGridLayout(box)
        for row, t in enumerate(t for t in schema.ASSET_TYPES if counts.get(t)):
            items = plan.by_type(t)
            c = QtWidgets.QCheckBox("%d %s" % (len(items), schema.TYPE_LABELS[t].lower() +
                                               ("" if len(items) == 1 else "s")))
            c.setChecked(True)
            c.setToolTip("Untick to leave these out of this batch.")
            names = ", ".join(i.name for i in items[:6]) + (" ..." if len(items) > 6 else "")
            label = QtWidgets.QLabel(names)
            label.setStyleSheet("color: #9aa0a8;")
            label.setToolTip("\n".join(i.name for i in items[:60]))
            grid.addWidget(c, row, 0)
            grid.addWidget(label, row, 1)
            self.type_checks[t] = c
        grid.setColumnStretch(1, 1)
        lay.addWidget(box)
        if plan.skipped:
            skipped = QtWidgets.QLabel("%d file(s) will be skipped (not geometry or images, or "
                                       "unused previews)." % len(plan.skipped))
            skipped.setStyleSheet("color: #9aa0a8;")
            skipped.setToolTip("\n".join(p for p, _r in plan.skipped[:40]))
            lay.addWidget(skipped)

        form = QtWidgets.QFormLayout()
        self.root_combo = QtWidgets.QComboBox()
        for r in self.roots:
            self.root_combo.addItem(r["path"], r)
            self.root_combo.setItemData(self.root_combo.count() - 1, r["expanded"],
                                        Qt.ItemDataRole.ToolTipRole)
        self.root_combo.setToolTip("Library root the new assets are written to. Read-only roots "
                                   "are not listed.")
        form.addRow("Library", self.root_combo)

        self.cat_combo = QtWidgets.QComboBox()
        self.cat_combo.setEditable(True)
        self.cat_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.cat_combo.setToolTip("Folder under each type folder (geometry/, materials/, hdri/, "
                                  "images/). Use '/' for sub-folders, e.g. trees/oak. Leave "
                                  "empty for none.")
        form.addRow("Category", self.cat_combo)

        self.tags_edit = QtWidgets.QLineEdit()
        self.tags_edit.setPlaceholderText("rock, cliff, scanned")
        self.tags_edit.setToolTip("Comma-separated tags added to every asset in this batch.")
        form.addRow("Tags", self.tags_edit)

        self.copy_check = QtWidgets.QCheckBox("Copy files into the library")
        self.copy_check.setChecked(settings.get("copy_on_ingest"))
        self.copy_check.setToolTip("On: files are copied into the asset folder (v001/).\n"
                                   "Off: the asset points at the files where they are now.")
        form.addRow("", self.copy_check)

        self.info_check = QtWidgets.QCheckBox("Read bounds and point/prim counts")
        self.info_check.setChecked(settings.get("read_geo_info"))
        self.info_check.setToolTip("Loads each geometry file once to store its size and counts. "
                                   "Slower for large files.")
        self.info_check.setVisible(bool(counts.get("geometry")))
        form.addRow("", self.info_check)

        self.render_check = QtWidgets.QCheckBox("Render thumbnails with Karma")
        self.render_check.setChecked(settings.get("ingest_render"))
        self.render_check.setToolTip("Renders in the background: geometry and materials without "
                                     "a preview image get one, HDRIs get a preview with a "
                                     "chrome and a grey sphere.")
        form.addRow("", self.render_check)
        lay.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.ok = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self.ok.setText("Add")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        for c in self.type_checks.values():
            c.toggled.connect(lambda _on: self.ok.setEnabled(bool(self.types())))

        self.root_combo.currentIndexChanged.connect(self._fill_categories)
        self._fill_categories()
        self.cat_combo.setEditText(settings.get("last_category"))
        self.resize(520, self.sizeHint().height())

    def _fill_categories(self):
        root = self.root_combo.currentData()
        text = self.cat_combo.currentText()
        self.cat_combo.clear()
        if root:
            self.cat_combo.addItems(library.categories(self.records, root["expanded"]))
        self.cat_combo.setEditText(text)

    def types(self):
        return {t for t, c in self.type_checks.items() if c.isChecked()}

    def values(self):
        return {
            "root": self.root_combo.currentData(),
            "category": self.cat_combo.currentText().strip(),
            "tags": self.tags_edit.text(),
            "copy": self.copy_check.isChecked(),
            "read_info": self.info_check.isChecked(),
            "render": self.render_check.isChecked(),
            "types": self.types(),
        }


class ReportDialog(QtWidgets.QDialog):
    def __init__(self, report, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add to library: " + report.summary)
        self.resize(760, 420)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel("<b>%s</b>" % report.summary))

        table = QtWidgets.QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["Result", "Item", "Details"])
        hh = table.horizontalHeader()
        hh.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)

        rows = ([("added", n, d, "#5fbf7f") for n, d in report.created] +
                [("skipped", p, r, "#d0a040") for p, r in report.skipped] +
                [("failed", p, r, "#dc463c") for p, r in report.failed] +
                [("warning", "", w, "#d0a040") for w in report.warnings])
        table.setRowCount(len(rows))
        for i, (kind, item, detail, color) in enumerate(rows):
            k = QtWidgets.QTableWidgetItem(kind)
            k.setForeground(QtGui.QColor(color))
            table.setItem(i, 0, k)
            table.setItem(i, 1, QtWidgets.QTableWidgetItem(item))
            table.setItem(i, 2, QtWidgets.QTableWidgetItem(detail))
        lay.addWidget(table, 1)

        btn = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        btn.rejected.connect(self.reject)
        lay.addWidget(btn)


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AX Asset Library settings")
        self.settings = settings
        self.resize(640, 480)
        lay = QtWidgets.QVBoxLayout(self)

        lay.addWidget(QtWidgets.QLabel("<b>Library roots</b>"))
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Path", "Read-only"])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setToolTip("Folders scanned for assets. A path can use variables such as "
                              "$AX_ASSET_LIB; node parms then keep the variable.\n"
                              "Read-only roots are browsed but never written to.")
        lay.addWidget(self.table, 1)
        roots = [r for r in settings.get("roots") if isinstance(r, dict)]
        if not roots and os.environ.get(_prefs.ENV_VAR):
            roots = [{"path": "$" + _prefs.ENV_VAR, "read_only": False}]
        for r in roots:
            self._add_row(r.get("path", ""), r.get("read_only", False))

        row = QtWidgets.QHBoxLayout()
        b_add = QtWidgets.QPushButton("Add folder...")
        b_add.setToolTip("Pick a folder to add as a library root.")
        b_var = QtWidgets.QPushButton("Add $%s" % _prefs.ENV_VAR)
        b_var.setToolTip("Add the root from the %s environment variable." % _prefs.ENV_VAR)
        b_del = QtWidgets.QPushButton("Remove")
        b_del.setToolTip("Remove the selected roots from the list. Files are not touched.")
        b_add.clicked.connect(self._browse)
        b_var.clicked.connect(lambda: self._add_row("$" + _prefs.ENV_VAR, False))
        b_del.clicked.connect(self._remove)
        row.addWidget(b_add)
        row.addWidget(b_var)
        row.addWidget(b_del)
        row.addStretch()
        lay.addLayout(row)

        form = QtWidgets.QFormLayout()
        self.path_mode = QtWidgets.QComboBox()
        self.path_mode.addItem("Keep root variable ($AX_ASSET_LIB/...)", "token")
        self.path_mode.addItem("Absolute path", "absolute")
        self.path_mode.setCurrentIndex(max(0, self.path_mode.findData(settings.get("path_mode"))))
        self.path_mode.setToolTip("How file paths are written into imported nodes. Keeping the "
                                  "variable lets the .hip open on machines where the library "
                                  "is in another place.")
        form.addRow("Paths in nodes", self.path_mode)

        self.formats = QtWidgets.QLineEdit(", ".join(settings.get("geo_format_order")))
        self.formats.setToolTip("When an asset has several formats, the first one in this list "
                                "is imported.")
        form.addRow("Format order", self.formats)

        self.renderer = QtWidgets.QComboBox()
        for key, label in karma.RENDERERS:
            self.renderer.addItem(label, key)
        idx = self.renderer.findData(settings.get("thumb_renderer"))
        if idx < 0:
            self.renderer.addItem(settings.get("thumb_renderer"), settings.get("thumb_renderer"))
            idx = self.renderer.count() - 1
        self.renderer.setCurrentIndex(idx)
        self.renderer.setToolTip("Karma engine used by husk for thumbnail renders.")
        form.addRow("Thumbnail renderer", self.renderer)

        self.samples = QtWidgets.QSpinBox()
        self.samples.setRange(1, 1024)
        self.samples.setValue(settings.get("thumb_samples"))
        self.samples.setToolTip("Path-traced samples per pixel for thumbnail renders. "
                                "More samples: less noise, slower renders.")
        form.addRow("Thumbnail samples", self.samples)

        self.resolution = QtWidgets.QSpinBox()
        self.resolution.setRange(64, 2048)
        self.resolution.setSingleStep(64)
        self.resolution.setValue(settings.get("thumb_resolution"))
        self.resolution.setToolTip("Rendered size in pixels. The stored thumbnail is at most "
                                   "512 x 512.")
        form.addRow("Render size", self.resolution)
        lay.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _add_row(self, path, read_only):
        r = self.table.rowCount()
        self.table.insertRow(r)
        item = QtWidgets.QTableWidgetItem(path)
        item.setToolTip(_prefs.expand(path))
        self.table.setItem(r, 0, item)
        chk = QtWidgets.QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Checked if read_only else Qt.CheckState.Unchecked)
        self.table.setItem(r, 1, chk)

    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Library root")
        if d:
            self._add_row(d.replace("\\", "/"), False)

    def _remove(self):
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def _save(self):
        roots = []
        for r in range(self.table.rowCount()):
            path = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            if path:
                ro = self.table.item(r, 1).checkState() == Qt.CheckState.Checked
                roots.append({"path": path, "read_only": ro})
        order = [f.strip().lstrip(".").lower() for f in self.formats.text().split(",") if f.strip()]
        self.settings.set("roots", roots, save=False)
        self.settings.set("path_mode", self.path_mode.currentData(), save=False)
        self.settings.set("geo_format_order", order or list(_prefs.GEO_FORMAT_ORDER), save=False)
        self.settings.set("thumb_renderer", self.renderer.currentData(), save=False)
        self.settings.set("thumb_samples", self.samples.value(), save=False)
        self.settings.set("thumb_resolution", self.resolution.value(), save=False)
        self.settings.save()
        self.accept()


class PublishDialog(QtWidgets.QDialog):
    """Options for saving a node's geometry as a new asset or a new version of one."""

    def __init__(self, node_path, name, settings, records, existing=None, parent=None):
        """existing: AssetRecord to add a version to (name, category and root locked)."""
        super().__init__(parent)
        self.settings = settings
        self.records = records
        self.existing = existing
        self.roots = settings.writable_roots()
        self.setWindowTitle("New version of %s" % existing.name if existing
                            else "Save to library")

        lay = QtWidgets.QVBoxLayout(self)
        src = QtWidgets.QLabel("Geometry from <b>%s</b>" % node_path)
        src.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(src)

        form = QtWidgets.QFormLayout()
        self.root_combo = QtWidgets.QComboBox()
        for r in self.roots:
            self.root_combo.addItem(r["path"], r)
        self.root_combo.setToolTip("Library root the asset is written to.")
        form.addRow("Library", self.root_combo)

        self.cat_combo = QtWidgets.QComboBox()
        self.cat_combo.setEditable(True)
        self.cat_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.cat_combo.setToolTip("Folder under geometry/. Use '/' for sub-folders.")
        form.addRow("Category", self.cat_combo)

        self.name_edit = QtWidgets.QLineEdit(name)
        self.name_edit.setToolTip("Asset and folder name. Spaces and special characters "
                                  "become '_'. An existing name in the same category saves "
                                  "a new version of that asset.")
        form.addRow("Name", self.name_edit)

        self.status = QtWidgets.QLabel()
        form.addRow("", self.status)

        self.tags_edit = QtWidgets.QLineEdit()
        self.tags_edit.setPlaceholderText("rock, cliff")
        self.tags_edit.setToolTip("Comma-separated tags. For a new version they are added to "
                                  "the existing tags.")
        form.addRow("Tags", self.tags_edit)

        self.desc_edit = QtWidgets.QLineEdit()
        self.desc_edit.setToolTip("Short description. For a new version, leave empty to keep "
                                  "the current one.")
        form.addRow("Description", self.desc_edit)

        self.note_edit = QtWidgets.QLineEdit()
        self.note_edit.setPlaceholderText("what changed in this version")
        self.note_edit.setToolTip("Note stored with this version, shown in the version list.")
        form.addRow("Version note", self.note_edit)

        fmt_row = QtWidgets.QHBoxLayout()
        self.fmt_checks = {}
        for fmt in publish.PUBLISH_FORMATS:
            c = QtWidgets.QCheckBox(fmt)
            c.setChecked(fmt == "bgeo.sc" or fmt in settings.get("publish_formats"))
            if fmt == "bgeo.sc":
                c.setEnabled(False)
                c.setToolTip("Always written; used for import and thumbnails.")
            else:
                c.setToolTip("Also write a .%s file. If Houdini can't write it, the asset is "
                             "saved without it and a warning is shown." % fmt)
            fmt_row.addWidget(c)
            self.fmt_checks[fmt] = c
        fmt_row.addStretch()
        form.addRow("Formats", fmt_row)

        self.center_check = QtWidgets.QCheckBox("Center on X and Z")
        self.center_check.setChecked(settings.get("publish_center"))
        self.center_check.setToolTip("Move the bounding box center to the origin on X and Z.")
        self.ground_check = QtWidgets.QCheckBox("Move to ground (Y = 0)")
        self.ground_check.setChecked(settings.get("publish_ground"))
        self.ground_check.setToolTip("Move the geometry up or down so its lowest point is at Y = 0.")
        self.unpack_check = QtWidgets.QCheckBox("Unpack packed primitives")
        self.unpack_check.setChecked(settings.get("publish_unpack"))
        self.unpack_check.setToolTip("Unpack packed primitives (and packed disk files) before "
                                     "saving, so the asset holds plain geometry.")
        opts = QtWidgets.QVBoxLayout()
        for c in (self.center_check, self.ground_check, self.unpack_check):
            opts.addWidget(c)
        form.addRow("Options", opts)

        self.thumb_karma = QtWidgets.QRadioButton("Render with Karma")
        self.thumb_karma.setToolTip("Render a thumbnail in the background after saving.")
        self.thumb_image = QtWidgets.QRadioButton("Image file")
        self.thumb_image.setToolTip("Use an existing image, cropped to a square.")
        self.thumb_none = QtWidgets.QRadioButton("None")
        self.thumb_none.setToolTip("No new thumbnail. A new version keeps the current one.")
        self.image_edit = QtWidgets.QLineEdit()
        self.image_edit.setToolTip("Image file for the thumbnail.")
        browse = QtWidgets.QPushButton("...")
        browse.setFixedWidth(28)
        browse.clicked.connect(self._browse_image)
        img_row = QtWidgets.QHBoxLayout()
        img_row.addWidget(self.thumb_image)
        img_row.addWidget(self.image_edit, 1)
        img_row.addWidget(browse)
        thumbs = QtWidgets.QVBoxLayout()
        thumbs.addWidget(self.thumb_karma)
        thumbs.addLayout(img_row)
        thumbs.addWidget(self.thumb_none)
        mode = settings.get("publish_thumbnail")
        {"image": self.thumb_image, "none": self.thumb_none}.get(mode, self.thumb_karma).setChecked(True)
        form.addRow("Thumbnail", thumbs)
        lay.addLayout(form)

        self.buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                                  QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

        if existing is not None:
            root = settings.root_for(existing.root)
            idx = self.root_combo.findText(root["path"]) if root else -1
            self.root_combo.setCurrentIndex(max(0, idx))
            self.cat_combo.setEditText(existing.category)
            for w in (self.root_combo, self.cat_combo, self.name_edit):
                w.setEnabled(False)
        else:
            self.root_combo.currentIndexChanged.connect(self._fill_categories)
            self._fill_categories()
            self.cat_combo.setEditText(settings.get("last_category"))
        self.cat_combo.editTextChanged.connect(self._update_status)
        self.name_edit.textChanged.connect(self._update_status)
        self.root_combo.currentIndexChanged.connect(self._update_status)
        self._update_status()
        self.resize(520, self.sizeHint().height())

    def _fill_categories(self):
        root = self.root_combo.currentData()
        text = self.cat_combo.currentText()
        self.cat_combo.blockSignals(True)
        self.cat_combo.clear()
        if root:
            self.cat_combo.addItems(library.categories(self.records, root["expanded"], "geometry"))
        self.cat_combo.setEditText(text)
        self.cat_combo.blockSignals(False)

    def _browse_image(self):
        start = os.path.dirname(self.image_edit.text()) or self.settings.get("last_ingest_dir")
        path, _f = QtWidgets.QFileDialog.getOpenFileName(
            self, "Thumbnail image", start,
            "Images (%s)" % " ".join("*" + e for e in sorted(schema.IMAGE_EXTS)))
        if path:
            self.image_edit.setText(path)
            self.thumb_image.setChecked(True)

    def _update_status(self, *_):
        ok = self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        root = self.root_combo.currentData()
        name = schema.sanitize_name(self.name_edit.text()) if self.name_edit.text().strip() else ""
        if not root or not name:
            self.status.setText("<span style='color:#dc463c'>Choose a library and a name.</span>")
            ok.setEnabled(False)
            return
        ok.setEnabled(True)
        if self.existing is not None:
            try:
                data = schema.load(self.existing.sidecar)
            except schema.SidecarError:
                data = None
        else:
            data = publish.existing_asset(root, self.cat_combo.currentText(), name)
        if data is None:
            self.status.setText("New asset <b>%s</b>" % name)
            ok.setText("Save")
        elif data.get("type") != "geometry":
            self.status.setText("<span style='color:#dc463c'>%s exists as a %s asset.</span>"
                                % (name, data.get("type")))
            ok.setEnabled(False)
        else:
            self.status.setText("<b>%s</b> exists: saves %s" % (
                name, schema.version_label(schema.next_version(data))))
            ok.setText("Save version")

    def _accept(self):
        if self.thumb_image.isChecked() and not os.path.isfile(self.image_edit.text()):
            QtWidgets.QMessageBox.warning(self, self.windowTitle(), "Pick an existing image file.")
            return
        self.accept()

    def values(self):
        thumb = "karma" if self.thumb_karma.isChecked() else (
            "image" if self.thumb_image.isChecked() else "none")
        return {
            "root": self.root_combo.currentData(),
            "category": self.cat_combo.currentText().strip(),
            "name": self.name_edit.text().strip(),
            "tags": self.tags_edit.text(),
            "description": self.desc_edit.text().strip(),
            "note": self.note_edit.text().strip(),
            "formats": [f for f, c in self.fmt_checks.items() if c.isChecked()],
            "center": self.center_check.isChecked(),
            "ground": self.ground_check.isChecked(),
            "unpack": self.unpack_check.isChecked(),
            "thumbnail": thumb,
            "image": self.image_edit.text() if thumb == "image" else "",
        }


class MoveDialog(QtWidgets.QDialog):
    """Pick a library root and category to move assets to."""

    def __init__(self, records, settings, all_records, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Move %s" % (records[0].name if len(records) == 1
                                         else "%d assets" % len(records)))
        self.all_records = all_records
        self.types = {r.type for r in records}
        lay = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.root_combo = QtWidgets.QComboBox()
        for r in settings.writable_roots():
            self.root_combo.addItem(r["path"], r)
        idx = self.root_combo.findText((settings.root_for(records[0].root) or {}).get("path", ""))
        self.root_combo.setCurrentIndex(max(0, idx))
        self.root_combo.setToolTip("Library root to move to. Read-only roots are not listed.")
        form.addRow("Library", self.root_combo)
        self.cat_combo = QtWidgets.QComboBox()
        self.cat_combo.setEditable(True)
        self.cat_combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.cat_combo.setToolTip("Category folder. Use '/' for sub-folders; leave empty for "
                                  "none. Assets stay under their own type folder.")
        form.addRow("Category", self.cat_combo)
        lay.addLayout(form)
        note = QtWidgets.QLabel("Nodes in this scene that load these assets are relinked. "
                                "Other .hip files that load them by path need relinking "
                                "(for example with the Asset Manager).")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9aa0a8;")
        lay.addWidget(note)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Move")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.root_combo.currentIndexChanged.connect(self._fill)
        self._fill()
        self.cat_combo.setEditText(records[0].category)
        self.resize(460, self.sizeHint().height())

    def _fill(self):
        root = self.root_combo.currentData()
        text = self.cat_combo.currentText()
        self.cat_combo.clear()
        if root:
            cats = set()
            for t in self.types:
                cats.update(library.categories(self.all_records, root["expanded"], t))
            self.cat_combo.addItems(sorted(cats, key=str.lower))
        self.cat_combo.setEditText(text)

    def values(self):
        return self.root_combo.currentData(), self.cat_combo.currentText().strip()