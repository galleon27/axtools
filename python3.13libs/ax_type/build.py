"""Turn a Layout into Houdini geometry."""
from .fonts import signed_area

OUT_FACES, OUT_CURVES, OUT_POINTS = 0, 1, 2
PACK_NONE, PACK_GLYPH, PACK_WORD, PACK_LINE = 0, 1, 2, 3

_face_cache = {}


# --------------------------------------------------------------------------
# Hole bridging (keyhole polygons, as the Font SOP produces)
# --------------------------------------------------------------------------

def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _same(p, q):
    return abs(p[0] - q[0]) < 1e-12 and abs(p[1] - q[1]) < 1e-12


def _crosses(m, v, edges):
    for a, b in edges:
        if _same(a, m) or _same(b, m) or _same(a, v) or _same(b, v):
            continue
        d1 = _orient(m, v, a)
        d2 = _orient(m, v, b)
        if (d1 > 0) == (d2 > 0) and d1 != 0 and d2 != 0:
            continue
        d3 = _orient(a, b, m)
        d4 = _orient(a, b, v)
        if (d3 > 0) == (d4 > 0) and d3 != 0 and d4 != 0:
            continue
        if d1 == 0 and d2 == 0:
            continue  # collinear, ignore
        return True
    return False


def _edges(poly):
    return [(poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]


def bridge(outer, holes):
    """Merge holes into outer contour. outer CCW, holes CW (math orientation)."""
    poly = list(outer)
    holes = sorted(holes, key=lambda h: -max(p[0] for p in h))
    for k, h in enumerate(holes):
        mi = max(range(len(h)), key=lambda i: (h[i][0], -h[i][1]))
        m = h[mi]
        edges = _edges(poly)
        for other in holes[k:]:
            edges += _edges(other)
        order = sorted(range(len(poly)),
                       key=lambda i: (poly[i][0] - m[0]) ** 2 + (poly[i][1] - m[1]) ** 2)
        vi = order[0]
        for i in order:
            if not _crosses(m, poly[i], edges):
                vi = i
                break
        poly = poly[:vi + 1] + h[mi:] + h[:mi + 1] + poly[vi:]
    return poly


def glyph_faces(font, gname, tol_em):
    """Bridged face polygons in em units, CCW."""
    key = (id(font), gname, round(tol_em, 6))
    res = _face_cache.get(key)
    if res is not None:
        return res
    cls = font.outline(gname, tol_em)
    holes_of = {}
    for i, (c, is_hole, parent) in enumerate(cls):
        if is_hole:
            holes_of.setdefault(parent, []).append(c)
    res = []
    for i, (c, is_hole, _parent) in enumerate(cls):
        if is_hole:
            continue
        outer = c if signed_area(c) > 0 else c[::-1]
        hs = [h if signed_area(h) < 0 else h[::-1] for h in holes_of.get(i, [])]
        res.append(bridge(outer, hs) if hs else outer)
    _face_cache[key] = res
    return res


def glyph_curves(font, gname, tol_em):
    """[(contour, is_hole)] in em units."""
    return [(c, h) for c, h, _p in font.outline(gname, tol_em)]


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def write(geo, lay, opts):
    import hou

    mode = opts.get("output", OUT_FACES)
    tol = max(opts.get("tolerance", 0.002), 1e-5)
    gscale = opts.get("scale", 1.0)
    tx, ty, tz = opts.get("t", (0.0, 0.0, 0.0))
    pack = opts.get("pack", PACK_NONE) if mode != OUT_POINTS else PACK_NONE
    # Houdini's front face is clockwise when looking down -Z
    flip = opts.get("flip_winding", True)

    vis = [g for g in lay.glyphs if g.visible]
    nvis = len(vis)
    nwords = max(lay.num_words, 1)
    nlines = max(len(lay.lines), 1)

    positions = []
    prim_pts = []
    prim_glyph = []
    prim_hole = []

    for gi, g in enumerate(vis):
        if mode == OUT_POINTS:
            positions.append(hou.Vector3(g.x * gscale + tx, g.y * gscale + ty, tz))
            prim_glyph.append(gi)
            continue
        s = max(abs(g.sx), abs(g.sy), 1e-9)
        tol_em = tol / (s * abs(gscale) if gscale else s)
        if mode == OUT_FACES:
            shapes = [(c, 0) for c in glyph_faces(g.font, g.name, tol_em)]
        else:
            shapes = [(c, 1 if h else 0) for c, h in glyph_curves(g.font, g.name, tol_em)]
        for c, h in shapes:
            if mode == OUT_FACES and flip:
                c = c[::-1]
            start = len(positions)
            for px, py in c:
                x = g.x + px * g.sx + py * g.sy * g.shear
                y = g.y + py * g.sy
                positions.append(hou.Vector3(x * gscale + tx, y * gscale + ty, tz))
            n = len(c)
            idx = list(range(start, start + n))
            if mode == OUT_CURVES:
                idx.append(start)
            prim_pts.append(idx)
            prim_glyph.append(gi)
            prim_hole.append(h)

    pts = geo.createPoints(positions)
    if mode == OUT_POINTS:
        elems = len(pts)
        attr_type = hou.attribType.Point
    else:
        polys = geo.createPolygons([[pts[i] for i in idx] for idx in prim_pts],
                                   mode == OUT_FACES)
        elems = len(polys)
        attr_type = hou.attribType.Prim

    def per(fn):
        return [fn(vis[gi]) for gi in prim_glyph]

    def add(name, default, values, tuple_size=1):
        geo.addAttrib(attr_type, name, default)
        if attr_type == hou.attribType.Point:
            setter = {float: geo.setPointFloatAttribValues, int: geo.setPointIntAttribValues,
                      str: geo.setPointStringAttribValues}
        else:
            setter = {float: geo.setPrimFloatAttribValues, int: geo.setPrimIntAttribValues,
                      str: geo.setPrimStringAttribValues}
        kind = type(default[0] if isinstance(default, tuple) else default)
        if tuple_size > 1:
            values = [v for t in values for v in t]
        setter[kind](name, values)

    if elems:
        gidx = prim_glyph
        add("char", "", per(lambda g: g.text))
        add("glyph", "", per(lambda g: g.name))
        add("font", "", per(lambda g: g.font.full))
        add("style", "", per(lambda g: g.styles[-1] if g.styles else ""))
        add("char_index", 0, per(lambda g: g.chars[0]))
        add("glyph_index", 0, list(gidx))
        add("word_index", 0, per(lambda g: g.word))
        add("line_index", 0, per(lambda g: g.line))
        add("run_index", 0, per(lambda g: g.run))
        add("glyph_u", 0.0, [gi / float(max(nvis - 1, 1)) for gi in gidx])
        add("word_u", 0.0, per(lambda g: g.word / float(max(nwords - 1, 1))))
        add("line_u", 0.0, per(lambda g: g.line / float(max(nlines - 1, 1))))
        add("size", 0.0, per(lambda g: g.props["size"] * gscale))
        add("Cd", (1.0, 1.0, 1.0), per(lambda g: tuple(g.props.get("color", (1, 1, 1)))), 3)
        add("Alpha", 1.0, per(lambda g: g.props.get("alpha", 1.0)))
        if mode == OUT_CURVES:
            add("ishole", 0, prim_hole)
        if mode == OUT_POINTS:
            add("advance", 0.0, per(lambda g: g.width * gscale))
            add("pscale", 0.0, per(lambda g: g.props["size"] * gscale))
        if pack != PACK_NONE:
            fmt = {PACK_GLYPH: lambda g, gi: "glyph_%d" % gi,
                   PACK_WORD: lambda g, gi: "word_%d" % g.word,
                   PACK_LINE: lambda g, gi: "line_%d" % g.line}[pack]
            add("name", "", [fmt(vis[gi], gi) for gi in gidx])

    geo.addAttrib(hou.attribType.Global, "text", "")
    geo.setGlobalAttribValue("text", lay.text)
    for name, val in (("num_glyphs", nvis), ("num_words", lay.num_words),
                      ("num_lines", len(lay.lines))):
        geo.addAttrib(hou.attribType.Global, name, 0)
        geo.setGlobalAttribValue(name, val)
    xmin, ymin, xmax, ymax = lay.bbox
    geo.addAttrib(hou.attribType.Global, "text_bounds", (0.0, 0.0, 0.0, 0.0))
    geo.setGlobalAttribValue("text_bounds", (xmin * gscale + tx, ymin * gscale + ty,
                                             xmax * gscale + tx, ymax * gscale + ty))
