"""Portable mission cooking, layout and bounded authoring validation."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from tools import compile_mission as mission

ROOT = Path(__file__).resolve().parents[1]


class MissionCookTests(unittest.TestCase):
    def setUp(self):
        # Windows workspace sandbox permits atomic renames in the workspace;
        # its redirected system temp denies os.replace even for owned files.
        (ROOT / "build").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="mission-test-", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.content = self.root / "content"
        self.content.mkdir()
        self.document = json.loads((ROOT / "content/streaming_probe.json").read_text())
        self.manifest = json.loads((ROOT / "content/streaming_probe.mission.json").read_text())
        self.source = self.content / self.manifest["source"]
        (self.content / "models").mkdir()
        shutil.copy2(ROOT / "content/models/guard.obj", self.content / "models/guard.obj")
        self.write_source()

    def write_source(self):
        self.source.write_text(json.dumps(self.document), encoding="utf-8")

    def cook(self):
        return mission.cook_mission(self.manifest, self.content)

    @staticmethod
    def region_rows(package):
        count = struct.unpack_from(">I", package, 12)[0]
        return [struct.unpack_from(">8I6f8s", package, 64 + index * 64) for index in range(count)]

    @staticmethod
    def sections(chunk):
        count = struct.unpack_from(">I", chunk, 12)[0]
        return [struct.unpack_from(">HHIIIII", chunk, 32 + index * 24) for index in range(count)]

    def test_layout_crc_and_source_identity(self):
        package, report = self.cook()
        header = struct.unpack_from(">4sHHIIIIII32s", package)
        magic, version, header_bytes, file_bytes, region_count, entity_count, directory_bytes, entry, crc, identity = header
        self.assertEqual((magic, version, header_bytes), (b"DLM1", 1, 64))
        self.assertEqual((file_bytes, region_count, entity_count, entry), (len(package), 2, 23, 2))
        self.assertEqual(directory_bytes, 64 + 2 * 64 + 23 * 16 + 2 * 4)
        directory = bytearray(package[:directory_bytes])
        directory[28:32] = bytes(4)
        self.assertEqual(crc, zlib.crc32(directory))
        self.assertEqual(identity.hex(), report["content_identity_sha256"])
        self.assertEqual(hashlib.sha256(package).hexdigest(), report["package_sha256"])
        self.assertEqual(package[directory_bytes:mission.align16(directory_bytes)], bytes(mission.align16(directory_bytes) - directory_bytes))
        previous_end, dependency_end = directory_bytes, 0
        for index, row in enumerate(self.region_rows(package)):
            ident, offset, stored, decoded, chunk_crc, first, count, flags, *bounds_reserved = row
            self.assertEqual((ident, stored, flags), (index + 1, decoded, 0))
            self.assertEqual(offset % 16, 0)
            self.assertGreaterEqual(offset, previous_end)
            self.assertEqual(first, dependency_end)
            dependency_end += count
            self.assertEqual(bounds_reserved[-1], bytes(8))
            chunk = package[offset:offset + stored]
            self.assertEqual(zlib.crc32(chunk), chunk_crc)
            ch = struct.unpack_from(">4sHHII3fI", chunk)
            self.assertEqual(ch[:4], (b"DLR1", 1, 32, len(chunk)))
            self.assertEqual(ch[-1], 0)
            section_end, previous_kind = 32 + ch[4] * 24, 0
            for kind, reserved, payload_offset, payload_bytes, record_count, record_bytes, tail in self.sections(chunk):
                self.assertGreater(kind, previous_kind)
                self.assertEqual((reserved, tail), (0, 0))
                self.assertEqual(record_bytes, mission.RECORD_BYTES[kind])
                self.assertEqual(payload_bytes, record_count * record_bytes)
                self.assertEqual(payload_offset % 16, 0)
                self.assertGreaterEqual(payload_offset, section_end)
                self.assertLessEqual(payload_offset + payload_bytes, len(chunk))
                previous_kind, section_end = kind, payload_offset + payload_bytes
            previous_end = offset + stored
        self.assertEqual(previous_end, len(package))
        self.assertEqual(dependency_end, 2)

    def test_home_entities_triangles_and_door_association(self):
        package, report = self.cook()
        entity_start = 64 + 2 * 64
        global_entities = [struct.unpack_from(">IIHHI", package, entity_start + index * 16) for index in range(23)]
        self.assertEqual([row[0] for row in global_entities], list(range(1, 24)))
        entities_by_id = {row["id"]: row for row in report["entity_ids"]}
        door_id = next(row["id"] for row in report["entity_ids"] if row["source_id"] == "probe-door")
        self.assertEqual(global_entities[door_id - 1][2], 2)
        homes, triangle_total, collider_total = [], 0, 0
        materials = {row["id"] for row in report["material_ids"]}
        surfaces = {row["id"] for row in report["surface_ids"]}
        for region in self.region_rows(package):
            ident, offset, size = region[:3]
            chunk = package[offset:offset + size]
            for kind, _, start, _, count, record_bytes, _ in self.sections(chunk):
                if kind == 1:
                    triangle_total += count
                    for index in range(count):
                        row = struct.unpack_from(">9fII", chunk, start + index * record_bytes)
                        self.assertIn(row[-2], materials)
                        self.assertIn(row[-1], surfaces)
                elif kind == 2:
                    collider_total += count
                    for index in range(count):
                        row = struct.unpack_from(">7fI", chunk, start + index * record_bytes)
                        self.assertTrue(all(value > 0 for value in row[3:6]))
                        self.assertIn(row[-1], entities_by_id)
                else:
                    ids = struct.unpack_from(">" + "I" * count, chunk, start)
                    homes.extend(ids)
                    self.assertTrue(all(entities_by_id[value]["home_region"] == ident for value in ids))
        self.assertEqual(sorted(homes), list(range(1, 24)))
        self.assertEqual((triangle_total, collider_total), (192, 17))

    def test_reordered_definitions_partition_and_membership_are_byte_identical(self):
        package, report = self.cook()
        for key in ("assets", "materials", "entities"):
            self.document[key].reverse()
        self.manifest["regions"].reverse()
        for region in self.manifest["regions"]:
            region["entities"].reverse()
            region["neighbors"].reverse()
        self.write_source()
        reordered, reordered_report = self.cook()
        self.assertEqual(package, reordered)
        self.assertEqual(report["source_sha256"], reordered_report["source_sha256"])
        self.assertNotEqual(report["source_file_sha256"], reordered_report["source_file_sha256"])

    def test_report_labels_partial_memory_and_omitted_features(self):
        package, report = self.cook()
        memory = report["memory"]
        self.assertEqual(memory["peak_neighbor_overlap_chunk_bytes"], sum(row["chunk_bytes"] for row in report["regions"]))
        self.assertEqual(memory["directory_plus_peak_neighbor_chunks_bytes"], report["directory_bytes"] + memory["peak_neighbor_overlap_chunk_bytes"])
        self.assertIsNone(memory["renderer_bytes"])
        self.assertIsNone(memory["audio_bytes"])
        self.assertIn("probe-guard", report["omissions"]["nonstatic_visual_entities"])
        self.assertEqual(report["package_bytes"], len(package))

    def test_entity_coverage_unknown_and_duplicate_home(self):
        original = copy.deepcopy(self.manifest)
        for change in ("missing", "duplicate", "unknown"):
            with self.subTest(change=change):
                self.manifest = copy.deepcopy(original)
                if change == "missing":
                    self.manifest["regions"][0]["entities"].remove("probe-spawn")
                elif change == "duplicate":
                    self.manifest["regions"][1]["entities"].append("probe-spawn")
                else:
                    self.manifest["regions"][0]["entities"].append("missing-source-entity")
                with self.assertRaises(ValueError):
                    self.cook()

    def test_partition_shape_and_dependency_references(self):
        original = copy.deepcopy(self.manifest)
        cases = [dict(original, version=True), dict(original, regions=[]), dict(original, typo=1),
                 dict(original, entry_region="missing"), dict(original, regions=[original["regions"][0]] * 2)]
        for neighbors in (["missing"], ["west"], ["east", "east"], [1]):
            candidate = copy.deepcopy(original)
            candidate["regions"][0]["neighbors"] = neighbors
            cases.append(candidate)
        for candidate in cases:
            with self.subTest(manifest=candidate), self.assertRaises(ValueError):
                mission.cook_mission(candidate, self.content)

    def test_bounds_cover_static_geometry_colliders_and_markers(self):
        # Floor origin/markers still fit; its edge extending to x=-8 is checked.
        self.manifest["regions"][0]["bounds"][0][0] = -7.99
        with self.assertRaisesRegex(ValueError, "bounds do not cover.*static geometry"):
            self.cook()
        self.manifest["regions"][0]["bounds"][0][0] = -8
        self.manifest["regions"][0]["bounds"][1][0] = 0
        with self.assertRaisesRegex(ValueError, "bounds do not cover.*collider"):
            self.cook()
        self.manifest["regions"][0]["bounds"][1][0] = 0.11
        self.manifest["regions"][0]["bounds"][1][1] = 1.5
        with self.assertRaisesRegex(ValueError, "bounds do not cover.*position"):
            self.cook()

    def test_bounds_must_be_finite_and_ordered(self):
        for bounds in ([[0, 0, 0], [0, 1, 1]], [[0, 0, 0], [-1, 1, 1]], [[0, 0, float("nan")], [1, 1, 1]]):
            with self.subTest(bounds=bounds), self.assertRaises(ValueError):
                manifest = copy.deepcopy(self.manifest)
                manifest["regions"][0]["bounds"] = bounds
                mission.cook_mission(manifest, self.content)

    def test_rotated_collider_bounds_are_checked(self):
        # A collision-only rotated thin box has corners outside a narrow region.
        self.document["entities"].append(dict(id="rotated-box", kind="static", transform=dict(position=[-2, 3, 0], rotation=[0, 45, 0], scale=[1, 1, 1]),
                                              collider=dict(shape="box", center=[0, 0, 0], half_size=[0.1, 0.5, 3])))
        self.manifest["regions"][0]["entities"].append("rotated-box")
        self.manifest["regions"][0]["bounds"][1][1] = 4
        self.write_source()
        with self.assertRaisesRegex(ValueError, "bounds do not cover rotated-box collider"):
            self.cook()

    def test_source_and_dependency_paths_remain_inside_content(self):
        for source in ("../outside.json", "C:/outside.json", "\\outside.json", "/outside.json", "sub\\file.json", "missing.json"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                manifest = dict(self.manifest, source=source)
                mission.cook_mission(manifest, self.content)
        (self.root / "outside.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 0 1\nf 1 2 3\n")
        for uri in ("../outside.obj", "sub/../../outside.obj", "C:/outside.obj", "sub\\outside.obj"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                self.document["assets"][0] = dict(id="probe-box", uri=uri)
                self.write_source()
                self.cook()

    def test_mesh_dependencies_are_hashed_and_content_affects_identity(self):
        mesh = self.content / "probe.obj"
        # A tiny upright wall uses the same model for source actors as well.
        mesh.write_text("v 0 0 0\nv 0.25 0 0\nv 0 0.25 0\nf 1 2 3\n")
        self.document["assets"][0] = dict(id="probe-box", uri="probe.obj")
        self.write_source()
        package, report = self.cook()
        self.assertIn(dict(path="probe.obj", sha256=hashlib.sha256(mesh.read_bytes()).hexdigest()), report["content_dependencies"])
        self.assertEqual([row["path"] for row in report["content_dependencies"]], ["models/guard.obj", "probe.obj"])
        mesh.write_text(mesh.read_text() + "# changed source\n")
        changed, _ = self.cook()
        self.assertNotEqual(package, changed)

    def test_external_scale_study_stays_protected(self):
        self.document["authoring_profile"] = "external-scale-study"
        self.write_source()
        with self.assertRaisesRegex(ValueError, "authoring-only source study"):
            self.cook()

    def test_normal_runtime_limits_still_apply(self):
        self.document["entities"] += [dict(id=f"extra-light-{index}", kind="light", transform=dict(position=[-1, 2, 0], rotation=[0, 0, 0], scale=[1, 1, 1]), radius=5, intensity=0.5) for index in range(16)]
        self.write_source()
        with self.assertRaisesRegex(ValueError, "expected 1-16 lights"):
            self.cook()

    def test_unsupported_inputs_fail_explicitly(self):
        original = copy.deepcopy(self.document)
        for feature in ("character", "texture", "packs"):
            self.document = copy.deepcopy(original)
            if feature == "character":
                self.document["assets"][0]["type"] = "character"
            elif feature == "texture":
                self.document["materials"][0]["texture"] = dict(uri="missing.png")
            else:
                self.document["asset_packs"] = ["missing-pack.json"]
            self.write_source()
            with self.subTest(feature=feature), self.assertRaisesRegex(ValueError, "does not support"):
                self.cook()

    def test_cli_atomic_publish_and_failure_preserves_existing_output(self):
        manifest_path, output = self.root / "mission.json", self.root / "output/probe.dlm"
        manifest_path.write_text(json.dumps(self.manifest))
        args = ["--manifest", str(manifest_path), "--output", str(output), "--content-root", str(self.content)]
        with patch("builtins.print"):
            self.assertEqual(mission.main(args), 0)
        original = output.read_bytes()
        self.assertEqual(original[:4], b"DLM1")
        self.assertTrue(output.with_suffix(".report.json").is_file())
        self.assertFalse(list(output.parent.glob("*.tmp")))
        self.manifest["entry_region"] = "missing"
        manifest_path.write_text(json.dumps(self.manifest))
        with patch("builtins.print"):
            self.assertEqual(mission.main(args), 1)
        self.assertEqual(original, output.read_bytes())
        self.assertFalse(list(output.parent.glob("*.tmp")))

    def prepare_bootstrap(self):
        manifest_path = self.content / "probe.mission.json"
        manifest_path.write_text(json.dumps(self.manifest))
        report = mission.prepare_stream_level(manifest_path, self.root / "bootstrap", self.content)
        return report, self.root / "bootstrap/generated/demo_level.h"

    def test_bootstrap_contains_only_shared_dynamic_meshes_and_global_links(self):
        report, header = self.prepare_bootstrap()
        text = header.read_text()
        self.assertNotIn('"west-floor"', text)
        self.assertNotIn('"shared-wall-north"', text)
        self.assertNotIn("static const DlCollider", text)
        self.assertNotIn("dl_collider_objects", text)
        self.assertIn(".colliders=NULL, .collider_count=0", text)
        self.assertIn(".stream=&dl_stream_definition", text)
        self.assertIn('"probe-guard"', text)
        self.assertIn('"probe-exit"', text)
        self.assertIn(".enemy_count=1", text)
        self.assertIn(".object_count=3", text)
        self.assertIn(".test_start_count=2", text)
        self.assertEqual(report["bootstrap"]["models"], ["probe-door", "probe-guard", "probe-loot"])
        self.assertEqual(report["compiled_geometry_bytes"], 1800)
        packaged = self.root / "bootstrap" / report["mission"]["path"]
        self.assertEqual(hashlib.sha256(packaged.read_bytes()).hexdigest(), report["mission"]["sha256"])

    def test_bootstrap_reorder_and_partition_change_affect_fingerprint(self):
        report, header = self.prepare_bootstrap()
        original = header.read_bytes()
        for key in ("assets", "materials", "entities"):
            self.document[key].reverse()
        self.manifest["regions"].reverse()
        for region in self.manifest["regions"]:
            region["entities"].reverse()
        self.write_source()
        reordered, header = self.prepare_bootstrap()
        self.assertEqual(original, header.read_bytes())
        self.assertEqual(report["source_sha256"], reordered["source_sha256"])
        self.manifest["regions"][0]["bounds"][1][1] = 4.1
        changed, _ = self.prepare_bootstrap()
        self.assertNotEqual(report["source_sha256"], changed["source_sha256"])

    def test_generated_bootstrap_compiles_against_runtime_contract(self):
        report, header = self.prepare_bootstrap()
        compiler = shutil.which("gcc") or next((str(path) for path in [Path("C:/ProgramData/mingw64/mingw64/bin/gcc.exe"), Path("C:/msys64/ucrt64/bin/gcc.exe")] if path.is_file()), None)
        if not compiler:
            self.skipTest("Host GCC unavailable")
        source = self.root / "check.c"
        source_ids = {row["source_id"]: row["id"] for row in report["mission"]["report"]["entity_ids"]}
        source.write_text('#include "' + header.as_posix() + '"\n#include <assert.h>\nint main(void){const DlLevel *l=&dl_demo_level; assert(l->stream); assert(l->collider_count==0); assert(l->model_count==3); assert(l->object_count==3); ' +
                          f'assert(l->stream->model_entity_ids[0]=={source_ids["probe-door"]}); assert(l->stream->enemy_entity_ids[0]=={source_ids["probe-guard"]}); return 0;}}\n')
        executable = self.root / "check.exe"
        env = dict(os.environ, PATH=str(Path(compiler).parent) + os.pathsep + os.environ.get("PATH", ""))
        subprocess.run([compiler, "-std=c17", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "src"), str(source), "-o", str(executable)], check=True, capture_output=True, text=True, env=env)
        subprocess.run([executable], check=True, capture_output=True, env=env)

    def test_one_region_portable_package_is_rejected_by_rom_before_publication(self):
        self.manifest["regions"] = [dict(id="west", bounds=[[-8, -.5, -4], [8, 4, 4]],
                                        entities=[row["id"] for row in self.document["entities"]], neighbors=[])]
        package, report = self.cook()
        self.assertEqual(package[:4], b"DLM1")
        self.assertEqual(len(report["regions"]), 1)
        with self.assertRaisesRegex(ValueError, "exactly 2 regions"):
            self.prepare_bootstrap()
        self.assertFalse((self.root / "bootstrap").exists())

    def test_three_region_portable_package_is_rejected_by_rom_before_publication(self):
        west = next(row for row in self.manifest["regions"] if row["id"] == "west")
        west["entities"].remove("probe-light-west")
        west["neighbors"].append("light-corner")
        self.manifest["regions"].append(dict(id="light-corner", bounds=[[-6, 2, -1], [-4, 4, 1]],
                                              entities=["probe-light-west"], neighbors=["west"]))
        package, report = self.cook()
        self.assertEqual(package[:4], b"DLM1")
        self.assertEqual(len(report["regions"]), 3)
        with self.assertRaisesRegex(ValueError, "exactly 2 regions"):
            self.prepare_bootstrap()
        self.assertFalse((self.root / "bootstrap").exists())

    def test_seventeen_region_material_groups_are_rejected_before_publication(self):
        statics = [row for row in self.document["entities"] if row["kind"] == "static" and "model" in row]
        self.assertEqual(len(statics), 16)
        extra = dict(id="extra-group", kind="static", model="probe-box", material="group-17",
                     transform=dict(position=[-6, .25, -2], rotation=[0, 0, 0], scale=[.1, .1, .1]))
        self.document["entities"].append(extra)
        next(row for row in self.manifest["regions"] if row["id"] == "west")["entities"].append(extra["id"])
        for index, entity in enumerate(statics + [extra], 1):
            ident = f"group-{index:02d}"
            self.document["materials"].append(dict(id=ident, color=[.5, .5, .5, 1]))
            entity["material"] = ident
        self.write_source()
        _, report = self.cook()
        self.assertEqual(sum(len(row["material_ids"]) for row in report["regions"]), 17)
        self.assertLess(sum(row["triangle_count"] for row in report["regions"]), 512)
        with self.assertRaisesRegex(ValueError, "16 nonempty region/material groups"):
            self.prepare_bootstrap()
        self.assertFalse((self.root / "bootstrap").exists())

    def test_rom_bridge_reserves_an_unavailable_region_collider(self):
        _, report = self.cook()
        report["regions"][0]["collider_count"] = 256
        with self.assertRaisesRegex(ValueError, "unavailable-region barrier"):
            mission.validate_stream_bridge(report)


if __name__ == "__main__":
    unittest.main()
