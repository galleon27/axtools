"""Minimal OpenType shaping: case, GSUB single/ligature substitution, kerning."""
import re


class Glyph(object):
    """One shaped glyph. chars are indices into the source text."""
    __slots__ = ("name", "text", "chars", "scale", "advance", "kern")

    def __init__(self, name, text, chars, scale=1.0, advance=0.0):
        self.name = name
        self.text = text
        self.chars = chars
        self.scale = scale      # faux small caps scale
        self.advance = advance  # em units, before scale
        self.kern = 0.0         # em units, added after this glyph


# --------------------------------------------------------------------------
# Case
# --------------------------------------------------------------------------

_WORD_START = re.compile(r"(^|[\s\-/(\"'‘“])(\w)", re.U)


def apply_case(text, case, prev_char=""):
    """Case transform keeping a 1:1 char mapping (needed for attributes)."""
    if case == "upper":
        return "".join(c.upper() if len(c.upper()) == 1 else c for c in text)
    if case == "lower":
        return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)
    if case == "title":
        out = []
        prev = prev_char
        for c in text:
            if c.isalpha() and (not prev or not (prev.isalnum() or prev in "'’")):
                u = c.upper()
                out.append(u if len(u) == 1 else c)
            else:
                lo = c.lower()
                out.append(lo if len(lo) == 1 else c)
            prev = c
        return "".join(out)
    return text


# --------------------------------------------------------------------------
# GSUB / GPOS helpers
# --------------------------------------------------------------------------

def _feature_lookups(table, tag):
    """Lookup indices for a feature tag (union across scripts)."""
    if table is None or not table.table.FeatureList:
        return []
    idx = set()
    for fr in table.table.FeatureList.FeatureRecord:
        if fr.FeatureTag == tag:
            idx.update(fr.Feature.LookupListIndex)
    return sorted(idx)


def _subtables(lookup, ext_type):
    """Yield (lookup type, subtable), unwrapping extension lookups."""
    for st in lookup.SubTable:
        if lookup.LookupType == ext_type:
            yield st.ExtensionLookupType, st.ExtSubTable
        else:
            yield lookup.LookupType, st


def _gsub_data(font, tag):
    """Cached {'single': {g: g2}, 'liga': {first: [(components, lig)]}}."""
    data = font._gsub.get(tag)
    if data is not None:
        return data
    single, liga = {}, {}
    gsub = font.table("GSUB")
    if gsub is not None and gsub.table.LookupList:
        lookups = gsub.table.LookupList.Lookup
        for li in _feature_lookups(gsub, tag):
            for ltype, st in _subtables(lookups[li], 7):
                if ltype == 1 and st.mapping:
                    for k, v in st.mapping.items():
                        single.setdefault(k, v)
                elif ltype == 4 and st.ligatures:
                    for first, ligs in st.ligatures.items():
                        lst = liga.setdefault(first, [])
                        for lig in ligs:
                            lst.append((tuple(lig.Component), lig.LigGlyph))
    for lst in liga.values():
        lst.sort(key=lambda t: -len(t[0]))   # longest match first
    data = {"single": single, "liga": liga}
    font._gsub[tag] = data
    return data


class _Kerning(object):
    def __init__(self, font):
        self.pairs = []    # list of per-lookup (format1 dict, [format2 subtables])
        self.legacy = {}
        self.cache = {}
        gpos = font.table("GPOS")
        if gpos is not None and gpos.table.LookupList:
            lookups = gpos.table.LookupList.Lookup
            for li in _feature_lookups(gpos, "kern"):
                fmt1, fmt2 = {}, []
                for ltype, st in _subtables(lookups[li], 9):
                    if ltype != 2:
                        continue
                    if st.Format == 1:
                        cov = st.Coverage.glyphs
                        for gi, ps in enumerate(st.PairSet):
                            first = cov[gi]
                            d = fmt1.setdefault(first, {})
                            for rec in ps.PairValueRecord:
                                v = rec.Value1
                                xa = getattr(v, "XAdvance", 0) if v is not None else 0
                                if xa and rec.SecondGlyph not in d:
                                    d[rec.SecondGlyph] = xa
                    elif st.Format == 2:
                        fmt2.append((set(st.Coverage.glyphs),
                                     st.ClassDef1.classDefs if st.ClassDef1 else {},
                                     st.ClassDef2.classDefs if st.ClassDef2 else {},
                                     st.Class1Record))
                if fmt1 or fmt2:
                    self.pairs.append((fmt1, fmt2))
        if not self.pairs and "kern" in font.tt:
            try:
                for kt in font.tt["kern"].kernTables:
                    if getattr(kt, "format", 0) == 0 and getattr(kt, "coverage", 1) & 1:
                        for k, v in kt.kernTable.items():
                            self.legacy.setdefault(k, v)
            except Exception:
                pass
        self.upem = font.upem

    def value(self, a, b):
        key = (a, b)
        v = self.cache.get(key)
        if v is not None:
            return v
        total = 0
        for fmt1, fmt2 in self.pairs:
            d = fmt1.get(a)
            if d is not None and b in d:
                total += d[b]
                continue
            for cov, cd1, cd2, recs in fmt2:
                if a in cov:
                    c1 = cd1.get(a, 0)
                    c2 = cd2.get(b, 0)
                    try:
                        val = recs[c1].Class2Record[c2].Value1
                    except (IndexError, AttributeError):
                        val = None
                    xa = getattr(val, "XAdvance", 0) if val is not None else 0
                    if xa:
                        total += xa
                    break   # first subtable covering 'a' wins
        if not self.pairs:
            total = self.legacy.get(key, 0)
        v = total / self.upem
        self.cache[key] = v
        return v


def kerning(font, a, b):
    if font._kern is None:
        font._kern = _Kerning(font)
    return font._kern.value(a, b)


# --------------------------------------------------------------------------
# Shaping a run
# --------------------------------------------------------------------------

def shape(font, text, char_offset, props, missing):
    """Shape a single-font run into Glyphs.

    text has already had case applied (except small caps). char_offset is the
    index of text[0] in the full source string. Unknown characters are added
    to the `missing` set.
    """
    case = props.get("case", "none")
    sc_scale = props.get("smallcaps_scale", 0.75)
    glyphs = []
    smcp = _gsub_data(font, "smcp")["single"] if case == "smallcaps" else None

    for i, ch in enumerate(text):
        idx = char_offset + i
        if ch in "\n\r":
            continue
        scale = 1.0
        g = font.glyph(ch)
        if smcp is not None and ch.islower():
            real = smcp.get(g) if g else None
            if real:
                g = real
            else:
                up = ch.upper()
                gu = font.glyph(up) if len(up) == 1 else None
                if gu:
                    g = gu
                    scale = sc_scale
        if not g:
            if not ch.isspace():
                missing.add(ch)
            g = font.glyph(" ") if ch.isspace() else ".notdef"
            if not font.has_glyph(g or ""):
                g = ".notdef"
        glyphs.append(Glyph(g, ch, [idx], scale))

    if props.get("liga", True) and glyphs:
        liga = _gsub_data(font, "liga")["liga"]
        if liga:
            out = []
            i = 0
            n = len(glyphs)
            while i < n:
                gl = glyphs[i]
                cands = liga.get(gl.name) if gl.scale == 1.0 else None
                done = False
                if cands:
                    for comps, lig in cands:
                        k = len(comps)
                        if i + k < n and all(
                                glyphs[i + 1 + j].name == comps[j]
                                and glyphs[i + 1 + j].scale == 1.0 for j in range(k)):
                            parts = glyphs[i:i + 1 + k]
                            out.append(Glyph(lig, "".join(p.text for p in parts),
                                             [c for p in parts for c in p.chars]))
                            i += 1 + k
                            done = True
                            break
                if not done:
                    out.append(gl)
                    i += 1
            glyphs = out

    for gl in glyphs:
        gl.advance = font.advance(gl.name)

    if props.get("kern", True):
        for a, b in zip(glyphs, glyphs[1:]):
            if a.scale == b.scale:
                a.kern = kerning(font, a.name, b.name) * a.scale
    return glyphs
