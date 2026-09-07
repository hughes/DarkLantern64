import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import depth_study


class DepthFixtureTests(unittest.TestCase):
    def test_baseline_builds_pristine_copy_without_checkout_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / "external/tiny3d"
            source.mkdir(parents=True)
            output = root / "build/depth-study/fresh"
            output.mkdir(parents=True)
            calls = []

            def copy_inputs(stage):
                (stage / "src/t3d").mkdir(parents=True)
                (stage / "src/t3d/t3d.c").write_text("pristine")
                (stage / "Makefile").write_text("all: library")

            def make(args):
                calls.append(args)
                stage = args[-2]
                self.assertFalse((stage / "build_sdk_study/old.o").exists())
                library = stage / "build_sdk_study/libt3d.a"
                library.parent.mkdir()
                library.write_bytes(b"freshly built pristine library")
                return subprocess.CompletedProcess(args, 0)

            with patch("depth_study.ROOT", root), patch("depth_study.SOURCE", source), \
                    patch("depth_study.copy_pinned_inputs", side_effect=copy_inputs) as copy, \
                    patch("depth_study.run", side_effect=make), \
                    patch("depth_study.apply_viewport_precision") as precision:
                library, include = depth_study.stage_library(output, root / "sdk", False, source)
                (library.parent / "old.o").write_bytes(b"stale")
                depth_study.stage_library(output, root / "sdk", False, source)
                self.assertEqual(copy.call_count, 2)
                self.assertEqual(len(calls), 2)
                precision.assert_not_called()
                self.assertEqual(library.read_bytes(), b"freshly built pristine library")
                self.assertEqual((include / "t3d/t3d.c").read_text(), "pristine")
                self.assertFalse((source / "build_sdk_main/libt3d.a").exists())

    def test_fixture_retains_authored_gap_and_triangulation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            report = depth_study.fixture(output)
            self.assertAlmostEqual(report["gap_metres"], .0475)
            self.assertEqual(report["camera_count"], 60)
            header = (output / "fixture.h").read_text()
            self.assertIn("{32,48,vertices_0,indices_0", header)
            self.assertIn("{4,6,vertices_1,indices_1", header)
            self.assertEqual(len(report["inputs"]), 3)

    def test_changed_art_gap_rejected_instead_of_relabelled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = root / "content"
            (content / "models").mkdir(parents=True)
            original = depth_study.ROOT / "content"
            for name in ("night_block.obj", "night_facade.obj"):
                shutil.copyfile(original / "models" / name, content / "models" / name)
            level = json.loads((original / "moonlit_courtyard.json").read_text())
            window = next(e for e in level["entities"] if e["id"] == "west-lit-window")
            window["transform"]["position"][0] += .02
            (content / "moonlit_courtyard.json").write_text(json.dumps(level))
            with patch("depth_study.ROOT", root):
                with self.assertRaisesRegex(ValueError, "gap changed"):
                    depth_study.fixture(root)


if __name__ == "__main__":
    unittest.main()
