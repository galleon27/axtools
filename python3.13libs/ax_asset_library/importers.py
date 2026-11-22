"""Create nodes for library assets in the current SOP, OBJ or LOP network.

Every created node carries user data (ax_asset_id, ax_asset_version, ax_asset_root) so the
panel can find library assets in the scene later.
"""
import os

import hou

from . import materials
from . import prefs as _prefs
from . import schema

UD_ID = "ax_asset_id"
UD_VERSION = "ax_asset_version"
UD_ROOT = "ax_asset_root"

# SOP loader per extension; everything else goes through the File SOP, which reads
# bgeo, obj, fbx, ply and USD files.
SOP_LOADERS = {".abc": ("alembic", "fileName")}
DEFAULT_SOP_LOADER = ("file", "file")
LOP_REFERENCE_TYPES = ("reference::2.0", "reference")
LOP_DOME_TYPES = ("domelight::3.0", "domelight::2.0", "domelight")
DOME_TEXTURE_PARMS = ("xn__inputstexturefile_r3ah", "texture_file")
TYPE_KEYS = {"hdri": ["hdri"], "image": ["image"]}

CONTEXT_LABELS = {"sop": "SOP", "obj": "OBJ", "lop": "LOP"}


class AssetImportError(Exception):
    """Import cannot go ahead; the message is shown to the user as is."""


# --- target network ---------------------------------------------------------------
def network_editors():
    return [p for p in hou.ui.paneTabs() if isinstance(p, hou.NetworkEditor)]


def target_editor():
    """Network editor under the cursor, else the current one, else any."""
    pane = hou.ui.paneTabUnderCursor()
    if isinstance(pane, hou.NetworkEditor):
        return pane
    pane = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if pane is not None:
        return pane
    editors = network_editors()
    return editors[0] if editors else None


def context_of(network):
    cat = network.childTypeCategory()
    if cat == hou.sopNodeTypeCategory():
        return "sop"
    if cat == hou.objNodeTypeCategory():
        return "obj"
    if cat == hou.lopNodeTypeCategory():
        return "lop"
    return None


def current_target():
    """(editor, network, context) or raise AssetImportError with a readable reason."""
    editor = target_editor()
    if editor is None:
        raise AssetImportError("Open a network editor first.")
    network = editor.pwd()
    ctx = context_of(network)
    if ctx is None:
        raise AssetImportError(
            "The network editor shows %s. Go to a SOP, OBJ or LOP network to import assets."
            % network.path())
    if network.isInsideLockedHDA():
        raise AssetImportError("%s is inside a locked HDA." % network.path())
    return editor, network, ctx


# --- helpers ------------------------------------------------------------------------
def unique_name(parent, base):
    base = schema.sanitize_name(base)
    if base[0].isdigit():
        base = "_" + base
    name, i = base, 1
    while parent.node(name) is not None:
        name = "%s_%d" % (base, i)
        i += 1
    return name


def _set_first(node, names, value):
    """Set the first of `names` that exists on node; return its name or None."""
    for n in names:
        p = node.parm(n)
        if p is not None:
            p.set(value)
            return n
    return None


def _lop_type(names):
    types = hou.lopNodeTypeCategory().nodeTypes()
    for n in names:
        if n in types:
            return n
    raise AssetImportError("No LOP node type found among: %s" % ", ".join(names))


def _tag_node(node, rec, settings, version):
    root = settings.root_for(rec.root)
    node.setUserData(UD_ID, rec.id or "")
    node.setUserData(UD_VERSION, str(version))
    node.setUserData(UD_ROOT, root["path"] if root else rec.root)  # "$AX_ASSET_LIB" when typed so
    node.setComment("%s %s" % (rec.name, schema.version_label(version)))
    node.setGenericFlag(hou.nodeFlag.DisplayComment, True)


def _anchor(network):
    sel = [n for n in hou.selectedNodes() if n.parent() == network]
    return sel[-1] if sel else None


def _start_position(editor, network, anchor):
    if anchor is not None:
        return anchor.position() + hou.Vector2(0, -1.5)
    if editor is not None and editor.pwd() == network:
        return editor.visibleBounds().center()
    return hou.Vector2(0, 0)


def _root(rec, settings):
    return settings.root_for(rec.root) or {"path": rec.root, "expanded": rec.root}


def to_parm(rec, settings, path):
    return _prefs.parm_path(path, _root(rec, settings), settings.get("path_mode"))


def file_for(rec, settings, version=None):
    """(absolute path, parm path) of the file to load; current version when version is None.

    For a material this is the folder holding its textures.
    """
    files = rec.files_for(version)
    if rec.type == "material":
        key = "basecolor" if "basecolor" in files else schema.pick_file(files, [])
        path = os.path.dirname(files[key]) if key else ""
    else:
        order = TYPE_KEYS.get(rec.type) or settings.get("geo_format_order")
        key = schema.pick_file(files, order)
        path = files.get(key, "") if key else ""
    if not path:
        raise AssetImportError("%s has no files in version %s."
                               % (rec.name, version or rec.current))
    return path, to_parm(rec, settings, path)


def parm_path_for(rec, settings):
    return file_for(rec, settings)[1]


# --- geometry -------------------------------------------------------------------------
def _sop_loader(parent, rec, path, parm_value):
    node_type, parm = SOP_LOADERS.get(schema.file_ext(path), DEFAULT_SOP_LOADER)
    node = parent.createNode(node_type, unique_name(parent, rec.name))
    if _set_first(node, (parm,), parm_value) is None:
        raise AssetImportError("%s has no '%s' parameter." % (node.type().name(), parm))
    return node


def _wire_lop(node, anchor):
    if anchor is not None and anchor.type().category() == hou.lopNodeTypeCategory():
        node.setInput(0, anchor)
        if anchor.isDisplayFlagSet():
            node.setDisplayFlag(True)


def _import_geometry(rec, network, ctx, pos, anchor, settings, version, opts):
    path, value = file_for(rec, settings, version)
    if ctx == "sop":
        node = _sop_loader(network, rec, path, value)
        node.setPosition(pos)
        _tag_node(node, rec, settings, version)
        return node

    if ctx == "obj":
        geo = network.createNode("geo", unique_name(network, rec.name))
        for child in geo.children():  # some setups add a default File SOP
            child.destroy()
        loader = _sop_loader(geo, rec, path, value)
        loader.setDisplayFlag(True)
        loader.setRenderFlag(True)
        geo.setPosition(pos)
        _tag_node(geo, rec, settings, version)
        _tag_node(loader, rec, settings, version)
        return geo

    # LOP: Reference LOP; Houdini's USD plugins read bgeo and abc files as layers too.
    ref = network.createNode(_lop_type(LOP_REFERENCE_TYPES), unique_name(network, rec.name))
    if _set_first(ref, ("filepath1", "filepath"), value) is None:
        raise AssetImportError("%s has no file path parameter." % ref.type().name())
    _set_first(ref, ("primpath1", "primpath"), "/assets/" + schema.sanitize_name(rec.name))
    _wire_lop(ref, anchor)
    ref.setPosition(pos)
    _tag_node(ref, rec, settings, version)
    return ref


# --- materials ----------------------------------------------------------------------------
def _import_material(rec, network, ctx, pos, anchor, settings, version, opts):
    renderer = opts.get("renderer")
    if not renderer:
        raise AssetImportError("No renderer chosen for the material.")
    files = rec.files_for(version)
    maps = {ch: to_parm(rec, settings, p) for ch, p in files.items()}
    disp = (rec.info or {}).get("displacement_scale")
    try:
        if ctx == "lop":
            lib = network.createNode("materiallibrary", unique_name(network, rec.name))
            mat = materials.build(lib, schema.sanitize_name(rec.name), maps, renderer,
                                  lop=True, disp_scale=disp)
            _wire_lop(lib, anchor)
            lib.setPosition(pos)
            _tag_node(lib, rec, settings, version)
            _tag_node(mat, rec, settings, version)
            return lib
        matnet = hou.node("/mat")
        if matnet is None:
            raise AssetImportError("/mat not found.")
        mat = materials.build(matnet, unique_name(matnet, rec.name), maps, renderer,
                              disp_scale=disp)
        mat.moveToGoodPosition()
        _tag_node(mat, rec, settings, version)
        return mat
    except materials.MaterialError as e:
        raise AssetImportError(str(e))


# --- HDRIs / images ---------------------------------------------------------------------
def _import_hdri(rec, network, ctx, pos, anchor, settings, version, opts):
    _path, value = file_for(rec, settings, version)
    if ctx == "sop":
        raise AssetImportError("HDRIs import into an OBJ network (Environment Light) or a "
                               "LOP network (Dome Light).")
    if ctx == "obj":
        light = network.createNode("envlight", unique_name(network, rec.name))
        if _set_first(light, ("env_map",), value) is None:
            raise AssetImportError("envlight has no env_map parameter.")
    else:
        light = network.createNode(_lop_type(LOP_DOME_TYPES), unique_name(network, rec.name))
        if _set_first(light, DOME_TEXTURE_PARMS, value) is None:
            raise AssetImportError("%s has no texture parameter." % light.type().name())
        _wire_lop(light, anchor)
    light.setPosition(pos)
    _tag_node(light, rec, settings, version)
    return light


def _import_image(rec, network, ctx, pos, anchor, settings, version, opts):
    raise AssetImportError("images have no node to create. Use Copy path, or drag the image "
                           "onto a parameter.")


IMPORTERS = {"geometry": _import_geometry, "material": _import_material,
             "hdri": _import_hdri, "image": _import_image}


def import_records(records, settings, version=None, renderer=None):
    """Import records into the current network. Returns (nodes, warnings).

    `version` picks a version other than the current one (single record only).
    `renderer` ("karma", "octane", "redshift") is needed when materials are imported.

    Raises AssetImportError when nothing can be imported (no editor, wrong context,
    unsupported type).
    """
    editor, network, ctx = current_target()
    records = [r for r in records if not r.broken]
    if len(records) != 1:
        version = None
    if not records:
        raise AssetImportError("Nothing to import: the selected assets have errors.")
    unsupported = sorted({schema.TYPE_LABELS.get(r.type, r.type) for r in records
                          if r.type not in IMPORTERS})
    if unsupported and len(unsupported) == len({r.type for r in records}):
        raise AssetImportError("Import for %s assets is not available yet."
                               % ", ".join(unsupported))
    if all(r.type == "image" for r in records):
        raise AssetImportError("Images have no node to create. Use Copy path, or drag the "
                               "image onto a parameter.")
    if ctx == "sop" and all(r.type == "hdri" for r in records):
        raise AssetImportError("HDRIs import into an OBJ network (Environment Light) or a "
                               "LOP network (Dome Light).")
    opts = {"renderer": renderer}

    anchor = _anchor(network)
    pos = _start_position(editor, network, anchor)
    nodes, warnings = [], []
    label = records[0].name if len(records) == 1 else "%d assets" % len(records)
    with hou.undos.group("AX Asset Library: import %s" % label):
        for rec in records:
            fn = IMPORTERS.get(rec.type)
            if fn is None:
                warnings.append("Skipped %s: %s import is not available yet."
                                % (rec.name, schema.TYPE_LABELS.get(rec.type, rec.type)))
                continue
            use = version or rec.current
            missing = sorted(k for k, p in rec.files_for(use).items() if not os.path.exists(p))
            if missing:
                warnings.append("%s: missing file(s) %s." % (rec.name, ", ".join(missing)))
            try:
                node = fn(rec, network, ctx, pos, anchor, settings, use, opts)
            except AssetImportError as e:
                warnings.append("%s: %s" % (rec.name, e))
                continue
            nodes.append(node)
            if node.parent() == network:
                pos = pos + hou.Vector2(3.0, 0)
        for i, node in enumerate(nodes):
            node.setSelected(True, clear_all_selected=(i == 0))
        if nodes:
            nodes[-1].setCurrent(True, clear_all_selected=False)
    return nodes, warnings


def reveal(path):
    """Show a file (or its folder, if the file is gone) in the OS file browser."""
    target = path if os.path.exists(path) else os.path.dirname(path)
    if not os.path.exists(target):
        raise AssetImportError("Not found: %s" % path)
    hou.ui.showInFileBrowser(target)
