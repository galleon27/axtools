"""Thumbnail images: square, center-cropped JPGs written next to asset.json, and the
Karma render queue that produces them for geometry.

Qt reads the common 8-bit formats directly. EXR, HDR and RAT go through Houdini's
`iconvert` first (no tone mapping yet, so bright HDRIs can look clipped).
"""
import collections
import hashlib
import os
import shutil
import subprocess
import tempfile
import time

from PySide6 import QtCore, QtGui

from . import karma, prefs, schema

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


# --- previews for files Qt can't read ------------------------------------------------------
_PREVIEWS = {}    # source path -> cached JPG written for it
_FAILED = {}      # source path -> mtime of a file that failed to convert


def preview_cache_dir():
    return os.path.join(prefs.cache_dir(), "previews")


def clear_preview_cache():
    """Forget and delete the JPGs made for EXR/HDR/RAT previews."""
    _PREVIEWS.clear()
    _FAILED.clear()
    shutil.rmtree(preview_cache_dir(), ignore_errors=True)


def preview_path(path):
    """A file Qt can show for `path`: `path` itself for 8-bit images, else a cached JPG.

    The JPG is written with iconvert into the prefs cache dir; its name follows the file's
    mtime, so a replaced file never shows the old preview. Raises ThumbnailError when the
    conversion fails (the grid then shows the type letter).
    """
    if not path or schema.file_ext(path) in schema.QT_IMAGE_EXTS:
        return path
    cached = _PREVIEWS.get(path)
    if cached and os.path.isfile(cached):
        return cached
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return path
    if _FAILED.get(path) == stamp:   # same broken file: no second iconvert call
        raise ThumbnailError("Can't read %s." % path)
    key = "%s|%.6f" % (os.path.normcase(path), stamp)
    dst = os.path.join(preview_cache_dir(),
                       hashlib.sha1(key.encode("utf-8")).hexdigest()[:16] + ".jpg")
    if not os.path.isfile(dst):
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            make_thumbnail(path, dst)
        except (ThumbnailError, OSError):
            _FAILED[path] = stamp
            raise
    if cached and cached != dst:      # the file changed: drop the old preview
        try:
            os.remove(cached)
        except OSError:
            pass
    _PREVIEWS[path] = dst
    return dst


def store_thumbnail(asset_dir, image_path, method):
    """Write thumb.jpg from `image_path` and record it in the asset's sidecar."""
    sidecar = os.path.join(asset_dir, schema.SIDECAR)
    data = schema.load(sidecar)
    make_thumbnail(image_path, os.path.join(asset_dir, schema.THUMB))
    data["thumbnail"].update(file=schema.THUMB, method=method, generated=schema.now_iso())
    schema.save(sidecar, data)


# --- Karma render queue -------------------------------------------------------------------
LOG_NAME = "render.log"
LAST_LOG = "last_thumbnail_render.log"
# Startup chatter from renderer plugins, left out of error messages (still in the log).
_NOISE = ("[Redshift]", "[Octane]")


def husk_env():
    """Environment for thumbnail renders. Karma XPU writes a mipmapped <texture>.rat next to
    every JPG/PNG texture it reads (50-200 MB each for Megascans maps), into the asset's
    folder. KARMA_XPU_DISABLE_MIPMAPS stops that; a 512 px thumbnail doesn't need mipmaps.
    Karma CPU does not write them (tested with Houdini 22.0.459)."""
    env = dict(os.environ)
    env["KARMA_XPU_DISABLE_MIPMAPS"] = "1"
    env["HOUDINI_AUTOCONVERT_IMAGE_FILES"] = "0"
    return env


def log_tail(path, lines=12):
    """Last meaningful lines of a husk log, without renderer plugin start-up lines."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read().splitlines()
    except OSError:
        return ""
    keep = [ln for ln in text if ln.strip() and not ln.lstrip().startswith(_NOISE)]
    return "\n".join(keep[-lines:])


def find_output(workdir, expected):
    """The rendered image: `expected`, or else any image husk wrote in the work folder
    (covers frame-numbered names such as thumb.0001.png)."""
    if os.path.isfile(expected):
        return expected
    try:
        names = sorted(os.listdir(workdir))
    except OSError:
        return None
    for n in names:
        if schema.is_image(n):
            return os.path.join(workdir, n)
    return None


class _Job:
    __slots__ = ("key", "asset_dir", "target", "name", "workdir", "stage", "image")

    def __init__(self, key, asset_dir, target, name, workdir, stage, image):
        self.key, self.asset_dir, self.target = key, asset_dir, target
        self.name, self.workdir = name, workdir
        self.stage, self.image = stage, image

    @property
    def log(self):
        return os.path.join(self.workdir, LOG_NAME)


class KarmaQueue(QtCore.QObject):
    """Renders thumbnails one at a time with husk, so Houdini stays responsive.

    husk runs through subprocess.Popen (no console window on Windows, output to a log file
    in the work folder), polled by a timer. Lives at module level: renders started from a
    panel finish even if the panel closes. A failed render keeps its work folder, and the
    log is copied to last_thumbnail_render.log in the library's pref folder.
    """

    changed = QtCore.Signal()                       # queue grew or shrank
    finished = QtCore.Signal(str, bool, str)        # record key, ok, message
    POLL_MS = 250

    def __init__(self):
        super().__init__()
        self._jobs = collections.deque()
        self._current = None
        self._proc = None
        self._log_file = None
        self._started = 0.0
        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._check)
        self.renderer, self.timeout_s = "BRAY_HdKarma", 300

    def pending(self):
        """Keys (record keys) of queued and running renders."""
        out = {j.key for j in self._jobs}
        if self._current is not None:
            out.add(self._current.key)
        return out

    def is_pending(self, key):
        return key in self.pending()

    def __len__(self):
        return len(self._jobs) + (1 if self._current is not None else 0)

    def add(self, key, name, workdir, write_stage, asset_dir=None, target=None):
        """Write the stage now (main thread) and queue the render. Returns False if queued already.

        key: the record key (library.norm_path of asset.json, or a Megascans record key).
        write_stage(stage_path, image_path) writes the USD stage to render (see karma.py).
        The result goes to `target` when given (Megascans thumbnails in the prefs cache),
        otherwise to <asset_dir>/thumb.jpg with asset.json updated.
        """
        if self.is_pending(key):
            shutil.rmtree(workdir, ignore_errors=True)
            return False
        stage, image = karma.work_paths(workdir)
        write_stage(stage, image)
        self._jobs.append(_Job(key, asset_dir, target, name, workdir, stage, image))
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
        while self._current is None and self._jobs:
            husk = find_tool("husk")
            if husk is None:
                while self._jobs:
                    self._finish(self._jobs.popleft(), False, "husk not found in $HFS/bin")
                return
            job = self._jobs.popleft()
            kw = {"creationflags": _CREATE_NO_WINDOW} if os.name == "nt" else {}
            try:
                self._log_file = open(job.log, "wb")
                self._proc = subprocess.Popen(
                    [husk] + karma.husk_args(job.stage, job.image, self.renderer),
                    cwd=job.workdir, stdin=subprocess.DEVNULL, stdout=self._log_file,
                    stderr=subprocess.STDOUT, env=husk_env(), **kw)
            except OSError as e:
                self._close_log()
                self._finish(job, False, "husk failed to start: %s" % e)
                continue
            self._current = job
            self._started = time.time()
            self._poll.start()

    def _close_log(self):
        if self._log_file is not None:
            try:
                self._log_file.close()
            except OSError:
                pass
            self._log_file = None

    def _check(self):
        proc, job = self._proc, self._current
        if proc is None or job is None:
            self._poll.stop()
            return
        code = proc.poll()
        timed_out = False
        if code is None:
            if time.time() - self._started < self.timeout_s:
                return
            proc.kill()
            code = proc.wait()
            timed_out = True
        self._poll.stop()
        self._close_log()
        self._proc, self._current = None, None

        image = find_output(job.workdir, job.image)
        ok = code == 0 and image is not None and not timed_out
        if timed_out:
            msg = "render timed out after %ds" % self.timeout_s
        elif code != 0:
            msg = "husk exited with code %s" % code
        elif image is None:
            msg = "husk finished but wrote no image"
        else:
            msg = ""
        self._finish(job, ok, msg, image)
        self._next()

    def _finish(self, job, ok, msg, image=None):
        if ok:
            try:
                if job.target:
                    os.makedirs(os.path.dirname(job.target), exist_ok=True)
                    make_thumbnail(image, job.target)
                else:
                    store_thumbnail(job.asset_dir, image, "karma")
            except (ThumbnailError, schema.SidecarError, OSError) as e:
                ok, msg = False, "could not store thumbnail: %s" % e
        if ok:
            shutil.rmtree(job.workdir, ignore_errors=True)
            msg = "%s: thumbnail rendered" % job.name
        else:
            msg = "%s: %s" % (job.name, msg)
            tail = log_tail(job.log)
            if tail:
                msg += "\n\n" + tail
            saved = self._save_log(job)
            if saved:
                msg += "\n\nFull log: %s\nRender files kept in: %s" % (saved, job.workdir)
        self.finished.emit(job.key, ok, msg)
        self.changed.emit()

    @staticmethod
    def _save_log(job):
        if not os.path.isfile(job.log):
            return ""
        from . import prefs
        dst = os.path.join(prefs.cache_dir(), LAST_LOG)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(job.log, dst)
        except OSError:
            return job.log
        return dst


_QUEUE = None


def karma_queue():
    global _QUEUE
    if _QUEUE is None:
        _QUEUE = KarmaQueue()
    return _QUEUE
