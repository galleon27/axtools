"""Set library root variables at Houdini startup, before any .hip file loads.

Imported node parms store $MEGASCANS/... (or another root variable), so the variables must
exist when a .hip file is read. Import the prefs submodule only, so the panel UI is not
loaded.
"""
try:
    from ax_asset_library import prefs

    prefs.apply_root_vars(prefs.Prefs())
except Exception:
    pass
