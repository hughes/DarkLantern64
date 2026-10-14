import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools.project_status import (Inspector, canonical_sha256, collect_status,
                                  sessions_status, validation_status)

ROOT = Path(__file__).resolve().parents[1]
KNOWN = "test_sponza_physics.AuthoredSponzaPhysics.test_actual_core_traverses_authored_stairs_and_respects_stacked_floors"
MESSAGE = "ValueError: Level collision differs from geometry: sponza-collision-parapet-north"


class ProjectStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="status-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.document = dict(version=2, title="Fixture", entities=[], assets=[], materials=[])
        self.scene = self.write("content/room.json", self.document)
        self.write("tools/acceptance_manifest.json", json.loads((ROOT / "tools/acceptance_manifest.json").read_text(encoding="utf-8")))

    def write(self, name, document):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def status(self, draft=None):
        return collect_status(self.root, draft, False, processes=[])

    def cook(self, partition=None):
        report = dict(source_sha256=hashlib.sha256(self.scene.read_bytes()).hexdigest(), dependencies=[], playable=True,
                      recipe_sources=Inspector(self.root).recipe_sources(),
                      renderer_diagnostics=dict(identity=dict(source_file_sha256=hashlib.sha256(self.scene.read_bytes()).hexdigest())))
        if partition:
            report["mission_manifest_sha256"] = hashlib.sha256(partition.read_bytes()).hexdigest()
        self.write("build/scenes/room/editor-assets/levels/first_room.json", {})
        return self.write("build/scenes/room/generated/level_report.json", report)

    def test_clean_checkout_missing_artifacts_are_unavailable(self):
        result = self.status()
        scene = result["scenes"][0]
        self.assertEqual(scene["cook"]["status"], "unavailable")
        self.assertEqual(scene["rom"]["status"], "unavailable")
        self.assertFalse(scene["cook"]["matching_current"])
        self.assertIsNone(result["draft"]["dirty"])

    def test_source_and_partition_changes_invalidate_cook(self):
        partition = self.write("content/room.mission.json", dict(version=1, source="room.json", regions=[]))
        self.cook(partition)
        self.assertEqual(self.status()["scenes"][0]["cook"]["freshness"], "current")
        self.write("content/room.mission.json", dict(version=1, source="room.json", regions=[{"id": "new"}]))
        result = self.status()
        self.assertEqual(result["scenes"][0]["cook"]["status"], "stale")
        self.assertTrue(any("Partition" in r for r in result["scenes"][0]["cook"]["reasons"]))
        self.write("content/room.json", {**self.document, "title": "Changed"})
        self.assertTrue(any("Scene" in r for r in self.status()["scenes"][0]["cook"]["reasons"]))

    def test_missing_preview_is_incomplete_not_pass(self):
        self.cook()
        (self.root / "build/scenes/room/editor-assets/levels/first_room.json").unlink()
        self.assertEqual(self.status()["scenes"][0]["cook"]["status"], "incomplete")

    def test_source_recipe_change_invalidates_matching_scene_cook(self):
        self.write("tools/cooker.py", "before")
        self.cook()
        self.assertEqual(self.status()["scenes"][0]["cook"]["freshness"], "current")
        self.write("tools/cooker.py", "after")
        self.assertEqual(self.status()["scenes"][0]["cook"]["freshness"], "stale")

    def test_canonical_editor_stream_hash_and_target_failure_are_explicit(self):
        partition = self.write("content/room.mission.json", dict(version=1, source="room.json", regions=[]))
        cook = self.cook(partition)
        report = json.loads(cook.read_text())
        identity = report["renderer_diagnostics"]["identity"]
        identity.update(source_file_sha256=canonical_sha256(self.document), source_file_sha256_basis="canonical_json")
        report["bridge_diagnostics"] = dict(valid=False, failures=[dict(message="Capacity exceeded")])
        self.write(cook.relative_to(self.root), report)
        row = self.status()["scenes"][0]["cook"]
        self.assertEqual(row["freshness"], "current")
        self.assertEqual(row["outcome"], "failing")
        self.assertEqual(row["authoring_preview_outcome"], "passing")

    def test_wrapped_target_stream_cook_uses_mission_identity_without_authoring_descriptor(self):
        partition = self.write("content/room.mission.json", dict(version=1, source="room.json", regions=[]))
        package = b"fixture package"
        digest = hashlib.sha256(package).hexdigest()
        report = dict(experimental_stream=True, playable=True, source_sha256="compiled-target-identity",
                      mission_manifest_sha256=hashlib.sha256(partition.read_bytes()).hexdigest(),
                      recipe_sources=Inspector(self.root).recipe_sources(),
                      mission=dict(path="romfs/missions/fixture.dlm", sha256=digest,
                                   report=dict(source_file_sha256=hashlib.sha256(self.scene.read_bytes()).hexdigest(),
                                               source_file_sha256_basis="saved_bytes")))
        self.write("build/scenes/room/generated/level_report.json", report)
        target = self.root / "build/scenes/room/romfs/missions/fixture.dlm"
        target.parent.mkdir(parents=True); target.write_bytes(package)
        row = self.status()["scenes"][0]["cook"]
        self.assertTrue(row["matching_current"])
        self.assertEqual((row["status"], row["scope"]), ("passing", "target-cook"))
        self.assertEqual(row["package_sha256"], digest)
        self.assertEqual(row["authoring_preview_outcome"], "unavailable")

    def test_missing_dependency_is_incomplete_changed_dependency_stale(self):
        cook = self.cook()
        value = json.loads(cook.read_text())
        value["dependencies"] = [{"uri": "models/absent.obj", "sha256": "0" * 64}]
        self.write(cook.relative_to(self.root), value)
        self.assertEqual(self.status()["scenes"][0]["cook"]["status"], "incomplete")
        asset = self.root / "content/models/absent.obj"
        asset.parent.mkdir(); asset.write_text("changed", encoding="utf-8")
        self.assertEqual(self.status()["scenes"][0]["cook"]["status"], "stale")

    def test_draft_identity_ignores_formatting_and_never_saves(self):
        before = self.scene.read_bytes()
        draft = dict(scene_file="content/room.json", scene=copy.deepcopy(self.document))
        self.assertFalse(self.status(draft)["draft"]["dirty"])
        draft["scene"]["title"] = "Unsaved"
        result = self.status(draft)
        self.assertTrue(result["draft"]["dirty"])
        self.assertTrue(result["scenes"][0]["draft"]["dirty"])
        self.assertEqual(self.scene.read_bytes(), before)

    def test_matching_rom_checks_bytes_and_complete_input_inventory(self):
        rom = self.root / "build/fixture.z64"
        rom.parent.mkdir(); rom.write_bytes(b"ROM")
        data = dict(rom="build/fixture.z64", rom_sha256=hashlib.sha256(b"ROM").hexdigest(),
                    input_hashes=[dict(path=str(self.scene), sha256=hashlib.sha256(self.scene.read_bytes()).hexdigest())],
                    level_catalog=dict(levels=[dict(source=str(self.scene), cooked_dir="build/scenes/room")]))
        self.write("build/fixture/build.json", data)
        self.assertEqual(self.status()["scenes"][0]["rom"]["status"], "passing")
        rom.write_bytes(b"tampered")
        self.assertEqual(self.status()["scenes"][0]["rom"]["status"], "stale")
        rom.unlink()
        self.assertEqual(self.status()["scenes"][0]["rom"]["status"], "incomplete")

    def test_validation_reclassifies_exact_known_failure_without_hiding_new_error(self):
        inspector = Inspector(self.root)
        files = inspector.inventory()
        result = dict(tests_run=2, failures=[dict(id=KNOWN, message=MESSAGE)], skipped_tests=[])
        report = dict(inputs=dict(files=files, source_sha256=canonical_sha256(files)),
                      results=[dict(id="portable-python", required=True, status="known_failure", result=result)])
        row = validation_status(inspector, self.root / "summary.json", report)
        self.assertEqual(row["outcome"], "known_failure")
        self.assertEqual(row["known_fixture_failures"][0]["id"], KNOWN)
        result["failures"].append(dict(id=KNOWN, message=MESSAGE + "\nPermissionError: new regression"))
        row = validation_status(inspector, self.root / "summary.json", report)
        self.assertEqual(row["outcome"], "failing")
        self.assertEqual(len(row["known_fixture_failures"]), 1)

    def test_missing_local_data_is_separate_from_failures(self):
        result = dict(tests_run=1, failures=[], skipped_tests=[dict(
            id="test_thief_objects.InstalledThiefObjectsTests.test_bafford_placements_and_loot_total",
            message="Set DL_THIEF_GOLD to verify locally installed source assets")])
        row = validation_status(Inspector(self.root), self.root / "results.json", result)
        self.assertEqual(row["outcome"], "passing")
        self.assertEqual(row["freshness"], "unknown")
        self.assertEqual(len(row["unavailable_test_data"]), 1)
        self.assertFalse(row["known_fixture_failures"])

    def test_added_source_makes_previous_passing_report_stale(self):
        files = Inspector(self.root).inventory()
        report = dict(inputs=dict(files=files), results=[dict(id="one", passed=True, status="passed")])
        self.write("tools/new.py", "new source")
        row = validation_status(Inspector(self.root), self.root / "results.json", report)
        self.assertEqual((row["status"], row["outcome"]), ("stale", "passing"))

    def test_runner_inventory_excludes_external_and_subset_remains_visible(self):
        self.write("external/LightEngine/ignored.cpp", "not in Python evidence")
        report = dict(tests_run=1, status="passed", accepted=True, failures=[], skipped_tests=[],
                      inputs=dict(files=Inspector(self.root).inventory(external=False), include_external=False), runner={},
                      selection=dict(pattern="test_subset.py"), scope="Python subset only")
        row = validation_status(Inspector(self.root), self.root / "runner.json", report)
        self.assertEqual(row["freshness"], "current")
        self.assertEqual(row["selection"]["pattern"], "test_subset.py")
        self.assertFalse(row["full_acceptance"])

    def test_incomplete_publication_cannot_be_overruled_by_passing_test_counts(self):
        result = dict(tests_run=1, failures=[], skipped_tests=[])
        report = dict(inputs=dict(files=Inspector(self.root).inventory()),
                      results=[dict(id="python", status="incomplete", accepted=False, result=result)])
        row = validation_status(Inspector(self.root), self.root / "runner.json", report)
        self.assertEqual(row["outcome"], "incomplete")

    def test_process_identity_rejects_pid_reuse_and_preserves_unrelated(self):
        started = "2026-10-14T10:00:00+00:00"
        self.write(".dev/editor/project/session.json", dict(project_editor=True, pid=7, source=str(self.scene),
                                                          process_started_at_utc=started))
        observed = [dict(pid=7, executable="darklantern64_project_editor.exe", command_line=str(self.root), started_at_utc=started),
                    dict(pid=9, executable="ares.exe", command_line="unrelated.z64", started_at_utc=started)]
        rows = sessions_status(Inspector(self.root), observed)
        self.assertTrue(rows[0]["owned"])
        self.assertFalse(rows[1]["owned"])
        observed[0]["started_at_utc"] = "2026-10-14T11:00:00+00:00"
        self.assertFalse(sessions_status(Inspector(self.root), observed)[0]["owned"])
        self.assertEqual(len(observed), 2)

    def test_invalid_artifact_is_visible_and_does_not_crash_collector(self):
        self.write("build/invalid/build.json", ["not metadata"])
        self.write("build/test-results/latest.json", ["not a report"])
        self.write(".dev/editor/project/session.json", ["not a session"])
        result = self.status()
        self.assertEqual(result["reports"][0]["status"], "incomplete")
        self.assertEqual(result["reports"][1]["status"], "incomplete")
        self.assertEqual(result["sessions"][0]["identity_status"], "unknown")

    def test_ambiguous_partition_cannot_be_matching_current(self):
        partition = self.write("content/room.mission.json", dict(version=1, source="room.json", regions=[]))
        self.cook(partition)
        self.write("content/duplicate.mission.json", dict(version=1, source="room.json", regions=[]))
        scene = self.status()["scenes"][0]
        self.assertEqual(scene["partition"]["status"], "incomplete")
        self.assertFalse(scene["cook"]["matching_current"])

    def test_collection_audits_changes_after_scene_hashing(self):
        self.cook()
        def changed(*args, **kwargs):
            self.write("content/room.json", {**self.document, "title": "Changed during collection"})
            return []
        with patch("tools.project_status.sessions_status", side_effect=changed):
            result = self.status()
        self.assertFalse(result["collection"]["consistent"])
        self.assertEqual(result["scenes"][0]["cook"]["status"], "incomplete")
        self.assertFalse(result["scenes"][0]["cook"]["matching_current"])
        self.assertIsNone(result["draft"]["dirty"])

    def test_cli_creates_no_files_in_clean_project(self):
        before = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        completed = subprocess.run([sys.executable, "-B", str(ROOT / "tools/project_status.py"), "--project", str(self.root), "--no-doctor"],
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["schema_version"], 1)
        after = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
