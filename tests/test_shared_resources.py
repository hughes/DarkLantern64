"""Linked art remains live, preserves authored objects, and obeys scene budgets."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from level_fixtures import gameplay_baseline, copy_level_dependencies
from tools.asset_pack import resolve_asset_packs, merge_pack
from tools import compile_level as compiler
from tools import compile_bundle as bundle
from tools import build

ROOT = Path(__file__).resolve().parents[1]


class SharedResourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.content = self.root / "content"
        self.content.mkdir()
        self.level = gameplay_baseline()
        copy_level_dependencies(self.level, self.content)
        self.pack = {"version": 1,
            "assets": [next(a for a in self.level["assets"] if a["id"] == "mesh-guard")],
            "materials": [next(m for m in self.level["materials"] if m["id"] == "mat-guard")],
            "prefabs": [{"id": "guard-watchman", "enemy_type": "watchman", "model": "mesh-guard",
                         "material": "mat-guard", "scale": [1, 1, 1]}]}
        self.level["assets"] = [a for a in self.level["assets"] if a["id"] != "mesh-guard"]
        self.level["materials"] = [m for m in self.level["materials"] if m["id"] != "mat-guard"]
        self.level["asset_packs"] = ["guard-pack.json"]
        self.write_pack()

    def write_pack(self):
        (self.content / "guard-pack.json").write_text(json.dumps(self.pack))

    def test_resolution_preserves_source_and_defines_shared_ownership(self):
        original = copy.deepcopy(self.level)
        state = compiler.validate(self.level, self.content)
        self.assertEqual(self.level, original)
        self.assertEqual(state["resolved_document"]["entities"], original["entities"])
        self.assertIn("mesh-guard", state["characters"] or state["meshes"])
        catalog = state["resolved_catalog"]
        self.assertEqual(catalog["origins"], {"mesh-guard": "guard-pack.json", "mat-guard": "guard-pack.json",
                                              "guard-watchman": "guard-pack.json"})
        self.assertEqual(state["enemy_types"][0]["visual_prefab"], "guard-watchman")
        preview = compiler.preview_scene(self.level, state)
        self.assertIn("mat-guard", {material["name"] for material in preview["materials"]})
        imported, prefabs = merge_pack(self.level, "guard-pack.json", self.content)
        self.assertEqual(imported, original)  # Importing a linked pack never flattens it.
        self.assertEqual(prefabs, self.pack["prefabs"])

    def test_shared_edit_changes_both_cooks_and_preview_without_rewriting_levels(self):
        sources = [self.content / "one.json", self.content / "two.json"]
        raw = json.dumps(self.level).encode()
        for source in sources:
            source.write_bytes(raw)
        before = [compiler.compile_level(source, self.root / (source.stem + "-before")) for source in sources]
        self.pack["materials"][0]["color"] = [.2, .4, .8, 1]
        self.write_pack()
        for index, source in enumerate(sources):
            output = self.root / (source.stem + "-after")
            report = compiler.compile_level(source, output)
            self.assertNotEqual(report["source_sha256"], before[index]["source_sha256"])
            self.assertEqual(source.read_bytes(), raw)
            self.assertIn("guard-pack.json", [d["uri"] for d in report["dependencies"]])
            self.assertIn("code:src/content_limits.h", [d["uri"] for d in report["dependencies"]])
            scene = json.loads((output / "editor-assets/levels/first_room.json").read_text())
            self.assertEqual(next(m["baseColor"] for m in scene["materials"] if m["name"] == "mat-guard"), [.2, .4, .8, 1])
            self.assertEqual(scene["metadata"]["darklantern"]["resolved_catalog"], report["resolved_catalog"])
            self.assertEqual(report["limits"]["scene_vertices"], 6144)

    def test_invalid_links_collisions_and_type_defaults_fail_atomically(self):
        for links in (["../escape.json"], ["guard-pack.json", "guard-pack.json"], "guard-pack.json"):
            level = copy.deepcopy(self.level)
            level["asset_packs"] = links
            before = copy.deepcopy(level)
            with self.assertRaises(ValueError):
                resolve_asset_packs(level, self.content)
            self.assertEqual(level, before)
        collision = copy.deepcopy(self.level)
        collision["assets"].append(self.pack["assets"][0])
        with self.assertRaisesRegex(ValueError, "conflicts"):
            compiler.validate(collision, self.content)
        self.pack["prefabs"][0]["enemy_type"] = "unknown"
        self.write_pack()
        with self.assertRaisesRegex(ValueError, "unknown prefab enemy_type"):
            compiler.validate(self.level, self.content)
        self.pack["prefabs"][0]["enemy_type"] = "watchman"
        self.pack["prefabs"].append(dict(self.pack["prefabs"][0], id="ambiguous-watchman"))
        self.write_pack()
        with self.assertRaisesRegex(ValueError, "Multiple visual prefabs"):
            compiler.validate(self.level, self.content)

    def test_prefab_only_edit_invalidates_build_manifest_with_identical_c_arrays(self):
        source, output = self.content / "one.json", self.root / "cook"
        source.write_text(json.dumps(self.level))
        first = compiler.compile_level(source, output)
        header = output / "generated/demo_level.h"
        before = header.read_bytes()
        signature = build.fingerprint([header], {}, content_hashes=[first["source_sha256"]])
        self.pack["prefabs"][0]["scale"] = [1.1, 1.1, 1.1]
        self.write_pack()
        second = compiler.compile_level(source, output)
        self.assertEqual(header.read_bytes(), before)  # Existing authored scale stays 1.
        self.assertNotEqual(first["source_sha256"], second["source_sha256"])
        self.assertNotEqual(signature, build.fingerprint([header], {}, content_hashes=[second["source_sha256"]]))

    def test_scene_budget_is_distinct_from_per_mesh_budget(self):
        # Many instances of a valid mesh can exceed 4096 vertices. One oversized
        # mesh still fails even if the scene total would fit 6144.
        data = copy.deepcopy(self.level)
        data["assets"].append({"id": "budget-mesh", "uri": "budget.obj"})
        path = self.content / "budget.obj"
        triangle = "v 0 0 0\nv .1 0 0\nv 0 .1 0\n"
        path.write_text(triangle + "v 0 0 0\n" * 2045 + "f 1 2 3\n")
        for i in range(2):
            data["entities"].append({"id": f"budget-{i}", "kind": "static", "model": "budget-mesh", "material": "mat-guard",
                                     "transform": {"position": [0, 0, 0], "rotation": [0, 0, 0], "scale": [1, 1, 1]}})
        state = compiler.validate(data, self.content)
        total = sum(len(state["meshes"][e["model"]]["vertices"]) for e in state["models"])
        self.assertGreater(total, 4096)
        self.assertLessEqual(total, 6144)
        data["entities"].append(dict(data["entities"][-1], id="budget-2"))
        with self.assertRaisesRegex(ValueError, "maximum 6144 instanced vertices"):
            compiler.validate(data, self.content)
        path.write_text(triangle + "v 0 0 0\n" * 4094 + "f 1 2 3\n")
        with self.assertRaisesRegex(ValueError, "3-4096 vertices"):
            compiler.validate(data, self.content)

    def test_all_authored_levels_resolve_one_character_and_fit_new_capacity(self):
        # Keep the level's static geometry baseline, while allowing the shared
        # artist-authored character to evolve within the actual scene budget.
        expected = {"first_room": (1, 1674), "moonlit_courtyard": (1, 3186),
                    "enemy_patrols": (3, 1674), "animation_workshop": (2, 80)}
        character_vertices = len(json.loads((ROOT / "content/assets/guard/guard.character.json").read_bytes())["mesh"]["vertices"])
        hashes = set()
        for name, (guards, static_vertices) in expected.items():
            with self.subTest(level=name):
                source = ROOT / "content" / (name + ".json")
                data = json.loads(source.read_text())
                original = copy.deepcopy(data)
                state = compiler.validate(data, ROOT / "content")
                self.assertEqual(data, original)
                self.assertEqual(data["asset_packs"], ["assets/guard/pack.json"])
                self.assertNotIn("mesh-guard", {a["id"] for a in data["assets"]})
                self.assertNotIn("mat-guard", {m["id"] for m in data["materials"]})
                self.assertEqual(len(state["enemies"]), guards)
                self.assertTrue(all(e["model"] in state["characters"] for e in state["kinds"]["guard"]))
                total = sum(len(state["meshes"][e["model"]]["vertices"]) for e in state["models"])
                self.assertEqual(total, static_vertices + guards * character_vertices)
                self.assertLessEqual(total, compiler.LIMITS["scene_vertices"])
                hashes.add(state["characters"]["mesh-guard"]["source_sha256"])
        self.assertEqual(len(hashes), 1)

    def test_bundle_keeps_one_shared_character_definition(self):
        # Use the real shared character in two independent small room cooks.
        data = copy.deepcopy(self.level)
        data["asset_packs"] = ["assets/guard/pack.json"]
        entries = []
        for name in ("one", "two"):
            source = self.content / (name + ".json")
            source.write_text(json.dumps(data))
            entries.append({"id": name, "source": source})
        copy_level_dependencies(data, self.content)
        empty_sprites = {"textures": [], "decoded_bytes": 0, "sprite_bytes": 0}
        with patch.object(bundle, "cook_sprites", return_value=empty_sprites):
            report, _ = bundle.prepare_bundle(entries, "Shared guard", self.root / "bundle", self.root / "sdk")
        self.assertEqual(report["characters"]["unique_assets"], 1)
        self.assertGreater(report["characters"]["geometry_duplication_avoided_bytes"], 0)
        self.assertEqual(report["characters"]["shared_key_bytes"], 8820)


if __name__ == "__main__":
    unittest.main()
