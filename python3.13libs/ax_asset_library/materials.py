"""Plain material networks for Karma (MaterialX), Octane and Redshift, built from a
material asset's texture files.

SOP/OBJ context: the material goes into /mat. LOP context: into a new Material Library LOP.
Nothing is assigned; the material is only created.
"""
import hou

from . import textures

RENDERERS = (("karma", "Karma"), ("octane", "Octane"), ("redshift", "Redshift"))
RENDERER_LABELS = dict(RENDERERS)
DEFAULT_DISPLACEMENT = 0.01


class MaterialError(Exception):
    pass


def available_renderers():
    """Renderers whose node types exist in this Houdini session."""
    vop = hou.vopNodeTypeCategory().nodeTypes()
    out = []
    for key, label in RENDERERS:
        if key == "karma" and "mtlxstandard_surface" in vop:
            out.append((key, label))
        elif key == "octane" and ("octane::NT_MAT_STANDARD_SURFACE" in vop or
                                  "NT_MAT_STANDARD_SURFACE" in vop):
            out.append((key, label))
        elif key == "redshift" and "redshift::OpenPBRMaterial" in vop:
            out.append((key, label))
    return out


def _child(parent, type_name):
    for c in parent.children():
        if c.type().name() == type_name or c.type().name().endswith("::" + type_name):
            return c
    return None


def _set(node, parm, value):
    p = node.parm(parm)
    if p is not None:
        try:
            p.set(value)
        except hou.Error:
            pass


# --- Karma ------------------------------------------------------------------------------
def _karma(parent, name, maps, disp_scale):
    import voptoolutils
    mat = voptoolutils._setupMtlXBuilderSubnet(
        destination_node=parent, name=name, mask=voptoolutils.KARMAMTLX_TAB_MASK,
        folder_label="Karma Material Builder")
    surf = _child(mat, "mtlxstandard_surface") or mat.createNode("mtlxstandard_surface")
    out = _child(mat, "suboutput")
    disp = _child(mat, "mtlxdisplacement")

    def image(channel, signature, colorspace):
        img = mat.createNode("mtlximage", channel)
        _set(img, "signature", signature)
        img.parm("file").set(maps[channel])
        _set(img, "filecolorspace", colorspace)
        return img

    if "basecolor" in maps:
        surf.setNamedInput("base_color", image("basecolor", "color3", "srgb_texture"), 0)
    if "roughness" in maps:
        surf.setNamedInput("specular_roughness", image("roughness", "default", "Raw"), 0)
    if "metallic" in maps:
        surf.setNamedInput("metalness", image("metallic", "default", "Raw"), 0)
    if "opacity" in maps:
        surf.setNamedInput("opacity", image("opacity", "color3", "Raw"), 0)
    if "emission" in maps:
        surf.setNamedInput("emission_color", image("emission", "color3", "srgb_texture"), 0)
        _set(surf, "emission", 1.0)
    if "normal" in maps:
        nmap = mat.createNode("mtlxnormalmap", "normal_map")
        nmap.setNamedInput("in", image("normal", "vector3", "Raw"), 0)
        surf.setNamedInput("normal", nmap, 0)
    if out is not None:
        out.setNamedInput("surface", surf, 0)
        if "displacement" in maps:
            disp = disp or mat.createNode("mtlxdisplacement")
            disp.setNamedInput("displacement", image("displacement", "default", "Raw"), 0)
            _set(disp, "scale", disp_scale)
            out.setNamedInput("displacement", disp, 0)
        elif disp is not None:
            disp.destroy()
    mat.layoutChildren()
    return mat


# --- Octane -----------------------------------------------------------------------------
def _octane(parent, name, maps, disp_scale):
    try:
        import octane_material_builder
    except ImportError:
        raise MaterialError("Octane is not loaded in this Houdini session.")
    mat = octane_material_builder.createMaskedOctaneSubnet(target_node=parent, name=name)
    out = mat.node("surface_output")
    surf = _child(mat, "NT_MAT_STANDARD_SURFACE") or mat.createNode("NT_MAT_STANDARD_SURFACE")
    if out is not None:
        out.setInput(0, surf)

    def image(channel, float_map):
        img = mat.createNode("NT_TEX_FLOATIMAGE" if float_map else "NT_TEX_IMAGE", channel)
        img.parm("A_FILENAME").set(maps[channel])
        _set(img, "colorSpace", "NAMED_COLOR_SPACE_SRGB" if channel in textures.COLOR_CHANNELS
             else "NAMED_COLOR_SPACE_OTHER")
        return img

    if "basecolor" in maps:
        surf.setNamedInput("baseColor", image("basecolor", False), 0)
    if "roughness" in maps:
        surf.setNamedInput("roughness", image("roughness", True), 0)
    if "metallic" in maps:
        surf.setNamedInput("metallic", image("metallic", True), 0)
    if "opacity" in maps:
        surf.setNamedInput("opacity", image("opacity", True), 0)
    if "normal" in maps:
        surf.setNamedInput("normal", image("normal", False), 0)
    if "emission" in maps:
        emis = mat.createNode("NT_EMIS_TEXTURE", "emission_tex")
        emis.setNamedInput("efficiency_or_texture", image("emission", False), 0)
        surf.setNamedInput("emission", emis, 0)
    if "displacement" in maps:
        disp = mat.createNode("NT_VERTEX_DISPLACEMENT", "displacement_node")
        disp.setNamedInput("texture", image("displacement", True), 0)
        _set(disp, "amount", disp_scale)
        _set(disp, "black_level", 0.5)
        surf.setNamedInput("displacement", disp, 0)
    mat.layoutChildren()
    return mat


# --- Redshift ---------------------------------------------------------------------------
def _redshift(parent, name, maps, disp_scale, usd):
    """usd=True: rs_usd_material_builder (Solaris); False: redshift_vopnet (/mat)."""
    mat = parent.createNode("rs_usd_material_builder" if usd else "redshift_vopnet", name)
    out = _child(mat, "redshift_usd_material" if usd else "redshift_material")
    surf = _child(mat, "OpenPBRMaterial") or mat.createNode("redshift::OpenPBRMaterial")
    if out is not None:
        out.setNamedInput("Surface", surf, 0)

    def image(channel):
        img = mat.createNode("redshift::TextureSampler", channel)
        img.parm("tex0").set(maps[channel])
        _set(img, "tex0_colorSpace", "sRGB" if channel in textures.COLOR_CHANNELS else "Raw")
        return img

    if "basecolor" in maps:
        surf.setNamedInput("base_color", image("basecolor"), 0)
    if "roughness" in maps:
        surf.setNamedInput("specular_roughness", image("roughness"), 0)
    if "metallic" in maps:
        surf.setNamedInput("base_metalness", image("metallic"), 0)
    if "opacity" in maps:
        surf.setNamedInput("geometry_opacity", image("opacity"), 0)
    if "emission" in maps:
        surf.setNamedInput("emission_color", image("emission"), 0)
        _set(surf, "emission_luminance", 1.0)
    if out is not None and "normal" in maps:
        bump = mat.createNode("redshift::BumpMap", "normal_map")
        _set(bump, "inputType", "1")  # tangent-space normal
        bump.setNamedInput("input", image("normal"), 0)
        out.setNamedInput("BumpMap" if usd else "Bump Map", bump, 0)
    if out is not None and "displacement" in maps:
        disp = mat.createNode("redshift::Displacement", "displacement_node")
        disp.setNamedInput("texMap", image("displacement"), 0)
        _set(disp, "scale", disp_scale)
        out.setNamedInput("Displacement", disp, 0)
    mat.layoutChildren()
    return mat


def build(parent, name, maps, renderer, lop=False, disp_scale=None):
    """Create the material network under `parent`. `maps` = {channel: parm path}."""
    maps = {k: v for k, v in maps.items() if k in textures.WIRED and v}
    if not maps:
        raise MaterialError("No usable texture channels (%s)." % ", ".join(textures.WIRED))
    scale = DEFAULT_DISPLACEMENT if disp_scale is None else float(disp_scale)
    if renderer == "karma":
        return _karma(parent, name, maps, scale)
    if renderer == "octane":
        return _octane(parent, name, maps, scale)
    if renderer == "redshift":
        return _redshift(parent, name, maps, scale, usd=lop)
    raise MaterialError("Unknown renderer %r." % renderer)
