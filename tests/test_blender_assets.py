"""Host-side Blender command, output-boundary and add-on package checks."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import blender_assets as cli


class BlenderAssetCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.content = self.root / "content"
        self.art = self.root / "art"
        self.art.mkdir()
        self.source = self.art / "source.blend"
        self.source.write_bytes(b"test saved source")
        self.output = self.content / "assets/test-pack"
        self.blender = self.root / "blender.exe"
        self.blender.write_bytes(b"test executable")
        for name, value in (("ROOT", self.root), ("CONTENT", self.content)):
            patcher = patch.object(cli, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_output_requires_named_asset_folder_and_rejects_windows_aliases(self):
        self.assertEqual(cli.output_directory(self.output), self.output.resolve())
        for path in (self.content, self.content / "assets", self.root / "outside", self.content / "assets/../levels",
                     self.content / "assets/bad name", self.content / "assets/CON", self.content / "assets/aux"):
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                cli.output_directory(path)

    def test_sources_are_read_only_outside_art_and_demo_writes_stay_inside_art(self):
        outside = self.root / "other.blend"
        outside.write_bytes(b"external read-only source")
        self.assertEqual(cli.source_file(outside), outside.resolve())
        self.assertEqual(cli.source_file(self.art / "new.blend", create=True), (self.art / "new.blend").resolve())
        with self.assertRaisesRegex(ValueError, "art folder"):
            cli.source_file(outside, create=True)
        for path in (self.art / "missing.blend", self.art / "not-a-blend.json"):
            with self.assertRaises(ValueError):
                cli.source_file(path)

    def test_explicit_blender_failure_does_not_fall_back_to_another_installation(self):
        with patch.dict(cli.os.environ, {"BLENDER_EXE": str(self.blender)}):
            self.assertEqual(cli.find_blender(), self.blender.resolve())
            with self.assertRaisesRegex(ValueError, "executable not found"):
                cli.find_blender(self.root / "missing.exe")

    def test_headless_export_uses_fixed_script_and_reports_child_failures(self):
        def export(command, **kwargs):
            self.assertLess(command.index("--disable-autoexec"), command.index(str(self.source)))
            self.assertEqual(command[command.index("--python-exit-code") + 1], "1")
            self.assertEqual(command[command.index("--python") + 1], str(Path(cli.__file__).resolve()))
            self.assertIn("--export-worker", command)
            self.assertNotIn("shell", kwargs)
            self.output.mkdir(parents=True)
            (self.output / "pack.json").write_text(json.dumps({"assets": [{}], "materials": [{}], "prefabs": [{"id": "example"}]}))

        args = ["--source", str(self.source), "--output", str(self.output), "--blender", str(self.blender)]
        with patch.object(cli.subprocess, "run", side_effect=export), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args), 0)
        error = cli.subprocess.CalledProcessError(1, [str(self.blender)])
        with patch.object(cli.subprocess, "run", side_effect=error), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(args), 1)
        self.assertEqual(self.source.read_bytes(), b"test saved source")

    def test_package_is_repeatable_and_includes_module_dependencies(self):
        addon = self.root / "addon"
        addon.mkdir()
        (addon / "__init__.py").write_text("from .core import helper\n")
        (addon / "core.py").write_text("helper = 1\n")
        (addon / "__pycache__").mkdir()
        (addon / "__pycache__/core.pyc").write_bytes(b"cache")
        with patch.object(cli, "ADDON", addon), contextlib.redirect_stdout(io.StringIO()):
            first = cli.package_addon().read_bytes()
            archive = cli.package_addon()
            self.assertEqual(first, archive.read_bytes())
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(set(package.namelist()), {"darklantern64_export/__init__.py", "darklantern64_export/core.py"})


if __name__ == "__main__":
    unittest.main()
