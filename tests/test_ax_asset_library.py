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

from ax_asset_library import library, prefs, schema  # noqa: E402


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
