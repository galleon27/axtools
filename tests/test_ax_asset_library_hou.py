"""Import-context tests for AX Asset Library. Run under hython; skipped under plain Python."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "python3.13libs"))

try:
    import hou
except ImportError:
    hou = None

if hou is not None:
    from ax_asset_library import (importers, library, materials, prefs, publish,  # noqa: E402
                                  schema, thumbnails, variants)

_QT_APP = None


def qt_app():
    """One QApplication for the whole run (images and the offscreen panel tests)."""
    global _QT_APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = _QT_APP = QtWidgets.QApplication([])
    return app


def write_png(path, blocks=False, size=(64, 64), rects=None):
    """A small PNG: grey, or white rectangles on black for tracing (`rects`: x, y, w, h)."""
    qt_app()
    from PySide6 import QtGui
    img = QtGui.QImage(size[0], size[1], QtGui.QImage.Format.Format_RGBA8888)
    img.fill(QtGui.QColor(0, 0, 0, 255) if blocks else QtGui.QColor(128, 128, 128, 255))
    if blocks:
        painter = QtGui.QPainter(img)
        for x, y, w, h in rects or ((8, 8, 20, 40), (36, 16, 20, 30)):
            painter.fillRect(x, y, w, h, QtGui.QColor(255, 255, 255, 255))
        painter.end()
    img.save(path, "PNG")


@unittest.skipIf(hou is None, "needs hython")
class ImportContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root_dir = os.path.join(self.tmp.name, "library")
        os.makedirs(root_dir)
        self.settings = prefs.Prefs(os.path.join(self.tmp.name, "prefs.json"))
        self.settings.set("roots", [{"path": root_dir, "read_only": False}], save=False)
        root = self.settings.roots()[0]

        hou.hipFile.clear(suppress_save_prompt=True)

        from PySide6 import QtGui
        qt_app()   # QImage needs a GUI application; a QApplication so panel tests can follow
        src = os.path.join(self.tmp.name, "src")
        os.makedirs(src)
        srcs = []
        for name, color in (("Brick_Albedo", (170, 90, 60)),
                            ("Brick_Roughness", (128, 128, 128)),
                            ("Brick_Normal", (128, 128, 255))):
            path = os.path.join(src, name + ".png")
            img = QtGui.QImage(8, 8, QtGui.QImage.Format.Format_RGBA8888)
            img.fill(QtGui.QColor(*color))
            img.save(path, "PNG")
            srcs.append(path)
        report = publish.ingest(publish.plan_ingest(srcs), root, "test")
        self.assertFalse(report.failed, report.failed)

        geo = hou.node("/obj").createNode("geo", "src_geo")
        box = geo.createNode("box", "box1")
        box_path = os.path.join(self.tmp.name, "box.bgeo.sc")
        box.geometry().saveToFile(box_path)
        report = publish.ingest(publish.plan_ingest([box_path]), root, "test")
        self.assertFalse(report.failed, report.failed)

        cache = os.path.join(self.tmp.name, "cache")
        os.makedirs(cache)
        records, errors = library.scan_roots([root_dir], cache)
        self.assertFalse(errors, errors)
        self.material = next(r for r in records if r.type == "material")
        self.geometry = next(r for r in records if r.type == "geometry")

    def _matnet(self):
        geo = hou.node("/obj").createNode("geo", "geo1")
        return geo.createNode("matnet", "matnet1")

    def _import_material(self, network, renderer="karma", position=(4, 2)):
        return importers.import_records([self.material], self.settings, renderer=renderer,
                                        target=(None, network),
                                        position=hou.Vector2(*position))

    def test_matnet_in_sop(self):
        matnet = self._matnet()
        nodes, warnings = self._import_material(matnet)
        self.assertFalse(warnings)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].parent(), matnet)
        self.assertEqual(tuple(nodes[0].position()), (4.0, 2.0))

    def test_inside_builder_goes_up(self):
        matnet = self._matnet()
        first, warnings = self._import_material(matnet)
        self.assertFalse(warnings)
        nodes, warnings = self._import_material(first[0], position=(1, 1))
        self.assertFalse(warnings)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].parent(), matnet)

    def test_mat_context(self):
        mat = hou.node("/mat")
        nodes, warnings = self._import_material(mat)
        self.assertFalse(warnings)
        self.assertEqual(nodes[0].parent(), mat)
        self.assertEqual(tuple(nodes[0].position()), (4.0, 2.0))

    def test_material_library_inside(self):
        stage = hou.node("/stage")
        lib = stage.createNode("materiallibrary", "matlib1")
        nodes, warnings = self._import_material(lib)
        self.assertFalse(warnings)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].parent(), lib)
        if "redshift" in [k for k, _ in materials.available_renderers()]:
            nodes, warnings = self._import_material(lib, renderer="redshift")
            self.assertFalse(warnings)
            self.assertEqual(nodes[0].type().name(), "rs_usd_material_builder")

    def test_other_context_goes_to_mat(self):
        nodes, warnings = self._import_material(hou.node("/out"))
        self.assertFalse(warnings)
        self.assertEqual(nodes[0].parent(), hou.node("/mat"))

    def test_lop_unchanged(self):
        nodes, warnings = self._import_material(hou.node("/stage"))
        self.assertFalse(warnings)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].type().name(), "materiallibrary")

    def test_geometry_into_matnet_refused(self):
        matnet = self._matnet()
        with self.assertRaises(importers.AssetImportError) as caught:
            importers.import_records([self.geometry], self.settings, target=(None, matnet))
        self.assertIn("SOP, OBJ or LOP", str(caught.exception))

    def _locked_hda_sop(self):
        """A SOP-level HDA in the temp dir with its contents locked: the network editor
        showing its inside has pwd() == this node."""
        geo = hou.node("/obj").createNode("geo", "hda_host")
        sub = geo.createNode("subnet", "locked_tool")
        sub.createNode("null", "inner")
        hda_file = os.path.join(self.tmp.name, "ax_test_locked.hda")
        node = sub.createDigitalAsset(name="ax_test_locked_tool", hda_file_name=hda_file,
                                      description="AX test locked tool")
        node.matchCurrentDefinition()      # contents locked
        self.assertTrue(importers.is_locked(node))
        return node

    def test_material_from_locked_hda_goes_to_mat(self):
        node = self._locked_hda_sop()
        nodes, warnings = self._import_material(node)
        self.assertFalse(warnings)
        self.assertEqual(nodes[0].parent(), hou.node("/mat"))

    def test_geometry_into_locked_hda_refused(self):
        node = self._locked_hda_sop()
        with self.assertRaises(importers.AssetImportError) as caught:
            importers.import_records([self.geometry], self.settings, target=(None, node))
        self.assertIn("locked HDA", str(caught.exception))

    def test_gsg_batch_import(self):
        from tests.test_ax_asset_library import make_gsg_library
        gsg_root = make_gsg_library(os.path.join(self.tmp.name, "Greyscalegorilla"))
        plan = publish.plan_ingest([gsg_root])
        self.assertEqual(plan.gsg, 5)
        self.assertEqual(plan.counts(), {"hdri": 1, "material": 3, "image": 1, "geometry": 1})
        lib = os.path.join(self.tmp.name, "gsg_lib")
        root = {"path": lib, "expanded": lib, "read_only": False}
        report = publish.ingest(plan, root, "gsg", copy=False, read_info=False)
        self.assertFalse(report.failed, report.failed)
        self.assertEqual(len(report.created), 6)
        recs, errors = library.scan_roots([lib], os.path.join(self.tmp.name, "gsg_cache"))
        self.assertFalse(errors, errors)
        cork = next(r for r in recs if r.name == "Cork_Chips_03_MC036_A001")
        self.assertEqual(cork.type, "material")
        self.assertEqual(cork.category, "gsg")
        data = json.load(open(cork.sidecar, encoding="utf-8"))
        self.assertEqual(data["source"]["kind"], "gsg")
        self.assertEqual(data["source"]["gsg_id"], "MC036_A001")
        cork_item = next(i for i in plan.items if i.name == "Cork_Chips_03_MC036_A001")
        self.assertTrue(cork_item.thumb.endswith("GSG_MC036_A001_Cork_preview.jpg"))
        # (the fixture's preview files are empty, so no thumbnail image is written here)

    def test_mixed_into_matnet(self):
        matnet = self._matnet()
        nodes, warnings = importers.import_records(
            [self.geometry, self.material], self.settings, renderer="karma",
            target=(None, matnet), position=hou.Vector2(4, 2))
        self.assertEqual(len(nodes), 1)
        self.assertTrue(any("geometry" in w for w in warnings), warnings)


@unittest.skipIf(hou is None, "needs hython")
def report_info(plan):
    """Union of the info dicts of a plan's items."""
    out = {}
    for item in plan.items:
        out.update(item.info)
    return out


class ImportFeatureTests(unittest.TestCase):
    """Models with material/LOD/resolution, atlas tracing, texture nodes, root variables."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = prefs.Prefs(os.path.join(self.tmp.name, "prefs.json"))
        hou.hipFile.clear(suppress_save_prompt=True)
        self.cache = os.path.join(self.tmp.name, "cache")
        os.makedirs(self.cache)

        self.dl = os.path.join(self.tmp.name, "Downloaded")
        folder = os.path.join(self.dl, "3d", "tree_testid")
        os.makedirs(folder)
        with open(os.path.join(folder, "testid.json"), "w") as f:
            json.dump({"id": "testid", "name": "Test Tree", "tags": [], "categories": ["3d"],
                       "meta": [{"key": "height", "value": "2m"}]}, f)
        for lod in (0, 2):
            geo = hou.node("/obj").createNode("geo", "lod%d" % lod)
            geo.createNode("box", "box1").geometry().saveToFile(
                os.path.join(folder, "testid_LOD%d.bgeo.sc" % lod))
            geo.destroy()
        for res in ("2K", "4K"):
            for channel in ("Albedo", "Normal", "Roughness"):
                write_png(os.path.join(folder, "testid_%s_%s.png" % (res, channel)))
        self.settings.set("roots", [{"path": self.dl, "read_only": False,
                                     "kind": "megascans"}], save=False)
        self.records, errors = library.scan_roots([(self.dl, "megascans")], self.cache,
                                                  use_cache=False)
        self.assertFalse(errors, errors)

    def _rec(self, asset_type):
        return next(r for r in self.records if r.type == asset_type)

    def test_thumbnail_material_maps(self):
        from ax_asset_library import publish
        geo = self._rec("geometry")
        maps = publish.linked_material_maps(geo, self.records)
        self.assertEqual(set(maps), {"basecolor", "normal", "roughness"})
        self.assertTrue(all("_2K_" in os.path.basename(p) for p in maps.values()), maps)
        self.assertEqual(publish.linked_material_maps(geo, []), {})
        files = dict(maps, displacement=maps["basecolor"],
                     metallic=os.path.join(self.tmp.name, "missing.png"))
        self.assertEqual(set(publish._thumb_maps(files)), {"basecolor", "normal", "roughness"})
        # The material is hidden from browsing but stays linked for imports.
        self.assertEqual([r.type for r in library.browsable(self.records)], ["geometry"])

    def test_publish_and_import_materials_bundle(self):
        from ax_asset_library import material_bundle
        tex_dir = os.path.join(self.tmp.name, "bundle_tex")
        os.makedirs(tex_dir)
        png = os.path.join(tex_dir, "wood_basecolor.png")
        write_png(png)

        hou.hipFile.clear(suppress_save_prompt=True)
        shader = hou.node("/mat").createNode("principledshader::2.0", "woodmat")
        shader.parm("basecolor_texture").set(png)
        src = hou.node("/obj").createNode("geo", "bundle_src")
        box = src.createNode("box", "box1")
        assign = src.createNode("material", "assign")
        assign.parm("num_materials").set(1)
        assign.parm("shop_materialpath1").set(assign.relativePathTo(shader))
        assign.setInput(0, box)

        root_dir = os.path.join(self.tmp.name, "bundle_lib")
        os.makedirs(root_dir)
        self.settings.set("roots", [{"path": root_dir, "read_only": False}], save=False)
        root = self.settings.roots()[0]
        result = publish.publish_node(assign, root, "BundleRock", formats=(),
                                      materials=True, thumbnail_image=None)
        self.assertFalse(result.warnings, result.warnings)
        vdir = os.path.join(result.asset_dir, "v001")
        self.assertTrue(os.path.isfile(os.path.join(vdir, material_bundle.BUNDLE_FILE)))
        self.assertTrue(os.path.isfile(os.path.join(vdir, material_bundle.TEXTURE_DIR,
                                                    "wood_basecolor.png")))
        data = schema.load(os.path.join(result.asset_dir, schema.SIDECAR))
        self.assertEqual(data["versions"][0]["materials"]["names"], ["woodmat"])

        record, = library.scan_roots([root_dir], os.path.join(self.tmp.name, "cache_bundle"),
                                     use_cache=False)[0]
        self.assertIn("materials", record.version(1))
        dst = hou.node("/obj").createNode("geo", "bundle_dst")
        nodes, warnings = importers.import_records([record], self.settings,
                                                   target=(None, dst))
        self.assertFalse(warnings, warnings)
        mnode = nodes[-1]
        self.assertEqual(mnode.type().name(), "material")
        subnet = dst.node("materials").node("BundleRock_materials")
        self.assertIsNotNone(subnet)
        loaded = subnet.node("woodmat")
        self.assertIsNotNone(loaded)
        texture = loaded.parm("basecolor_texture")
        self.assertNotIn(material_bundle.PLACEHOLDER, texture.unexpandedString())
        self.assertTrue(os.path.normcase(os.path.abspath(texture.eval())).startswith(
            os.path.normcase(os.path.abspath(result.asset_dir))))
        self.assertEqual(mnode.parm("group1").eval(), '@shop_materialpath="woodmat"')
        mnode.cook(force=True)
        values = mnode.geometry().primStringAttribValues("shop_materialpath")
        self.assertEqual(len(values), 6)                      # every box prim
        self.assertEqual(set(values), {loaded.path()})

    def _publish_bundle_src(self, name, set_detail=False):
        """Publish a box assigned to a Principled Shader that uses one texture twice."""
        tex_dir = os.path.join(self.tmp.name, name + "_tex")
        os.makedirs(tex_dir)
        png = os.path.join(tex_dir, "wood_basecolor.png")
        write_png(png)
        hou.hipFile.clear(suppress_save_prompt=True)
        shader = hou.node("/mat").createNode("principledshader::2.0", "woodmat")
        shader.parm("basecolor_texture").set(png)
        shader.parm("rough_texture").set(png)
        src = hou.node("/obj").createNode("geo", name + "_src")
        box = src.createNode("box", "box1")
        if set_detail:
            last = src.createNode("attribwrangle", "detail_assign")
            last.parm("class").set(0)
            last.parm("snippet").set('s@shop_materialpath = "%s";' % shader.path())
            last.setInput(0, box)
        else:
            last = src.createNode("material", "assign")
            last.parm("num_materials").set(1)
            last.parm("shop_materialpath1").set(last.relativePathTo(shader))
            last.setInput(0, box)
        root_dir = os.path.join(self.tmp.name, name + "_lib")
        os.makedirs(root_dir)
        self.settings.set("roots", [{"path": root_dir, "read_only": False}], save=False)
        root = self.settings.roots()[0]
        result = publish.publish_node(last, root, name, formats=(), materials=True,
                                      thumbnail_image=None)
        return result, root_dir

    def test_bundle_copies_a_shared_texture_once(self):
        result, _root_dir = self._publish_bundle_src("Shared")
        tex = os.listdir(os.path.join(result.asset_dir, "v001", "textures"))
        self.assertEqual(tex, ["wood_basecolor.png"])
        data = schema.load(os.path.join(result.asset_dir, schema.SIDECAR))
        self.assertEqual(data["versions"][0]["materials"]["textures"],
                         ["v001/textures/wood_basecolor.png"])

    def test_bundle_detail_material_assignment(self):
        from ax_asset_library import material_bundle
        result, root_dir = self._publish_bundle_src("DetailRock", set_detail=True)
        self.assertFalse(result.warnings, result.warnings)
        record, = library.scan_roots([root_dir], os.path.join(self.tmp.name, "cache_detail"),
                                     use_cache=False)[0]
        dst = hou.node("/obj").createNode("geo", "detail_dst")
        nodes, _warnings = importers.import_records([record], self.settings, target=(None, dst))
        mnode = nodes[-1]
        mnode.cook(force=True)
        values = mnode.geometry().primStringAttribValues(material_bundle.MATERIAL_ATTR)
        self.assertEqual(len(values), 6)
        self.assertTrue(all(v.endswith("woodmat") for v in values), values)

    def test_publish_without_materials_has_no_warning(self):
        hou.hipFile.clear(suppress_save_prompt=True)
        src = hou.node("/obj").createNode("geo", "plain_src")
        box = src.createNode("box", "box1")
        root_dir = os.path.join(self.tmp.name, "plain_lib")
        os.makedirs(root_dir)
        self.settings.set("roots", [{"path": root_dir, "read_only": False}], save=False)
        result = publish.publish_node(box, self.settings.roots()[0], "PlainBox", formats=(),
                                      materials=True, thumbnail_image=None)
        self.assertEqual(result.warnings, [])

    def test_add_watch_folder_component_to_library(self):
        watch_root = os.path.join(self.tmp.name, "watch")
        chair = os.path.join(watch_root, "cat", "Chair")
        os.makedirs(os.path.join(chair, "maps"))
        for name in ("Chair.usd", "geo.usd", "mtl.usd",
                     "maps/Chair_basecolor.png", "maps/Chair_roughness.png"):
            open(os.path.join(chair, *name.split("/")), "w").close()
        records, errors = library.scan_roots(
            [(watch_root, "folder")], os.path.join(self.tmp.name, "cache2"), use_cache=False)
        self.assertFalse(errors, errors)
        geo = next(r for r in records if r.type == "geometry")
        self.assertEqual(geo.source_kind, "folder")

        root_dir = os.path.join(self.tmp.name, "lib2")
        os.makedirs(root_dir)
        root = {"path": root_dir, "expanded": root_dir, "read_only": False, "var": "",
                "kind": "library", "megascans": False}
        plan = publish.plan_from_records([geo], records)
        self.assertEqual(sorted(i.type for i in plan.items), ["geometry", "material"])
        report = publish.ingest(plan, root, copy=True, read_info=False)
        self.assertFalse(report.failed, report.failed)
        self.assertEqual(len(report.created), 2)
        gdir = os.path.join(root_dir, "geometry", "cat", "Chair", "v001")
        self.assertTrue(os.path.isfile(os.path.join(gdir, "geo.usd")))       # sublayer copied
        self.assertTrue(os.path.isfile(os.path.join(gdir, "maps",
                                                    "Chair_basecolor.png")))
        lib_records, errors = library.scan_roots(
            [root_dir], os.path.join(self.tmp.name, "cache3"), use_cache=False)
        self.assertFalse(errors, errors)
        geos = [r for r in lib_records if r.type == "geometry"]
        self.assertEqual(len(geos), 1)
        self.assertIsNotNone(variants.linked_material(geos[0], lib_records))
        # The library copy keeps source.kind "folder" but is not browsed in place: it must not
        # be offered for "Add to library" again.
        self.assertEqual(geos[0].source_kind, "folder")
        self.assertFalse(library.browsed_in_place(geos[0]))
        self.assertEqual(publish.plan_from_records(geos, lib_records).items, [])
        self.assertNotIn("component_dir", report_info(plan))

    def test_model_with_material_sop(self):
        geo = hou.node("/obj").createNode("geo", "geo1")
        nodes, warnings = importers.import_records(
            [self._rec("geometry")], self.settings, renderer="karma", target=(None, geo),
            lod=2, resolution="2K", records_all=self.records)
        self.assertFalse(warnings, warnings)
        self.assertEqual(nodes[-1].type().name(), "material")
        loader = next(n for n in geo.children() if n.type().name() == "file")
        self.assertTrue(loader.parm("file").eval().endswith("_LOD2.bgeo.sc"))
        mat = nodes[-1].node(nodes[-1].parm("shop_materialpath1").eval())
        self.assertIsNotNone(mat)
        self.assertEqual(mat.parent(), geo.node("materials"))
        self.assertIn("_2K_", mat.node("basecolor").parm("file").eval())

    def test_model_material_lop(self):
        from pxr import UsdGeom, UsdShade
        stage = hou.node("/stage")
        nodes, warnings = importers.import_records(
            [self._rec("geometry")], self.settings, renderer="karma", target=(None, stage),
            lod=2, resolution="2K", records_all=self.records)
        self.assertFalse(warnings, warnings)
        lib = nodes[-1]
        self.assertEqual(lib.type().name(), "materiallibrary")
        self.assertEqual(lib.parm("assign1").eval(), 1)
        lib.setDisplayFlag(True)
        lib.cook(force=True)
        prim = "/assets/" + schema.sanitize_name(self._rec("geometry").name)
        usd = lib.stage()
        mesh = next((p for p in usd.Traverse()
                     if p.IsA(UsdGeom.Mesh) and p.GetPath().pathString.startswith(prim)), None)
        self.assertIsNotNone(mesh)
        bound, _rel = UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial()
        self.assertTrue(bound.GetPath().pathString.startswith("/materials/"),
                        bound.GetPath().pathString)

    def test_atlas_traced(self):
        tex = os.path.join(self.tmp.name, "atlas")
        os.makedirs(tex)
        opacity = os.path.join(tex, "leaves_opacity.png")
        base = os.path.join(tex, "leaves_basecolor.png")
        write_png(opacity, blocks=True)
        write_png(base)
        atlas = library.AssetRecord(
            key="atlas1", id="atlas1", name="Leaves", type="material", root=self.tmp.name,
            current=1, versions=[1],
            files={"opacity": opacity, "basecolor": base},
            info={"vendor_kind": "atlas", "megascans_size_m": 2.0})
        geo = hou.node("/obj").createNode("geo", "geo1")
        nodes, warnings = importers.import_records(
            [atlas], self.settings, renderer="karma", target=(None, geo),
            as_geometry=True, records_all=[atlas])
        self.assertFalse(warnings, warnings)
        last = nodes[-1]
        self.assertEqual(last.type().name(), "material")
        splitter = next(n for n in geo.children() if n.type().name() == "AX_atlas_splitter")
        self.assertTrue(splitter.parm("pack").eval())
        splitter.parm("dilate").set(0)     # default dilation would bridge the two blocks
        splitter.parm("step").set(1)       # the small test blocks need a fine trace step
        last.cook(force=True)
        data = last.geometry()
        packed = [p for p in data.prims() if "Packed" in str(p.type())]
        self.assertEqual(len(packed), 2)
        self.assertEqual({p.attribValue("name") for p in data.prims()}, {"piece0", "piece1"})
        self.assertIsNotNone(data.findPrimAttrib("shop_materialpath"))
        parm = last.parm("shop_materialpath1")
        self.assertTrue(parm.unexpandedString().startswith("../materials/"),
                        parm.unexpandedString())
        self.assertEqual(last.node(parm.eval()).parent(), geo.node("materials"))

    def test_atlas_uv_non_square(self):
        """The splitter keeps the image UVs for any aspect ratio."""
        tex = os.path.join(self.tmp.name, "wide")
        os.makedirs(tex)
        opacity = os.path.join(tex, "wide_opacity.png")
        write_png(opacity, blocks=True, size=(128, 64), rects=[(8, 8, 20, 20)])
        atlas = library.AssetRecord(
            key="wide", id="wide", name="Wide", type="material", root=self.tmp.name,
            current=1, versions=[1], files={"opacity": opacity}, info={"vendor_kind": "atlas"})
        geo = hou.node("/obj").createNode("geo", "geo1")
        nodes, warnings = importers.import_records(
            [atlas], self.settings, renderer="none", target=(None, geo), as_geometry=True)
        self.assertFalse(warnings, warnings)
        split = nodes[-1]
        self.assertEqual(split.type().name(), "AX_atlas_splitter")
        split.parm("pack").set(0)
        split.parm("dilate").set(0)
        split.parm("step").set(1)
        split.cook(force=True)
        data = split.geometry()
        names = {p.attribValue("name") for p in data.prims()} \
            if data.findPrimAttrib("name") else set()
        self.assertEqual(len(names), 1)
        if data.findVertexAttrib("uv") is not None:
            uvs = [v.attribValue("uv") for pr in data.prims() for v in pr.vertices()]
        else:
            uvs = [pt.attribValue("uv") for pt in data.points()]
        us = [uv[0] for uv in uvs]
        # Block at pixels x 8..28 of 128.
        self.assertAlmostEqual(min(us), 8 / 128, delta=0.03)
        self.assertAlmostEqual(max(us), 28 / 128, delta=0.03)

    def test_update_keeps_lod_and_resolution(self):
        from ax_asset_library import scene
        geo = hou.node("/obj").createNode("geo", "geo1")
        nodes, warnings = importers.import_records(
            [self._rec("geometry")], self.settings, renderer="karma", target=(None, geo),
            lod=2, resolution="2K", records_all=self.records)
        self.assertFalse(warnings, warnings)
        loader = next(n for n in geo.children() if n.type().name() == "file")
        _n, warnings = scene.update_nodes(self._rec("geometry"), [loader], self.settings, "keep")
        self.assertFalse(warnings, warnings)
        self.assertTrue(loader.parm("file").eval().endswith("_LOD2.bgeo.sc"),
                        loader.parm("file").eval())
        mat = nodes[-1].node(nodes[-1].parm("shop_materialpath1").eval())
        scene.update_nodes(self._rec("material"), [mat], self.settings, "keep")
        self.assertIn("_2K_", mat.node("basecolor").parm("file").eval())

    def test_image_into_mtlx_builder(self):
        import voptoolutils
        builder = voptoolutils._setupMtlXBuilderSubnet(
            destination_node=hou.node("/mat"), name="m1",
            mask=voptoolutils.KARMAMTLX_TAB_MASK, folder_label="Karma Material Builder")
        tex = os.path.join(self.tmp.name, "tex.png")
        write_png(tex)
        rec = library.AssetRecord(name="Tex", type="image", root=self.tmp.name,
                                  current=1, versions=[1], files={"image": tex})
        nodes, warnings = importers.import_records([rec], self.settings,
                                                   target=(None, builder))
        self.assertFalse(warnings, warnings)
        node = nodes[0]
        self.assertEqual(node.type().name(), "mtlximage")
        self.assertTrue(node.parm("file").eval().endswith("tex.png"))
        self.assertEqual(node.parm("filecolorspace").eval(), "srgb_texture")

        nodes, _w = importers.import_records([rec], self.settings, renderer="karma",
                                             target=(None, hou.node("/mat")))
        self.assertEqual(nodes[0].parent(), hou.node("/mat"))
        # Without a renderer at /mat, the importer's error becomes a warning (import_records
        # collects per-asset errors) and no node is created.
        nodes, warnings = importers.import_records([rec], self.settings,
                                                   target=(None, hou.node("/mat")))
        self.assertEqual(nodes, [])
        self.assertTrue(any("renderer" in w for w in warnings), warnings)

    def test_root_var_parm(self):
        lib = os.path.join(self.tmp.name, "varlib")
        os.makedirs(lib)
        self.settings.set("roots", [{"var": "AXTESTLIB", "path": lib, "read_only": False}],
                          save=False)
        prefs.apply_root_vars(self.settings)
        self.addCleanup(lambda: os.environ.pop("AXTESTLIB", None))
        src = hou.node("/obj").createNode("geo", "src")
        box = os.path.join(self.tmp.name, "var_box.bgeo.sc")
        src.createNode("box", "box1").geometry().saveToFile(box)
        report = publish.ingest(publish.plan_ingest([box]), self.settings.roots()[0], "vars")
        self.assertFalse(report.failed, report.failed)
        recs, errors = library.scan_roots([lib], self.cache, use_cache=False)
        self.assertFalse(errors, errors)
        rec = next(r for r in recs if r.type == "geometry")
        target = hou.node("/obj").createNode("geo", "geo1")
        nodes, warnings = importers.import_records([rec], self.settings, target=(None, target))
        self.assertFalse(warnings, warnings)
        parm = nodes[0].parm("file")
        self.assertTrue(parm.unexpandedString().startswith("$AXTESTLIB/"),
                        parm.unexpandedString())
        self.assertEqual(os.path.normcase(parm.eval()),
                         os.path.normcase(next(iter(rec.files.values()))))

    def test_usage_skips_deleted_nodes(self):
        from ax_asset_library import scene
        rec = self._rec("geometry")
        nodes, warnings = importers.import_records([rec], self.settings,
                                                   target=(None, hou.node("/obj")))
        self.assertFalse(warnings, warnings)
        use = scene.usage(self.records)[rec.key]
        self.assertTrue(use.nodes)
        nodes[-1].destroy()
        self.assertEqual(use.nodes, [])
        self.assertEqual(use.outdated, [])


@unittest.skipIf(hou is None, "needs hython")
class PlantVariationTests(unittest.TestCase):
    """A Megascans plant: one geometry record holding Var1..VarN, imported either merged into
    a single SOP with a `name` prim attribute or as a LOP variant set."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = prefs.Prefs(os.path.join(self.tmp.name, "prefs.json"))
        hou.hipFile.clear(suppress_save_prompt=True)
        self.cache = os.path.join(self.tmp.name, "cache")
        os.makedirs(self.cache)
        self.dl = os.path.join(self.tmp.name, "Downloaded")
        self.folder = os.path.join(self.dl, "3dplant", "plant_ptest1")
        for var, width in (("Var1", 1.0), ("Var2", 2.0)):
            os.makedirs(os.path.join(self.folder, var))
            geo = hou.node("/obj").createNode("geo", "src_" + var)
            box = geo.createNode("box", "box1")
            box.parm("sizex").set(width)
            box.geometry().saveToFile(os.path.join(self.folder, var,
                                                  "%s_LOD0.bgeo.sc" % var))
            geo.destroy()
        atlas = os.path.join(self.folder, "Textures", "Atlas")
        os.makedirs(atlas)
        for channel in ("Albedo", "Normal"):
            write_png(os.path.join(atlas, "ptest1_8K_%s.png" % channel))
        with open(os.path.join(self.folder, "ptest1.json"), "w") as f:
            json.dump({"id": "ptest1", "name": "Test Fern", "tags": [],
                       "categories": ["3dplant"],
                       "meta": [{"key": "dimensions", "value": [
                           {"variation": 1, "length": "0.26m", "width": "0.24m",
                            "height": "0.1m"},
                           {"variation": 2, "length": "0.52m", "width": "0.48m",
                            "height": "0.2m"}]}]}, f)
        self.settings.set("roots", [{"path": self.dl, "read_only": False,
                                     "kind": "megascans"}], save=False)
        self.records, errors = library.scan_roots([(self.dl, "megascans")], self.cache,
                                                  use_cache=False)
        self.assertFalse(errors, errors)

    def _plant(self):
        """The plant's single geometry record (all variations in one record)."""
        recs = [r for r in self.records if r.type == "geometry"]
        self.assertEqual(len(recs), 1)
        return recs[0]

    def test_plant_is_one_record_with_variations(self):
        rec = self._plant()
        self.assertEqual(rec.id, "megascans:ptest1:geometry")
        self.assertTrue(rec.files["bgeo.sc"].endswith("Var1_LOD0.bgeo.sc"))
        self.assertEqual(sorted(rec.variations_for()), ["Var1", "Var2"])
        self.assertTrue(rec.variations_for()["Var2"]["bgeo.sc"].endswith("Var2_LOD0.bgeo.sc"))
        self.assertEqual(next(r for r in self.records if r.type == "material")
                         .variations_for(), {})

    def test_ingest_keeps_the_variation_set(self):
        root_dir = os.path.join(self.tmp.name, "library")
        os.makedirs(root_dir)
        root = {"path": root_dir, "expanded": root_dir, "read_only": False, "var": "",
                "kind": "library", "megascans": False}
        plan = publish.plan_ingest([self.folder])
        self.assertEqual(sorted(item.type for item in plan.items), ["geometry", "material"])
        report = publish.ingest(plan, root, copy=True, read_info=False)
        self.assertFalse(report.failed, report.failed)
        recs, errors = library.scan_roots([root_dir], os.path.join(self.tmp.name, "cache2"),
                                          use_cache=False)
        self.assertFalse(errors, errors)
        rec = next(r for r in recs if r.type == "geometry")
        self.assertEqual(sorted(rec.variations_for()), ["Var1", "Var2"])
        for files in rec.variations_for().values():
            for path in files.values():
                self.assertTrue(os.path.isfile(path), path)
        data = schema.load(os.path.join(rec.asset_dir, schema.SIDECAR))
        self.assertEqual(sorted(data["versions"][0]["variations"]), ["Var1", "Var2"])
        self.assertEqual(sorted(data["versions"][0]["files"]), ["bgeo.sc"])
        self.assertEqual(sorted(os.listdir(os.path.join(rec.asset_dir, "v001"))),
                         ["Var1_LOD0.bgeo.sc", "Var2_LOD0.bgeo.sc"])
        # The library copy still imports as a variation set.
        stage = hou.node("/stage")
        nodes, warnings = importers.import_records([rec], self.settings, target=(None, stage))
        self.assertFalse(warnings, warnings)
        self.assertEqual(nodes[-1].type().name(), "setvariant")

    def test_sop_merge_with_name_attribute(self):
        rec = self._plant()
        rec.info["import_scale"] = 0.01          # bgeo has no scale: set it to exercise the node
        geo = hou.node("/obj").createNode("geo", "geo1")
        nodes, warnings = importers.import_records([rec], self.settings, target=(None, geo))
        self.assertFalse(warnings, warnings)
        merge = nodes[-1]
        self.assertEqual(merge.type().name(), "merge")
        self.assertEqual([n.type().name() for n in merge.inputs()], ["name", "name"])
        named = merge.input(0)                   # Var1's chain: loader -> scale -> pack -> name
        self.assertEqual(named.parm("attribname").eval(), "name")
        self.assertEqual(named.parm("class").eval(), 0)          # menu index 0 = Primitive
        self.assertEqual(named.parm("name1").eval(), "Var1")
        self.assertEqual(named.userData(importers.UD_VARIATION), "Var1")
        self.assertEqual(merge.input(1).parm("name1").eval(), "Var2")
        self.assertEqual(named.input(0).type().name(), "pack")
        self.assertEqual(named.input(0).input(0).type().name(), "xform")
        self.assertAlmostEqual(named.input(0).input(0).parm("scale").eval(), 0.01)
        self.assertEqual(named.input(0).input(0).input(0).type().name(), "file")
        self.assertEqual(merge.userData(importers.UD_ID), rec.id)
        merge.cook(force=True)
        widths = {prim.attribValue("name"): prim.boundingBox().sizevec().x()
                  for prim in merge.geometry().prims()}
        self.assertEqual(sorted(widths), ["Var1", "Var2"])
        self.assertAlmostEqual(widths["Var1"], 0.01, places=4)   # 1 m box, cm->m scale
        self.assertAlmostEqual(widths["Var2"], 0.02, places=4)

    def test_obj_variations(self):
        rec = self._plant()
        nodes, warnings = importers.import_records([rec], self.settings,
                                                   target=(None, hou.node("/obj")))
        self.assertFalse(warnings, warnings)
        obj = nodes[-1]
        self.assertEqual(obj.type().name(), "geo")
        self.assertEqual(obj.userData(importers.UD_ID), rec.id)
        merge = next(n for n in obj.children() if n.type().name() == "merge")
        self.assertTrue(merge.isDisplayFlagSet())
        self.assertTrue(merge.isRenderFlagSet())
        self.assertEqual(sorted(n.name() for n in obj.children() if n.type().name() == "file"),
                         ["Test_Fern_ptest1_Var1", "Test_Fern_ptest1_Var2"])

    def test_lop_variant_set(self):
        from pxr import Usd, UsdGeom
        rec = self._plant()
        stage = hou.node("/stage")
        nodes, warnings = importers.import_records([rec], self.settings, target=(None, stage))
        self.assertFalse(warnings, warnings)
        sel = nodes[-1]
        self.assertEqual(sel.type().name(), "setvariant")  # outside the subnet
        self.assertEqual(len(nodes), 1)
        sub = sel.input(0)
        self.assertEqual(sub.type().name(), "subnet")     # references and variants inside
        self.assertEqual({c.name() for c in stage.children()}, {sel.name(), sub.name()})
        add = sub.node("output0").input(0)                       # the Var2 Add Variant, last in the chain
        self.assertEqual(add.type().name(), "addvariant")
        prim = "/assets/" + schema.sanitize_name(rec.name)
        self.assertEqual(add.parm("primpath").eval(), prim)
        self.assertEqual(add.parm("variantset").eval(), importers.VARIATION_SET)
        self.assertEqual(add.parm("variantprimpath").eval(), prim)
        self.assertEqual(add.parm("variantname").eval(), "Var2")
        self.assertTrue(add.input(1).type().name().startswith("reference"))
        self.assertTrue(add.input(1).parm("filepath1").eval().endswith("Var2_LOD0.bgeo.sc"))
        first = add.input(0)                     # the Var1 Add Variant starts the chain
        self.assertEqual(first.parm("variantname").eval(), "Var1")
        self.assertTrue(first.input(1).parm("filepath1").eval().endswith("Var1_LOD0.bgeo.sc"))
        self.assertEqual(sel.parm("primpattern1").eval(), prim)
        self.assertEqual(sel.parm("variantset1").eval(), importers.VARIATION_SET)
        self.assertEqual(sel.parm("variantname1").eval(), "Var1")
        sel.setDisplayFlag(True)
        sel.cook(force=True)
        usd = sel.stage()
        usd_prim = usd.GetPrimAtPath(prim)
        self.assertTrue(usd_prim.IsValid())
        vset = usd_prim.GetVariantSets().GetVariantSet(importers.VARIATION_SET)
        self.assertEqual(sorted(vset.GetVariantNames()), ["Var1", "Var2"])
        self.assertEqual(vset.GetVariantSelection(), "Var1")

        def width(node):
            usd = node.stage()
            mesh = next(p for p in Usd.PrimRange(usd.GetPrimAtPath(prim))
                        if p.IsA(UsdGeom.Mesh))
            xs = [q[0] for q in UsdGeom.Mesh(mesh).GetPointsAttr().Get()]
            return max(xs) - min(xs)

        self.assertAlmostEqual(width(sel), 1.0, places=4)     # Var1 selected by default
        # A cooked LOP stage is read-only, so the other variation is shown by a second
        # Set Variant LOP on the same chain rather than by changing the selection.
        other = stage.createNode("setvariant", "pick_Var2")
        other.setInput(0, sub)
        other.parm("primpattern1").set(prim)
        other.parm("variantset1").set(importers.VARIATION_SET)
        other.parm("variantname1").set("Var2")
        other.cook(force=True)
        self.assertAlmostEqual(width(other), 2.0, places=4)   # switching swaps the geometry

    def test_lop_variants_keep_the_anchor_chain(self):
        rec = self._plant()
        stage = hou.node("/stage")
        anchor = stage.createNode("reference", "existing")
        anchor.setDisplayFlag(True)
        anchor.setSelected(True, clear_all_selected=True)
        nodes, warnings = importers.import_records([rec], self.settings, target=(None, stage))
        self.assertFalse(warnings, warnings)
        sel = nodes[-1]
        sub = sel.input(0)
        self.assertEqual(sub.input(0), anchor)                    # the anchor feeds the subnet
        first = sub.node("output0").input(0).input(0)            # Var1's Add Variant
        self.assertEqual(first.input(0), anchor)                 # wired through the subnet input
        self.assertTrue(sel.isDisplayFlagSet())                   # whole chain in the viewport
        self.assertFalse(anchor.isDisplayFlagSet())


@unittest.skipIf(hou is None, "needs hython")
class LoaderAndPreviewTests(unittest.TestCase):
    """USD geometry goes through the USD Import SOP; EXR/HDR previews through iconvert."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = prefs.Prefs(os.path.join(self.tmp.name, "prefs.json"))
        hou.hipFile.clear(suppress_save_prompt=True)

    def test_usd_geometry_loads_with_usdimport(self):
        path = os.path.join(self.tmp.name, "box.usda")
        with open(path, "w") as f:
            f.write('#usda 1.0\ndef Mesh "box"\n{\n'
                    "    int[] faceVertexCounts = [4]\n"
                    "    int[] faceVertexIndices = [0, 1, 2, 3]\n"
                    "    point3f[] points = [(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)]\n"
                    "}\n")
        rec = library.AssetRecord(key="usd1", id="usd:1", name="Box Usd", type="geometry",
                                  root=self.tmp.name, current=1, versions=[1],
                                  files={"usd": path})
        geo = hou.node("/obj").createNode("geo", "geo1")
        nodes, warnings = importers.import_records([rec], self.settings, target=(None, geo))
        self.assertFalse(warnings, warnings)
        node = nodes[-1]
        self.assertEqual(node.type().name(), "usdimport")
        self.assertTrue(node.parm("filepath1").eval().endswith("box.usda"))
        self.assertEqual(node.userData(importers.UD_ID), "usd:1")
        node.cook(force=True)
        self.assertGreater(len(node.geometry().prims()), 0)

    def test_preview_path_converts_exr(self):
        qt_app()
        from PySide6 import QtGui
        png = os.path.join(self.tmp.name, "chip.png")
        write_png(png)
        tool = thumbnails.find_tool("iconvert")
        self.assertIsNotNone(tool, "iconvert not found in $HFS/bin")
        exr = os.path.join(self.tmp.name, "chip.exr")
        subprocess.run([tool, png, exr], check=True)
        previews = os.path.join(self.tmp.name, "previews")
        with mock.patch.object(thumbnails, "preview_cache_dir", lambda: previews):
            self.assertEqual(thumbnails.preview_path(png), png)    # Qt reads PNG directly
            jpg = thumbnails.preview_path(exr)
            self.assertTrue(jpg.endswith(".jpg"), jpg)
            self.assertTrue(os.path.isfile(jpg))
            self.assertFalse(QtGui.QImage(jpg).isNull())
            self.assertEqual(thumbnails.preview_path(exr), jpg)    # second call: cached
            thumbnails.clear_preview_cache()
            self.assertFalse(os.path.isfile(jpg))
            self.assertEqual(thumbnails.preview_path(exr), jpg)    # rebuilt on demand
            self.assertTrue(os.path.isfile(jpg))

    def test_preview_path_retries_a_fixed_file(self):
        qt_app()
        bad = os.path.join(self.tmp.name, "bad.exr")
        with open(bad, "wb") as f:
            f.write(b"not an image")
        previews = os.path.join(self.tmp.name, "previews")
        with mock.patch.object(thumbnails, "preview_cache_dir", lambda: previews):
            with self.assertRaises(thumbnails.ThumbnailError):
                thumbnails.preview_path(bad)
            with self.assertRaises(thumbnails.ThumbnailError):
                thumbnails.preview_path(bad)           # unchanged file: not converted again
            png = os.path.join(self.tmp.name, "chip.png")
            write_png(png)
            good = os.path.join(self.tmp.name, "good.exr")
            subprocess.run([thumbnails.find_tool("iconvert"), png, good], check=True)
            shutil.copyfile(good, bad)                 # same path, new content and mtime
            self.assertTrue(os.path.isfile(thumbnails.preview_path(bad)))

    def test_display_image_caches_large_files(self):
        qt_app()
        big = os.path.join(self.tmp.name, "big.png")
        write_png(big, size=(2000, 1000))
        small = os.path.join(self.tmp.name, "small.png")
        write_png(small)
        previews = os.path.join(self.tmp.name, "previews")
        with mock.patch.object(thumbnails, "preview_cache_dir", lambda: previews):
            img = thumbnails.display_image(big, 320)
            self.assertEqual((img.width(), img.height()), (320, 160))
            self.assertEqual(len(os.listdir(previews)), 1)
            img = thumbnails.display_image(big, 320)         # second call: from the cache
            self.assertEqual((img.width(), img.height()), (320, 160))
            self.assertEqual(len(os.listdir(previews)), 1)
            self.assertEqual(thumbnails.display_image(small, 320).width(), 64)
            self.assertEqual(len(os.listdir(previews)), 1)   # small files write no copy
            self.assertTrue(thumbnails.display_image(os.path.join(self.tmp.name, "gone.png")).isNull())

    def test_display_image_new_mtime(self):
        qt_app()
        big = os.path.join(self.tmp.name, "big.png")
        write_png(big, size=(2000, 1000))
        previews = os.path.join(self.tmp.name, "previews")
        with mock.patch.object(thumbnails, "preview_cache_dir", lambda: previews):
            thumbnails.display_image(big, 320)
            stamp = os.path.getmtime(big) + 10
            os.utime(big, (stamp, stamp))
            thumbnails.display_image(big, 320)
            self.assertEqual(len(os.listdir(previews)), 2)

    def test_thumb_cache_cancel_pending(self):
        qt_app()
        from ax_asset_library.ui import grid
        cache = grid.ThumbCache()
        cache._pending.update({"a", "b"})
        cache._started.add("a")
        cache.cancel_pending()
        self.assertEqual(cache._pending, {"a"})


@unittest.skipIf(hou is None, "needs hython")
class PanelUITests(unittest.TestCase):
    """The panel offscreen, with hou.ui / hou.qt stubbed (hython has no UI)."""

    def setUp(self):
        import types
        from PySide6 import QtGui
        self.app = qt_app()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        hou.hipFile.clear(suppress_save_prompt=True)
        stubs = {
            "ui": types.SimpleNamespace(
                displayMessage=lambda *a, **k: 0, setStatusMessage=lambda *a, **k: None,
                paneTabs=lambda: [], paneTabUnderCursor=lambda: None,
                paneTabOfType=lambda *a: None),
            "qt": types.SimpleNamespace(styleSheet=lambda: "", Icon=lambda n: QtGui.QIcon()),
        }
        for name, stub in stubs.items():
            had, old = hasattr(hou, name), getattr(hou, name, None)
            setattr(hou, name, stub)
            self.addCleanup(lambda n=name, h=had, o=old: setattr(hou, n, o) if h
                            else delattr(hou, n))
        old_pref_dir = prefs.pref_dir
        prefs.pref_dir = lambda: self.tmp.name          # never touch the user's prefs
        self.addCleanup(lambda: setattr(prefs, "pref_dir", old_pref_dir))
        from ax_asset_library.ui import panel
        self.P = panel
        self.libs = []
        for i in range(2):
            lib = os.path.join(self.tmp.name, "lib%d" % i)
            os.makedirs(os.path.join(lib, "geometry"))
            self.libs.append(lib)
        self.w = panel.AssetLibraryPanel()
        self.addCleanup(self.w.close)
        self.w.settings.set("roots", [{"path": p} for p in self.libs])
        self.w.rescan()
        self.w.show()
        self._wait(lambda: not self.w._scanning)

    def _wait(self, done, timeout=10.0):
        import time
        end = time.time() + timeout
        while not done() and time.time() < end:
            self.app.processEvents()
            time.sleep(0.01)
        for _ in range(5):
            self.app.processEvents()

    def _root_rect(self, n):
        """Viewport rect of root n. No QTreeWidgetItem is kept (see panel._tree_menu)."""
        from PySide6 import QtWidgets
        it = QtWidgets.QTreeWidgetItemIterator(self.w.tree)
        rect = None
        while it.value():
            scope = it.value().data(0, self.P.ScopeRole)
            if scope and scope[0] == "scope" and scope[2] is None and \
                    os.path.normcase(scope[1]) == os.path.normcase(self.libs[n]):
                rect = self.w.tree.visualItemRect(it.value())
                break
            it += 1
        del it
        return rect

    def _menu_action(self, n, text, dialog_text=None):
        """Right-click root n and click `text` in its menu, like a user. True if found."""
        from PySide6 import QtCore, QtGui, QtWidgets
        from PySide6.QtTest import QTest
        rect = self._root_rect(n)
        self.assertIsNotNone(rect)
        seen = []

        def click():
            menu = QtWidgets.QApplication.activePopupWidget()
            if menu is None:
                return
            act = next((a for a in menu.actions() if a.text() == text), None)
            seen.append(act is not None)
            if act is None:
                menu.close()
                return
            if dialog_text is not None:
                def answer():
                    for d in self.app.topLevelWidgets():
                        if isinstance(d, QtWidgets.QInputDialog) and d.isVisible():
                            d.setTextValue(dialog_text)
                            d.accept()
                QtCore.QTimer.singleShot(50, answer)
            QTest.mouseClick(menu, QtCore.Qt.MouseButton.LeftButton,
                             pos=menu.actionGeometry(act).center())

        QtCore.QTimer.singleShot(50, click)
        event = QtGui.QContextMenuEvent(QtGui.QContextMenuEvent.Reason.Mouse, rect.center(),
                                        self.w.tree.viewport().mapToGlobal(rect.center()))
        QtWidgets.QApplication.sendEvent(self.w.tree.viewport(), event)
        self._wait(lambda: False, 0.2)
        return bool(seen and seen[0])

    def test_sidebar_menu_no_crash(self):
        # This sequence crashed Houdini: the tree was rebuilt while the menu held an item.
        for n in range(6):
            self.assertTrue(self._menu_action(n % 2, "Rescan"))
            self._wait(lambda: not self.w._scanning)
        self.assertTrue(self._menu_action(1, "Remove from library list"))
        self._wait(lambda: not self.w._scanning)
        roots = self.w.settings.get("roots")
        self.assertEqual(len(roots), 1)
        self.assertEqual(os.path.normcase(roots[0]["path"]),
                         os.path.normcase(self.libs[0]))
        self.assertTrue(self.w.isVisible())

    def test_root_shows_folder_name(self):
        texts = [self.w.tree.topLevelItem(i).text(0)
                 for i in range(self.w.tree.topLevelItemCount())]
        root_texts = [t for t in texts if t.startswith("lib")]
        self.assertEqual(len(root_texts), 2, texts)
        self.assertTrue(root_texts[0].startswith("lib0"), root_texts)
        self.assertTrue(root_texts[1].startswith("lib1"), root_texts)

    def test_material_in_sop_asks_renderer(self):
        tex = os.path.join(self.tmp.name, "rock_basecolor.png")
        write_png(tex)
        rec = library.AssetRecord(key="m", id="m", name="Rock", type="material",
                                  root=self.libs[0], current=1, versions=[1],
                                  files={"basecolor": tex})
        asked = []
        self.w._ask_renderer = lambda: asked.append(1) or "karma"
        geo = hou.node("/obj").createNode("geo", "geo1")
        nodes = self.w._import_records([rec], target=(None, geo))
        self.assertEqual(asked, [1])
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].parent(), geo.node("materials"))
