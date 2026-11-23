"""Drag-and-drop of library assets onto the network editor.

Registers a nodegraphdrop handler (Houdini 20.5+) that turns a drop into the same import
as double-click, at the drop position. Drag payloads are a small JSON document carrying
asset keys; the grid also keeps its file URLs and parm text for parameter fields.
"""
import json
import weakref

import hou

from . import materials

MIME = "application/json"
PAYLOAD_TYPE = "ax_asset_library"


def payload_bytes(records, panel=None):
    """JSON drag payload for `records` (library.Record-like objects with key and type).

    `panel` is the panel the drag starts from; its id lets the drop find it again when
    several Asset Library panels are open."""
    return json.dumps({"type": PAYLOAD_TYPE,
                       "panel": id(panel) if panel is not None else None,
                       "keys": [r.key for r in records],
                       "types": [r.type for r in records]}).encode("utf-8")


def read_payload(source):
    """The parsed payload from a drag source, or None for any other drag.

    source.data() yields the drag data in chunks (str, bytes, or a single one of them);
    malformed or foreign data is ignored rather than raising into Houdini's dispatcher."""
    try:
        data = source.data(MIME)
        parts = [data] if isinstance(data, (str, bytes)) else list(data)
        chunks = [p if isinstance(p, bytes) else str(p).encode("utf-8") for p in parts]
        payload = json.loads(b"".join(chunks).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("type") != PAYLOAD_TYPE:
        return None
    return payload


def network_position(editor, position):
    """Drop position in network units, clamped to what the editor shows.

    The position passed to a drop handler is not documented for the network editor; it is
    most likely pane pixels, so try posFromScreen first and fall back to network units."""
    try:
        p = hou.Vector2(position[0], position[1])
    except Exception:
        return editor.visibleBounds().center()
    candidates = [p]
    try:
        candidates.insert(0, editor.posFromScreen(p))
    except Exception:
        pass
    bounds = editor.visibleBounds()
    for candidate in candidates:
        try:
            if bounds.contains(candidate):
                return candidate
        except Exception:
            continue
    return bounds.center()


_PANELS = weakref.WeakSet()  # every open Asset Library panel; closed ones are removed


def add_panel(panel):
    _PANELS.add(panel)


def remove_panel(panel):
    _PANELS.discard(panel)


def find_panel(payload):
    """The panel a drag came from (by id), else any open panel that knows its keys."""
    panels = list(_PANELS)
    pid = payload.get("panel")
    for p in panels:
        if pid is not None and id(p) == pid:
            return p
    keys = set(payload.get("keys") or [])
    for p in panels:
        if keys & {r.key for r in getattr(p, "records", [])}:
            return p
    return None


class AssetDropHandler:
    """nodegraphdrop handler: import the dragged records into the drop network."""

    mime_type = MIME

    def accepts(self, source):
        return read_payload(source) is not None

    def choices(self, pane, source):
        payload = read_payload(source) or {}
        types = payload.get("types", [])
        if types and all(t == "material" for t in types):
            tokens, labels, helps = [], [], []
            for key, label in materials.available_renderers():
                tokens.append(key)
                labels.append("Import (materials as %s)" % label)
                helps.append("")
            return tokens, labels, helps
        return ["import"], ["Import"], [""]

    def accept(self, pane, source, position, token):
        payload = read_payload(source)
        if payload is None or not isinstance(pane, hou.NetworkEditor):
            return False
        panel = find_panel(payload)
        if panel is None:
            return False
        renderers = dict(materials.available_renderers())
        renderer = token if token in renderers else None
        return panel.import_dropped(payload["keys"], pane,
                                    network_position(pane, position), renderer)


def register(panel):
    """Hook the panel into the network editor drop registry; False on older Houdini."""
    add_panel(panel)
    try:
        import nodegraphdrop
    except ImportError:
        return False
    for handler in nodegraphdrop.handlers():
        # After dev_reload the class object differs, so match by name.
        if type(handler).__name__ == "AssetDropHandler":
            nodegraphdrop.unregisterHandler(handler)
    nodegraphdrop.registerHandler(AssetDropHandler())
    return True
