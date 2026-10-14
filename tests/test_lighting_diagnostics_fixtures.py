"""Source-linked renderer predictions, separate storage accounting and failures."""
import copy
import json
from pathlib import Path
import unittest

from tools.compile_level import ContentError
from tools.compile_mission import cook_mission, validate_stream_bridge

FIXTURES = Path(__file__).parent / "fixtures"
NAMES = ("stream_lighting_single_door", "stream_lighting_multiple_doors")


class LightingDiagnosticsFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cooked = {}
        for name in NAMES:
            scene = json.loads((FIXTURES / (name + ".json")).read_bytes())
            manifest = json.loads((FIXTURES / (name + ".mission.json")).read_bytes())
            raw, report = cook_mission(manifest, FIXTURES)
            cls.cooked[name] = scene, manifest, raw, report

    def test_single_and_independent_doors_predict_actual_shared_policy_paths(self):
        for name, doors, mode, reason in ((NAMES[0], 1, "inline_two_state", "inline_payload"),
                                         (NAMES[1], 2, "live_night", "independent_doors")):
            with self.subTest(name=name):
                scene, manifest, raw, report = self.cooked[name]
                validate_stream_bridge(report)
                policy = report["renderer_diagnostics"]
                self.assertEqual(report["chunk_version"], 4)
                self.assertEqual(policy["basis"], "cooked_prediction")
                self.assertEqual(policy["door_object_count"], doors)
                self.assertEqual(len(set(policy["door_object_ids"])), doors)
                self.assertEqual({row["mode"] for row in policy["model_entries"]}, {mode})
                self.assertEqual({row["reason"] for row in policy["model_entries"]}, {reason})
                self.assertEqual({row["change_mode"] for row in policy["model_entries"]}, {mode})
                self.assertEqual(policy["identity"]["package_sha256"], report["package_sha256"])
                self.assertEqual(policy["identity"]["renderer_source_sha256"], report["source_sha256"])
                self.assertIsNone(policy["identity"]["build_id"])
                self.assertIsNone(policy["actual"])

    def test_every_maximum_identifies_regions_groups_and_source_objects(self):
        scene, manifest, raw, report = self.cooked[NAMES[1]]
        source_ids = {row["id"] for row in scene["entities"]}
        diagnostics = report["bridge_diagnostics"]
        self.assertTrue(diagnostics["valid"])
        self.assertEqual(diagnostics["failures"], [])
        for metric, row in diagnostics["maxima"].items():
            if row["cap"] is not None:
                self.assertGreaterEqual(row["cap"], row["usage"], metric)
            else:
                self.assertIn(metric, ("renderer_night_cache_bytes", "chunk_total_bytes"))
                self.assertGreaterEqual(row["usage"], 0)
            self.assertTrue(row["witnesses"], metric)
            for witness in row["witnesses"]:
                self.assertTrue(witness["anchor"], metric)
                self.assertTrue(witness["source_regions"], metric)
                self.assertTrue(witness["contributors"], metric)
                self.assertTrue(all(item["id"] in source_ids for item in witness["contributors"]), metric)
                for group in witness["groups"]:
                    self.assertTrue(group["id"])
                    self.assertTrue(set(group["source_entity_ids"]) <= source_ids)
        mapping = report["runtime_debug_map"]
        self.assertEqual(mapping["renderer_diagnostics"], report["renderer_diagnostics"])
        self.assertEqual(mapping["bridge_diagnostics"], diagnostics)

    def test_embedded_rgba_is_a_package_subset_host_cache_is_excluded_unknowns_stay_null(self):
        for name in NAMES:
            with self.subTest(name=name):
                scene, manifest, raw, report = self.cooked[name]
                memory = report["bridge_diagnostics"]["memory"]
                self.assertEqual(memory["basis"], "cooked_estimate")
                self.assertEqual(memory["rom_package_bytes"], len(raw))
                self.assertGreater(memory["embedded_rgba_in_package_bytes"], 0)
                self.assertLess(memory["embedded_rgba_in_package_bytes"], len(raw))
                self.assertGreater(memory["host_worker_cache_bytes"], 0)
                self.assertEqual(memory["standalone_lighting_rom_bytes"], 0)
                self.assertEqual(memory["known_target_resident_bytes"], sum(memory[field] for field in (
                    "directory_resident_bytes", "slot_buffer_resident_bytes", "bootstrap_geometry_resident_bytes",
                    "bootstrap_rgba_resident_bytes", "scene_banks_rgba_resident_bytes", "renderer_night_cache_peak_bytes")))
                self.assertIsNone(memory["total_target_resident_bytes"])
                self.assertIsNone(memory["total_target_rom_bytes"])
                self.assertTrue(memory["unknown_target_costs"])
        self.assertEqual(self.cooked[NAMES[0]][3]["bridge_diagnostics"]["memory"]["renderer_night_cache_peak_bytes"], 0)
        self.assertGreater(self.cooked[NAMES[1]][3]["bridge_diagnostics"]["memory"]["renderer_night_cache_peak_bytes"], 0)

    def test_over_capacity_portable_cook_retains_actionable_source_witnesses(self):
        scene, manifest, _, _ = self.cooked[NAMES[0]]
        scene, manifest = copy.deepcopy(scene), copy.deepcopy(manifest)
        prototype = next(row for row in scene["entities"] if row["id"] == "courtyard-landmark")
        hub = next(row for row in manifest["regions"] if row["id"] == "hub")
        for index in range(120):
            entity = copy.deepcopy(prototype)
            entity["id"] = f"capacity-box-{index:03d}"
            entity["transform"]["position"] = [-5, 1.4, 0]
            entity["material"] = "hub-stone" if index % 2 else "north-blue"
            scene["entities"].append(entity)
            hub["entities"].append(entity["id"])
        _, report = cook_mission(manifest, FIXTURES, source_document=scene)
        diagnostics = report["bridge_diagnostics"]
        self.assertFalse(diagnostics["valid"])
        failures = [row for row in diagnostics["failures"] if row["metric"] == "triangle_count"]
        self.assertTrue(failures)
        self.assertTrue(all(row["usage"] > row["cap"] and "hub" in row["source_regions"] for row in failures))
        self.assertTrue(any("capacity-box-000" in {item["id"] for item in row["contributors"]} for row in failures))
        with self.assertRaises(ContentError):
            validate_stream_bridge(report)


if __name__ == "__main__":
    unittest.main()
