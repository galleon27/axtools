"""Thumbnail grid: list model, painting delegate, lazy thumbnail loading."""
import collections

from PySide6 import QtCore, QtGui, QtWidgets

from .. import schema, thumbnails

Qt = QtCore.Qt
RecordRole = Qt.ItemDataRole.UserRole + 1
ASSET_MIME = "application/x-ax-asset-keys"

TYPE_COLORS = {
    "geometry": QtGui.QColor(37, 170, 142),
    "material": QtGui.QColor(200, 130, 40),
    "hdri": QtGui.QColor(70, 130, 210),
    "image": QtGui.QColor(150, 100, 190),
}
TYPE_LETTERS = {"geometry": "G", "material": "M", "hdri": "H", "image": "I"}
WARN_COLOR = QtGui.QColor(220, 70, 60)
FAV_COLOR = QtGui.QColor(240, 200, 60)
SCENE_COLOR = QtGui.QColor(90, 190, 110)
OUTDATED_COLOR = QtGui.QColor(230, 150, 40)
SELECT_COLOR = QtGui.QColor("#33AADD")


# --- thumbnail cache ---------------------------------------------------------------------
class _LoadSignals(QtCore.QObject):
    done = QtCore.Signal(object, QtGui.QImage)


class _LoadJob(QtCore.QRunnable):
    """Reads one thumbnail in a worker thread. QImage is thread-safe; QPixmap is not."""

    def __init__(self, key, path, signals):
        super().__init__()
        self.key, self.path, self.signals = key, path, signals

    def run(self):
        img = QtGui.QImage(self.path)
        if not img.isNull() and max(img.width(), img.height()) > 512:
            img = img.scaled(512, 512, Qt.AspectRatioMode.KeepAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
        self.signals.done.emit(self.key, img)


class ThumbCache(QtCore.QObject):
    """Module-level cache of thumbnail images, plus scaled pixmaps for the current cell size."""

    changed = QtCore.Signal()
    MAX_IMAGES = 800

    def __init__(self):
        super().__init__()
        self._images = collections.OrderedDict()   # (path, mtime) -> QImage (null = unreadable)
        self._pixmaps = {}                         # (path, mtime, size) -> QPixmap
        self._pending = set()
        self._pool = QtCore.QThreadPool()
        self._pool.setMaxThreadCount(3)
        self._signals = _LoadSignals()
        self._signals.done.connect(self._on_done)
        self._notify = QtCore.QTimer()
        self._notify.setSingleShot(True)
        self._notify.setInterval(30)               # batch repaints while many thumbs arrive
        self._notify.timeout.connect(self.changed.emit)

    def clear(self):
        self._images.clear()
        self._pixmaps.clear()
        self.changed.emit()

    def pixmap(self, path, mtime, size):
        """Scaled square pixmap, or None while loading / when there is no thumbnail."""
        if not path or mtime is None:
            return None
        key = (path, mtime)
        pkey = (path, mtime, size)
        pix = self._pixmaps.get(pkey)
        if pix is not None:
            return pix
        img = self._images.get(key)
        if img is None:
            if key not in self._pending:
                self._pending.add(key)
                self._pool.start(_LoadJob(key, path, self._signals))
            return None
        self._images.move_to_end(key)
        if img.isNull():
            return None
        if len(self._pixmaps) > self.MAX_IMAGES:
            self._pixmaps.clear()
        pix = QtGui.QPixmap.fromImage(img.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                                 Qt.TransformationMode.SmoothTransformation))
        self._pixmaps[pkey] = pix
        return pix

    def _on_done(self, key, img):
        self._pending.discard(key)
        self._images[key] = img
        while len(self._images) > self.MAX_IMAGES:
            self._images.popitem(last=False)
        self._notify.start()


_CACHE = None


def thumb_cache():
    global _CACHE
    if _CACHE is None:
        _CACHE = ThumbCache()
    return _CACHE


def clear_cache():
    if _CACHE is not None:
        _CACHE.clear()


# --- model -----------------------------------------------------------------------------
def tooltip(rec):
    lines = ["<b>%s</b>" % rec.name,
             "%s%s" % (schema.TYPE_LABELS.get(rec.type, rec.type),
                       (" · " + rec.category) if rec.category else "")]
    if rec.tags:
        lines.append("Tags: " + ", ".join(rec.tags))
    info = rec.info or {}
    if "polycount" in info:
        lines.append("%s prims, %s points" % (format(info["polycount"], ","),
                                              format(info.get("pointcount", 0), ",")))
    res = info.get("resolution")
    if isinstance(res, list) and len(res) == 2:
        lines.append("%d x %d" % tuple(res))
    if rec.files:
        lines.append(("Channels: " if rec.type == "material" else "Formats: ") +
                     ", ".join(sorted(rec.files)))
    for p in rec.problems:
        lines.append("<span style='color:#dc463c'>%s</span>" % p)
    if rec.missing:
        lines.append("<span style='color:#dc463c'>Missing: %s</span>" % ", ".join(rec.missing))
    return "<br>".join(lines)


class AssetModel(QtCore.QAbstractListModel):
    def __init__(self, drag_payload, parent=None):
        """drag_payload(records) -> (absolute file paths, text for parm fields)."""
        super().__init__(parent)
        self.records = []
        self._drag_payload = drag_payload

    def set_records(self, records):
        self.beginResetModel()
        self.records = list(records)
        self.endResetModel()

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.records):
            return None
        rec = self.records[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return rec.name
        if role == Qt.ItemDataRole.ToolTipRole:
            return tooltip(rec)
        if role == RecordRole:
            return rec
        return None

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if index.isValid():
            f |= Qt.ItemFlag.ItemIsDragEnabled
        return f

    def mimeTypes(self):
        return ["text/uri-list", "text/plain", ASSET_MIME]

    def mimeData(self, indexes):
        recs = [self.records[i.row()] for i in indexes if i.isValid()]
        mime = QtCore.QMimeData()
        paths, text = self._drag_payload(recs)
        mime.setUrls([QtCore.QUrl.fromLocalFile(p) for p in paths])
        mime.setText(text)
        mime.setData(ASSET_MIME, "\n".join(r.key for r in recs).encode("utf-8"))
        return mime

    def index_of_key(self, key):
        for i, r in enumerate(self.records):
            if r.key == key:
                return self.index(i)
        return QtCore.QModelIndex()


# --- delegate --------------------------------------------------------------------------
def _star(rect):
    """Five-point star polygon inside rect."""
    import math
    cx, cy = rect.center().x() + 0.5, rect.center().y() + 0.5
    r_out, r_in = rect.width() * 0.5, rect.width() * 0.22
    pts = []
    for i in range(10):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(-90 + i * 36)
        pts.append(QtCore.QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return QtGui.QPolygonF(pts)


class AssetDelegate(QtWidgets.QStyledItemDelegate):
    PAD = 6
    GAP = 4
    LIST_THUMB = 44

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.size = 128
        self.favorites = set()   # asset ids
        self.scene = {}          # record key -> True when outdated, False when current
        self.list_mode = False

    def sizeHint(self, option, index):
        fm = option.fontMetrics
        if self.list_mode:
            width = max(200, self.view.viewport().width() - 4)
            return QtCore.QSize(width, self.LIST_THUMB + 8)
        return QtCore.QSize(self.size + 2 * self.PAD,
                            self.size + 2 * self.PAD + self.GAP + fm.height())

    @staticmethod
    def _detail_line(rec):
        parts = [schema.TYPE_LABELS.get(rec.type, rec.type)]
        if rec.category:
            parts.append(rec.category)
        if rec.files:
            parts.append(", ".join(sorted(rec.files)))
        info = rec.info or {}
        if "polycount" in info:
            parts.append("%s prims" % format(info["polycount"], ","))
        res = info.get("resolution")
        if isinstance(res, list) and len(res) == 2:
            parts.append("%d x %d" % tuple(res))
        if len(rec.versions) > 1:
            parts.append(schema.version_label(rec.current))
        return "  \u00b7  ".join(parts)

    def paint(self, painter, option, index):
        rec = index.data(RecordRole)
        if rec is None:
            return
        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        cell = option.rect
        size = self.LIST_THUMB if self.list_mode else self.size
        pad = 4 if self.list_mode else self.PAD
        box = QtCore.QRect(cell.x() + pad, cell.y() + pad, size, size)
        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QtWidgets.QStyle.StateFlag.State_MouseOver)

        if selected or hover:
            bg = QtGui.QColor(SELECT_COLOR if selected else option.palette.color(
                QtGui.QPalette.ColorRole.Midlight))
            bg.setAlpha(60 if selected else 40)
            painter.fillRect(cell.adjusted(1, 1, -1, -1), bg)

        pix = thumb_cache().pixmap(rec.thumb, rec.thumb_mtime, size)
        painter.fillRect(box, QtGui.QColor(30, 30, 30))
        if pix is not None:
            x = box.x() + (box.width() - pix.width()) // 2
            y = box.y() + (box.height() - pix.height()) // 2
            painter.drawPixmap(x, y, pix)
        else:
            self._placeholder(painter, box, rec)

        self._badges(painter, box, rec)
        if thumbnails.karma_queue().is_pending(rec.asset_dir):
            painter.fillRect(box, QtGui.QColor(0, 0, 0, 140))
            painter.setPen(QtGui.QColor(230, 230, 230))
            painter.drawText(box, Qt.AlignmentFlag.AlignCenter, "Rendering...")

        border = SELECT_COLOR if selected else (WARN_COLOR if (rec.broken or rec.missing)
                                                else QtGui.QColor(0, 0, 0))
        pen = QtGui.QPen(border, 2 if selected or rec.broken or rec.missing else 1)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(box.adjusted(0, 0, -1, -1))

        fm = option.fontMetrics
        text_color = option.palette.color(QtGui.QPalette.ColorRole.Text)
        if self.list_mode:
            x = box.right() + 10
            w = cell.right() - x - 4
            line = fm.height()
            top = cell.y() + (cell.height() - 2 * line - 2) // 2
            f = QtGui.QFont(option.font)
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(text_color)
            painter.drawText(QtCore.QRect(x, top, w, line), Qt.AlignmentFlag.AlignVCenter,
                             QtGui.QFontMetrics(f).elidedText(rec.name, Qt.TextElideMode.ElideMiddle, w))
            painter.setFont(option.font)
            dim = QtGui.QColor(text_color)
            dim.setAlpha(150)
            painter.setPen(dim)
            painter.drawText(QtCore.QRect(x, top + line + 2, w, line), Qt.AlignmentFlag.AlignVCenter,
                             fm.elidedText(self._detail_line(rec), Qt.TextElideMode.ElideRight, w))
            painter.restore()
            return
        text_rect = QtCore.QRect(cell.x() + 2, box.bottom() + self.GAP, cell.width() - 4, fm.height())
        painter.setPen(text_color)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                         fm.elidedText(rec.name, Qt.TextElideMode.ElideMiddle, text_rect.width()))
        painter.restore()

    def _placeholder(self, painter, box, rec):
        color = QtGui.QColor(TYPE_COLORS.get(rec.type, QtGui.QColor(120, 120, 120)))
        color.setAlpha(90)
        f = QtGui.QFont(painter.font())
        f.setPixelSize(max(12, box.height() // 3))
        f.setBold(True)
        painter.setFont(f)
        painter.setPen(color)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, TYPE_LETTERS.get(rec.type, "?"))
        painter.setFont(self.view.font())

    def _badges(self, painter, box, rec):
        f = QtGui.QFont(self.view.font())
        f.setPixelSize(10)
        f.setBold(True)
        painter.setFont(f)
        fm = QtGui.QFontMetrics(f)
        h = fm.height() + 2

        # Type letter, top left.
        tr = QtCore.QRect(box.x() + 3, box.y() + 3, h, h)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(TYPE_COLORS.get(rec.type, QtGui.QColor(120, 120, 120)))
        painter.drawRoundedRect(tr, 3, 3)
        painter.setPen(QtGui.QColor(255, 255, 255))
        painter.drawText(tr, Qt.AlignmentFlag.AlignCenter, TYPE_LETTERS.get(rec.type, "?"))

        # Version, top right, when there is more than one.
        if len(rec.versions) > 1:
            label = schema.version_label(rec.current)
            w = fm.horizontalAdvance(label) + 6
            vr = QtCore.QRect(box.right() - 3 - w, box.y() + 3, w, h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(0, 0, 0, 160))
            painter.drawRoundedRect(vr, 3, 3)
            painter.setPen(QtGui.QColor(230, 230, 230))
            painter.drawText(vr, Qt.AlignmentFlag.AlignCenter, label)

        # Favorite star and in-scene dot, bottom left.
        x = box.x() + 3
        if rec.id and rec.id in self.favorites:
            star = QtCore.QRect(x, box.bottom() - 3 - h, h, h)
            painter.setPen(QtGui.QColor(0, 0, 0, 160))
            painter.setBrush(FAV_COLOR)
            painter.drawPolygon(_star(star))
            x += h + 3
        if rec.key in self.scene:
            dot = QtCore.QRect(x, box.bottom() - 3 - h, h, h).adjusted(2, 2, -2, -2)
            painter.setPen(QtGui.QColor(0, 0, 0, 160))
            painter.setBrush(OUTDATED_COLOR if self.scene[rec.key] else SCENE_COLOR)
            painter.drawEllipse(dot)

        # Warning, bottom right.
        if rec.broken or rec.missing:
            wr = QtCore.QRect(box.right() - 3 - h, box.bottom() - 3 - h, h, h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(WARN_COLOR)
            painter.drawEllipse(wr)
            painter.setPen(QtGui.QColor(255, 255, 255))
            painter.drawText(wr, Qt.AlignmentFlag.AlignCenter, "!")


# --- view ------------------------------------------------------------------------------
class AssetView(QtWidgets.QListView):
    """Thumbnail grid. Import is double-click or Enter only: Houdini's style turns on
    single-click activation, so QAbstractItemView.activated can't be used for it."""

    import_requested = QtCore.Signal()
    delete_requested = QtCore.Signal()
    rename_requested = QtCore.Signal()
    copy_requested = QtCore.Signal()
    favorite_requested = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.setMovement(QtWidgets.QListView.Movement.Static)
        self.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setWrapping(True)
        self.setSpacing(2)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.DragOnly)
        self.setDragEnabled(True)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(30)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.delegate = AssetDelegate(self)
        self.setItemDelegate(self.delegate)
        thumb_cache().changed.connect(self.viewport().update)
        thumbnails.karma_queue().changed.connect(self.viewport().update)

    def set_thumb_size(self, size):
        self.delegate.size = int(size)
        self._relayout()

    def set_list_mode(self, on):
        self.delegate.list_mode = bool(on)
        self.setViewMode(QtWidgets.QListView.ViewMode.ListMode if on
                         else QtWidgets.QListView.ViewMode.IconMode)
        self.setWrapping(not on)
        self.setMovement(QtWidgets.QListView.Movement.Static)
        self.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.setSpacing(0 if on else 2)
        self._relayout()

    def _relayout(self):
        if self.delegate.list_mode:
            self.setGridSize(QtCore.QSize())
        else:
            opt = QtWidgets.QStyleOptionViewItem()
            opt.initFrom(self)
            self.setGridSize(self.delegate.sizeHint(opt, QtCore.QModelIndex()))
        self.doItemsLayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.delegate.list_mode:   # rows follow the view width
            self.doItemsLayout()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and \
                self.indexAt(event.position().toPoint()).isValid():
            event.accept()
            self.import_requested.emit()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                self.selectionModel().hasSelection():
            event.accept()
            self.import_requested.emit()
            return
        if event.key() == Qt.Key.Key_Delete and self.selectionModel().hasSelection():
            event.accept()
            self.delete_requested.emit()
            return
        if event.key() == Qt.Key.Key_F2 and self.selectionModel().hasSelection():
            event.accept()
            self.rename_requested.emit()
            return
        if event.matches(QtGui.QKeySequence.StandardKey.Copy) and self.selectionModel().hasSelection():
            event.accept()
            self.copy_requested.emit()
            return
        if event.key() == Qt.Key.Key_F and not event.modifiers() and \
                self.selectionModel().hasSelection():
            event.accept()
            self.favorite_requested.emit()
            return
        super().keyPressEvent(event)

    def selected_records(self):
        rows = sorted(i.row() for i in self.selectionModel().selectedIndexes())
        model = self.model()
        return [model.records[r] for r in rows if r < len(model.records)]

    def startDrag(self, actions):
        # Small drag pixmap instead of Qt's default render of every selected cell.
        indexes = self.selectedIndexes()
        if not indexes:
            return
        drag = QtGui.QDrag(self)
        drag.setMimeData(self.model().mimeData(indexes))
        rec = indexes[0].data(RecordRole)
        pix = thumb_cache().pixmap(rec.thumb, rec.thumb_mtime, 64) if rec else None
        if pix is not None:
            drag.setPixmap(pix)
        drag.exec(Qt.DropAction.CopyAction)
