"""Geometry publication validates real level budgets before changing any OBJ."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools import export_sponza as exporter
from tools.project_levels import ProjectLevels, starter_level
from tools.compile_level import read_obj


class SponzaExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.content = self.root / "content"
        (self.content / "models").mkdir(parents=True)
        for name in ("block.obj", "guard.obj"):
            shutil.copyfile(exporter.ROOT / "content/models" / name, self.content / "models" / name)
        guard = self.content / "assets/guard"
        guard.mkdir(parents=True)
        (guard / "pack.json").write_text(json.dumps({"version": 1,
            "assets": [{"id": "mesh-guard", "uri": "models/guard.obj"}],
            "materials": [{"id": "mat-guard", "color": [1, 1, 1, 1]}],
            "prefabs": [{"id": "guard-watchman", "model": "mesh-guard", "material": "mat-guard", "scale": [1, 1, 1]}]}))
        scene = starter_level("Sponza test")
        # Keep this test independent from textures and Blender. The production
        # roundtrip separately exercises saved UV/custom-normal data.
        (self.content / "sponza_courtyard.json").write_text(json.dumps(scene))
        self.project = ProjectLevels(self.root)
        self.expected = {path: path.read_bytes() for path in self.content.rglob("*") if path.is_file()}
        self.target = self.content / "models/block.obj"

    def test_invalid_geometry_never_publishes(self):
        with self.assertRaisesRegex(ValueError, "degenerate"):
            exporter.publish_candidates(self.project, {self.target: b"v 0 0 0\nv 0 0 0\nv 0 0 0\nf 1 2 3\n"}, self.expected)
        self.assertEqual(self.target.read_bytes(), self.expected[self.target])

    def test_report_cannot_overwrite_canonical_content(self):
        source = self.root / "saved.blend"
        source.write_bytes(b"source existence fixture; Blender must not launch")
        with patch.object(exporter, "find_blender") as find:
            with self.assertRaisesRegex(ValueError, "Export reports"):
                exporter.export_saved(source, self.root, report_path=self.content / "sponza_courtyard.json")
            find.assert_not_called()
        self.assertEqual((self.content / "sponza_courtyard.json").read_bytes(),
                         self.expected[self.content / "sponza_courtyard.json"])

    def test_check_rejects_content_changed_during_validation(self):
        def concurrent_edit(*args):
            self.target.write_bytes(b"changed by another author")
            return {}
        with patch.object(exporter, "validate_candidates", side_effect=concurrent_edit):
            with self.assertRaisesRegex(ValueError, "Content changed"):
                exporter.publish_candidates(self.project, {}, self.expected, check=True)
        self.assertEqual(self.target.read_bytes(), b"changed by another author")

    def test_valid_mesh_exceeding_instanced_budget_never_publishes(self):
        lines = []
        for index in range(300):
            x = index * .001
            lines += [f"v {x} 0 0", f"v {x+.001} 0 0", f"v {x} 1 0"]
        lines += [f"f {3*i+1} {3*i+2} {3*i+3}" for i in range(300)]
        with self.assertRaisesRegex(ValueError, "instanced vertices"):
            exporter.publish_candidates(self.project, {self.target: ("\n".join(lines)+"\n").encode()}, self.expected)
        self.assertEqual(self.target.read_bytes(), self.expected[self.target])

    def test_check_preserves_sources_and_publish_changes_only_geometry(self):
        before = read_obj(self.target)
        rows = self.expected[self.target].decode().splitlines()
        index = next(i for i, row in enumerate(rows) if row.startswith("v "))
        xyz = list(map(float, rows[index].split()[1:]))
        xyz[0] += .01
        rows[index] = "v " + " ".join(map(str, xyz))
        payload = ("\n".join(rows)+"\n").encode()
        with self.project.lock():
            report = exporter.publish_candidates(self.project, {self.target: payload}, self.expected, check=True)
        self.assertFalse(report["published"])
        self.assertEqual(self.target.read_bytes(), self.expected[self.target])
        with self.project.lock():
            report = exporter.publish_candidates(self.project, {self.target: payload}, self.expected)
        self.assertTrue(report["published"])
        self.assertNotEqual(read_obj(self.target)["vertices"], before["vertices"])
        for path, old in self.expected.items():
            self.assertEqual(path.read_bytes(), payload if path == self.target else old)


if __name__ == "__main__":
    unittest.main()
