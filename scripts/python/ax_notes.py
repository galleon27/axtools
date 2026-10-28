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
# The editor
# --------------------------------------------------------------------------- #
class NotesEdit(QtWidgets.QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(True)
        self.viewport().setMouseTracking(True)
        self.document().setDocumentMargin(10)
        self.setPlaceholderText("Write your notes here...")
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
                    cur.insertBlock()
                    cur.insertText(BOX_OFF + " ", QTextCharFormat())
                    cur.endEditBlock()
                    self.setTextCursor(cur)
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
        header.addWidget(self.b_link)
        header.addWidget(self.b_sticky)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.edit)

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
            self._timer.start()
            if self._sticky:
                self._sticky_timer.start()

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

    def link_sticky(self):
        """Link the sticky note selected in the network editor to this panel."""
        warn = hou.severityType.Warning
        editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if editor is None:
            hou.ui.setStatusMessage("Custom Notes: open a Network Editor first.",
                                    severity=warn)
            return
        try:
            parent = editor.pwd()
            picked = [n for n in parent.stickyNotes() if n.isSelected()]
        except Exception as ex:
            hou.ui.setStatusMessage("Custom Notes: could not read the network (%s)" % ex,
                                    severity=warn)
            return
        if len(picked) != 1:
            hou.ui.setStatusMessage(
                "Custom Notes: select exactly one sticky note in the network editor, "
                "then click Link.", severity=warn)
            return
        note = picked[0]
        if hou.ui.displayMessage(
                "Link sticky note '%s'?\n\nThis panel's text will be replaced by the "
                "sticky note's text, and the sticky note will then follow your edits."
                % note.name(),
                buttons=("Link", "Cancel"), default_choice=1, close_choice=1,
                title="Link sticky note") != 0:
            return
        self._loading = True                       # don't echo the load back out
        try:
            self.edit.setPlainText(note.text())
        finally:
            self._loading = False
        self._sticky = (parent.path(), note.name())
        self._set_user_data(STICKY_KEY, json.dumps(self._sticky))
        self.save()

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
        except Exception:
            self._sticky = None
            mode = None
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