"""Details pane: preview, metadata and actions for the selected asset(s)."""
import html
import os

from PySide6 import QtCore, QtGui, QtWidgets

from .. import schema, thumbnails, variants
from ..schema import RENDERABLE_TYPES

Qt = QtCore.Qt


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return ""


def _fmt_bounds(b):
    try:
        lo, hi = b
        return " x ".join("%.3g" % (hi[i] - lo[i]) for i in range(3))
    except (TypeError, ValueError, IndexError):
        return ""


def _fmt_date(iso):
    return iso.replace("T", " ").rstrip("Z")[:16] if iso else ""


class Preview(QtWidgets.QLabel):
    """Square preview that scales with the pane width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Preferred)
        self.setStyleSheet("background: #1e1e1e; border: 1px solid #000;")
        self._pix = None

    def set_image(self, path):
        self._pix = None
        if path and os.path.isfile(path):
            try:
                img = thumbnails.display_image(path)   # cached copy for big files and EXR/HDR/RAT
            except (thumbnails.ThumbnailError, OSError):
                img = QtGui.QImage()
            if not img.isNull():
                self._pix = QtGui.QPixmap.fromImage(img)
        self._update()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return min(w, 512)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update()

    def _update(self):
        if self._pix is None:
            self.setPixmap(QtGui.QPixmap())
            self.setText("No thumbnail")
            return
        side = min(self.width(), self.height(), 512)
        self.setPixmap(self._pix.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))


class DetailsPane(QtWidgets.QWidget):
    import_requested = QtCore.Signal()
    copy_requested = QtCore.Signal()
    reveal_requested = QtCore.Signal()
    thumbnail_requested = QtCore.Signal()
    render_requested = QtCore.Signal()
    set_current_requested = QtCore.Signal(int)
    meta_save_requested = QtCore.Signal(str, str)   # tags, description
    scene_update_requested = QtCore.Signal()
    scene_select_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.labels = {}   # asset id -> color label key (set by the panel)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        self.preview = Preview()
        lay.addWidget(self.preview)

        self.title = QtWidgets.QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.title.setFont(f)
        self.title.setWordWrap(True)
        self.title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.title)

        self.subtitle = QtWidgets.QLabel()
        self.subtitle.setStyleSheet("color: #9aa0a8;")
        self.subtitle.setWordWrap(True)
        lay.addWidget(self.subtitle)

        self.version_box = QtWidgets.QWidget()
        vrow = QtWidgets.QHBoxLayout(self.version_box)
        vrow.setContentsMargins(0, 0, 0, 0)
        vrow.addWidget(QtWidgets.QLabel("Version"))
        self.version_combo = QtWidgets.QComboBox()
        self.version_combo.setToolTip("Version used by Import, Copy path and drag. "
                                      "The current version is the default for new imports.")
        vrow.addWidget(self.version_combo, 1)
        self.btn_current = QtWidgets.QPushButton("Make current")
        self.btn_current.setToolTip("Make this version the default for imports. "
                                    "Nodes already in scenes keep the version they load.")
        vrow.addWidget(self.btn_current)
        lay.addWidget(self.version_box)
        self.version_note = QtWidgets.QLabel()
        self.version_note.setWordWrap(True)
        self.version_note.setStyleSheet("color: #9aa0a8;")
        lay.addWidget(self.version_note)
        self.version_combo.currentIndexChanged.connect(self._on_version_changed)
        self.btn_current.clicked.connect(
            lambda: self.set_current_requested.emit(self.selected_version() or 0))

        # In the open scene
        self.scene_box = QtWidgets.QWidget()
        srow = QtWidgets.QHBoxLayout(self.scene_box)
        srow.setContentsMargins(0, 0, 0, 0)
        self.scene_label = QtWidgets.QLabel()
        self.scene_label.setWordWrap(True)
        srow.addWidget(self.scene_label, 1)
        self.btn_scene_select = QtWidgets.QPushButton("Select")
        self.btn_scene_select.setToolTip("Select the nodes in this scene that load this asset.")
        self.btn_scene_update = QtWidgets.QPushButton("Update")
        self.btn_scene_update.setToolTip("Point the nodes in this scene at the current version.")
        srow.addWidget(self.btn_scene_select)
        srow.addWidget(self.btn_scene_update)
        lay.addWidget(self.scene_box)
        self.btn_scene_select.clicked.connect(self.scene_select_requested)
        self.btn_scene_update.clicked.connect(self.scene_update_requested)

        # Editable metadata
        self.meta_box = QtWidgets.QWidget()
        form = QtWidgets.QFormLayout(self.meta_box)
        form.setContentsMargins(0, 0, 0, 0)
        self.tags_edit = QtWidgets.QLineEdit()
        self.tags_edit.setPlaceholderText("rock, cliff")
        self.tags_edit.setToolTip("Comma-separated tags. Search them with tag:<name>.")
        self.desc_edit = QtWidgets.QPlainTextEdit()
        self.desc_edit.setPlaceholderText("Description")
        self.desc_edit.setFixedHeight(48)
        self.desc_edit.setToolTip("Free text, found by the search field.")
        self.btn_meta_save = QtWidgets.QPushButton("Save tags")
        self.btn_meta_save.setToolTip("Write tags and description to the asset's asset.json.")
        form.addRow("Tags", self.tags_edit)
        form.addRow("Notes", self.desc_edit)
        form.addRow("", self.btn_meta_save)
        lay.addWidget(self.meta_box)
        self.tags_edit.textChanged.connect(self._meta_changed)
        self.desc_edit.textChanged.connect(self._meta_changed)
        self.tags_edit.returnPressed.connect(self._save_meta)
        self.btn_meta_save.clicked.connect(self._save_meta)

        self.body = QtWidgets.QLabel()
        self.body.setWordWrap(True)
        self.body.setTextFormat(Qt.TextFormat.RichText)
        self.body.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidget(self.body)
        lay.addWidget(scroll, 1)

        grid = QtWidgets.QGridLayout()
        grid.setSpacing(4)
        self.btn_import = QtWidgets.QPushButton("Import")
        self.btn_import.setToolTip("Load the asset into the network shown in the network editor "
                                   "(SOP, OBJ or LOP). Same as double-click or Enter.")
        self.btn_copy = QtWidgets.QPushButton("Copy path")
        self.btn_copy.setToolTip("Copy the file path as it would be written into a node.")
        self.btn_reveal = QtWidgets.QPushButton("Show in folder")
        self.btn_reveal.setToolTip("Open the asset folder in the file browser.")
        self.btn_thumb = QtWidgets.QPushButton("Thumbnail from image...")
        self.btn_thumb.setToolTip("Pick an image file; it is cropped to a square and saved as "
                                  "the asset's thumbnail.")
        self.btn_render = QtWidgets.QPushButton("Render thumbnail")
        self.btn_render.setToolTip("Render the thumbnail with Karma in the background "
                                   "(husk). Renderer and samples are set in Settings.")
        grid.addWidget(self.btn_import, 0, 0, 1, 2)
        grid.addWidget(self.btn_copy, 1, 0)
        grid.addWidget(self.btn_reveal, 1, 1)
        grid.addWidget(self.btn_thumb, 2, 0)
        grid.addWidget(self.btn_render, 2, 1)
        lay.addLayout(grid)

        self.btn_import.clicked.connect(self.import_requested)
        self.btn_copy.clicked.connect(self.copy_requested)
        self.btn_reveal.clicked.connect(self.reveal_requested)
        self.btn_thumb.clicked.connect(self.thumbnail_requested)
        self.btn_render.clicked.connect(self.render_requested)
        self._rec = None
        self._editable = True
        self.set_records([])

    def selected_version(self):
        """Version picked in the combo (single selection), else None."""
        if self._rec is None or self.version_box.isHidden():
            return None
        return self.version_combo.currentData()

    def _fill_versions(self, rec):
        self.version_combo.blockSignals(True)
        self.version_combo.clear()
        for v in reversed(rec.version_list):
            label = schema.version_label(v["version"])
            if v["version"] == rec.current:
                label += " (current)"
            self.version_combo.addItem(label, v["version"])
        idx = self.version_combo.findData(rec.current)
        self.version_combo.setCurrentIndex(max(0, idx))
        self.version_combo.blockSignals(False)
        self.version_box.setVisible(bool(rec.version_list))
        self._on_version_changed()

    def _on_version_changed(self, *_):
        rec = self._rec
        number = self.version_combo.currentData()
        v = rec.version(number) if rec is not None and number is not None else None
        self.btn_current.setEnabled(v is not None and number != rec.current)
        note = []
        if v:
            if v.get("created"):
                note.append(_fmt_date(v["created"]))
            if v.get("note"):
                note.append(html.escape(v["note"]))
            if v.get("author"):
                note.append("by " + html.escape(v["author"]))
        self.version_note.setText(" · ".join(note))
        self.version_note.setVisible(bool(note))

    def set_rendering(self, rendering):
        self.btn_render.setText("Rendering..." if rendering else "Render thumbnail")
        self.btn_render.setEnabled(not rendering and self._rec is not None
                                   and self._rec.type in RENDERABLE_TYPES)

    # ---- metadata / scene -------------------------------------------------------------------
    def _meta_values(self):
        return self.tags_edit.text(), self.desc_edit.toPlainText().strip()

    def _meta_changed(self, *_):
        rec = self._rec
        if rec is None:
            self.btn_meta_save.setEnabled(False)
            return
        tags, desc = self._meta_values()
        dirty = schema.clean_tags(tags) != list(rec.tags) or desc != (rec.description or "")
        self.btn_meta_save.setEnabled(dirty and self._editable)

    def _save_meta(self):
        if self.btn_meta_save.isEnabled():
            self.meta_save_requested.emit(*self._meta_values())

    def _fill_meta(self, rec, editable, vendor=False):
        self._editable = editable
        for w in (self.tags_edit, self.desc_edit):
            w.blockSignals(True)
        self.tags_edit.setText(", ".join(rec.tags))
        self.desc_edit.setPlainText(rec.description or "")
        for w in (self.tags_edit, self.desc_edit):
            w.blockSignals(False)
            w.setReadOnly(not editable)
        if not editable:
            tip = "This asset is in a read-only library root."
        elif vendor:
            tip = "Tags and description are stored in your preferences, not in the folder."
        else:
            tip = "Write tags and description to the asset's asset.json."
        self.btn_meta_save.setToolTip(tip)
        self._meta_changed()

    def _fill_scene(self, rec, use):
        if use is None or not use.nodes:
            self.scene_box.hide()
            return
        outdated = len(use.outdated)
        text = "In this scene: " + use.summary()
        if outdated:
            text += " <span style='color:#e0a030'>(%d older than %s)</span>" % (
                outdated, schema.version_label(rec.current))
        self.scene_label.setText(text)
        self.btn_scene_update.setEnabled(outdated > 0)
        self.btn_scene_update.setText("Update to %s" % schema.version_label(rec.current))
        self.scene_box.show()

    def set_records(self, records, rendering=False, usage=None, editable=True,
                    vendor=False):
        """usage: scene.Usage of the single selected record, or None if not in the scene.

        vendor: a read-only vendor root, whose tags live in the user's prefs."""
        n = len(records)
        self._rec = records[0] if n == 1 else None
        self.meta_box.setVisible(n == 1)
        if n == 1:
            self._fill_meta(records[0], editable, vendor)
            self._fill_scene(records[0], usage)
        else:
            self.scene_box.hide()
        for b in (self.btn_import, self.btn_copy):
            b.setEnabled(n > 0)
        for b in (self.btn_reveal, self.btn_thumb):
            b.setEnabled(n == 1)
        self.set_rendering(rendering)
        if n != 1:
            self.version_box.hide()
            self.version_note.hide()
        else:
            self._fill_versions(records[0])
        if n == 0:
            self.preview.set_image("")
            self.title.setText("")
            self.subtitle.setText("Select an asset")
            self.body.setText("")
            return
        if n > 1:
            self.preview.set_image("")
            self.title.setText("%d assets selected" % n)
            self.subtitle.setText("")
            self.body.setText("<br>".join(html.escape(r.name) for r in records[:40]) +
                              ("<br>..." if n > 40 else ""))
            return
        rec = records[0]
        self.preview.set_image(rec.thumb)
        self.title.setText(rec.name)
        self.subtitle.setText("%s%s" % (schema.TYPE_LABELS.get(rec.type, rec.type),
                                        (" · " + rec.category) if rec.category else ""))
        self.body.setText(self._body_html(rec, self.labels.get(rec.id or "", "")))

    @staticmethod
    def _body_html(rec, label=""):
        rows = []

        def row(label, value):
            if value not in ("", None):
                rows.append("<tr><td style='color:#9aa0a8; padding-right:8px'>%s</td>"
                            "<td>%s</td></tr>" % (label, value))

        info = rec.info or {}
        row("Version", "%s of %d" % (schema.version_label(rec.current), len(rec.versions))
            if rec.versions else "")
        if rec.type == "material":
            row("Channels", html.escape(", ".join(sorted(rec.files))))
        else:
            row("Formats", html.escape(", ".join(sorted(rec.files))))
        variations = rec.variations_for() if rec.type == "geometry" else {}
        if variations:
            names = sorted(variations, key=variants.variation_order)
            row("Variations", "%d: %s" % (len(names), html.escape(", ".join(names))))
        res = info.get("resolution")
        if isinstance(res, list) and len(res) == 2:
            row("Resolution", "%d x %d" % tuple(res))
        if "displacement_scale" in info:
            row("Displacement", "%g" % info["displacement_scale"])
        if "polycount" in info:
            row("Prims", format(info["polycount"], ","))
            row("Points", format(info.get("pointcount", 0), ","))
        row("Size", _fmt_bounds(info.get("bounds")))
        sizes = info.get("file_sizes") or {}
        if sizes:
            row("On disk", _fmt_size(sum(v for v in sizes.values() if isinstance(v, (int, float)))))
        row("Added", _fmt_date(rec.created))
        row("Author", html.escape(rec.author))
        for key, name, rgb in schema.COLOR_LABELS:
            if key == label:
                row("Label", "<span style='color:#%02x%02x%02x'>&#9632;</span> %s"
                    % (rgb[0], rgb[1], rgb[2], html.escape(name)))
        out = "<table>%s</table>" % "".join(rows)
        warn = list(rec.problems) + ["missing file: %s" % k for k in rec.missing]
        if warn:
            out += "<p style='color:#dc463c'>%s</p>" % "<br>".join(html.escape(w) for w in warn)
        if rec.files:
            out += "<p style='color:#9aa0a8'>%s</p>" % "<br>".join(
                html.escape(p) for p in sorted(rec.files.values()))
        return out
