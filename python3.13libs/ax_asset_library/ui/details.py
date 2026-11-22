"""Details pane: preview, metadata and actions for the selected asset(s)."""
import html
import os

from PySide6 import QtCore, QtGui, QtWidgets

from .. import schema

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
            pix = QtGui.QPixmap(path)
            self._pix = pix if not pix.isNull() else None
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

    def __init__(self, parent=None):
        super().__init__(parent)
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
        self.btn_thumb = QtWidgets.QPushButton("Set thumbnail...")
        self.btn_thumb.setToolTip("Pick an image file; it is cropped to a square and saved as "
                                  "the asset's thumbnail.")
        grid.addWidget(self.btn_import, 0, 0, 1, 2)
        grid.addWidget(self.btn_copy, 1, 0)
        grid.addWidget(self.btn_reveal, 1, 1)
        grid.addWidget(self.btn_thumb, 2, 0, 1, 2)
        lay.addLayout(grid)

        self.btn_import.clicked.connect(self.import_requested)
        self.btn_copy.clicked.connect(self.copy_requested)
        self.btn_reveal.clicked.connect(self.reveal_requested)
        self.btn_thumb.clicked.connect(self.thumbnail_requested)
        self.set_records([])

    def set_records(self, records):
        n = len(records)
        for b in (self.btn_import, self.btn_copy):
            b.setEnabled(n > 0)
        for b in (self.btn_reveal, self.btn_thumb):
            b.setEnabled(n == 1)
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
        self.body.setText(self._body_html(rec))

    @staticmethod
    def _body_html(rec):
        rows = []

        def row(label, value):
            if value not in ("", None):
                rows.append("<tr><td style='color:#9aa0a8; padding-right:8px'>%s</td>"
                            "<td>%s</td></tr>" % (label, value))

        info = rec.info or {}
        row("Version", "%s of %d" % (schema.version_label(rec.current), len(rec.versions))
            if rec.versions else "")
        row("Tags", html.escape(", ".join(rec.tags)))
        row("Formats", html.escape(", ".join(sorted(rec.files))))
        if "polycount" in info:
            row("Prims", format(info["polycount"], ","))
            row("Points", format(info.get("pointcount", 0), ","))
        row("Size", _fmt_bounds(info.get("bounds")))
        sizes = info.get("file_sizes") or {}
        if sizes:
            row("On disk", _fmt_size(sum(v for v in sizes.values() if isinstance(v, (int, float)))))
        row("Added", _fmt_date(rec.created))
        row("Author", html.escape(rec.author))
        out = "<table>%s</table>" % "".join(rows)
        if rec.description:
            out += "<p>%s</p>" % html.escape(rec.description)
        warn = list(rec.problems) + ["missing file: %s" % k for k in rec.missing]
        if warn:
            out += "<p style='color:#dc463c'>%s</p>" % "<br>".join(html.escape(w) for w in warn)
        if rec.files:
            out += "<p style='color:#9aa0a8'>%s</p>" % "<br>".join(
                html.escape(p) for p in sorted(rec.files.values()))
        return out
