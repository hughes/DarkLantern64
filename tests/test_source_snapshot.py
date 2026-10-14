import json
import os
from pathlib import Path
import tempfile
import subprocess
import unittest

from tools.source_snapshot import FrozenInputs, ReadGuard, SourceChanged, inventory, project_run_lock, SOURCE_FILES


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "src").mkdir()
        (self.root / "content").mkdir()
        (self.root / "src/main.c").write_text("int main(void){return 0;}\n")
        (self.root / "content/scene.json").write_text('{"id":"scene"}\n')
        self.archive = self.root / ".dev/run/inputs"

    def test_exact_bytes_archived_and_build_outputs_excluded(self):
        (self.root / "build").mkdir()
        (self.root / "build/output.o").write_bytes(b"not source")
        with FrozenInputs(self.root, self.archive, external=False) as frozen:
            self.assertEqual(inventory(self.archive, external=False), frozen.files)
            self.assertEqual(json.loads((self.archive.parent / "input-manifest.json").read_text())["source_sha256"], frozen.identity["source_sha256"])
            (self.root / "build/output.o").write_bytes(b"new output")
            frozen.assert_current()
            frozen.assert_archive()

    def test_added_source_is_stale_and_cannot_be_certified(self):
        with FrozenInputs(self.root, self.archive, external=False) as frozen:
            (self.root / "src/new_header.h").write_text("#define NEW_INPUT 1\n")
            with self.assertRaises(SourceChanged):
                frozen.assert_current()

    def test_maintained_document_change_invalidates_snapshot_but_evidence_can_publish(self):
        document = self.root / "docs/project-status.md"
        document.parent.mkdir()
        document.write_text("Original maintained capabilities", encoding="utf-8")
        readme = self.root / "README.md"
        readme.write_text("Original project guide", encoding="utf-8")
        with FrozenInputs(self.root, self.archive, external=False) as frozen:
            self.assertIn("docs/project-status.md", frozen.files)
            self.assertIn("README.md", frozen.files)
            self.assertEqual((self.archive / "docs/project-status.md").read_bytes(), document.read_bytes())
            evidence = self.root / "docs/evidence/result.json"
            evidence.parent.mkdir()
            evidence.write_text('{"passed":true}', encoding="utf-8")
            frozen.assert_current()
            frozen.guard.close()
            document.write_text("Changed maintained capabilities", encoding="utf-8")
            with self.assertRaises(SourceChanged):
                frozen.assert_current()

    def test_all_capability_marker_targets_are_captured_inputs(self):
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "tools/project_capabilities.json").read_text(encoding="utf-8"))
        targets = {row["path"] for row in data["documentation"]}
        self.assertTrue(targets <= set(SOURCE_FILES), targets - set(SOURCE_FILES))

    def test_modified_archive_is_stale(self):
        with FrozenInputs(self.root, self.archive, external=False) as frozen:
            (self.archive / "content/scene.json").write_text("{}")
            with self.assertRaises(SourceChanged):
                frozen.assert_archive()

    def test_external_dependency_change_detected_even_preserved_timestamp(self):
        header = self.root / "sdk.h"
        header.write_text("old")
        stamp = header.stat().st_mtime_ns
        with FrozenInputs(self.root, self.archive, external=False, dependencies=[header]) as frozen:
            # Release OS protection to exercise the portable hash audit itself.
            frozen.guard.close()
            header.write_text("new")
            os.utime(header, ns=(stamp, stamp))
            with self.assertRaises(SourceChanged):
                frozen.assert_current()

    @unittest.skipUnless(os.name == "nt", "Windows share-mode immutability")
    def test_windows_freeze_blocks_write_and_atomic_replacement_then_releases(self):
        source = self.root / "src/main.c"
        stage = self.root / "src/stage.tmp"
        stage.write_text("replacement")
        with FrozenInputs(self.root, self.archive, external=False):
            with self.assertRaises(OSError):
                source.write_text("changed")
            with self.assertRaises(OSError):
                os.replace(stage, source)
        source.write_text("released")
        os.replace(stage, source)

    def test_run_lock_timeout_and_release(self):
        with project_run_lock(self.root):
            with self.assertRaises(TimeoutError):
                with project_run_lock(self.root, timeout=.01):
                    self.fail("Concurrent publisher admitted")
        with project_run_lock(self.root, timeout=.01):
            pass

    def test_archive_cannot_overwrite_source(self):
        with self.assertRaises(ValueError):
            FrozenInputs(self.root, self.root)

    def test_source_directory_alias_cannot_escape_inventory(self):
        target = self.root / "alias-target"
        target.mkdir()
        (target / "input.h").write_text("mutable dependency")
        alias = self.root / "src/alias"
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(target)], check=True, capture_output=True)
            self.addCleanup(os.rmdir, alias)
        else:
            alias.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "source directory must be regular"):
            inventory(self.root, external=False)

    def test_dependency_repository_metadata_is_retained_and_audited(self):
        checkout = self.root / "external/tiny3d"
        (checkout / ".git/objects").mkdir(parents=True)
        (checkout / "Makefile").write_text("all:\n")
        (checkout / ".git/HEAD").write_text("pinned revision")
        (checkout / ".git/index.lock").write_text("transient")
        with FrozenInputs(self.root, self.archive) as frozen:
            self.assertEqual((self.archive / "external/tiny3d/.git/HEAD").read_text(), "pinned revision")
            self.assertFalse((self.archive / "external/tiny3d/.git/index.lock").exists())
            frozen.guard.close()
            (checkout / ".git/HEAD").write_text("different revision")
            with self.assertRaises(SourceChanged):
                frozen.assert_current()


if __name__ == "__main__":
    unittest.main()
