"""Plain-Python tests for the hou-free parts of ax_asset_library.

Run from the repo root:  python -m unittest tests.test_ax_asset_library
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "python3.13libs"))

from ax_asset_library import (folders, gsg, karma, library, manage, megascans,  # noqa: E402
                              prefs, schema, textures, variants)


def make_gsg_library(root):
    """Fake Greyscalegorilla library with the real folder and file names (empty files)."""
    def asset(rel, meta_name, meta, files):
        d = os.path.join(root, rel)
        os.makedirs(d)
        with open(os.path.join(d, meta_name), "w") as f:
            json.dump(meta, f)
        for fn, size in files:
            with open(os.path.join(d, fn), "wb") as f:
                f.write(b"x" * size)
        return d

    hd = "GSG_HC006_A003_GSGPROSTUDIOSMETAL003"
    asset("hdris/" + hd, "GSG_PRO_STUDIOS_METAL_003.gsga",
          {"version": 1, "type": "hdri", "productCode": "HC006", "assetcode": "A003",
           "name": "GSG PRO STUDIOS METAL 003", "tags": "", "res": ""},
          [(hd + ".exr", 0), (hd + ".exr.rat", 0), (hd + ".hdr", 0), (hd + "_preview.jpg", 0)])
    mf = "GSG_MC032_A010_MicrofiberJerseyLightGreen"
    asset("materials/" + mf, "Microfiber_Jersey_Light_Green.gsgm",
          {"version": 1, "type": "material", "subtype": "cloth", "productCode": "MC032",
           "assetcode": "A010", "name": "Microfiber Jersey Light Green", "tags": "",
           "physicalsize": 0, "params": {"standard_surface": {"subsurface_scale": 0.01}}},
          [(mf + "_4k_" + ch, 0) for ch in ("basecolor.tif", "height.exr", "normal.tif",
                                            "roughness.tif", "anisotropyangle.tif",
                                            "sheencolor.tif")] + [(mf + "_preview.jpg", 0)])
    ck = "GSG_MC036_A001_CorkChips03"
    asset("materials/" + ck, "Cork_Chips_03.gsgm",
          {"version": 1, "type": "material", "productCode": "MC036", "assetcode": "A001",
           "name": "Cork Chips 03", "tags": "", "physicalsize": 35},
          [(ck + "_4k_" + ch, 0) for ch in ("basecolor.tif", "height.exr", "normal.tif",
                                            "roughness.tif")]
          + [("GSG_MC036_A001_Cork_preview.jpg", 0), ("Thumbs.db", 0)])
    tx = "GSG_GC001_A009_Abstract09"
    asset("textures/" + tx, "Abstract_09.gsga",
          {"version": 1, "type": "gobo", "productCode": "GC001", "assetcode": "A009",
           "name": "Abstract 09", "tags": "", "res": ""},
          [(tx + ".jpg", 100), (tx + "_preview.jpg", 10), ("Thumbs.db", 0)])
    md = "GSG_MD001_A001_Chair"
    asset("models/" + md, "Chair.gsga",
          {"version": 1, "type": "model", "productCode": "MD001", "assetcode": "A001",
           "name": "Chair", "tags": "wood, furniture"},
          [(md + ".fbx", 0), (md + "_4k_basecolor.tif", 0), (md + "_4k_normal.tif", 0)])
    return root

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
        # A missing thumb.jpg: an HDRI/image previews its own file.
        self.assertTrue(by_name["Sunset"].thumb.endswith("sunset.exr"))
        self.assertEqual(by_name["Sunset"].thumb_method, "image")
        self.assertIsNotNone(by_name["Sunset"].thumb_mtime)
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


class VariantsTests(unittest.TestCase):
    def test_lod_files_and_pick(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("x_LOD0.fbx", "x_LOD1.fbx", "x_LOD3.fbx", "x_LOD0.abc"):
                open(os.path.join(d, name), "w").close()
            files = {"fbx": os.path.join(d, "x_LOD0.fbx"),
                     "abc": os.path.join(d, "x_LOD0.abc")}
            lods = variants.lod_files(files)
            self.assertEqual(sorted(lods), [0, 1, 3])
            self.assertEqual(sorted(lods[1]), ["fbx"])
        self.assertEqual(variants.pick_lod([0, 1, 3], 2), 1)
        self.assertEqual(variants.pick_lod([0, 1, 3], 5), 3)
        self.assertEqual(variants.pick_lod([2, 3], 0), 2)
        self.assertEqual(variants.lod_label(-1), "Full")
        self.assertEqual(sorted(["Var10", "Var2", "Var1"], key=variants.variation_order),
                         ["Var1", "Var2", "Var10"])

    def test_resolution_sets_and_pick(self):
        with tempfile.TemporaryDirectory() as d:
            for name in ("a_2K_Albedo.jpg", "a_4K_Albedo.jpg", "a_4K_Normal.jpg"):
                open(os.path.join(d, name), "w").close()
            channels = {"basecolor": os.path.join(d, "a_4K_Albedo.jpg"),
                        "normal": os.path.join(d, "a_4K_Normal.jpg")}
            sets = variants.resolution_sets(channels)
            self.assertEqual(list(sets), ["4K", "2K"])
            self.assertEqual(sets["2K"]["basecolor"], os.path.join(d, "a_2K_Albedo.jpg"))
            self.assertEqual(sets["2K"]["normal"], channels["normal"])
            self.assertEqual(variants.resolution_sets({"basecolor": "x.jpg"}), {"": {"basecolor": "x.jpg"}})
        self.assertEqual(variants.pick_resolution(["4K", "2K"], "8K"), "4K")
        self.assertEqual(variants.pick_resolution(["4K", "2K"], "1K"), "2K")
        self.assertEqual(variants.pick_resolution(["4K", "2K"], ""), "4K")

    def test_linked_material_library(self):
        geo = library.AssetRecord(name="Rock", type="geometry", info={"megascans_id": "abc"},
                                  root="/lib2")
        mat = library.AssetRecord(name="Rock Mat", type="material", info={"megascans_id": "abc"},
                                  root="/lib1")
        same_root = library.AssetRecord(name="Rock Mat 2", type="material",
                                        info={"megascans_id": "abc"}, root="/lib2")
        self.assertIs(variants.linked_material(geo, [geo, mat, same_root]), same_root)
        self.assertIsNone(variants.linked_material(geo, [geo]))

    def test_is_atlas(self):
        rec = library.AssetRecord(type="material", info={"vendor_kind": "atlas"},
                                  files={"basecolor": "a.jpg"})
        self.assertTrue(variants.is_atlas(rec))
        rec.info = {"vendor_kind": "surface"}
        rec.files = {"basecolor": "a.jpg", "opacity": "b.jpg"}
        self.assertTrue(variants.is_atlas(rec))
        rec.files = {"basecolor": "a.jpg"}
        self.assertFalse(variants.is_atlas(rec))

    def test_companion_materials_hidden(self):
        def rec(key, rid, atype, kind="megascans"):
            return library.AssetRecord(key=key, id=rid, name=key, type=atype, source_kind=kind)
        rock_geo = rec("rg", "megascans:rock1:geometry", "geometry")
        rock_mat = rec("rm", "megascans:rock1:material", "material")
        plant_var = rec("pv", "megascans:fern1:geometry:Var1", "geometry")
        plant_atlas = rec("pa", "megascans:fern1:material", "material")
        surface = rec("sf", "megascans:sand1:material", "material")
        gsg_geo = rec("gg", "gsg:chair1:geometry", "geometry", "gsg")
        gsg_mat = rec("gm", "gsg:chair1:material", "material", "gsg")
        lib_geo = rec("lg", "abc", "geometry", "")
        lib_mat = rec("lm", "abc", "material", "")
        records = [rock_geo, rock_mat, plant_var, plant_atlas, surface, gsg_geo, gsg_mat,
                   lib_geo, lib_mat]
        self.assertEqual(library.companion_keys(records), {"rm", "pa", "gm"})
        self.assertEqual([r.key for r in library.browsable(records)],
                         ["rg", "pv", "sf", "gg", "lg", "lm"])
        self.assertIs(variants.linked_material(rock_geo, records), rock_mat)  # still linked


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

    def test_single_set_without_preview(self):
        with tempfile.TemporaryDirectory() as d:
            files = self._touch(d, "bark/bark_Albedo.jpg", "bark/bark_Normal.jpg")
            sets, _left, _prev = textures.group(files)
            self.assertEqual(len(sets), 1)
            self.assertIsNone(sets[0].preview)

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
    def test_backdrop_and_softbox(self):
        from pxr import Usd, UsdGeom, UsdLux
        bounds = [[-1, 0, -1], [1, 2, 1]]
        center = ((bounds[0][0] + bounds[1][0]) * 0.5, (bounds[0][2] + bounds[1][2]) * 0.5)
        radius = karma._vlen(karma._vsub(bounds[1], bounds[0])) * 0.5

        def check(stage_path, eye):
            stage = Usd.Stage.Open(stage_path)
            prim = stage.GetPrimAtPath("/AXThumb/backdrop")
            self.assertTrue(prim.IsValid())
            backdrop = UsdGeom.Mesh(prim)
            self.assertTrue(backdrop.GetDoubleSidedAttr().Get())
            points = backdrop.GetPointsAttr().Get()
            self.assertAlmostEqual(min(p[1] for p in points), 0.0, places=6)  # floor
            self.assertGreater(max(p[1] for p in points), eye[1])             # wall above eye
            for p in points:
                if p[1] > 2 * radius:  # the wall stands behind the asset, not in front
                    dx, dz = p[0] - center[0], p[2] - center[1]
                    self.assertLess(dx * (eye[0] - center[0]) + dz * (eye[2] - center[1]), 0)
            self.assertTrue(stage.GetPrimAtPath("/AXThumb/softbox").IsA(UsdLux.RectLight))
            self.assertFalse(stage.GetPrimAtPath("/AXThumb/key").IsValid())
            self.assertFalse(stage.GetPrimAtPath("/AXThumb/ground").IsValid())

        with tempfile.TemporaryDirectory() as d:
            layer = os.path.join(d, "asset.usda")
            st = Usd.Stage.CreateNew(layer)
            UsdGeom.Cube.Define(st, "/rock")
            st.GetRootLayer().Save()
            stage_path, image = karma.work_paths(d)
            karma.write_stage(stage_path, layer, bounds, image, 128, 4)
            check(stage_path, karma.frame_camera(bounds)[0])

            maps = {k: os.path.join(d, k + ".png") for k in ("basecolor", "normal", "roughness")}
            mdir = os.path.join(d, "material")
            os.makedirs(mdir)
            stage_path, image = karma.work_paths(mdir)
            karma.write_material_stage(stage_path, maps, image, 128, 4)
            check(stage_path, karma.frame_camera(bounds, elevation=15.0)[0])

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

    @unittest.skipUnless(HAVE_PXR, "pxr not available")
    def test_geometry_stage_binds_material(self):
        from pxr import Sdf, Usd, UsdGeom, UsdShade
        for primvar in ("st", "uv"):
            with tempfile.TemporaryDirectory() as d:
                layer = os.path.join(d, "asset.usda")
                st = Usd.Stage.CreateNew(layer)
                mesh = UsdGeom.Mesh.Define(st, "/Rock/mesh")
                UsdGeom.PrimvarsAPI(mesh).CreatePrimvar(
                    primvar, Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex)
                st.DefinePrim("/HoudiniLayerInfo", "HoudiniLayerInfo")
                st.GetRootLayer().Save()
                del st
                stage_path, image = karma.work_paths(d)
                maps = {"basecolor": os.path.join(d, "rock_albedo.png")}
                karma.write_stage(stage_path, layer, [[-1, -1, -1], [1, 1, 1]], image, 64, 1,
                                  maps=maps)
                stage = Usd.Stage.Open(stage_path)
                rock = stage.GetPrimAtPath("/Rock/mesh")
                mat, _rel = UsdShade.MaterialBindingAPI(rock).ComputeBoundMaterial()
                self.assertEqual(mat.GetPath().pathString, karma.MATERIAL_PATH)
                self.assertFalse(stage.GetPrimAtPath("/HoudiniLayerInfo")
                                 .HasAPI(UsdShade.MaterialBindingAPI))
                tex = UsdShade.Shader(stage.GetPrimAtPath(karma.MATERIAL_PATH + "/basecolor_tex"))
                self.assertEqual(tuple(tex.GetInput("uvtiling").Get()), (1.0, 1.0))
                reader = stage.GetPrimAtPath(karma.MATERIAL_PATH + "/texcoord")
                if primvar == "st":
                    self.assertFalse(reader.IsValid())
                else:
                    self.assertEqual(UsdShade.Shader(reader).GetInput("geomprop").Get(), "uv")
                    self.assertTrue(tex.GetInput("texcoord").HasConnectedSource())
                del stage


class MegascansTests(unittest.TestCase):
    """Fake Bridge download with the folder layout and JSON keys of real Megascans assets."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dl = os.path.join(self.tmp.name, "Downloaded")

        def asset(rel, aid, name, cats, meta, files):
            d = os.path.join(self.dl, rel)
            os.makedirs(d)
            with open(os.path.join(d, aid + ".json"), "w") as f:
                json.dump({"id": aid, "name": name, "tags": ["rock", "Beach"], "categories": cats,
                           "meta": [{"key": k, "value": v} for k, v in meta.items()]}, f)
            for fn in files:
                p = os.path.join(d, fn)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w").close()
            return d

        asset("surface/rock_cliff_wdeledb", "wdeledb", "Beach Cliff", ["surface", "Rock", "cliff"],
              {"height": "0.094 m", "scanArea": "2x2 m"},
              ["wdeledb_4K_Albedo.jpg", "wdeledb_2K_Albedo.jpg", "wdeledb_4K_Normal.jpg",
               "wdeledb_4K_Roughness.jpg", "wdeledb_4K_Displacement.exr", "wdeledb_Preview.png",
               "Thumbs/1k/wdeledb_1K_Albedo.jpg"])
        asset("3d/nature_tree_wgqvdhf", "wgqvdhf", "Broken Tree Branch", ["3d", "nature", "tree"],
              {"length": "1.02m", "width": "0.34m", "height": "0.21m"},
              ["wgqvdhf_LOD0.fbx", "wgqvdhf_LOD3.fbx", "wgqvdhf_8K_Albedo.jpg",
               "wgqvdhf_8K_Normal_LOD0.jpg", "wgqvdhf_8K_Normal_LOD2.jpg", "wgqvdhf_Preview.png"])
        asset("3dplant/plant_xefmcgrqx", "xefmcgrqx", "Small Balsam", ["3dplant", "ground cover"],
              {"dimensions": [{"variation": 1, "length": "0.26m", "width": "0.24m", "height": "0.1m"}]},
              ["Var1/Var1_LOD0.fbx", "Var1/Var1_LOD1.fbx", "Var2/Var2_LOD0.fbx",
               "Textures/Atlas/xefmcgrqx_8K_Albedo.jpg", "Textures/Atlas/xefmcgrqx_8K_Normal.jpg",
               "Textures/Billboard/Billboard_8K_Albedo.jpg", "xefmcgrqx_Preview.png"])
        asset("brush/brush_xyz", "xyzbrush", "Brush", ["brush"], {}, ["xyzbrush_Brush.png"])
        os.makedirs(os.path.join(self.tmp.name, "other"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_and_plan(self):
        other_dir = os.path.join(self.tmp.name, "other")
        assets, other = megascans.find_assets([self.dl, other_dir])
        self.assertEqual(other, [other_dir])
        by = {a.id: a for a in assets}
        self.assertEqual(sorted(by), ["wdeledb", "wgqvdhf", "xefmcgrqx", "xyzbrush"])

        surf = by["wdeledb"]
        self.assertEqual(surf.library_name, "Beach_Cliff_wdeledb")
        self.assertEqual(surf.category, "rock/cliff")             # lower case
        self.assertEqual(surf.displacement_scale, 0.094)
        self.assertTrue(surf.preview.endswith("wdeledb_Preview.png"))
        (kind, name, files, info, _extra), = megascans.plan(surf)
        self.assertEqual(kind, "material")
        self.assertTrue(files["basecolor"].endswith("4K_Albedo.jpg"))   # highest resolution
        self.assertTrue(files["displacement"].endswith(".exr"))
        self.assertEqual(info["displacement_scale"], 0.094)

        mesh = by["wgqvdhf"]
        self.assertIsNone(mesh.displacement_scale)                # object height, not displacement
        made = {k: (f, i) for k, _n, f, i, _e in megascans.plan(mesh)}
        self.assertTrue(made["geometry"][0]["fbx"].endswith("LOD0.fbx"))
        self.assertEqual(made["geometry"][1]["import_scale"], 0.01)
        self.assertEqual(made["geometry"][1]["megascans_size_m"], 1.02)
        self.assertTrue(made["material"][0]["normal"].endswith("Normal_LOD0.jpg"))

        plant = megascans.plan(by["xefmcgrqx"])
        names = [(k, n) for k, n, _f, _i, _e in plant]
        self.assertEqual(names, [("geometry", "Small_Balsam_xefmcgrqx"),
                                 ("material", "Small_Balsam_xefmcgrqx")])
        geo_files, geo_info = plant[0][2], plant[0][3]
        self.assertEqual(sorted(geo_files), ["fbx"])              # Var1 at its most detailed LOD
        self.assertTrue(geo_files["fbx"].endswith("Var1_LOD0.fbx"))
        self.assertEqual(sorted(geo_info["variations"]), ["Var1", "Var2"])
        self.assertTrue(geo_info["variations"]["Var2"]["fbx"].endswith("Var2_LOD0.fbx"))
        self.assertEqual(geo_info["megascans_size_m"], 0.26)
        self.assertNotIn("Billboard", plant[1][2]["basecolor"])  # atlas, not billboard
        self.assertEqual(megascans.plan(by["xyzbrush"]), [])

    def test_cache_entry_usable_while_rebuilding(self):
        records, entries, errors = megascans.scan_root(self.dl)
        self.assertFalse(errors, errors)
        self.assertTrue(entries)
        # An entry with records (brush folders have none; entry order follows the threads).
        key, entry = next((k, e) for k, e in sorted(entries.items()) if e["records"])
        entry["version"] = 2
        self.assertFalse(megascans._entry_current(entry, entry.get("mtime")))
        again = megascans.records_from_entries({key: entry}, self.dl, "")
        self.assertTrue(again)

    def test_scan_root_parallel_matches_serial(self):
        serial, serial_entries, serial_errors = megascans.scan_root(self.dl, workers=1)
        parallel, parallel_entries, parallel_errors = megascans.scan_root(self.dl, workers=8)
        self.assertEqual([r.id for r in serial], [r.id for r in parallel])
        self.assertEqual(sorted(serial_entries), sorted(parallel_entries))
        self.assertEqual(serial_errors, parallel_errors)

    def test_scan_root_progress_and_batches(self):
        batches, progress = [], []
        with mock.patch.object(megascans, "BATCH_SIZE", 1):
            megascans.scan_root(
                self.dl, workers=4,
                on_batch=lambda records, entries: batches.append((records, entries)),
                progress=lambda done, total: progress.append((done, total)))
        self.assertTrue(batches)
        self.assertIsInstance(batches[-1][0], list)
        self.assertIsInstance(batches[-1][1], dict)
        self.assertEqual(progress[-1][0], progress[-1][1])
        self.assertGreater(progress[-1][0], 0)

    def test_scan_roots_saves_partial_index(self):
        cache = os.path.join(self.tmp.name, "cache")
        calls = []

        class Stop(Exception):
            pass

        def partial(records):
            calls.append(records)
            raise Stop()

        with mock.patch.object(megascans, "BATCH_SIZE", 1):
            with self.assertRaises(Stop):
                library.scan_roots([(self.dl, "megascans")], cache, partial=partial)
        self.assertTrue(calls)
        self.assertTrue(library.load_index(cache, self.dl, "megascans"))

    def _bridge_asset(self, rel, aid, data, files=()):
        """Asset with a full Bridge JSON (assetCategories) under <tmp>/bridge."""
        d = os.path.join(self.tmp.name, "bridge", rel)
        os.makedirs(d)
        with open(os.path.join(d, aid + ".json"), "w") as f:
            json.dump(dict(data, id=aid), f)
        for fn in files:
            open(os.path.join(d, fn), "w").close()
        return d

    def test_bridge_kind_and_category(self):
        d = self._bridge_asset("atlases/tdfdfebc_Leakage", "tdfdfebc", {
            "name": "Leakage", "categories": ["atlas", "Decal", "Leakage"],
            "assetCategories": {"decal": {"leakage": {"stamp": {}, "other": {}}}}})
        a = megascans.read_asset(d)
        self.assertEqual(a.kind, "decal")
        self.assertEqual(a.category, "leakage/stamp")
        self.assertEqual(a.browse_category, "Decals/leakage/stamp")
        d = self._bridge_asset("3d/vmnmaax_Cliff", "vmnmaax", {
            "name": "Cliff", "categories": ["3d", "nature", "rock"],
            "assetCategories": {"3D asset": {"nature": {"rock": {"cliff": {}}}}}})
        self.assertEqual(megascans.read_asset(d).browse_category, "3D Assets/nature/rock/cliff")

    def test_bridge_category_fallback(self):
        surf = megascans.read_asset(os.path.join(self.dl, "surface", "rock_cliff_wdeledb"))
        self.assertEqual(surf.category, "rock/cliff")              # from "categories"
        self.assertEqual(surf.browse_category, "Surfaces/rock/cliff")

    def test_flat_plant_has_geometry(self):
        d = self._bridge_asset("3d/qmBr2_Grass Clumps", "qmBr2", {
            "name": "Grass Clumps", "categories": ["3dplant", "plants", "3d"],
            "assetCategories": {"3D plant": {"grass": {"lawn": {}}}}},
            ["qmBr2_LOD0.fbx", "qmBr2_LOD1.fbx", "qmBr2_4K_Albedo.jpg", "qmBr2_4K_Opacity.jpg",
             "qmBr2_4K_Normal.jpg"])
        a = megascans.read_asset(d)
        self.assertEqual(a.kind, "3dplant")
        types = [t for t, _n, _f, _i, _e in megascans.plan(a)]
        self.assertEqual(types, ["geometry", "material"])

    def test_scan_root_cancelled(self):
        records, entries, errors = megascans.scan_root(self.dl, workers=2,
                                                       cancelled=lambda: True)
        self.assertEqual(errors, [])
        self.assertLessEqual(len(entries), 2 * 4)
        records, entries, errors = megascans.scan_root(self.dl, workers=1,
                                                       cancelled=lambda: True)
        self.assertEqual(entries, {})

    def test_tree_counts_without_type(self):
        recs, _ = library.scan_roots([(self.dl, "megascans")],
                                     os.path.join(self.tmp.name, "cache"))
        counts = library.tree_counts(library.browsable(recs))
        self.assertEqual(counts[(self.dl, None, "3D Plants")], 1)   # all variations, one record
        self.assertIn("Surfaces/rock/cliff", library.categories(recs, self.dl, None))

    def test_linked_material_records(self):
        recs, errors = library.scan_roots([(self.dl, "megascans")],
                                          os.path.join(self.tmp.name, "cache"))
        self.assertFalse(errors, errors)
        geo = next(r for r in recs if r.type == "geometry"
                   and r.id.startswith("megascans:wgqvdhf:"))
        mat = variants.linked_material(geo, recs)
        self.assertIsNotNone(mat)
        self.assertEqual(mat.type, "material")
        self.assertEqual(mat.id.split(":")[:2], geo.id.split(":")[:2])

    def test_check_scale_and_height(self):
        info = {"import_scale": 0.01, "megascans_size_m": 1.02,
                "bounds": [[-50.8, -5.7, -14.8], [50.9, 15.0, 19.2]]}
        self.assertEqual(megascans.check_scale(info), 0.01)       # cm file
        info["bounds"] = [[-0.5, 0, -0.1], [0.52, 0.2, 0.2]]
        self.assertEqual(megascans.check_scale(info), 1.0)        # already meters
        self.assertEqual(textures.megascans_height(
            os.path.join(self.dl, "3d", "nature_tree_wgqvdhf")), None)
        self.assertEqual(textures.megascans_height(
            os.path.join(self.dl, "surface", "rock_cliff_wdeledb")), 0.094)

    def _downloader_layout(self):
        """megascans_dl layout: <kind>/<id>_<Name>/<id>.json with only assetID."""
        root = os.path.join(self.tmp.name, "megascans_dl")

        def asset(rel, aid, files):
            d = os.path.join(root, rel)
            os.makedirs(d)
            with open(os.path.join(d, aid + ".json"), "w") as f:
                json.dump({"assetID": aid, "resolution": 8192, "exrAccess": "full"}, f)
            for fn in files:
                open(os.path.join(d, fn), "w").close()
            return d

        asset("3d/vcsaeeyfa_Burnt Log", "vcsaeeyfa",
              ["vcsaeeyfa_LOD0.fbx", "vcsaeeyfa_LOD1.fbx", "vcsaeeyfa_4K_Albedo.jpg",
               "vcsaeeyfa_4K_Normal_LOD0.jpg", "vcsaeeyfa_4K_Roughness.jpg"])
        asset("surfaces/uhpjdeyn_Fresh Windswept Snow", "uhpjdeyn",
              ["uhpjdeyn_4K_Albedo.jpg", "uhpjdeyn_4K_Normal.jpg", "uhpjdeyn_4K_Displacement.exr"])
        asset("atlases/sguxbeff2_Golden Chain", "sguxbeff2",
              ["sguxbeff2_4K_Albedo.jpg", "sguxbeff2_4K_Opacity.jpg", "sguxbeff2_4K_Normal.jpg"])
        asset("unknown/seumefac_High Velocity Blood Spatter", "seumefac", ["seumefac_4K_Brush.jpg"])
        asset("unknown/abcdefgh_Old Crate", "abcdefgh", ["abcdefgh_LOD0.fbx"])
        return root

    def test_downloader_layout(self):
        root = self._downloader_layout()
        self.assertTrue(megascans.looks_like_download(root))
        log = megascans.read_asset(os.path.join(root, "3d", "vcsaeeyfa_Burnt Log"))
        self.assertEqual((log.id, log.name, log.kind), ("vcsaeeyfa", "Burnt Log", "3d"))
        self.assertEqual(log.library_name, "Burnt_Log_vcsaeeyfa")
        assets, _other = megascans.find_assets([root])
        kinds = {a.id: a.kind for a in assets}
        self.assertEqual(kinds, {"vcsaeeyfa": "3d", "uhpjdeyn": "surface", "sguxbeff2": "atlas",
                                 "seumefac": "", "abcdefgh": "3d"})   # unknown/ inferred from files

    def test_scan_in_place(self):
        root = self._downloader_layout()
        cache = os.path.join(self.tmp.name, "cache")
        before = sorted(os.walk(root))
        recs, errs = library.scan_roots([(root, "megascans")], cache)
        self.assertEqual(errs, [])
        self.assertEqual(sorted(os.walk(root)), before)                 # nothing written there
        got = sorted((r.type, r.name, r.category) for r in recs)
        self.assertEqual(got, [
            ("geometry", "Burnt_Log_vcsaeeyfa", "3D Assets"),
            ("geometry", "Old_Crate_abcdefgh", "3D Assets"),
            ("material", "Burnt_Log_vcsaeeyfa", "3D Assets"),
            ("material", "Fresh_Windswept_Snow_uhpjdeyn", "Surfaces"),
            ("material", "Golden_Chain_sguxbeff2", "Atlases")])
        log = next(r for r in recs if r.type == "geometry" and "Burnt" in r.name)
        self.assertEqual(log.id, "megascans:vcsaeeyfa:geometry")
        self.assertEqual(log.info["import_scale"], 0.01)
        self.assertTrue(log.files["fbx"].endswith("vcsaeeyfa_LOD0.fbx"))
        self.assertEqual(len({r.key for r in recs}), len(recs))
        self.assertIsNone(log.thumb_mtime)                              # no preview, no render yet
        snow = next(r for r in recs if "Snow" in r.name)
        self.assertEqual(snow.info["resolution"], [4096, 4096])

        # A rendered thumbnail in the cache is picked up; cached records match a fresh scan.
        thumb = megascans.thumb_path(library.megascans_thumb_dir(cache), log.id)
        os.makedirs(os.path.dirname(thumb), exist_ok=True)
        open(thumb, "wb").close()
        again, _ = library.scan_roots([(root, "megascans")], cache)
        log2 = next(r for r in again if r.id == log.id)
        self.assertEqual(log2.thumb, thumb)
        self.assertIsNotNone(log2.thumb_mtime)
        cached = library.records_from_index(cache, root, "megascans")
        self.assertEqual(sorted(r.key for r in cached), sorted(r.key for r in again))

        # A new download shows up on rescan.
        d = os.path.join(root, "surfaces", "zzzzzzzz_New Moss")
        os.makedirs(d)
        with open(os.path.join(d, "zzzzzzzz.json"), "w") as f:
            json.dump({"assetID": "zzzzzzzz"}, f)
        open(os.path.join(d, "zzzzzzzz_2K_Albedo.jpg"), "w").close()
        recs3, _ = library.scan_roots([(root, "megascans")], cache)
        self.assertIn("New_Moss_zzzzzzzz", {r.name for r in recs3})

    def test_looks_like_download_needs_megascans_json(self):
        # A project folder with a "models" subfolder is not a Megascans download.
        proj = os.path.join(self.tmp.name, "project")
        os.makedirs(os.path.join(proj, "models", "chair"))
        open(os.path.join(proj, "models", "chair", "chair.fbx"), "w").close()
        self.assertFalse(megascans.looks_like_download(proj))
        self.assertTrue(megascans.looks_like_download(self._downloader_layout()))
        self.assertTrue(megascans.looks_like_download(self.tmp.name))   # holds "Downloaded"

    def test_plant_variations_in_one_record(self):
        cache = os.path.join(self.tmp.name, "cache")
        recs, errors = library.scan_roots([(self.dl, "megascans")], cache)
        self.assertFalse(errors, errors)
        geo = next(r for r in recs if r.type == "geometry" and "Small_Balsam" in r.name)
        self.assertEqual(geo.id, "megascans:xefmcgrqx:geometry")
        self.assertTrue(geo.files["fbx"].endswith("Var1_LOD0.fbx"))
        self.assertEqual(sorted(geo.variations_for()), ["Var1", "Var2"])
        self.assertTrue(geo.variations_for()["Var2"]["fbx"].endswith("Var2_LOD0.fbx"))
        self.assertEqual(next(r for r in recs if r.type == "material").variations_for(), {})
        # The scan cache keeps the variation set (info["variations"] -> version entry).
        for again in (library.scan_roots([(self.dl, "megascans")], cache)[0],
                      library.records_from_index(cache, self.dl, "megascans")):
            rec = next(r for r in again if r.id == geo.id)
            self.assertEqual(sorted(rec.variations_for()), ["Var1", "Var2"])

    def test_nested_folder_change_rescans_asset(self):
        cache = os.path.join(self.tmp.name, "cache")
        plant = os.path.join(self.dl, "3dplant", "plant_xefmcgrqx")
        names = lambda recs: sorted(r.name for r in recs if "Small_Balsam" in r.name)
        recs, _ = library.scan_roots([(self.dl, "megascans")], cache)
        self.assertEqual(len(names(recs)), 2)            # one geometry (Var1..N) + atlas material
        # Files added inside Var2/ (an existing folder) don't change the asset folder's mtime.
        var3 = os.path.join(plant, "Var2", "Var2_LOD0.abc")
        st = os.stat(plant).st_mtime
        open(var3, "w").close()
        os.utime(os.path.join(plant, "Var2"), (st + 10, st + 10))
        os.utime(plant, (st, st))
        recs, _ = library.scan_roots([(self.dl, "megascans")], cache)
        geo = next(r for r in recs if r.type == "geometry" and "Small_Balsam" in r.name)
        var2 = geo.variations_for(1)["Var2"]
        self.assertIn("abc", var2)
        self.assertTrue(var2["abc"].endswith("Var2_LOD0.abc"))

    def test_same_asset_in_two_roots_listed_once(self):
        cache = os.path.join(self.tmp.name, "cache")
        root2 = os.path.join(self.tmp.name, "copy")
        shutil.copytree(os.path.join(self.dl, "surface"), os.path.join(root2, "surfaces"))
        recs, _ = library.scan_roots([(self.dl, "megascans"), (root2, "megascans")], cache)
        ids = [r.id for r in recs]
        self.assertEqual(len(ids), len(set(ids)))
        cliff = next(r for r in recs if "Beach_Cliff" in r.name)
        self.assertEqual(cliff.root, self.dl)                     # first root wins

    def test_deleted_rendered_thumbnail_falls_back(self):
        cache = os.path.join(self.tmp.name, "cache")
        thumb_dir = library.megascans_thumb_dir(cache)
        recs, _ = library.scan_roots([(self.dl, "megascans")], cache)
        cliff = next(r for r in recs if "Beach_Cliff" in r.name)
        self.assertTrue(cliff.thumb.endswith("wdeledb_Preview.png"))
        rendered = megascans.thumb_path(thumb_dir, cliff.id)
        os.makedirs(thumb_dir, exist_ok=True)
        open(rendered, "wb").close()
        recs, _ = library.scan_roots([(self.dl, "megascans")], cache)
        self.assertEqual(next(r for r in recs if r.id == cliff.id).thumb, rendered)
        os.remove(rendered)                                       # cache cleared by the user
        for again in (library.scan_roots([(self.dl, "megascans")], cache)[0],
                      library.records_from_index(cache, self.dl, "megascans")):
            rec = next(r for r in again if r.id == cliff.id)
            self.assertTrue(rec.thumb.endswith("wdeledb_Preview.png"))
            self.assertEqual(rec.thumb_method, "image")

    def test_sort_modes(self):
        recs = [library.AssetRecord(name="b", type="material", category="x"),
                library.AssetRecord(name="a", type="hdri", category=""),
                library.AssetRecord(name="c", type="geometry", category="a")]
        self.assertEqual([r.name for r in library.sort_records(recs, "type")], ["c", "b", "a"])
        self.assertEqual([r.name for r in library.sort_records(recs, "category")], ["c", "b", "a"])


class GsgTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_gsg_library(os.path.join(self.tmp.name, "Greyscalegorilla"))
        self.cache = os.path.join(self.tmp.name, "cache")

    def tearDown(self):
        self.tmp.cleanup()

    def _asset(self, rel):
        return gsg.read_asset(os.path.join(self.root, rel))

    def test_read_and_plan(self):
        mf = self._asset("materials/GSG_MC032_A010_MicrofiberJerseyLightGreen")
        self.assertEqual((mf.id, mf.name, mf.kind, mf.category),
                         ("MC032_A010", "Microfiber Jersey Light Green", "material", "cloth"))
        self.assertIn("cloth", mf.tags)
        self.assertEqual(mf.library_name, "Microfiber_Jersey_Light_Green_MC032_A010")

        (kind, _n, files, _i, extra), = gsg.plan(
            self._asset("hdris/GSG_HC006_A003_GSGPROSTUDIOSMETAL003"))
        self.assertEqual(kind, "hdri")
        self.assertTrue(files["hdri"].endswith(".exr"))
        self.assertEqual(sorted(schema.file_ext(f) for f in extra), [".hdr", ".rat"])

        (kind, _n, files, info, extra), = gsg.plan(mf)
        self.assertEqual(sorted(files), ["basecolor", "displacement", "normal", "roughness"])
        self.assertEqual(sorted(os.path.basename(f).split("_4k_")[1] for f in extra),
                         ["anisotropyangle.tif", "sheencolor.tif"])
        self.assertEqual(info["resolution"], [4096, 4096])
        self.assertNotIn("physical_size", info)                     # physicalsize 0

        cork = self._asset("materials/GSG_MC036_A001_CorkChips03")
        self.assertTrue(cork.preview.endswith("GSG_MC036_A001_Cork_preview.jpg"))
        self.assertEqual(gsg.plan(cork)[0][3]["physical_size"], 35)
        self.assertFalse(any("Thumbs.db" in f for f in cork.content))

        (kind, _n, files, _i, _e), = gsg.plan(self._asset("textures/GSG_GC001_A009_Abstract09"))
        self.assertEqual(kind, "image")
        self.assertTrue(files["image"].endswith("Abstract09.jpg"))   # not the preview

        chair = self._asset("models/GSG_MD001_A001_Chair")
        self.assertEqual(chair.tags, ["wood", "furniture"])
        self.assertEqual([p[0] for p in gsg.plan(chair)], ["geometry", "material"])

    def test_scan_in_place(self):
        before = sorted(os.walk(self.root))
        recs, errs = library.scan_roots([(self.root, "gsg")], self.cache)
        self.assertEqual(errs, [])
        self.assertEqual(sorted(os.walk(self.root)), before)       # nothing written there
        types = sorted(r.type for r in recs)
        self.assertEqual(types, ["geometry", "hdri", "image", "material", "material", "material"])
        ids = {r.id for r in recs}
        by_id = {r.id: r for r in recs}
        self.assertEqual(by_id["gsg:MC032_A010:material"].category, "cloth")
        self.assertEqual(by_id["gsg:MC036_A001:material"].category, "")   # no subtype
        self.assertIn("gsg:MC036_A001:material", ids)
        self.assertIn("gsg:HC006_A003:hdri", ids)
        self.assertTrue(all(r.source_kind == "gsg" for r in recs))
        with_preview = [r for r in recs if r.id != "gsg:MD001_A001:geometry"
                        and r.id != "gsg:MD001_A001:material"]
        self.assertTrue(all(r.thumb.endswith("_preview.jpg") for r in with_preview))
        cached = library.records_from_index(self.cache, self.root, "gsg")
        self.assertEqual(sorted(r.key for r in cached), sorted(r.key for r in recs))
        again, _ = library.scan_roots([(self.root, "gsg")], self.cache)
        self.assertEqual(sorted(r.key for r in again), sorted(r.key for r in recs))

    def test_detection(self):
        self.assertTrue(gsg.looks_like_library(self.root))
        self.assertFalse(megascans.looks_like_download(self.root))
        proj = os.path.join(self.tmp.name, "project")
        os.makedirs(os.path.join(proj, "materials", "wood"))
        open(os.path.join(proj, "materials", "wood", "wood.jpg"), "w").close()
        self.assertFalse(gsg.looks_like_library(proj))
        ms = os.path.join(self.tmp.name, "ms", "surfaces", "abcdefgh_Moss")
        os.makedirs(ms)
        with open(os.path.join(ms, "abcdefgh.json"), "w") as f:
            json.dump({"assetID": "abcdefgh"}, f)
        self.assertFalse(gsg.looks_like_library(os.path.join(self.tmp.name, "ms")))
        self.assertTrue(megascans.looks_like_download(os.path.join(self.tmp.name, "ms")))

    def test_prefs_kinds(self):
        s = prefs.Prefs(os.path.join(self.tmp.name, "p.json"))
        s.set("roots", [{"path": "a", "kind": "gsg"}, {"path": "b", "megascans": True},
                        {"path": "c", "kind": "nonsense"}])
        roots = s.roots()
        self.assertEqual([r["kind"] for r in roots], ["gsg", "megascans", "library"])
        self.assertEqual([r["read_only"] for r in roots], [True, True, False])

    def test_dedupe_two_roots(self):
        root2 = os.path.join(self.tmp.name, "copy")
        shutil.copytree(os.path.join(self.root, "hdris"), os.path.join(root2, "hdris"))
        recs, _ = library.scan_roots([(self.root, "gsg"), (root2, "gsg")], self.cache)
        self.assertEqual([r.id for r in recs].count("gsg:HC006_A003:hdri"), 1)


class FoldersTests(unittest.TestCase):
    """Watch folders: folders with no metadata file, recognized from their file names."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "watch")

        def files(rel, names):
            d = os.path.join(self.root, *rel.split("/"))
            os.makedirs(d, exist_ok=True)
            for n in names:
                p = os.path.join(d, n)
                os.makedirs(os.path.dirname(p), exist_ok=True)
                open(p, "w").close()
            return d

        self.chair = files("cat/Chair", ["Chair.usd", "geo.usd", "mtl.usd", "thumbnail.png",
                                         "maps/Chair_basecolor.png", "maps/Chair_roughness.png"])
        self.props = files("props", ["Rock.abc", "Rock.bgeo.sc", "Rock_preview.jpg", "Pot.fbx"])
        self.tex = files("tex", ["Brick_basecolor.jpg", "Brick_roughness.jpg",
                                 "Brick_normal.jpg"])
        self.lamp = files("a/b/Lamp", ["Lamp.usdc"])
        self.mixed = files("mixed", ["Tree.abc"])
        files("mixed/sub", ["Bush.abc"])

        self.records, self.entries, self.errors = folders.scan_root(self.root)
        self.assertFalse(self.errors, self.errors)

    def tearDown(self):
        self.tmp.cleanup()

    def _in(self, folder, asset_type=None):
        return [r for r in self.records if os.path.normcase(r.asset_dir)
                == os.path.normcase(folder) and (asset_type is None or r.type == asset_type)]

    def _write(self, root, rels):
        for rel in rels:
            path = os.path.join(root, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()

    def test_unrelated_texture_set_stays_standalone(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, ["props/Rock.abc", "props/Pot.fbx", "props/Brick_basecolor.jpg",
                            "props/Brick_roughness.jpg"])
            recs, _entries, errors = folders.scan_root(d)
            self.assertFalse(errors, errors)
            shown = library.browsable(recs)
            self.assertEqual(sorted((r.type, r.name) for r in shown),
                             [("geometry", "Pot"), ("geometry", "Rock"),
                              ("material", "Brick")])
            rock = next(r for r in recs if r.name == "Rock")
            self.assertIsNone(variants.linked_material(rock, recs))

    def test_texture_set_named_like_model_is_linked(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, ["m/Chair.fbx", "m/Chair_basecolor.jpg", "m/Chair_roughness.jpg",
                            "m/Lamp.fbx"])
            recs, _entries, _errors = folders.scan_root(d)
            self.assertEqual([r.name for r in library.browsable(recs)], ["Chair", "Lamp"])
            chair = next(r for r in recs if r.name == "Chair")
            self.assertEqual(variants.linked_material(chair, recs).name, "Chair")

    def test_files_in_the_root_itself(self):
        with tempfile.TemporaryDirectory() as d:
            self._write(d, ["a.abc"])
            recs, entries, _e = folders.scan_root(d)
            self.assertEqual([r.name for r in recs], ["a"])
            self.assertEqual(recs[0].category, "")
            self._write(d, ["sub/b.abc"])
            stamp = os.stat(d).st_mtime + 5              # file system clocks can be coarse
            os.utime(d, (stamp, stamp))
            recs, _entries, _e = folders.scan_root(d, entries)    # root entry is stale now
            self.assertEqual(sorted(r.name for r in recs), ["a", "b"])
            _r2, e2, _ = folders.scan_root(d)
            recs, _e3, _ = folders.scan_root(d, e2)                  # all from the cache
            self.assertEqual(sorted(r.name for r in recs), ["a", "b"])

    def test_usd_component_with_model_textures(self):
        listing = self._in(self.chair)
        self.assertEqual([r.type for r in library.browsable(listing)], ["geometry"])
        geo, = self._in(self.chair, "geometry")
        self.assertEqual(geo.category, "cat")
        self.assertEqual(sorted(geo.files), ["usd"])
        self.assertEqual((geo.info.get("component_dir") or "").lower(), self.chair.lower())
        self.assertEqual(os.path.basename(geo.thumb), "thumbnail.png")
        hidden = library.companion_keys(listing)
        mat, = self._in(self.chair, "material")
        self.assertIn(mat.key, hidden)
        self.assertIn("basecolor", mat.info["channels"])
        self.assertIs(variants.linked_material(geo, listing), mat)

    def test_geometry_groups_and_previews(self):
        geos = self._in(self.props, "geometry")
        self.assertEqual(sorted(r.name for r in geos), ["Pot", "Rock"])
        rock = next(r for r in geos if r.name == "Rock")
        pot = next(r for r in geos if r.name == "Pot")
        self.assertEqual(sorted(rock.files), ["abc", "bgeo.sc"])
        self.assertEqual(os.path.basename(rock.thumb), "Rock_preview.jpg")
        self.assertNotEqual(rock.id, pot.id)
        self.assertEqual(self._in(self.props, "material"), [])

    def test_texture_folder(self):
        mat, = self._in(self.tex, "material")
        self.assertEqual(mat.name, "Brick")
        self.assertEqual(sorted(mat.info["channels"]), ["basecolor", "normal", "roughness"])

    def test_component_category(self):
        geo, = self._in(self.lamp, "geometry")
        self.assertEqual(geo.category, "a/b")

    def test_descends_into_subfolders(self):
        names = {r.name for r in self.records}
        self.assertTrue({"Tree", "Bush"} <= names)

    def test_second_scan_reuses_entries(self):
        again, _entries, errors = folders.scan_root(self.root, self.entries)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(r.key for r in again), sorted(r.key for r in self.records))

    def test_detection(self):
        self.assertTrue(folders.looks_like_library(self.root))
        lib = os.path.join(self.tmp.name, "lib")
        make_asset(lib, "geometry", "rocks", "Boulder")
        self.assertFalse(folders.looks_like_library(lib))


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

    def test_asset_meta_tags(self):
        scanned = {"tool": "x"}
        rec = library.AssetRecord(id="folder:rock:geometry", name="Rock",
                                  source_kind="folder", tags=["scanned"],
                                  description="from disk", info=scanned)
        meta = {"folder:rock:geometry": {"tags": ["pool"], "description": ""}}
        library.apply_meta([rec], meta)
        self.assertEqual(rec.tags, ["pool"])               # the entry replaces scanned tags
        self.assertEqual(rec.description, "")              # a cleared description stays cleared
        self.assertTrue(library.Query("tag:pool").matches(rec))
        self.assertEqual(scanned, {"tool": "x"})            # the scan thread's dict is untouched
        library.apply_meta([rec], {"folder:rock:geometry": {"tags": ["scanned", "pool"],
                                                           "description": "wet"}})
        self.assertEqual(rec.tags, ["scanned", "pool"])
        self.assertEqual(rec.description, "wet")
        library.apply_meta([rec], {"folder:rock:geometry": {"tags": [], "description": ""}})
        self.assertEqual(rec.tags, [])                      # scanned tag removed
        library.apply_meta([rec], {})
        self.assertEqual(rec.tags, ["scanned"])             # no entry: scanned values again
        self.assertEqual(rec.description, "from disk")
        self.assertFalse(library.Query("tag:pool").matches(rec))

    def test_apply_meta_skips_library_assets(self):
        rec = library.AssetRecord(id="6f1c-uuid", name="Rock", source_kind="megascans",
                                  tags=["a"], info={})
        library.apply_meta([rec], {"6f1c-uuid": {"tags": ["x"], "description": ""}})
        self.assertEqual(rec.tags, ["a"])
        self.assertNotIn("base_tags", rec.info)

    def test_root_and_kind_fields(self):
        recs = [library.AssetRecord(id="megascans:a:geometry", name="A",
                                    root="D:/libs/MegaScanLib", source_kind="megascans"),
                library.AssetRecord(id="uuid-b", name="B", root="D:/libs/MyAssets",
                                    source_kind=""),
                library.AssetRecord(id="folder:c:geometry", name="C",
                                    root="D:/libs/WatchProj", source_kind="folder"),
                # ingested from Megascans into a library: a library asset
                library.AssetRecord(id="uuid-d", name="D", root="D:/libs/MyAssets",
                                    source_kind="megascans")]

        def names(text):
            q = library.Query(text)
            return [r.name for r in recs if q.matches(r)]

        self.assertEqual(names("root:scan"), ["A"])
        self.assertEqual(names("kind:folder"), ["C"])
        self.assertEqual(names("kind:library"), ["B", "D"])
        self.assertEqual(names("kind:megascans"), ["A"])
        self.assertEqual(names("kind:gsg"), [])


class LabelTests(unittest.TestCase):
    def test_set_and_clear(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            s = prefs.Prefs(path)
            prefs.set_color_label(s, ["a", "b"], "red")
            self.assertEqual(prefs.color_labels(s), {"a": "red", "b": "red"})
            prefs.set_color_label(s, ["a"], "")
            self.assertEqual(prefs.color_labels(s), {"b": "red"})
            with self.assertRaises(ValueError):
                prefs.set_color_label(s, ["a"], "magenta")
            self.assertEqual(prefs.color_labels(prefs.Prefs(path)), {"b": "red"})

    def test_query_and_sort(self):
        recs = [library.AssetRecord(id="a", name="Zulu"),
                library.AssetRecord(id="b", name="Alpha"),
                library.AssetRecord(id="c", name="Mike")]
        labels = {"a": "red", "b": "green"}
        names = lambda q: [r.name for r in recs if q.matches(r)]  # noqa: E731
        self.assertEqual(names(library.Query("label:red", labels=labels)), ["Zulu"])
        self.assertEqual(names(library.Query("label:any", labels=labels)), ["Zulu", "Alpha"])
        self.assertEqual(names(library.Query("label:none", labels=labels)), ["Mike"])
        self.assertEqual(names(library.Query("-label:red", labels=labels)), ["Alpha", "Mike"])
        self.assertEqual([r.name for r in library.sort_records(recs, "label", labels)],
                         ["Zulu", "Alpha", "Mike"])


class PrefsTests(unittest.TestCase):
    def test_asset_meta(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            s = prefs.Prefs(path)
            prefs.set_asset_meta(s, "folder:rock:geometry", ["Pool", "pool", " "], " wet rock ")
            self.assertEqual(prefs.asset_meta(s),
                             {"folder:rock:geometry": {"tags": ["pool"],
                                                       "description": "wet rock"}})
            prefs.set_asset_meta(s, "folder:rock:geometry", [], "")   # cleared: an override
            self.assertEqual(prefs.asset_meta(prefs.Prefs(path)),
                             {"folder:rock:geometry": {"tags": [], "description": ""}})
            prefs.set_asset_meta(s, "folder:rock:geometry", ["Scanned"], "",
                                 base_tags=["scanned"], base_description="")
            self.assertEqual(prefs.asset_meta(prefs.Prefs(path)), {})  # same as scanned

    def test_smart_searches(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            s = prefs.Prefs(path)
            prefs.save_smart_search(s, "Trees", "cat:trees")
            prefs.save_smart_search(s, "Rocks", "tag:rock")
            self.assertEqual([x["name"] for x in prefs.smart_searches(s)], ["Trees", "Rocks"])
            prefs.save_smart_search(s, "trees", "type:geo")      # same name: replaced
            self.assertEqual(prefs.smart_searches(prefs.Prefs(path)),
                             [{"name": "Rocks", "query": "tag:rock"},
                              {"name": "trees", "query": "type:geo"}])
            prefs.remove_smart_search(s, "ROCKS")
            self.assertEqual(prefs.smart_searches(s),
                             [{"name": "trees", "query": "type:geo"}])

    def test_bad_asset_meta_entries_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            with open(path, "w") as f:
                json.dump({"asset_meta": {"good": {"tags": ["A"], "description": ""},
                                          "empty": {"tags": []}, "bad": "x", "": {}}}, f)
            self.assertEqual(prefs.asset_meta(prefs.Prefs(path)),
                             {"good": {"tags": ["a"], "description": ""},
                              "empty": {"tags": [], "description": ""}})

    def test_roots_and_parm_path(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ[prefs.ENV_VAR] = d
            try:
                p = prefs.Prefs(os.path.join(d, "p.json"))
                self.assertEqual(p.roots(), [])             # no $AX_ASSET_LIB fallback
                p.set("roots", [{"path": "$" + prefs.ENV_VAR, "read_only": True}])
                roots = p.roots()
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

    def test_check_var(self):
        self.assertEqual(prefs.check_var(""), "")
        self.assertEqual(prefs.check_var("MEGASCANS"), "")
        for bad in ("HIP", "HOUDINI_PATH", "1A", "A-B"):
            self.assertNotEqual(prefs.check_var(bad), "", bad)

    def test_apply_root_vars(self):
        with tempfile.TemporaryDirectory() as d:
            p = prefs.Prefs(os.path.join(d, "p.json"))
            p.set("roots", [{"var": "megascans", "path": d, "read_only": False}], save=False)
            applied = prefs.apply_root_vars(p)
            try:
                self.assertEqual(applied, {"MEGASCANS": os.path.normpath(d)})
                self.assertEqual(os.environ["MEGASCANS"], os.path.normpath(d))
                root = p.roots()[0]
                self.assertEqual(root["var"], "MEGASCANS")
                f = os.path.join(d, "3d", "x.fbx")
                self.assertEqual(prefs.parm_path(f, root, "token"), "$MEGASCANS/3d/x.fbx")
            finally:
                os.environ.pop("MEGASCANS", None)

    def test_bad_types_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            with open(path, "w") as f:
                json.dump({"thumb_size": "big", "sort": "added", "unknown": 1}, f)
            p = prefs.Prefs(path)
            self.assertEqual(p.get("thumb_size"), prefs.DEFAULTS["thumb_size"])
            self.assertEqual(p.get("sort"), "added")


class ResourcesTests(unittest.TestCase):
    def test_fmt_bytes(self):
        from ax_asset_library import resources
        self.assertEqual(resources.fmt_bytes(512), "512 B")
        self.assertEqual(resources.fmt_bytes(1536), "1.5 KB")
        self.assertEqual(resources.fmt_bytes(5 * 1024 ** 2), "5.0 MB")

    def test_disk_usage_and_clear_index(self):
        from ax_asset_library import resources
        with tempfile.TemporaryDirectory() as tmp:
            for rel, size in (("index_a.json", 10), ("index_b.json", 5),
                              ("megascans_thumbs/x.jpg", 7), ("previews/y.jpg", 3),
                              ("prefs.json", 99)):
                path = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(b"x" * size)
            usage = resources.disk_usage(tmp)
            self.assertEqual(usage["scan index"], (2, 15))
            self.assertEqual(usage["rendered thumbnails"], (1, 7))
            self.assertEqual(usage["image previews"], (1, 3))
            lines = resources.report_lines(tmp, {"images": 2, "image_bytes": 2048,
                                                 "pixmaps": 1, "pixmap_bytes": 1024,
                                                 "limit": 1 << 30, "pending": 0,
                                                 "hits": 3, "misses": 1}, records=4)
            self.assertTrue(any(l.startswith("Records") and l.endswith("4") for l in lines))
            self.assertTrue(any("= 75%" in l for l in lines))
            resources.clear_index(tmp)
            self.assertEqual(resources.disk_usage(tmp)["scan index"], (0, 0))
            self.assertTrue(os.path.isfile(os.path.join(tmp, "prefs.json")))

    def test_process_memory_is_a_number_or_none(self):
        from ax_asset_library import resources
        mem = resources.process_memory()
        self.assertTrue(mem is None or mem > 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
