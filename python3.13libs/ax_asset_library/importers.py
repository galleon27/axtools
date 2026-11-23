"""Create nodes for library assets in the current SOP, OBJ or LOP network.

Every created node carries user data (ax_asset_id, ax_asset_version, ax_asset_root) so the
panel can find library assets in the scene later.
"""
import os

import hou

from . import materials
from . import prefs as _prefs
from . import schema
from . import variants

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

CONTEXT_LABELS = {"sop": "SOP", "obj": "OBJ", "lop": "LOP", "vop": "material network",
                  None: "/mat"}
# Node types whose child network holds materials. /mat itself has type "mat".
MATERIAL_CONTAINERS = ("matnet", "mat", "materiallibrary")
# Where each asset type can be imported; ctx None = any other context (materials go to /mat).
ALLOWED_CONTEXTS = {
    "geometry": ("sop", "obj", "lop"),
    "hdri": ("obj", "lop"),
    "material": ("sop", "obj", "lop", "vop", None),
    "image": ("vop",),
}
TYPE_WHERE = {
    "geometry": "a SOP, OBJ or LOP network",
    "hdri": "an OBJ network (Environment Light) or a LOP network (Dome Light)",
    "material": "any network",
    "image": "a material network (texture node), or use Copy path",
}
IMAGE_NODES = {"karma": ("mtlximage", "file"),
               "octane": ("NT_TEX_IMAGE", "A_FILENAME"),
               "redshift": ("redshift::TextureSampler", "tex0")}


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


def material_container(network):
    """The material network `network` is in: itself when it is /mat, a Material Network or a
    Material Library LOP, else the nearest such parent while still inside VOP networks
    (e.g. from inside a Karma Material Builder). None when there is none."""
    node = network
    while node is not None:
        if node.path() == "/mat" or node.type().name() in MATERIAL_CONTAINERS:
            return node
        if node.childTypeCategory() != hou.vopNodeTypeCategory():
            return None
        node = node.parent()
    return None


def context_of(network):
    """"sop", "obj", "lop", "vop" (in a material network) or None (any other context)."""
    if material_container(network) is not None:
        return "vop"
    cat = network.childTypeCategory()
    if cat == hou.sopNodeTypeCategory():
        return "sop"
    if cat == hou.objNodeTypeCategory():
        return "obj"
    if cat == hou.lopNodeTypeCategory():
        return "lop"
    return None


def check_target(editor, network):
    """Context of `network` (see context_of). `editor` may be None (a drop names its network).

    A locked HDA is checked in import_records: a material from inside one still goes to
    /mat or a material network above, so the network alone doesn't block an import."""
    return context_of(network)


def is_locked(network):
    """True when nodes can't be created in `network`: it is a locked HDA (the network editor
    shows its inside) or sits inside one."""
    try:
        return network.isLockedHDA() or network.isInsideLockedHDA()
    except AttributeError:   # nodes without HDA methods
        return network.isInsideLockedHDA()


def creates_in_network(rec_type, network, ctx):
    """True when importing a `rec_type` asset with context `ctx` creates nodes in `network`
    itself (rather than in /mat or a material network above it)."""
    if rec_type in ("geometry", "hdri", "image"):
        return True
    if rec_type == "material":
        return ctx == "lop" or (ctx == "vop" and material_container(network) == network)
    return False


def renderer_of(network):
    """Renderer of the material network `network` is in, or None when it isn't clear."""
    container = material_container(network)
    node = network
    while node is not None:
        if node is container:
            return None
        name = node.type().name()
        if name in ("redshift_vopnet", "rs_usd_material_builder"):
            return "redshift"
        children = [child.type().name() for child in node.children()]
        if any(n.startswith("octane::") or n.startswith("NT_") for n in children):
            return "octane"
        if any(n.startswith("mtlx") or n.startswith("kma") for n in children):
            return "karma"
        if node.childTypeCategory() != hou.vopNodeTypeCategory():
            return None
        node = node.parent()
    return None


def needs_renderer(records, network):
    """True when images in `records` need a renderer the network doesn't imply."""
    return renderer_of(network) is None and any(r.type == "image" for r in records)


def current_target():
    """(editor, network, context) or raise AssetImportError with a readable reason."""
    editor = target_editor()
    if editor is None:
        raise AssetImportError("Open a network editor first.")
    network = editor.pwd()
    return editor, network, check_target(editor, network)


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


def file_for(rec, settings, version=None, files=None):
    """(absolute path, parm path) of the file to load; current version when version is None.

    For a material this is the folder holding its textures. `files` overrides the record's
    files (an LOD or a texture resolution picked in the import dialog).
    """
    files = rec.files_for(version) if files is None else files
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
def _geo_files(rec, version, opts):
    """The record's files, or the picked LOD's files when opts has one."""
    files = rec.files_for(version)
    lod = opts.get("lod")
    if lod is not None:
        lods = variants.lod_files(files)
        files = lods[variants.pick_lod(list(lods), lod)]
    return files


def _material_maps(mrec, settings, opts):
    """{channel: parm path} for a material, at the picked texture resolution.
    """
    channels = mrec.files_for(mrec.current)
    resolution = opts.get("resolution")
    if resolution is not None:
        sets = variants.resolution_sets(channels)
        channels = sets[variants.pick_resolution(list(sets), resolution)]
    return {ch: to_parm(mrec, settings, p) for ch, p in channels.items()}


def _material_in(parent, mrec, maps, renderer, disp):
    """Build a material node inside `parent` (a /mat, a matnet or a Material Library LOP)."""
    usd = parent.type().name() == "materiallibrary"
    return materials.build(parent, unique_name(parent, mrec.name), maps, renderer,
                           lop=usd, disp_scale=disp)


def _build_linked_material(mrec, ctx, network, settings, opts, lop_parent=None):
    """The material for a linked record, built once per import (cached in opts)."""
    cache = opts.setdefault("materials", {})
    if mrec.key in cache:
        return cache[mrec.key]
    renderer = opts.get("renderer")
    maps = _material_maps(mrec, settings, opts)
    disp = (mrec.info or {}).get("displacement_scale")
    try:
        if ctx == "lop":
            if lop_parent is None:
                raise AssetImportError("No material library for %s." % mrec.name)
            mat = materials.build(lop_parent, unique_name(lop_parent, mrec.name), maps,
                                  renderer, lop=True, disp_scale=disp)
        else:
            parent = hou.node("/mat")
            if parent is None:
                raise AssetImportError("/mat not found.")
            if is_locked(parent):
                raise AssetImportError("%s is inside a locked HDA." % parent.path())
            mat = _material_in(parent, mrec, maps, renderer, disp)
            mat.moveToGoodPosition()
    except materials.MaterialError as e:
        raise AssetImportError(str(e))
    _tag_node(mat, mrec, settings, mrec.current)
    cache[mrec.key] = mat
    return mat


def _material_sop(parent, mat_path, name):
    node = parent.createNode("material", unique_name(parent, name))
    node.parm("num_materials").set(1)
    node.parm("shop_materialpath1").set(mat_path)
    return node


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


def _import_scale(rec):
    try:
        scale = float((rec.info or {}).get("import_scale") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return scale if scale > 0 else 1.0


def _sop_scale(parent, loader, rec):
    """Transform SOP after the loader when the asset has an import scale (Megascans FBX
    are in centimeters). Returns the last node of the chain."""
    scale = _import_scale(rec)
    if scale == 1.0:
        return loader
    xf = parent.createNode("xform", unique_name(parent, rec.name + "_scale"))
    xf.parm("scale").set(scale)
    xf.setInput(0, loader)
    xf.setComment("import scale %g" % scale)
    xf.setGenericFlag(hou.nodeFlag.DisplayComment, True)
    return xf


def _import_geometry(rec, network, ctx, pos, anchor, settings, version, opts):
    if ctx not in ALLOWED_CONTEXTS["geometry"]:
        raise AssetImportError("geometry imports into %s." % TYPE_WHERE["geometry"])
    files = _geo_files(rec, version, opts)
    path, value = file_for(rec, settings, version, files)
    mrec = None
    if opts.get("renderer") and opts.get("records_all"):
        mrec = variants.linked_material(rec, opts["records_all"])

    if ctx == "sop":
        node = _sop_loader(network, rec, path, value)
        node.setPosition(pos)
        _tag_node(node, rec, settings, version)
        last = _sop_scale(network, node, rec)
        if last is not node:
            last.setPosition(pos + hou.Vector2(0, -1))
        if mrec is not None:
            mat = _build_linked_material(mrec, ctx, network, settings, opts)
            mnode = _material_sop(network, mat.path(), rec.name + "_material")
            mnode.setPosition(last.position() + hou.Vector2(0, -1))
            mnode.setInput(0, last)
            _tag_node(mnode, rec, settings, version)
            return mnode
        return last

    if ctx == "obj":
        geo = network.createNode("geo", unique_name(network, rec.name))
        for child in geo.children():  # some setups add a default File SOP
            child.destroy()
        loader = _sop_loader(geo, rec, path, value)
        last = _sop_scale(geo, loader, rec)
        last.setDisplayFlag(True)
        last.setRenderFlag(True)
        if mrec is not None:
            mat = _build_linked_material(mrec, ctx, network, settings, opts)
            mnode = _material_sop(geo, mat.path(), rec.name + "_material")
            mnode.setPosition(last.position() + hou.Vector2(0, -1))
            mnode.setInput(0, last)
            mnode.setDisplayFlag(True)
            mnode.setRenderFlag(True)
            _tag_node(mnode, rec, settings, version)
        geo.layoutChildren()
        geo.setPosition(pos)
        _tag_node(geo, rec, settings, version)
        _tag_node(loader, rec, settings, version)
        return geo

    # LOP: Reference LOP; Houdini's USD plugins read bgeo and abc files as layers too.
    ref = network.createNode(_lop_type(LOP_REFERENCE_TYPES), unique_name(network, rec.name))
    if _set_first(ref, ("filepath1", "filepath"), value) is None:
        raise AssetImportError("%s has no file path parameter." % ref.type().name())
    prim = "/assets/" + schema.sanitize_name(rec.name)
    _set_first(ref, ("primpath1", "primpath"), prim)
    _wire_lop(ref, anchor)
    ref.setPosition(pos)
    _tag_node(ref, rec, settings, version)
    last = ref
    scale = _import_scale(rec)
    if scale != 1.0:
        xf = network.createNode("xform", unique_name(network, rec.name + "_scale"))
        xf.parm("primpattern").set(prim)
        xf.parm("scale").set(scale)
        xf.setInput(0, ref)
        xf.setPosition(pos + hou.Vector2(0, -1))
        xf.setComment("import scale %g" % scale)
        xf.setGenericFlag(hou.nodeFlag.DisplayComment, True)
        if ref.isDisplayFlagSet():
            xf.setDisplayFlag(True)
        last = xf
    if mrec is not None:
        lib = network.createNode("materiallibrary",
                                 unique_name(network, rec.name + "_material"))
        lib.setInput(0, last)
        lib.setPosition(last.position() + hou.Vector2(0, -1))
        mat = _build_linked_material(mrec, ctx, network, settings, opts, lop_parent=lib)
        lib.parm("matnode1").set(mat.name())
        lib.parm("matpath1").set("/materials/" + mat.name())
        lib.parm("assign1").set(1)
        lib.parm("geopath1").set(prim)
        _tag_node(lib, rec, settings, version)
        return lib
    return last


# --- materials ----------------------------------------------------------------------------
def _import_material(rec, network, ctx, pos, anchor, settings, version, opts):
    renderer = opts.get("renderer")
    if not renderer:
        raise AssetImportError("No renderer chosen for the material.")
    maps = _material_maps(rec, settings, opts)
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
        if ctx == "vop":
            parent = material_container(network)
            in_place = parent == network      # else: we're inside a material, go up
        else:
            parent = hou.node("/mat")
            in_place = False
        if parent is None:
            raise AssetImportError("/mat not found.")
        if is_locked(parent):
            raise AssetImportError("%s is inside a locked HDA." % parent.path())
        mat = _material_in(parent, rec, maps, renderer, disp)
        if in_place:
            mat.setPosition(pos)
        else:
            mat.moveToGoodPosition()
        _tag_node(mat, rec, settings, version)
        return mat
    except materials.MaterialError as e:
        raise AssetImportError(str(e))


# --- HDRIs / images ---------------------------------------------------------------------
def _import_hdri(rec, network, ctx, pos, anchor, settings, version, opts):
    _path, value = file_for(rec, settings, version)
    if ctx not in ALLOWED_CONTEXTS["hdri"]:
        raise AssetImportError("HDRIs import into %s." % TYPE_WHERE["hdri"])
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
    if ctx != "vop":
        raise AssetImportError("images import into %s." % TYPE_WHERE["image"])
    renderer = renderer_of(network) or opts.get("renderer")
    if renderer is None:
        raise AssetImportError("Choose a renderer for the texture node.")
    if network is material_container(network) and renderer != "karma":
        raise AssetImportError("Open a %s material to add the texture node."
                               % materials.RENDERER_LABELS.get(renderer, renderer))
    node_type, parm = IMAGE_NODES[renderer]
    node = materials._tex_node(network, node_type, schema.sanitize_name(rec.name))
    _path, value = file_for(rec, settings, version)
    if _set_first(node, (parm,), value) is None:
        raise AssetImportError("%s has no %s parameter." % (node.type().name(), parm))
    if renderer == "karma":
        materials._set(node, "signature", "color3")
        materials._set(node, "filecolorspace", "srgb_texture")
    elif renderer == "octane":
        materials._set(node, "colorSpace", "NAMED_COLOR_SPACE_SRGB")
    else:
        materials._set(node, "tex0_colorSpace", "sRGB")
    node.setPosition(pos)
    _tag_node(node, rec, settings, version)
    return node


def _atlas_chain(parent, rec, maps, size, pos):
    """Trace the opacity map into cutouts, then give each cutout the atlas size."""
    trace = parent.createNode("trace", unique_name(parent, rec.name + "_trace"))
    if _set_first(trace, ("file",), maps.get("opacity", "")) is None:
        raise AssetImportError("%s has no file parameter." % trace.type().name())
    for parm, value in (("thresh", 0.5), ("addtexture", 1), ("convpoly", 1),
                        ("dosmooth", 1), ("hole", 1), ("delborder", 1)):
        trace.parm(parm).set(value)
    trace.setPosition(pos)
    # Trace's own UVs (addtexture) match the image for any aspect ratio; don't reproject.
    divide = parent.createNode("divide", unique_name(parent, rec.name + "_divide"))
    divide.parm("convex").set(1)
    divide.setInput(0, trace)
    divide.setPosition(pos + hou.Vector2(0, -1))
    match = parent.createNode("matchsize", unique_name(parent, rec.name + "_size"))
    for parm, value in (("justify_x", 0), ("justify_y", 0), ("justify_z", 0),
                        ("doscale", 1), ("uniformscale", 1), ("sizex", size), ("sizey", size)):
        match.parm(parm).set(value)
    match.setInput(0, divide)
    match.setPosition(pos + hou.Vector2(0, -2))
    return trace, match


def _import_atlas(rec, network, ctx, pos, anchor, settings, version, opts):
    """Atlas material as geometry: opacity map -> cutouts -> packed primitives with UVs."""
    maps = _material_maps(rec, settings, opts)
    info = rec.info or {}
    size = info.get("megascans_size_m") or info.get("physical_size") or 1.0
    renderer = opts.get("renderer")

    if ctx == "lop":
        sc = network.createNode("sopcreate", unique_name(network, rec.name))
        sc.setPosition(pos)
        sc.allowEditingOfContents()
        trace, last = _atlas_chain(sc.node("sopnet"), rec, maps, size, hou.Vector2(0, 0))
        last.setDisplayFlag(True)
        _tag_node(trace, rec, settings, version)
        if renderer:
            prefix = sc.parm("pathprefix")
            prim = prefix.eval() if prefix is not None else ""
            lib = network.createNode("materiallibrary",
                                     unique_name(network, rec.name + "_material"))
            lib.setInput(0, sc)
            lib.setPosition(pos + hou.Vector2(0, -1))
            mat = _build_linked_material(rec, "lop", network, settings, opts, lop_parent=lib)
            lib.parm("matnode1").set(mat.name())
            lib.parm("matpath1").set("/materials/" + mat.name())
            lib.parm("assign1").set(1)
            lib.parm("geopath1").set(prim)
            _tag_node(lib, rec, settings, version)
            return lib
        return sc

    geo = None
    if ctx == "obj":
        geo = network.createNode("geo", unique_name(network, rec.name))
        for child in geo.children():
            child.destroy()
        parent, base = geo, hou.Vector2(0, 0)
    else:
        parent, base = network, pos
    trace, last = _atlas_chain(parent, rec, maps, size, base)
    _tag_node(trace, rec, settings, version)
    if renderer:
        mat = _build_linked_material(rec, ctx, network, settings, opts)
        mnode = _material_sop(parent, mat.path(), rec.name + "_material")
        mnode.setInput(0, last)
        mnode.setPosition(last.position() + hou.Vector2(0, -1))
        _tag_node(mnode, rec, settings, version)
        last = mnode
    assemble = parent.createNode("assemble", unique_name(parent, rec.name + "_assemble"))
    for parm, value in (("connect", 1), ("pack_geo", 1), ("pivot", 1)):
        assemble.parm(parm).set(value)
    assemble.parm("transfer_attributes").set("*")
    assemble.setInput(0, last)
    assemble.setPosition(last.position() + hou.Vector2(0, -1))
    if geo is not None:
        assemble.setDisplayFlag(True)
        assemble.setRenderFlag(True)
        geo.layoutChildren()
        geo.setPosition(pos)
        _tag_node(geo, rec, settings, version)
        return geo
    _tag_node(assemble, rec, settings, version)
    return assemble


IMPORTERS = {"geometry": _import_geometry, "material": _import_material,
             "hdri": _import_hdri, "image": _import_image}


def import_records(records, settings, version=None, renderer=None, target=None,
                   position=None, lod=None, resolution=None, records_all=None,
                   as_geometry=None):
    """Import records into the current network. Returns (nodes, warnings).

    `version` picks a version other than the current one (single record only).
    `renderer` ("karma", "octane", "redshift", "none") is needed when materials are imported.
    `target` = (editor, network) imports there instead of the current network; the editor
    may be None. `position` (hou.Vector2, network units) places the first node.
    `lod` is a wanted LOD rank, `resolution` a texture label such as "4K".
    `records_all` is the full library list, used to find a model's material.
    `as_geometry` imports atlas materials as traced geometry (default: in sop/obj/lop).

    Raises AssetImportError when nothing can be imported (no editor, wrong context,
    unsupported type).
    """
    if target is None:
        editor, network, ctx = current_target()
    else:
        editor, network = target
        ctx = check_target(editor, network)
    if as_geometry is None:
        as_geometry = ctx in ("sop", "obj", "lop")
    if ctx == "vop":
        as_geometry = False
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
    if not any(ctx in ALLOWED_CONTEXTS.get(r.type, ()) for r in records):
        where = "; ".join("%s: %s" % (schema.TYPE_LABELS.get(t, t), TYPE_WHERE[t])
                          for t in sorted({r.type for r in records}) if t in TYPE_WHERE)
        raise AssetImportError("These assets can't be imported into %s. %s."
                               % (network.path(), where))
    if is_locked(network) and any(
            ctx in ALLOWED_CONTEXTS.get(r.type, ()) and creates_in_network(r.type, network, ctx)
            for r in records):
        raise AssetImportError("%s is inside a locked HDA." % network.path())
    opts = {"renderer": None if renderer == "none" else renderer,
            "lod": lod, "resolution": resolution, "records_all": records_all,
            "materials": {}, "as_geometry": as_geometry}

    # A drop has its own position: don't wire to whatever node happens to be selected.
    anchor = _anchor(network) if position is None else None
    pos = position if position is not None else _start_position(editor, network, anchor)
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
                if rec.type == "material" and opts["as_geometry"] and variants.is_atlas(rec):
                    node = _import_atlas(rec, network, ctx, pos, anchor, settings, use, opts)
                else:
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
