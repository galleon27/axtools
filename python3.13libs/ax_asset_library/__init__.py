"""AX Asset Library: browse a folder library of assets and load them into SOP, OBJ or LOP.

Panel entry point (python_panels/ax_asset_library.pypanel):
    import ax_asset_library; ax_asset_library.create_panel()

schema, library and prefs have no hou/Qt dependency at import time; the Houdini and UI
modules load on first use.
"""
import importlib
import sys

from . import schema, library, prefs  # noqa: F401

# Reload order: dependencies first.
_SUBMODULES = ("schema", "library", "prefs", "textures", "variants", "megascans", "gsg", "karma", "thumbnails", "materials",
               "importers", "publish", "manage", "scene", "dragdrop",
               "ui.grid", "ui.details", "ui.dialogs", "ui.panel")


def create_panel():
    from .ui import panel
    return panel.AssetLibraryPanel()


def open_floating_panel():
    """Open the panel in a floating pane (used by the AX Tools menu)."""
    import hou
    pane = hou.ui.curDesktop().createFloatingPaneTab(
        hou.paneTabType.PythonPanel, (300, 300), (1300, 800), "ax_asset_library")
    if pane:
        pane.floatingPanel().setName("AX Asset Library")
    return pane


def dev_reload():
    """Reload every loaded submodule so code edits apply without restarting Houdini.

    Close and reopen the panel afterwards to get a widget built from the new code.
    Also drops the module-level thumbnail cache.
    """
    for name in _SUBMODULES:
        full = __name__ + "." + name
        if full in sys.modules:
            importlib.reload(sys.modules[full])
    return importlib.reload(sys.modules[__name__])
