"""Project CRUD preserves authors' work and isolated previews across levels."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from tools import project_levels as project
from tools.compile_level import validate

ROOT = Path(__file__).resolve().parents[1]


class ProjectLevelTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.content = self.root / "content"
        (self.content / "models").mkdir(parents=True)
        for filename in ("block.obj", "guard.obj"):
            shutil.copy2(ROOT / "content/models" / filename, self.content / "models" / filename)
        self.one = project.starter_level("One")
        self.one["test_starts"] = [{"id": "entry", "label": "Entry", "position": [1, 0, 2.7], "yaw": 180, "pitch": 0}]
        self.write("first_room.json", self.one)
        self.write("second.json", project.starter_level("Two"))
        self.bundle = {"version": 1, "title": "Test game", "levels": [
            {"id": "stable-original-id", "source": "first_room.json"},
            {"id": "stable-second-id", "source": "second.json"}]}
        self.write("level_bundle.json", self.bundle)
        self.project = project.ProjectLevels(self.root)

    def write(self, name, data):
        (self.content / name).write_bytes(project.json_bytes(data))

    def hash(self, name="first_room.json"):
        return project.sha256((self.content / name).read_bytes())

    def content_bytes(self):
        return {p.relative_to(self.content).as_posix(): p.read_bytes()
                for p in self.content.rglob("*") if p.is_file()}

    def test_discovery_reports_saved_levels_and_ignores_unrelated_documents(self):
        self.write("audio_budget.json", {"version": 1, "title": "Not a scene"})
        self.write("random.json", {"something": 12})
        self.write("broken_scene.json", {"version": 2, "title": "Broken"})
        (self.content / "sub").mkdir()
        self.write("sub/hidden.json", self.one)
        catalog = self.project.list()
        self.assertEqual([r["file"] for r in catalog["levels"]], ["first_room.json", "second.json"])
        first = catalog["levels"][0]
        self.assertEqual(first["source_sha256"], self.hash())
        self.assertEqual(first["bundle_id"], "stable-original-id")
        self.assertEqual(first["test_starts"], self.one["test_starts"])
        self.assertEqual(catalog["warnings"][0]["file"], "broken_scene.json")
        self.assertEqual(catalog["bundle"]["max_levels"], 8)

    def test_create_starter_reuses_assets_is_valid_and_registers_bundle(self):
        before_models = (self.content / "models/block.obj").read_bytes()
        result = self.project.create("new_courtyard", "New Courtyard")
        path = self.content / result["level"]["file"]
        data = json.loads(path.read_bytes())
        state = validate(data, self.content)
        self.assertEqual(result["level"]["title"], "New Courtyard")
        self.assertTrue(result["level"]["in_bundle"])
        self.assertEqual(len(state["kinds"]["spawn"]), 1)
        self.assertEqual(len(state["kinds"]["door"]), 1)
        self.assertEqual(len(state["enemies"]), 0)
        self.assertEqual(data["assets"], [{"id": "mesh-block", "uri": "models/block.obj"},
                                          {"id": "mesh-guard", "uri": "models/guard.obj"}])
        self.assertEqual((self.content / "models/block.obj").read_bytes(), before_models)
        self.assertEqual(len(list((self.content / "models").iterdir())), 2)
        self.project.cook(path, result["level"]["source_sha256"])

    def test_starter_supports_the_editor_first_enemy_fallback_without_an_enemy_instance(self):
        result = self.project.create("guard_ready", "Ready for enemies")
        path = self.content / result["level"]["file"]
        data = json.loads(path.read_bytes())
        self.assertFalse(any(entity["kind"] == "guard" for entity in data["entities"]))
        self.assertEqual(next(asset["uri"] for asset in data["assets"] if asset["id"] == "mesh-guard"), "models/guard.obj")
        self.assertTrue(any(material["id"] == "mat-guard" for material in data["materials"]))
        # These are the named model/material and default behavior used by the
        # editor's AddEnemy path when the scene has no existing enemy template.
        data["entities"].append({"id": "first-enemy", "kind": "guard", "model": "mesh-guard",
            "material": "mat-guard", "enemy_type": "watchman", "behavior": "sentry", "patrol": [],
            "transform": {"position": [2, 0, 2], "rotation": [0, 0, 0], "scale": [1, 1, 1]}})
        self.write(path.name, data)
        cooked = self.project.cook(path)
        self.assertEqual(cooked["report"]["counts"]["enemies"], 1)
        self.assertEqual(cooked["report"]["enemy_instances"]["first-enemy"]["behavior"], "sentry")
        self.assertIn('"first-enemy"', (Path(cooked["output"]) / "generated/demo_level.h").read_text())

    def test_duplicate_preserves_authored_objects_and_starts_without_editing_original(self):
        before = (self.content / "first_room.json").read_bytes()
        result = self.project.duplicate(self.content / "first_room.json", "copied", "Copy", self.hash(), False)
        expected = copy.deepcopy(self.one)
        expected["title"] = "Copy"
        self.assertEqual(json.loads((self.content / "copied.json").read_bytes()), expected)
        self.assertEqual((self.content / "first_room.json").read_bytes(), before)
        self.assertFalse(result["level"]["in_bundle"])
        self.assertIsNone(result["level"]["bundle_id"])

    def test_metadata_update_preserves_stable_bundle_id_and_other_authored_data(self):
        result = self.project.update("first_room.json", self.hash(), "Changed title", True)
        self.assertEqual(result["level"]["bundle_id"], "stable-original-id")
        self.assertEqual(result["level"]["title"], "Changed title")
        self.assertEqual(json.loads((self.content / "first_room.json").read_bytes())["entities"], self.one["entities"])
        self.project.update("first_room.json", result["level"]["source_sha256"], in_bundle=False)
        self.assertEqual(self.project.bundle()["levels"], [self.bundle["levels"][1]])
        self.assertTrue((self.content / "first_room.json").exists())

    def test_new_bundle_id_avoids_existing_id_case_insensitively(self):
        self.bundle["levels"][0]["id"] = "THIRD"
        self.write("level_bundle.json", self.bundle)
        result = self.project.create("third", "Third")
        self.assertEqual(result["level"]["bundle_id"], "third-2")

    def test_invalid_titles_paths_and_case_collisions_leave_sources_unchanged(self):
        before = self.content_bytes()
        for name in ("first_room", "FIRST_ROOM", "../outside", "sub/room", "sub\\room", "con", "LPT1", "level_bundle", "C:/outside.json", "a" * 65):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.project.create(name, "New")
            self.assertEqual(self.content_bytes(), before)
        for title in ("", "New\nRoom", "x" * 121, "\u00e9"):
            with self.subTest(title=title), self.assertRaises(ValueError):
                self.project.create("new", title)
            self.assertEqual(self.content_bytes(), before)
        for level in ("FIRST_ROOM.json", "../first_room.json", self.root / "first_room.json"):
            with self.subTest(level=str(level)), self.assertRaises(ValueError):
                self.project.update(level, self.hash(), "Invalid target")
        self.assertEqual(self.content_bytes(), before)

    def test_stale_hash_rejects_every_source_mutation_and_cook(self):
        stale = self.hash()
        self.one["title"] = "Saved by another editor"
        self.write("first_room.json", self.one)
        before = self.content_bytes()
        actions = [lambda: self.project.update("first_room.json", stale, "Overwrite"),
                   lambda: self.project.delete("first_room.json", stale),
                   lambda: self.project.duplicate("first_room.json", "copy", "Copy", stale),
                   lambda: self.project.cook("first_room.json", stale)]
        for action in actions:
            with self.assertRaisesRegex(ValueError, "changed on disk"):
                action()
            self.assertEqual(self.content_bytes(), before)

    def test_required_mutation_hash_cannot_be_bypassed_via_python_api(self):
        for expected in (None, "", "abc"):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.project.delete("first_room.json", expected)

    def test_bundle_capacity_can_be_handled_by_creating_unbundled(self):
        for index in range(6):
            self.project.create(f"room{index}", f"Room {index}")
        before = self.content_bytes()
        with self.assertRaisesRegex(ValueError, "bundle is full"):
            self.project.create("overflow", "Overflow")
        self.assertEqual(self.content_bytes(), before)
        result = self.project.create("overflow", "Overflow", False)
        self.assertFalse(result["level"]["in_bundle"])
        self.assertEqual(len(result["catalog"]["levels"]), 9)

    def test_delete_archives_exact_source_and_membership_without_touching_shared_assets(self):
        original = (self.content / "first_room.json").read_bytes()
        models = (self.content / "models/block.obj").read_bytes()
        result = self.project.delete("first_room.json", self.hash())
        self.assertFalse((self.content / "first_room.json").exists())
        self.assertEqual(Path(result["backup"]).read_bytes(), original)
        recovery = json.loads((Path(result["backup"]).parent / "recovery.json").read_bytes())
        self.assertEqual(recovery["bundle_entry"], self.bundle["levels"][0])
        self.assertEqual((self.content / "models/block.obj").read_bytes(), models)
        self.assertEqual(self.project.bundle()["levels"], [self.bundle["levels"][1]])
        # The documented recovery is sufficient without recreating shared files.
        shutil.copy2(result["backup"], self.content / "first_room.json")
        self.assertEqual(len(self.project.list()["levels"]), 2)

    def test_last_project_level_and_last_bundled_level_are_protected(self):
        self.project.delete("first_room.json", self.hash())
        before = self.content_bytes()
        with self.assertRaisesRegex(ValueError, "at least one level in the project"):
            self.project.delete("second.json", self.hash("second.json"))
        with self.assertRaisesRegex(ValueError, "at least one level in the game menu"):
            self.project.update("second.json", self.hash("second.json"), in_bundle=False)
        self.assertEqual(self.content_bytes(), before)
        self.project.create("unbundled", "Unbundled", False)
        with self.assertRaisesRegex(ValueError, "at least one level in the game menu"):
            self.project.delete("second.json", self.hash("second.json"))

    def test_source_and_bundle_publication_rolls_back_on_second_replace_failure(self):
        before = self.content_bytes()
        real_replace = os.replace
        attempts = []
        def fail_second(source, target):
            attempts.append(str(target))
            if len(attempts) == 2:
                raise OSError("simulated disk failure")
            return real_replace(source, target)
        with patch.object(project.os, "replace", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "simulated disk failure"):
                self.project.update("first_room.json", self.hash(), "Unsaved title", False)
        self.assertEqual(self.content_bytes(), before)
        self.assertFalse((self.root / ".dev/editor/project-levels.lock").exists())

    def test_failed_new_level_registration_removes_new_source(self):
        before = self.content_bytes()
        real_replace = os.replace
        def fail_bundle(source, target):
            if Path(target) == self.content / "level_bundle.json":
                raise OSError("cannot replace manifest")
            return real_replace(source, target)
        with patch.object(project.os, "replace", side_effect=fail_bundle):
            with self.assertRaises(OSError):
                self.project.create("not_created", "Not created")
        self.assertEqual(self.content_bytes(), before)

    def test_backup_survives_when_disk_failure_also_prevents_rollback(self):
        original = (self.content / "first_room.json").read_bytes()
        real_replace = os.replace
        attempts = []
        def fail_after_first(source, target):
            attempts.append(str(target))
            if len(attempts) > 1:
                raise OSError("disk unavailable")
            return real_replace(source, target)
        with patch.object(project.os, "replace", side_effect=fail_after_first):
            with self.assertRaisesRegex(OSError, "Original files and recovery.json were retained"):
                self.project.update("first_room.json", self.hash(), "Publication interrupted")
        recovery_path = next((self.root / ".dev/editor/transactions").rglob("recovery.json"))
        recovery = json.loads(recovery_path.read_bytes())
        source = next(row for row in recovery["files"] if row["target"] == "content/first_room.json")
        self.assertEqual((recovery_path.parent / source["original"]).read_bytes(), original)

    def test_failed_delete_preserves_source_and_removes_partial_archive(self):
        before = self.content_bytes()
        real_replace = os.replace
        def fail_bundle(source, target):
            if Path(target) == self.content / "level_bundle.json":
                raise OSError("cannot replace manifest")
            return real_replace(source, target)
        with patch.object(project.os, "replace", side_effect=fail_bundle):
            with self.assertRaises(OSError):
                self.project.delete("first_room.json", self.hash())
        self.assertEqual(self.content_bytes(), before)
        self.assertFalse(any(p.is_file() for p in (self.root / ".dev/editor/deleted-levels").rglob("*")))

    def test_namespaced_cooks_coexist_and_only_changed_assets_are_invalidated(self):
        one = self.project.cook("first_room.json")
        original = Path(one["asset_root"]) / one["scene_path"]
        original_bytes = original.read_bytes()
        two = self.project.cook("second.json")
        self.assertEqual(one["output"], (self.root / "build").as_posix())
        self.assertEqual(two["output"], (self.root / "build/scenes/second").as_posix())
        self.assertEqual(original.read_bytes(), original_bytes)
        for result in (one, two):
            scene = json.loads((Path(result["asset_root"]) / result["scene_path"]).read_bytes())
            stem = Path(result["source"]).stem
            meshes = [e["mesh"] for e in scene["entities"] if "mesh" in e]
            self.assertTrue(meshes)
            self.assertTrue(all(uri.startswith(f"file://meshes/{stem}/") for uri in meshes))
            for uri in meshes:
                self.assertTrue((Path(result["asset_root"]) / uri.removeprefix("file://")).is_file())
            self.assertTrue(result["changed_preview_assets"])
        self.assertEqual(self.project.cook("first_room.json")["changed_preview_assets"], [])
        model = self.content / "models/block.obj"
        model.write_text(model.read_text().replace("v -0.5 -0.5 -0.5", "v -0.45 -0.5 -0.5"))
        changed = self.project.cook("first_room.json")["changed_preview_assets"]
        self.assertEqual(changed, [(Path(one["asset_root"]) / "meshes/first_room/mesh-block.gltf").as_posix()])

    def test_texture_uris_are_namespaced_and_resolve_to_preview_pixels(self):
        (self.content / "models/triangle.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nf 1/1 2/2 3/3\n")
        Image.new("RGB", (4, 4), (200, 130, 20)).save(self.content / "gold.png")
        data = copy.deepcopy(self.one)
        data["assets"].append({"id": "mesh-textured", "uri": "models/triangle.obj"})
        data["materials"].append({"id": "mat-textured", "color": [1, 1, 1, 1],
                                  "texture": {"uri": "gold.png", "width": 4, "height": 4}})
        data["entities"].append({"id": "decal", "kind": "static", "model": "mesh-textured", "material": "mat-textured",
                                 "transform": {"position": [0, 1, -3], "rotation": [0, 0, 0], "scale": [1, 1, 1]}})
        self.write("second.json", data)
        result = self.project.cook("second.json")
        scene = json.loads((Path(result["asset_root"]) / result["scene_path"]).read_bytes())
        uri = next(m["textures"]["albedo"] for m in scene["materials"] if "textures" in m)
        self.assertTrue(uri.startswith("file://textures/second/"))
        target = Path(result["asset_root"]) / uri.removeprefix("file://")
        self.assertTrue(target.is_file())
        self.assertIn(target.as_posix(), result["changed_preview_assets"])
        original_pixels = target.read_bytes()
        scene_path = Path(result["asset_root"]) / result["scene_path"]
        unchanged = self.project.cook("second.json")
        unchanged_scene = json.loads(scene_path.read_bytes())
        self.assertEqual(next(m["textures"]["albedo"] for m in unchanged_scene["materials"] if "textures" in m), uri)
        self.assertEqual(unchanged["changed_preview_assets"], [])
        Image.new("RGB", (4, 4), (20, 80, 230)).save(self.content / "gold.png")
        changed = self.project.cook("second.json")
        changed_scene = json.loads(scene_path.read_bytes())
        changed_uri = next(m["textures"]["albedo"] for m in changed_scene["materials"] if "textures" in m)
        self.assertNotEqual(changed_uri, uri)
        changed_target = Path(result["asset_root"]) / changed_uri.removeprefix("file://")
        self.assertEqual(changed["changed_preview_assets"], [changed_target.as_posix()])
        self.assertNotEqual(changed_target.read_bytes(), original_pixels)
        # Previously loaded descriptors/cache entries still refer to immutable
        # pixel bytes while the new descriptor picks up the replacement URI.
        self.assertEqual(target.read_bytes(), original_pixels)

    def test_staged_cook_never_publishes_canonical_source(self):
        staged = self.root / ".dev/staged.json"
        staged.parent.mkdir()
        edited = copy.deepcopy(self.one)
        edited["title"] = "Unsaved draft"
        staged.write_bytes(project.json_bytes(edited))
        before = self.content_bytes()
        result = self.project.cook("first_room.json", self.hash(), staged)
        self.assertTrue(result["staged"])
        self.assertEqual(result["report"]["title"], "Unsaved draft")
        self.assertEqual(self.content_bytes(), before)
        preview = Path(result["asset_root"]) / result["scene_path"]
        preview_before = preview.read_bytes()
        edited["entities"] = []
        staged.write_bytes(project.json_bytes(edited))
        with self.assertRaises(ValueError):
            self.project.cook("first_room.json", self.hash(), staged)
        self.assertEqual(self.content_bytes(), before)
        self.assertEqual(preview.read_bytes(), preview_before)

    def test_external_edit_during_cook_prevents_generated_publication(self):
        original_compile = project.compile_level
        def external_edit(*args, **kwargs):
            result = original_compile(*args, **kwargs)
            data = copy.deepcopy(self.one)
            data["title"] = "Externally edited"
            self.write("first_room.json", data)
            return result
        with patch.object(project, "compile_level", side_effect=external_edit):
            with self.assertRaisesRegex(ValueError, "changed during this operation"):
                self.project.cook("first_room.json", self.hash())
        self.assertFalse((self.root / "build/project/editor-assets/levels/first_room.json").exists())
        self.assertEqual(json.loads((self.content / "first_room.json").read_bytes())["title"], "Externally edited")

    def test_symlink_sources_and_output_escapes_are_rejected(self):
        outside = self.root.parent / (self.root.name + "-outside.json")
        outside.write_bytes(project.json_bytes(self.one))
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        link = self.content / "linked.json"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symlink creation unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.project.cook("linked.json")
        self.assertNotIn("linked.json", [entry["file"] for entry in self.project.list()["levels"]])
        build = self.root / "build"
        build.symlink_to(self.content, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            self.project.cook("first_room.json")

    def test_cli_json_success_and_error_are_machine_readable(self):
        command = [sys.executable, str(ROOT / "tools/project_levels.py"), "--root", str(self.root)]
        success = subprocess.run(command + ["list"], capture_output=True, text=True)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertTrue(json.loads(success.stdout)["ok"])
        failure = subprocess.run(command + ["delete", "--level", "../outside.json", "--expected-sha256", "0" * 64], capture_output=True, text=True)
        self.assertEqual(failure.returncode, 1)
        self.assertFalse(json.loads(failure.stdout)["ok"])
        self.assertEqual(failure.stderr, "")


if __name__ == "__main__":
    unittest.main()
