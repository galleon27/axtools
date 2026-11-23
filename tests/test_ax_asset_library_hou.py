"""Import-context tests for AX Asset Library. Run under hython; skipped under plain Python."""
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
    from ax_asset_library import importers, library, materials, prefs, publish  # noqa: E402


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
        if QtGui.QGuiApplication.instance() is None:
            self._app = QtGui.QGuiApplication([])  # QImage needs a GUI application
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

    def test_mixed_into_matnet(self):
        matnet = self._matnet()
        nodes, warnings = importers.import_records(
            [self.geometry, self.material], self.settings, renderer="karma",
            target=(None, matnet), position=hou.Vector2(4, 2))
        self.assertEqual(len(nodes), 1)
        self.assertTrue(any("geometry" in w for w in warnings), warnings)
