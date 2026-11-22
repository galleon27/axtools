"""Thumbnail render scene for Karma: a USD stage rendered by husk.

No hou or Qt imports (pxr is imported when a stage is written), so the framing math and
the stage layout can be tested outside Houdini.

The asset file is added as a sublayer, so its prims compose at their own paths whatever
format it is (bgeo, abc and USD all load through Houdini's USD plugins). Camera, lights,
ground and render settings live under /AXThumb and /Render.
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


def frame_camera(bounds, azimuth=35.0, elevation=22.0, margin=1.12):
    """Camera that fits a bounding box: returns (eye, center, near, far).

    bounds = [[xmin, ymin, zmin], [xmax, ymax, zmax]], Y up. The bounding sphere is fitted
    into the field of view, so the asset fills most of the frame at any rotation.
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


def write_stage(stage_path, layer_path, bounds, image_path, resolution=512, samples=16,
                ground=True):
    """Write the render stage. `layer_path` is the asset file, `image_path` the render output."""
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdRender

    stage = Usd.Stage.CreateNew(stage_path)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)

    eye, center, near, far = frame_camera(bounds)
    cam = UsdGeom.Camera.Define(stage, "/AXThumb/cam")
    cam.CreateFocalLengthAttr(FOCAL)
    cam.CreateHorizontalApertureAttr(APERTURE)
    cam.CreateVerticalApertureAttr(APERTURE)
    cam.CreateClippingRangeAttr(Gf.Vec2f(near, far))
    view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*center), Gf.Vec3d(0, 1, 0))
    UsdGeom.Xformable(cam).AddTransformOp().Set(view.GetInverse())

    dome = UsdLux.DomeLight.Define(stage, "/AXThumb/dome")
    dome.CreateIntensityAttr(0.6)
    dome.CreateColorAttr(Gf.Vec3f(0.92, 0.95, 1.0))
    key = UsdLux.DistantLight.Define(stage, "/AXThumb/key")
    key.CreateIntensityAttr(2.2)
    key.CreateAngleAttr(3.0)
    UsdGeom.Xformable(key).AddRotateXYZOp().Set(Gf.Vec3f(-50.0, 35.0, 0.0))

    if ground:
        lo, hi = bounds
        size = max(_vlen(_vsub(hi, lo)), 1e-3) * 30.0
        y = lo[1]
        mesh = UsdGeom.Mesh.Define(stage, "/AXThumb/ground")
        cx, cz = center[0], center[2]
        mesh.CreatePointsAttr([Gf.Vec3f(cx - size, y, cz - size), Gf.Vec3f(cx - size, y, cz + size),
                               Gf.Vec3f(cx + size, y, cz + size), Gf.Vec3f(cx + size, y, cz - size)])
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateDisplayColorAttr([Gf.Vec3f(0.18, 0.18, 0.18)])
        mesh.CreateExtentAttr([Gf.Vec3f(cx - size, y, cz - size), Gf.Vec3f(cx + size, y, cz + size)])

    var = UsdRender.Var.Define(stage, "/Render/Products/Vars/C")
    var.CreateSourceNameAttr("C")
    var.CreateDataTypeAttr("color4f")
    product = UsdRender.Product.Define(stage, "/Render/Products/thumb")
    product.CreateProductNameAttr(image_path.replace("\\", "/"))
    product.CreateOrderedVarsRel().SetTargets([var.GetPath()])
    settings = UsdRender.Settings.Define(stage, SETTINGS_PATH)
    settings.CreateResolutionAttr(Gf.Vec2i(int(resolution), int(resolution)))
    settings.CreateCameraRel().SetTargets([cam.GetPath()])
    settings.CreateProductsRel().SetTargets([product.GetPath()])
    settings.GetPrim().CreateAttribute("karma:global:pathtracedsamples",
                                       Sdf.ValueTypeNames.Int).Set(int(samples))
    stage.SetMetadata("renderSettingsPrimPath", SETTINGS_PATH)
    stage.GetRootLayer().Save()
    del stage  # add the asset only once no stage composes this layer: nothing gets loaded here
    layer = Sdf.Layer.FindOrOpen(stage_path)
    layer.subLayerPaths.append(layer_path.replace("\\", "/"))
    layer.Save()
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
