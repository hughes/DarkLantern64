"""Actual C legacy parity, compact two-state output and safe cooker caching."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from tools import cook_lighting, compile_level
from level_fixtures import gameplay_baseline, copy_level_dependencies

ROOT = Path(__file__).resolve().parents[1]
HEADER = '''#include "game.h"
static const DlVec3 vertices[]={{0,0,0},{1,0,0},{0,0,1}};
static const uint16_t indices[]={0,2,1};
static const DlNormal normals[]={{0,127,0},{0,127,0},{0,127,0}};
static const DlMesh meshes[]={{.vertices=vertices,.vertex_count=3,.indices=indices,.index_count=3,.normals=normals}};
static const DlModelInstance models[]={
 {.id="floor",.mesh=0,.scale={1,1,1},.color={100,120,140,255}},
 {.id="door",.mesh=0,.scale={1,1,1},.color={80,160,240,255},.role=DL_MODEL_DOOR,.double_sided=true}};
static const DlLevel dl_demo_level={.version=2,.meshes=meshes,.mesh_count=1,.models=models,.model_count=2,
 .environment={.enabled=true,.ambient={.25f,.25f,.25f},.exposure=1}};
'''


class LightingCookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dl64-lighting-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_actual_shared_core_matches_frozen_legacy_renderer(self):
        compiler, env, _ = cook_lighting.host_compiler()
        output = self.directory / "test.exe"
        cook_lighting.run([compiler, *cook_lighting.FLAGS, "-I" + str(ROOT / "src"),
                           ROOT / "src/game.c", ROOT / "src/static_lighting.c",
                           ROOT / "tests/test_static_lighting.c", "-lm", "-o", output], env)
        result = cook_lighting.run([output], env)
        self.assertIn("480 exact legacy comparisons passed", result.stdout)

    def test_compact_format_and_warm_cache_skip_compilation(self):
        cache = self.directory / "cache"
        record, payload = cook_lighting.prepare_lighting(HEADER, cache_root=cache)
        self.assertEqual((record["static_models"], record["static_triangles"], record["rgb_bytes"]), (2, 2, 54))
        self.assertEqual(record["bytes"], 64 + 24 + 54)
        self.assertEqual(struct.unpack_from(">HHIHH", payload, 64), (0, 1, 0, 1, 0))
        self.assertEqual(struct.unpack_from(">HHIHH", payload, 76), (1, 1, 1, 2, 0))
        self.assertEqual(payload[88:106], bytes([50, 60, 70]) * 6)  # front-only, both states
        self.assertEqual(payload[106:], bytes([40, 80, 120]) * 12)  # both sides, both states
        with patch.object(cook_lighting, "run", wraps=cook_lighting.run) as runner:
            again = cook_lighting.prepare_lighting(HEADER, cache_root=cache)
        self.assertEqual(again, (record, payload))
        self.assertFalse(any("-c" in call.args[0] or "-o" in call.args[0] for call in runner.call_args_list))
        changed_header = HEADER.replace(".25f,.25f,.25f", ".36f,.25f,.25f")
        with patch.object(cook_lighting, "run", wraps=cook_lighting.run) as runner:
            changed, changed_payload = cook_lighting.prepare_lighting(changed_header, cache_root=cache)
        self.assertNotEqual(changed["signature"], record["signature"])
        self.assertNotEqual(changed_payload[88], payload[88])
        self.assertFalse(any("-c" in call.args[0] for call in runner.call_args_list))  # common objects reused

    def test_corrupt_cached_bytes_are_rebuilt(self):
        cache = self.directory / "cache"
        report, raw = cook_lighting.prepare_lighting(HEADER, cache_root=cache)
        path = cache / (report["signature"] + ".bin")
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 255]))
        self.assertEqual(cook_lighting.prepare_lighting(HEADER, cache_root=cache), (report, raw))

    def test_source_change_invalidates_cached_bake(self):
        private_root = self.directory / "project"
        for name, raw in cook_lighting.snapshot_sources().items():
            path = private_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        with patch.object(cook_lighting, "ROOT", private_root):
            before, raw_before = cook_lighting.prepare_lighting(HEADER)
            game = private_root / "src/game.c"
            game.write_bytes(game.read_bytes() + b"\n/* cache invalidation fixture */\n")
            after, raw_after = cook_lighting.prepare_lighting(HEADER)
        self.assertNotEqual(before["signature"], after["signature"])
        self.assertEqual(raw_before[64:], raw_after[64:])

    def test_failed_bake_does_not_publish_changed_level_artifacts(self):
        level = gameplay_baseline()
        content, output = self.directory / "content", self.directory / "out"
        copy_level_dependencies(level, content)
        source = content / "level.json"
        source.write_text(json.dumps(level))
        with patch.object(compile_level, "prepare_lighting", side_effect=AssertionError("scalar invoked host")):
            record = compile_level.compile_level(source, output)
        self.assertIsNone(record["lighting"])
        before = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
        level["environment"] = json.loads((ROOT / "content/moonlit_courtyard.json").read_text())["environment"]
        source.write_text(json.dumps(level))
        with patch.object(compile_level, "prepare_lighting", side_effect=RuntimeError("compiler failed")):
            with self.assertRaisesRegex(RuntimeError, "compiler failed"):
                compile_level.compile_level(source, output)
        self.assertEqual(before, {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()})
        # A night level with only moving/animated models needs no empty file.
        # Door/control require visible models in the source schema, so give
        # them the same animated asset instead of bypassing that validation.
        for entity in level["entities"]:
            if entity["kind"] in ("door", "control"):
                entity["model"] = "mesh-guard"
            elif entity["kind"] not in ("guard", "objective"):
                entity.pop("model", None)
                entity.pop("material", None)
        level["entities"] = [e for e in level["entities"] if e["kind"] != "static" or "collider" in e]
        asset = next(a for a in level["assets"] if a["id"] == "mesh-guard")
        asset.update(type="character", uri="assets/guard/guard.character.json")
        copy_level_dependencies(level, content)
        source.write_text(json.dumps(level))
        with patch.object(compile_level, "prepare_lighting", side_effect=AssertionError("empty bake invoked host")):
            record = compile_level.compile_level(source, output)
        self.assertIsNone(record["lighting"])

    def test_bundle_shared_character_host_header_is_standalone(self):
        shared = {}
        report = compile_level.compile_level(ROOT / "content/moonlit_courtyard.json", self.directory / "bundle-level",
                                             shared_characters=shared)
        self.assertTrue(shared)
        self.assertGreater(report["lighting"]["static_models"], 0)
        header = (self.directory / "bundle-level/generated/demo_level.h").read_text()
        self.assertIn("extern const DlAnimationAsset", header)
        self.assertIn(".baked_lighting=", header)
        payload = (self.directory / "bundle-level" / report["lighting"]["path"]).read_bytes()
        self.assertEqual(cook_lighting.digest(payload), report["lighting"]["sha256"])


if __name__ == "__main__":
    unittest.main()
