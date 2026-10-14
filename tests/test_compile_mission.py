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
        # Keep the legacy v1/v2/v3 decoder fixtures scalar. Dedicated night
        # fixtures below exercise the v4 native lighting contract.
        self.environment = self.document.pop("environment", None)
        self.manifest = json.loads((ROOT / "content/streaming_probe.mission.json").read_text())
        self.source = self.content / self.manifest["source"]
        (self.content / "models").mkdir()
        shutil.copy2(ROOT / "content/models/guard.obj", self.content / "models/guard.obj")
        self.write_source()

    def write_source(self):
        self.source.write_text(json.dumps(self.document), encoding="utf-8")

    def test_atomic_write_with_long_hash_addressed_destination(self):
        parent = self.root / ("p" * max(1, 170 - len(str(self.root.resolve())) - 1))
        destination = parent / ("mission-" + "a" * 64 + ".dlm")
        self.assertLess(len(str(destination.resolve())), 260)
        mission.atomic_write(destination, b"new package")
        self.assertEqual(destination.read_bytes(), b"new package")
        self.assertEqual(list(parent.iterdir()), [destination])

    def authored_five_regions(self,count=5):
        self.document["authoring_profile"] = "streamed-mission"
        first, second = self.manifest["regions"]
        first.update(id="r00", neighbors=["r01"], working_set=["r00"],
                     activation_bounds=[[-8,-.5,-4],[.05,4,4]])
        second.update(id="r01", neighbors=["r00","r02"], working_set=["r01"],
                      activation_bounds=[[-.05,-.5,-4],[8.05,4,4]])
        regions = [first, second]
        for index in range(2,count):
            ident, start = f"r{index:02d}", (index-1)*8
            homes = []
            for model in range(40):
                entity_id = f"{ident}-mesh-{model:03d}"
                self.document["entities"].append(dict(id=entity_id,kind="static",model="probe-box",material="probe-east",
                    transform=dict(position=[start+4,2,0],rotation=[0,0,0],scale=[.1,.1,.1])))
                homes.append(entity_id)
            for box in range(130):
                entity_id = f"{ident}-box-{box:03d}"
                self.document["entities"].append(dict(id=entity_id,kind="static",
                    transform=dict(position=[start+4,1,0],rotation=[0,0,0],scale=[1,1,1]),
                    collider=dict(shape="box",center=[0,0,0],half_size=[.01,.01,.01])))
                homes.append(entity_id)
            neighbors = [f"r{index-1:02d}"] + ([f"r{index+1:02d}"] if index<count-1 else [])
            regions.append(dict(id=ident,bounds=[[start-.1,-.5,-4],[start+8.1,4,4]],entities=homes,
                neighbors=neighbors,working_set=[ident],activation_bounds=[[start-.05,-.5,-4],[start+8.05,4,4]]))
        gates = []
        for index, x in enumerate(range(0,(count-1)*8,8)):
            for source,target in ((index,index+1),(index+1,index)):
                gates.append(dict(id=f"gate-{source}-{target}",**{"from":f"r{source:02d}","to":f"r{target:02d}"},
                    bounds=[[x-.25,-.5,-1],[x+.25,4,1]]))
        self.manifest.update(entry_region="r00",visibility="authored-sets",regions=regions,transitions=gates)
        self.write_source()

    def test_authored_five_region_world_validates_working_sets_instead_of_total_source(self):
        self.authored_five_regions()
        package, report = self.cook()
        self.assertGreater(sum(row["triangle_count"] for row in report["regions"]),1440)
        self.assertGreater(sum(row["collider_count"] for row in report["regions"]),400)
        self.assertGreater(len(report["entity_ids"]),256)
        mission.validate_stream_bridge(report)
        self.assertEqual(report["max_resident_regions"],2)
        self.assertTrue(all(len(row["regions"])<=2 for row in report["working_sets"]))
        path = self.content/"five.mission.json"
        path.write_text(json.dumps(self.manifest))
        output = self.root/"five-output"
        prepared = mission.prepare_stream_level(path,output,self.content)
        header = (output/"generated/demo_level.h").read_text()
        self.assertIn("DL_STREAM_POLICY_AUTHORED",header)
        self.assertIn(".max_resident_regions=2",header)
        self.assertEqual(prepared["bootstrap"]["static_model_count"],0)
        self.assertEqual((output/prepared["mission"]["path"]).read_bytes(),package)

    def test_authored_working_set_gate_rejects_oversized_transition_union(self):
        self.authored_five_regions(count=6)
        self.manifest["regions"][1]["working_set"] = ["r01","r02","r03","r04","r05"]
        _,report = self.cook()
        with self.assertRaisesRegex(ValueError,"5 resident region slots"):
            mission.validate_stream_bridge(report)

    def test_runtime_debug_map_resolves_package_ids_gate_indices_and_blocker_ordinals(self):
        self.authored_five_regions(count=3)
        door = next(row for row in self.document["entities"] if row["id"] == "probe-door")
        door["source"] = dict(game="Thief", object_id=471, source_scene="original.mis")
        gate = next(row for row in self.manifest["transitions"] if row["id"] == "gate-0-1")
        gate["door"] = "probe-door"
        self.write_source()
        package, report = self.cook()
        mapped = report["runtime_debug_map"]
        self.assertEqual(mapped["package_sha256"], hashlib.sha256(package).hexdigest())
        self.assertEqual(mapped["content_identity_sha256"], package[32:64].hex())
        door_row = next(row for row in mapped["entities"] if row["source_id"] == "probe-door")
        self.assertEqual(door_row["source_provenance"]["object_id"], 471)
        self.assertEqual(mapped["object_entity_ids"][door_row["object_index"]], door_row["id"])
        for index, gate_row in enumerate(mapped["transitions"]):
            self.assertEqual(gate_row["index"], index)
            self.assertEqual(gate_row["id"], report["transitions"][index]["id"])
        self.assertEqual(next(row for row in mapped["transitions"] if row["id"] == "gate-0-1")["door_entity_id"], door_row["id"])
        for region in mapped["regions"]:
            self.assertIn("activation_bounds", region)
            self.assertIn("working_mask", region)
            chunk = package[region["file_offset"]:region["file_offset"] + region["chunk_bytes"]]
            _, _, start, size, _, _, _ = next(section for section in self.sections(chunk) if section[0] == 2)
            colliders = chunk[start:start + size]
            for row in (row for row in mapped["colliders"] if row["region"] == region["id"]):
                self.assertEqual(row["entity_id"], struct.unpack_from(">I", colliders, row["record"] * 32 + 28)[0])
        path = self.content / "mapped.mission.json"
        path.write_text(json.dumps(self.manifest))
        output = self.root / "mapped-output"
        prepared = mission.prepare_stream_level(path, output, self.content)
        self.assertEqual(json.loads((output / "generated/runtime_debug_map.json").read_text()), mapped)
        self.assertIn('.package_sha256="' + prepared["mission"]["sha256"] + '"', (output / "generated/demo_level.h").read_text())

    def test_authored_rejects_bad_activation_gate_or_unreachable_region(self):
        self.authored_five_regions()
        original = copy.deepcopy(self.manifest)
        self.manifest["regions"][0]["activation_bounds"] = [[0,0,0],[0,1,1]]
        with self.assertRaisesRegex(ValueError,"activation bounds"):
            self.cook()
        self.manifest = copy.deepcopy(original)
        self.manifest["transitions"][0]["bounds"] = [[100,0,0],[101,1,1]]
        with self.assertRaisesRegex(ValueError,"intersect both activation"):
            self.cook()
        self.manifest = copy.deepcopy(original)
        self.manifest["transitions"] = [row for row in self.manifest["transitions"] if row["to"]!="r04"]
        with self.assertRaisesRegex(ValueError,"reachable"):
            self.cook()

    def test_single_authored_region_after_reassignment_cooks_without_gates(self):
        # Deleting one of two regions transfers its homes and removes the pair.
        # The remaining region still owns both rooms and their global objects.
        bounds = [[-8.1, -.5, -4.1], [8.1, 4.1, 4.1]]
        self.manifest.update(entry_region="whole", visibility="authored-sets", transitions=[],
                             regions=[dict(id="whole", bounds=bounds, activation_bounds=bounds,
                                           entities=[row["id"] for row in self.document["entities"]],
                                           neighbors=[], working_set=["whole"])])
        package, report = self.cook()
        mission.validate_stream_bridge(report)
        self.assertEqual(report["transitions"], [])
        self.assertEqual(report["max_resident_regions"], 1)
        self.assertEqual(report["policy_regions"][0]["working_mask"], 1)
        self.assertEqual(report["working_sets"][0]["barrier_count"], 0)
        prepared, header = self.prepare_bootstrap()
        text = header.read_text()
        self.assertIn(".transitions=NULL, .transition_count=0", text)
        self.assertNotIn("dl_stream_transitions[]", text)
        self.assertEqual((self.root / "bootstrap" / prepared["mission"]["path"]).read_bytes(), package)
        native = json.loads((header.parent / "play.json").read_bytes())
        self.assertEqual(native["stream"]["transitions"], [])
        self.assertEqual(native["stream"]["max_resident_regions"], 1)
        # The generated descriptor must be standard C, not a zero-sized array.
        unit = self.root / "single-header.c"
        unit.write_text('#include "demo_level.h"\nint main(void) { return dl_demo_level.stream->transition_count != 0; }\n')
        subprocess.run([os.environ.get("CC", "gcc"), "-std=c17", "-pedantic-errors", "-Wall", "-Wextra", "-Werror",
                        "-I" + str(ROOT / "src"), "-I" + str(header.parent), str(unit), "-o", str(self.root / "single-header.exe")],
                       check=True, capture_output=True, text=True)

    def test_zero_gates_still_rejects_unreachable_multiple_regions(self):
        self.authored_five_regions()
        self.manifest["transitions"] = []
        for region in self.manifest["regions"]:
            region["neighbors"] = []
        with self.assertRaisesRegex(ValueError, "reachable"):
            self.cook()

    def test_v3_exact_floor_records_preserve_transformed_vertices_and_bootstrap_omits_contacts(self):
        self.document["entities"].append(dict(id="ramp-plane",kind="static",
            transform=dict(position=[-4,0,0],rotation=[0,0,0],scale=[2,1,1]),
            floor=dict(vertices=[[0,.05,-2],[.5,.05,-2],[0,.55,-1]])))
        self.manifest["regions"][0]["entities"].append("ramp-plane")
        self.write_source()
        package,report=self.cook()
        self.assertEqual(report["chunk_version"],3)
        self.assertEqual(sum(row["floor_count"] for row in report["regions"]),1)
        row=next(row for row in report["regions"] if row["floor_count"])
        chunk=package[row["file_offset"]:row["file_offset"]+row["chunk_bytes"]]
        section=next(section for section in self.sections(chunk) if section[0]==4)
        self.assertEqual((section[3],section[4],section[5]),(36,1,36))
        points=struct.unpack_from(">9f",chunk,section[2])
        world=[[points[v*3+a]+row["origin"][a] for a in range(3)] for v in range(3)]
        for actual,expected in zip(world,[[-4,.05,-2],[-3,.05,-2],[-4,.55,-1]]):
            for a,b in zip(actual,expected):self.assertAlmostEqual(a,b,places=5)
        path=self.content/"floor.mission.json";path.write_text(json.dumps(self.manifest))
        output=self.root/"floor-output";mission.prepare_stream_level(path,output,self.content)
        header=(output/"generated/demo_level.h").read_text()
        self.assertIn(".floor_count=0",header)
        self.assertNotIn("static const DlFloorTriangle",header)

    def test_authored_contact_budget_includes_gate_barriers_and_floor_triangles(self):
        self.authored_five_regions()
        _,report=self.cook()
        row=next(row for row in report["regions"] if row["source_id"]=="r02")
        row["collider_count"]=152
        row["floor_count"]=120
        with self.assertRaisesRegex(ValueError,r"boxes \+ floors \+ unavailable-region barriers"):
            mission.validate_stream_bridge(report)
        row["floor_count"]=129
        with self.assertRaisesRegex(ValueError,"128 floor triangles"):
            mission.validate_stream_bridge(report)

    def test_floor_target_local_and_reconstructed_precision_are_preflighted(self):
        # Both canonical planes and direct f32 world positions are usable;
        # the additional chunk-local encoding/reconstruction must also be usable.
        self.document["entities"].append(dict(id="small-plane",kind="static",
            transform=dict(position=[1000.00001,0,0],rotation=[0,0,0],scale=[1,1,1]),
            floor=dict(vertices=[[0,0,0],[.001,.00099,0],[0,0,1]])))
        self.manifest["regions"].append(dict(id="far",bounds=[[999.2,-1,-1],[1001,2,2]],entities=["small-plane"],neighbors=[]))
        self.write_source()
        with self.assertRaisesRegex(ValueError,"target reconstructed float precision"):
            self.cook()
        self.document["entities"][-1]["transform"]["position"]=[-4,0,0]
        self.document["entities"][-1]["floor"]["vertices"]=[[0,0,0],[.00001,0,0],[0,0,1]]
        self.manifest["regions"][-1]["bounds"]=[[-8,-1,-1],[1008,2,2]]
        self.write_source()
        with self.assertRaisesRegex(ValueError,"target local float precision"):
            self.cook()

    def test_floor_reconstruction_checks_target_arithmetic_not_only_rounded_vertices(self):
        # Direct source f32 arithmetic and local-wire double arithmetic are
        # usable. Encoded origin [2.1,2.1,-.7] shifts the reconstructed vertices
        # so f32 cancellation makes the runtime gradient exceed its limit.
        points=[[0,0,0],[1.000000238418579,1.314579963684082,1.000000238418579],
                [1.0000003576278687,1.314579725265503,.9999997615814209]]
        self.document['entities'].append(dict(id='arithmetic-plane',kind='static',floor=dict(vertices=points),
            transform=dict(position=[0,0,0],rotation=[0,0,0],scale=[1,1,1])))
        self.manifest['regions'].append(dict(id='thin-plane-region',bounds=[[-.9,-.9,-3.7],[5.1,5.1,2.3]],
                                            entities=['arithmetic-plane'],neighbors=[]))
        self.write_source()
        with self.assertRaisesRegex(ValueError,'target reconstructed float precision.*floor grade'):
            self.cook()
        for point in self.document['entities'][-1]['floor']['vertices']:point[1]*=.5
        self.write_source()
        _,report=self.cook()
        self.assertEqual(sum(row['floor_count'] for row in report['regions']),1)

    def test_authored_door_transition_uses_canonical_id_and_endpoint_home(self):
        self.authored_five_regions()
        self.manifest["transitions"][0]["door"]="probe-door"
        _,report=self.cook()
        self.assertEqual(next(row for row in report["transitions"] if row["id"]=="gate-0-1")["door"],"probe-door")
        self.manifest["transitions"][2]["door"]="probe-door"
        with self.assertRaisesRegex(ValueError,"door home must match"):
            self.cook()

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
        for feature in ("character", "packs"):
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

    def test_native_stream_bootstrap_binds_package_and_has_relocatable_identity(self):
        report, header = self.prepare_bootstrap()
        output = header.parent.parent
        play = json.loads((output / "generated/play.json").read_bytes())
        stream = play["stream"]
        self.assertEqual(play["colliders"], [])
        self.assertEqual(play["floors"], [])
        self.assertEqual(hashlib.sha256(Path(stream["package_path"]).read_bytes()).hexdigest(), stream["package_sha256"])
        self.assertEqual([row["id"] for row in play["stream_models"]], report["bootstrap"]["models"])
        self.assertEqual(stream["object_entity_ids"], report["bootstrap"]["object_entity_ids"])
        document = mission.canonical_source(self.document)
        state = mission.level.validate(document, self.content, streaming=True)
        package, package_report = self.cook()
        moved = mission.stream_play_package(document, state, package_report, "elsewhere/package.dlm")
        self.assertEqual(play["fingerprint"], moved["fingerprint"])
        staged_package, staged_report = mission.cook_mission(self.manifest, self.content, source_document=self.document)
        self.assertEqual(staged_package, package)
        staged = mission.stream_play_package(document, state, staged_report, "stage/package.dlm")
        self.assertEqual(play["fingerprint"], staged["fingerprint"])
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

    def test_bootstrap_manifest_fingerprint_matches_exact_saved_bytes(self):
        path = self.content / "proof.mission.json"
        output = self.root / "manifest-proof"
        source_before = self.source.read_bytes()
        raw = json.dumps(self.manifest).encode("utf-8")
        path.write_bytes(raw)
        first = mission.prepare_stream_level(path, output, self.content)
        self.assertEqual(first["mission_manifest_sha256"], hashlib.sha256(raw).hexdigest())
        reformatted = (json.dumps(self.manifest, indent=2) + "\n").encode("utf-8")
        path.write_bytes(reformatted)
        second = mission.prepare_stream_level(path, output, self.content)
        self.assertEqual(second["mission_manifest_sha256"], hashlib.sha256(reformatted).hexdigest())
        self.assertNotEqual(first["mission_manifest_sha256"], second["mission_manifest_sha256"])
        self.assertEqual(first["mission"]["sha256"], second["mission"]["sha256"])
        self.assertEqual(self.source.read_bytes(), source_before)

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

    def test_night_v4_cooks_both_rgba_states_and_instance_bootstrap_colors(self):
        self.document["environment"] = dict(self.environment, ambient=[.25, .25, .25])
        for entity in self.document["entities"]:
            if entity["kind"] == "light":
                entity["intensity"] = 0
        self.write_source()
        package, report = self.cook()
        self.assertEqual(report["chunk_version"], 4)
        lighting = report["lighting"]
        self.assertEqual(lighting["states"], 2)
        self.assertEqual(lighting["stream_triangles"], 192)
        self.assertEqual(lighting["full_scene_colliders"], 17)
        self.assertEqual(lighting["global_lights"], 2)
        for region in self.region_rows(package):
            chunk = package[region[1]:region[1] + region[2]]
            self.assertEqual(struct.unpack_from(">H", chunk, 4)[0], 4)
            for kind, _, start, _, count, stride, _ in self.sections(chunk):
                if kind != 1:
                    continue
                self.assertEqual(stride, 128)
                for index in range(count):
                    offset = start + index * stride
                    self.assertEqual(chunk[offset + 77:offset + 80], bytes([3, 0, 0]))
                    closed, opened = chunk[offset + 80:offset + 104], chunk[offset + 104:offset + 128]
                    self.assertEqual(closed, opened)
                    self.assertEqual(closed[3::4], bytes([255]) * 6)
        prepared, header = self.prepare_bootstrap()
        text = header.read_text()
        self.assertEqual([row["id"] for row in lighting["bootstrap_models"]], ["probe-door", "probe-loot"])
        self.assertEqual(prepared["bootstrap"]["baked_color_bytes"], 2 * 12 * 48)
        self.assertEqual(prepared["compiled_baked_color_bytes"], 1152)
        self.assertEqual(prepared["compiled_known_array_bytes"], prepared["compiled_geometry_bytes"] + 1152)
        self.assertEqual(lighting["host_worker_bytes"], lighting["bytes"])
        self.assertEqual(lighting["standalone_rom_asset_bytes"], 0)
        self.assertEqual(lighting["mission_embedded_rgba_bytes"], 192 * 48)
        self.assertEqual(lighting["bootstrap_embedded_rgba_bytes"], 1152)
        self.assertIn("dl_stream_baked_model_0_0", text)
        self.assertIn("dl_stream_baked_model_2_1", text)
        door, loot = lighting["bootstrap_models"]
        self.assertNotEqual(door["closed_rgba"], loot["closed_rgba"])

    def test_night_reordering_preserves_native_cook_and_package(self):
        self.document["environment"] = self.environment
        self.write_source()
        package, report = self.cook()
        for key in ("assets", "materials", "entities"):
            self.document[key].reverse()
        self.manifest["regions"].reverse()
        for region in self.manifest["regions"]:
            region["entities"].reverse()
        self.write_source()
        changed, changed_report = self.cook()
        self.assertEqual(changed, package)
        self.assertEqual(changed_report["lighting"], report["lighting"])

    def test_night_full_scene_exceeds_target_totals_with_bounded_resident_views(self):
        self.authored_five_regions(count=12)
        self.document["environment"] = self.environment
        for index in range(14):
            self.document["materials"].append(dict(id=f"night-group-{index:02d}", color=[.4, .4, .4, 1]))
        removed = set()
        for region in self.manifest["regions"][2:]:
            models = [entity for entity in self.document["entities"] if entity["id"] in region["entities"] and "model" in entity]
            for index, entity in enumerate(models):
                entity["material"] = f"night-group-{index % 14:02d}"
            # Keep each v4 chunk below 64 KiB; source-wide counts remain large.
            removed.update(ident for ident in region["entities"] if "-box-" in ident and int(ident.rsplit("-", 1)[1]) >= 70)
            region["entities"] = [ident for ident in region["entities"] if ident not in removed]
        self.document["entities"] = [entity for entity in self.document["entities"] if entity["id"] not in removed]
        for entity in self.document["entities"]:
            if entity["kind"] == "light":
                entity["intensity"] = 0
        self.write_source()
        _, report = self.cook()
        mission.validate_stream_bridge(report)
        self.assertGreater(report["lighting"]["level_models"], 128)
        self.assertGreater(report["lighting"]["level_triangles"], 4096)
        self.assertTrue(all(row["group_count"] <= 64 and row["triangle_count"] <= 1440 for row in report["working_sets"]))

    def test_failed_night_bake_preserves_all_previously_published_outputs(self):
        _, header = self.prepare_bootstrap()
        output = header.parents[1]
        before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
        self.document["environment"] = self.environment
        self.write_source()
        with patch.object(mission, "prepare_stream_lighting", side_effect=RuntimeError("native cook failed")):
            with self.assertRaisesRegex(RuntimeError, "native cook failed"):
                self.prepare_bootstrap()
        self.assertEqual(before, {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()})

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

    def test_more_than_sixty_four_groups_are_rejected_before_publication(self):
        for index in range(33):
            self.document["materials"].append(dict(id=f"group-{index}", color=[.5, .5, .5, 1]))
        for region in self.manifest["regions"]:
            statics = [e for e in self.document["entities"] if e["id"] in region["entities"] and e["kind"] == "static" and "model" in e]
            while len(statics) < 33:
                ident = f"extra-{region['id']}-{len(statics)}"
                entity = dict(id=ident, kind="static", model="probe-box", material="group-0",
                              transform=dict(position=[-6 if region["id"] == "west" else 6, .25, -2], rotation=[0,0,0], scale=[.01,.01,.01]))
                self.document["entities"].append(entity)
                region["entities"].append(ident)
                statics.append(entity)
            for index, entity in enumerate(statics):
                entity["material"] = f"group-{index}"
        self.write_source()
        _, report = self.cook()
        self.assertEqual(sum(len(row["material_ids"]) for row in report["regions"]), 66)
        with self.assertRaisesRegex(ValueError, "64 nonempty region/material groups"):
            self.prepare_bootstrap()
        self.assertFalse((self.root / "bootstrap").exists())

    def test_textured_v2_preserves_uv_normal_and_pixel_dependencies(self):
        from PIL import Image
        Image.new("RGB", (8,8), (180,100,40)).save(self.content / "brick.png")
        (self.content / "models/uv.obj").write_text("v -.1 0 -.1\nv .1 0 -.1\nv 0 .1 .1\nvt 0 0\nvt 2 0\nvt 1 3\nvn 0 0 1\nf 1/1/1 2/2/1 3/3/1\n")
        self.document["assets"].append(dict(id="uv-mesh", uri="models/uv.obj"))
        self.document["materials"].append(dict(id="brick", color=[1,1,1,1], texture=dict(uri="brick.png",width=8,height=8,format="RGBA16")))
        self.document["entities"].append(dict(id="uv-wall",kind="static",model="uv-mesh",material="brick",transform=dict(position=[-6,.3,2],rotation=[0,90,0],scale=[1,2,1])))
        next(r for r in self.manifest["regions"] if r["id"]=="west")["entities"].append("uv-wall")
        self.manifest["visibility"]="all-regions"
        self.write_source()
        package, report=self.cook()
        self.assertEqual(report["chunk_version"],2)
        surface=next(r["id"] for r in report["surface_ids"] if r["entity"]=="uv-wall")
        found=[]
        for region in self.region_rows(package):
            chunk=package[region[1]:region[1]+region[2]]
            self.assertEqual(struct.unpack_from(">H",chunk,4)[0],2)
            for kind,_,start,_,count,stride,_ in self.sections(chunk):
                if kind!=1: continue
                self.assertEqual(stride,80)
                for i in range(count):
                    offset=start+i*stride
                    if struct.unpack_from(">I",chunk,offset+40)[0]==surface:
                        found.append(struct.unpack_from(">6f9bB2s",chunk,offset+44))
        self.assertEqual(len(found),1)
        self.assertEqual(found[0][:6],(0,1,2,1,1,-2))
        self.assertEqual(found[0][6:15],(127,0,0)*3)
        self.assertEqual(found[0][-2:],(3,bytes(2)))
        first_hash=report["package_sha256"]
        bootstrap, header=self.prepare_bootstrap()
        self.assertIn(".keep_neighbors_resident=true",header.read_text())
        self.assertEqual(len(bootstrap["textures"]["textures"]),1)
        self.assertTrue((self.root/"bootstrap/generated/texture_report.json").is_file())
        Image.new("RGB",(8,8),(10,20,30)).save(self.content/"brick.png")
        self.assertNotEqual(self.cook()[1]["package_sha256"],first_hash)

    def test_rom_bridge_reserves_an_unavailable_region_collider(self):
        _, report = self.cook()
        report["regions"][0]["collider_count"] = 400
        with self.assertRaisesRegex(ValueError, "unavailable-region barrier"):
            mission.validate_stream_bridge(report)

    def test_rom_bridge_rejects_oversized_chunk(self):
        _, report = self.cook()
        report["regions"][0]["chunk_bytes"] = 65537
        with self.assertRaisesRegex(ValueError, "65536 bytes"):
            mission.validate_stream_bridge(report)

    def test_changed_source_during_bootstrap_keeps_previous_outputs(self):
        _, header = self.prepare_bootstrap()
        previous = header.read_bytes()
        real_header = mission.bootstrap_header
        def changed(*args):
            result = real_header(*args)
            self.document["title"] = "Changed during preparation"
            self.write_source()
            return result
        with patch.object(mission, "bootstrap_header", side_effect=changed), self.assertRaisesRegex(ValueError, "changed during preparation"):
            self.prepare_bootstrap()
        self.assertEqual(header.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
