"""
Custom Notes - a rich-text notes Python Panel for Houdini.

Features
    * Bold / Italic / Underline / Strikethrough
    * Bullet lists, numbered lists (Tab / Shift+Tab to nest)
    * Clickable to-do checkboxes (checked items get struck through + greyed)
    * Headings (H1 / H2 / H3), text colour, highlight, inline code, divider line
    * "Sticky" button: mirrors the note into a network-editor sticky note as
      Unicode text (underline/strike/code/lists/to-dos survive; bold, italic and headings become plain text;
      colours don't) and keeps it updated live while you edit
    * "Link sticky" (or opening the panel with one sticky selected) reads a
      sticky's text back into rich text and keeps it in sync
    * "Open DOCX" loads a Word document (headings, bold/italic/underline/strike,
      colours, highlights, bullet/numbered lists, tables, checkboxes) into the
      note - replacing it or inserting at the cursor; images become [image]
    * Footer shows when the note was last edited
    * Edit / View mode toggle (view mode hides formatting, locks the text,
      but to-do boxes can still be ticked)
    * Notes are stored INSIDE the .hip file (root node user data), autosaved

Shortcuts (while the note has focus)
    Ctrl+B  bold            Ctrl+I  italic           Ctrl+U  underline
    Ctrl+Shift+X  strikethrough
    Ctrl+Shift+8  bullet list     Ctrl+Shift+7  numbered list
    Ctrl+Shift+T  to-do list      Ctrl+Return   check / uncheck current to-do
    Tab / Shift+Tab  indent / outdent

Install: see the bottom of this file, or use the generated custom_notes.pypanel.
Works with PySide2 (Houdini 18.5 - 20.5) and PySide6 (Houdini 21+).
"""

import json
import os
import re
import time

import hou

try:
    from PySide2 import QtCore, QtGui, QtWidgets
    QShortcut = QtWidgets.QShortcut
    BOLD, NORMAL = 75, 50
except ImportError:  # Houdini builds that ship PySide6
    from PySide6 import QtCore, QtGui, QtWidgets
    QShortcut = QtGui.QShortcut
    BOLD, NORMAL = 700, 400

Qt = QtCore.Qt
QTextCursor = QtGui.QTextCursor
QTextCharFormat = QtGui.QTextCharFormat
QTextListFormat = QtGui.QTextListFormat

STORAGE_KEY = "custom_notes_html"      # user-data key on the root node (/)
STICKY_KEY = "custom_notes_sticky"     # JSON [parent path, sticky name]
MODE_KEY = "custom_notes_mode"         # "edit" or "view"
EDITED_KEY = "custom_notes_edited"     # epoch seconds of the last edit
DOCX_DIR_KEY = "custom_notes_docx_dir" # folder of the last opened .docx
PLACEHOLDER = "Write your notes here..."
BOX_OFF = "\u2610"                     # ☐
BOX_ON = "\u2611"                      # ☑
BOXES = (BOX_OFF, BOX_ON)
DONE_COLOR = (130, 130, 130)
CODE_BG = (60, 60, 60)                 # background that marks inline code

# Houdini 22 look: rounded slate buttons, accent for the active one, green "add"
PANEL_STYLE = """
QPushButton, QToolButton {
    background: #3f4656; color: #e4e6ea; border: 1px solid #4b5366;
    border-radius: 5px; padding: 3px 10px;
}
QToolButton { padding: 2px 6px; }
QPushButton:hover, QToolButton:hover { background: #4b5468; }
QPushButton:pressed, QToolButton:pressed { background: #353b49; }
QToolButton:checked { background: #5a6b94; border-color: #7388b8; color: #ffffff; }
QPushButton#modeBtn:checked { background: #5a6b94; border-color: #7388b8; color: #ffffff; }
QPushButton#addBtn { background: #3a5a3c; border-color: #4f7a52; color: #c9f0cb; }
QPushButton#addBtn:hover { background: #46704a; }
QPushButton#addBtn:pressed { background: #31502f; }
QToolButton::menu-indicator { image: none; width: 0px; }
"""

DEFAULT_SIZE = (660, 660)              # default panel size (width, height)

HEADING_SIZES = {0: None, 1: 22.0, 2: 17.0, 3: 14.0}

TEXT_COLORS = [
    ("Default", None), ("Red", "#ff6b6b"), ("Orange", "#ffa94d"),
    ("Yellow", "#ffe066"), ("Green", "#69db7c"), ("Blue", "#74c0fc"),
    ("Purple", "#b197fc"), ("Grey", "#adb5bd"),
]
HIGHLIGHTS = [
    ("None", None), ("Yellow", "#7a6a00"), ("Green", "#2f6f3a"),
    ("Red", "#7a2f2f"), ("Blue", "#2f4f7a"), ("Purple", "#5a3a7a"),
]


def _event_pos(e):
    return e.position().toPoint() if hasattr(e, "position") else e.pos()


# --------------------------------------------------------------------------- #
# Rich text -> plain Unicode (sticky notes have no rich text support)
# --------------------------------------------------------------------------- #
def _math_table(upper, lower, digit=None, exceptions=None):
    table = {}
    for i in range(26):
        table[chr(65 + i)] = chr(upper + i)
        table[chr(97 + i)] = chr(lower + i)
    if digit:
        for i in range(10):
            table[chr(48 + i)] = chr(digit + i)
    table.update(exceptions or {})
    return table


_BOLD_MAP = _math_table(0x1D400, 0x1D41A, 0x1D7CE)
_ITALIC_MAP = _math_table(0x1D434, 0x1D44E, None, {"h": "ℎ"})
_BOLD_ITALIC_MAP = _math_table(0x1D468, 0x1D482)
_MONO_MAP = _math_table(0x1D670, 0x1D68A, 0x1D7F6)

UNDERLINE_MARK = "̲"
STRIKE_WRAP = "~~"                     # sticky font has no combining stroke glyph
DIVIDER_TEXT = "─" * 12

# lookalike char -> (plain char, bold, italic, code), for reading sticky text back
_UNSTYLE = {}
for _map, _flags in ((_BOLD_MAP, (True, False, False)),
                     (_ITALIC_MAP, (False, True, False)),
                     (_BOLD_ITALIC_MAP, (True, True, False)),
                     (_MONO_MAP, (False, False, True))):
    for _k, _v in _map.items():
        _UNSTYLE[_v] = (_k,) + _flags
_LIST_RE = re.compile(r"( *)(?:(•)|(\d+)\.) (.*)$")


def _style_char(ch, bold, italic, code, underline):
    if code:
        out = _MONO_MAP.get(ch, ch)
    elif bold and italic:
        out = _BOLD_ITALIC_MAP.get(ch, ch)
    elif bold:
        out = _BOLD_MAP.get(ch, ch)
    elif italic:
        out = _ITALIC_MAP.get(ch, ch)
    else:
        out = ch
    if underline and not ch.isspace():
        out += UNDERLINE_MARK
    return out


def _is_code_format(cf):
    bg = cf.background()
    if bg.style() != Qt.SolidPattern:
        return False
    c = bg.color()
    return (c.red(), c.green(), c.blue()) == CODE_BG


def _block_is_divider(block):
    prop = getattr(QtGui.QTextFormat, "BlockTrailingHorizontalRulerWidth", None)
    return prop is not None and block.blockFormat().hasProperty(prop)


def _wrap_strike(run):
    """~~run~~, keeping leading/trailing spaces outside the markers."""
    core = run.strip()
    if not core:
        return run
    start = run.index(core[0])
    end = start + len(core)
    return run[:start] + STRIKE_WRAP + core + STRIKE_WRAP + run[end:]


def _block_to_unicode(block):
    text = block.text()
    doc = block.document()
    out = []
    run, run_struck = [], False
    cur = QTextCursor(doc)
    for i, ch in enumerate(text):
        cur.setPosition(block.position() + i)
        cur.setPosition(block.position() + i + 1, QTextCursor.KeepAnchor)
        cf = cur.charFormat()
        struck = cf.fontStrikeOut()
        if struck != run_struck:
            out.append(_wrap_strike("".join(run)) if run_struck else "".join(run))
            run, run_struck = [], struck
        # bold / italic / headings don't render well in sticky notes -> plain text
        run.append(_style_char(
            ch, False, False, _is_code_format(cf), cf.fontUnderline()))
    out.append(_wrap_strike("".join(run)) if run_struck else "".join(run))
    line = "".join(out)

    lst = block.textList()
    if lst is not None:
        level = max(1, lst.format().indent())
        if lst.format().style() == QTextListFormat.ListDecimal:
            marker = "%d." % (lst.itemNumber(block) + 1)
        else:
            marker = "•"
        line = "  " * (level - 1) + marker + " " + line
    else:
        line = "  " * max(0, block.blockFormat().indent()) + line
    return line


def rich_to_unicode(doc):
    """Flatten a QTextDocument to plain text using Unicode lookalikes."""
    lines = []
    block = doc.begin()
    while block.isValid():
        lines.append(_block_to_unicode(block))
        if _block_is_divider(block):
            lines.append(DIVIDER_TEXT)
        block = block.next()
    return "\n".join(lines).rstrip()


# --------------------------------------------------------------------------- #
# .docx -> rich text (standard library only: a .docx is a zip of XML parts)
# --------------------------------------------------------------------------- #
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"

# Word's named highlight colours -> muted tones that read on the dark panel
_DOCX_HIGHLIGHTS = {
    "yellow": "#7a6a00", "darkYellow": "#7a6a00",
    "green": "#2f6f3a", "darkGreen": "#2f6f3a",
    "red": "#7a2f2f", "darkRed": "#7a2f2f",
    "blue": "#2f4f7a", "darkBlue": "#2f4f7a",
    "magenta": "#5a3a7a", "darkMagenta": "#5a3a7a",
    "cyan": "#2f6a6f", "darkCyan": "#2f6a6f",
    "lightGray": "#555555", "darkGray": "#444444", "black": "#2a2a2a",
}
_MONO_FONTS = ("consolas", "courier", "menlo", "monaco", "lucida console",
               "source code", "fira code", "jetbrains mono", "cascadia")
_BLOCK_MARGINS = ("margin-top:0px; margin-bottom:0px; margin-left:0px; "
                  "margin-right:0px; -qt-block-indent:0; text-indent:0px;")
# CSS weight that Qt's HTML import turns back into BOLD (Qt5 divides by 8)
CSS_BOLD = BOLD * 8 if BOLD < 100 else BOLD
_DONE_CSS = "text-decoration: line-through; color:#%02x%02x%02x;" % DONE_COLOR


def _html_escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _on(el):
    """A Word toggle property (<w:b/>, <w:i w:val="0"/>, ...) -> bool."""
    if el is None:
        return False
    return el.get(_W + "val", "true").lower() not in ("0", "false", "off", "none")


def _readable_color(hex6):
    """Word text colour -> CSS colour, or None if it's auto / too dark for the panel."""
    if not hex6 or hex6.lower() == "auto" or len(hex6) != 6:
        return None
    try:
        r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    except ValueError:
        return None
    if 0.299 * r + 0.587 * g + 0.114 * b < 70:  # black / navy text: use the default
        return None
    return "#" + hex6


class _DocxReader(object):
    def __init__(self, path):
        import zipfile
        import xml.etree.ElementTree as ET
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                if "word/document.xml" not in names:
                    raise ValueError("not a Word document (no word/document.xml)")
                self.doc = ET.fromstring(z.read("word/document.xml"))
                numbering = (ET.fromstring(z.read("word/numbering.xml"))
                             if "word/numbering.xml" in names else None)
                styles = (ET.fromstring(z.read("word/styles.xml"))
                          if "word/styles.xml" in names else None)
        except zipfile.BadZipFile:
            raise ValueError("not a valid .docx file (it may be an old .doc file)")
        except ET.ParseError as ex:
            raise ValueError("the document XML is damaged (%s)" % ex)
        self._styles = self._read_styles(styles)
        self._num_fmts = self._read_numbering(numbering)

    # ---- lookup tables ---------------------------------------------------- #
    @staticmethod
    def _read_styles(root):
        """styleId -> heading level (1-3), for paragraph styles that are headings."""
        out = {}
        if root is None:
            return out
        for st in root.iter(_W + "style"):
            sid = st.get(_W + "styleId", "")
            name_el = st.find(_W + "name")
            name = (name_el.get(_W + "val", "") if name_el is not None else "").lower()
            level = None
            for cand in (name, sid.lower()):
                if cand == "title":
                    level = 1
                    break
                m = re.match(r"heading\s*(\d)", cand)
                if m:
                    level = min(3, max(1, int(m.group(1))))
                    break
            if level is None:
                ol = st.find(_W + "pPr/" + _W + "outlineLvl")
                if ol is not None and ol.get(_W + "val", "").isdigit():
                    level = min(3, int(ol.get(_W + "val")) + 1)
            if level:
                out[sid] = level
        return out

    @staticmethod
    def _read_numbering(root):
        """(numId, ilvl) -> Word numFmt ("bullet", "decimal", ...)."""
        out = {}
        if root is None:
            return out
        abstract = {}
        for an in root.iter(_W + "abstractNum"):
            levels = {}
            for lvl in an.findall(_W + "lvl"):
                fmt = lvl.find(_W + "numFmt")
                levels[lvl.get(_W + "ilvl", "0")] = (
                    fmt.get(_W + "val", "decimal") if fmt is not None else "decimal")
            abstract[an.get(_W + "abstractNumId")] = levels
        for num in root.iter(_W + "num"):
            ref = num.find(_W + "abstractNumId")
            if ref is None:
                continue
            for ilvl, fmt in abstract.get(ref.get(_W + "val"), {}).items():
                out[(num.get(_W + "numId"), ilvl)] = fmt
        return out

    # ---- runs ------------------------------------------------------------- #
    def _run_css(self, rpr, link):
        css = []
        if rpr is not None:
            if _on(rpr.find(_W + "b")):
                css.append("font-weight:%d;" % CSS_BOLD)
            if _on(rpr.find(_W + "i")):
                css.append("font-style:italic;")
            deco = []
            if _on(rpr.find(_W + "u")):
                deco.append("underline")
            if _on(rpr.find(_W + "strike")) or _on(rpr.find(_W + "dstrike")):
                deco.append("line-through")
            if deco:
                css.append("text-decoration:%s;" % " ".join(deco))
            col = rpr.find(_W + "color")
            col = _readable_color(col.get(_W + "val") if col is not None else None)
            if col and not link:
                css.append("color:%s;" % col)
            bg = None
            hl = rpr.find(_W + "highlight")
            if hl is not None and hl.get(_W + "val") not in (None, "none"):
                bg = _DOCX_HIGHLIGHTS.get(hl.get(_W + "val"), "#555555")
            fonts = rpr.find(_W + "rFonts")
            family = fonts.get(_W + "ascii", "") if fonts is not None else ""
            if family and family.lower().startswith(_MONO_FONTS):
                css.append("font-family:'%s';" % family)
                bg = "#%02x%02x%02x" % CODE_BG        # the panel's inline-code look
            if bg:
                css.append("background-color:%s;" % bg)
        if link:
            css.append("color:#74c0fc; text-decoration:underline;")
        return "".join(css)

    def _run(self, r, state, link=False):
        parts = []
        for ch in r:
            tag = ch.tag
            if tag == _W + "t":
                parts.append(_html_escape(ch.text or ""))
            elif tag == _W + "tab":
                parts.append("&nbsp;" * 4)
            elif tag in (_W + "br", _W + "cr"):
                parts.append("<br />")
            elif tag in (_W + "drawing", _W + "pict", _W + "object"):
                parts.append('<span style="color:#8a909c;">[image]</span>')
            elif tag == _W + "fldChar":                # legacy form checkbox
                cb = ch.find(_W + "ffData/" + _W + "checkBox")
                if cb is not None:
                    checked = cb.find(_W + "checked")
                    if checked is None:
                        checked = cb.find(_W + "default")
                    self._box(state, _on(checked))
        if not parts:
            return
        css = self._run_css(r.find(_W + "rPr"), link) + state["css"]
        if state["done"]:
            css += _DONE_CSS
        html = "".join(parts)
        state["out"].append('<span style="%s">%s</span>' % (css, html) if css else html)
        state["has_text"] = True

    def _box(self, state, checked):
        """A Word checkbox becomes the panel's own clickable to-do marker."""
        mark = BOX_ON if checked else BOX_OFF
        if not state["has_text"]:                     # leading box: a to-do line
            state["done"] = checked
            state["out"].append(mark + " ")
        else:
            state["out"].append(mark)
        state["has_text"] = True

    def _inline(self, parent, state, link=False):
        for el in parent:
            tag = el.tag
            if tag == _W + "r":
                self._run(el, state, link)
            elif tag == _W + "hyperlink":
                self._inline(el, state, True)
            elif tag == _W + "sdt":
                cb = el.find(_W + "sdtPr/" + _W14 + "checkbox")
                if cb is not None:
                    self._box(state, _on(cb.find(_W14 + "checked")))
                else:
                    content = el.find(_W + "sdtContent")
                    if content is not None:
                        self._inline(content, state, link)
            elif tag in (_W + "ins", _W + "smartTag", _W + "fldSimple",
                         _W + "customXml", _W + "moveTo"):
                self._inline(el, state, link)
            # w:del, w:moveFrom, bookmarks, proofing marks: skipped

    # ---- paragraphs / blocks ---------------------------------------------- #
    def _paragraph(self, p):
        """-> (inner html, block css, list info (numFmt, ilvl) or None)."""
        ppr = p.find(_W + "pPr")
        level, align, list_info = None, "", None
        if ppr is not None:
            st = ppr.find(_W + "pStyle")
            if st is not None:
                sid = st.get(_W + "val", "")
                level = self._styles.get(sid)
                if level is None:                     # no styles.xml: go by the id
                    m = re.match(r"heading\s*(\d)", sid.lower())
                    level = 1 if sid.lower() == "title" else (
                        min(3, max(1, int(m.group(1)))) if m else None)
            jc = ppr.find(_W + "jc")
            if jc is not None and jc.get(_W + "val") in ("center", "right", "both"):
                align = " align=\"%s\"" % {"both": "justify"}.get(
                    jc.get(_W + "val"), jc.get(_W + "val"))
            np_ = ppr.find(_W + "numPr")
            if np_ is not None:
                nid = np_.find(_W + "numId")
                ilvl = np_.find(_W + "ilvl")
                nid = nid.get(_W + "val") if nid is not None else None
                ilvl = ilvl.get(_W + "val", "0") if ilvl is not None else "0"
                if nid and nid != "0":
                    list_info = (self._num_fmts.get((nid, ilvl), "decimal"),
                                 int(ilvl) if ilvl.isdigit() else 0)
        state = {"out": [], "has_text": False, "done": False, "css": ""}
        if level:
            state["css"] = "font-size:%spt; font-weight:%d;" % (HEADING_SIZES[level], CSS_BOLD)
        self._inline(p, state)
        return "".join(state["out"]), align, list_info

    def _blocks(self, parent, out):
        open_list = None                              # (tag, indent) currently open
        for el in parent:
            tag = el.tag
            if tag == _W + "sdt":                     # block-level content control
                content = el.find(_W + "sdtContent")
                if content is not None:
                    if open_list:
                        out.append("</%s>" % open_list[0])
                        open_list = None
                    self._blocks(content, out)
                continue
            if tag == _W + "p":
                inner, align, list_info = self._paragraph(el)
                if list_info:
                    fmt, ilvl = list_info
                    ltag = "ul" if fmt == "bullet" else "ol"
                    key = (ltag, ilvl + 1)
                    if open_list != key:
                        if open_list:
                            out.append("</%s>" % open_list[0])
                        out.append('<%s style="%s -qt-list-indent:%d; list-style-type:%s;">'
                                   % (ltag, _BLOCK_MARGINS, ilvl + 1,
                                      "disc" if ltag == "ul" else "decimal"))
                        open_list = key
                    out.append('<li style="%s"%s>%s</li>' % (_BLOCK_MARGINS, align, inner))
                    continue
                if open_list:
                    out.append("</%s>" % open_list[0])
                    open_list = None
                if inner:
                    out.append('<p style="%s"%s>%s</p>' % (_BLOCK_MARGINS, align, inner))
                else:
                    out.append('<p style="-qt-paragraph-type:empty; %s"><br /></p>'
                               % _BLOCK_MARGINS)
            elif tag == _W + "tbl":
                if open_list:
                    out.append("</%s>" % open_list[0])
                    open_list = None
                out.append('<table border="1" cellspacing="0" cellpadding="4" '
                           'style="border-color:#6a7080; border-style:solid;">')
                for tr in el.findall(_W + "tr"):
                    out.append("<tr>")
                    for tc in tr.findall(_W + "tc"):
                        span = tc.find(_W + "tcPr/" + _W + "gridSpan")
                        colspan = (' colspan="%s"' % span.get(_W + "val")
                                   if span is not None else "")
                        cell = []
                        self._blocks(tc, cell)
                        out.append("<td%s>%s</td>" % (colspan, "".join(cell)))
                    out.append("</tr>")
                out.append("</table>")
        if open_list:
            out.append("</%s>" % open_list[0])

    def to_html(self):
        body = self.doc.find(_W + "body")
        if body is None:
            raise ValueError("the document has no body")
        out = []
        self._blocks(body, out)
        return "<html><body>%s</body></html>" % "".join(out)


def docx_to_html(path):
    """Convert a Word .docx file into HTML the notes editor understands.

    Raises ValueError with a readable message for files that can't be read.
    """
    return _DocxReader(path).to_html()


# --------------------------------------------------------------------------- #
# The editor
# --------------------------------------------------------------------------- #
class NotesEdit(QtWidgets.QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.viewport().setMouseTracking(True)
        self.document().setDocumentMargin(10)
        self.document().contentsChanged.connect(self._update_placeholder)
        self._update_placeholder()
        size = self.font().pointSizeF()
        self._base_size = size if size > 0 else 10.0
        self._base_family = self.font().family()
        try:                                   # a monospace font that really exists
            fixed = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
            self._code_family = fixed.family() or "monospace"
        except Exception:
            self._code_family = "monospace"

    # ---- helpers ---------------------------------------------------------- #
    def selected_blocks(self):
        cur = self.textCursor()
        doc = self.document()
        first = doc.findBlock(cur.selectionStart())
        last = doc.findBlock(cur.selectionEnd())
        if (cur.hasSelection() and last.blockNumber() != first.blockNumber()
                and cur.selectionEnd() == last.position()):
            last = last.previous()
        blocks, b = [], first
        while b.isValid():
            blocks.append(b)
            if b.blockNumber() == last.blockNumber():
                break
            b = b.next()
        return blocks

    def merge_format(self, fmt):
        self.mergeCurrentCharFormat(fmt)
        self.setFocus()

    def _update_placeholder(self):
        # Qt only checks for "no text", so an empty bullet would still show it
        doc = self.document()
        first = doc.firstBlock()
        empty = (doc.isEmpty() and doc.blockCount() == 1 and first.textList() is None
                 and first.blockFormat().indent() == 0)
        text = PLACEHOLDER if empty else ""
        if self.placeholderText() != text:
            self.setPlaceholderText(text)
            self.viewport().update()

    # ---- loading sticky-note text ----------------------------------------- #
    def _unicode_format(self, bold, italic, code, underline, struck, done):
        f = QTextCharFormat()
        if bold:
            f.setFontWeight(BOLD)
        if italic:
            f.setFontItalic(True)
        if underline:
            f.setFontUnderline(True)
        if struck:
            f.setFontStrikeOut(True)
        if done:
            f.setForeground(QtGui.QBrush(QtGui.QColor(*DONE_COLOR)))
        if code:
            f.setFontFamily(self._code_family)
            f.setBackground(QtGui.QBrush(QtGui.QColor(*CODE_BG)))
        return f

    def _insert_unicode_line(self, cur, body):
        done = False
        if body[:1] in BOXES:
            done = body[0] == BOX_ON
            cur.insertText(body[0], QTextCharFormat())
            body = body[1:]
        if body.count(STRIKE_WRAP) % 2 == 0:
            segments = body.split(STRIKE_WRAP)
        else:                              # unbalanced markers: keep them as text
            segments = [body]
        for i, seg in enumerate(segments):
            struck = done or i % 2 == 1
            run, run_key, j = [], None, 0
            while j < len(seg):
                ch = seg[j]
                j += 1
                underline = False
                while j < len(seg) and seg[j] == UNDERLINE_MARK:
                    underline = True
                    j += 1
                plain, bold, italic, code = _UNSTYLE.get(ch, (ch, False, False, False))
                key = (bold, italic, code, underline)
                if key != run_key and run:
                    cur.insertText("".join(run), self._unicode_format(*run_key + (struck, done)))
                    run = []
                run_key = key
                run.append(plain)
            if run:
                cur.insertText("".join(run), self._unicode_format(*run_key + (struck, done)))

    def set_unicode(self, text):
        """Rebuild rich text from sticky-note text (inverse of rich_to_unicode)."""
        self.clear()
        cur = QTextCursor(self.document())
        cur.beginEditBlock()
        ruler = getattr(QtGui.QTextFormat, "BlockTrailingHorizontalRulerWidth", None)
        lists = {}                         # indent -> list still open at that level
        first = True
        for line in text.split("\n"):
            if line == DIVIDER_TEXT and not first and ruler is not None:
                bf = cur.blockFormat()
                bf.setProperty(ruler, QtGui.QTextLength(QtGui.QTextLength.PercentageLength, 100))
                cur.setBlockFormat(bf)
                continue
            if not first:
                cur.insertBlock(QtGui.QTextBlockFormat(), QTextCharFormat())
            first = False
            m = _LIST_RE.match(line)
            if m:
                indent = len(m.group(1)) // 2 + 1
                style = QTextListFormat.ListDisc if m.group(2) else QTextListFormat.ListDecimal
                for k in [k for k in lists if k > indent]:
                    del lists[k]
                lst = lists.get(indent)
                if lst is not None and lst.format().style() == style:
                    lst.add(cur.block())
                else:
                    fmt = QTextListFormat()
                    fmt.setStyle(style)
                    fmt.setIndent(indent)
                    lists[indent] = cur.createList(fmt)
                body = m.group(4)
            else:
                lists.clear()
                body = line.lstrip(" ")
                indent = (len(line) - len(body)) // 2
                if indent:
                    bf = cur.blockFormat()
                    bf.setIndent(indent)
                    cur.setBlockFormat(bf)
            self._insert_unicode_line(cur, body)
        cur.endEditBlock()
        self.setCurrentCharFormat(QTextCharFormat())

    # ---- inline formatting ------------------------------------------------ #
    def toggle_format(self, kind):
        cf = self.currentCharFormat()
        fmt = QTextCharFormat()
        if kind == "bold":
            fmt.setFontWeight(NORMAL if cf.fontWeight() > NORMAL else BOLD)
        elif kind == "italic":
            fmt.setFontItalic(not cf.fontItalic())
        elif kind == "underline":
            fmt.setFontUnderline(not cf.fontUnderline())
        elif kind == "strike":
            fmt.setFontStrikeOut(not cf.fontStrikeOut())
        self.merge_format(fmt)

    def _is_code(self, cf):
        # NOTE: QTextCharFormat.fontFamily() hard-crashes Houdini 22 (Qt6), so
        # inline code is detected by its background colour instead.
        bg = cf.background()
        if bg.style() != Qt.SolidPattern:
            return False
        c = bg.color()
        return (c.red(), c.green(), c.blue()) == CODE_BG

    def toggle_code(self):
        cf = self.currentCharFormat()
        fmt = QTextCharFormat()
        if self._is_code(cf):
            fmt.setFontFamily(self._base_family)
            fmt.setBackground(QtGui.QBrush(Qt.NoBrush))
        else:
            fmt.setFontFamily(self._code_family)
            fmt.setBackground(QtGui.QBrush(QtGui.QColor(*CODE_BG)))
        self.merge_format(fmt)

    def set_text_color(self, hex_color):
        fmt = QTextCharFormat()
        if hex_color:
            fmt.setForeground(QtGui.QBrush(QtGui.QColor(hex_color)))
        else:
            fmt.setForeground(self.palette().text())
        self.merge_format(fmt)

    def set_highlight(self, hex_color):
        fmt = QTextCharFormat()
        if hex_color:
            fmt.setBackground(QtGui.QBrush(QtGui.QColor(hex_color)))
        else:
            fmt.setBackground(QtGui.QBrush(Qt.NoBrush))
        self.merge_format(fmt)

    def clear_formatting(self):
        cur = self.textCursor()
        if cur.hasSelection():
            cur.setCharFormat(QTextCharFormat())
        self.setCurrentCharFormat(QTextCharFormat())
        self.setFocus()

    def set_heading(self, level):
        size = HEADING_SIZES.get(level) or self._base_size
        fmt = QTextCharFormat()
        fmt.setFontPointSize(size)
        fmt.setFontWeight(BOLD if level else NORMAL)
        cur = self.textCursor()
        cur.beginEditBlock()
        for b in self.selected_blocks():
            bc = QTextCursor(b)
            bc.movePosition(QTextCursor.StartOfBlock)
            bc.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            bc.mergeCharFormat(fmt)
        cur.endEditBlock()
        self.mergeCurrentCharFormat(fmt)
        self.setFocus()

    def insert_divider(self):
        self.textCursor().insertHtml("<hr>")
        self.setFocus()

    # ---- lists ------------------------------------------------------------ #
    def toggle_list(self, style):
        blocks = self.selected_blocks()
        first_list = blocks[0].textList()
        cur = self.textCursor()
        cur.beginEditBlock()
        if first_list is not None and first_list.format().style() == style:
            for b in blocks:
                lst = b.textList()
                if lst is not None:
                    lst.remove(b)
                bc = QTextCursor(b)
                bf = bc.blockFormat()
                bf.setIndent(0)
                bc.setBlockFormat(bf)
        else:
            for b in blocks:                   # a to-do becomes a plain list item
                if b.text()[:1] in BOXES:
                    self._strip_box(b)
                else:                          # don't draw the marker struck/grey
                    QTextCursor(b).setBlockCharFormat(QTextCharFormat())
            fmt = QTextListFormat()
            fmt.setStyle(style)
            fmt.setIndent(first_list.format().indent() if first_list else 1)
            new_list = QTextCursor(blocks[0]).createList(fmt)
            for b in blocks[1:]:
                new_list.add(b)
        cur.endEditBlock()
        self.setFocus()

    def _find_sibling_list(self, block, indent, style):
        p = block.previous()
        while p.isValid():
            pl = p.textList()
            if pl is None:
                return None
            ind = pl.format().indent()
            if ind < indent:
                return None
            if ind == indent and pl.format().style() == style:
                return pl
            p = p.previous()
        return None

    def change_indent(self, delta):
        cur = self.textCursor()
        cur.beginEditBlock()
        for b in self.selected_blocks():
            bc = QTextCursor(b)
            lst = b.textList()
            if lst is not None:
                fmt = lst.format()
                new_indent = fmt.indent() + delta
                if new_indent < 1:
                    lst.remove(b)
                    bf = bc.blockFormat()
                    bf.setIndent(0)
                    bc.setBlockFormat(bf)
                else:
                    nf = QTextListFormat(fmt)
                    nf.setIndent(new_indent)
                    sibling = self._find_sibling_list(b, new_indent, nf.style())
                    if sibling is not None:
                        sibling.add(b)
                    else:
                        bc.createList(nf)
            else:
                bf = bc.blockFormat()
                bf.setIndent(max(0, bf.indent() + delta))
                bc.setBlockFormat(bf)
        cur.endEditBlock()

    # ---- to-do lines ------------------------------------------------------ #
    def toggle_todo(self):
        blocks = self.selected_blocks()
        all_todo = all(b.text()[:1] in BOXES for b in blocks)
        cur = self.textCursor()
        cur.beginEditBlock()
        for b in blocks:
            if all_todo:
                self._strip_box(b)
            elif b.text()[:1] not in BOXES:
                lst = b.textList()
                if lst is not None:
                    lst.remove(b)
                QTextCursor(b).insertText(BOX_OFF + " ", QTextCharFormat())
        cur.endEditBlock()
        self.setFocus()

    def _strip_box(self, block):
        text = block.text()
        was_done = text[:1] == BOX_ON
        n = 2 if text[1:2] == " " else 1
        c = QTextCursor(block)
        c.setPosition(block.position() + n, QTextCursor.KeepAnchor)
        c.removeSelectedText()
        if was_done:
            r = QTextCursor(block)
            r.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            f = QTextCharFormat()
            f.setFontStrikeOut(False)
            f.setForeground(self.palette().text())
            r.mergeCharFormat(f)
        # the block format is what an empty line types with / lists draw with
        QTextCursor(block).setBlockCharFormat(QTextCharFormat())
        if block.length() <= 1 and self.textCursor().block() == block:
            self.setCurrentCharFormat(QTextCharFormat())

    def toggle_box(self, block):
        text = block.text()
        if text[:1] not in BOXES:
            return
        done = text[0] == BOX_OFF          # unchecked -> becoming checked
        c = QTextCursor(block)
        c.beginEditBlock()
        c.setPosition(block.position() + 1, QTextCursor.KeepAnchor)
        c.insertText(BOX_ON if done else BOX_OFF, QTextCharFormat())
        rest = QTextCursor(block)
        rest.setPosition(block.position() + 1)
        rest.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        f = QTextCharFormat()
        f.setFontStrikeOut(done)
        if done:
            f.setForeground(QtGui.QBrush(QtGui.QColor(*DONE_COLOR)))
        else:
            f.setForeground(self.palette().text())
        rest.mergeCharFormat(f)
        c.endEditBlock()

    def toggle_current_box(self):
        self.toggle_box(self.textCursor().block())

    def _box_at(self, pos):
        block = self.cursorForPosition(pos).block()
        if block.text()[:1] not in BOXES:
            return None
        c0 = QTextCursor(block)
        c1 = QTextCursor(block)
        c1.setPosition(block.position() + 1)
        r0, r1 = self.cursorRect(c0), self.cursorRect(c1)
        if (r0.top() <= pos.y() <= r0.bottom()
                and r0.left() - 3 <= pos.x() <= r1.left() + 3):
            return block
        return None

    # ---- events ----------------------------------------------------------- #
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            block = self._box_at(_event_pos(e))
            if block is not None:
                self.toggle_box(block)
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        over = self._box_at(_event_pos(e)) is not None
        self.viewport().setCursor(Qt.PointingHandCursor if over else Qt.IBeamCursor)
        super().mouseMoveEvent(e)

    def keyPressEvent(self, e):
        if self.isReadOnly():              # view mode: no custom editing keys
            super().keyPressEvent(e)
            return
        key, mods = e.key(), e.modifiers()
        cur = self.textCursor()
        block = cur.block()
        text = block.text()
        is_todo = text[:1] in BOXES

        if key in (Qt.Key_Return, Qt.Key_Enter):
            if mods & Qt.ControlModifier:
                return                     # handled by the Ctrl+Return shortcut
            if is_todo and not (mods & Qt.ShiftModifier) and not cur.hasSelection():
                if text[1:].strip() == "":            # empty to-do -> leave the list
                    cur.beginEditBlock()
                    self._strip_box(block)
                    cur.endEditBlock()
                    return
                if cur.positionInBlock() >= 2:        # continue the to-do list
                    cur.beginEditBlock()
                    cur.insertBlock(cur.blockFormat(), QTextCharFormat())
                    cur.insertText(BOX_OFF + " ", QTextCharFormat())
                    cur.endEditBlock()
                    self.setTextCursor(cur)
                    self.setCurrentCharFormat(QTextCharFormat())
                    return

        elif key == Qt.Key_Backspace and is_todo and not cur.hasSelection():
            pos = cur.positionInBlock()
            if pos == 1 or (pos == 2 and text[1:2] == " "):
                cur.beginEditBlock()
                self._strip_box(block)
                cur.endEditBlock()
                return

        elif key == Qt.Key_Tab and not (mods & Qt.ControlModifier):
            self.change_indent(1)
            return
        elif key == Qt.Key_Backtab:
            self.change_indent(-1)
            return

        super().keyPressEvent(e)


# --------------------------------------------------------------------------- #
# The panel (toolbar + editor + persistence)
# --------------------------------------------------------------------------- #
class NotesPanel(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading = False
        self._edit_mode = True
        self._sticky = None                # (parent path, sticky name)
        self._fmt_actions = []             # toolbar items hidden in view mode
        self._fmt_shortcuts = []           # shortcuts disabled in view mode

        self.edit = NotesEdit(self)
        self.toolbar = QtWidgets.QToolBar(self)
        self.toolbar.setMovable(False)
        self._build_toolbar()
        self.resize(*DEFAULT_SIZE)
        self.setStyleSheet(PANEL_STYLE)

        # header row: [Edit][View] ............ [+ add_sticky]
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(4)
        self.b_edit = self._mode_btn("Edit", "Edit mode")
        self.b_view = self._mode_btn("View", "View mode (read-only)")
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.b_edit)
        group.addButton(self.b_view)
        self.b_edit.clicked.connect(lambda *_: self.set_edit_mode(True))
        self.b_view.clicked.connect(lambda *_: self.set_edit_mode(False))
        header.addWidget(self.b_edit)
        header.addWidget(self.b_view)
        header.addStretch(1)
        self.b_sticky = QtWidgets.QPushButton("+ ADD STICKY")
        self.b_sticky.setObjectName("addBtn")
        self.b_sticky.setFocusPolicy(Qt.NoFocus)
        self.b_sticky.setToolTip(
            "Add this note as a sticky note in the network editor (kept in sync "
            "while you edit; click again to refresh/re-create it)")
        self.b_sticky.clicked.connect(lambda *_: self.add_sticky())
        self.b_link = QtWidgets.QPushButton("LINK STICKY")
        self.b_link.setFocusPolicy(Qt.NoFocus)
        self.b_link.setToolTip(
            "Link the sticky note selected in the network editor: its text is "
            "loaded into this panel, and it then follows your edits")
        self.b_link.clicked.connect(lambda *_: self.link_sticky())
        self.b_docx = QtWidgets.QPushButton("OPEN DOCX")
        self.b_docx.setFocusPolicy(Qt.NoFocus)
        self.b_docx.setToolTip(
            "Open a Word (.docx) file and load its text and formatting into this note")
        self.b_docx.clicked.connect(lambda *_: self.open_docx())
        header.addWidget(self.b_docx)
        header.addWidget(self.b_link)
        header.addWidget(self.b_sticky)

        # footer row: last edit time
        self._edited = None
        self.footer = QtWidgets.QLabel()
        self.footer.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.footer.setStyleSheet("color: #8a909c; font-size: 11px; padding: 0 4px;")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.edit)
        layout.addWidget(self.footer)

        self._shortcuts = []
        self._build_shortcuts()

        # debounced autosave into the .hip
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.save)
        # short debounce for the live sticky-note mirror
        self._sticky_timer = QtCore.QTimer(self)
        self._sticky_timer.setSingleShot(True)
        self._sticky_timer.setInterval(150)
        self._sticky_timer.timeout.connect(self._update_sticky)
        self.edit.textChanged.connect(self._on_text_changed)
        self.edit.currentCharFormatChanged.connect(self._sync_buttons)

        # reload when a hip file is opened / cleared, flush before saving
        self._cb = self._on_hip_event
        hou.hipFile.addEventCallback(self._cb)
        cb = self._cb
        self.destroyed.connect(lambda *_: self._remove_cb(cb))

        self.load()

    def sizeHint(self):
        return QtCore.QSize(*DEFAULT_SIZE)

    # ---- toolbar ---------------------------------------------------------- #
    def _add_fmt(self, widget):
        """Add a formatting-only widget (hidden in view mode)."""
        self._fmt_actions.append(self.toolbar.addWidget(widget))

    def _add_fmt_separator(self):
        self._fmt_actions.append(self.toolbar.addSeparator())

    def _mode_btn(self, text, tip):
        b = QtWidgets.QPushButton(text)
        b.setObjectName("modeBtn")
        b.setCheckable(True)
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)
        return b

    def _btn(self, text, tip, slot, checkable=False, style=None, fmt=True):
        b = QtWidgets.QToolButton()
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setFocusPolicy(Qt.NoFocus)
        if style:
            f = QtGui.QFont(b.font())
            f.setBold("b" in style)
            f.setItalic("i" in style)
            f.setUnderline("u" in style)
            f.setStrikeOut("s" in style)
            b.setFont(f)
        b.clicked.connect(lambda *_: slot())
        if fmt:
            self._add_fmt(b)
        else:
            self.toolbar.addWidget(b)
        return b

    def _menu_btn(self, label, tip, entries, slot):
        b = QtWidgets.QToolButton()
        b.setText(label + " \u25BE")
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.NoFocus)
        b.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        menu = QtWidgets.QMenu(b)
        for name, hex_color in entries:
            act = menu.addAction(name)
            if hex_color:
                pm = QtGui.QPixmap(14, 14)
                pm.fill(QtGui.QColor(hex_color))
                act.setIcon(QtGui.QIcon(pm))
            act.triggered.connect(lambda checked=False, h=hex_color: slot(h))
        b.setMenu(menu)
        self._add_fmt(b)
        return b

    def _build_toolbar(self):
        e = self.edit
        self.heading = QtWidgets.QComboBox()
        self.heading.addItems(["Normal", "Heading 1", "Heading 2", "Heading 3"])
        self.heading.setFocusPolicy(Qt.NoFocus)
        self.heading.setToolTip("Paragraph style")
        self.heading.activated.connect(e.set_heading)
        self._add_fmt(self.heading)
        self._add_fmt_separator()

        self.b_bold = self._btn("B", "Bold (Ctrl+B)", lambda: e.toggle_format("bold"), True, "b")
        self.b_ital = self._btn("I", "Italic (Ctrl+I)", lambda: e.toggle_format("italic"), True, "i")
        self.b_under = self._btn("U", "Underline (Ctrl+U)", lambda: e.toggle_format("underline"), True, "u")
        self.b_strike = self._btn("S", "Strikethrough (Ctrl+Shift+X)", lambda: e.toggle_format("strike"), True, "s")
        self._add_fmt_separator()

        self._btn("\u2022 List", "Bullet list (Ctrl+Shift+8)",
                  lambda: e.toggle_list(QTextListFormat.ListDisc))
        self._btn("1. List", "Numbered list (Ctrl+Shift+7)",
                  lambda: e.toggle_list(QTextListFormat.ListDecimal))
        self._btn("\u2610 To-do", "To-do list (Ctrl+Shift+T). Click a box to tick it.",
                  e.toggle_todo)
        self._add_fmt_separator()

        self._menu_btn("Color", "Text colour", TEXT_COLORS, e.set_text_color)
        self._menu_btn("Highlight", "Highlight colour", HIGHLIGHTS, e.set_highlight)
        self._btn("</>", "Inline code", e.toggle_code)
        self._btn("\u2015", "Insert divider line", e.insert_divider)
        self._btn("Clear", "Clear formatting of selection", e.clear_formatting)

    def _build_shortcuts(self):
        e = self.edit
        table = [
            ("Ctrl+B", lambda: e.toggle_format("bold")),
            ("Ctrl+I", lambda: e.toggle_format("italic")),
            ("Ctrl+U", lambda: e.toggle_format("underline")),
            ("Ctrl+Shift+X", lambda: e.toggle_format("strike")),
            ("Ctrl+Shift+8", lambda: e.toggle_list(QTextListFormat.ListDisc)),
            ("Ctrl+Shift+7", lambda: e.toggle_list(QTextListFormat.ListDecimal)),
            ("Ctrl+Shift+T", e.toggle_todo),
            ("Ctrl+Return", e.toggle_current_box),
            ("Ctrl+Enter", e.toggle_current_box),
        ]
        for seq, fn in table:
            sc = QShortcut(QtGui.QKeySequence(seq), e)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
            self._shortcuts.append(sc)
            if "Return" not in seq and "Enter" not in seq:
                self._fmt_shortcuts.append(sc)

    def _sync_buttons(self, fmt):
        self.b_bold.setChecked(fmt.fontWeight() > NORMAL)
        self.b_ital.setChecked(fmt.fontItalic())
        self.b_under.setChecked(fmt.fontUnderline())
        self.b_strike.setChecked(fmt.fontStrikeOut())
        size = fmt.fontPointSize()
        idx = 0
        if size >= 21:
            idx = 1
        elif size >= 16:
            idx = 2
        elif size >= 13.5:
            idx = 3
        self.heading.blockSignals(True)
        self.heading.setCurrentIndex(idx)
        self.heading.blockSignals(False)

    # ---- persistence (stored in the .hip) --------------------------------- #
    def _on_text_changed(self):
        if not self._loading:
            self._edited = time.time()
            self._update_footer()
            self._timer.start()
            if self._sticky:
                self._sticky_timer.start()

    def _update_footer(self):
        if self._edited:
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(self._edited))
        else:
            stamp = "—"
        self.footer.setText("Last edited: " + stamp)

    # ---- view / edit mode ------------------------------------------------- #
    def set_edit_mode(self, editing, persist=True):
        self._edit_mode = editing
        self.edit.setReadOnly(not editing)
        for act in self._fmt_actions:
            act.setVisible(editing)
        for sc in self._fmt_shortcuts:
            sc.setEnabled(editing)
        self.toolbar.setVisible(editing)
        self.b_edit.setChecked(editing)
        self.b_view.setChecked(not editing)
        if persist:
            self._set_user_data(MODE_KEY, "edit" if editing else "view")

    # ---- sticky note ------------------------------------------------------ #
    @staticmethod
    def _set_user_data(key, value):
        try:
            root = hou.node("/")
            if root.userData(key) != value:
                with hou.undos.disabler():
                    root.setUserData(key, value)
        except Exception as ex:
            print("Custom Notes: could not store %s ->" % key, ex)

    def _find_sticky(self):
        if not self._sticky:
            return None
        try:
            parent = hou.node(self._sticky[0])
            return parent.findStickyNote(self._sticky[1]) if parent else None
        except Exception:
            return None

    def _forget_sticky(self):
        self._sticky = None
        self._set_user_data(STICKY_KEY, "")

    def add_sticky(self):
        text = rich_to_unicode(self.edit.document())
        note = self._find_sticky()
        if note is not None:                       # already linked: just refresh
            try:
                with hou.undos.disabler():
                    note.setText(text)
                note.setSelected(True, clear_all_selected=True)
                return
            except Exception:
                self._forget_sticky()
        editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if editor is None:
            hou.ui.setStatusMessage("Custom Notes: open a Network Editor first.",
                                    severity=hou.severityType.Warning)
            return
        parent = editor.pwd()
        try:
            note = parent.createStickyNote()
            size = hou.Vector2(5.0, 4.0)
            note.setSize(size)
            note.setPosition(editor.visibleBounds().center() - size * 0.5)
            note.setText(text)
            note.setSelected(True, clear_all_selected=True)
        except Exception as ex:
            hou.ui.setStatusMessage("Custom Notes: could not create sticky note (%s)" % ex,
                                    severity=hou.severityType.Warning)
            return
        self._sticky = (parent.path(), note.name())
        self._set_user_data(STICKY_KEY, json.dumps(self._sticky))

    @staticmethod
    def _selected_stickies():
        """(parent, [selected sticky notes]) of the network editor, or an error string."""
        editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if editor is None:
            return "Custom Notes: open a Network Editor first."
        try:
            parent = editor.pwd()
            return parent, [n for n in parent.stickyNotes() if n.isSelected()]
        except Exception as ex:
            return "Custom Notes: could not read the network (%s)" % ex

    def link_sticky(self, auto=False):
        """Link the sticky note selected in the network editor to this panel.

        auto: called when the panel opens - stay quiet unless exactly one sticky
        is selected, and only ask before replacing text that would be lost.
        """
        warn = hou.severityType.Warning
        found = self._selected_stickies()
        if isinstance(found, str):
            if not auto:
                hou.ui.setStatusMessage(found, severity=warn)
            return
        parent, picked = found
        if len(picked) != 1:
            if not auto:
                hou.ui.setStatusMessage(
                    "Custom Notes: select exactly one sticky note in the network editor, "
                    "then click Link.", severity=warn)
            return
        note = picked[0]
        key = (parent.path(), note.name())
        if auto and key == self._sticky:           # already linked
            return
        doc = self.edit.document()
        harmless = doc.isEmpty() or rich_to_unicode(doc) == note.text().rstrip()
        if not (auto and harmless) and hou.ui.displayMessage(
                "Link sticky note '%s'?\n\nThis panel's text will be replaced by the "
                "sticky note's text, and the sticky note will then follow your edits."
                % note.name(),
                buttons=("Link", "Cancel"), default_choice=1, close_choice=1,
                title="Link sticky note") != 0:
            return
        self._loading = True                       # don't echo the load back out
        try:
            self.edit.set_unicode(note.text())
        finally:
            self._loading = False
        self._sticky = key
        self._set_user_data(STICKY_KEY, json.dumps(self._sticky))
        self._edited = time.time()
        self._update_footer()
        self.save()

    # ---- Word documents --------------------------------------------------- #
    def open_docx(self):
        warn = hou.severityType.Warning
        start = ""
        try:
            start = hou.node("/").userData(DOCX_DIR_KEY) or ""
        except Exception:
            pass
        if not start or not os.path.isdir(start):
            start = hou.text.expandString("$HIP")
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open Word document", start, "Word documents (*.docx);;All files (*)")
        if not path:
            return
        self._set_user_data(DOCX_DIR_KEY, os.path.dirname(path))
        try:
            html = docx_to_html(path)
        except Exception as ex:
            hou.ui.setStatusMessage("Custom Notes: could not read %s (%s)"
                                    % (os.path.basename(path), ex), severity=warn)
            return

        replace = True
        if not self.edit.document().isEmpty():
            choice = hou.ui.displayMessage(
                "Load '%s' into this note?" % os.path.basename(path),
                buttons=("Replace", "Insert at cursor", "Cancel"),
                default_choice=0, close_choice=2, title="Open Word document")
            if choice == 2:
                return
            replace = choice == 0
        if replace:
            self.edit.setHtml(html)            # textChanged -> autosave / sticky / footer
        else:
            self.edit.textCursor().insertHtml(html)
        self.edit.setCurrentCharFormat(QTextCharFormat())
        hou.ui.setStatusMessage("Custom Notes: loaded " + os.path.basename(path))

    def showEvent(self, e):
        super().showEvent(e)
        # opening the panel with a sticky selected links it
        QtCore.QTimer.singleShot(0, self._auto_link)

    def _auto_link(self):
        try:
            self.link_sticky(auto=True)
        except RuntimeError:               # panel closed in the meantime
            pass

    def _update_sticky(self):
        if not self._sticky:
            return
        note = self._find_sticky()
        if note is None:                           # sticky was deleted
            self._forget_sticky()
            return
        try:
            text = rich_to_unicode(self.edit.document())
            if note.text() != text:
                with hou.undos.disabler():
                    note.setText(text)
        except Exception as ex:
            print("Custom Notes: could not update sticky note ->", ex)

    def load(self):
        html = None
        try:
            root = hou.node("/")
            html = root.userData(STORAGE_KEY)
            link = root.userData(STICKY_KEY)
            self._sticky = tuple(json.loads(link)) if link else None
            mode = root.userData(MODE_KEY)
            edited = root.userData(EDITED_KEY)
            self._edited = float(edited) if edited else None
        except Exception:
            self._sticky = None
            self._edited = None
            mode = None
        self._update_footer()
        self.set_edit_mode(mode != "view", persist=False)
        self._loading = True
        try:
            if html:
                self.edit.setHtml(html)
            else:
                self.edit.clear()
        finally:
            self._loading = False

    def save(self):
        if self._edited:
            self._set_user_data(EDITED_KEY, repr(self._edited))
        try:
            html = self.edit.toHtml()
            root = hou.node("/")
            if root.userData(STORAGE_KEY) == html:
                return
            with hou.undos.disabler():
                root.setUserData(STORAGE_KEY, html)
        except Exception as ex:
            print("Custom Notes: could not save notes ->", ex)

    def _on_hip_event(self, event_type):
        try:
            if event_type in (hou.hipFileEventType.AfterLoad,
                              hou.hipFileEventType.AfterClear):
                self.load()
            elif event_type == hou.hipFileEventType.BeforeSave:
                self._timer.stop()
                self.save()
        except RuntimeError:               # panel was deleted
            self._remove_cb(self._cb)

    @staticmethod
    def _remove_cb(cb):
        try:
            hou.hipFile.removeEventCallback(cb)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Python Panel entry point
# --------------------------------------------------------------------------- #
def onCreateInterface():
    return NotesPanel()


# INSTALL (manual): Windows > Python Panel Editor > "+" New Interface,
# name it custom_notes, paste this whole script into the Script tab, click
# Accept. Then: pane tab menu (+) > New Pane Tab Type > Python Panel > Custom Notes.