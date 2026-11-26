"""Save a SOP's assigned materials with its geometry, textures included.

Published geometry keeps the look it had: the material nodes are saved as a .cpio bundle in
the version folder and their file parms point at copied textures through a placeholder that
the importer replaces with the asset's version folder. Renderer-agnostic: whatever material
nodes the geometry uses are saved as they are.

The geometry's shop_materialpath values are rewritten to the saved node names, so a Material
SOP built on import hands every primitive its material again.
"""
import glob
import os
import shutil

import hou

from . import schema

PLACEHOLDER = "__AX_VERSION_DIR__"
BUNDLE_FILE = "materials.cpio"
TEXTURE_DIR = "textures"
UDIM_TOKENS = ("<UDIM>", "%(UDIM)d", "<udim>")
MATERIAL_ATTR = "shop_materialpath"


def assigned(sop, geo):
    """({shop_materialpath value: material node}, warnings) for a cooked geometry."""
    values = []
    if geo.findPrimAttrib(MATERIAL_ATTR) is not None:
        values.extend(geo.primStringAttribValues(MATERIAL_ATTR))
    materials, warnings, seen = {}, [], set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        try:
            node = sop.node(value)
        except hou.Error:
            node = None
        if node is None:
            warnings.append("material not found: %s" % value)
        else:
            materials[value] = node
    return materials, warnings


def _expand_udim(path):
    for token in UDIM_TOKENS:
        path = path.replace(token, "*")
    return path


def _walk(node):
    """`node` and what it holds, without the insides of locked HDAs (their parms can't be set
    and mostly just reference the top-level ones)."""
    return [node] + list(node.allSubChildren(recurse_in_locked_nodes=False))


def _is_expression(parm):
    """True for a parm with keyframes or an expression, or one that references another parm."""
    try:
        if parm.keyframes():
            return True
        return parm.getReferencedParm() != parm
    except hou.Error:
        return True


def texture_parms(nodes):
    """([(parm, path)], [skipped parm paths]) for the file-reference string parms of `nodes`.

    Only parms that point at files on disk are returned. UDIM tokens become a glob, so every
    tile of a texture is found. Parms driven by an expression or a reference are skipped (the
    second list): the file they use is not copied."""
    out, skipped = [], []
    for node in nodes:
        for n in _walk(node):
            for parm in n.parms():
                try:
                    template = parm.parmTemplate()
                except hou.Error:
                    continue
                if template.type() != hou.parmTemplateType.String or                         template.stringType() != hou.stringParmType.FileReference:
                    continue
                if _is_expression(parm):
                    skipped.append(parm.path())
                    continue
                value = parm.eval()
                if not isinstance(value, str) or not value.strip():
                    continue
                pattern = _expand_udim(value)
                if any(os.path.isfile(m) for m in glob.glob(pattern)):
                    out.append((parm, pattern))
    return out, skipped


def _new_node(parent, base):
    name, n = base, 2
    while parent.node(name) is not None:
        name = "%s%d" % (base, n)
        n += 1
    return parent.createNode("geo", name)


def _unique_name(used, base):
    name, n = base, 2
    while name.lower() in used:
        name = "%s_%d" % (base, n)
        n += 1
    used.add(name.lower())
    return name


def _tile_name(name, suffix):
    if not suffix:
        return name
    return schema.strip_ext(name) + suffix + schema.file_ext(name)


def _promote_detail(geo):
    """Turn a detail shop_materialpath into a prim attribute (prims without their own value
    get it), so one attribute carries every assignment and rewrite_attr() can rename it."""
    if geo.findGlobalAttrib(MATERIAL_ATTR) is None:
        return
    try:
        value = geo.attribValue(MATERIAL_ATTR)
    except hou.Error:
        return
    if geo.findPrimAttrib(MATERIAL_ATTR) is None:
        geo.addAttrib(hou.attribType.Prim, MATERIAL_ATTR, "")
    if value:
        current = geo.primStringAttribValues(MATERIAL_ATTR)
        geo.setPrimStringAttribValues(MATERIAL_ATTR, [v or value for v in current])
    geo.findGlobalAttrib(MATERIAL_ATTR).destroy()


def write(vdir, sop, geo):
    """Save the materials assigned to `geo` in `vdir`; returns (info, warnings, rename).

    info is stored in asset.json's version entry (None when nothing is assigned); rename maps
    the old shop_materialpath values to the saved node names. A detail shop_materialpath on
    `geo` becomes a prim attribute."""
    _promote_detail(geo)
    materials, warnings = assigned(sop, geo)
    if not materials:
        return None, warnings, {}

    from . import publish        # publish imports this module
    obj = hou.node("/obj")
    holder = _new_node(obj, "__ax_publish_materials")
    tmp = holder.createNode("matnet", "__ax_publish_tmp")
    try:
        originals = list(materials.values())
        copies = hou.copyNodesTo(tuple(originals), tmp)
        used_names = set()
        saved = [_unique_name(used_names, copy.name()) for copy in copies]
        for copy, name in zip(copies, saved):
            copy.setName(name)
        rename = {value: name for value, name in zip(materials.keys(), saved)}

        textures, used = [], set()
        copied = {}              # source pattern -> value written into the parm
        tex_dir = os.path.join(vdir, TEXTURE_DIR)
        parms, skipped = texture_parms(copies)
        for path in skipped:
            warnings.append("texture not copied (expression or reference): %s" % path)
        for parm, pattern in parms:
            key = os.path.normcase(os.path.abspath(pattern))
            if key in copied:                      # the same file used twice: one copy
                new_value = copied[key]
            else:
                value = parm.eval()
                os.makedirs(tex_dir, exist_ok=True)
                if any(token in value for token in UDIM_TOKENS):
                    matches = sorted(m for m in glob.glob(pattern) if os.path.isfile(m))
                    suffix, n = "", 2
                    while any(_tile_name(os.path.basename(m), suffix).lower() in used
                              for m in matches):
                        suffix = "_%d" % n
                        n += 1
                    for src in matches:
                        name = _tile_name(os.path.basename(src), suffix)
                        used.add(name.lower())
                        shutil.copy2(src, os.path.join(tex_dir, name))
                        textures.append(TEXTURE_DIR + "/" + name)
                    name = _tile_name(os.path.basename(value), suffix)
                else:
                    dst = publish._copy_into(tex_dir, pattern, used)
                    name = os.path.basename(dst)
                    textures.append(TEXTURE_DIR + "/" + name)
                new_value = PLACEHOLDER + "/" + TEXTURE_DIR + "/" + name
                copied[key] = new_value
            try:
                parm.set(new_value)
            except hou.Error as e:
                warnings.append("texture not relinked (%s): %s" % (parm.path(),
                                                                   e.instanceMessage()))

        tmp.saveItemsToFile(tmp.children(), os.path.join(vdir, BUNDLE_FILE))
    finally:
        holder.destroy()
    info = {"file": BUNDLE_FILE, "names": saved, "textures": textures}
    return info, warnings, rename


def rewrite_attr(geo, rename):
    """Point the geometry's shop_materialpath values at the saved node names."""
    if not rename or geo.findPrimAttrib(MATERIAL_ATTR) is None:
        return
    values = geo.primStringAttribValues(MATERIAL_ATTR)
    geo.setPrimStringAttribValues(MATERIAL_ATTR, [rename.get(v, v) for v in values])


def load(bundle_info, version_dir_parm, container, asset_name):
    """Load a bundle into `<asset>_materials` inside the container's material network.

    `version_dir_parm` is the version folder as written into parms (prefs.parm_path, so a
    $VAR root stays tokenized). Returns (subnet, {saved name: node}). A failed load leaves no
    half-built subnet behind."""
    from . import importers
    matnet = importers.local_matnet(container)
    if matnet is None:
        raise hou.OperationFailed("/mat not found.")
    name = importers.unique_name(matnet,
                                 schema.sanitize_name(asset_name) + "_materials")
    subnet = matnet.createNode("subnet", name)
    try:
        path = bundle_info.get("file") or BUNDLE_FILE
        if not os.path.isabs(path):
            path = os.path.join(hou.text.expandString(version_dir_parm), path)
        subnet.loadItemsFromFile(path)
        for n in _walk(subnet):
            for parm in n.parms():
                try:
                    template = parm.parmTemplate()
                    if template.type() != hou.parmTemplateType.String or parm.keyframes():
                        continue
                    value = parm.unexpandedString()
                    if PLACEHOLDER in value:
                        parm.set(value.replace(PLACEHOLDER, version_dir_parm))
                except hou.Error:      # expressions, locked parms: leave them alone
                    continue
    except BaseException:
        subnet.destroy()
        raise
    nodes = {}
    for saved in bundle_info.get("names") or []:
        node = subnet.node(saved)
        if node is not None:
            nodes[saved] = node
    return subnet, nodes
