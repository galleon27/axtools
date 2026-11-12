"""Inline markup parser for AX Type.

Syntax
    [title]Hello[/title]            apply character style "title"
    [font="Times New Roman" size=2]x[/]   inline overrides, [/] closes the last tag
    [title size=3]x[/]              style plus overrides
    \\[ \\]                          literal brackets

Tags nest. Closing a named tag pops back to (and including) the most recent
tag with that name, so slightly mismatched nesting still behaves.
"""
import re
import shlex


# Property name -> (canonical key, parser)
def _parse_bool(v):
    v = v.strip().lower()
    if v in ("1", "on", "true", "yes", "y"):
        return True
    if v in ("0", "off", "false", "no", "n"):
        return False
    raise ValueError("expected on/off, got %r" % v)


def _parse_color(v):
    v = v.strip()
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            raise ValueError("bad hex color %r" % v)
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    parts = [float(p) for p in re.split(r"[,\s]+", v) if p]
    if len(parts) == 1:
        parts *= 3
    if len(parts) != 3:
        raise ValueError("bad color %r" % v)
    return tuple(parts)


CASES = ("none", "upper", "lower", "title", "smallcaps")
ALIGNS = ("left", "center", "right", "justify")


def _parse_choice(choices):
    def parse(v):
        v = v.strip().lower()
        if v not in choices:
            raise ValueError("expected one of %s, got %r" % ("/".join(choices), v))
        return v
    return parse


def _parse_leading(v):
    """'auto' or 0 = automatic leading, otherwise baseline distance in units."""
    v = v.strip().lower()
    if v == "auto":
        return 0.0
    f = float(v)
    if f < 0:
        raise ValueError("leading must be auto or >= 0, got %r" % v)
    return f


PROPS = {
    "font": ("font", str),
    "size": ("size", float),
    "leading": ("leading", _parse_leading),
    "lead": ("leading", _parse_leading),
    "color": ("color", _parse_color),
    "cd": ("color", _parse_color),
    "alpha": ("alpha", float),
    "track": ("track", float),
    "tracking": ("track", float),
    "shift": ("shift", float),
    "baseline": ("shift", float),
    "hscale": ("hscale", float),
    "vscale": ("vscale", float),
    "slant": ("slant", float),
    "case": ("case", _parse_choice(CASES)),
    "kern": ("kern", _parse_bool),
    "kerning": ("kern", _parse_bool),
    "liga": ("liga", _parse_bool),
    "ligatures": ("liga", _parse_bool),
    "align": ("align", _parse_choice(ALIGNS)),
}


PROP_KEYS = ("font", "size", "leading", "color", "alpha", "track", "shift", "hscale",
             "vscale", "slant", "case", "kern", "liga", "align")


class Segment(object):
    __slots__ = ("text", "props", "styles")

    def __init__(self, text, props, styles):
        self.text = text
        self.props = props      # fully resolved property dict
        self.styles = styles    # tuple of style names active on this segment

    def __repr__(self):
        return "Segment(%r, %s)" % (self.text, "/".join(self.styles) or "-")


_TAG_RE = re.compile(r"\\\[|\\\]|\[([^\[\]]*)\]")


def _parse_tag_body(body, styles, warnings):
    """Return (tag name or None, style name or None, overrides dict)."""
    try:
        tokens = shlex.split(body, posix=True)
    except ValueError:
        tokens = body.split()
    name = None
    style = None
    overrides = {}
    for tok in tokens:
        if "=" in tok:
            key, val = tok.split("=", 1)
            key = key.strip().lower()
            if key not in PROPS:
                warnings.append("Unknown property '%s' in tag [%s]" % (key, body))
                continue
            canon, parser = PROPS[key]
            try:
                overrides[canon] = parser(val)
            except ValueError as e:
                warnings.append("Tag [%s]: %s" % (body, e))
            if name is None:
                name = key
        else:
            if style is not None:
                warnings.append("Tag [%s]: only one style name per tag" % body)
                continue
            style = tok
            name = tok
            if tok not in styles:
                warnings.append("Unknown character style '%s'" % tok)
    return name, style, overrides


def _is_tag(body, styles, stack):
    """Brackets that aren't a known style, property tag or closing tag stay literal."""
    if not body:
        return False
    if body == "/":
        return True
    if body.startswith("/"):
        close = body[1:].strip()
        return any(close in (n, s) for n, s, _ in stack[1:])
    if "=" in body:
        return True
    first = body.split()[0]
    return first in styles


def parse(text, base, styles=None, warnings=None):
    """Split marked-up text into Segments.

    base:   dict with every property (the base style)
    styles: {name: {prop: value}} partial overrides per character style
    """
    styles = styles or {}
    warnings = warnings if warnings is not None else []
    # stack entries: (tag name, style name, resolved props)
    stack = [(None, None, dict(base))]
    segments = []
    buf = []
    # True while the innermost open tag has no text yet. An empty tag still
    # leaves a zero-length segment so an otherwise empty line takes its look.
    empty_tag = [False]

    def flush(closing=False):
        text = "".join(buf)
        if text or (closing and empty_tag[0]):
            names = tuple(s for _, s, _ in stack[1:] if s)
            segments.append(Segment(text, stack[-1][2], names))
        if text:
            empty_tag[0] = False
        del buf[:]

    pos = 0
    for m in _TAG_RE.finditer(text):
        buf.append(text[pos:m.start()])
        pos = m.end()
        tok = m.group(0)
        if tok == "\\[":
            buf.append("[")
            continue
        if tok == "\\]":
            buf.append("]")
            continue
        body = m.group(1).strip()
        if not _is_tag(body, styles, stack):
            buf.append(tok)
            continue
        if body.startswith("/"):
            flush(closing=len(stack) > 1)
            empty_tag[0] = False
            close = body[1:].strip()
            if len(stack) == 1:
                warnings.append("Closing tag [%s] without opening tag" % body)
                continue
            if not close:
                stack.pop()
                continue
            for i in range(len(stack) - 1, 0, -1):
                if stack[i][0] == close or stack[i][1] == close:
                    del stack[i:]
                    break
            else:
                warnings.append("Closing tag [%s] does not match an open tag" % body)
            continue
        name, style, overrides = _parse_tag_body(body, styles, warnings)
        flush()
        props = dict(stack[-1][2])
        if style:
            props.update(styles.get(style, {}))
        props.update(overrides)
        stack.append((name, style, props))
        empty_tag[0] = True
    buf.append(text[pos:])
    flush()
    return segments


# --------------------------------------------------------------------------
# Flat runs <-> markup (used by the editor)
# --------------------------------------------------------------------------

def values_equal(a, b):
    if isinstance(a, (tuple, list)) or isinstance(b, (tuple, list)):
        try:
            return len(a) == len(b) and all(abs(x - y) < 1e-6 for x, y in zip(a, b))
        except TypeError:
            return False
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(a - b) < 1e-6
        except TypeError:
            return False
    return a == b


def resolve_props(base, styles, style=None, overrides=None):
    props = dict(base)
    if style:
        props.update(styles.get(style, {}))
    if overrides:
        props.update(overrides)
    return props


def prune(props, base, styles, style=None, keep_align=False):
    """Overrides needed on top of base+style to reach props."""
    ref = resolve_props(base, styles, style)
    return {k: props[k] for k in PROP_KEYS
            if k in props and (keep_align or k != "align") and not values_equal(props[k], ref.get(k))}


def char_runs(segments, base, styles):
    """Flatten segments to lines: [(align, [(text, style, overrides), ...]), ...].

    The style is the innermost active style; everything else that differs from
    base+style becomes an override. Alignment is per line, as layout uses it.
    """
    # [align of first visible text, runs, align of the segment holding the newline]
    default = base.get("align", "left")
    first = segments[0].props.get("align", default) if segments else default
    lines = [[None, [], first]]
    for seg in segments:
        style = seg.styles[-1] if seg.styles else None
        if style not in styles:
            style = None
        ovr = prune(seg.props, base, styles, style)
        if not seg.text:
            # an empty tag on an empty line: keep it as a placeholder run so the
            # line keeps its look ([title][/title])
            if not lines[-1][1] and (style or ovr):
                lines[-1][1].append(("", style, ovr))
            continue
        for k, part in enumerate(seg.text.split("\n")):
            if k > 0:
                lines.append([None, [], seg.props.get("align", default)])
            if part and lines[-1][0] is None:
                lines[-1][0] = seg.props.get("align", default)
            if part:
                runs = lines[-1][1]
                if len(runs) == 1 and not runs[0][0]:
                    del runs[:]   # the line has text after all; drop the placeholder
                if runs and runs[-1][1] == style and runs[-1][2] == ovr:
                    runs[-1] = (runs[-1][0] + part, style, ovr)
                else:
                    runs.append((part, style, ovr))
    return [(a or empty_align, runs) for a, runs, empty_align in lines]


def format_value(key, v):
    if key == "color":
        ints = [round(c * 255) for c in v]
        if all(abs(i / 255.0 - c) < 1e-6 and 0 <= i <= 255 for i, c in zip(ints, v)):
            return "#%02x%02x%02x" % tuple(ints)
        return ",".join("%g" % c for c in v)
    if key == "leading" and v <= 0:
        return "auto"
    if key in ("kern", "liga"):
        return "on" if v else "off"
    if isinstance(v, float):
        return "%g" % v
    v = str(v)
    if not v or re.search(r"[\s\"'\[\]=]", v):
        return '"%s"' % v.replace('"', "'")
    return v


def _escape(text):
    return text.replace("[", "\\[").replace("]", "\\]")


def serialize(lines, base_align="left"):
    """Inverse of char_runs: lines of (align, runs) -> markup string."""
    out = []
    for align, runs in lines:
        merged = []
        # a line holding only an empty run keeps it, as an empty tag
        empty = [r for r in runs if not r[0] and (r[1] or r[2])]
        if empty and not any(r[0] for r in runs):
            runs = empty[:1]
        for text, style, ovr in runs:
            if not text and len(runs) > 1:
                continue
            if merged and merged[-1][1] == style and merged[-1][2] == ovr:
                merged[-1] = (merged[-1][0] + text, style, ovr)
            else:
                merged.append((text, style, dict(ovr)))
        parts = []
        for i, (text, style, ovr) in enumerate(merged):
            ovr = {k: v for k, v in ovr.items() if k != "align"}
            if i == 0 and align and align != base_align:
                ovr["align"] = align
            kv = " ".join("%s=%s" % (k, format_value(k, ovr[k])) for k in PROP_KEYS if k in ovr)
            if style and kv:
                parts.append("[%s %s]%s[/]" % (style, kv, _escape(text)))
            elif style:
                parts.append("[%s]%s[/%s]" % (style, _escape(text), style))
            elif kv:
                parts.append("[%s]%s[/]" % (kv, _escape(text)))
            else:
                parts.append(_escape(text))
        out.append("".join(parts))
    return "\n".join(out)
