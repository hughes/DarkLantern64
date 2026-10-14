"""Host checks for the editor status verifier's comparison boundary (no GUI)."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.editorctl import OPERATIONS
from tools.project_status import collect_status
from tools.verify_project_status_editor import comparison_fields, compare_shared_status

ROOT = Path(__file__).resolve().parents[1]


class ProjectStatusEditorTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / ".dev/status-editor-host-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scene = dict(version=2, title="Status fixture", entities=[], materials=[], assets=[])
        self.partition = dict(version=1, source="room.json", regions=[])
        self.write("content/room.json", self.scene)
        self.write("content/room.mission.json", self.partition)
        self.draft = dict(scene_file="content/room.json", scene=copy.deepcopy(self.scene),
                          partition_file="content/room.mission.json", partition=copy.deepcopy(self.partition))

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def status(self):
        return collect_status(self.root, self.draft, False, processes=[])

    def test_command_is_exposed(self):
        self.assertIn("get_project_status", OPERATIONS)

    def test_cli_and_editor_comparison_agree_on_unsaved_pair_without_writes(self):
        self.draft["scene"]["title"] = "Unsaved title"
        self.draft["partition"]["regions"] = [{"id": "unsaved"}]
        path = self.write("draft.json", self.draft)
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        editor = self.status()
        editor["editor_observation"] = dict(collecting=False, draft_changed_since_collection=False, error="")
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "tools/project_status.py"),
                                    "--project", str(self.root), "--draft-file", str(path), "--no-doctor"],
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        cli = json.loads(completed.stdout)
        compare_shared_status(editor, cli)
        self.assertTrue(editor["draft"]["dirty"])
        self.assertTrue(editor["draft"]["partition_dirty"])
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_timestamps_and_volatile_sessions_are_independent_observations(self):
        original = self.status()
        later = copy.deepcopy(original)
        later["generated_at_utc"] = "later"
        later["sessions"] = [{"pid": 100, "alive": True, "owned": False, "identity_status": "unknown"}]
        later["editor_observation"] = {"collecting": False}
        compare_shared_status(original, later)
        self.assertNotIn("sessions", comparison_fields(later))

    def test_saved_source_unavailable_keeps_nullable_draft_identity(self):
        (self.root / "content/room.json").unlink()
        status = self.status()
        compare_shared_status(status, copy.deepcopy(status))
        self.assertIsNone(comparison_fields(status)["draft"]["dirty"])
        self.assertIsNone(comparison_fields(status)["draft"]["partition_dirty"])

    def test_compare_rejects_changed_artifact_classification(self):
        status = self.status()
        altered = copy.deepcopy(status)
        altered["scenes"][0]["cook"]["freshness"] = "current"
        altered["scenes"][0]["cook"]["status"] = "passing"
        with self.assertRaisesRegex(AssertionError, "differs from the shared CLI"):
            compare_shared_status(status, altered)

    def test_compare_rejects_lost_unsaved_partition_identity(self):
        self.draft["partition"]["regions"] = [{"id": "new"}]
        status = self.status()
        altered = copy.deepcopy(status)
        altered["draft"]["partition_dirty"] = False
        with self.assertRaises(AssertionError):
            compare_shared_status(status, altered)

    def test_compare_rejects_collection_changed_during_read(self):
        status = self.status()
        status["collection"] = dict(consistent=False, changed_inputs=["content/room.json"])
        with self.assertRaisesRegex(AssertionError, "changed during collection"):
            compare_shared_status(status, copy.deepcopy(status))

    def test_compare_preserves_unknown_capability_and_missing_data_reasons(self):
        status = self.status()
        self.assertEqual(status["capabilities"]["status"], "unavailable")
        self.assertIn("reason", comparison_fields(status)["capabilities"])
        altered = copy.deepcopy(status)
        altered["capabilities"]["reason"] = "different evidence"
        with self.assertRaises(AssertionError):
            compare_shared_status(status, altered)


if __name__ == "__main__":
    unittest.main()
