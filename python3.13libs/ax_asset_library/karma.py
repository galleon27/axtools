"""Thumbnail render scenes for Karma: USD stages rendered by husk.

No hou or Qt imports (pxr is imported when a stage is written), so the framing math and
the stage layout can be tested outside Houdini.

Geometry: the asset file is added as a sublayer, so its prims compose at their own paths
whatever format it is (bgeo, abc and USD all load through Houdini's USD plugins).
Materials: a textured MaterialX shader ball. HDRIs: the map as dome light and background
with a chrome and a grey sphere. Camera, lights and helpers live under /AXThumb, render
settings under /Render.
"""
import math
import os

FOCAL = 50.0          # mm
APERTURE = 36.0       # mm, square film for square thumbnails
SETTINGS_PATH = "/Render/rendersettings"
RENDERERS = (("BRAY_HdKarma", "Karma CPU"), ("BRAY_HdKarmaXPU", "Karma XPU"))


def _vsub(a, b):
    return tuple(a[i] - b[i] for i in range(3))


def _vlen(a):
    return math.sqrt(sum(c * c for c in a))


def frame_camera(bounds, azimuth=35.0, elevation=22.0, margin=0.85):
    """Camera that fits a bounding box: returns (eye, center, near, far).

    bounds = [[xmin, ymin, zmin], [xmax, ymax, zmax]], Y up. The bounding sphere is fitted
    into the field of view; margin < 1 crops into the sphere, because the asset itself is
    smaller than its bounding sphere.
    """
    lo, hi = bounds
    center = tuple((lo[i] + hi[i]) * 0.5 for i in range(3))
    radius = max(_vlen(_vsub(hi, lo)) * 0.5, 1e-4)
    half_fov = math.atan(APERTURE * 0.5 / FOCAL)
    dist = radius * margin / math.sin(half_fov)
    az, el = math.radians(azimuth), math.radians(elevation)
    direction = (math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el))
    eye = tuple(center[i] + direction[i] * dist for i in range(3))
    near = max(dist - radius * 2.0, dist * 1e-3)
    far = dist + radius * 50.0
    return eye, center, near, far


def _camera(stage, bounds, **frame):
    from pxr import Gf, UsdGeom
    eye, center, near, far = frame_camera(bounds, **frame)
    cam = UsdGeom.Camera.Define(stage, "/AXThumb/cam")
    cam.CreateFocalLengthAttr(FOCAL)
    cam.CreateHorizontalApertureAttr(APERTURE)
    cam.CreateVerticalApertureAttr(APERTURE)
    cam.CreateClippingRangeAttr(Gf.Vec2f(near, far))
    view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*center), Gf.Vec3d(0, 1, 0))
    UsdGeom.Xformable(cam).AddTransformOp().Set(view.GetInverse())
    return cam


def _studio_lights(stage):
    from pxr import Gf, UsdGeom, UsdLux
    dome = UsdLux.DomeLight.Define(stage, "/AXThumb/dome")
    dome.CreateIntensityAttr(0.6)
    dome.CreateColorAttr(Gf.Vec3f(0.92, 0.95, 1.0))
    key = UsdLux.DistantLight.Define(stage, "/AXThumb/key")
    key.CreateIntensityAttr(2.2)
    key.CreateAngleAttr(3.0)
    UsdGeom.Xformable(key).AddRotateXYZOp().Set(Gf.Vec3f(-50.0, 35.0, 0.0))


def _ground(stage, bounds):
    from pxr import Gf, UsdGeom
    lo, hi = bounds
    size = max(_vlen(_vsub(hi, lo)), 1e-3) * 30.0
    y = lo[1]
    cx, cz = (lo[0] + hi[0]) * 0.5, (lo[2] + hi[2]) * 0.5
    mesh = UsdGeom.Mesh.Define(stage, "/AXThumb/ground")
    mesh.CreatePointsAttr([Gf.Vec3f(cx - size, y, cz - size), Gf.Vec3f(cx - size, y, cz + size),
                           Gf.Vec3f(cx + size, y, cz + size), Gf.Vec3f(cx + size, y, cz - size)])
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateDisplayColorAttr([Gf.Vec3f(0.18, 0.18, 0.18)])
    mesh.CreateExtentAttr([Gf.Vec3f(cx - size, y, cz - size), Gf.Vec3f(cx + size, y, cz + size)])


def _settings(stage, cam, resolution, samples):
    # No RenderProduct/RenderVar: husk rejects vars without its driver:parameters:aov:*
    # tokens, and with none defined it writes its default beauty image to --output.
    from pxr import Gf, Sdf, UsdRender
    settings = UsdRender.Settings.Define(stage, SETTINGS_PATH)
    settings.CreateResolutionAttr(Gf.Vec2i(int(resolution), int(resolution)))
    settings.CreateCameraRel().SetTargets([cam.GetPath()])
    settings.GetPrim().CreateAttribute("karma:global:pathtracedsamples",
                                       Sdf.ValueTypeNames.Int).Set(int(samples))
    stage.SetMetadata("renderSettingsPrimPath", SETTINGS_PATH)


def write_stage(stage_path, layer_path, bounds, image_path, resolution=512, samples=16,
                ground=True):
    """Geometry thumbnail stage. `layer_path` is the asset file, `image_path` the render output."""
    from pxr import Sdf, Usd, UsdGeom

    stage = Usd.Stage.CreateNew(stage_path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    cam = _camera(stage, bounds)
    _studio_lights(stage)
    if ground:
        _ground(stage, bounds)
    _settings(stage, cam, resolution, samples)
    stage.GetRootLayer().Save()
    del stage  # add the asset only once no stage composes this layer: nothing gets loaded here
    layer = Sdf.Layer.FindOrOpen(stage_path)
    layer.subLayerPaths.append(layer_path.replace("\\", "/"))
    layer.Save()
    return stage_path


# --- material shader ball -----------------------------------------------------------------
BALL_BOUNDS = [[-1.0, 0.0, -1.0], [1.0, 2.0, 1.0]]
UV_TILING = (2.0, 1.0)  # around x up: keeps texels about square on the sphere


def _uv_sphere(stage, path, center, radius, rings=48, segments=96):
    """Mesh sphere with an 'st' primvar (UsdGeom.Sphere has no UVs for textures)."""
    from pxr import Gf, Sdf, UsdGeom
    pts, uvs = [], []
    for r in range(rings + 1):
        v = float(r) / rings
        theta = math.pi * v
        for sgm in range(segments + 1):
            u = float(sgm) / segments
            phi = 2.0 * math.pi * u
            pts.append(Gf.Vec3f(center[0] + radius * math.sin(theta) * math.cos(phi),
                                center[1] - radius * math.cos(theta),
                                center[2] - radius * math.sin(theta) * math.sin(phi)))
            uvs.append(Gf.Vec2f(u, v))
    counts, idx = [], []
    row = segments + 1
    for r in range(rings):
        for sgm in range(segments):
            a = r * row + sgm
            counts.append(4)
            idx.extend([a, a + 1, a + row + 1, a + row])
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr(pts)
    mesh.CreateFaceVertexCountsAttr(counts)
    mesh.CreateFaceVertexIndicesAttr(idx)
    mesh.CreateSubdivisionSchemeAttr(UsdGeom.Tokens.none)
    mesh.CreateExtentAttr([Gf.Vec3f(*[c - radius for c in center]),
                           Gf.Vec3f(*[c + radius for c in center])])
    st = UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                                 UsdGeom.Tokens.vertex)
    st.Set(uvs)
    return mesh


def _mtlx_material(stage, path, maps=None, constants=None):
    """MaterialX standard_surface material. `maps` = {channel: file}, `constants` = {input: value}."""
    from pxr import Gf, Sdf, UsdShade
    maps = maps or {}
    mat = UsdShade.Material.Define(stage, path)
    surf = UsdShade.Shader.Define(stage, path + "/surface")
    surf.CreateIdAttr("ND_standard_surface_surfaceshader")
    for name, value in (constants or {}).items():
        if isinstance(value, tuple):
            surf.CreateInput(name, Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*value))
        else:
            surf.CreateInput(name, Sdf.ValueTypeNames.Float).Set(float(value))

    def image(channel, kind, colorspace):
        tex = UsdShade.Shader.Define(stage, "%s/%s_tex" % (path, channel))
        tex.CreateIdAttr("ND_tiledimage_" + kind)
        f = tex.CreateInput("file", Sdf.ValueTypeNames.Asset)
        f.Set(Sdf.AssetPath(maps[channel].replace("\\", "/")))
        f.GetAttr().SetColorSpace(colorspace)
        tex.CreateInput("uvtiling", Sdf.ValueTypeNames.Float2).Set(Gf.Vec2f(*UV_TILING))
        out_type = {"color3": Sdf.ValueTypeNames.Color3f, "float": Sdf.ValueTypeNames.Float,
                    "vector3": Sdf.ValueTypeNames.Float3}[kind]
        tex.CreateOutput("out", out_type)
        return tex

    def connect(name, value_type, shader):
        surf.CreateInput(name, value_type).ConnectToSource(shader.ConnectableAPI(), "out")

    if "basecolor" in maps:
        connect("base_color", Sdf.ValueTypeNames.Color3f, image("basecolor", "color3", "srgb_texture"))
    if "roughness" in maps:
        connect("specular_roughness", Sdf.ValueTypeNames.Float, image("roughness", "float", "raw"))
    if "metallic" in maps:
        connect("metalness", Sdf.ValueTypeNames.Float, image("metallic", "float", "raw"))
    if "opacity" in maps:
        connect("opacity", Sdf.ValueTypeNames.Color3f, image("opacity", "color3", "raw"))
    if "emission" in maps:
        connect("emission_color", Sdf.ValueTypeNames.Color3f, image("emission", "color3", "srgb_texture"))
        surf.CreateInput("emission", Sdf.ValueTypeNames.Float).Set(1.0)
    if "normal" in maps:
        nmap = UsdShade.Shader.Define(stage, path + "/normal_map")
        nmap.CreateIdAttr("ND_normalmap_float")
        nmap.CreateInput("in", Sdf.ValueTypeNames.Float3).ConnectToSource(
            image("normal", "vector3", "raw").ConnectableAPI(), "out")
        nmap.CreateOutput("out", Sdf.ValueTypeNames.Float3)
        connect("normal", Sdf.ValueTypeNames.Float3, nmap)
    surf.CreateOutput("out", Sdf.ValueTypeNames.Token)
    mat.CreateSurfaceOutput("mtlx").ConnectToSource(surf.ConnectableAPI(), "out")
    return mat


def _bind(prim, mat):
    from pxr import UsdShade
    UsdShade.MaterialBindingAPI.Apply(prim.GetPrim()).Bind(mat)


def write_material_stage(stage_path, maps, image_path, resolution=512, samples=16):
    """Shader ball thumbnail: textured sphere on a ground plane. `maps` = {channel: file}."""
    from pxr import Usd, UsdGeom
    stage = Usd.Stage.CreateNew(stage_path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    cam = _camera(stage, BALL_BOUNDS, elevation=15.0)
    _studio_lights(stage)
    _ground(stage, BALL_BOUNDS)
    ball = _uv_sphere(stage, "/AXThumb/ball", (0.0, 1.0, 0.0), 1.0)
    _bind(ball, _mtlx_material(stage, "/AXThumb/materials/asset", maps))
    _settings(stage, cam, resolution, samples)
    stage.GetRootLayer().Save()
    return stage_path


# --- HDRI preview -----------------------------------------------------------------------------
HDRI_BOUNDS = [[-2.2, -1.0, -1.0], [2.2, 1.0, 1.0]]


def write_hdri_stage(stage_path, hdri_path, image_path, resolution=512, samples=16):
    """HDRI thumbnail: the map as the only light and the background, with a chrome and a
    grey sphere showing reflections and diffuse lighting."""
    from pxr import Sdf, Usd, UsdGeom, UsdLux
    stage = Usd.Stage.CreateNew(stage_path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    cam = _camera(stage, HDRI_BOUNDS, azimuth=0.0, elevation=6.0, margin=1.0)
    dome = UsdLux.DomeLight.Define(stage, "/AXThumb/dome")
    dome.CreateIntensityAttr(1.0)
    dome.CreateTextureFileAttr(Sdf.AssetPath(hdri_path.replace("\\", "/")))
    dome.CreateTextureFormatAttr(UsdLux.Tokens.latlong)
    # Karma: show the map behind the spheres (off by default for dome lights).
    dome.GetPrim().CreateAttribute("karma:light:renderlightgeo", Sdf.ValueTypeNames.Bool).Set(True)
    for name, x, consts in (("chrome", -1.1, {"base_color": (0.95, 0.95, 0.95), "metalness": 1.0,
                                               "specular_roughness": 0.02}),
                            ("grey", 1.1, {"base_color": (0.18, 0.18, 0.18), "specular": 0.0})):
        sphere = UsdGeom.Sphere.Define(stage, "/AXThumb/" + name)
        sphere.CreateRadiusAttr(1.0)
        UsdGeom.Xformable(sphere).AddTranslateOp().Set((x, 0.0, 0.0))
        _bind(sphere, _mtlx_material(stage, "/AXThumb/materials/" + name, constants=consts))
    _settings(stage, cam, resolution, samples)
    stage.GetRootLayer().Save()
    return stage_path


def husk_args(stage_path, image_path, renderer="BRAY_HdKarma"):
    """Arguments for husk (without the executable)."""
    return ["--renderer", renderer, "--settings", SETTINGS_PATH,
            "--output", image_path.replace("\\", "/"), stage_path.replace("\\", "/")]


# Formats husk can read as a USD layer through Houdini's plugins; others are converted
# to bgeo.sc before rendering.
LAYER_FORMATS = {"bgeo.sc", "bgeo", "bgeo.gz", "usd", "usda", "usdc", "usdz", "abc"}


def needs_conversion(path):
    from . import schema
    return schema.format_key(path) not in LAYER_FORMATS


def is_renderable(bounds):
    try:
        lo, hi = bounds
        return all(math.isfinite(v) for v in list(lo) + list(hi)) and \
            _vlen(_vsub(hi, lo)) > 0
    except (TypeError, ValueError):
        return False


def work_paths(workdir):
    return os.path.join(workdir, "thumb.usda"), os.path.join(workdir, "thumb.png")
