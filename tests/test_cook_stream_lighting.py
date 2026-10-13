"""Native stream lighting, full-scene occluders and deterministic warm cache."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import cook_lighting, cook_stream_lighting

ROOT = Path(__file__).resolve().parents[1]
HEADER = '''#include "game.h"
static const DlVec3 vertices[]={{0,0,0},{0,0,1},{1,0,0}};
static const uint16_t indices[]={0,1,2};
static const DlNormal normals[]={{0,127,0},{0,127,0},{0,127,0}};
static const DlMesh meshes[]={{.vertices=vertices,.vertex_count=3,.indices=indices,.index_count=3,.normals=normals}};
static const DlModelInstance models[]={
 {.id="floor",.mesh=0,.scale={1,1,1},.color={100,120,140,255},.double_sided=true}};
static const DlLevel dl_demo_level={.version=2,.meshes=meshes,.mesh_count=1,.models=models,.model_count=1,
 .environment={.enabled=true,.ambient={.25f,.25f,.25f},.exposure=1}};
'''


class StreamLightingCookTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="stream-lighting-test-", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_rgba_both_sides_states_and_warm_cache(self):
        report, colors = cook_stream_lighting.prepare_stream_lighting(HEADER, collider_count=0, cache_root=self.root)
        expected = bytes([50, 60, 70, 255]) * 6
        self.assertEqual(colors, {0: (expected, expected)})
        self.assertEqual((report["rgba_bytes"], report["static_triangles"], report["states"]), (48, 1, 2))
        with patch.object(cook_lighting, "run", wraps=cook_lighting.run) as runner:
            again = cook_stream_lighting.prepare_stream_lighting(HEADER, collider_count=0, cache_root=self.root)
        self.assertEqual(again, (report, colors))
        self.assertFalse(any("-c" in call.args[0] or "-o" in call.args[0] for call in runner.call_args_list))

    def test_full_mission_uses_occluders_after_runtime_working_set_limit(self):
        rows = ["{.center={100,100,100},.half_size={1,1,1}}"] * 400
        rows.append("{.center={0,2,0},.half_size={4,.1f,4}}")
        declarations = "static const DlCollider boxes[]={" + ",".join(rows) + "};\n"
        declarations += "static const DlLight lights[]={{{0,4,0},10,1,{1,1,1}}};\n"
        header = HEADER.replace("static const DlLevel", declarations + "static const DlLevel")
        header = header.replace(".version=2", ".version=2,.colliders=boxes,.collider_count=401,.lights=lights,.light_count=1")
        header = header.replace(".25f,.25f,.25f", ".01f,.01f,.01f")
        _, full = cook_stream_lighting.prepare_stream_lighting(header, collider_count=401, cache_root=self.root)
        _, clipped = cook_stream_lighting.prepare_stream_lighting(header, collider_count=400, cache_root=self.root)
        self.assertEqual(full[0][0][0], 10)
        self.assertGreater(clipped[0][0][0], full[0][0][0])

    def test_corrupt_cached_output_is_recooked(self):
        report, colors = cook_stream_lighting.prepare_stream_lighting(HEADER, collider_count=0, cache_root=self.root)
        path = self.root / (report["signature"] + ".bin")
        path.write_bytes(path.read_bytes()[:-1])
        self.assertEqual(cook_stream_lighting.prepare_stream_lighting(HEADER, collider_count=0, cache_root=self.root), (report, colors))

    def test_full_mission_uses_floors_after_runtime_working_set_limit(self):
        rows = ["{.vertices={{100,100,100},{101,100,100},{100,100,101}}}"] * 256
        rows.append("{.vertices={{-4,2,-4},{8,2,-4},{-4,2,8}}}")
        declarations = "static const DlFloorTriangle floors[]={" + ",".join(rows) + "};\n"
        declarations += "static const DlLight lights[]={{{0,4,0},10,1,{1,1,1}}};\n"
        header = HEADER.replace("static const DlLevel", declarations + "static const DlLevel")
        header = header.replace(".version=2", ".version=2,.floors=floors,.floor_count=257,.lights=lights,.light_count=1")
        header = header.replace(".25f,.25f,.25f", ".01f,.01f,.01f")
        _, full = cook_stream_lighting.prepare_stream_lighting(header, collider_count=0, floor_count=257, cache_root=self.root)
        _, clipped = cook_stream_lighting.prepare_stream_lighting(header, collider_count=0, floor_count=256, cache_root=self.root)
        self.assertEqual(full[0][0][0], 10)
        self.assertGreater(clipped[0][0][0], full[0][0][0])


if __name__ == "__main__":
    unittest.main()
