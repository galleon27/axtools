"""Plain-Python tests for the hou-free parts of ax_asset_library.

Run from the repo root:  python -m unittest tests.test_ax_asset_library
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "python3.13libs"))

from ax_asset_library import karma, library, manage, prefs, schema, textures  # noqa: E402

try:
    import pxr  # noqa: F401
    HAVE_PXR = True
except ImportError:
    HAVE_PXR = False


def make_asset(root, asset_type, category, name, files=("model.bgeo.sc",), tags=(), thumb=False,
               created=None):
    folder = schema.TYPE_FOLDERS[asset_type]
    asset_dir = os.path.join(root, folder, *category.split("/"), name) if category else \
        os.path.join(root, folder, name)
    os.makedirs(os.path.join(asset_dir, "v001"), exist_ok=True)
    data = schema.new_asset(name, asset_type, category=category, tags=list(tags))
    if created:
        data["created"] = created
    rel = {}
    for fn in files:
        p = os.path.join(asset_dir, "v001", fn)
        with open(p, "w") as f:
            f.write("x")
        rel[schema.format_key(fn)] = "v001/" + fn
    schema.add_version(data, rel)
    if thumb:
        with open(os.path.join(asset_dir, schema.THUMB), "wb") as f:
            f.write(b"jpg")
    schema.save(os.path.join(asset_dir, schema.SIDECAR), data)
    return asset_dir, data


class SchemaTests(unittest.TestCase):
    def test_ext_handling(self):
        self.assertEqual(schema.file_ext("a/Rock.BGEO.sc"), ".bgeo.sc")
        self.assertEqual(schema.strip_ext("Rock.bgeo.sc"), "Rock")
        self.assertEqual(schema.format_key("x.abc"), "abc")
        self.assertTrue(schema.is_geometry("x.usdc"))
        self.assertFalse(schema.is_geometry("x.jpg"))

    def test_sanitize(self):
        self.assertEqual(schema.sanitize_name("  Big Rock (02) "), "Big_Rock_02")
        self.assertEqual(schema.sanitize_name("!!!"), "asset")

    def test_round_trip_and_versions(self):
        with tempfile.TemporaryDirectory() as d:
            data = schema.new_asset("Rock", "geometry", category="/rocks/", tags="Rock, Stone ,rock")
            self.assertEqual(data["category"], "rocks")
            self.assertEqual(data["tags"], ["rock", "stone"])
            self.assertEqual(schema.add_version(data, {"bgeo.sc": "v001/a.bgeo.sc"}), 1)
            self.assertEqual(schema.add_version(data, {"bgeo.sc": "v002/a.bgeo.sc"}), 2)
            path = os.path.join(d, schema.SIDECAR)
            schema.save(path, data)
            back = schema.load(path)
            self.assertEqual(back["current"], 2)
            self.assertEqual(schema.current_version(back)["files"]["bgeo.sc"], "v002/a.bgeo.sc")
            self.assertEqual(schema.validate(back), [])
            self.assertFalse([f for f in os.listdir(d) if ".tmp" in f])

    def test_migrate_minimal(self):
        data = schema.migrate({"name": "x", "type": "hdri",
                               "versions": [{"version": 3, "files": {}}, "junk"]})
        self.assertEqual(data["current"], 3)
        self.assertEqual(len(data["versions"]), 1)
        self.assertIn("kind", data["source"])
        with self.assertRaises(schema.SidecarError):
            schema.migrate([])

    def test_load_errors(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "asset.json")
            with self.assertRaises(schema.SidecarError):
                schema.load(p)
            with open(p, "w") as f:
                f.write("{not json")
            with self.assertRaises(schema.SidecarError):
                schema.load(p)

    def test_resolve_relpath(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(schema.relpath(d, os.path.join(d, "v001", "a.abc")), "v001/a.abc")
            outside = os.path.abspath(os.path.join(d, "..", "elsewhere.abc"))
            self.assertTrue(os.path.isabs(schema.relpath(d, outside)))
            self.assertEqual(schema.resolve(d, "v001/a.abc"), os.path.join(d, "v001", "a.abc"))

    def test_pick_file(self):
        files = {"abc": "a", "bgeo.sc": "b"}
        self.assertEqual(schema.pick_file(files, ["bgeo.sc", "abc"]), "bgeo.sc")
        self.assertEqual(schema.pick_file(files, ["usd"]), "abc")
        self.assertIsNone(schema.pick_file({}, ["usd"]))


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "lib")
        self.cache = os.path.join(self.tmp.name, "cache")
        make_asset(self.root, "geometry", "rocks", "Boulder", tags=["rock"], thumb=True,
                   created="2026-01-01T00:00:00Z")
        make_asset(self.root, "geometry", "trees/oak", "Oak_A", files=("oak.abc", "oak.usd"),
                   created="2026-03-01T00:00:00Z")
        make_asset(self.root, "hdri", "", "Sunset", files=("sunset.exr",))
        # Ignored: hidden folder, unknown top-level folder.
        make_asset(os.path.join(self.root, ".trash"), "geometry", "", "Gone")
        os.makedirs(os.path.join(self.root, "misc", "x"))
        with open(os.path.join(self.root, "misc", "x", "asset.json"), "w") as f:
            f.write("{}")

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan(self):
        recs, errors = library.scan_roots([self.root], self.cache)
        self.assertEqual(errors, [])
        by_name = {r.name: r for r in recs}
        self.assertEqual(sorted(by_name), ["Boulder", "Oak_A", "Sunset"])
        self.assertEqual(by_name["Oak_A"].category, "trees/oak")
        self.assertEqual(by_name["Sunset"].type, "hdri")
        self.assertEqual(by_name["Sunset"].category, "")
        self.assertIsNotNone(by_name["Boulder"].thumb_mtime)
        self.assertIsNone(by_name["Oak_A"].thumb_mtime)
        self.assertEqual(by_name["Oak_A"].primary_key(prefs.GEO_FORMAT_ORDER), "usd")
        self.assertTrue(os.path.isabs(by_name["Oak_A"].primary_file(["abc"])))
        self.assertTrue(os.path.exists(library.index_path(self.cache, self.root)))

    def test_incremental_and_missing(self):
        library.scan_roots([self.root], self.cache)
        boulder = os.path.join(self.root, "geometry", "rocks", "Boulder")
        # Cached record is reused, but missing files are re-checked.
        os.remove(os.path.join(boulder, "v001", "model.bgeo.sc"))
        recs, _ = library.scan_roots([self.root], self.cache)
        rec = next(r for r in recs if r.name == "Boulder")
        self.assertEqual(rec.missing, ["bgeo.sc"])
        # A changed sidecar is parsed again.
        sidecar = os.path.join(boulder, schema.SIDECAR)
        data = schema.load(sidecar)
        data["tags"] = ["granite"]
        schema.save(sidecar, data)
        st = os.stat(sidecar)
        os.utime(sidecar, (st.st_atime, st.st_mtime + 5))
        recs, _ = library.scan_roots([self.root], self.cache)
        self.assertEqual(next(r for r in recs if r.name == "Boulder").tags, ["granite"])

    def test_cache_only_records(self):
        library.scan_roots([self.root], self.cache)
        recs = library.records_from_index(self.cache, self.root)
        self.assertEqual(len(recs), 3)

    def test_broken_sidecar(self):
        bad = os.path.join(self.root, "geometry", "rocks", "Bad")
        os.makedirs(bad)
        with open(os.path.join(bad, schema.SIDECAR), "w") as f:
            f.write("nope")
        recs, errors = library.scan_roots([self.root], self.cache)
        rec = next(r for r in recs if r.name == "Bad")
        self.assertTrue(rec.broken)
        self.assertEqual(len(errors), 1)

    def test_missing_root(self):
        recs, errors = library.scan_roots([os.path.join(self.tmp.name, "nope")], self.cache)
        self.assertEqual(recs, [])
        self.assertIn("not found", errors[0])

    def test_moved_folder_rederives_category(self):
        library.scan_roots([self.root], self.cache)
        src = os.path.join(self.root, "geometry", "rocks", "Boulder")
        dst_parent = os.path.join(self.root, "geometry", "stones")
        os.makedirs(dst_parent)
        os.rename(src, os.path.join(dst_parent, "Boulder"))
        recs, _ = library.scan_roots([self.root], self.cache)
        self.assertEqual(next(r for r in recs if r.name == "Boulder").category, "stones")

    def test_scope_tree_categories_sort(self):
        recs, _ = library.scan_roots([self.root], self.cache)
        counts = library.tree_counts(recs)
        self.assertEqual(counts[(self.root, None, "")], 3)
        self.assertEqual(counts[(self.root, "geometry", "trees")], 1)
        self.assertEqual(counts[(self.root, "geometry", "trees/oak")], 1)
        self.assertEqual(library.categories(recs, asset_type="geometry"),
                         ["rocks", "trees", "trees/oak"])
        in_trees = [r.name for r in recs if library.in_scope(r, self.root, "geometry", "trees")]
        self.assertEqual(in_trees, ["Oak_A"])
        self.assertFalse([r for r in recs if library.in_scope(r, category="tree")])
        self.assertEqual([r.name for r in library.sort_records(recs, "added")][:2],
                         ["Sunset", "Oak_A"])
        self.assertEqual([r.name for r in library.sort_records(recs)], ["Boulder", "Oak_A", "Sunset"])


class VersionTests(unittest.TestCase):
    def test_versions_in_records(self):
        with tempfile.TemporaryDirectory() as d:
            root = os.path.join(d, "lib")
            asset_dir, data = make_asset(root, "geometry", "rocks", "Boulder")
            os.makedirs(os.path.join(asset_dir, "v002"))
            open(os.path.join(asset_dir, "v002", "b.bgeo.sc"), "w").close()
            self.assertEqual(schema.next_version(data), 2)
            schema.add_version(data, {"bgeo.sc": "v002/b.bgeo.sc"}, note="fixed normals")
            schema.set_current(data, 1)
            with self.assertRaises(ValueError):
                schema.set_current(data, 9)
            schema.save(os.path.join(asset_dir, schema.SIDECAR), data)
            rec, = library.scan_roots([root], os.path.join(d, "cache"))[0]
            self.assertEqual(rec.current, 1)
            self.assertEqual(rec.versions, [1, 2])
            self.assertEqual(rec.version(2)["note"], "fixed normals")
            self.assertTrue(rec.files_for(2)["bgeo.sc"].endswith(os.path.join("v002", "b.bgeo.sc")))
            self.assertEqual(rec.files_for(None), rec.files)
            self.assertEqual(rec.files_for(7), {})

    def test_old_index_version_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            cache = os.path.join(d, "cache")
            os.makedirs(cache)
            with open(library.index_path(cache, d), "w") as f:
                json.dump({"version": 1, "root": d, "entries": {"x": {}}}, f)
            self.assertEqual(library.load_index(cache, d), {})


class KarmaTests(unittest.TestCase):
    def test_frame_camera_fits_bounds(self):
        bounds = [[-1, 0, -1], [1, 2, 1]]
        eye, center, near, far = karma.frame_camera(bounds)
        self.assertEqual(center, (0.0, 1.0, 0.0))
        radius = (12 ** 0.5) / 2
        dist = sum((eye[i] - center[i]) ** 2 for i in range(3)) ** 0.5
        import math
        half_fov = math.atan(karma.APERTURE / 2 / karma.FOCAL)
        # The sphere's silhouette is cropped by `margin` (0.85): about the frame size.
        self.assertAlmostEqual(radius / dist, math.sin(half_fov) / 0.85, places=6)
        self.assertGreater(eye[1], center[1])                 # looking down a bit
        self.assertTrue(0 < near < dist - radius < dist + radius < far)

    def test_renderable_and_conversion(self):
        self.assertTrue(karma.is_renderable([[0, 0, 0], [1, 1, 1]]))
        self.assertFalse(karma.is_renderable([[0, 0, 0], [0, 0, 0]]))
        self.assertFalse(karma.is_renderable(None))
        self.assertFalse(karma.is_renderable([[0, 0, 0], [float("inf"), 1, 1]]))
        self.assertFalse(karma.needs_conversion("a.bgeo.sc"))
        self.assertTrue(karma.needs_conversion("a.fbx"))
        args = karma.husk_args("C:\\t\\s.usda", "C:\\t\\o.png", "BRAY_HdKarmaXPU")
        self.assertEqual(args[-1], "C:/t/s.usda")
        self.assertIn("BRAY_HdKarmaXPU", args)

    @unittest.skipUnless(HAVE_PXR, "pxr not available")
    def test_write_stage(self):
        from pxr import Usd, UsdGeom, UsdRender
        with tempfile.TemporaryDirectory() as d:
            layer = os.path.join(d, "asset.usda")
            st = Usd.Stage.CreateNew(layer)
            UsdGeom.Cube.Define(st, "/rock")
            st.GetRootLayer().Save()
            stage_path, image = karma.work_paths(d)
            karma.write_stage(stage_path, layer, [[-1, -1, -1], [1, 1, 1]], image, 256, 8)
            stage = Usd.Stage.Open(stage_path)
            self.assertTrue(stage.GetPrimAtPath("/rock"))              # sublayered asset
            settings = UsdRender.Settings(stage.GetPrimAtPath(karma.SETTINGS_PATH))
            self.assertEqual(tuple(settings.GetResolutionAttr().Get()), (256, 256))
            self.assertEqual(settings.GetCameraRel().GetTargets()[0].pathString, "/AXThumb/cam")
            self.assertEqual(stage.GetMetadata("renderSettingsPrimPath"), karma.SETTINGS_PATH)
            self.assertEqual(settings.GetPrim().GetAttribute(
                "karma:global:pathtracedsamples").Get(), 8)
            # No render products: husk refuses vars without its aov tokens and writes its
            # default beauty to --output instead.
            self.assertFalse(settings.GetProductsRel().GetTargets())
            self.assertFalse([p for p in stage.Traverse() if p.IsA(UsdRender.Var)])
            cam = UsdGeom.Camera(stage.GetPrimAtPath("/AXThumb/cam"))
            # The camera looks at the bounds center: center projects to the middle of the frame.
            xf = cam.ComputeLocalToWorldTransform(Usd.TimeCode.Default()).GetInverse()
            p = xf.Transform((0, 0, 0))
            self.assertAlmostEqual(p[0], 0, places=6)
            self.assertAlmostEqual(p[1], 0, places=6)
            self.assertLess(p[2], 0)                                    # in front (-Z)


class TextureTests(unittest.TestCase):
    def test_classify(self):
        c = textures.classify
        self.assertEqual(c("Rock_Wall_2K_Roughness.jpg"), ("Rock_Wall", "roughness"))
        self.assertEqual(c("rock_wall_diff_2k.jpg"), ("rock_wall", "basecolor"))
        self.assertEqual(c("rock_wall_nor_gl_4k.exr"), ("rock_wall", "normal"))
        self.assertEqual(c("Metal_Plate_BaseColor.png"), ("Metal_Plate", "basecolor"))
        self.assertEqual(c("Metal_Plate_Base_Color.png"), ("Metal_Plate", "basecolor"))
        self.assertEqual(c("Metal_Plate_Roughness.png"), ("Metal_Plate", "roughness"))
        self.assertEqual(c("vgvlbhqn_2K_Normal_LOD0.jpg"), ("vgvlbhqn", "normal"))
        self.assertEqual(c("bricks_ambient_occlusion.png"), ("bricks", "ao"))
        self.assertEqual(c("bricks_Cavity.jpg"), ("bricks", "other"))
        self.assertEqual(c("holiday_photo.jpg"), (None, None))
        self.assertTrue(textures.is_preview("vgvlbhqn_Preview.png"))
        self.assertFalse(textures.is_preview("bricks_albedo.png"))

    def _touch(self, d, *names):
        for n in names:
            p = os.path.join(d, n)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "w").close()
        return [os.path.join(d, n) for n in names]

    def test_group(self):
        with tempfile.TemporaryDirectory() as d:
            files = self._touch(
                d,
                "rock_cliff_vgvlbhqn/vgvlbhqn_2K_Albedo.jpg",
                "rock_cliff_vgvlbhqn/vgvlbhqn_2K_Normal_LOD0.jpg",
                "rock_cliff_vgvlbhqn/vgvlbhqn_2K_Normal_LOD2.jpg",
                "rock_cliff_vgvlbhqn/vgvlbhqn_2K_Displacement.jpg",
                "rock_cliff_vgvlbhqn/vgvlbhqn_2K_Displacement.exr",
                "rock_cliff_vgvlbhqn/vgvlbhqn_2K_Cavity.jpg",
                "rock_cliff_vgvlbhqn/vgvlbhqn_Preview.png",
                "mixed/wood_diff_2k.jpg", "mixed/wood_rough_2k.jpg",
                "mixed/wood_nor_dx_2k.jpg", "mixed/wood_nor_gl_2k.jpg",
                "mixed/metal_BaseColor.png", "mixed/metal_Metallic.png",
                "mixed/onlyao_ao.png",
                "mixed/photo.jpg", "mixed/sky.hdr", "mixed/notes.txt")
            sets, leftovers, previews = textures.group(files)
            by_name = {s.name: s for s in sets}
            self.assertEqual(sorted(by_name), ["metal", "rock_cliff_vgvlbhqn", "wood"])
            ms = by_name["rock_cliff_vgvlbhqn"]           # scan ID -> folder name
            self.assertTrue(ms.channels["normal"].endswith("Normal_LOD0.jpg"))
            self.assertTrue(ms.channels["displacement"].endswith(".exr"))
            self.assertTrue(ms.preview.endswith("Preview.png"))
            self.assertEqual(len(ms.extra), 3)             # LOD2, jpg disp, cavity
            self.assertTrue(by_name["wood"].channels["normal"].endswith("nor_gl_2k.jpg"))
            self.assertIsNone(by_name["wood"].preview)      # two sets in mixed/, no named preview
            names = sorted(os.path.basename(p) for p in leftovers)
            self.assertEqual(names, ["onlyao_ao.png", "photo.jpg", "sky.hdr"])
            self.assertEqual(previews, [])

    def test_hdri_and_height(self):
        self.assertTrue(textures.looks_like_hdri("a.hdr"))
        self.assertTrue(textures.looks_like_hdri("a.exr", (4096, 2048)))
        self.assertFalse(textures.looks_like_hdri("a.exr", (1024, 1024)))
        self.assertFalse(textures.looks_like_hdri("a.jpg", (4096, 2048)))
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(textures.megascans_height(d))
            with open(os.path.join(d, "x.json"), "w") as f:
                json.dump({"meta": [{"key": "height", "value": "0.02 m"}]}, f)
            self.assertEqual(textures.megascans_height(d), 0.02)


class StageTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_PXR, "pxr not available")
    def test_material_and_hdri_stages(self):
        from pxr import Usd, UsdGeom, UsdLux, UsdShade
        with tempfile.TemporaryDirectory() as d:
            stage_path, image = karma.work_paths(d)
            maps = {"basecolor": os.path.join(d, "a_albedo.png"),
                    "normal": os.path.join(d, "a_normal.png"),
                    "roughness": os.path.join(d, "a_rough.png")}
            karma.write_material_stage(stage_path, maps, image, 128, 4)
            st = Usd.Stage.Open(stage_path)
            ball = st.GetPrimAtPath("/AXThumb/ball")
            self.assertTrue(UsdGeom.PrimvarsAPI(ball).GetPrimvar("st").IsDefined())
            mat, _rel = UsdShade.MaterialBindingAPI(ball).ComputeBoundMaterial()
            self.assertEqual(mat.GetPath().pathString, "/AXThumb/materials/asset")
            surf = mat.ComputeSurfaceSource("mtlx")[0]
            self.assertEqual(surf.GetIdAttr().Get(), "ND_standard_surface_surfaceshader")
            src = surf.GetInput("base_color").GetConnectedSources()[0][0].source
            tex = UsdShade.Shader(src.GetPrim())
            self.assertEqual(tex.GetIdAttr().Get(), "ND_tiledimage_color3")
            self.assertEqual(tex.GetInput("file").GetAttr().GetColorSpace(), "srgb_texture")
            nsrc = surf.GetInput("normal").GetConnectedSources()[0][0].source
            self.assertEqual(UsdShade.Shader(nsrc.GetPrim()).GetIdAttr().Get(), "ND_normalmap_float")

            hdir = os.path.join(d, "hdri")  # one work folder per render, as in the queue
            os.makedirs(hdir)
            stage_path, image = karma.work_paths(hdir)
            karma.write_hdri_stage(stage_path, os.path.join(d, "sky.hdr"), image, 128, 4)
            st = Usd.Stage.Open(stage_path)
            dome = UsdLux.DomeLight(st.GetPrimAtPath("/AXThumb/dome"))
            self.assertTrue(dome.GetTextureFileAttr().Get().path.endswith("sky.hdr"))
            self.assertTrue(dome.GetPrim().GetAttribute("karma:light:renderlightgeo").Get())
            self.assertTrue(st.GetPrimAtPath("/AXThumb/chrome"))


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "lib")
        self.root2 = os.path.join(self.tmp.name, "lib2")
        self.asset, _ = make_asset(self.root, "geometry", "rocks/big", "Boulder", tags=["rock"])
        make_asset(self.root, "geometry", "rocks", "Pebble")

    def tearDown(self):
        self.tmp.cleanup()

    def scan(self, *roots):
        recs, _ = library.scan_roots(list(roots or [self.root]), os.path.join(self.tmp.name, "c"),
                                     use_cache=False)
        return {r.name: r for r in recs}

    def test_meta(self):
        manage.set_meta(self.asset, "Granite, rock", "  big one ")
        rec = self.scan()["Boulder"]
        self.assertEqual(rec.tags, ["granite", "rock"])
        self.assertEqual(rec.description, "big one")
        manage.set_meta(self.asset, description="")
        self.assertEqual(self.scan()["Boulder"].tags, ["granite", "rock"])

    def test_rename(self):
        new = manage.rename(self.asset, "Big Boulder")
        self.assertEqual(os.path.basename(new), "Big_Boulder")
        recs = self.scan()
        self.assertIn("Big_Boulder", recs)
        self.assertFalse(recs["Big_Boulder"].missing)       # relative paths still resolve
        with self.assertRaises(manage.ManageError):
            manage.rename(new, "   ")                          # empty name
        rec = library.load_record(new, self.root)
        self.assertEqual((rec.name, rec.category), ("Big_Boulder", "rocks/big"))

    def test_rename_conflict_and_case(self):
        pebble = os.path.join(self.root, "geometry", "rocks", "Pebble")
        other = manage.move(pebble, "geometry", self.root, self.root, "rocks/big")
        with self.assertRaises(manage.ManageError):
            manage.rename(other, "Boulder")
        new = manage.rename(self.asset, "boulder")           # case-only rename
        self.assertEqual(os.path.basename(new), "boulder")
        self.assertEqual(schema.load(os.path.join(new, "asset.json"))["name"], "boulder")

    def test_move_and_prune(self):
        new = manage.move(self.asset, "geometry", self.root, self.root, "stones")
        rec = self.scan()["Boulder"]
        self.assertEqual(rec.category, "stones")
        self.assertEqual(schema.load(os.path.join(new, "asset.json"))["category"], "stones")
        self.assertFalse(os.path.exists(os.path.join(self.root, "geometry", "rocks", "big")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "geometry", "rocks")))  # Pebble
        self.assertEqual(manage.move(new, "geometry", self.root, self.root, "stones"), new)
        other = manage.move(new, "geometry", self.root, self.root2, "")
        self.assertEqual(self.scan(self.root2)["Boulder"].category, "")
        self.assertTrue(os.path.isdir(other))

    def test_trash(self):
        dst = manage.trash(self.asset, self.root, "geometry")
        self.assertTrue(dst.startswith(os.path.join(self.root, ".trash")))
        self.assertNotIn("Boulder", self.scan())
        # A second asset with the same name gets its own trash folder.
        again, _ = make_asset(self.root, "geometry", "rocks", "Boulder")
        self.assertNotEqual(manage.trash(again, self.root, "geometry"), dst)


class CollectionTests(unittest.TestCase):
    def test_favorites_recent_query(self):
        with tempfile.TemporaryDirectory() as d:
            s = prefs.Prefs(os.path.join(d, "p.json"))
            prefs.set_favorite(s, ["a", "b"], True)
            prefs.set_favorite(s, ["a"], False)
            self.assertEqual(prefs.favorites(s), {"b"})
            s.set("recent_limit", 2)
            prefs.add_recent(s, ["x"], "2026-01-01")
            prefs.add_recent(s, ["y"], "2026-01-02")
            prefs.add_recent(s, ["x"], "2026-01-03")
            prefs.add_recent(s, ["z"], "2026-01-04")
            self.assertEqual(prefs.recent(s), {"z": "2026-01-04", "x": "2026-01-03"})
            recs = [library.AssetRecord(id="b", name="B"), library.AssetRecord(id="c", name="C")]
            q = library.Query("fav:1", prefs.favorites(s))
            self.assertEqual([r.name for r in recs if q.matches(r)], ["B"])
            q = library.Query("-fav:1", prefs.favorites(s))
            self.assertEqual([r.name for r in recs if q.matches(r)], ["C"])


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.recs = [
            library.AssetRecord(name="Big Boulder", type="geometry", category="rocks",
                                tags=["rock", "red oak"], files={"bgeo.sc": "a"}),
            library.AssetRecord(name="Oak", type="geometry", category="trees/oak",
                                tags=["tree", "wip"], files={"abc": "b"}, missing=["abc"]),
            library.AssetRecord(name="Sunset", type="hdri", category="sky",
                                description="warm evening", files={"exr": "c"}),
        ]

    def names(self, text):
        q = library.Query(text)
        return [r.name for r in self.recs if q.matches(r)]

    def test_queries(self):
        self.assertEqual(self.names(""), ["Big Boulder", "Oak", "Sunset"])
        self.assertEqual(self.names("oak"), ["Big Boulder", "Oak"])   # tag "red oak" + name
        self.assertEqual(self.names("type:hdr"), ["Sunset"])
        self.assertEqual(self.names("type:geo -tag:wip"), ["Big Boulder"])
        self.assertEqual(self.names('tag:"red oak"'), ["Big Boulder"])
        self.assertEqual(self.names("cat:trees"), ["Oak"])
        self.assertEqual(self.names("evening"), ["Sunset"])
        self.assertEqual(self.names("fmt:abc"), ["Oak"])
        self.assertEqual(self.names("type:geo type:hdri"), ["Big Boulder", "Oak", "Sunset"])
        self.assertEqual(self.names("broken"), ["Oak"])
        self.assertEqual(self.names("-broken"), ["Big Boulder", "Sunset"])
        self.assertEqual(self.names("http:x"), [])  # unknown field = plain word
        self.assertEqual(self.names("BIG boulder"), ["Big Boulder"])


class PrefsTests(unittest.TestCase):
    def test_roots_and_parm_path(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ[prefs.ENV_VAR] = d
            try:
                p = prefs.Prefs(os.path.join(d, "p.json"))
                roots = p.roots()
                self.assertEqual(roots[0]["path"], "$AX_ASSET_LIB")
                self.assertEqual(roots[0]["expanded"], os.path.normpath(d))
                f = os.path.join(d, "geometry", "rocks", "A", "v001", "a.bgeo.sc")
                self.assertEqual(prefs.parm_path(f, roots[0], "token"),
                                 "$AX_ASSET_LIB/geometry/rocks/A/v001/a.bgeo.sc")
                self.assertEqual(prefs.parm_path(f, roots[0], "absolute"), f.replace("\\", "/"))
                outside = os.path.join(os.path.dirname(d), "x.abc")
                self.assertEqual(prefs.parm_path(outside, roots[0], "token"),
                                 os.path.normpath(outside).replace("\\", "/"))
                p.set("roots", [{"path": d, "read_only": True}])
                again = prefs.Prefs(os.path.join(d, "p.json"))
                self.assertTrue(again.roots()[0]["read_only"])
                self.assertEqual(again.writable_roots(), [])
                self.assertIsNotNone(again.root_for(d))
            finally:
                del os.environ[prefs.ENV_VAR]

    def test_bad_types_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            with open(path, "w") as f:
                json.dump({"thumb_size": "big", "sort": "added", "unknown": 1}, f)
            p = prefs.Prefs(path)
            self.assertEqual(p.get("thumb_size"), prefs.DEFAULTS["thumb_size"])
            self.assertEqual(p.get("sort"), "added")


if __name__ == "__main__":
    unittest.main()
