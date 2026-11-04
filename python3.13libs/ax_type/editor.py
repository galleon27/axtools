"""AX Type rich text editor.

Select text and set a character style, font, size, color, tracking, baseline
shift or case from the toolbar; the window writes the tag markup into the
node's Text parm. Open with:
    import ax_type.editor; ax_type.editor.open_editor(node)
"""
import json
import re

import hou

try:
    from PySide2 import QtCore, QtGui, QtWidgets
except ImportError:  # Houdini builds that ship PySide6
    from PySide6 import QtCore, QtGui, QtWidgets

from . import fonts, markup
from . import read_base, read_styles, _extra_dirs, STYLE_PROPS

P_STYLE = QtGui.QTextFormat.UserProperty + 1
P_OVR = QtGui.QTextFormat.UserProperty + 2
BASE_PT = 18.0
PARA_SEP = " "

ALIGN_TO_QT = {"left": QtCore.Qt.AlignLeft, "center": QtCore.Qt.AlignHCenter,
               "right": QtCore.Qt.AlignRight, "justify": QtCore.Qt.AlignJustify}
CAPS = {"upper": QtGui.QFont.AllUppercase, "lower": QtGui.QFont.AllLowercase,
        "title": QtGui.QFont.Capitalize, "smallcaps": QtGui.QFont.SmallCaps}
CASE_LABELS = (("none", "Normal"), ("upper", "ALL CAPS"), ("lower", "lowercase"),
               ("title", "Title Case"), ("smallcaps", "Small Caps"))

_editors = {}       # node session id -> TypeEditor
_qt_families = {}   # (path, number) -> (family, style name) or None


def open_editor(node):
    ed = _editors.get(node.sessionId())
    if ed is not None:
        try:
            ed.show()
            ed.raise_()
            ed.activateWindow()
            return ed
        except RuntimeError:  # widget already deleted
            pass
    try:
        parent = hou.qt.mainWindow()
    except Exception:
        parent = None
    ed = TypeEditor(node, parent)
    _editors[node.sessionId()] = ed
    ed.show()
    return ed


def _qt_family(spec, extra):
    """Register the font file with Qt and return (family, style name)."""
    r = fonts.resolve(spec, extra)
    if r is None:
        return None
    if r in _qt_families:
        return _qt_families[r]
    path, number = r
    res = None
    try:
        fid = QtGui.QFontDatabase.addApplicationFont(path)
        fams = QtGui.QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        fd = fonts.load(path, number)
        legacy = fd.tt["name"].getDebugName(1) or ""
        legacy_style = fd.tt["name"].getDebugName(2) or ""
        if legacy in fams or not fams:
            res = (legacy or fd.family, legacy_style)
        elif fd.family in fams:
            res = (fd.family, fd.style)
        else:
            res = (fams[0], fd.style)
    except Exception:
        res = None
    _qt_families[r] = res
    return res


def _dump(ovr):
    return json.dumps({k: list(v) if isinstance(v, tuple) else v for k, v in ovr.items()},
                      sort_keys=True)


def _load(s):
    try:
        d = json.loads(s) if s else {}
    except ValueError:
        d = {}
    return {k: tuple(v) if isinstance(v, list) else v for k, v in d.items()}


class _Spin(QtWidgets.QDoubleSpinBox):
    """Spin box with a blank state for mixed selections.

    zero_text shows 0 as a word (e.g. leading 0 = 'Auto').
    """

    def __init__(self, lo, hi, step, decimals, parent=None, zero_text=None):
        QtWidgets.QDoubleSpinBox.__init__(self, parent)
        self.zero_text = zero_text
        self.sentinel = lo - step
        self.setRange(self.sentinel, hi)
        self.setSingleStep(step)
        self.setDecimals(decimals)
        self.setSpecialValueText(" ")
        self.setKeyboardTracking(False)
        self.setMinimumWidth(70)
        self.setLocale(QtCore.QLocale.c())  # '1.5', not '1,5' on comma-decimal locales

    def textFromValue(self, value):
        if self.zero_text and abs(value) < 1e-9:
            return self.zero_text
        return QtWidgets.QDoubleSpinBox.textFromValue(self, value)

    def valueFromText(self, text):
        if self.zero_text and text.strip().lower() == self.zero_text.lower():
            return 0.0
        return QtWidgets.QDoubleSpinBox.valueFromText(self, text)

    def validate(self, text, pos):
        if self.zero_text and self.zero_text.lower().startswith(text.strip().lower()) and text.strip():
            state = (QtGui.QValidator.Acceptable if text.strip().lower() == self.zero_text.lower()
                     else QtGui.QValidator.Intermediate)
            return state, text, pos
        return QtWidgets.QDoubleSpinBox.validate(self, text, pos)

    def set_mixed(self):
        self.setValue(self.sentinel)

    def is_mixed(self):
        return self.value() <= self.sentinel + 1e-9


class TypeEditor(QtWidgets.QDialog):
    def __init__(self, node, parent=None):
        QtWidgets.QDialog.__init__(self, parent)
        self.node = node
        self.setWindowTitle("AX Type - %s" % node.path())
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setModal(False)
        self.resize(860, 480)
        try:
            self.setStyleSheet(hou.qt.styleSheet())
        except Exception:
            pass

        self._syncing = False
        self._fmt_cache = {}
        self._read_node_styles()
        self._original = node.parm("text").eval()

        self._write_timer = QtCore.QTimer(self)
        self._write_timer.setSingleShot(True)
        self._write_timer.setInterval(300)
        self._write_timer.timeout.connect(self.write_to_node)

        self._build_ui()
        self.load_from_node()

        self._cb = self._on_node_deleted
        try:
            node.addEventCallback((hou.nodeEventType.BeingDeleted,), self._cb)
        except Exception:
            self._cb = None

    def _on_node_deleted(self, **kwargs):
        self._cb = None
        self._write_timer.stop()
        try:
            self.close()
        except RuntimeError:
            pass

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        lay = QtWidgets.QVBoxLayout(self)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(QtWidgets.QLabel("Style"))
        self.style_combo = QtWidgets.QComboBox()
        self.style_combo.setMinimumWidth(140)
        self.style_combo.activated.connect(self._on_style)
        row1.addWidget(self.style_combo)
        b = QtWidgets.QPushButton("New Style from Selection")
        b.clicked.connect(self.new_style_from_selection)
        row1.addWidget(b)
        b = QtWidgets.QPushButton("Clear Format")
        b.setToolTip("Remove style and overrides from the selection")
        b.clicked.connect(lambda: self.apply(lambda st, ov: (None, {})))
        row1.addWidget(b)
        row1.addStretch(1)
        self.align_group = QtWidgets.QButtonGroup(self)
        for key, label in (("left", "Left"), ("center", "Center"), ("right", "Right"),
                           ("justify", "Justify")):
            tb = QtWidgets.QToolButton()
            tb.setText(label)
            tb.setCheckable(True)
            tb.setProperty("align", key)
            tb.setToolTip("Align selected lines: %s" % label)
            self.align_group.addButton(tb)
            row1.addWidget(tb)
        self.align_group.setExclusive(True)
        self.align_group.buttonClicked.connect(lambda btn: self.set_align(btn.property("align")))
        lay.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("Font"))
        self.font_combo = QtWidgets.QComboBox()
        self.font_combo.setEditable(True)
        self.font_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.font_combo.setMinimumWidth(230)
        names = fonts.menu_items(_extra_dirs(self.node))[::2]
        self.font_combo.addItems(names)
        comp = QtWidgets.QCompleter(names, self.font_combo)
        comp.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        comp.setFilterMode(QtCore.Qt.MatchContains)
        self.font_combo.setCompleter(comp)
        self.font_combo.activated.connect(lambda i: self._on_font())
        self.font_combo.lineEdit().returnPressed.connect(self._on_font)
        row2.addWidget(self.font_combo, 1)

        self.spins = {}
        for key, label, lo, hi, step, dec, zero in (
                ("size", "Size", 0.001, 1000, 0.1, 3, None),
                ("leading", "Leading", 0.0, 1000, 0.1, 3, "Auto"),
                ("track", "Track", -1000, 5000, 10, 1, None),
                ("shift", "Shift", -100, 100, 0.05, 3, None)):
            row2.addWidget(QtWidgets.QLabel(label))
            sp = _Spin(lo, hi, step, dec, zero_text=zero)
            if key == "leading":
                sp.setToolTip("Baseline distance for lines containing this text. Auto follows "
                              "the Layout tab")
            sp.valueChanged.connect(lambda v, k=key, s=sp: self._on_spin(k, s))
            self.spins[key] = sp
            row2.addWidget(sp)

        row2.addWidget(QtWidgets.QLabel("Color"))
        self.color_btn = QtWidgets.QPushButton()
        self.color_btn.setFixedWidth(40)
        self.color_btn.clicked.connect(self._pick_color)
        row2.addWidget(self.color_btn)

        row2.addWidget(QtWidgets.QLabel("Case"))
        self.case_combo = QtWidgets.QComboBox()
        for key, label in CASE_LABELS:
            self.case_combo.addItem(label, key)
        self.case_combo.activated.connect(
            lambda i: self.set_prop("case", self.case_combo.itemData(i)))
        row2.addWidget(self.case_combo)
        lay.addLayout(row2)

        self.edit = QtWidgets.QTextEdit()
        self.edit.setAcceptRichText(False)
        self.edit.setLineWrapMode(QtWidgets.QTextEdit.NoWrap)
        self.edit.setStyleSheet("QTextEdit { background: #1b1d21; padding: 8px; }")
        self.edit.document().setDocumentMargin(10)
        self.edit.cursorPositionChanged.connect(self.sync_ui)
        self.edit.selectionChanged.connect(self.sync_ui)
        self.edit.document().contentsChanged.connect(self._on_changed)
        lay.addWidget(self.edit, 1)

        row3 = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel()
        self.status.setStyleSheet("color: #8a909c;")
        row3.addWidget(self.status, 1)
        self.live = QtWidgets.QCheckBox("Live")
        self.live.setChecked(True)
        self.live.setToolTip("Update the node while editing")
        row3.addWidget(self.live)
        for label, fn in (("Reload", self.reload), ("OK", self.accept), ("Cancel", self.reject)):
            b = QtWidgets.QPushButton(label)
            b.clicked.connect(fn)
            row3.addWidget(b)
        lay.addLayout(row3)
        self._refresh_style_combo()

    # ------------------------------------------------------------ styles
    def _read_node_styles(self):
        self.base = read_base(self.node)
        self.styles = read_styles(self.node)
        self.extra = _extra_dirs(self.node)

    def _refresh_style_combo(self):
        cur = self.style_combo.currentData()
        self.style_combo.blockSignals(True)
        self.style_combo.clear()
        self.style_combo.addItem("(none)", "")
        for name in self.styles:
            self.style_combo.addItem(name, name)
        i = self.style_combo.findData(cur)
        self.style_combo.setCurrentIndex(i)
        self.style_combo.blockSignals(False)

    def changeEvent(self, e):
        QtWidgets.QDialog.changeEvent(self, e)
        if e.type() == QtCore.QEvent.ActivationChange and self.isActiveWindow():
            try:
                old = (self.base, self.styles)
                self._read_node_styles()
            except hou.ObjectWasDeleted:
                return
            if (self.base, self.styles) != old:
                self._refresh_style_combo()
                self._restyle_all()

    # ----------------------------------------------------------- formats
    def props_of(self, style, ovr):
        return markup.resolve_props(self.base, self.styles, style, ovr)

    def visual(self, style, ovr):
        key = (style or "", _dump(ovr))
        fmt = self._fmt_cache.get(key)
        if fmt is not None:
            return QtGui.QTextCharFormat(fmt)
        p = self.props_of(style, ovr)
        fam = _qt_family(p["font"], self.extra)
        font = QtGui.QFont(fam[0] if fam else "Arial")
        if fam and fam[1]:
            font.setStyleName(fam[1])
        pt = max(6.0, min(120.0, BASE_PT * p.get("size", 1.0)))
        font.setPointSizeF(pt)
        font.setLetterSpacing(QtGui.QFont.AbsoluteSpacing, p.get("track", 0.0) / 1000.0 * pt)
        font.setCapitalization(CAPS.get(p.get("case"), QtGui.QFont.MixedCase))
        font.setKerning(bool(p.get("kern", True)))
        if p.get("slant", 0.0):
            font.setItalic(True)
        fmt = QtGui.QTextCharFormat()
        fmt.setFont(font)
        r, g, b = [max(0.0, min(1.0, c)) for c in p.get("color", (1, 1, 1))]
        fmt.setForeground(QtGui.QColor.fromRgbF(r, g, b, max(0.15, min(1.0, p.get("alpha", 1.0)))))
        if style or ovr:
            fmt.setToolTip(self._describe(style, ovr))
        fmt.setProperty(P_STYLE, style or "")
        fmt.setProperty(P_OVR, _dump(ovr))
        self._fmt_cache[key] = fmt
        return QtGui.QTextCharFormat(fmt)

    @staticmethod
    def data_of(fmt):
        style = fmt.property(P_STYLE) or None
        return style, _load(fmt.property(P_OVR))

    def _describe(self, style, ovr):
        parts = ["style: %s" % (style or "(none)")]
        for k in markup.PROP_KEYS:
            if k in ovr:
                parts.append("%s: %s" % (k, markup.format_value(k, ovr[k])))
        return "  |  ".join(parts)

    def _restyle_all(self):
        self._fmt_cache.clear()
        doc = self.edit.document()
        n = doc.characterCount() - 1
        self._restyle_range(0, n)

    def _char_data(self, start, end):
        """[(start, end, style, ovr)] runs of identical formatting in [start, end)."""
        doc = self.edit.document()
        cur = QtGui.QTextCursor(doc)
        runs = []
        for pos in range(start, end):
            if doc.characterAt(pos) == PARA_SEP:
                continue
            cur.setPosition(pos + 1)
            style, ovr = self.data_of(cur.charFormat())
            if runs and runs[-1][1] == pos and runs[-1][2] == style and runs[-1][3] == ovr:
                runs[-1][1] = pos + 1
            else:
                runs.append([pos, pos + 1, style, ovr])
        return runs

    def _restyle_range(self, start, end, fn=None):
        doc = self.edit.document()
        cur = QtGui.QTextCursor(doc)
        cur.beginEditBlock()
        for s, e, style, ovr in self._char_data(start, end):
            if fn is not None:
                style, ovr = fn(style, ovr)
                ovr = markup.prune(self.props_of(style, ovr), self.base, self.styles, style)
            cur.setPosition(s)
            cur.setPosition(e, QtGui.QTextCursor.KeepAnchor)
            cur.setCharFormat(self.visual(style, ovr))
        cur.endEditBlock()

    # ------------------------------------------------------------ editing
    def apply(self, fn):
        """fn(style, overrides) -> (style, overrides), applied to the selection."""
        cur = self.edit.textCursor()
        if not cur.hasSelection():
            style, ovr = self.data_of(self.edit.currentCharFormat())
            style, ovr = fn(style, ovr)
            ovr = markup.prune(self.props_of(style, ovr), self.base, self.styles, style)
            self.edit.setCurrentCharFormat(self.visual(style, ovr))
            self.sync_ui()
            return
        self._restyle_range(cur.selectionStart(), cur.selectionEnd(), fn)
        self.sync_ui()

    def set_prop(self, key, value):
        if value is None:
            return
        self.apply(lambda st, ov: (st, dict(ov, **{key: value})))

    def set_style(self, name):
        """Apply a style; it replaces overrides of the properties it defines."""
        defined = self.styles.get(name, {}) if name else {}
        self.apply(lambda st, ov: (name or None,
                                   {k: v for k, v in ov.items() if k not in defined}))

    def set_align(self, align):
        cur = self.edit.textCursor()
        doc = self.edit.document()
        block = doc.findBlock(cur.selectionStart())
        last = doc.findBlock(cur.selectionEnd())
        c = QtGui.QTextCursor(doc)
        c.beginEditBlock()
        while block.isValid():
            c.setPosition(block.position())
            bf = block.blockFormat()
            bf.setAlignment(ALIGN_TO_QT[align])
            c.setBlockFormat(bf)
            if block == last:
                break
            block = block.next()
        c.endEditBlock()

    def _on_style(self, i):
        self.set_style(self.style_combo.itemData(i))

    def _on_font(self):
        if self._syncing:
            return
        name = self.font_combo.currentText().strip()
        if not name:
            return
        if fonts.resolve(name, self.extra) is None:
            self.status.setText("Font '%s' not found" % name)
            return
        self.set_prop("font", name)

    def _on_spin(self, key, spin):
        if self._syncing or spin.is_mixed():
            return
        self.set_prop(key, float(spin.value()))

    def _pick_color(self):
        p = self._cursor_props()
        start = p[0].get("color", (1, 1, 1)) if p else (1, 1, 1)
        rgb = None
        try:
            c = hou.ui.selectColor(hou.Color(start))
            rgb = c.rgb() if c is not None else None
        except Exception:
            q = QtWidgets.QColorDialog.getColor(QtGui.QColor.fromRgbF(*start), self)
            rgb = (q.redF(), q.greenF(), q.blueF()) if q.isValid() else None
        if rgb is not None:
            self.set_prop("color", tuple(float(c) for c in rgb))

    # -------------------------------------------------------------- sync
    def _selection_data(self):
        cur = self.edit.textCursor()
        if cur.hasSelection():
            runs = self._char_data(cur.selectionStart(), cur.selectionEnd())
            if runs:
                return [(st, ov) for _s, _e, st, ov in runs]
        return [self.data_of(self.edit.currentCharFormat())]

    def _cursor_props(self):
        return [self.props_of(st, ov) for st, ov in self._selection_data()]

    def sync_ui(self):
        self._syncing = True
        try:
            data = self._selection_data()
            props = [self.props_of(st, ov) for st, ov in data]

            def common(key):
                vals = [p.get(key) for p in props]
                return vals[0] if all(markup.values_equal(v, vals[0]) for v in vals) else None

            styles = set(st or "" for st, _ov in data)
            self.style_combo.setCurrentIndex(
                self.style_combo.findData(styles.pop()) if len(styles) == 1 else -1)
            font = common("font")
            self.font_combo.setEditText(font if font is not None else "")
            for key, sp in self.spins.items():
                v = common(key)
                if v is None:
                    sp.set_mixed()
                else:
                    sp.setValue(v)
            case = common("case")
            self.case_combo.setCurrentIndex(self.case_combo.findData(case) if case else -1)
            color = common("color")
            if color is not None:
                q = QtGui.QColor.fromRgbF(*[max(0.0, min(1.0, c)) for c in color])
                self.color_btn.setStyleSheet("background: %s;" % q.name())
            else:
                self.color_btn.setStyleSheet("")
            align = self._block_align(self.edit.textCursor().block())
            for btn in self.align_group.buttons():
                btn.setChecked(btn.property("align") == align)
            if len(data) == 1:
                self.status.setText(self._describe(*data[0]))
            else:
                self.status.setText("Mixed selection (%d runs)" % len(data))
        finally:
            self._syncing = False

    # ------------------------------------------------------- node <-> doc
    def _block_align(self, block):
        a = block.blockFormat().alignment()
        for key, qa in ALIGN_TO_QT.items():
            if a & qa == qa and qa != QtCore.Qt.AlignLeft:
                return key
        return "left"

    def to_markup(self):
        doc = self.edit.document()
        lines = []
        block = doc.begin()
        while block.isValid():
            start = block.position()
            end = start + len(block.text())
            runs = []
            for s, e, style, ovr in self._char_data(start, end):
                runs.append((block.text()[s - start:e - start], style, ovr))
            lines.append((self._block_align(block), runs))
            block = block.next()
        return markup.serialize(lines, self.base.get("align", "left"))

    def set_markup(self, text):
        segs = markup.parse(text, self.base, self.styles)
        lines = markup.char_runs(segs, self.base, self.styles) or [(self.base["align"], [])]
        doc = self.edit.document()
        self._loading = True
        doc.blockSignals(True)
        try:
            doc.clear()
            cur = QtGui.QTextCursor(doc)
            for i, (align, runs) in enumerate(lines):
                if i > 0:
                    cur.insertBlock()
                bf = QtGui.QTextBlockFormat()
                bf.setAlignment(ALIGN_TO_QT.get(align, QtCore.Qt.AlignLeft))
                cur.setBlockFormat(bf)
                cur.setBlockCharFormat(self.visual(None, {}))
                for text, style, ovr in runs:
                    cur.insertText(text, self.visual(style, ovr))
        finally:
            doc.blockSignals(False)
            self._loading = False
        self.edit.document().clearUndoRedoStacks()
        c = self.edit.textCursor()
        c.movePosition(QtGui.QTextCursor.Start)
        self.edit.setTextCursor(c)
        self.edit.setCurrentCharFormat(self.visual(None, {}) if doc.isEmpty() else c.charFormat())
        self.sync_ui()

    def load_from_node(self):
        self.set_markup(self.node.parm("text").eval())

    def reload(self):
        self._read_node_styles()
        self._fmt_cache.clear()
        self._refresh_style_combo()
        self.load_from_node()

    def write_to_node(self):
        try:
            text = self.to_markup()
            parm = self.node.parm("text")
            if parm.unexpandedString() != text:
                with hou.undos.group("AX Type: edit text"):
                    parm.set(text)
        except hou.ObjectWasDeleted:
            pass

    def _on_changed(self):
        if getattr(self, "_loading", False):
            return
        if self.live.isChecked():
            self._write_timer.start()

    # ------------------------------------------------------------- styles
    def new_style_from_selection(self):
        data = self._selection_data()
        props = self.props_of(*data[0])
        name, ok = QtWidgets.QInputDialog.getText(self, "New Character Style", "Style name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        if not re.match(r"^[A-Za-z_][\w\-]*$", name):
            self.status.setText("Style names need letters, digits, '_' or '-', no spaces")
            return
        if name in self.styles:
            self.status.setText("Style '%s' already exists" % name)
            return
        self.create_style(name, props)
        self.set_style(name)

    def create_style(self, name, props):
        """Add a Character Styles entry holding props that differ from the base."""
        diff = markup.prune(props, self.base, {}, None)
        node = self.node
        with hou.undos.group("AX Type: new style %s" % name):
            mp = node.parm("styles")
            i = mp.eval() + 1
            mp.set(i)
            node.parm("sname%d" % i).set(name)
            for key, suffix, kind in STYLE_PROPS:
                if key not in diff or node.parm("suse_%s%d" % (suffix, i)) is None:
                    continue
                node.parm("suse_%s%d" % (suffix, i)).set(1)
                v = diff[key]
                if kind == "color":
                    node.parmTuple("s%s%d" % (suffix, i)).set(v)
                elif kind == "toggle":
                    node.parm("s%s%d" % (suffix, i)).set(int(v))
                else:
                    node.parm("s%s%d" % (suffix, i)).set(v)
        self._read_node_styles()
        self._fmt_cache.clear()
        self._refresh_style_combo()

    # -------------------------------------------------------------- close
    def accept(self):
        self._write_timer.stop()
        self.write_to_node()
        QtWidgets.QDialog.accept(self)

    def reject(self):
        self._write_timer.stop()
        try:
            parm = self.node.parm("text")
            if parm.unexpandedString() != self._original:
                with hou.undos.group("AX Type: cancel edit"):
                    parm.set(self._original)
        except hou.ObjectWasDeleted:
            pass
        QtWidgets.QDialog.reject(self)

    def closeEvent(self, e):
        if self._write_timer.isActive():
            self._write_timer.stop()
            self.write_to_node()
        self._cleanup()
        QtWidgets.QDialog.closeEvent(self, e)

    def done(self, r):
        self._cleanup()
        QtWidgets.QDialog.done(self, r)

    def _cleanup(self):
        if self._cb is not None:
            try:
                self.node.removeEventCallback((hou.nodeEventType.BeingDeleted,), self._cb)
            except Exception:
                pass
            self._cb = None
        for key, ed in list(_editors.items()):
            if ed is self:
                del _editors[key]
