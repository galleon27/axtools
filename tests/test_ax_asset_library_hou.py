"""Import-context tests for AX Asset Library. Run under hython; skipped under plain Python."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "python3.13libs"))

try:
    import hou
except ImportError:
    hou = None

if hou is not None:
    from ax_asset_library import (importers, library, materials, prefs, publish,  # noqa: E402
                                  schema)

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
