"""Build provenance keeps authored debug maps bound to the tested ROM."""
import copy
from pathlib import Path
import tempfile
import unittest

from tools.build import build_runtime_debug_map
from tools.compile_mission import prepare_stream_level
from tools import cook_diagnostics

ROOT = Path(__file__).resolve().parents[1]


class RuntimeTraceBuildTests(unittest.TestCase):
    def test_stream_map_keeps_authored_identity_and_adds_executable_identity(self):
        authored = dict(schema_version=1, package_sha256="a" * 64,
                        source_sha256="b" * 64, content_identity_sha256="c" * 64,
                        regions=[dict(id=1, source_id="courtyard")],
                        transitions=[dict(index=0, id="entry-gate", from_region=1, to_region=2)],
                        entities=[dict(id=3, source_id="front-door", object_index=0,
                                       source_provenance=dict(object_id=472))],
                        colliders=[dict(region=1, record=0, entity_id=3, source_id="front-door")])
        catalog = dict(levels=[dict(id="district", source="content/district.json",
                                   mission_manifest="content/district.mission.json",
                                   content=dict(source_sha256="d" * 64, mission_manifest_sha256="e" * 64,
                                                mission=dict(report=dict(runtime_debug_map=authored))))])
        original = copy.deepcopy(catalog)
        mapped = build_runtime_debug_map(catalog, "f" * 64, "1" * 64)
        self.assertEqual(mapped["build_id"], "f" * 64)
        self.assertEqual(mapped["rom_sha256"], "1" * 64)
        level = mapped["levels"][0]
        self.assertEqual(level["id"], "district")
        self.assertEqual(level["package_sha256"], "a" * 64)
        self.assertEqual(level["source_sha256"], "b" * 64)
        self.assertEqual(level["compiled_source_sha256"], "d" * 64)
        self.assertEqual(level["mission_manifest_sha256"], "e" * 64)
        self.assertEqual(level["transitions"], authored["transitions"])
        self.assertEqual(level["colliders"], authored["colliders"])
        self.assertEqual(level["entities"][0]["source_provenance"]["object_id"], 472)
        self.assertEqual(catalog, original)
        replacement = build_runtime_debug_map(catalog, "2" * 64, "3" * 64)
        self.assertNotEqual(mapped["build_id"], replacement["build_id"])
        self.assertNotEqual(mapped["rom_sha256"], replacement["rom_sha256"])
        self.assertEqual(mapped["levels"], replacement["levels"])

    def test_catalog_with_resident_level_needs_no_mission_package(self):
        catalog = dict(levels=[dict(id="first-room", source="content/first_room.json",
                                   content=dict(source_sha256="a" * 64))])
        mapped = build_runtime_debug_map(catalog, "b" * 64, "c" * 64)
        self.assertEqual(mapped["levels"][0]["id"], "first-room")
        self.assertEqual(mapped["levels"][0]["regions"], [])
        self.assertNotIn("package_sha256", mapped["levels"][0])

    def test_disabled_build_reclassifies_actual_stream_catalog_and_cache_peak_witnesses(self):
        # Use the same nested content shape produced for real ROM bundle cooks,
        # rather than manufacturing top-level fields absent from that pipeline.
        with tempfile.TemporaryDirectory(prefix="debug-map-lighting-", dir=ROOT / "build") as temporary:
            content = prepare_stream_level(ROOT / "tests/fixtures/stream_lighting_single_door.mission.json", temporary)
        catalog = dict(levels=[dict(id="lighting-fixture", source="content/lighting.json",
            mission_manifest="content/lighting.mission.json", content=content)])
        original = copy.deepcopy(catalog)
        normal = build_runtime_debug_map(catalog, "a" * 64, "b" * 64, {"bake_enabled": True})
        disabled = build_runtime_debug_map(catalog, "c" * 64, "d" * 64, {"bake_enabled": False})
        normal_level, disabled_level = normal["levels"][0], disabled["levels"][0]
        source = content["mission"]["report"]
        self.assertEqual(catalog, original)
        self.assertEqual(normal_level["package_sha256"], source["package_sha256"])
        self.assertEqual(disabled_level["package_sha256"], source["package_sha256"])
        self.assertEqual(disabled_level["renderer_source_sha256"], source["source_sha256"])
        self.assertNotEqual(disabled_level["renderer_source_sha256"], disabled_level["compiled_source_sha256"])
        self.assertEqual(disabled_level["mission_manifest_sha256"], content["mission_manifest_sha256"])
        policy = disabled_level["renderer_diagnostics"]
        self.assertEqual(policy["identity"]["package_sha256"], source["package_sha256"])
        self.assertEqual(policy["identity"]["build_id"], "c" * 64)
        self.assertEqual(policy["identity"]["rom_sha256"], "d" * 64)
        self.assertFalse(policy["settings"]["bake_enabled"])
        self.assertFalse(disabled_level["render_options"]["bake_enabled"])
        self.assertTrue(all(row["mode"] == "inline_two_state" for row in normal_level["renderer_diagnostics"]["model_entries"]))
        self.assertTrue(all((row["mode"], row["reason"]) == ("live_night", "disabled_build") for row in policy["model_entries"]))
        self.assertTrue(all(row["change_mode"] == "runtime_two_state" for row in policy["model_entries"]))
        before, after = normal_level["bridge_diagnostics"], disabled_level["bridge_diagnostics"]
        self.assertEqual(before["memory"]["renderer_night_cache_peak_bytes"], 0)
        for row in after["working_sets"]:
            cache = 48 * row["usage"]["scene_triangles"]
            self.assertEqual(row["renderer"]["renderer_night_cache_bytes"], cache)
            self.assertEqual(row["usage"]["renderer_night_cache_bytes"], cache)
            self.assertTrue(all(item["reason"] == "disabled_build" for item in row["renderer"]["model_entries"]))
        peak = max(row["usage"]["renderer_night_cache_bytes"] for row in after["working_sets"])
        self.assertGreater(peak, 0)
        self.assertEqual(after["memory"]["renderer_night_cache_peak_bytes"], peak)
        self.assertEqual(after["memory"]["known_target_resident_bytes"], before["memory"]["known_target_resident_bytes"] + peak)
        maximum = after["maxima"]["renderer_night_cache_bytes"]
        self.assertEqual(maximum["usage"], peak)
        self.assertIsNone(maximum["cap"])
        expected = {(row["anchor"], tuple(row["regions"])) for row in after["working_sets"]
                    if row["usage"]["renderer_night_cache_bytes"] == peak}
        self.assertEqual({(row["anchor"], tuple(row["regions"])) for row in maximum["witnesses"]}, expected)
        self.assertTrue(all(row["usage"] == peak and row["contributors"] and row["groups"] for row in maximum["witnesses"]))
        self.assertEqual(after["maxima"]["chunk_total_bytes"], before["maxima"]["chunk_total_bytes"])
        self.assertEqual(after["memory"]["known_target_rom_bytes"], before["memory"]["known_target_rom_bytes"])
        self.assertIsNone(after["memory"]["total_target_resident_bytes"])

    def test_resident_build_uses_canonical_renderer_identity_and_explicit_disabled_flag(self):
        entries = [dict(entity_id="wall", source_entity_ids=["wall"], role="static", inline_payload=False,
            environment_enabled=True, animated_mesh=False,
            **cook_diagnostics.lighting_decision(night=True, role="static", triangles=12,
                standalone_status="loaded"))]
        prediction = cook_diagnostics.renderer_report(entries, door_ids=[], standalone_status="loaded",
            identity=dict(renderer_source_sha256="a"*64, source_sha256="b"*64, source_file_sha256="c"*64,
                          package_sha256=None, build_id=None))
        catalog = dict(levels=[dict(id="resident", source="content/resident.json",
                                   content=dict(source_sha256="b"*64, renderer_diagnostics=prediction))])
        original = copy.deepcopy(catalog)
        mapped = build_runtime_debug_map(catalog, "d"*64, "e"*64, {"bake_enabled": False})
        level = mapped["levels"][0]
        self.assertEqual(level["renderer_source_sha256"], "a"*64)
        self.assertEqual(level["compiled_source_sha256"], "b"*64)
        self.assertNotIn("package_sha256", level)
        identity = level["renderer_diagnostics"]["identity"]
        self.assertEqual(identity["build_id"], "d"*64)
        self.assertEqual(identity["rom_sha256"], "e"*64)
        self.assertIsNone(identity["package_sha256"])
        case = level["renderer_diagnostics"]["cases"][0]
        self.assertEqual((case["mode"], case["reason"], case["models"], case["triangles"]),
                         ("live_night", "disabled_build", 1, 12))
        self.assertEqual(catalog, original)


if __name__ == "__main__":
    unittest.main()
