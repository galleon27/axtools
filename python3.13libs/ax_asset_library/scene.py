"""Library assets in the open scene: find the nodes the panel created, update them to
another version, and relink them after an asset was renamed or moved.

Nodes are found by the user data the importers set (ax_asset_id / ax_asset_version).
"""
import os

import hou

from . import importers, materials, megascans, schema, variants
from .textures import CHANNELS

# Parms that hold the asset file on nodes the importers create.
FILE_PARMS = ("file", "fileName", "filepath1", "env_map") + importers.DOME_TEXTURE_PARMS
# Texture parm per material renderer; texture nodes are named after their channel.
TEXTURE_PARMS = ("file", "A_FILENAME", "tex0")


def asset_nodes():
    """{asset id: [nodes]} for every tagged node in the scene (locked HDAs not entered)."""
    out = {}
    for node in hou.node("/").allSubChildren(recurse_in_locked_nodes=False):
        try:
            aid = node.userData(importers.UD_ID)
        except hou.Error:
            continue
        if aid:
            out.setdefault(aid, []).append(node)
    return out


def node_version(node):
    try:
        return int(node.userData(importers.UD_VERSION) or 0)
    except (TypeError, ValueError):
        return 0


class Usage:
    """How one asset is used in the scene."""

    def __init__(self, rec, nodes):
        self.rec = rec
        self.nodes = nodes
        self.versions = sorted({node_version(n) for n in nodes})

    @property
    def outdated(self):
        return [n for n in self.nodes if node_version(n) < self.rec.current]

    def summary(self):
        labels = ", ".join(schema.version_label(v) for v in self.versions if v)
        return "%d node(s) using %s" % (len(self.nodes), labels or "unknown version")


def usage(records, nodes_by_id=None):
    """{record key: Usage} for records used in the scene."""
    nodes_by_id = asset_nodes() if nodes_by_id is None else nodes_by_id
    out = {}
    for rec in records:
        nodes = nodes_by_id.get(rec.id) if rec.id else None
        if nodes:
            out[rec.key] = Usage(rec, nodes)
    return out


# --- updating nodes ------------------------------------------------------------------------
def _same_lod(files, current):
    """The files at the LOD the node loads now (an LOD picked at import), else `files`."""
    rank = megascans.lod_rank(current) if current else -1
    if rank < 0:
        return files
    lods = variants.lod_files(files)
    pick = variants.pick_lod(list(lods), rank)
    return lods.get(pick, files)


def _same_resolution(files, current, cache):
    """{channel: file} at the texture resolution the node uses now (picked at import)."""
    m = variants.RES_RE.search(os.path.basename(current or ""))
    if not m:
        return files
    if "sets" not in cache:
        cache["sets"] = variants.resolution_sets(files)
    sets = cache["sets"]
    pick = variants.pick_resolution(list(sets), "%sK" % m.group(1))
    return sets.get(pick, files)


def _geometry_value(rec, settings, version, current):
    """Parm value for a geometry loader, keeping the format the node loads now if possible."""
    files = _same_lod(rec.files_for(version), current)
    key = schema.format_key(current) if current else None
    if key in files:
        return importers.to_parm(rec, settings, files[key])
    return importers.file_for(rec, settings, version)[1]


REFUSED = None


def _channel(node):
    """Material channel of a texture node: user data from the builder, else its name."""
    try:
        return node.userData(materials.CHANNEL_KEY) or node.name()
    except hou.Error:
        return node.name()


def _update_node(node, rec, settings, version, warnings):
    """Point one node at `version`.

    Returns True if a parm changed, False if it already pointed there, REFUSED (None) when
    the node can't use that version (a warning says why) and must keep its version tag.
    """
    changed = False
    for name in FILE_PARMS:
        parm = node.parm(name)
        if parm is None:
            continue
        current = parm.unexpandedString()
        if rec.type == "hdri":
            files = rec.files_for(version)
            value = importers.to_parm(rec, settings, files["hdri"]) if "hdri" in files else None
        else:
            value = _geometry_value(rec, settings, version, current)
        if value is None:
            warnings.append("%s: %s has no file in %s." % (node.path(), rec.name,
                                                             schema.version_label(version)))
            return REFUSED
        if schema.file_ext(value) != schema.file_ext(current) and node.type().name() == "alembic":
            warnings.append("%s: %s has no Alembic file; node left as is." % (
                node.path(), schema.version_label(version)))
            return REFUSED
        if value != current:
            parm.set(value)
            changed = True
        return changed

    if rec.type == "material" and node.type().name() != "materiallibrary":
        files = rec.files_for(version)
        res_cache = {}
        missing = set()
        for child in node.allSubChildren():
            channel = _channel(child)
            if not any(child.parm(p) for p in TEXTURE_PARMS):
                continue
            if channel not in files:
                if channel in CHANNELS:
                    missing.add(channel)
                continue
            for pname in TEXTURE_PARMS:
                parm = child.parm(pname)
                if parm is None:
                    continue
                chosen = _same_resolution(files, parm.unexpandedString(), res_cache)
                value = importers.to_parm(rec, settings, chosen.get(channel, files[channel]))
                if parm.unexpandedString() != value:
                    parm.set(value)
                    changed = True
                break
        if missing:
            warnings.append("%s: %s has no %s map; those texture nodes keep their old file." % (
                node.path(), schema.version_label(version), ", ".join(sorted(missing))))
    return changed


def update_nodes(rec, nodes, settings, version=None):
    """Point `nodes` (tagged with rec's id) at `version` (current when None).

    version="keep" relinks each node to the version it already uses (after a rename or
    move). Returns (changed node count, warnings).
    """
    warnings, changed = [], 0
    with hou.undos.group("AX Asset Library: update %s" % rec.name):
        for node in nodes:
            use = node_version(node) if version == "keep" else (version or rec.current)
            if use not in rec.versions:
                warnings.append("%s: %s no longer exists in %s." % (
                    node.path(), schema.version_label(use), rec.name))
                continue
            try:
                result = _update_node(node, rec, settings, use, warnings)
                if result is REFUSED:
                    continue
                if result:
                    changed += 1
            except (hou.Error, importers.AssetImportError) as e:
                msg = e.instanceMessage() if isinstance(e, hou.Error) else str(e)
                warnings.append("%s: %s" % (node.path(), msg))
                continue
            node.setUserData(importers.UD_VERSION, str(use))
            root = settings.root_for(rec.root)
            node.setUserData(importers.UD_ROOT, root["path"] if root else rec.root)
            node.setComment("%s %s" % (rec.name, schema.version_label(use)))
    return changed, warnings


def select_nodes(nodes):
    """Select the nodes and frame the first one's network in a network editor."""
    nodes = [n for n in nodes if n is not None]
    if not nodes:
        return
    for i, n in enumerate(nodes):
        n.setSelected(True, clear_all_selected=(i == 0))
    nodes[0].setCurrent(True, clear_all_selected=False)
    try:
        editor = importers.target_editor()
    except (hou.Error, AttributeError):  # no UI (hython)
        editor = None
    if editor is not None:
        try:
            editor.setPwd(nodes[0].parent())
            editor.frameSelection()
        except hou.Error:
            pass
