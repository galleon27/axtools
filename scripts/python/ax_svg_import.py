"""
AX SVG Importer - reads SVG and Adobe Illustrator (.ai) files into Houdini
as cubic Bezier curves with colour attributes.

Called from the AX::AX_SVG_importer HDA's parm callbacks (File / Reload):

    import importlib, ax_svg_import
    importlib.reload(ax_svg_import)
    ax_svg_import.import_file(kwargs["node"])

The parsed geometry is stored in the HDA's stash, so files are only read when
the file changes or Reload is pressed, and the .hip keeps working without them.

Supported
    SVG   path (all commands incl. Q/T/A), rect (rounded), circle, ellipse,
          line, polyline, polygon, use; transforms; fill / stroke colour from
          attributes, style="", <style> CSS rules (.class, #id, tag) and
          inheritance; gradients use their first stop colour
    .ai   Illustrator CS+ files saved with "Create PDF Compatible File"
          (the default): paths, transforms, RGB / CMYK / grey / spot colours,
          opacity, layers (-> name attribute). Also reads plain .pdf files.

Primitive attributes created
    Cd (fill, else stroke, linear), Alpha, fill_color, stroke_color,
    has_fill, has_stroke, stroke_width, name (SVG id / Illustrator layer)

The parsers only use the standard library, so they can be tested outside Houdini.
"""

import base64
import math
import os
import re
import zlib
import xml.etree.ElementTree as ET

SCALE = 0.01            # 1 SVG px / PDF pt = 1 cm
KAPPA = 0.5522847498307936
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


class Shape(object):
    """One painted path: a list of (points, closed) cubic-Bezier segments.

    points are [p0, c1, c2, p1, c1, c2, p2, ...] in document space (y down).
    Colours are sRGB tuples in 0-1, or None.
    """
    __slots__ = ("segments", "fill", "stroke", "alpha", "stroke_width", "name",
                 "artboard")

    def __init__(self, segments, fill=None, stroke=None, alpha=1.0,
                 stroke_width=0.0, name="", artboard=0):
        self.segments = segments
        self.fill = fill
        self.stroke = stroke
        self.alpha = alpha
        self.stroke_width = stroke_width
        self.name = name
        self.artboard = artboard          # 1-based artboard (PDF page); 0 for SVG

    def __repr__(self):
        return "Shape(%d segs, fill=%s, stroke=%s, a=%.2f, w=%.2f, %r)" % (
            len(self.segments), self.fill, self.stroke, self.alpha,
            self.stroke_width, self.name)


# --------------------------------------------------------------------------- #
# 2D affine matrices (a, b, c, d, e, f):  x' = a x + c y + e,  y' = b x + d y + f
# --------------------------------------------------------------------------- #
def mat_mul(m, n):
    """m * n : apply n first, then m."""
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + c * B, b * A + d * B,
            a * C + c * D, b * C + d * D,
            a * E + c * F + e, b * E + d * F + f)


def mat_apply(m, p):
    return (m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5])


def mat_scale_factor(m):
    return math.sqrt(abs(m[0] * m[3] - m[1] * m[2]))


# --------------------------------------------------------------------------- #
# Path building (everything becomes cubic Bezier segments)
# --------------------------------------------------------------------------- #
class PathBuilder(object):
    def __init__(self):
        self.segments = []          # [(points, closed)]
        self.pts = []
        self.start = (0.0, 0.0)
        self.cur = (0.0, 0.0)

    def _flush(self):
        if len(self.pts) >= 4:
            self.segments.append((self.pts, False))
        self.pts = []

    def move(self, p):
        self._flush()
        self.pts = [p]
        self.start = self.cur = p

    def _ensure(self):
        if not self.pts:
            self.pts = [self.cur]
            self.start = self.cur

    def line(self, p):
        self._ensure()
        p0 = self.cur
        self.pts += [(p0[0] + (p[0] - p0[0]) / 3.0, p0[1] + (p[1] - p0[1]) / 3.0),
                     (p0[0] + (p[0] - p0[0]) * 2.0 / 3.0, p0[1] + (p[1] - p0[1]) * 2.0 / 3.0),
                     p]
        self.cur = p

    def cubic(self, c1, c2, p):
        self._ensure()
        self.pts += [c1, c2, p]
        self.cur = p

    def quad(self, q, p):
        p0 = self.cur
        self.cubic((p0[0] + 2.0 / 3.0 * (q[0] - p0[0]), p0[1] + 2.0 / 3.0 * (q[1] - p0[1])),
                   (p[0] + 2.0 / 3.0 * (q[0] - p[0]), p[1] + 2.0 / 3.0 * (q[1] - p[1])), p)

    def arc(self, rx, ry, phi, large, sweep, p):
        for c1, c2, q in arc_to_cubics(self.cur, rx, ry, phi, large, sweep, p):
            self.cubic(c1, c2, q)
        self.cur = p

    def close(self):
        if self.pts:
            if len(self.pts) >= 4:
                if _dist(self.cur, self.start) > 1e-9:
                    self.line(self.start)
                self.segments.append((self.pts, True))
            self.pts = []
        self.cur = self.start

    def finish(self):
        self._flush()
        return self.segments


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def arc_to_cubics(p0, rx, ry, phi_deg, large, sweep, p1):
    """SVG elliptical arc (endpoint form) -> list of (c1, c2, p) cubic pieces."""
    if _dist(p0, p1) < 1e-12:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-12 or ry < 1e-12:
        return [(p0, p1, p1)]
    phi = math.radians(phi_deg % 360.0)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0[0] - p1[0]) / 2.0, (p0[1] - p1[1]) / 2.0
    x1 = cp * dx + sp * dy
    y1 = -sp * dx + cp * dy
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if bool(large) == bool(sweep):
        coef = -coef
    cxp = coef * rx * y1 / ry
    cyp = -coef * ry * x1 / rx
    cx = cp * cxp - sp * cyp + (p0[0] + p1[0]) / 2.0
    cy = sp * cxp + cp * cyp + (p0[1] + p1[1]) / 2.0

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = ang(1.0, 0.0, (x1 - cxp) / rx, (y1 - cyp) / ry)
    dt = ang((x1 - cxp) / rx, (y1 - cyp) / ry, (-x1 - cxp) / rx, (-y1 - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(1, int(math.ceil(abs(dt) / (math.pi / 2.0) - 1e-9)))
    step = dt / n
    k = 4.0 / 3.0 * math.tan(step / 4.0)

    def pt(t):
        x, y = rx * math.cos(t), ry * math.sin(t)
        return (cp * x - sp * y + cx, sp * x + cp * y + cy)

    def deriv(t):
        x, y = -rx * math.sin(t), ry * math.cos(t)
        return (cp * x - sp * y, sp * x + cp * y)

    out = []
    t = t1
    for i in range(n):
        a, b = t, t + step
        pa, pb = pt(a), pt(b)
        da, db = deriv(a), deriv(b)
        c1 = (pa[0] + k * da[0], pa[1] + k * da[1])
        c2 = (pb[0] - k * db[0], pb[1] - k * db[1])
        out.append((c1, c2, p1 if i == n - 1 else pb))
        t = b
    return out


def ellipse_path(pb, cx, cy, rx, ry):
    kx, ky = rx * KAPPA, ry * KAPPA
    pb.move((cx + rx, cy))
    pb.cubic((cx + rx, cy + ky), (cx + kx, cy + ry), (cx, cy + ry))
    pb.cubic((cx - kx, cy + ry), (cx - rx, cy + ky), (cx - rx, cy))
    pb.cubic((cx - rx, cy - ky), (cx - kx, cy - ry), (cx, cy - ry))
    pb.cubic((cx + kx, cy - ry), (cx + rx, cy - ky), (cx + rx, cy))
    pb.close()


def rect_path(pb, x, y, w, h, rx=0.0, ry=0.0):
    rx, ry = min(rx, w / 2.0), min(ry, h / 2.0)
    if rx <= 0 or ry <= 0:
        pb.move((x, y))
        pb.line((x + w, y))
        pb.line((x + w, y + h))
        pb.line((x, y + h))
        pb.close()
        return
    kx, ky = rx * KAPPA, ry * KAPPA
    pb.move((x + rx, y))
    pb.line((x + w - rx, y))
    pb.cubic((x + w - rx + kx, y), (x + w, y + ry - ky), (x + w, y + ry))
    pb.line((x + w, y + h - ry))
    pb.cubic((x + w, y + h - ry + ky), (x + w - rx + kx, y + h), (x + w - rx, y + h))
    pb.line((x + rx, y + h))
    pb.cubic((x + rx - kx, y + h), (x, y + h - ry + ky), (x, y + h - ry))
    pb.line((x, y + ry))
    pb.cubic((x, y + ry - ky), (x + rx - kx, y), (x + rx, y))
    pb.close()


def transform_segments(segments, m, flip=None):
    """Apply matrix m (and optionally a (x0, top) PDF -> y-down flip)."""
    out = []
    for pts, closed in segments:
        tp = [mat_apply(m, p) for p in pts]
        if flip is not None:
            x0, top = flip
            tp = [(x - x0, top - y) for x, y in tp]
        out.append((tp, closed))
    return out


# --------------------------------------------------------------------------- #
# Colours
# --------------------------------------------------------------------------- #
NAMED_COLORS = {
    "black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0),
    "green": (0, 128, 0), "lime": (0, 255, 0), "blue": (0, 0, 255),
    "yellow": (255, 255, 0), "cyan": (0, 255, 255), "aqua": (0, 255, 255),
    "magenta": (255, 0, 255), "fuchsia": (255, 0, 255), "gray": (128, 128, 128),
    "grey": (128, 128, 128), "silver": (192, 192, 192), "maroon": (128, 0, 0),
    "olive": (128, 128, 0), "navy": (0, 0, 128), "purple": (128, 0, 128),
    "teal": (0, 128, 128), "orange": (255, 165, 0), "pink": (255, 192, 203),
    "brown": (165, 42, 42), "gold": (255, 215, 0), "indigo": (75, 0, 130),
    "violet": (238, 130, 238), "darkgray": (169, 169, 169),
    "darkgrey": (169, 169, 169), "lightgray": (211, 211, 211),
    "lightgrey": (211, 211, 211),
}

NONE = "none"


def parse_color(value, current=None, gradients=None):
    """CSS colour -> (r, g, b) in 0-1, NONE, or None (unknown/unset)."""
    if value is None:
        return None
    v = value.strip()
    low = v.lower()
    if not low:
        return None
    if low in ("none", "transparent"):
        return NONE
    if low == "currentcolor":
        return current
    if low.startswith("url("):
        m = re.match(r"url\(\s*['\"]?#([^)'\"]+)['\"]?\s*\)", v)
        if m and gradients and m.group(1) in gradients:
            return gradients[m.group(1)]
        rest = v[v.find(")") + 1:].strip()      # url(#x) fallback-colour
        return parse_color(rest, current, gradients) if rest else (0.5, 0.5, 0.5)
    if low.startswith("#"):
        h = low[1:]
        if len(h) in (3, 4):
            h = "".join(ch * 2 for ch in h[:3])
        if len(h) in (6, 8):
            try:
                return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
            except ValueError:
                return None
        return None
    m = re.match(r"rgba?\(([^)]*)\)", low)
    if m:
        parts = [p for p in re.split(r"[\s,/]+", m.group(1)) if p]
        out = []
        for p in parts[:3]:
            try:
                out.append(float(p[:-1]) / 100.0 if p.endswith("%") else float(p) / 255.0)
            except ValueError:
                return None
        if len(out) == 3:
            return tuple(max(0.0, min(1.0, c)) for c in out)
        return None
    if low in NAMED_COLORS:
        return tuple(c / 255.0 for c in NAMED_COLORS[low])
    return None


def srgb_to_linear(c):
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


# --------------------------------------------------------------------------- #
# SVG
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_PATH_TOKEN_RE = re.compile(r"[a-zA-Z]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_SKIP_TAGS = {"defs", "clippath", "mask", "symbol", "style", "title", "desc",
              "metadata", "pattern", "lineargradient", "radialgradient", "text",
              "image", "marker", "filter", "script", "foreignobject", "switch"}
_INHERITED = ("fill", "stroke", "fill-opacity", "stroke-opacity", "stroke-width",
              "color", "visibility")
_PROPS = _INHERITED + ("opacity", "display", "transform")


def _local(tag):
    return tag.split("}")[-1].lower() if isinstance(tag, str) else ""


def _num(value, default=0.0):
    if value is None:
        return default
    m = _NUM_RE.search(str(value))
    return float(m.group(0)) if m else default


def parse_transform(text):
    m = IDENTITY
    if not text:
        return m
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text):
        a = [float(x) for x in _NUM_RE.findall(args)]
        name = name.lower()
        if name == "matrix" and len(a) == 6:
            t = tuple(a)
        elif name == "translate" and a:
            t = (1, 0, 0, 1, a[0], a[1] if len(a) > 1 else 0.0)
        elif name == "scale" and a:
            t = (a[0], 0, 0, a[1] if len(a) > 1 else a[0], 0, 0)
        elif name == "rotate" and a:
            r = math.radians(a[0])
            cr, sr = math.cos(r), math.sin(r)
            t = (cr, sr, -sr, cr, 0, 0)
            if len(a) >= 3:
                t = mat_mul(mat_mul((1, 0, 0, 1, a[1], a[2]), t), (1, 0, 0, 1, -a[1], -a[2]))
        elif name == "skewx" and a:
            t = (1, 0, math.tan(math.radians(a[0])), 1, 0, 0)
        elif name == "skewy" and a:
            t = (1, math.tan(math.radians(a[0])), 0, 1, 0, 0)
        else:
            continue
        m = mat_mul(m, t)
    return m


def _parse_decls(text):
    out = {}
    for decl in text.split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            out[k.strip().lower()] = v.replace("!important", "").strip()
    return out


def _parse_css(text):
    """-> list of (selector, decls) for simple selectors (.cls, #id, tag, tag.cls)."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    rules = []
    for sels, body in re.findall(r"([^{}]+)\{([^}]*)\}", text):
        decls = _parse_decls(body)
        for sel in sels.split(","):
            sel = sel.strip()
            if sel and re.match(r"^[\w\-]*(?:[.#][\w\-]+)*$", sel):
                rules.append((sel, decls))
    return rules


def _selector_matches(sel, tag, classes, eid):
    parts = re.findall(r"[.#]?[\w\-]+", sel)
    for p in parts:
        if p.startswith("."):
            if p[1:] not in classes:
                return False
        elif p.startswith("#"):
            if p[1:] != eid:
                return False
        elif p.lower() != tag:
            return False
    return True


def _specificity(sel):
    return (sel.count("#"), sel.count("."), 1 if re.match(r"^\w", sel) else 0)


class _SvgReader(object):
    def __init__(self, root):
        self.root = root
        self.ids = {}
        css = []
        for el in root.iter():
            eid = el.get("id")
            if eid:
                self.ids[eid] = el
            if _local(el.tag) == "style" and el.text:
                css.append(el.text)
        self.rules = sorted(_parse_css("\n".join(css)), key=lambda r: _specificity(r[0]))
        self.gradients = {}
        for eid, el in self.ids.items():
            if _local(el.tag) in ("lineargradient", "radialgradient"):
                c = self._gradient_color(el, 0)
                if c is not None:
                    self.gradients[eid] = c
        self.shapes = []

    def _gradient_color(self, el, depth):
        for stop in el:
            if _local(stop.tag) == "stop":
                props = dict((k, stop.get(k)) for k in ("stop-color",) if stop.get(k))
                props.update(_parse_decls(stop.get("style", "")))
                c = parse_color(props.get("stop-color", "black"))
                return c if isinstance(c, tuple) else None
        href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href")
        if href and href.startswith("#") and depth < 8 and href[1:] in self.ids:
            return self._gradient_color(self.ids[href[1:]], depth + 1)
        return None

    def _props(self, el, tag):
        props = {}
        for k in _PROPS:
            v = el.get(k)
            if v is not None:
                props[k] = v
        classes = set((el.get("class") or "").split())
        eid = el.get("id")
        for sel, decls in self.rules:
            if _selector_matches(sel, tag, classes, eid):
                props.update(decls)
        props.update(_parse_decls(el.get("style", "")))
        return props

    def walk(self, el, ctm, state, name, depth=0):
        tag = _local(el.tag)
        if tag in _SKIP_TAGS or depth > 64:
            return
        props = self._props(el, tag)
        if props.get("display", "").strip() == "none":
            return
        st = dict(state)
        for k in _INHERITED:
            if k in props and props[k].strip().lower() != "inherit":
                st[k] = props[k]
        st["opacity"] = state["opacity"] * max(0.0, min(1.0, _num(props.get("opacity"), 1.0)))
        if "transform" in props:
            ctm = mat_mul(ctm, parse_transform(el.get("transform") or props.get("transform")))
        nm = el.get("{http://ns.adobe.com/AdobeIllustrator/10.0/}name") or \
            el.get("data-name") or el.get("id") or name

        if tag in ("svg", "g", "a"):
            if tag == "svg" and depth > 0:
                ctm = mat_mul(ctm, (1, 0, 0, 1, _num(el.get("x")), _num(el.get("y"))))
            for child in el:
                self.walk(child, ctm, st, nm, depth + 1)
            return
        if tag == "use":
            href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href") or ""
            target = self.ids.get(href[1:]) if href.startswith("#") else None
            if target is not None:
                ctm = mat_mul(ctm, (1, 0, 0, 1, _num(el.get("x")), _num(el.get("y"))))
                if _local(target.tag) == "symbol":
                    for child in target:
                        self.walk(child, ctm, st, nm, depth + 1)
                else:
                    self.walk(target, ctm, st, nm, depth + 1)
            return

        pb = PathBuilder()
        if tag == "path":
            path_data(pb, el.get("d", ""))
        elif tag == "rect":
            w, h = _num(el.get("width")), _num(el.get("height"))
            if w <= 0 or h <= 0:
                return
            rx, ry = el.get("rx"), el.get("ry")
            rx = _num(rx) if rx is not None else None
            ry = _num(ry) if ry is not None else None
            if rx is None:
                rx = ry or 0.0
            if ry is None:
                ry = rx
            rect_path(pb, _num(el.get("x")), _num(el.get("y")), w, h, rx, ry)
        elif tag == "circle":
            r = _num(el.get("r"))
            if r <= 0:
                return
            ellipse_path(pb, _num(el.get("cx")), _num(el.get("cy")), r, r)
        elif tag == "ellipse":
            rx, ry = _num(el.get("rx")), _num(el.get("ry"))
            if rx <= 0 or ry <= 0:
                return
            ellipse_path(pb, _num(el.get("cx")), _num(el.get("cy")), rx, ry)
        elif tag == "line":
            pb.move((_num(el.get("x1")), _num(el.get("y1"))))
            pb.line((_num(el.get("x2")), _num(el.get("y2"))))
            st["fill"] = "none"                 # a line has no area, Cd comes from stroke
        elif tag in ("polyline", "polygon"):
            c = [float(x) for x in _NUM_RE.findall(el.get("points", ""))]
            if len(c) < 4:
                return
            pb.move((c[0], c[1]))
            for i in range(2, len(c) - 1, 2):
                pb.line((c[i], c[i + 1]))
            if tag == "polygon":
                pb.close()
        else:
            return
        segments = pb.finish()
        if not segments or st.get("visibility", "").strip() in ("hidden", "collapse"):
            return
        self._emit(segments, ctm, st, nm)

    def _emit(self, segments, ctm, st, name):
        current = parse_color(st.get("color", "black"), None, self.gradients)
        current = current if isinstance(current, tuple) else (0.0, 0.0, 0.0)
        fill = parse_color(st.get("fill", "black"), current, self.gradients)
        stroke = parse_color(st.get("stroke", "none"), current, self.gradients)
        if fill == NONE:
            fill = None
        elif not isinstance(fill, tuple):       # unknown value: SVG default is black
            fill = (0.0, 0.0, 0.0)
        stroke = stroke if isinstance(stroke, tuple) else None
        op = st["opacity"]
        if fill is not None:
            alpha = op * max(0.0, min(1.0, _num(st.get("fill-opacity"), 1.0)))
        elif stroke is not None:
            alpha = op * max(0.0, min(1.0, _num(st.get("stroke-opacity"), 1.0)))
        else:
            alpha = op
        width = _num(st.get("stroke-width"), 1.0) * mat_scale_factor(ctm) if stroke else 0.0
        self.shapes.append(Shape(transform_segments(segments, ctm), fill, stroke,
                                 alpha, width, name or ""))


def path_data(pb, d):
    toks = _PATH_TOKEN_RE.findall(d or "")
    i, n = 0, len(toks)
    cmd = ""
    prev = ""
    last_c2 = None          # last cubic control point (for S)
    last_q = None           # last quadratic control point (for T)

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    def flag():
        nonlocal i
        t = toks[i]
        if t in ("0", "1"):
            i += 1
            return t == "1"
        if t[:1] in ("0", "1"):         # compact flags: "a10 10 0 0110 10"
            toks[i] = t[1:]
            return t[0] == "1"
        raise ValueError("bad arc flag")

    while i < n:
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                pb.close()
                prev, last_c2, last_q = cmd, None, None
                continue
        elif not cmd:
            i += 1
            continue
        elif cmd == "M":
            cmd = "L"
        elif cmd == "m":
            cmd = "l"
        try:
            rel = cmd.islower()
            cx, cy = pb.cur
            ox, oy = (cx, cy) if rel else (0.0, 0.0)
            C = cmd.upper()
            if C == "M":
                pb.move((ox + num(), oy + num()))
                last_c2 = last_q = None
            elif C == "L":
                pb.line((ox + num(), oy + num()))
                last_c2 = last_q = None
            elif C == "H":
                pb.line(((cx if rel else 0.0) + num(), cy))
                last_c2 = last_q = None
            elif C == "V":
                pb.line((cx, (cy if rel else 0.0) + num()))
                last_c2 = last_q = None
            elif C == "C":
                c1 = (ox + num(), oy + num())
                c2 = (ox + num(), oy + num())
                p = (ox + num(), oy + num())
                pb.cubic(c1, c2, p)
                last_c2, last_q = c2, None
            elif C == "S":
                c2 = (ox + num(), oy + num())
                p = (ox + num(), oy + num())
                c1 = (2 * cx - last_c2[0], 2 * cy - last_c2[1]) if last_c2 else (cx, cy)
                pb.cubic(c1, c2, p)
                last_c2, last_q = c2, None
            elif C == "Q":
                q = (ox + num(), oy + num())
                p = (ox + num(), oy + num())
                pb.quad(q, p)
                last_q, last_c2 = q, None
            elif C == "T":
                p = (ox + num(), oy + num())
                q = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
                pb.quad(q, p)
                last_q, last_c2 = q, None
            elif C == "A":
                rx, ry, phi = num(), num(), num()
                large, sweep = flag(), flag()
                p = (ox + num(), oy + num())
                pb._ensure()
                pb.arc(rx, ry, phi, large, sweep, p)
                last_c2 = last_q = None
            else:
                i += 1
        except (ValueError, IndexError):
            break
        prev = cmd


def parse_svg(path):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as ex:
        raise ValueError("could not parse the SVG (%s)" % ex)
    reader = _SvgReader(root)
    state = {"fill": "black", "stroke": "none", "opacity": 1.0, "color": "black"}
    reader.walk(root, IDENTITY, state, "")
    return reader.shapes


# --------------------------------------------------------------------------- #
# PDF / Illustrator (.ai)
# --------------------------------------------------------------------------- #
class Name(str):
    pass


class Ref(tuple):
    @property
    def num(self):
        return self[0]


class Keyword(str):
    pass


class Stream(object):
    def __init__(self, d, raw):
        self.dict = d
        self.raw = raw


_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"


class Lexer(object):
    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos
        self.n = len(data)

    def _skip(self):
        d, n = self.data, self.n
        while self.pos < n:
            c = d[self.pos]
            if c in _WS:
                self.pos += 1
            elif c == 0x25:                      # % comment
                while self.pos < n and d[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def token(self):
        """Next raw token, or None at end."""
        self._skip()
        d = self.data
        if self.pos >= self.n:
            return None
        c = d[self.pos]
        if c == 0x2F:                            # /Name
            j = self.pos + 1
            while j < self.n and d[j] not in _WS and d[j] not in _DELIM:
                j += 1
            raw = d[self.pos + 1:j].decode("latin-1")
            self.pos = j
            return Name(re.sub(r"#([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), raw))
        if c == 0x28:                            # (string)
            return self._literal()
        if c == 0x3C:
            if d[self.pos:self.pos + 2] == b"<<":
                self.pos += 2
                return Keyword("<<")
            j = d.find(b">", self.pos)
            j = self.n if j < 0 else j
            hexs = re.sub(rb"\s", b"", d[self.pos + 1:j])
            self.pos = j + 1
            if len(hexs) % 2:
                hexs += b"0"
            try:
                return bytes.fromhex(hexs.decode("latin-1"))
            except ValueError:
                return b""
        if c == 0x3E and d[self.pos:self.pos + 2] == b">>":
            self.pos += 2
            return Keyword(">>")
        if c in b"[]{}":
            self.pos += 1
            return Keyword(chr(c))
        j = self.pos
        while j < self.n and d[j] not in _WS and d[j] not in _DELIM:
            j += 1
        if j == self.pos:                        # stray delimiter
            self.pos += 1
            return Keyword(chr(c))
        raw = d[self.pos:j]
        self.pos = j
        try:
            if b"." in raw:
                return float(raw)
            return int(raw)
        except ValueError:
            return Keyword(raw.decode("latin-1"))

    def _literal(self):
        d = self.data
        self.pos += 1
        depth, out = 1, bytearray()
        while self.pos < self.n:
            c = d[self.pos]
            self.pos += 1
            if c == 0x5C:                        # backslash escape
                if self.pos < self.n:
                    e = d[self.pos]
                    self.pos += 1
                    m = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}
                    if e in m:
                        out.append(m[e])
                    elif 0x30 <= e <= 0x37:
                        oct_ = bytes([e])
                        while len(oct_) < 3 and self.pos < self.n and 0x30 <= d[self.pos] <= 0x37:
                            oct_ += bytes([d[self.pos]])
                            self.pos += 1
                        out.append(int(oct_, 8) & 0xFF)
                    elif e in b"\r\n":
                        if e == 0x0D and self.pos < self.n and d[self.pos] == 0x0A:
                            self.pos += 1
                    else:
                        out.append(e)
                continue
            if c == 0x28:
                depth += 1
            elif c == 0x29:
                depth -= 1
                if depth == 0:
                    break
            out.append(c)
        return bytes(out)

    def value(self, tok=None):
        """Parse a full PDF value (arrays, dicts, references)."""
        if tok is None:
            tok = self.token()
        if isinstance(tok, Keyword):
            if tok == "[":
                arr = []
                while True:
                    t = self.token()
                    if t is None or t == "]":
                        return self._refs(arr)
                    arr.append(self.value(t))
            if tok == "<<":
                items = []
                while True:
                    t = self.token()
                    if t is None or t == ">>":
                        break
                    items.append(self.value(t))
                items = self._refs(items)
                return dict((items[k], items[k + 1]) for k in range(0, len(items) - 1, 2)
                            if isinstance(items[k], Name))
            if tok == "true":
                return True
            if tok == "false":
                return False
            if tok == "null":
                return None
        return tok

    @staticmethod
    def _refs(items):
        """Collapse `N G R` triples into Ref objects."""
        out = []
        i = 0
        while i < len(items):
            if (i + 2 < len(items) and isinstance(items[i + 2], Keyword) and items[i + 2] == "R"
                    and isinstance(items[i], int) and isinstance(items[i + 1], int)):
                out.append(Ref((items[i], items[i + 1])))
                i += 3
            else:
                out.append(items[i])
                i += 1
        return out


class PdfFile(object):
    def __init__(self, data):
        self.data = data
        self.offsets = {}
        self.cache = {}
        for m in re.finditer(rb"(?:^|[\r\n])\s*(\d+)\s+(\d+)\s+obj\b", data):
            self.offsets[int(m.group(1))] = m.end()
        self._load_object_streams()

    def _load_object_streams(self):
        for num, off in list(self.offsets.items()):
            if b"/ObjStm" not in self.data[off:off + 400]:
                continue
            obj = self.get(num)
            if not isinstance(obj, Stream) or obj.dict.get("Type") != "ObjStm":
                continue
            try:
                body = self.decode(obj)
                count, first = int(obj.dict.get("N", 0)), int(self.resolve(obj.dict.get("First", 0)))
                lex = Lexer(body)
                pairs = [(lex.token(), lex.token()) for _ in range(count)]
                for onum, ooff in pairs:
                    if isinstance(onum, int) and isinstance(ooff, int) and onum not in self.offsets:
                        self.cache[onum] = Lexer(body, first + ooff).value()
            except Exception:
                continue

    def get(self, num):
        if num in self.cache:
            return self.cache[num]
        off = self.offsets.get(num)
        if off is None:
            return None
        self.cache[num] = None                  # guards against reference loops
        lex = Lexer(self.data, off)
        val = lex.value()
        save = lex.pos
        tok = lex.token()
        if isinstance(tok, Keyword) and tok == "stream" and isinstance(val, dict):
            start = lex.pos
            if self.data[start:start + 2] == b"\r\n":
                start += 2
            elif self.data[start:start + 1] in (b"\n", b"\r"):
                start += 1
            length = self.resolve(val.get("Length"))
            end = start + length if isinstance(length, int) else -1
            if end < start or self.data[end:end + 30].find(b"endstream") < 0:
                end = self.data.find(b"endstream", start)
                if end < 0:
                    end = len(self.data)
                while end > start and self.data[end - 1] in b"\r\n":
                    end -= 1
            val = Stream(val, self.data[start:end])
        else:
            lex.pos = save
        self.cache[num] = val
        return val

    def resolve(self, v, depth=0):
        while isinstance(v, Ref) and depth < 32:
            v = self.get(v.num)
            depth += 1
        return v

    def decode(self, stream):
        data = stream.raw
        filters = self.resolve(stream.dict.get("Filter"))
        if filters is None:
            return data
        if not isinstance(filters, list):
            filters = [filters]
        for f in filters:
            f = self.resolve(f)
            if f in ("FlateDecode", "Fl"):
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    try:                        # truncated / padded streams
                        data = zlib.decompressobj().decompress(data)
                    except zlib.error as ex:
                        raise ValueError("damaged compressed stream (%s)" % ex)
            elif f in ("ASCII85Decode", "A85"):
                txt = data.strip()
                if txt.endswith(b"~>"):
                    txt = txt[:-2]
                if txt.startswith(b"<~"):
                    txt = txt[2:]
                data = base64.a85decode(re.sub(rb"\s", b"", txt))
            elif f in ("ASCIIHexDecode", "AHx"):
                h = re.sub(rb"[\s>]", b"", data)
                data = bytes.fromhex((h + b"0" * (len(h) % 2)).decode("latin-1"))
            else:
                raise ValueError("unsupported stream filter %s" % f)
        return data

    def root(self):
        refs = re.findall(rb"/Root\s+(\d+)\s+(\d+)\s+R", self.data)
        for num, _gen in reversed(refs):
            obj = self.get(int(num))
            if isinstance(obj, dict):
                return obj
        for num in self.offsets:                # no trailer found: look for the catalog
            obj = self.get(num)
            if isinstance(obj, dict) and obj.get("Type") == "Catalog":
                return obj
        return None

    def pages(self):
        """All leaf pages (= Illustrator artboards), in order, with inherited keys."""
        root = self.root()
        if root is None:
            return []
        out = []
        seen = set()

        def walk(ref, inherited, depth):
            if depth > 64:
                return
            if isinstance(ref, Ref):
                if ref.num in seen:
                    return
                seen.add(ref.num)
            node = self.resolve(ref)
            if not isinstance(node, dict):
                return
            inh = dict(inherited)
            for k in ("Resources", "MediaBox", "CropBox", "Rotate"):
                if k in node:
                    inh[k] = node[k]
            if node.get("Type") == "Page" or "Kids" not in node:
                inh.update(node)
                out.append(inh)
                return
            for kid in self.resolve(node.get("Kids")) or []:
                walk(kid, inh, depth + 1)

        walk(root.get("Pages"), {}, 0)
        return out

    def page_labels(self):
        """{page index: label prefix} from /PageLabels (Illustrator rarely writes these)."""
        root = self.root() or {}
        labels = self.resolve(root.get("PageLabels"))
        out = {}
        if not isinstance(labels, dict):
            return out
        nums = self.resolve(labels.get("Nums")) or []
        for i in range(0, len(nums) - 1, 2):
            d = self.resolve(nums[i + 1])
            prefix = self.resolve(d.get("P")) if isinstance(d, dict) else None
            if isinstance(nums[i], int) and isinstance(prefix, bytes):
                out[nums[i]] = _pdf_text(prefix).strip()
        return out


def _cmyk(c, m, y, k):
    return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))


class _PdfPainter(object):
    """Interprets content streams and collects painted paths as Shapes."""

    def __init__(self, pdf, flip):
        self.pdf = pdf
        self.flip = flip
        self.shapes = []
        self.layers = []
        self.saw_no_pdf_content = False

    # ---- colour spaces ---------------------------------------------------- #
    def _space(self, name, res):
        """-> ('gray'|'rgb'|'cmyk'|'sep'|'pattern'|'other', extra)"""
        if name in ("DeviceGray", "G", "CalGray"):
            return ("gray", None)
        if name in ("DeviceRGB", "RGB", "CalRGB"):
            return ("rgb", None)
        if name in ("DeviceCMYK", "CMYK"):
            return ("cmyk", None)
        if name == "Pattern":
            return ("pattern", None)
        cs = self.pdf.resolve((self.pdf.resolve((res or {}).get("ColorSpace")) or {}).get(name))
        return self._space_obj(cs)

    def _space_obj(self, cs):
        cs = self.pdf.resolve(cs)
        if isinstance(cs, Name):
            return self._space(cs, None)
        if not isinstance(cs, list) or not cs:
            return ("other", None)
        kind = cs[0]
        if kind == "ICCBased" and len(cs) > 1:
            st = self.pdf.resolve(cs[1])
            n = st.dict.get("N", 3) if isinstance(st, Stream) else 3
            return ({1: "gray", 4: "cmyk"}.get(n, "rgb"), None)
        if kind in ("CalRGB", "Lab"):
            return ("rgb", None)
        if kind == "CalGray":
            return ("gray", None)
        if kind in ("Separation", "DeviceN") and len(cs) >= 4:
            return ("sep", (self._space_obj(cs[2]), self.pdf.resolve(cs[3])))
        if kind == "Pattern":
            return ("pattern", None)
        return ("other", None)

    def _color(self, space, vals):
        kind, extra = space
        vals = [v for v in vals if isinstance(v, (int, float))]
        if kind == "gray" and vals:
            return (vals[0],) * 3
        if kind == "rgb" and len(vals) >= 3:
            return tuple(vals[:3])
        if kind == "cmyk" and len(vals) >= 4:
            return _cmyk(*vals[:4])
        if kind == "sep" and vals:
            alt, fn = extra
            fn = fn.dict if isinstance(fn, Stream) else fn
            if isinstance(fn, dict) and fn.get("FunctionType") == 2:
                c0 = fn.get("C0", [0.0])
                c1 = fn.get("C1", [1.0])
                e = float(fn.get("N", 1.0))
                t = max(0.0, min(1.0, vals[0])) ** e
                out = [a + t * (b - a) for a, b in zip(c0, c1)]
                return self._color(alt, out)
            if alt[0] == "cmyk":             # unknown tint transform: assume a grey ink
                return _cmyk(0, 0, 0, vals[0])
            return (1.0 - vals[0] * 0.5,) * 3
        if len(vals) == 1:                   # fall back on the operand count
            return (vals[0],) * 3
        if len(vals) == 3:
            return tuple(vals)
        if len(vals) == 4:
            return _cmyk(*vals)
        return (0.5, 0.5, 0.5)

    # ---- painting --------------------------------------------------------- #
    def _paint(self, gs, pb, fill, stroke):
        segments = pb.finish()
        if not segments:
            return
        if fill and not stroke:                 # PDF fills close their subpaths implicitly
            closed = []
            for pts, is_closed in segments:
                if not is_closed:
                    sub = PathBuilder()
                    sub.pts, sub.start, sub.cur = list(pts), pts[0], pts[-1]
                    sub.close()
                    pts, is_closed = sub.segments[0][0], True
                closed.append((pts, is_closed))
            segments = closed
        layer = next((n for n in reversed(self.layers) if n), "")
        f = tuple(max(0.0, min(1.0, c)) for c in gs["fill"]) if fill else None
        s = tuple(max(0.0, min(1.0, c)) for c in gs["stroke"]) if stroke else None
        alpha = gs["ca"] if fill else gs["CA"]
        width = gs["w"] * mat_scale_factor(gs["ctm"]) if stroke else 0.0
        self.shapes.append(Shape(transform_segments(segments, gs["ctm"], self.flip),
                                 f, s, alpha, width, layer))

    def run(self, content, res, gs, depth=0):
        if depth > 24:
            return
        if b"saved without PDF Content" in content:
            self.saw_no_pdf_content = True
        pdf = self.pdf
        res = pdf.resolve(res) or {}
        lex = Lexer(content)
        ops = []
        stack = []
        pb = PathBuilder()
        while True:
            tok = lex.token()
            if tok is None:
                break
            if not isinstance(tok, Keyword) or tok in ("[", "<<", "true", "false", "null"):
                ops.append(lex.value(tok))
                continue
            op = str(tok)
            try:
                self._op(op, ops, gs, stack, pb, res, lex, depth)
            except (IndexError, TypeError, ValueError, ZeroDivisionError):
                pass
            if op in ("f", "F", "f*", "S", "s", "B", "B*", "b", "b*", "n"):
                pb = PathBuilder()
            gs = stack.pop() if op == "Q" and stack else gs
            if op == "q":
                gs = dict(gs)
            ops = []

    def _op(self, op, a, gs, stack, pb, res, lex, depth):
        pdf = self.pdf
        if op == "q":
            stack.append(gs)
        elif op == "Q":
            pass                                    # handled by run()
        elif op == "cm":
            gs["ctm"] = mat_mul(gs["ctm"], tuple(float(x) for x in a[-6:]))
        elif op == "m":
            pb.move((a[-2], a[-1]))
        elif op == "l":
            pb.line((a[-2], a[-1]))
        elif op == "c":
            pb.cubic((a[-6], a[-5]), (a[-4], a[-3]), (a[-2], a[-1]))
        elif op == "v":
            pb.cubic(pb.cur, (a[-4], a[-3]), (a[-2], a[-1]))
        elif op == "y":
            pb.cubic((a[-4], a[-3]), (a[-2], a[-1]), (a[-2], a[-1]))
        elif op == "h":
            pb.close()
        elif op == "re":
            x, y, w, h = a[-4:]
            pb.move((x, y))
            pb.line((x + w, y))
            pb.line((x + w, y + h))
            pb.line((x, y + h))
            pb.close()
        elif op in ("f", "F", "f*"):
            self._paint(gs, pb, True, False)
        elif op == "S":
            self._paint(gs, pb, False, True)
        elif op == "s":
            pb.close()
            self._paint(gs, pb, False, True)
        elif op in ("B", "B*"):
            self._paint(gs, pb, True, True)
        elif op in ("b", "b*"):
            pb.close()
            self._paint(gs, pb, True, True)
        elif op == "w":
            gs["w"] = float(a[-1])
        elif op == "g":
            gs["fs"], gs["fill"] = ("gray", None), (a[-1],) * 3
        elif op == "G":
            gs["ss"], gs["stroke"] = ("gray", None), (a[-1],) * 3
        elif op == "rg":
            gs["fs"], gs["fill"] = ("rgb", None), tuple(a[-3:])
        elif op == "RG":
            gs["ss"], gs["stroke"] = ("rgb", None), tuple(a[-3:])
        elif op == "k":
            gs["fs"], gs["fill"] = ("cmyk", None), _cmyk(*a[-4:])
        elif op == "K":
            gs["ss"], gs["stroke"] = ("cmyk", None), _cmyk(*a[-4:])
        elif op == "cs":
            gs["fs"] = self._space(a[-1], res)
            gs["fill"] = (0.0, 0.0, 0.0)
        elif op == "CS":
            gs["ss"] = self._space(a[-1], res)
            gs["stroke"] = (0.0, 0.0, 0.0)
        elif op in ("sc", "scn"):
            gs["fill"] = (0.5, 0.5, 0.5) if gs["fs"][0] == "pattern" else self._color(gs["fs"], a)
        elif op in ("SC", "SCN"):
            gs["stroke"] = (0.5, 0.5, 0.5) if gs["ss"][0] == "pattern" else self._color(gs["ss"], a)
        elif op == "gs":
            ext = pdf.resolve((pdf.resolve(res.get("ExtGState")) or {}).get(a[-1]))
            if isinstance(ext, dict):
                if "ca" in ext:
                    gs["ca"] = float(pdf.resolve(ext["ca"]))
                if "CA" in ext:
                    gs["CA"] = float(pdf.resolve(ext["CA"]))
                if "LW" in ext:
                    gs["w"] = float(pdf.resolve(ext["LW"]))
        elif op == "Do":
            xo = pdf.resolve((pdf.resolve(res.get("XObject")) or {}).get(a[-1]))
            if isinstance(xo, Stream) and xo.dict.get("Subtype") == "Form":
                sub = dict(gs)
                mtx = pdf.resolve(xo.dict.get("Matrix"))
                if isinstance(mtx, list) and len(mtx) == 6:
                    sub["ctm"] = mat_mul(gs["ctm"], tuple(float(x) for x in mtx))
                try:
                    body = pdf.decode(xo)
                except (ValueError, zlib.error):
                    return
                self.run(body, xo.dict.get("Resources") or res, sub, depth + 1)
        elif op == "BDC":
            name = ""
            if len(a) >= 2 and a[-2] == "OC":
                props = a[-1]
                if isinstance(props, Name):
                    props = (pdf.resolve(res.get("Properties")) or {}).get(props)
                props = pdf.resolve(props)
                if isinstance(props, dict):
                    nm = pdf.resolve(props.get("Name"))
                    if isinstance(nm, bytes):
                        name = _pdf_text(nm)
            self.layers.append(name)
        elif op == "BMC":
            self.layers.append("")
        elif op == "EMC":
            if self.layers:
                self.layers.pop()
        elif op == "BI":                            # inline image: skip its binary data
            d = lex.data
            j = d.find(b"ID", lex.pos)
            if j >= 0:
                k = re.compile(rb"\sEI(?:\s|$)").search(d, j + 3)
                lex.pos = k.end() if k else len(d)


def _pdf_text(b):
    if b.startswith(b"\xfe\xff"):
        return b[2:].decode("utf-16-be", "replace")
    if b.startswith(b"\xff\xfe"):
        return b[2:].decode("utf-16-le", "replace")
    return b.decode("latin-1")


ALL_ARTBOARDS = -1
ARTBOARD_GAP = 20.0                     # pt between artboards when importing all


def _open_pdf(path):
    with open(path, "rb") as fh:
        data = fh.read()
    if b"%PDF" not in data[:1024]:
        if data.startswith(b"%!PS-Adobe") or data[:4] == b"\xc5\xd0\xd3\xc6":
            raise ValueError(
                "this is a legacy (PostScript) Illustrator file. Resave it from Illustrator "
                "with 'Create PDF Compatible File' turned on, or export it as SVG")
        raise ValueError("not an Illustrator / PDF file")
    pdf = PdfFile(data)
    pages = pdf.pages()
    if not pages:
        raise ValueError("could not find a page in the file")
    return pdf, pages


def _page_box(pdf, page):
    box = pdf.resolve(page.get("CropBox")) or pdf.resolve(page.get("MediaBox")) or [0, 0, 0, 0]
    box = [float(pdf.resolve(v) or 0) for v in box][:4]
    while len(box) < 4:
        box.append(0.0)
    return min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])


def _parse_page(pdf, page, number, x_offset=0.0):
    """Shapes of one page; number is the 1-based artboard index."""
    x0, _y0, _x1, top = _page_box(pdf, page)
    contents = pdf.resolve(page.get("Contents"))
    if not isinstance(contents, list):
        contents = [contents]
    body = []
    for c in contents:
        st = pdf.resolve(c)
        if isinstance(st, Stream):
            body.append(pdf.decode(st))
    painter = _PdfPainter(pdf, (x0 - x_offset, top))
    gs = {"ctm": IDENTITY, "fill": (0.0, 0.0, 0.0), "stroke": (0.0, 0.0, 0.0),
          "fs": ("gray", None), "ss": ("gray", None), "w": 1.0, "ca": 1.0, "CA": 1.0}
    painter.run(b"\n".join(body), page.get("Resources"), gs)
    for s in painter.shapes:
        s.artboard = number
    return painter.shapes, painter.saw_no_pdf_content


def parse_ai(path, artboard=0):
    """Read one artboard (0-based PDF page), or all of them with ALL_ARTBOARDS.

    All artboards are laid out left to right in page order, top edges aligned.
    """
    pdf, pages = _open_pdf(path)
    if artboard == ALL_ARTBOARDS:
        chosen = list(enumerate(pages))
    elif 0 <= artboard < len(pages):
        chosen = [(artboard, pages[artboard])]
    else:
        raise ValueError("artboard %d doesn't exist - the file has %d artboard%s"
                         % (artboard + 1, len(pages), "" if len(pages) == 1 else "s"))
    shapes, no_content, x = [], False, 0.0
    for index, page in chosen:
        page_shapes, flag = _parse_page(pdf, page, index + 1, x)
        shapes += page_shapes
        no_content = no_content or flag
        bx0, _by0, bx1, _by1 = _page_box(pdf, page)
        x += (bx1 - bx0) + ARTBOARD_GAP
    if not shapes:
        if no_content or b"AIPrivateData" in pdf.data:
            raise ValueError(
                "the file was saved without PDF content (only Illustrator can read it). "
                "Resave it from Illustrator with 'Create PDF Compatible File' turned on, "
                "or export it as SVG")
        where = ("any artboard" if artboard == ALL_ARTBOARDS
                 else "artboard %d" % (artboard + 1))
        raise ValueError("no vector artwork found on %s "
                         "(text has to be converted to outlines)" % where)
    return shapes


_ARTBOARD_CACHE = {}


def list_artboards(path):
    """[(0-based index, label)] for an .ai / .pdf file (cached per path + mtime)."""
    try:
        key = (os.path.abspath(path), os.path.getmtime(path))
    except OSError:
        return []
    if key in _ARTBOARD_CACHE:
        return _ARTBOARD_CACHE[key]
    pdf, pages = _open_pdf(path)
    labels = pdf.page_labels()
    out = []
    prefix = ""
    for i, page in enumerate(pages):
        prefix = labels.get(i, prefix)
        x0, y0, x1, y1 = _page_box(pdf, page)
        label = "Artboard %d" % (i + 1)
        if prefix:
            label += " - %s" % prefix
        label += "  (%g x %g pt)" % (round(x1 - x0, 1), round(y1 - y0, 1))
        out.append((i, label))
    _ARTBOARD_CACHE.clear()                 # keep only the latest file
    _ARTBOARD_CACHE[key] = out
    return out


def _is_pdf_like(path):
    return os.path.splitext(path)[1].lower() in (".ai", ".pdf", ".eps")


# --------------------------------------------------------------------------- #
# Houdini side
# --------------------------------------------------------------------------- #
def load_shapes(path, artboard=0):
    if not os.path.isfile(path):
        raise ValueError("file not found: %s" % path)
    if _is_pdf_like(path):
        return parse_ai(path, artboard)
    return parse_svg(path)


def build_geometry(shapes):
    import hou
    geo = hou.Geometry()
    a_cd = geo.addAttrib(hou.attribType.Prim, "Cd", (1.0, 1.0, 1.0))
    a_alpha = geo.addAttrib(hou.attribType.Prim, "Alpha", 1.0)
    a_fill = geo.addAttrib(hou.attribType.Prim, "fill_color", (0.0, 0.0, 0.0))
    a_stroke = geo.addAttrib(hou.attribType.Prim, "stroke_color", (0.0, 0.0, 0.0))
    a_hf = geo.addAttrib(hou.attribType.Prim, "has_fill", 0)
    a_hs = geo.addAttrib(hou.attribType.Prim, "has_stroke", 0)
    a_w = geo.addAttrib(hou.attribType.Prim, "stroke_width", 0.0)
    a_name = geo.addAttrib(hou.attribType.Prim, "name", "")
    a_board = geo.addAttrib(hou.attribType.Prim, "artboard", 0)
    for attr in (a_cd, a_fill, a_stroke):
        try:
            attr.setOptions({"type": "color"})
        except Exception:
            pass

    for shape in shapes:
        fill = srgb_to_linear(shape.fill) if shape.fill else None
        stroke = srgb_to_linear(shape.stroke) if shape.stroke else None
        cd = fill or stroke or (1.0, 1.0, 1.0)
        for pts, closed in shape.segments:
            if closed:
                pts = pts[:-1]                  # last point repeats the first
                if len(pts) < 3 or len(pts) % 3:
                    continue
            elif len(pts) < 4:
                continue
            try:
                prim = geo.createBezierCurve(len(pts), is_closed=closed, order=4)
            except hou.OperationFailed:
                continue
            for v, (x, y) in zip(prim.vertices(), pts):
                v.point().setPosition((x * SCALE, -y * SCALE, 0.0))
            prim.setAttribValue(a_cd, cd)
            prim.setAttribValue(a_alpha, float(shape.alpha))
            if fill:
                prim.setAttribValue(a_fill, fill)
                prim.setAttribValue(a_hf, 1)
            if stroke:
                prim.setAttribValue(a_stroke, stroke)
                prim.setAttribValue(a_hs, 1)
            prim.setAttribValue(a_w, float(shape.stroke_width) * SCALE)
            prim.setAttribValue(a_name, shape.name or "")
            prim.setAttribValue(a_board, int(shape.artboard))
    return geo


def import_file(node):
    """Parm callback entry point: read the file and store it in the HDA's stash."""
    import hou
    path = node.evalParm("file").strip()
    target = node.parm("geo_data")
    if not path:
        target.set(hou.Geometry())
        return
    full = hou.text.expandString(path)
    # The Artboard parm is an int menu, and Houdini stores the menu *position*:
    # 0 = All artboards, 1..N = that artboard (see artboard_menu).
    board_parm = node.parm("artboard")
    choice = board_parm.eval() if board_parm is not None else 1
    try:
        if choice > 1 and _is_pdf_like(full):
            count = len(list_artboards(full))
            if count and choice > count:        # new file has fewer artboards
                choice = 1
                board_parm.set(1)               # doesn't re-run the callback
        artboard = ALL_ARTBOARDS if choice == 0 else max(0, choice - 1)
        shapes = load_shapes(full, artboard)
    except (OSError, ValueError) as ex:
        hou.ui.displayMessage("AX SVG importer could not read\n%s\n\n%s" % (path, ex),
                              severity=hou.severityType.Error, title="AX SVG importer")
        return
    geo = build_geometry(shapes)
    with hou.undos.group("AX SVG importer: import %s" % os.path.basename(path)):
        target.set(geo)


def artboard_menu(node):
    """Menu script for the HDA's Artboard parm: [token, label, ...].

    Houdini stores an int menu's item *position*, so the order is fixed:
    position 0 is always "All artboards" and position k is artboard k.
    """
    fallback = ["0", "All artboards", "1", "Artboard 1"]
    try:
        import hou
        path = hou.text.expandString(node.evalParm("file").strip())
        if not path or not _is_pdf_like(path) or not os.path.isfile(path):
            return fallback
        boards = list_artboards(path)
    except Exception:
        return fallback
    if not boards:
        return fallback
    menu = ["0", "All artboards"]
    for index, label in boards:
        menu += [str(index + 1), label]
    return menu
