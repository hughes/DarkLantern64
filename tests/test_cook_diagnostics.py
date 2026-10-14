"""Predictions checked against the actual portable policy, plus source witnesses."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools import compile_level, compile_mission, cook_diagnostics, cook_lighting

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"


class CookDiagnosticsTests(unittest.TestCase):
    def test_predictions_match_shared_c_policy_for_all_supported_settings(self):
        driver = r'''
#include "lighting_policy.h"
#include <stdio.h>
int main(void) {
    const DlModelRole roles[]={DL_MODEL_STATIC,DL_MODEL_GUARD,DL_MODEL_OBJECTIVE,DL_MODEL_DOOR};
    uint8_t colors[24]={0};
    for(int night=0;night<2;++night)for(int role=0;role<4;++role)
    for(int animated=0;animated<2;++animated)for(int payload=0;payload<4;++payload)
    for(int doors=0;doors<3;++doors)for(int enabled=0;enabled<2;++enabled)
    for(int standalone=0;standalone<3;++standalone) {
        DlMesh mesh={.index_count=9,.animation=animated?(const struct DlAnimationAsset*)colors:NULL};
        DlModelInstance model={.role=roles[role],.baked_colors={payload&1?colors:NULL,payload&2?colors:NULL}};
        DlLevel level={.models=&model,.meshes=&mesh,.model_count=1,.mesh_count=1,.environment={.enabled=night}};
        DlLightingSettings settings={enabled,standalone==1,standalone==2,(uint32_t)doors};
        DlLightingDecision d=dl_lighting_decision(&level,0,settings);
        printf("%d,%d,%d,%d,%d,%d,%d,%s,%s,%s,%s,%d,%d\n",night,role,animated,payload,doors,enabled,standalone,
            dl_lighting_mode_name(d.mode),dl_lighting_reason_name(d.reason),
            dl_lighting_mode_name(d.change_mode),dl_lighting_reason_name(d.change_reason),d.static_eligible,d.inline_eligible);
    }
    return 0;
}
'''
        compiler, env, _ = cook_lighting.host_compiler()
        with tempfile.TemporaryDirectory(prefix="policy-prediction-", dir=ROOT / "build") as temporary:
            source, binary = Path(temporary) / "policy.c", Path(temporary) / "policy.exe"
            source.write_text(driver)
            cook_lighting.run([compiler, *cook_lighting.FLAGS, "-I" + str(ROOT / "src"),
                source, ROOT / "src/lighting_policy.c", "-o", binary], env)
            output = cook_lighting.run([binary], env).stdout.splitlines()
        self.assertEqual(len(output), 1152)
        for line in output:
            night, role, animated, payload, doors, enabled, standalone, *expected = line.split(",")
            row = cook_diagnostics.lighting_decision(night=bool(int(night)),
                role=["static", "guard", "objective", "door"][int(role)], animated=bool(int(animated)),
                inline_payload=int(payload) == 3, incomplete_inline_payload=int(payload) in (1, 2),
                doors=int(doors), bake_enabled=bool(int(enabled)),
                standalone_status=["unconfigured", "loaded", "rejected"][int(standalone)], triangles=3)
            actual = [row[key] for key in ("mode", "reason", "change_mode", "change_reason")]
            actual += [str(int(row[key])) for key in ("static_eligible", "inline_eligible")]
            self.assertEqual(actual, expected, line)

    def cook_fixture(self, name):
        manifest = json.loads((FIXTURES / (name + ".mission.json")).read_text())
        document = json.loads((FIXTURES / (name + ".json")).read_text())
        package, report = compile_mission.cook_mission(manifest, FIXTURES)
        return manifest, document, package, report

    def test_second_global_door_changes_embedded_prediction_and_reports_cause(self):
        _, _, _, single = self.cook_fixture("stream_lighting_single_door")
        _, _, _, multiple = self.cook_fixture("stream_lighting_multiple_doors")
        for report, mode, reason, doors in ((single, "inline_two_state", "inline_payload", 1),
                                           (multiple, "live_night", "independent_doors", 2)):
            diagnostic = report["renderer_diagnostics"]
            self.assertEqual(diagnostic["door_object_count"], doors)
            self.assertTrue(report["bridge_diagnostics"]["valid"])
            static = [row for row in diagnostic["model_entries"] if row["role"] == "static"]
            self.assertTrue(static)
            self.assertTrue(all(row["inline_payload"] and (row["mode"], row["reason"]) == (mode, reason) for row in static))
            self.assertTrue(all(row["source_entity_ids"] and row["group_id"] for row in static))
            self.assertEqual(diagnostic["identity"]["renderer_source_sha256"], report["source_sha256"])
            self.assertEqual(report["runtime_debug_map"]["renderer_diagnostics"], diagnostic)
            self.assertIsNone(diagnostic["actual"])
        self.assertNotEqual(single["renderer_diagnostics"]["identity"], multiple["renderer_diagnostics"]["identity"])

    def test_collects_all_failures_with_editable_working_set_witnesses(self):
        _, _, package, report = self.cook_fixture("stream_lighting_single_door")
        # Preserve valid portable bytes while lowering many adapter capacities
        # through a modified report, as caller-side inspection tools can do.
        self.assertTrue(package.startswith(b"DLM1"))
        report["regions"][0].update(triangle_count=2000, collider_count=500, floor_count=129, chunk_bytes=65537)
        report["directory_bytes"] = 65537
        diagnostic = cook_diagnostics.bridge_report(report)
        metrics = {row["metric"] for row in diagnostic["failures"]}
        self.assertTrue({"directory_bytes", "triangle_count", "floor_count", "collision_records", "chunk_bytes"} <= metrics)
        for row in diagnostic["failures"]:
            self.assertTrue(row["anchor"] and row["source_regions"] and row["contributors"])
            self.assertGreater(row["usage"], row["cap"])
        for metric, maximum in diagnostic["maxima"].items():
            self.assertTrue(maximum["witnesses"], metric)
            self.assertTrue(all(row["anchor"] and row["source_regions"] for row in maximum["witnesses"]), metric)
        with self.assertRaisesRegex(ValueError, "65536 bytes"):
            compile_mission.validate_stream_bridge(report)

    def test_memory_counts_embedded_colors_once_and_excludes_host_cache(self):
        _, _, _, report = self.cook_fixture("stream_lighting_single_door")
        memory = report["bridge_diagnostics"]["memory"]
        self.assertEqual(memory["embedded_rgba_in_package_bytes"], report["lighting"]["mission_embedded_rgba_bytes"])
        self.assertGreater(memory["host_worker_cache_bytes"], memory["embedded_rgba_in_package_bytes"])
        keys = ("directory_resident_bytes", "slot_buffer_resident_bytes", "bootstrap_geometry_resident_bytes",
                "bootstrap_rgba_resident_bytes", "scene_banks_rgba_resident_bytes", "renderer_night_cache_peak_bytes")
        self.assertEqual(memory["known_target_resident_bytes"], sum(memory[key] for key in keys))
        self.assertEqual(memory["standalone_lighting_rom_bytes"], 0)
        self.assertEqual(memory["known_target_rom_bytes"], memory["rom_package_bytes"] +
            memory["bootstrap_geometry_resident_bytes"] + memory["bootstrap_rgba_resident_bytes"])
        self.assertIsNone(memory["total_target_resident_bytes"])
        self.assertIsNone(memory["total_target_rom_bytes"])
        self.assertTrue(memory["unknown_target_costs"])

    def test_canonical_identity_matches_header_and_survives_definition_reordering(self):
        manifest, document, _, report = self.cook_fixture("stream_lighting_single_door")
        state = compile_level.validate(document, FIXTURES, streaming=True)
        header, _ = compile_mission.bootstrap_header(document, state, report, "rom:/mission.dlm")
        self.assertIn('.renderer_source_sha256="' + report["source_sha256"] + '"', header)
        reordered = copy.deepcopy(document)
        for key in ("assets", "materials", "entities"):
            reordered[key].reverse()
        self.assertEqual(cook_diagnostics.source_identity(reordered), report["source_sha256"])
        changed = copy.deepcopy(document)
        changed["entities"][0]["transform"]["position"][1] += .1
        self.assertNotEqual(cook_diagnostics.source_identity(changed), report["source_sha256"])
        self.assertEqual(manifest["source"], report["source"])

    def test_explicit_build_flag_recomputes_prediction_without_mutating_identity(self):
        _, _, _, report = self.cook_fixture("stream_lighting_single_door")
        before = report["renderer_diagnostics"]
        disabled = cook_diagnostics.apply_renderer_settings(before, bake_enabled=False)
        self.assertEqual(disabled["identity"], before["identity"])
        self.assertTrue(before["settings"]["bake_enabled"])
        self.assertFalse(disabled["settings"]["bake_enabled"])
        self.assertTrue(all((row["mode"], row["reason"]) == ("live_night", "disabled_build")
                            for row in disabled["model_entries"] if row["static_eligible"]))
        self.assertIn("inline_two_state", before["mode_counts"])
        self.assertNotIn("inline_two_state", disabled["mode_counts"])
        self.assertTrue(all(row["change_mode"] == "runtime_two_state" for row in disabled["model_entries"] if row["static_eligible"]))

    def test_native_bootstrap_json_retains_target_material_colors_and_inline_payload(self):
        _, document, _, report = self.cook_fixture("stream_lighting_single_door")
        state = compile_level.validate(document, FIXTURES, streaming=True)
        play = compile_mission.stream_play_package(document, state, report, "fixture.dlm")
        self.assertEqual(play["stream"]["renderer_source_sha256"], report["source_sha256"])
        baked = {row["id"]: row for row in report["lighting"]["bootstrap_models"]}
        by_id = {row["id"]: row for row in play["stream_models"]}
        for entity in state["models"]:
            if entity["kind"] == "static":
                continue
            model = by_id[entity["id"]]
            material = state["materials"][entity["material"]]
            self.assertEqual(model["color"], [round(value*255) for value in material["color"]])
            self.assertEqual(model["emissive"], [round(value*255) for value in material.get("emissive", [0,0,0])])
            self.assertEqual(model["double_sided"], material.get("double_sided", True))
            if entity["id"] in baked:
                self.assertEqual(model["baked_colors"], [baked[entity["id"]][key] for key in ("closed_rgba", "open_rgba")])
                expected = len(state["meshes"][entity["model"]]["indices"])//3*24
                self.assertTrue(all(len(bytes.fromhex(value)) == expected for value in model["baked_colors"]))
            else:
                self.assertNotIn("baked_colors", model)
        altered = copy.deepcopy(report)
        row = altered["lighting"]["bootstrap_models"][0]
        color = bytearray.fromhex(row["closed_rgba"])
        color[0] ^= 1
        row["closed_rgba"] = color.hex()
        changed = compile_mission.stream_play_package(document, state, altered, "fixture.dlm")
        self.assertNotEqual(play["fingerprint"], changed["fingerprint"])
        self.assertEqual(play["stream"]["package_sha256"], changed["stream"]["package_sha256"])

    def test_uncapped_memory_peaks_keep_renderer_and_source_selection_witnesses(self):
        _, _, _, report = self.cook_fixture("stream_lighting_multiple_doors")
        bridge = report["bridge_diagnostics"]
        source = {(row["anchor"], tuple(row["regions"])): row for row in report["working_sets"]}
        for row in bridge["working_sets"]:
            original = source[row["anchor"], tuple(row["regions"])]
            self.assertEqual(row["renderer"]["model_entries"], original["renderer"]["model_entries"])
            self.assertEqual(row["scene_triangles"], row["usage"]["scene_triangles"])
            self.assertEqual(row["groups"], original["groups"])
            self.assertEqual(row["contributors"], original["contributors"])
        for metric in ("renderer_night_cache_bytes", "chunk_total_bytes"):
            maximum = bridge["maxima"][metric]
            self.assertIsNone(maximum["cap"])
            self.assertEqual(maximum["usage"], max(row["usage"][metric] for row in bridge["working_sets"]))
            for witness in maximum["witnesses"]:
                self.assertTrue(witness["anchor"] and witness["source_regions"] and witness["contributors"])
                self.assertIsNone(witness["cap"])
                self.assertTrue(all(group["source_entity_ids"] for group in witness["groups"]))


if __name__ == "__main__":
    unittest.main()
