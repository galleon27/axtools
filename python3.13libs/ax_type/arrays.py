"""Array attributes in the Text parm.

Houdini's points(), prims() and details() expressions return an empty string
for array attributes. When the Text parm holds one of those calls with literal
arguments, e.g. `points("../foreach_begin1", 0, "titles")`, and the attribute
is an array, AX Type reads it itself and puts each item on its own line.
Everything else in the text is evaluated by Houdini exactly as before.
"""
import re

import hou

_CALL_RE = re.compile(
    r"(?<!\\)`\s*(points|prims|details)\s*\(\s*"
    r"(['\"])(?P<path>.*?)\2\s*,\s*"
    r"(?:(?P<index>\d+)\s*,\s*)?"
    r"(['\"])(?P<attr>[A-Za-z_][A-Za-z0-9_]*)\5\s*\)\s*`")

# private-use characters: never typed by hand, untouched by Houdini expansion
_MARK = u"%d"


def _fmt(v):
    return "%g" % v if isinstance(v, float) else str(v)


def _array_value(hda, func, path, index, attr):
    """Items joined by newlines if the call reads an array attribute, else None."""
    if (func == "details") != (index is None):
        return None
    node = hda.node(path)
    if node is None:
        return None
    try:
        geo = node.geometry()
    except (hou.Error, AttributeError):
        return None
    if geo is None:
        return None
    if func == "details":
        a, elem = geo.findGlobalAttrib(attr), geo
    else:
        if func == "points":
            a, elem = geo.findPointAttrib(attr), geo.point(int(index))
        else:
            a, elem = geo.findPrimAttrib(attr), geo.prim(int(index))
    if a is None or elem is None or not a.isArrayType():
        return None
    v = elem.attribValue(attr)
    size = max(a.size(), 1)
    if size > 1:   # array of vectors: one tuple per line
        return "\n".join(" ".join(_fmt(c) for c in v[i:i + size]) for i in range(0, len(v), size))
    return "\n".join(_fmt(x) for x in v)


def text_with_arrays(hda):
    """The evaluated Text parm, with array attributes expanded to one item per line."""
    parm = hda.parm("text")
    evaluated = parm.eval()   # also keeps Houdini's dependencies on referenced nodes
    try:
        raw = parm.unexpandedString()
    except hou.OperationFailed:   # keyframed or expression-driven parm
        return evaluated

    values = []

    def repl(m):
        v = _array_value(hda, m.group(1), m.group("path"), m.group("index"), m.group("attr"))
        if v is None:
            return m.group(0)
        values.append(v)
        return _MARK % (len(values) - 1)

    marked = _CALL_RE.sub(repl, raw)
    if not values:
        return evaluated   # no array attributes: exactly Houdini's own result

    prev = hou.pwd()
    hou.setPwd(hda)   # relative paths in the remaining expressions resolve from the HDA
    try:
        text = hou.text.expandString(marked)
    finally:
        hou.setPwd(prev)
    for i, v in enumerate(values):
        text = text.replace(_MARK % i, v)
    return text
