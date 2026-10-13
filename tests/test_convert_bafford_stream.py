"""Synthetic, redistributable clipping and publication fixtures."""
import copy
import json
import math
import struct
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from tools import compile_level as level
from tools import compile_mission as mission
from tools import convert_bafford_stream as converter

ROOT = Path(__file__).resolve().parents[1]


class BaffordStreamConversionTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="bafford-convert-test-", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.content = self.root / "content"
        self.content.mkdir()
        Image.new("RGBA", (4, 4), (150, 90, 40, 255)).save(self.content / "stone.png")
        transform = lambda position, scale=[1, 1, 1]: dict(position=position, rotation=[0, 0, 0], scale=scale)
        self.document = dict(version=2, title="Synthetic entry", assets=[dict(id="box", type="primitive", shape="box")],
                             materials=[dict(id="stone", color=[1, 1, 1, 1], double_sided=False,
                                             texture=dict(uri="stone.png", width=4, height=4, format="RGBA16"),
                                             source=dict(texture_id=7, surface="stone", tags="Material Stone"))],
                             entities=[dict(id="floor", kind="static", model="box", material="stone", transform=transform([0, -.1, 0], [4, .2, 4]),
                                            collider=dict(shape="box", center=[0, 0, 0], half_size=[.5, .5, .5]),
                                            source=dict(cells=[10, 11], polygon=2)),
                                       dict(id="spawn", kind="spawn", transform=transform([-1, 0, 0])),
                                       dict(id="light", kind="light", transform=transform([1, 2, 0]), radius=5, intensity=.5)], test_starts=[])
        self.source = self.content / "entry.json"
        self.output = self.content / "thief_bafford_stream.json"
        self.write_source()

    def write_source(self):
        self.source.write_bytes(converter.json_bytes(self.document))

    def convert(self, **kwargs):
        return converter.convert(self.source, self.output, content_root=self.content, seam=0, **kwargs)

    def test_triangle_clipping_interpolates_uv_normal_and_preserves_area(self):
        triangle = [dict(position=[-1, 0, 0], uv=[0, 0], normal=[0, 0, 1]),
                    dict(position=[1, 0, 0], uv=[1, 0], normal=[0, 0, 1]),
                    dict(position=[1, 1, 0], uv=[1, 1], normal=[0, 0, 1])]
        left, right = [converter.clip_triangle(triangle, 0, side) for side in converter.SIDES]
        self.assertEqual((len(left), len(right)), (1, 2))
        self.assertTrue(all(corner["position"][0] <= 0 for part in left for corner in part))
        self.assertTrue(all(corner["position"][0] >= 0 for part in right for corner in part))
        seam_uvs = {tuple(corner["uv"]) for part in left + right for corner in part if corner["position"][0] == 0}
        self.assertEqual(seam_uvs, {(.5, 0), (.5, .5)})
        self.assertAlmostEqual(sum(math.sqrt(converter.area_squared(part)) for part in left + right), math.sqrt(converter.area_squared(triangle)))
        self.assertEqual(triangle[0]["position"], [-1, 0, 0])

    def test_coplanar_triangle_has_exactly_one_home(self):
        triangle = [dict(position=[0, 0, 0], normal=[1, 0, 0]), dict(position=[0, 1, 0], normal=[1, 0, 0]), dict(position=[0, 0, 1], normal=[1, 0, 0])]
        self.assertEqual(len(converter.clip_triangle(triangle, 0, "west")), 1)
        self.assertEqual(converter.clip_triangle(triangle, 0, "east"), [])

    def test_box_split_preserves_extent_and_rotated_crossing_is_explicit(self):
        box = dict(id="proxy", center=[0, 1, 0], half_size=[2, 1, 3], yaw=0, door=False)
        parts = converter.clip_box(box, 0)
        self.assertEqual(parts["west"]["center"], [-1, 1, 0])
        self.assertEqual(parts["east"]["center"], [1, 1, 0])
        self.assertEqual(parts["west"]["half_size"], [1, 1, 3])
        self.assertEqual(box["half_size"], [2, 1, 3])
        with self.assertRaisesRegex(ValueError, "rotated collider crosses"):
            converter.clip_box(dict(box, yaw=.4), 0)
        self.assertEqual(set(converter.clip_box(dict(box, center=[-10, 1, 0], yaw=.4), 0)), {"west"})

    def test_derived_scene_validates_with_texture_normals_and_source_provenance(self):
        original = self.source.read_bytes()
        report = self.convert()
        derived = json.loads(self.output.read_bytes())
        state = level.validate(derived, self.content)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertNotIn("asset_packs", derived)
        self.assertEqual(derived["materials"], self.document["materials"])
        self.assertTrue(all("uvs" in mesh and "normals" in mesh for mesh in state["meshes"].values()))
        proxies = [row for row in derived["entities"] if "collider" in row]
        self.assertEqual({row["id"] for row in proxies}, {"floor-west", "floor-east"})
        self.assertTrue(all(row["source"]["polygon"] == 2 for row in proxies))
        for entity in [row for row in derived["entities"] if "model" in row]:
            self.assertTrue(entity["source"]["triangle_provenance"])
            self.assertEqual({row["source_entity_id"] for row in entity["source"]["triangle_provenance"]}, {"floor"})
            self.assertEqual(entity["source"]["sources"], [dict(id="floor", metadata=dict(cells=[10, 11], polygon=2))])
        manifest = json.loads(self.output.with_name(self.output.stem + ".mission.json").read_bytes())
        self.assertEqual(manifest["visibility"], "all-regions")
        self.assertEqual(manifest["entry_region"], "west")
        self.assertEqual(manifest["source"], self.output.name)
        self.assertEqual([row["bounds"][0][0] for row in manifest["regions"]][1], -converter.BOUND_PADDING)
        self.assertGreaterEqual(manifest["regions"][1]["bounds"][1][1], 1.65)
        homes = [ident for region in manifest["regions"] for ident in region["entities"]]
        self.assertEqual(sorted(homes), sorted(row["id"] for row in derived["entities"]))
        self.assertEqual(report["source_counts"]["crossing_colliders"], 1)

    def test_default_overwrite_gate_and_explicit_replacement_retains_edits(self):
        self.convert()
        original = self.output.read_bytes()
        artist = json.loads(original)
        artist["title"] = "Artist edits"
        self.output.write_bytes(converter.json_bytes(artist))
        edited = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, "--replace"):
            self.convert()
        self.assertEqual(self.output.read_bytes(), edited)
        self.convert(replace=True)
        self.assertEqual(self.output.read_bytes(), original)
        backups = list((self.root / ".dev/editor/replaced-streams").glob("*/thief_bafford_stream.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), edited)

    def test_reordered_input_has_identical_mesh_payloads_and_stable_derived_ids(self):
        state = level.validate(self.document, self.content)
        first = converter.derive(self.document, state, "entry.json", "a" * 64, 0)
        shuffled = copy.deepcopy(self.document)
        shuffled["entities"].reverse()
        second = converter.derive(shuffled, level.validate(shuffled, self.content), "entry.json", "a" * 64, 0)
        self.assertEqual(first, second)

    def test_source_cannot_be_replaced_and_protected_study_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "original source untouched"):
            converter.convert(self.source, self.source, content_root=self.content, replace=True)
        self.document["authoring_profile"] = "external-scale-study"
        self.write_source()
        with self.assertRaisesRegex(ValueError, "authoring-only source study"):
            self.convert()
        self.assertFalse(self.output.exists())

    def test_concurrent_source_mesh_edit_rejects_publication_from_captured_inputs(self):
        state = level.validate(self.document, self.content)
        mesh = state["meshes"]["box"]
        triangles = [[dict(position=mesh["vertices"][i], uv=mesh["uvs"][i], normal=mesh["normals"][i])
                      for i in mesh["indices"][index:index + 3]] for index in range(0, len(mesh["indices"]), 3)]
        path = self.content / "original.obj"
        path.write_bytes(converter.obj_payload(triangles, [0, 0, 0]))
        self.document["assets"] = [dict(id="box", uri="original.obj")]
        self.write_source()
        original_derive = converter.derive
        def edit_original_after_snapshot(*args, **kwargs):
            result = original_derive(*args, **kwargs)
            path.write_bytes(path.read_bytes() + b"# concurrent artist edit\n")
            return result
        with patch.object(converter, "derive", side_effect=edit_original_after_snapshot), self.assertRaisesRegex(ValueError, "changed during this operation"):
            self.convert()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name(self.output.stem + ".mission.json").exists())
        self.assertFalse((self.content / "assets").exists())
        self.assertTrue(path.read_bytes().endswith(b"# concurrent artist edit\n"))

    def test_report_binds_every_discovered_input_including_compiler_definitions(self):
        state = level.validate(self.document, self.content)
        report = self.convert()
        self.assertEqual({row["uri"]: row["sha256"] for row in report["input_dependencies"]},
                         {uri: converter.digest(path.read_bytes()) for uri, path in state["dependencies"]})
        self.assertEqual(json.loads(self.output.read_bytes())["stream_conversion"]["input_dependencies"], report["input_dependencies"])

    def test_nontrivial_region_anchors_roundtrip_inside_padded_bounds(self):
        positions = {"floor": [-3.048, -.1, 44.635], "spawn": [-6.522049, 0, 49.8252936],
                     "light": [8.382, 2, 51.3988]}
        for entity in self.document["entities"]:
            entity["transform"]["position"] = positions[entity["id"]]
            if entity["id"] == "floor":
                entity["transform"]["scale"] = [12, .2, 16]
        self.write_source()
        converter.convert(self.source, self.output, content_root=self.content, seam=-3.048)
        manifest = json.loads(self.output.with_name(self.output.stem + ".mission.json").read_bytes())
        package, report = mission.cook_mission(manifest, self.content)
        self.assertTrue(package.startswith(b"DLM1"))
        state = level.validate(json.loads(self.output.read_bytes()), self.content)
        homes = {ident: region for region in manifest["regions"] for ident in region["entities"]}
        float32 = lambda value: struct.unpack(">f", struct.pack(">f", value))[0]
        for entity in state["models"]:
            region = homes[entity["id"]]
            mesh = state["meshes"][entity["model"]]
            for point in mesh["vertices"]:
                world = level.transform_point(point, entity["transform"])
                mission.assert_bounds(region, [world], entity["id"])
                target_world = [float32(float32(point[axis]) + float32(entity["transform"]["position"][axis])) for axis in range(3)]
                mission.assert_bounds(region, [target_world], entity["id"] + " target float")
        self.assertEqual(len(report["regions"]), 2)

    def test_publication_failure_rolls_back_generated_assets_source_and_manifest(self):
        original_replace = converter.ProjectLevels.publish
        def fail_publication(project, changes, expected):
            original = __import__("os").replace
            def fail_manifest(source, destination):
                if Path(destination).name.endswith(".mission.json"):
                    raise OSError("injected manifest failure")
                return original(source, destination)
            with patch("os.replace", side_effect=fail_manifest):
                return original_replace(project, changes, expected)
        with patch.object(converter.ProjectLevels, "publish", fail_publication), self.assertRaisesRegex(OSError, "injected manifest"):
            self.convert()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name(self.output.stem + ".mission.json").exists())
        self.assertEqual(list((self.content / "assets").rglob("*.obj")), [])


if __name__ == "__main__":
    unittest.main()
