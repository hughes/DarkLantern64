"""Portable mission cooking, layout and bounded authoring validation."""
import copy
import hashlib
import json
from pathlib import Path
import struct
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
        self.assertEqual((file_bytes, region_count, entity_count, entry), (len(package), 2, 7, 2))
        self.assertEqual(directory_bytes, 64 + 2 * 64 + 7 * 16 + 2 * 4)
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
        global_entities = [struct.unpack_from(">IIHHI", package, entity_start + index * 16) for index in range(7)]
        self.assertEqual([row[0] for row in global_entities], list(range(1, 8)))
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
        self.assertEqual(sorted(homes), list(range(1, 8)))
        self.assertEqual((triangle_total, collider_total), (24, 3))

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
        # Floor origin still fits: its geometry extending to x=-4 must be checked.
        self.manifest["regions"][0]["bounds"][0][0] = -3
        with self.assertRaisesRegex(ValueError, "bounds do not cover.*static geometry"):
            self.cook()
        self.manifest["regions"][0]["bounds"][0][0] = -4
        self.manifest["regions"][0]["bounds"][1][0] = 0
        with self.assertRaisesRegex(ValueError, "bounds do not cover.*collider"):
            self.cook()
        self.manifest["regions"][0]["bounds"][1][0] = 0.1
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
        self.assertEqual(report["content_dependencies"], [dict(path="probe.obj", sha256=hashlib.sha256(mesh.read_bytes()).hexdigest())])
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


if __name__ == "__main__":
    unittest.main()
