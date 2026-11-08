"""Builds otls/AX_type.hdalc.

Run from a Houdini 22 Python shell:
    exec(open(r"<axtools>/scripts/python/build_ax_type_hda.py").read())
or from hython:
    hython build_ax_type_hda.py
The layout engine lives in python3.13libs/ax_type; the HDA only holds the UI.
"""
import os
import sys

import hou

if "__file__" in globals():
    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
else:  # exec() from the Python shell
    ROOT = os.environ.get("AXTOOLS", r"S:\Galerija_12_Digital_Tools\Assets\Houdini\axtools")
HDA_PATH = os.path.join(ROOT, "otls", "AX_type.hdalc").replace("\\", "/")
LIBS = os.path.join(ROOT, "python3.13libs")
if LIBS not in sys.path:
    sys.path.insert(0, LIBS)

import ax_type  # noqa: E402

PYTHON_SOP_CODE = '''import ax_type
node = hou.pwd()
if node.parent().evalParm("devreload"):
    ax_type = ax_type.dev_reload()
ax_type.cook(node)
'''

FONT_MENU = '''import ax_type
return ax_type.font_menu(kwargs["node"])
'''

ON_CREATED = '''node = kwargs["node"]
node.parm("styles").set(1)
for name, value in (("sname1", "title"),
                    ("suse_font1", 1), ("sfont1", "Arial Black"),
                    ("suse_size1", 1), ("ssize1", 1.6),
                    ("suse_track1", 1), ("strack1", 20)):
    node.parm(name).set(value)
'''

DEFAULT_TEXT = ('AX [title]TYPE[/title]\n'
                'per word [font="Times New Roman Italic" color=#ff5a1f]fonts[/] '
                'and [size=0.6 track=200]sizes[/]')

HELP = r'''= AX Type =

#icon: SOP/font

"""Illustrator-style text: per-word fonts, sizes, colors and tracking via inline tags and character styles."""

== Editing text ==

Click __Edit Text...__ to open the editor. Type as usual, select some text, then:

* Pick a __Style__ from the Character Styles tab, or set __Font__, __Size__, __Track__, __Shift__, __Color__ or __Case__. Only the property you change is overridden.
* __Left/Center/Right/Justify__ align the selected lines.
* __New Style from Selection__ saves the selection's look as a new character style and applies it.
* __Clear Format__ removes the style and overrides from the selection.

With __Live__ on, the node updates while you edit; with it off, click __Apply__ to push your changes to the node without closing the editor. __OK__ keeps the changes, __Cancel__ restores the text from when the editor opened, and __Reload__ re-reads the node if you edited the Text parm by hand. The editor writes the tag markup described below, so you can still fine-tune it by hand.

== Markup ==

Tags in the Text field style the text they wrap. Tags nest.

:box:
    `The [title]QUICK[/title] brown fox` - apply the character style named _title_.

    `[font="Times New Roman Italic" size=2 color=#ff5a1f]fox[/]` - inline overrides; `[/]` closes the last tag.

    `[title track=200]X[/]` - a style plus overrides.

    `\[` and `\]` - literal brackets. Brackets that are not a known style or `key=value` tag are kept as text.

Properties usable in tags (and in Character Styles):

`font`: Full font name (`Arial Bold`), family (`Arial`) or file path. Collections accept `path.ttc,1`.
`size`: Font size (em height in Houdini units).
`color` / `cd`: `#rrggbb`, `#rgb` or `r,g,b`.
`alpha`: Alpha.
`track` / `tracking`: Tracking in 1/1000 em, like Illustrator.
`shift` / `baseline`: Baseline shift in units.
`hscale`, `vscale`: Horizontal / vertical scale multipliers.
`slant`: Faux italic angle in degrees.
`case`: `none`, `upper`, `lower`, `title`, `smallcaps` (uses the font's real small caps when available).
`kern`, `liga`: `on`/`off` - font kerning and standard ligatures.
`align`: `left`, `center`, `right`, `justify` - applies to the line the tag starts on.

== Output attributes ==

Primitive attributes (point attributes in Points mode): `char`, `glyph`, `font`, `style`, `char_index`, `glyph_index`, `word_index`, `line_index`, `run_index`, `glyph_u`, `word_u`, `line_u`, `size`, `Cd`, `Alpha`. Curves add `ishole`; Points add `advance` and `pscale`. Detail: `text`, `num_glyphs`, `num_words`, `num_lines`, `text_bounds` (xmin, ymin, xmax, ymax).

@parameters

Edit Text:
    Opens the rich text editor for this node.
Text:
    Text with optional style tags. Use new lines for multiple lines.
Output:
    Faces (filled, with holes), Curves (outlines) or Points (one per glyph at its baseline origin).
Curve Tolerance:
    Maximum deviation in units when converting glyph curves to polygons.
Align:
    Base line alignment around the origin, like Illustrator point type. Lines are aligned inside a block as wide as the widest line, so lines with a different `[align=...]` tag share the block's edges. Justify stretches every line except the last to the block width.
Vertical Origin:
    Which part of the text block sits at the origin.
Horizontal Origin:
    Alignment keeps the origin where alignment puts it (like Illustrator point type); the others move the block bounds.
Leading:
    Auto uses the largest size on each line times Auto Leading; Fixed uses a constant baseline distance.
Pack:
    Pack per glyph, word or line into packed primitives with a `name` attribute, for animation.
Font:
    Base font. Full name, family name or a file path.
Character Styles:
    Named styles referenced by tags. Only enabled properties override the surrounding style.
Extra Font Folders:
    Additional folders to search for fonts (one per line).
Rebuild Font Index:
    Rescan font folders after installing new fonts.
'''

CASE_MENU = (("none", "upper", "lower", "title", "smallcaps"),
             ("Normal", "ALL CAPS", "lowercase", "Title Case", "Small Caps"))
ALIGN_MENU = (("left", "center", "right", "justify"), ("Left", "Center", "Right", "Justify"))


def _font_parm(name, label, default="Arial"):
    return hou.StringParmTemplate(
        name, label, 1, default_value=(default,),
        menu_type=hou.menuType.StringReplace,
        item_generator_script=FONT_MENU,
        item_generator_script_language=hou.scriptLanguage.Python)


def _prop_parms(prefix, suffix=""):
    """Character properties. prefix '' for base style, 's' for style instances."""
    n = lambda k: "%s%s%s" % (prefix, k, suffix)
    return [
        _font_parm(n("font"), "Font"),
        hou.FloatParmTemplate(n("size"), "Size", 1, default_value=(1.0,), min=0.01, max=10),
        hou.FloatParmTemplate(n("color"), "Color", 3, default_value=(1, 1, 1),
                              naming_scheme=hou.parmNamingScheme.RGBA,
                              look=hou.parmLook.ColorSquare),
        hou.FloatParmTemplate(n("alpha"), "Alpha", 1, default_value=(1.0,), min=0, max=1),
        hou.FloatParmTemplate(n("track"), "Tracking", 1, default_value=(0.0,), min=-200, max=1000,
                              help="1/1000 em"),
        hou.FloatParmTemplate(n("shift"), "Baseline Shift", 1, default_value=(0.0,), min=-1, max=1),
        hou.FloatParmTemplate(n("hscale"), "Horizontal Scale", 1, default_value=(1.0,), min=0.1, max=3),
        hou.FloatParmTemplate(n("vscale"), "Vertical Scale", 1, default_value=(1.0,), min=0.1, max=3),
        hou.FloatParmTemplate(n("slant"), "Slant", 1, default_value=(0.0,), min=-30, max=30),
        hou.MenuParmTemplate(n("case"), "Case", CASE_MENU[0], CASE_MENU[1], default_value=0,
                             store_default_value_as_string=True),
        hou.ToggleParmTemplate(n("kern"), "Kerning", default_value=True),
        hou.ToggleParmTemplate(n("liga"), "Ligatures", default_value=True),
    ]


def _style_instance_parms():
    parms = [hou.StringParmTemplate("sname#", "Style Name", 1, default_value=("style#",))]
    props = _prop_parms("s", "#")
    align = hou.MenuParmTemplate("salign#", "Align", ALIGN_MENU[0], ALIGN_MENU[1])
    props.append(align)
    for p in props:
        key = p.name()[1:-1]
        use = hou.ToggleParmTemplate("suse_%s#" % key, "", default_value=False)
        use.setJoinWithNext(True)
        p.setConditional(hou.parmCondType.DisableWhen, "{ suse_%s# == 0 }" % key)
        parms += [use, p]
    return parms


def build_ptg():
    ptg = hou.ParmTemplateGroup()

    ptg.append(hou.ButtonParmTemplate(
        "edittext", "Edit Text...",
        script_callback='import ax_type.editor; ax_type.editor.open_editor(kwargs["node"])',
        script_callback_language=hou.scriptLanguage.Python,
        help="Open the rich text editor: select text and set styles, fonts, sizes and colors"))
    text = hou.StringParmTemplate("text", "Text", 1, default_value=(DEFAULT_TEXT,))
    text.setTags({"editor": "1", "editorlines": "4-20"})
    ptg.append(text)
    ptg.append(hou.MenuParmTemplate("output", "Output", ("faces", "curves", "points"),
                                    ("Faces", "Curves", "Points")))
    tol = hou.FloatParmTemplate("tolerance", "Curve Tolerance", 1, default_value=(0.002,),
                                min=0.0001, max=0.02)
    tol.setConditional(hou.parmCondType.DisableWhen, "{ output == points }")
    ptg.append(tol)

    lay = hou.FolderParmTemplate("f_layout", "Layout", folder_type=hou.folderType.Tabs)
    lay.addParmTemplate(hou.MenuParmTemplate("align", "Align", ALIGN_MENU[0], ALIGN_MENU[1],
                                             store_default_value_as_string=True))
    lay.addParmTemplate(hou.MenuParmTemplate(
        "valign", "Vertical Origin", ("baseline", "top", "cap", "center", "bottom"),
        ("First Baseline", "Top", "Cap Height", "Center", "Bottom"),
        store_default_value_as_string=True))
    lay.addParmTemplate(hou.MenuParmTemplate(
        "horigin", "Horizontal Origin", ("align", "left", "center", "right"),
        ("From Alignment", "Left", "Center", "Right"), store_default_value_as_string=True))
    lay.addParmTemplate(hou.SeparatorParmTemplate("sep1"))
    lay.addParmTemplate(hou.MenuParmTemplate("leadmode", "Leading", ("auto", "fixed"),
                                             ("Auto", "Fixed"), store_default_value_as_string=True))
    al = hou.FloatParmTemplate("autolead", "Auto Leading", 1, default_value=(1.2,), min=0.5, max=3)
    al.setConditional(hou.parmCondType.HideWhen, "{ leadmode == fixed }")
    lay.addParmTemplate(al)
    fl = hou.FloatParmTemplate("leading", "Leading", 1, default_value=(1.2,), min=0, max=5)
    fl.setConditional(hou.parmCondType.HideWhen, "{ leadmode == auto }")
    lay.addParmTemplate(fl)
    lay.addParmTemplate(hou.SeparatorParmTemplate("sep2"))
    lay.addParmTemplate(hou.FloatParmTemplate("t", "Translate", 3))
    lay.addParmTemplate(hou.FloatParmTemplate("scale", "Uniform Scale", 1, default_value=(1.0,),
                                              min=0, max=10))
    pk = hou.MenuParmTemplate("packby", "Pack", ("none", "glyph", "word", "line"),
                              ("Off", "Per Glyph", "Per Word", "Per Line"))
    pk.setConditional(hou.parmCondType.DisableWhen, "{ output == points }")
    lay.addParmTemplate(pk)
    ptg.append(lay)

    ch = hou.FolderParmTemplate("f_char", "Character", folder_type=hou.folderType.Tabs)
    for p in _prop_parms(""):
        ch.addParmTemplate(p)
    sc = hou.FloatParmTemplate("smallcaps", "Faux Small Caps Scale", 1, default_value=(0.75,),
                               min=0.3, max=1,
                               help="Used when the font has no real small caps")
    ch.addParmTemplate(sc)
    ptg.append(ch)

    st = hou.FolderParmTemplate("f_styles", "Character Styles", folder_type=hou.folderType.Tabs)
    multi = hou.FolderParmTemplate("styles", "Styles", folder_type=hou.folderType.MultiparmBlock,
                                   default_value=0)
    for p in _style_instance_parms():
        multi.addParmTemplate(p)
    st.addParmTemplate(multi)
    ptg.append(st)

    fo = hou.FolderParmTemplate("f_fonts", "Fonts", folder_type=hou.folderType.Tabs)
    dirs = hou.StringParmTemplate("fontdirs", "Extra Font Folders", 1,
                                  string_type=hou.stringParmType.FileReference,
                                  file_type=hou.fileType.Directory)
    fo.addParmTemplate(dirs)
    fo.addParmTemplate(hou.ButtonParmTemplate(
        "rebuild", "Rebuild Font Index",
        script_callback='import ax_type; ax_type.rebuild_font_index(kwargs["node"]); '
                        'kwargs["node"].node("layout").cook(force=True)',
        script_callback_language=hou.scriptLanguage.Python))
    fo.addParmTemplate(hou.ToggleParmTemplate("devreload", "Reload Module on Cook (dev)",
                                              default_value=False))
    ptg.append(fo)
    return ptg


def build(path=HDA_PATH):
    if os.path.isfile(path):
        try:
            hou.hda.uninstallFile(path)
        except hou.OperationFailed:
            pass  # not installed in this session
        os.remove(path)

    geo = hou.node("/obj").createNode("geo", "ax_type_build")
    sub = geo.createNode("subnet", "AX_type")
    py = sub.createNode("python", "layout")
    py.parm("python").set(PYTHON_SOP_CODE)

    pack = sub.createNode("pack", "pack")
    pack.setInput(0, py)
    pack.parm("packbyname").set(1)
    pack.parm("transfer_attributes").set("*")

    sw = sub.createNode("switch", "pack_switch")
    sw.setInput(0, py)
    sw.setInput(1, pack)
    sw.parm("input").setExpression('ch("../packby") > 0 && ch("../output") != 2')

    out = sub.createNode("output", "output0")
    out.setInput(0, sw)
    out.setDisplayFlag(True)
    out.setRenderFlag(True)
    sub.layoutChildren()

    node = sub.createDigitalAsset(name="AX_type", hda_file_name=path, description="AX Type",
                                  min_num_inputs=0, max_num_inputs=0, ignore_external_references=True)
    defn = node.type().definition()
    defn.setParmTemplateGroup(build_ptg())
    defn.setIcon("SOP_font")
    defn.addSection("OnCreated", ON_CREATED)
    defn.setExtraFileOption("OnCreated/IsPython", True)
    defn.addSection("HelpText", HELP)
    defn.setComment("Illustrator-style rich text: per-word fonts, sizes, colors, tracking.")
    node.matchCurrentDefinition()
    geo.destroy()
    return path


if __name__ == "__main__":
    print("Saved", build())
