"""Thumbnail images: square, center-cropped JPGs written next to asset.json, and the
Karma render queue that produces them for geometry.

Qt reads the common 8-bit formats directly. EXR, HDR and RAT go through Houdini's
`iconvert` first (no tone mapping yet, so bright HDRIs can look clipped).
"""
import collections
import os
import shutil
import subprocess
import tempfile

from PySide6 import QtCore, QtGui

from . import karma, schema

THUMB_SIZE = 512
_CREATE_NO_WINDOW = 0x08000000  # keep a console from flashing up on Windows


class ThumbnailError(Exception):
    pass


def find_tool(name):
    """Full path of a tool in $HFS/bin (iconvert, husk, hoiiotool), or None."""
    hfs = os.environ.get("HFS", "")
    if not hfs:
        try:
            import hou
            hfs = hou.text.expandString("$HFS")
        except Exception:
            hfs = ""
    for exe in (name, name + ".exe"):
        p = os.path.join(hfs, "bin", exe)
        if hfs and os.path.isfile(p):
            return p
    return None


def _run(cmd, timeout=120):
    kw = {"creationflags": _CREATE_NO_WINDOW} if os.name == "nt" else {}
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          timeout=timeout, **kw)
    if proc.returncode != 0:
        raise ThumbnailError("%s failed: %s" % (os.path.basename(cmd[0]),
                                                proc.stdout.decode(errors="replace").strip()))


def load_image(path):
    """QImage for any supported file; converts non-Qt formats with iconvert."""
    if schema.file_ext(path) in schema.QT_IMAGE_EXTS:
        img = QtGui.QImage(path)
        if not img.isNull():
            return img
    tool = find_tool("iconvert")
    if tool is None:
        raise ThumbnailError("Can't read %s (iconvert not found in $HFS/bin)." % path)
    fd, tmp = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        _run([tool, path, tmp])
        img = QtGui.QImage(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if img.isNull():
        raise ThumbnailError("Can't read %s." % path)
    return img


def square(img, size=THUMB_SIZE):
    """Center-crop to a square and scale down to `size` (never up)."""
    side = min(img.width(), img.height())
    x, y = (img.width() - side) // 2, (img.height() - side) // 2
    img = img.copy(x, y, side, side)
    if side > size:
        img = img.scaled(size, size, QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                         QtCore.Qt.TransformationMode.SmoothTransformation)
    return img


def make_thumbnail(src, dst, size=THUMB_SIZE):
    """Write a square JPG thumbnail of `src` to `dst` (atomic replace)."""
    img = square(load_image(src), size)
    if img.hasAlphaChannel():  # JPG has no alpha: flatten on dark grey
        flat = QtGui.QImage(img.size(), QtGui.QImage.Format.Format_RGB32)
        flat.fill(QtGui.QColor(40, 40, 40))
        p = QtGui.QPainter(flat)
        p.drawImage(0, 0, img)
        p.end()
        img = flat
    tmp = dst + ".tmp"
    if not img.save(tmp, "JPG", 90):
        raise ThumbnailError("Could not write %s." % dst)
    os.replace(tmp, dst)
    return dst


def store_thumbnail(asset_dir, image_path, method):
    """Write thumb.jpg from `image_path` and record it in the asset's sidecar."""
    sidecar = os.path.join(asset_dir, schema.SIDECAR)
    data = schema.load(sidecar)
    make_thumbnail(image_path, os.path.join(asset_dir, schema.THUMB))
    data["thumbnail"].update(file=schema.THUMB, method=method, generated=schema.now_iso())
    schema.save(sidecar, data)


# --- Karma render queue -------------------------------------------------------------------
class _Job:
    __slots__ = ("asset_dir", "name", "workdir", "stage", "image")

    def __init__(self, asset_dir, name, workdir, stage, image):
        self.asset_dir, self.name, self.workdir = asset_dir, name, workdir
        self.stage, self.image = stage, image


class KarmaQueue(QtCore.QObject):
    """Renders thumbnails one at a time with husk in a QProcess, so Houdini stays responsive.

    Lives at module level: renders started from a panel finish even if the panel closes.
    """

    changed = QtCore.Signal()                       # queue grew or shrank
    finished = QtCore.Signal(str, bool, str)        # asset_dir, ok, message

    def __init__(self):
        super().__init__()
        self._jobs = collections.deque()
        self._current = None
        self._proc = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._timed_out = False
        self.renderer, self.timeout_s = "BRAY_HdKarma", 300

    def pending(self):
        out = {os.path.normcase(j.asset_dir) for j in self._jobs}
        if self._current is not None:
            out.add(os.path.normcase(self._current.asset_dir))
        return out

    def is_pending(self, asset_dir):
        return os.path.normcase(asset_dir) in self.pending()

    def __len__(self):
        return len(self._jobs) + (1 if self._current is not None else 0)

    def add(self, asset_dir, name, layer_file, bounds, workdir, resolution=512, samples=16):
        """Write the stage now (main thread) and queue the render. Returns False if queued already."""
        if self.is_pending(asset_dir):
            shutil.rmtree(workdir, ignore_errors=True)
            return False
        stage, image = karma.work_paths(workdir)
        karma.write_stage(stage, layer_file, bounds, image, resolution, samples)
        self._jobs.append(_Job(asset_dir, name, workdir, stage, image))
        self.changed.emit()
        self._next()
        return True

    def cancel_all(self):
        while self._jobs:
            shutil.rmtree(self._jobs.popleft().workdir, ignore_errors=True)
        if self._proc is not None:
            self._proc.kill()
        self.changed.emit()

    def _next(self):
        if self._current is not None or not self._jobs:
            return
        husk = find_tool("husk")
        if husk is None:
            while self._jobs:
                self._finish(self._jobs.popleft(), False, "husk not found in $HFS/bin")
            return
        job = self._jobs.popleft()
        self._current = job
        self._timed_out = False
        proc = QtCore.QProcess(self)
        proc.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        proc.setWorkingDirectory(job.workdir)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        self._proc = proc
        proc.start(husk, karma.husk_args(job.stage, job.image, self.renderer))
        self._timer.start(int(self.timeout_s * 1000))

    def _output(self):
        if self._proc is None:
            return ""
        text = bytes(self._proc.readAll()).decode(errors="replace").strip()
        return text[-600:]

    def _on_timeout(self):
        if self._proc is not None:
            self._timed_out = True
            self._proc.kill()  # _on_finished/_on_error reports it

    def _on_error(self, error):
        if error == QtCore.QProcess.ProcessError.FailedToStart and self._current is not None:
            self._done(False, "husk failed to start")

    def _on_finished(self, code, _status):
        if self._current is None:
            return
        ok = code == 0 and os.path.isfile(self._current.image)
        msg = "" if ok else ("husk exited with code %s. %s" % (code, self._output()))
        if self._timed_out:
            msg = "render timed out after %ds" % self.timeout_s
        self._done(ok, msg)

    def _done(self, ok, msg):
        self._timer.stop()
        job, self._current = self._current, None
        if self._proc is not None:
            self._proc.deleteLater()
            self._proc = None
        self._finish(job, ok, msg)
        self._next()

    def _finish(self, job, ok, msg):
        if ok:
            try:
                store_thumbnail(job.asset_dir, job.image, "karma")
            except (ThumbnailError, schema.SidecarError, OSError) as e:
                ok, msg = False, str(e)
        shutil.rmtree(job.workdir, ignore_errors=True)
        self.finished.emit(job.asset_dir, ok, msg or "%s: thumbnail rendered" % job.name)
        self.changed.emit()


_QUEUE = None


def karma_queue():
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = KarmaQueue()
    return _QUEUE
