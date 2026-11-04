"""Patches for the fontTools build bundled with Houdini.

SideFX ships fontTools 4.55.4 without fontTools/ttLib/tables/_h_e_a_d.py, so
any font fails to load ('head' falls back to DefaultTable). This registers a
read-only replacement of the upstream module before fontTools looks for it.
"""
import sys
import types


HEAD_FORMAT = """
    >   # big endian
    tableVersion:       16.16F
    fontRevision:       16.16F
    checkSumAdjustment: I
    magicNumber:        I
    flags:              H
    unitsPerEm:         H
    created:            Q
    modified:           Q
    xMin:               h
    yMin:               h
    xMax:               h
    yMax:               h
    macStyle:           H
    lowestRecPPEM:      H
    fontDirectionHint:  h
    indexToLocFormat:   h
    glyphDataFormat:    h
"""

_MODNAME = "fontTools.ttLib.tables._h_e_a_d"


def ensure_head_table():
    if _MODNAME in sys.modules:
        return
    try:
        __import__(_MODNAME)
        return
    except ImportError:
        pass

    from fontTools.misc import sstruct
    from fontTools.ttLib.tables import DefaultTable

    class table__h_e_a_d(DefaultTable.DefaultTable):
        dependencies = ["maxp", "loca", "CFF ", "CFF2"]

        def decompile(self, data, ttFont):
            sstruct.unpack2(HEAD_FORMAT, data, self)

        def compile(self, ttFont):
            return sstruct.pack(HEAD_FORMAT, self)

    mod = types.ModuleType(_MODNAME)
    mod.headFormat = HEAD_FORMAT
    mod.table__h_e_a_d = table__h_e_a_d
    sys.modules[_MODNAME] = mod
    import fontTools.ttLib.tables as tables_pkg
    tables_pkg._h_e_a_d = mod


ensure_head_table()
