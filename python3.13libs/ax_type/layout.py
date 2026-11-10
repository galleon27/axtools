"""Point-type layout: lines split on newlines, per-line alignment, no wrapping."""
import math

from . import shaping


class Placed(object):
    """A positioned glyph. x/y is the baseline origin in output units."""
    __slots__ = ("font", "name", "text", "chars", "props", "styles", "x", "y",
                 "sx", "sy", "shear", "width", "line", "word", "run", "visible")


class Line(object):
    __slots__ = ("glyphs", "width", "ascent", "descent", "cap", "size", "leading", "align",
                 "baseline", "offset")


class Layout(object):
    def __init__(self):
        self.glyphs = []
        self.lines = []
        self.text = ""
        self.num_words = 0
        self.bbox = (0.0, 0.0, 0.0, 0.0)   # xmin, ymin, xmax, ymax (metrics, not ink)


def _units(props):
    s = props["size"]
    return s, s * props.get("hscale", 1.0), s * props.get("vscale", 1.0)


def layout(segments, opts, get_font, missing):
    """
    segments: markup.Segment list
    opts: dict(leading_mode, auto_leading, leading, valign, horigin)
    get_font: callable(font spec) -> fonts.FontData
    """
    res = Layout()
    res.text = "".join(s.text for s in segments)

    # ---- split into lines of pieces (segment, start offset, text)
    lines_pieces = [[]]
    line_props = [segments[0].props if segments else None]
    pos = 0
    for seg in segments:
        if not seg.text:
            # an empty tag, e.g. [title][/title]: an empty line takes its look
            if not lines_pieces[-1]:
                line_props[-1] = seg.props
            continue
        parts = seg.text.split("\n")
        for k, part in enumerate(parts):
            if k > 0:
                lines_pieces.append([])
                line_props.append(seg.props)
                pos += 1
            if part:
                lines_pieces[-1].append((seg, pos, part))
            pos += len(part)

    # ---- shape and advance each line
    word = -1
    in_space = True
    run = -1
    for li, pieces in enumerate(lines_pieces):
        line = Line()
        line.glyphs = []
        lp = pieces[0][0].props if pieces else line_props[li]
        line.align = lp.get("align", "left") if lp else "left"
        pen = 0.0
        prev = None   # (Placed, shaping.Glyph, font, props)
        asc = desc = cap = size = lead = 0.0
        in_space = True

        if not pieces and lp is not None:
            f = get_font(lp["font"])
            s, _sx, sy = _units(lp)
            asc, desc, cap, size = f.ascender * sy, f.descender * sy, f.cap_height * sy, s
            lead = _char_leading(lp, opts)

        for seg, start, part in pieces:
            run += 1
            props = seg.props
            font = get_font(props["font"])
            prev_char = res.text[start - 1] if start > 0 else ""
            txt = shaping.apply_case(part, props.get("case", "none"), prev_char)
            glyphs = shaping.shape(font, txt, start, props, missing)
            s, ux, uy = _units(props)
            track = props.get("track", 0.0) / 1000.0 * s
            shear = math.tan(math.radians(props.get("slant", 0.0)))
            asc = max(asc, font.ascender * uy)
            desc = min(desc, font.descender * uy)
            cap = max(cap, font.cap_height * uy)
            size = max(size, s)
            lead = max(lead, _char_leading(props, opts))

            for g in glyphs:
                # kerning across a style change when font and scale match
                if prev is not None and prev[2] is font and prev[1].kern == 0.0 \
                        and prev[3].get("kern", True) and props.get("kern", True) \
                        and _units(prev[3]) == (s, ux, uy) and prev[0].run != run \
                        and prev[1].scale == g.scale:
                    k = shaping.kerning(font, prev[1].name, g.name) * prev[1].scale * ux
                    pen += k

                p = Placed()
                p.font = font
                p.name = g.name
                p.text = g.text
                p.chars = g.chars
                p.props = props
                p.styles = seg.styles
                p.sx = ux * g.scale
                p.sy = uy * g.scale
                p.shear = shear
                p.x = pen
                p.y = props.get("shift", 0.0)
                p.width = g.advance * p.sx
                p.line = li
                p.run = run
                p.visible = not g.text.isspace()
                if p.visible:
                    if in_space:
                        word += 1
                    in_space = False
                else:
                    in_space = True
                p.word = word if p.visible else -1
                line.glyphs.append(p)
                pen += p.width + track + g.kern * ux
                prev = (p, g, font, props)

        # width excludes trailing whitespace
        vis = [g for g in line.glyphs if g.visible]
        line.width = (vis[-1].x + vis[-1].width) if vis else 0.0
        line.ascent, line.descent, line.cap, line.size = asc, desc, cap, size
        line.leading = lead
        res.lines.append(line)
    res.num_words = word + 1

    # ---- optionally drop lines with nothing visible on them
    if opts.get("skip_empty"):
        kept = [l for l in res.lines if any(g.visible for g in l.glyphs)]
        res.lines = kept or res.lines[:1]
        for li, line in enumerate(res.lines):
            for g in line.glyphs:
                g.line = li

    # ---- vertical placement
    # A line sits its own leading below the previous baseline: the largest
    # leading of any character on it (Illustrator's rule).
    baseline = 0.0
    for i, line in enumerate(res.lines):
        if i > 0:
            if not line.leading:
                line.leading = res.lines[i - 1].leading
            baseline -= line.leading
        line.baseline = baseline

    # ---- horizontal alignment
    # Lines align inside a block as wide as the widest line. The block sits on
    # the origin according to the base alignment, so uniformly aligned text
    # behaves like Illustrator point type and mixed alignments share edges.
    block_w = max([l.width for l in res.lines] or [0.0])
    box = {"center": -block_w * 0.5, "right": -block_w}.get(opts.get("block_align"), 0.0)
    last = len(res.lines) - 1
    for i, line in enumerate(res.lines):
        a = line.align
        if a == "justify":
            if i < last and line.width > 0:
                _justify(line, block_w)
            line.offset = box
        elif a == "center":
            line.offset = box + (block_w - line.width) * 0.5
        elif a == "right":
            line.offset = box + block_w - line.width
        else:
            line.offset = box

    # ---- block bounds and anchoring
    xs = [(l.offset, l.offset + l.width) for l in res.lines if l.width > 0] or [(0.0, 0.0)]
    xmin = min(a for a, _ in xs)
    xmax = max(b for _, b in xs)
    first, lastl = res.lines[0], res.lines[-1]
    top = first.baseline + first.ascent
    bottom = lastl.baseline + lastl.descent

    valign = opts.get("valign", "baseline")
    dy = {"top": -top, "cap": -(first.baseline + first.cap),
          "center": -(top + bottom) * 0.5, "bottom": -bottom}.get(valign, 0.0)
    horigin = opts.get("horigin", "align")
    dx = {"left": -xmin, "center": -(xmin + xmax) * 0.5, "right": -xmax}.get(horigin, 0.0)

    for line in res.lines:
        for g in line.glyphs:
            g.x += line.offset + dx
            g.y += line.baseline + dy
            res.glyphs.append(g)
    res.bbox = (xmin + dx, bottom + dy, xmax + dx, top + dy)
    return res


def _char_leading(props, opts):
    """Character leading; 0 means auto, which follows the Layout tab."""
    lead = props.get("leading", 0.0) or 0.0
    if lead > 0:
        return lead
    if opts.get("leading_mode", "auto") == "fixed":
        return opts.get("leading", 1.2)
    return opts.get("auto_leading", 1.2) * props["size"]


def _justify(line, target):
    extra = target - line.width
    if extra <= 0:
        return
    vis_idx = [i for i, g in enumerate(line.glyphs) if g.visible]
    first, lastv = vis_idx[0], vis_idx[-1]
    inner = line.glyphs[first:lastv + 1]
    # spread across word gaps; letter-space if the line is a single word
    gaps = [i for i in range(1, len(inner)) if inner[i].visible and not inner[i - 1].visible]
    if gaps:
        step = extra / len(gaps)
        shift = 0.0
        gi = set(gaps)
        for i, g in enumerate(inner):
            if i in gi:
                shift += step
            g.x += shift
    elif len(inner) > 1:
        step = extra / (len(inner) - 1)
        for i, g in enumerate(inner):
            g.x += step * i
    for g in line.glyphs[lastv + 1:]:
        g.x += extra
    line.width = target
