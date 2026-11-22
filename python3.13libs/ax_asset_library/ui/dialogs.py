"""Ingest, report and settings dialogs."""
import os

from PySide6 import QtCore, QtGui, QtWidgets

from .. import library, prefs as _prefs

Qt = QtCore.Qt


class IngestDialog(QtWidgets.QDialog):
    """Options for adding geometry files: target root, category, tags, copy or reference."""

    def __init__(self, groups, settings, records, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add geometry to library")
        self.settings = settings
        self.records = records
        self.roots = settings.writable_roots()

        lay = QtWidgets.QVBoxLayout(self)
        names = [g[0] for g in groups]
        head = QtWidgets.QLabel("<b>%d asset(s)</b>: %s%s" % (
            len(names), ", ".join(names[:8]), " ..." if len(names) > 8 else ""))
        head.setWordWrap(True)
        lay.addWidget(head)

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
        self.cat_combo.setToolTip("Folder under geometry/. Use '/' for sub-folders, "
                                  "e.g. trees/oak. Leave empty for none.")
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
        self.info_check.setToolTip("Loads each file once to store its size and counts. "
                                   "Slower for large files.")
        form.addRow("", self.info_check)
        lay.addLayout(form)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok |
                                             QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok).setText("Add")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

        self.root_combo.currentIndexChanged.connect(self._fill_categories)
        self._fill_categories()
        self.cat_combo.setEditText(settings.get("last_category"))
        self.resize(460, self.sizeHint().height())

    def _fill_categories(self):
        root = self.root_combo.currentData()
        text = self.cat_combo.currentText()
        self.cat_combo.clear()
        if root:
            self.cat_combo.addItems(library.categories(self.records, root["expanded"], "geometry"))
        self.cat_combo.setEditText(text)

    def values(self):
        return {
            "root": self.root_combo.currentData(),
            "category": self.cat_combo.currentText().strip(),
            "tags": self.tags_edit.text(),
            "copy": self.copy_check.isChecked(),
            "read_info": self.info_check.isChecked(),
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
        self.resize(640, 380)
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
        self.settings.save()
        self.accept()
