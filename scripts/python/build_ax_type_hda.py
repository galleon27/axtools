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

#type: node
#context: sop
#internal: AX_type
#icon: SOP/font
#tags: text, font, type, typography, motion design

"""Point type with per-character fonts, sizes, colors, tracking and leading, set through character styles, inline tags or a rich text editor."""

== Overview ==

The Font SOP applies one font, size and color to the whole string. AX Type lets
each word or letter have its own: font, size, color, alpha, tracking, leading,
baseline shift, horizontal and vertical scale, slant and case. Looks you reuse
can be saved as named character styles.

Formatting is stored as tags in the Text parameter (`The [title]QUICK[/title] fox`).
You don't have to type them. __Edit Text...__ opens an editor where you select
text and pick a style, font or color, and the editor writes the tags for you.

Text is shaped with the font's kerning and standard ligatures, and uses the
font's real small caps when it has them. The output is filled faces, outline
curves or one point per glyph, with per glyph, word and line attributes for
animation.

== Quick start ==

# Put down an AX Type node. It starts with sample text and a `title` style.
# Click __Edit Text...__, type your text, select a word and pick a __Style__,
  __Font__, __Size__ or __Color__.
# Click __OK__.
# Set __Output__ to Faces, Curves or Points, and set the base look on the
  __Character__ tab.
# Save looks you want to reuse on the __Character Styles__ tab, then apply them
  from the editor.

== Editing text ==

Click __Edit Text...__, type as usual, then select some text and:

* pick a __Style__, or set __Font__, __Size__, __Leading__, __Track__,
  __Shift__, __Color__ or __Case__. Only the property you change is overridden.
* click __Left__, __Center__, __Right__ or __Justify__ to align the selected
  lines.
* click __New Style from Selection__ to save the selection's look as a new
  character style and apply it.
* click __Clear Format__ to remove the style and overrides from the selection.

The buttons along the bottom:

* __Live__: update the node as you type. Off by default.
* __Apply__: write the text to the node without closing the editor (when Live
  is off).
* __Reload__: re-read the node, for when you changed the Text parameter by hand.
* __OK__ keeps your changes. __Cancel__ puts back the text the node had when the
  editor opened.

The editor preview is approximate. Check the result in the viewport. You can
still edit the tags in the Text parameter directly.

== Character styles ==

Each entry on the __Character Styles__ tab has a __Style Name__ and a list of
properties, each with a toggle. Only properties that are toggled on take effect.
The rest come from the surrounding text: the __Character__ tab, or an outer tag.
That is why styles can nest and layer.

Apply a style with a tag of the same name (`[title]...[/title]`) or from the
editor's __Style__ menu. Edit the style and all text that uses it updates.

New nodes come with one style, `title` (Arial Black, size 1.6, tracking 20).

== Markup ==

A tag styles the text it wraps. Tags can nest.

:box:
    `The [title]QUICK[/title] brown fox`: applies the character style `title`.

    `[font="Times New Roman Italic" size=2 color=#ff5a1f]fox[/]`: inline
    overrides. `[/]` closes the most recent tag.

    `[title track=200]X[/]`: a style with an override on top.

    `\[` and `\]`: literal brackets. A bracket that isn't a known style name or a
    `key=value` tag is also left as text.

Properties you can use in tags and character styles:

`font`: Full font name (`Arial Bold`), family (`Arial`) or file path. For a font inside a collection, use `path.ttc,1`.
`size`: Font size, as em height in Houdini units.
`leading` / `lead`: Baseline distance for the lines this text is on, in units. `auto` uses the Layout tab setting.
`color` / `cd`: `#rrggbb`, `#rgb` or `r,g,b`.
`alpha`: Alpha.
`track` / `tracking`: Tracking in 1/1000 em, as in Illustrator.
`shift` / `baseline`: Baseline shift in units.
`hscale`, `vscale`: Horizontal and vertical scale multipliers.
`slant`: Faux italic angle in degrees.
`case`: `none`, `upper`, `lower`, `title` or `smallcaps`. `smallcaps` uses the font's real small caps when it has them.
`kern` / `kerning`, `liga` / `ligatures`: `on` or `off`. Turns font kerning and standard ligatures on or off.
`align`: `left`, `center`, `right` or `justify`. Applies to the line the tag starts on.

== Layout ==

AX Type sets point type: a line ends only where you type a new line. It does not
wrap text into a box or place it on a path.

All lines are aligned within a block as wide as the longest line, so lines with
different alignment still share the block's edges. Justify stretches each line
except the last to that width.

__Vertical Origin__ and __Horizontal Origin__ choose which point of the block
sits at the origin. __Translate__ and __Uniform Scale__ then move and scale the
finished block.

Line spacing comes from __Leading__ on the Layout tab, unless the text on a line
has its own leading (from a `leading=` tag, a style, or the Character tab).

== Fonts ==

AX Type searches for fonts in this order:

* __Extra Font Folders__ on the Fonts tab
* folders in `HOUDINI_FONT_PATH`
* Houdini's fonts in `$HFS/houdini/fonts`
* system fonts. On Windows that is `C:\Windows\Fonts` and the per-user
  `%LOCALAPPDATA%\Microsoft\Windows\Fonts`. On macOS and Linux it is the
  standard system and user font folders.

It reads `.ttf`, `.otf`, `.ttc` and `.otc` files. Fonts can be given by full
name, family name or file path.

The list of fonts found is cached so the font menus open quickly. Click
__Rebuild Font Index__ after installing fonts or adding a folder.

If a font can't be found, the node warns and falls back to DejaVu Sans. Any
characters missing from a font are listed in a warning as well.

== Output attributes ==

Primitive attributes (point attributes in Points mode): `char`, `glyph`, `font`,
`style`, `char_index`, `glyph_index`, `word_index`, `line_index`, `run_index`,
`glyph_u`, `word_u`, `line_u`, `size`, `Cd`, `Alpha`.

Curves output adds `ishole`. Points output adds `advance` and `pscale`.

Detail attributes: `text`, `num_glyphs`, `num_words`, `num_lines`, and
`text_bounds` (xmin, ymin, xmax, ymax).

The `*_u` attributes run from 0 to 1 across the glyphs, words or lines.

== Tips ==

* To animate per letter or word, set __Pack__ and animate the packed primitives
  by `name`. Or drive a wrangle or Transform Pieces from the `*_index` and `*_u`
  attributes.
* Use __Points__ output with Copy to Points. Each point sits at its glyph's
  baseline origin, with `pscale` set to the size and `advance` to the glyph
  width.
* Use __Curves__ output to extrude or sweep outlines. `ishole` marks the
  counters (the holes in o, e, a).
* Put repeated looks in character styles rather than inline tags, so you can
  change them in one place.

@parameters

Edit Text:
    Opens the rich text editor.
Text:
    The text, with optional style tags. Press Enter to start a new line.
Output:
    Faces: filled polygons with holes. Curves: glyph outlines. Points: one point
    per glyph at its baseline origin.
Curve Tolerance:
    Maximum distance, in units, between the glyph curves and the polygons made
    from them. Lower values give smoother, heavier geometry. Not used for Points.

== Layout ==

Align:
    Default alignment for every line. Lines are aligned within a block as wide as
    the longest line. Justify stretches each line except the last to that width.
    An `[align=...]` tag overrides this for its line.
Vertical Origin:
    Which part of the block sits at the origin: the first baseline, the top, the
    cap height of the first line, the center or the bottom.
Horizontal Origin:
    From Alignment places the origin where the alignment puts it, as with
    Illustrator point type. Left, Center and Right put that edge or the center of
    the block on the origin.
Leading:
    Auto: the largest size on each line times Auto Leading. Fixed: a constant
    baseline distance. Character leading overrides either for its lines.
Auto Leading:
    Line spacing as a multiple of the largest size on the line.
Leading (Fixed):
    Baseline distance in units.
Translate:
    Moves the finished block.
Uniform Scale:
    Scales the finished block.
Pack:
    Packs each glyph, word or line into a packed primitive with a `name`
    attribute. Not used for Points.

== Character ==

The base look for all text. Tags and character styles override it.

Font:
    Full font name, family name or file path.
Size:
    Font size, as em height in Houdini units.
Color:
    Text color, written to `Cd`.
Alpha:
    Text alpha, written to `Alpha`.
Tracking:
    Extra space between letters, in 1/1000 em.
Leading:
    Baseline distance for the lines this text is on. 0 means Auto, which uses the
    Layout tab setting.
Baseline Shift:
    Moves text up or down from the baseline, in units.
Horizontal Scale:
    Horizontal glyph scale.
Vertical Scale:
    Vertical glyph scale.
Slant:
    Faux italic angle in degrees.
Case:
    Normal, ALL CAPS, lowercase, Title Case or Small Caps.
Kerning:
    Uses the font's kerning.
Ligatures:
    Uses the font's standard ligatures (fi, fl).
Faux Small Caps Scale:
    Size of faux small caps, used when the font has no real small caps.

== Character Styles ==

Styles:
    Named styles that tags refer to. Each property has a toggle, and only toggled
    properties override the surrounding text. A style can also set line
    alignment.

== Fonts ==

Extra Font Folders:
    More folders to search for fonts, one per line.
Rebuild Font Index:
    Rescans the font folders. Use after installing fonts.
Reload Module on Cook (dev):
    Reloads the `ax_type` Python module on every cook. Only for development of
    the module. Leave it off, as it slows cooking.
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
    defn.addSection("Help", HELP)
    defn.setComment("Illustrator-style rich text: per-word fonts, sizes, colors, tracking.")
    node.matchCurrentDefinition()
    geo.destroy()
    return path


if __name__ == "__main__":
    print("Saved", build())
