"""Thumbnail images: square, center-cropped JPGs written next to asset.json.

Qt reads the common 8-bit formats directly. EXR, HDR and RAT go through Houdini's
`iconvert` first (no tone mapping yet, so bright HDRIs can look clipped).
"""
import os
import subprocess
import tempfile

from PySide6 import QtCore, QtGui

from . import schema

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
