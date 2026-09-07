"""Load evidence must not hide missing states, stale assets, or diagnostic cost."""
import copy
from pathlib import Path
import types
import unittest

from tools.verify_level_loading import analyze, compare, parse_stages, private_build_root, STAGES, validate_historical_baseline


SIGNATURE = "a" * 64


def manifest(mode="ordinary"):
    content = {"source_sha256": "content", "counts": {"models": 3, "meshes": 2, "instanced_vertices": 20,
               "instanced_triangles": 10}, "textures": {"textures": []},
               "lighting": {"path": "romfs/lighting/lighting-" + SIGNATURE + ".bin", "signature": SIGNATURE,
                   "sha256": "file", "bytes": 400, "rgb_bytes": 288, "static_models": 2, "static_triangles": 8, "states": 2}}
    return {"renderer": "t3d", "autoplay": False, "capture": False, "debug_overlay": False, "scale_bench": False,
            "menu_test": False, "model_culling": True, "lighting_bake_verify": mode == "verify", "disable_lighting_bake": mode == "bypass",
            "start_preset": None, "source_sha256": "content", "level_catalog": {"levels": [{"id": "fixture", "content": content}],
            "rom_assets": [{"path": content["lighting"]["path"], "kind": "lighting"}]}}


def log(mode="ordinary"):
    status = "status=disabled reason=build_flag" if mode == "bypass" else (
        "status=loaded reason=ok models=2 triangles=8 states=2 rgb_bytes=288 crc32=12345678 path=rom:/lighting/lighting-" + SIGNATURE + ".bin")
    text = "DL64 lighting_bake " + status + "\n"
    if mode == "verify":
        text += "DL64 lighting_bake_verify models=2 triangles=8 states=2 bytes=384 mismatches=0 max_channel_delta=0 baked_fnv1a=1234abcd reference_fnv1a=1234abcd\n"
    text += ("DL64 geometry_ready models=3 meshes=2 vertices=20 triangles=10\n"
             "DL64 scene_prepared ms=1.0 textures=0 night_cache_bytes=480 door_states=2\n"
             "DL64 scene_load total_ticks=110 " + " ".join(s + "_ticks=10" for s in STAGES) +
             " ticks_per_second=1000 scope=renderer_prepare_including_interrupts\n"
             "DL64 level_load id=fixture preset=default total_ticks=120 ticks_per_second=1000 scope=reset_to_ready_including_interrupts\n"
             "DL64 level_start id=fixture preset=default heap=1000/8000\nDL64 profile_end window=3\n")
    return text.encode()


class LevelLoadingEvidence(unittest.TestCase):
    def test_complete_both_state_rgba_parity(self):
        result = analyze(log("verify"), manifest("verify"), "verify", level_id="fixture")
        self.assertTrue(result["passed"])
        self.assertFalse(result["ordinary_load_timing"])
        self.assertEqual(result["parity"]["bytes"], "384")

    def test_missing_partial_or_mismatching_parity_rejected(self):
        for before, after in ((b"states=2 bytes=384", b"states=1 bytes=384"), (b"bytes=384", b"bytes=192"),
                              (b"triangles=8 states=2 bytes", b"triangles=7 states=2 bytes"),
                              (b"mismatches=0", b"mismatches=1"), (b"max_channel_delta=0", b"max_channel_delta=1"),
                              (b"reference_fnv1a=1234abcd", b"reference_fnv1a=1234abce"),
                              (b"DL64 lighting_bake_verify", b"DL64 skipped_verification")):
            with self.subTest(after=after), self.assertRaises(ValueError):
                analyze(log("verify").replace(before, after), manifest("verify"), "verify", level_id="fixture")

    def test_missing_stage_or_incorrect_sum_rejected(self):
        for before, after in ((b"bake_ticks=10 ", b""), (b"total_ticks=110", b"total_ticks=109"),
                              (b"gpu_ticks=10", b"gpu_ticks=-1"), (b"hud_ticks=10", b"hud_ticks=10 hud_ticks=10")):
            with self.subTest(after=after), self.assertRaises(ValueError):
                analyze(log().replace(before, after), manifest(), "ordinary", level_id="fixture")

    def test_content_signature_geometry_and_flag_mismatch_rejected(self):
        for field in ("source_sha256", "lighting_bake_verify", "disable_lighting_bake"):
            changed = manifest(); changed[field] = True
            with self.subTest(field=field), self.assertRaises(ValueError):
                analyze(log(), changed, "ordinary", level_id="fixture")
        for before, after in ((SIGNATURE.encode(), b"b" * 64), (b"vertices=20", b"vertices=19"),
                              (b"rgb_bytes=288", b"rgb_bytes=287")):
            with self.subTest(after=after), self.assertRaises(ValueError):
                analyze(log().replace(before, after), manifest(), "ordinary", level_id="fixture")

    def test_zero_texture_lighting_requires_real_loaded_asset(self):
        self.assertTrue(analyze(log(), manifest(), "ordinary", level_id="fixture", zero_textures=True)["passed"])
        changed = manifest(); changed["level_catalog"]["rom_assets"] = []
        with self.assertRaises(ValueError):
            analyze(log(), changed, "ordinary", level_id="fixture", zero_textures=True)
        with self.assertRaises(ValueError):
            analyze(log().replace(b"status=loaded", b"status=rejected"), manifest(), "ordinary", level_id="fixture", zero_textures=True)

    def test_explicit_bypass_and_unconfigured_are_distinct(self):
        self.assertTrue(analyze(log("bypass"), manifest("bypass"), "bypass", level_id="fixture")["passed"])
        raw = log().replace(log().splitlines()[0], b"DL64 lighting_bake status=absent reason=unconfigured")
        self.assertTrue(analyze(raw, manifest(), "ordinary", level_id="fixture", unconfigured=True)["passed"])
        with self.assertRaises(ValueError):
            analyze(raw, manifest(), "ordinary", level_id="fixture")

    def test_crc_corruption_requires_specific_rejection_and_complete_fallback(self):
        raw = log().replace(b"status=loaded reason=ok", b"status=rejected reason=checksum")
        self.assertTrue(analyze(raw, manifest(), "ordinary", level_id="fixture", corrupt=True)["passed"])
        with self.assertRaises(ValueError):
            analyze(raw.replace(b"reason=checksum", b"reason=open"), manifest(), "ordinary", level_id="fixture", corrupt=True)
        with self.assertRaises(ValueError):
            analyze(raw.replace(b"profile_end window=3", b"profile_end window=2"), manifest(), "ordinary", level_id="fixture", corrupt=True)

    def test_legacy_level_requires_explicit_legacy_marker(self):
        changed = manifest(); changed["level_catalog"]["levels"][0]["content"]["lighting"] = None
        raw = log().replace(log().splitlines()[0], b"DL64 lighting_bake status=absent reason=legacy")
        self.assertTrue(analyze(raw, changed, "ordinary", level_id="fixture")["passed"])

    def test_crlf_and_no_complete_boot(self):
        self.assertTrue(analyze(log().replace(b"\n", b"\r\n"), manifest(), "ordinary", level_id="fixture")["passed"])
        with self.assertRaises(ValueError):
            analyze(log().replace(b"profile_end window=3", b"profile_end window=2"), manifest(), "ordinary", level_id="fixture")

    def test_comparison_requires_same_content_and_ordinary_work(self):
        before = analyze(log("bypass"), manifest("bypass"), "bypass", level_id="fixture")
        after = analyze(log(), manifest(), "ordinary", level_id="fixture")
        self.assertEqual(compare(before, after)["speedup"], 1)
        bad = copy.deepcopy(after); bad["content_sha256"] = "changed"
        with self.assertRaises(ValueError): compare(before, bad)
        bad = copy.deepcopy(after); bad["ordinary_load_timing"] = False
        with self.assertRaises(ValueError): compare(before, bad)

    def test_historical_baseline_rechecks_available_raw_and_labels_missing(self):
        import hashlib
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b"historical log"
            (root / "old.log").write_bytes(raw)
            row = {"scope": "renderer_prepare_including_interrupts", "ticks_per_second": "1000", "total_ticks": "90",
                   **{name + "_ticks": "10" for name in STAGES if name not in ("bake", "bake_verify")}}
            before = {"passed": True, "stage_sum_exact": True, "record": row, "run": {"log": "old.log"},
                      "log_sha256": hashlib.sha256(raw).hexdigest(), "rom": "absent.z64", "rom_sha256": "a" * 64}
            checked = validate_historical_baseline(before, root)
            self.assertTrue(checked["log"]["revalidated_locally"])
            self.assertFalse(checked["rom"]["available_locally"])
            self.assertFalse(checked["rom"]["revalidated_locally"])
            (root / "old.log").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "log differs"):
                validate_historical_baseline(before, root)
            (root / "old.log").unlink()
            before["record"]["total_ticks"] = "91"
            with self.assertRaisesRegex(ValueError, "stage sum"):
                validate_historical_baseline(before, root)
    def test_private_roots_restored_after_failure(self):
        original = lambda: None
        module = types.SimpleNamespace(ROOT=Path("root"), BUILD=Path("build"), prepare_bundle=original)
        with self.assertRaisesRegex(ValueError, "deliberate"):
            with private_build_root(module, Path("private")):
                self.assertEqual(module.BUILD, Path("private/build"))
                raise ValueError("deliberate")
        self.assertEqual(module.ROOT, Path("root")); self.assertEqual(module.BUILD, Path("build"))
        self.assertIs(module.prepare_bundle, original)

    def test_frozen_tiny3d_copies_dependencies_without_building_and_restores_modules(self):
        import hashlib
        import json
        import sys
        import tempfile
        from unittest.mock import Mock, patch
        from tools import verify_level_loading as verifier

        with tempfile.TemporaryDirectory(prefix="dl64-frozen-dependency-") as temporary:
            canonical = Path(temporary) / "canonical"
            destination = Path(temporary) / "fixture"
            stage = canonical / "build/tiny3d-source"
            library = stage / "build_sdk_main/libt3d.a"
            patch_path = canonical / "tools/tiny3d/patches/depth.patch"
            originals = {library: b"verified archive bytes",
                         stage / "src/t3d/t3d.h": b"public header\n",
                         stage / "src/t3d/generated/state.h": b"generated header\n",
                         patch_path: b"tracked precision patch\n"}
            for path, payload in originals.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)
            (canonical / "build/tiny3d-dependency.json").write_text(json.dumps(
                {"library_sha256": hashlib.sha256(originals[library]).hexdigest()}))
            copied_patch = destination / patch_path.relative_to(canonical)
            copied_patch.parent.mkdir(parents=True, exist_ok=True)
            copied_patch.write_bytes(originals[patch_path])
            original_module = types.ModuleType("build_tiny3d")
            original_module.LIBRARY, original_module.BUILD_SOURCE = library, stage
            original_module.REVISION = "pinned-test-revision"
            original_module.patch_inputs = lambda: [patch_path]
            original_module.build_library = Mock(side_effect=AssertionError("Canonical dependency build must not run"))
            package_module = types.ModuleType("tools.build_tiny3d")
            with patch.object(verifier, "ROOT", canonical), patch.dict(sys.modules, {
                    "build_tiny3d": original_module, "tools.build_tiny3d": package_module}):
                with self.assertRaisesRegex(RuntimeError, "deliberate fixture failure"):
                    with verifier.frozen_tiny3d(destination) as hashes:
                        facade = sys.modules["build_tiny3d"]
                        self.assertIs(sys.modules["tools.build_tiny3d"], facade)
                        self.assertIsNot(facade, original_module)
                        self.assertEqual(facade.REVISION, original_module.REVISION)
                        copied_library = facade.build_library(Path("unused-sdk"))
                        self.assertTrue(copied_library.is_relative_to(destination))
                        self.assertEqual(copied_library.read_bytes(), originals[library])
                        self.assertEqual(facade.patch_inputs(), [copied_patch])
                        for source in (stage / "src").rglob("*.h"):
                            copied = facade.BUILD_SOURCE / source.relative_to(stage)
                            self.assertEqual(copied.read_bytes(), originals[source])
                        self.assertEqual(len(hashes), 4)  # archive, patch, both headers
                        self.assertTrue(all(Path(path).is_relative_to(destination) and
                                            hashlib.sha256(Path(path).read_bytes()).hexdigest() == value
                                            for path, value in hashes.items()))
                        raise RuntimeError("deliberate fixture failure")
                self.assertIs(sys.modules["build_tiny3d"], original_module)
                self.assertIs(sys.modules["tools.build_tiny3d"], package_module)
            original_module.build_library.assert_not_called()
            self.assertTrue(all(path.read_bytes() == payload for path, payload in originals.items()))


if __name__ == "__main__":
    unittest.main()
