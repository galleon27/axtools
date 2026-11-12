"""AX Type: Illustrator-style rich text for Houdini.

Called from the Python SOP inside the AX_type HDA:
    import ax_type; ax_type.cook(hou.pwd())
"""
import importlib
import sys

from . import _ft_compat  # noqa: F401
from . import fonts, markup, shaping, layout, build, arrays

# Character style properties: (prop key, parm suffix, kind)
STYLE_PROPS = (
    ("font", "font", "str"),
    ("size", "size", "float"),
    ("leading", "clead", "float"),   # 'leading' is taken by the Layout tab
    ("color", "color", "color"),
    ("alpha", "alpha", "float"),
    ("track", "track", "float"),
    ("shift", "shift", "float"),
    ("hscale", "hscale", "float"),
    ("vscale", "vscale", "float"),
    ("slant", "slant", "float"),
    ("case", "case", "menu"),
    ("kern", "kern", "toggle"),
    ("liga", "liga", "toggle"),
    ("align", "align", "menu"),
)


PROP_DEFAULTS = {"leading": 0.0}


def _eval(node, name, kind):
    """Parm value, or None if this version of the HDA doesn't have the parm."""
    if kind == "color":
        pt = node.parmTuple(name)
        return tuple(pt.eval()) if pt is not None else None
    p = node.parm(name)
    if p is None:
        return None
    if kind == "menu":
        return p.evalAsString()
    if kind == "toggle":
        return bool(p.eval())
    return p.eval()


def _extra_dirs(hda):
    p = hda.parm("fontdirs")
    raw = p.eval() if p else ""
    return [d.strip() for d in raw.replace(";", "\n").splitlines() if d.strip()]


def read_styles(hda):
    styles = {}
    p = hda.parm("styles")
    if p is None:
        return styles
    for i in range(1, p.eval() + 1):
        name = hda.parm("sname%d" % i).eval().strip()
        if not name:
            continue
        d = {}
        for key, suffix, kind in STYLE_PROPS:
            use = hda.parm("suse_%s%d" % (suffix, i))
            if use is not None and use.eval():
                v = _eval(hda, "s%s%d" % (suffix, i), kind)
                if v is not None:
                    d[key] = v
        styles[name] = d
    return styles


def read_base(hda):
    base = {}
    for key, suffix, kind in STYLE_PROPS:
        v = _eval(hda, suffix, kind)
        base[key] = v if v is not None else PROP_DEFAULTS.get(key)
    base["smallcaps_scale"] = hda.parm("smallcaps").eval()
    return base


def _warn(node, msg):
    try:
        node.addWarning(msg)
    except Exception:
        pass


def cook(node, hda=None):
    import hou

    hda = hda or node.parent()
    geo = node.geometry()
    geo.clear()

    warnings = []
    extra = _extra_dirs(hda)
    base = read_base(hda)
    styles = read_styles(hda)
    text = arrays.text_with_arrays(hda)
    segments = markup.parse(text, base, styles, warnings)
    if not segments:
        return

    resolved = {}

    def get_font(spec):
        fd = resolved.get(spec)
        if fd is not None:
            return fd
        r = fonts.resolve(spec, extra)
        if r is None:
            fb = fonts.fallback_font_path()
            if fb is None:
                raise hou.NodeError("Font '%s' not found and no fallback font available" % spec)
            warnings.append("Font '%s' not found, using DejaVu Sans" % spec)
            r = (fb, 0)
        try:
            fd = fonts.load(*r)
        except Exception as e:
            raise hou.NodeError("Could not load font '%s' (%s): %s" % (spec, r[0], e))
        resolved[spec] = fd
        return fd

    missing = set()
    opts = {
        "leading_mode": hda.parm("leadmode").evalAsString(),
        "auto_leading": hda.parm("autolead").eval(),
        "leading": hda.parm("leading").eval(),
        "valign": hda.parm("valign").evalAsString(),
        "horigin": hda.parm("horigin").evalAsString(),
        "block_align": base["align"],
        "skip_empty": bool(hda.parm("skipempty").eval()) if hda.parm("skipempty") else False,
    }
    lay = layout.layout(segments, opts, get_font, missing)
    if missing:
        warnings.append("Characters missing from font: %s" % " ".join(sorted(missing)))

    build.write(geo, lay, {
        "output": hda.parm("output").eval(),
        "tolerance": hda.parm("tolerance").eval(),
        "scale": hda.parm("scale").eval(),
        "t": tuple(hda.parmTuple("t").eval()),
        "pack": hda.parm("packby").eval(),
    })

    for w in dict.fromkeys(warnings):
        _warn(node, w)


def font_menu(hda=None):
    """Menu script helper for font parms."""
    extra = _extra_dirs(hda) if hda is not None else []
    return fonts.menu_items(extra)


def dev_reload():
    """Reload all submodules (font caches are dropped too). Returns the package."""
    for name in ("fonts", "markup", "shaping", "layout", "build", "arrays"):
        importlib.reload(sys.modules[__name__ + "." + name])
    pkg = importlib.reload(sys.modules[__name__])
    if __name__ + ".editor" in sys.modules:  # only loaded once the editor was opened
        importlib.reload(sys.modules[__name__ + ".editor"])
    return pkg


def rebuild_font_index(hda=None):
    extra = _extra_dirs(hda) if hda is not None else []
    fonts.clear_cache()
    fonts.build_index(extra, force=True)
    build._face_cache.clear()
