"""Redistributable source-cell expansion and exact solid-union fixtures."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import compile_level as level
from tools import compile_mission as mission
from tools import convert_bafford_stream as converter
from tools import expand_bafford_stream as expansion

ROOT = Path(__file__).resolve().parents[1]


def record(ident, low, high, *, tags="stone", yaw=0):
    return dict(lo=list(low), hi=list(high), yaw=yaw, semantic=("stone", tags),
                sources=[dict(scene="synthetic.json", entity_id=ident)], original=dict(id=ident))


class ExactProxyMergeTests(unittest.TestCase):
    def test_touching_intervals_and_containment_preserve_union_and_sources(self):
        boxes = [record("a", [0, 0, 0], [1, 1, 1]), record("b", [1, 0, 0], [2, 1, 1]),
                 record("inside", [.2, .2, .2], [.5, .5, .5])]
        original = copy.deepcopy(boxes)
        merged, report = expansion.merge_boxes(boxes)
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0]["lo"], merged[0]["hi"]), ([0, 0, 0], [2, 1, 1]))
        self.assertEqual({row["entity_id"] for row in merged[0]["sources"]}, {"a", "b", "inside"})
        self.assertEqual(report["containment"], 1)
        self.assertEqual(report["interval_union"], 1)
        self.assertEqual(boxes, original)
        for x in (-.1, 0, .3, .9, 1, 1.4, 2, 2.1):
            for y in (-.1, .5, 1, 1.1):
                point = [x, y, .5]
                contains = lambda box: all(box["lo"][i] <= point[i] <= box["hi"][i] for i in range(3))
                self.assertEqual(any(map(contains, boxes)), any(map(contains, merged)))

    def test_no_tolerance_gap_fill_slab_expansion_or_material_collapse(self):
        first = record("a", [0, 0, 0], [1, 1, 1])
        for second in (record("gap", [1 + 1e-12, 0, 0], [2, 1, 1]),
                       record("slab", [1, 0, 0], [2, 1 + 1e-12, 1]),
                       record("material", [1, 0, 0], [2, 1, 1], tags="metal"),
                       record("rotated", [1, 0, 0], [2, 1, 1], yaw=.2)):
            with self.subTest(second=second["sources"][0]["entity_id"]):
                merged, _ = expansion.merge_boxes([first, second])
                self.assertEqual(len(merged), 2)
        l_shape = [record("long", [0, 0, 0], [2, 1, 1]), record("short", [0, 0, 1], [1, 1, 2])]
        merged, _ = expansion.merge_boxes(l_shape)
        self.assertEqual(len(merged), 2)
        self.assertFalse(any(all(box["lo"][i] <= [1.5, .5, 1.5][i] <= box["hi"][i] for i in range(3)) for box in merged))

    def test_input_order_is_deterministic_with_multiaxis_merges(self):
        boxes = [record(str(x) + str(z), [x, 0, z], [x + 1, 1, z + 1]) for x in range(3) for z in range(2)]
        first, counts = expansion.merge_boxes(boxes)
        second, second_counts = expansion.merge_boxes(list(reversed(boxes)))
        self.assertEqual(first, second)
        self.assertEqual(counts, second_counts)
        self.assertEqual(len(first), 1)


class SourceCellExpansionTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="bafford-expand-test-", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.content = self.root / "content"
        self.content.mkdir()
        self.entry_path = self.content / "entry.json"
        self.full_path = self.content / "full.json"
        self.output = self.content / "thief_bafford_expanded.json"
        self.annotations_path = self.root / "annotations.json"
        self.topology_path = self.root / "topology.json"
        self.obj = self.content / "added.obj"
        transform = lambda position, scale=[1, 1, 1]: dict(position=position, rotation=[0, 0, 0], scale=scale)
        material = dict(id="stone", color=[.7, .5, .3, 1], double_sided=False,
                        source=dict(surface="stone", tags="Material Stone"))
        self.entry = dict(version=2, title="Edited entry", assets=[dict(id="box", type="primitive", shape="box")],
                          materials=[material], entities=[dict(id="entry-floor", kind="static", model="box", material="stone",
                          transform=transform([-1, -.1, 1], [2, .2, 2]), collider=dict(shape="box", center=[0, 0, 0], half_size=[.5, .5, .5]),
                          source=dict(cells=[1], edited="artist", material=copy.deepcopy(material["source"]))),
                          dict(id="spawn", kind="spawn", transform=transform([-1, 0, 1])),
                          dict(id="light", kind="light", transform=transform([-1, 2, 1]), radius=3, intensity=.5)], test_starts=[])
        self.full = dict(version=2, authoring_profile="external-scale-study", title="Synthetic full study",
                         assets=[dict(id="source-model", uri="added.obj")], materials=[copy.deepcopy(material)],
                         entities=[dict(id="source-surface", kind="static", model="source-model", material="stone",
                                        transform=transform([0, 0, 0]), source=dict(cells=[2]))])
        points = [[0, 0, 0], [0, 0, 2], [2, 0, 2], [2, 0, 0]]
        self.triangles = [[dict(position=points[i], normal=[0, 1, 0]) for i in indices] for indices in ((0, 1, 2), (0, 2, 3))]
        self.obj.write_bytes(converter.obj_payload(self.triangles, [0, 0, 0]))
        self.annotations = {"source-model": [[2, 0], [2, 0]]}
        scale = expansion.METRES_PER_UNIT
        planes = [dict(normal=[1, 0, 0], distance=0), dict(normal=[-1, 0, 0], distance=2 / scale),
                  dict(normal=[0, 1, 0], distance=2 / scale), dict(normal=[0, -1, 0], distance=0),
                  dict(normal=[0, 0, 1], distance=0), dict(normal=[0, 0, -1], distance=3 / scale)]
        self.topology = dict(cells=[dict(id=1, planes=[], portals=[dict(target=2)]),
                                   dict(id=2, planes=planes, portals=[dict(target=1)])])
        self.write_inputs()

    def write_inputs(self):
        for path, document in ((self.entry_path, self.entry), (self.full_path, self.full),
                               (self.annotations_path, self.annotations), (self.topology_path, self.topology)):
            path.write_bytes(converter.json_bytes(document))

    def expand(self, **kwargs):
        return expansion.expand(self.entry_path, self.full_path, self.annotations_path, self.topology_path, self.output,
                                content_root=self.content, selected=(2,), seam=0, **kwargs)

    def test_preserves_authored_entry_and_adds_only_requested_provenanced_geometry(self):
        originals = {path: path.read_bytes() for path in (self.entry_path, self.full_path, self.obj, self.annotations_path, self.topology_path)}
        report = self.expand()
        result = json.loads(self.output.read_bytes())
        state = level.validate(result, self.content)
        self.assertNotIn("authoring_profile", result)
        self.assertEqual(state["by_id"]["spawn"], self.entry["entities"][1])
        self.assertEqual(state["by_id"]["light"], self.entry["entities"][2])
        self.assertEqual(result["materials"], self.entry["materials"])
        for path, raw in originals.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(report["expansion"]["added_triangle_counts"], {"2": 2})
        provenance = [row for entity in result["entities"] for row in entity.get("source", {}).get("triangle_provenance", [])]
        added = [row for row in provenance if row["source_entity_id"] == "source-surface"]
        self.assertEqual({row["source_triangle"] for row in added}, {0, 1})
        self.assertTrue(all((row["cell"], row["polygon"]) == (2, 0) for row in added))
        old = [row for row in provenance if row["source_entity_id"] == "entry-floor"]
        self.assertEqual({row["source_triangle"] for row in old}, set(range(12)))
        proxy_sources = [source for entity in result["entities"] if "collider" in entity
                         for source in entity["source"]["sources"]]
        self.assertEqual({(source["scene"], source["entity_id"]) for source in proxy_sources},
                         {("entry.json", "entry-floor"), ("full.json", "thief-bafford-collision-0002-000")})
        self.assertTrue(all("transform" in source and "collider" in source and "metadata" in source for source in proxy_sources))
        manifest = json.loads(self.output.with_name(self.output.stem + ".mission.json").read_bytes())
        package, package_report = mission.cook_mission(manifest, self.content)
        mission.validate_stream_bridge(package_report)
        self.assertTrue(package.startswith(b"DLM1"))

    def test_annotation_count_edit_and_wrong_cell_boundary_fail_before_publication(self):
        self.annotations["source-model"].pop()
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "annotation count"):
            self.expand()
        self.assertFalse(self.output.exists())
        self.annotations["source-model"] = [[2, 0], [2, 0]]
        self.write_inputs()
        for triangle in self.triangles:
            for corner in triangle:
                corner["position"][0] += 3
        self.obj.write_bytes(converter.obj_payload(self.triangles, [0, 0, 0]))
        with self.assertRaisesRegex(ValueError, "leaves annotated source cell"):
            self.expand()
        self.assertFalse(self.output.exists())

    def test_disconnected_or_preexisting_cell_selection_is_rejected(self):
        state = level.validate(self.entry, self.content)
        with self.assertRaisesRegex(ValueError, "overlap"):
            expansion.expansion_state(self.entry, state, self.full, self.annotations, self.topology, [1], self.content)
        self.topology["cells"][1]["portals"] = []
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "disconnected"):
            self.expand()
        self.assertFalse(self.output.exists())

    def test_default_overwrite_gate_archives_explicitly_replaced_artist_edits(self):
        self.expand()
        original = self.output.read_bytes()
        artist = json.loads(original)
        artist["title"] = "New artist edits"
        self.output.write_bytes(converter.json_bytes(artist))
        edited = self.output.read_bytes()
        with self.assertRaisesRegex(ValueError, "--replace"):
            self.expand()
        self.assertEqual(self.output.read_bytes(), edited)
        self.expand(replace=True)
        self.assertEqual(self.output.read_bytes(), original)
        backups = list((self.root / ".dev/editor/replaced-expansions").glob("*/thief_bafford_expanded.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), edited)

    def test_concurrent_selected_full_mesh_edit_rejects_snapshot_publication(self):
        original = expansion.expansion_state
        def concurrent(*args, **kwargs):
            result = original(*args, **kwargs)
            self.obj.write_bytes(self.obj.read_bytes() + b"# concurrent artist edit\n")
            return result
        with patch.object(expansion, "expansion_state", side_effect=concurrent), self.assertRaisesRegex(ValueError, "changed during this operation"):
            self.expand()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name(self.output.stem + ".mission.json").exists())

    def test_concurrent_topology_edit_rejects_snapshot_publication(self):
        original = expansion.expansion_state
        def concurrent(*args, **kwargs):
            result = original(*args, **kwargs)
            self.topology_path.write_bytes(self.topology_path.read_bytes() + b"\n")
            return result
        with patch.object(expansion, "expansion_state", side_effect=concurrent), self.assertRaisesRegex(ValueError, "changed during this operation"):
            self.expand()
        self.assertFalse(self.output.exists())

    def test_concurrent_annotations_and_entry_json_edits_reject_publication(self):
        original = expansion.expansion_state
        for path in (self.annotations_path, self.entry_path):
            with self.subTest(path=path):
                self.write_inputs()
                def concurrent(*args, **kwargs):
                    result = original(*args, **kwargs)
                    path.write_bytes(path.read_bytes() + b"\n")
                    return result
                with patch.object(expansion, "expansion_state", side_effect=concurrent), self.assertRaisesRegex(ValueError, "changed during this operation"):
                    self.expand()
                self.assertFalse(self.output.exists())

    def test_named_starts_are_valid_on_imported_bridge_and_east_floor(self):
        self.full["assets"], self.full["entities"], self.annotations = [], [], {}
        self.topology["cells"] = [dict(id=1, planes=[], portals=[dict(target=3831)])]
        scale = expansion.METRES_PER_UNIT
        for cid, x0, x1, floor in ((3831, 8.382, 12.0396, -2.1336), (4987, 12.0396, 15.5448, -2.4384)):
            points = [[x0, floor, 47.3964], [x0, floor, 49.8348], [x1, floor, 49.8348], [x1, floor, 47.3964]]
            triangles = [[dict(position=points[i], normal=[0, 1, 0]) for i in indices] for indices in ((0, 1, 2), (0, 2, 3))]
            uri = f"floor-{cid}.obj"
            (self.content / uri).write_bytes(converter.obj_payload(triangles, [0, 0, 0]))
            model = f"mesh-{cid}"
            self.full["assets"].append(dict(id=model, uri=uri))
            self.full["entities"].append(dict(id=f"surface-{cid}", kind="static", model=model, material="stone",
                                              transform=dict(position=[0, 0, 0], rotation=[0, 0, 0], scale=[1, 1, 1]), source=dict(cells=[cid])))
            self.annotations[model] = [[cid, 0], [cid, 0]]
            planes = [dict(normal=[1, 0, 0], distance=-x0 / scale), dict(normal=[-1, 0, 0], distance=x1 / scale),
                      dict(normal=[0, 1, 0], distance=49.8348 / scale), dict(normal=[0, -1, 0], distance=-47.3964 / scale),
                      dict(normal=[0, 0, 1], distance=-floor / scale), dict(normal=[0, 0, -1], distance=(floor + 3) / scale)]
            self.topology["cells"].append(dict(id=cid, planes=planes, portals=[dict(target=1 if cid == 3831 else 3831), dict(target=4987 if cid == 3831 else 3831)]))
        self.write_inputs()
        expansion.expand(self.entry_path, self.full_path, self.annotations_path, self.topology_path, self.output,
                         content_root=self.content, selected=(3831, 4987), seam=0)
        result = json.loads(self.output.read_bytes())
        state = level.validate(result, self.content)
        self.assertEqual({start["id"] for start in state["test_starts"]}, {"east-street", "bridge-west"})
        self.assertEqual(state["by_id"]["spawn"], self.entry["entities"][1])

    def test_cannot_replace_preserved_sources_or_current_stream(self):
        for path in (self.entry_path, self.full_path, self.content / "thief_bafford_stream.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "preserve"):
                expansion.expand(self.entry_path, self.full_path, self.annotations_path, self.topology_path, path,
                                 content_root=self.content, selected=(2,), replace=True)

    def test_publication_failure_rolls_back_source_manifest_and_generated_meshes(self):
        original_publish = expansion.ProjectLevels.publish
        def failing(project, changes, expected):
            original_replace = __import__("os").replace
            def fail_manifest(source, destination):
                if Path(destination).name.endswith(".mission.json"):
                    raise OSError("injected expanded manifest failure")
                return original_replace(source, destination)
            with patch("os.replace", side_effect=fail_manifest):
                return original_publish(project, changes, expected)
        with patch.object(expansion.ProjectLevels, "publish", failing), self.assertRaisesRegex(OSError, "injected expanded"):
            self.expand()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.with_name(self.output.stem + ".mission.json").exists())
        self.assertEqual(list((self.content / "assets").rglob("*.obj")), [])


if __name__ == "__main__":
    unittest.main()
