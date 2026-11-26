"""Resource console: what the asset browser uses, refreshed while it is open."""
from PySide6 import QtCore, QtGui, QtWidgets

from .. import prefs as _prefs, resources, thumbnails
from . import grid


class ResourceConsole(QtWidgets.QWidget):
    """Read-only text with memory and cache numbers, plus buttons that empty the caches.

    `records` is a callable returning the number of records the panel holds."""

    rescan_requested = QtCore.Signal()

    def __init__(self, records=None, parent=None):
        super().__init__(parent)
        self._records = records
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(200)
        self.text.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.SystemFont.FixedFont))
        self.text.setFixedHeight(130)
        lay.addWidget(self.text)
        row = QtWidgets.QHBoxLayout()
        for label, tip, fn in (
                ("Clear images", "Empty the thumbnails kept in memory.", self._clear_images),
                ("Clear previews", "Delete the JPG previews made for EXR/HDR/RAT files.",
                 self._clear_previews),
                ("Clear scan index", "Delete the scan index: the next scan reads every "
                 "asset again.", self._clear_index)):
            b = QtWidgets.QPushButton(label)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self.refresh)

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._timer.stop()

    def refresh(self):
        lines = resources.report_lines(
            _prefs.cache_dir(), grid.thumb_cache().stats(),
            self._records() if self._records else None, len(thumbnails.karma_queue()))
        bar = self.text.verticalScrollBar().value()
        self.text.setPlainText("\n".join(lines))
        self.text.verticalScrollBar().setValue(bar)

    def _clear_images(self):
        grid.clear_cache()
        self.refresh()

    def _clear_previews(self):
        thumbnails.clear_preview_cache()
        grid.clear_cache()
        self.refresh()

    def _clear_index(self):
        resources.clear_index(_prefs.cache_dir())
        self.refresh()
