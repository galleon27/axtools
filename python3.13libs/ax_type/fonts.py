"""Font discovery, loading and glyph outlines for AX Type."""
import json
import math
import os
import re

from . import _ft_compat  # noqa: F401  (must run before fontTools loads fonts)
from fontTools.ttLib import TTFont, TTCollection
from fontTools.pens.basePen import BasePen


FONT_EXTS = (".ttf", ".otf", ".ttc", ".otc")
INDEX_VERSION = 2

_index = None          # list of dict entries
_fonts = {}            # (path, number, mtime) -> FontData


# --------------------------------------------------------------------------
# Font index
# --------------------------------------------------------------------------

def _hfs():
    return os.environ.get("HFS", "")


def fallback_font_path():
    p = os.path.join(_hfs(), "houdini", "fonts", "DejaVuSans.ttf")
    return p if os.path.isfile(p) else None


def font_dirs(extra=()):
    dirs = []
    for d in extra:
        if d:
            dirs.append(d)
    for var in ("HOUDINI_FONT_PATH",):
        for d in os.environ.get(var, "").split(os.pathsep if os.name != "nt" else ";"):
            if d and d != "&":
                dirs.append(d)
    if _hfs():
        dirs.append(os.path.join(_hfs(), "houdini", "fonts"))
    if os.name == "nt":
        windir = os.environ.get("WINDIR", "C:/Windows")
        dirs.append(os.path.join(windir, "Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    else:
        dirs += ["/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts"),
                 "/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
                 os.path.expanduser("~/.local/share/fonts")]
    seen, out = set(), []
    for d in dirs:
        d = os.path.normpath(os.path.expandvars(d))
        key = os.path.normcase(d)
        if key not in seen and os.path.isdir(d):
            seen.add(key)
            out.append(d)
    return out


def _cache_path():
    base = os.environ.get("HOUDINI_USER_PREF_DIR") or os.path.expanduser("~")
    return os.path.join(base, "ax_type_fontindex.json")


def _norm(s):
    return re.sub(r"[\s\-_]+", "", s or "").lower()


def _names_from_font(f):
    name = f["name"]

    def get(i):
        try:
            return name.getDebugName(i) or ""
        except Exception:
            return ""

    family = get(16) or get(1)
    sub = get(17) or get(2)
    full = get(4) or ("%s %s" % (family, sub)).strip()
    return family, sub, full


def _scan_file(path):
    entries = []
    low = path.lower()
    try:
        if low.endswith((".ttc", ".otc")):
            coll = TTCollection(path, lazy=True)
            fonts = list(coll.fonts)
        else:
            fonts = [TTFont(path, lazy=True)]
        for i, f in enumerate(fonts):
            family, sub, full = _names_from_font(f)
            if family:
                entries.append({"path": path, "number": i, "family": family,
                                "style": sub, "full": full})
            f.close()
    except Exception:
        pass
    return entries


def build_index(extra_dirs=(), force=False):
    """Index installed fonts. Cached on disk keyed by file size/mtime."""
    global _index
    cache = {}
    cpath = _cache_path()
    if not force and os.path.isfile(cpath):
        try:
            with open(cpath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("version") == INDEX_VERSION:
                cache = data.get("files", {})
        except Exception:
            cache = {}

    files = {}
    changed = False
    for d in font_dirs(extra_dirs):
        for root, _dirs, names in os.walk(d):
            for n in names:
                if not n.lower().endswith(FONT_EXTS):
                    continue
                p = os.path.join(root, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                stamp = "%d:%d" % (st.st_size, int(st.st_mtime))
                c = cache.get(p)
                if c and c.get("stamp") == stamp:
                    files[p] = c
                else:
                    files[p] = {"stamp": stamp, "entries": _scan_file(p)}
                    changed = True
    if changed or len(files) != len(cache):
        try:
            with open(cpath, "w", encoding="utf-8") as fh:
                json.dump({"version": INDEX_VERSION, "files": files}, fh)
        except Exception:
            pass
    _index = [e for f in files.values() for e in f["entries"]]
    _index.sort(key=lambda e: (e["family"].lower(), e["style"].lower()))
    return _index


def index(extra_dirs=()):
    if _index is None:
        build_index(extra_dirs)
    return _index


def menu_items(extra_dirs=()):
    """Flat [token, label, ...] list for a Houdini string menu."""
    out = []
    seen = set()
    for e in index(extra_dirs):
        full = e["full"]
        if full in seen:
            continue
        seen.add(full)
        out += [full, full]
    return out


_STYLE_WORDS = ("regular", "normal", "book", "roman", "plain")


def resolve(spec, extra_dirs=()):
    """Resolve a font spec to (path, font number) or None.

    spec may be a file path (optionally 'path,index' for collections), a full
    font name ('Arial Bold Italic'), or a family name ('Arial').
    """
    spec = (spec or "").strip()
    if not spec:
        return None
    m = re.match(r"^(.*\.(?:ttf|otf|ttc|otc)),\s*(\d+)$", spec, re.I)
    if m and os.path.isfile(m.group(1)):
        return m.group(1), int(m.group(2))
    if os.path.isfile(spec):
        return spec, 0

    key = _norm(spec)
    entries = index(extra_dirs)
    for e in entries:
        if _norm(e["full"]) == key:
            return e["path"], e["number"]
    for e in entries:
        if _norm(e["family"] + e["style"]) == key:
            return e["path"], e["number"]
    fam = [e for e in entries if _norm(e["family"]) == key]
    if fam:
        for e in fam:
            if _norm(e["style"]) in _STYLE_WORDS:
                return e["path"], e["number"]
        return fam[0]["path"], fam[0]["number"]
    for e in entries:
        if _norm(e["full"]).startswith(key):
            return e["path"], e["number"]
    return None


# --------------------------------------------------------------------------
# Font data
# --------------------------------------------------------------------------

class _FlattenPen(BasePen):
    """Collects contours as lists of (x, y) points with beziers flattened."""

    def __init__(self, glyphset, tol):
        BasePen.__init__(self, glyphset)
        self.tol = tol
        self.contours = []
        self.cur = None

    def _moveTo(self, p):
        self.cur = [p]

    def _lineTo(self, p):
        self.cur.append(p)

    def _segments(self, est_len):
        if self.tol <= 0:
            return 8
        return max(1, min(64, int(math.ceil(math.sqrt(est_len / (8.0 * self.tol))))))

    def _qCurveToOne(self, p1, p2):
        p0 = self.cur[-1]
        # deviation of the control point from the chord
        dev = math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1])
        n = self._segments(dev * 2.0)
        for i in range(1, n + 1):
            t = i / float(n)
            a = (1 - t) * (1 - t)
            b = 2 * (1 - t) * t
            c = t * t
            self.cur.append((a * p0[0] + b * p1[0] + c * p2[0],
                             a * p0[1] + b * p1[1] + c * p2[1]))

    def _curveToOne(self, p1, p2, p3):
        p0 = self.cur[-1]
        dev = max(math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]),
                  math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]))
        n = self._segments(dev * 6.0)
        for i in range(1, n + 1):
            t = i / float(n)
            mt = 1 - t
            a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
            self.cur.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                             a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))

    def _closePath(self):
        self._finish()

    def _endPath(self):
        self._finish()

    def _finish(self):
        c = self.cur
        self.cur = None
        if not c:
            return
        if len(c) > 1 and abs(c[0][0] - c[-1][0]) < 1e-9 and abs(c[0][1] - c[-1][1]) < 1e-9:
            c.pop()
        # drop consecutive duplicates
        pts = [c[0]]
        for p in c[1:]:
            if abs(p[0] - pts[-1][0]) > 1e-9 or abs(p[1] - pts[-1][1]) > 1e-9:
                pts.append(p)
        if len(pts) >= 3:
            self.contours.append(pts)


def signed_area(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a * 0.5


def point_in_poly(x, y, pts):
    inside = False
    n = len(pts)
    j = n - 1
    for i in range(n):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _interior_sample(pts):
    """A point just inside the contour, near its first edge."""
    area = signed_area(pts)
    n = len(pts)
    for i in range(n):
        (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        ln = math.hypot(dx, dy)
        if ln < 1e-9:
            continue
        # left normal for CCW, right normal for CW
        nx, ny = (-dy / ln, dx / ln) if area > 0 else (dy / ln, -dx / ln)
        eps = min(ln * 0.01, 1e-3 * (abs(area) ** 0.5 + 1))
        return ((x0 + x1) * 0.5 + nx * eps, (y0 + y1) * 0.5 + ny * eps)
    return pts[0]


def classify(contours):
    """Return list of (contour, is_hole, parent index).

    Solid vs hole comes from winding direction relative to the glyph's largest
    contour (always solid), which also copes with overlapping contours. A hole's
    parent is the smallest solid contour containing it.
    """
    n = len(contours)
    if not n:
        return []
    sareas = [signed_area(c) for c in contours]
    areas = [abs(a) for a in sareas]
    big = max(range(n), key=lambda i: areas[i])
    solid_sign = 1 if sareas[big] > 0 else -1
    holes = [(sareas[i] > 0) != (solid_sign > 0) for i in range(n)]
    out = []
    for i in range(n):
        parent = -1
        if holes[i]:
            sx, sy = _interior_sample(contours[i])
            cont = [j for j in range(n) if not holes[j] and areas[j] > areas[i]
                    and point_in_poly(sx, sy, contours[j])]
            if cont:
                parent = min(cont, key=lambda j: areas[j])
            else:
                holes[i] = False   # orphan "hole": draw it as a solid
        out.append((contours[i], holes[i], parent))
    return out


class FontData(object):
    def __init__(self, path, number=0):
        self.path = path
        self.number = number
        self.tt = TTFont(path, fontNumber=number, lazy=True)
        self.upem = float(self.tt["head"].unitsPerEm)
        self.cmap = self.tt.getBestCmap() or {}
        self.glyphset = self.tt.getGlyphSet()
        self.hmtx = self.tt["hmtx"].metrics
        self._outlines = {}
        self._kern = None
        self._gsub = {}
        family, style, full = _names_from_font(self.tt)
        self.family, self.style, self.full = family, style, full or os.path.basename(path)

        hhea = self.tt["hhea"]
        os2 = self.tt["OS/2"] if "OS/2" in self.tt else None
        if os2 is not None and getattr(os2, "fsSelection", 0) & (1 << 7):
            asc, desc, gap = os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap
        else:
            asc, desc, gap = hhea.ascent, hhea.descent, hhea.lineGap
        self.ascender = asc / self.upem
        self.descender = desc / self.upem          # negative
        self.linegap = gap / self.upem
        cap = getattr(os2, "sCapHeight", 0) if os2 is not None else 0
        xh = getattr(os2, "sxHeight", 0) if os2 is not None else 0
        self.cap_height = (cap / self.upem) if cap else self._ink_top("H", 0.7)
        self.x_height = (xh / self.upem) if xh else self._ink_top("x", 0.5)

    def _ink_top(self, ch, default):
        g = self.cmap.get(ord(ch))
        if not g:
            return default
        cs = self.outline(g, 0.002)
        ys = [p[1] for c, _d, _p in cs for p in c]
        return max(ys) if ys else default

    def glyph(self, ch):
        return self.cmap.get(ord(ch))

    def has_glyph(self, name):
        return name in self.glyphset

    def advance(self, gname):
        m = self.hmtx.get(gname)
        return (m[0] / self.upem) if m else 0.0

    def outline(self, gname, tol_em):
        """Classified contours in em units: [(points, depth, parent), ...]."""
        key = (gname, round(tol_em, 6))
        res = self._outlines.get(key)
        if res is None:
            pen = _FlattenPen(self.glyphset, tol_em * self.upem)
            try:
                self.glyphset[gname].draw(pen)
            except KeyError:
                pass
            s = 1.0 / self.upem
            contours = [[(x * s, y * s) for x, y in c] for c in pen.contours]
            res = classify(contours)
            self._outlines[key] = res
        return res

    # OpenType layout data is built lazily in shaping.py
    def table(self, tag):
        return self.tt[tag] if tag in self.tt else None


def load(path, number=0):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0
    key = (os.path.normcase(os.path.abspath(path)), number, mtime)
    fd = _fonts.get(key)
    if fd is None:
        fd = FontData(path, number)
        _fonts[key] = fd
    return fd


def clear_cache():
    global _index
    _fonts.clear()
    _index = None
