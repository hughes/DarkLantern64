"""Audio planning accounting invariants; no waveform assets or SDK required."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("estimate_audio", ROOT / "tools/estimate_audio.py")
estimator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(estimator)


def asset(key="cue", codec="pcm", channels=1, residency="stream"):
    return {"id": key, "label": key, "codec": codec, "sample_rate_hz": 32000,
            "sample_frames": 32000, "channels": channels, "bits_per_sample": 16,
            "encoded_payload_bytes": None, "container_bytes": 24, "residency": residency,
            "resident_padding_bytes": 64, "shared_state_bytes": 16,
            "decoder_workspace_bytes": 32, "decode_block_frames": 32 if codec == "vadpcm" else 1,
            "note": "Invented metadata."}


def voice(key="one", source="cue"):
    return {"id": key, "label": key, "asset": source, "window_ms": 125,
            "max_playback_rate_hz": 32000, "buffer_bits": 16, "padding_bytes": 64,
            "note": "Explicit proposed window, not the runtime allocation."}


def context(key="quiet", voice_ids=None, residents=None, effects=None):
    return {"id": key, "label": key, "voices": voice_ids if voice_ids is not None else ["one"],
            "resident_assets": residents or [], "effects": effects or [], "note": "Planning only."}


def manifest():
    return {"schema_version": 1, "status": "planning_only", "description": "Not runtime state.",
            "output": {"sample_rate_hz": 32000, "buffer_frames": 1280, "buffer_count": 3,
                       "padding_bytes": 8, "note": "Proposed output."},
            "budget": {"ram_bytes": 262144, "status": "illustrative", "note": "Not accepted."},
            "buffer_pool": {"decoded_bytes": 32768, "decoder_workspace_bytes": 4096,
                            "channel_capacity": 8, "retention": "persistent", "note": "Declared pool."},
            "allowances": {"runtime_bytes": 100, "allocator_bytes": 200, "unmeasured_bytes": 300,
                           "reserve_bytes": 400, "unmeasured_items": ["Unmeasured decoder state"],
                           "note": "Manual allowances."},
            "assets": [asset()], "voices": [voice()], "effects": [],
            "contexts": [context()], "transitions": []}


class AudioMemoryTests(unittest.TestCase):
    def setUp(self):
        self.data = manifest()

    def report(self):
        return estimator.estimate_audio(self.data)

    def test_pcm_bytes_stereo_and_output_rounding(self):
        self.data["assets"][0]["channels"] = 2
        report = self.report()
        self.assertEqual(report["summary"]["rom_encoded_payload_bytes"], 128000)
        self.assertEqual(report["voices"][0]["decoded_window_bytes"], 16064)
        self.assertEqual(report["scenarios"][0]["mixer_channels"], 2)
        # 1280 stereo16 frames + 8-byte driver padding, rounded to 16, times 3.
        self.assertEqual(report["scenarios"][0]["components"]["output_buffer_bytes"], 5136 * 3)

    def test_vadpcm_payload_and_lookahead_block_rounding(self):
        self.data["assets"] = [asset(codec="vadpcm", channels=2)]
        self.data["assets"][0]["sample_frames"] = 33
        report = self.report()
        self.assertEqual(report["assets"][0]["encoded_payload_bytes"], 4 * 9 * 2)
        # 4000 frames + 64/4 lookahead => 4016, then 4032 frames.
        self.assertEqual(report["voices"][0]["decoded_window_bytes"], 4032 * 4)

    def test_asset_dedup_keeps_independent_playback_windows(self):
        self.data["assets"][0]["residency"] = "encoded"
        self.data["voices"].append(voice("two"))
        self.data["contexts"] = [context(voice_ids=["one", "two"], residents=["cue"])]
        report = self.report()
        scene = report["scenarios"][0]
        self.assertEqual(report["summary"]["rom_encoded_payload_bytes"], 64000)
        self.assertEqual(scene["components"]["resident_asset_bytes"], 64096)
        self.assertEqual(scene["components"]["shared_asset_state_bytes"], 16)
        self.assertEqual(scene["required_decoded_window_bytes"], 2 * 8064)

    def test_resident_pcm_still_needs_playback_buffer(self):
        self.data["assets"][0]["residency"] = "decoded"
        self.data["contexts"][0]["resident_assets"] = ["cue"]
        scene = self.report()["scenarios"][0]
        self.assertEqual(scene["components"]["resident_asset_bytes"], 64064)
        self.assertEqual(scene["required_decoded_window_bytes"], 8064)
        self.assertEqual(scene["required_decoder_workspace_bytes"], 0)
        self.data["voices"][0]["window_ms"] = 0
        with self.assertRaises(estimator.AudioBudgetError):
            self.report()

    def test_transition_union_dedup_and_independent_effect_tails(self):
        self.data["voices"].append(voice("two"))
        effect = {"id": "hall", "label": "Hall", "sample_rate_hz": 32000, "delay_ms": 100,
                  "channels": 1, "sample_bytes": 2, "state_bytes": 16, "scratch_bytes": 32, "note": "Planned."}
        other = dict(effect, id="yard", label="Yard")
        self.data["effects"] = [effect, other]
        self.data["contexts"] = [context("old", ["one"], effects=["hall"]),
                                 context("new", ["one", "two"], effects=["hall", "yard"])]
        self.data["transitions"] = [{"id": "cross", "label": "Cross", "contexts": ["old", "new"], "extra_bytes": 1000, "note": "Overlap."}]
        scene = next(x for x in self.report()["scenarios"] if x["id"] == "cross")
        self.assertEqual(scene["voice_ids"], ["one", "two"])
        self.assertEqual(scene["required_decoded_window_bytes"], 16128)
        self.assertEqual(scene["components"]["dsp_bytes"], 2 * (6400 + 16 + 32))
        self.assertEqual(scene["components"]["reserve_bytes"], 400)
        self.assertEqual(scene["components"]["transition_extra_bytes"], 1000)

    def test_silence_does_not_release_persistent_pool(self):
        self.data["contexts"].append(context("silent", []))
        scenes = {x["id"]: x for x in self.report()["scenarios"]}
        for scene in scenes.values():
            parts = scene["components"]
            self.assertEqual(parts["decoded_window_bytes"] + parts["retained_decoded_pool_bytes"], 32768)
            self.assertEqual(parts["decoder_workspace_bytes"] + parts["retained_decoder_pool_bytes"], 4096)
        self.assertEqual(scenes["silent"]["required_decoded_window_bytes"], 0)

    def test_pool_shortfall_is_reported_without_hiding_memory(self):
        self.data["buffer_pool"]["decoded_bytes"] = 0
        scene = self.report()["scenarios"][0]
        self.assertEqual(scene["pool_shortfall_bytes"], 8064)
        self.assertEqual(scene["conditional_budget_state"], "pool_exceeded")
        self.assertEqual(scene["components"]["decoded_window_bytes"], 8064)
        self.data["buffer_pool"]["decoded_bytes"] = 32768
        self.data["buffer_pool"]["channel_capacity"] = 1
        self.data["assets"][0]["channels"] = 2
        self.assertEqual(self.report()["scenarios"][0]["channel_shortfall"], 1)

    def test_opus_declared_payload_workspace_and_boundary(self):
        source = asset(codec="opus")
        source.update(sample_rate_hz=48000, decode_block_frames=960,
                      encoded_payload_bytes=12345, decoder_workspace_bytes=16384)
        self.data["assets"] = [source]
        self.data["voices"][0].update(window_ms=20, max_playback_rate_hz=48000)
        report = self.report()
        self.assertEqual(report["assets"][0]["encoded_payload_bytes"], 12345)
        # 960 requested frames + 32-frame lookahead => two whole Opus blocks.
        self.assertEqual(report["voices"][0]["decoded_window_bytes"], 3840)
        for key, value in (("encoded_payload_bytes", None), ("decoder_workspace_bytes", 0)):
            bad = copy.deepcopy(self.data)
            bad["assets"][0][key] = value
            with self.assertRaises(estimator.AudioBudgetError):
                estimator.estimate_audio(bad)

    def test_playback_rate_limit_is_explicit_and_cannot_understate_source(self):
        self.data["assets"][0]["sample_rate_hz"] = 22050
        self.assertEqual(self.report()["voices"][0]["decoded_window_bytes"], 8064)
        self.data["voices"][0]["max_playback_rate_hz"] = 16000
        with self.assertRaises(estimator.AudioBudgetError):
            self.report()

    def test_unknown_coverage_never_produces_verified_pass(self):
        scene = self.report()["scenarios"][0]
        self.assertGreater(scene["remaining_bytes"], 0)
        self.assertFalse(scene["coverage"]["complete"])
        self.assertFalse(scene["coverage"]["measured"])
        self.assertEqual(scene["conditional_budget_state"], "within_budget_unverified")
        self.data["budget"]["ram_bytes"] = 1
        self.assertEqual(self.report()["scenarios"][0]["budget_status"], "over_budget")
        self.data["budget"] = None
        scene = self.report()["scenarios"][0]
        self.assertEqual(scene["budget_status"], "no_budget")
        self.assertIsNone(scene["remaining_bytes"])
        del self.data["allowances"]["unmeasured_items"]
        with self.assertRaises(estimator.AudioBudgetError):
            self.report()

    def test_invalid_numbers_references_duplicate_ids_and_typo_fields(self):
        for value in (True, False, 1.5, float("nan"), float("inf"), -1, "32"):
            bad = copy.deepcopy(self.data)
            bad["output"]["sample_rate_hz"] = value
            with self.subTest(value=value), self.assertRaises(estimator.AudioBudgetError):
                estimator.estimate_audio(bad)
        for mutate in (
            lambda x: x["assets"].append(copy.deepcopy(x["assets"][0])),
            lambda x: x["contexts"][0]["voices"].append("one"),
            lambda x: x["contexts"][0]["voices"].append("missing"),
            lambda x: x["output"].update(buffer_frame=64),
            lambda x: x["budget"].update(status="accepted"),
        ):
            bad = copy.deepcopy(self.data)
            mutate(bad)
            with self.assertRaises(estimator.AudioBudgetError):
                estimator.estimate_audio(bad)

    def test_deterministic_report_and_failed_write_preserves_previous(self):
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / "input.json", Path(folder) / "report.json"
            source.write_text(json.dumps(self.data), encoding="utf-8")
            first = estimator.build_report(source, output)
            original, stamp = output.read_bytes(), output.stat().st_mtime_ns
            source.write_text(json.dumps(self.data, indent=4, sort_keys=True), encoding="utf-8")
            self.assertEqual(first, estimator.build_report(source, output))
            self.assertEqual(stamp, output.stat().st_mtime_ns)
            source.write_text('{"schema_version":1,"schema_version":2}', encoding="utf-8")
            with self.assertRaisesRegex(estimator.AudioBudgetError, "duplicate key"):
                estimator.build_report(source, output)
            self.assertEqual(original, output.read_bytes())

    def test_example_is_explicitly_planning_and_totals_reconcile(self):
        data = json.loads((ROOT / "content/audio_budget.json").read_text(encoding="utf-8"))
        report = estimator.estimate_audio(data)
        self.assertEqual(report["status"], "planning_only")
        self.assertEqual(report["assumptions"]["budget"]["status"], "illustrative")
        self.assertEqual(len(report["source_sha256"]), 64)
        for scene in report["scenarios"]:
            self.assertEqual(scene["total_ram_bytes"], sum(scene["components"].values()))
            self.assertNotEqual(scene["conditional_budget_state"], "pass")

    def test_output_cannot_replace_source_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.json"
            source.write_text(json.dumps(self.data), encoding="utf-8")
            before = source.read_bytes()
            with self.assertRaisesRegex(estimator.AudioBudgetError, "must differ"):
                estimator.build_report(source, source.parent / "." / source.name)
            self.assertEqual(before, source.read_bytes())


if __name__ == "__main__":
    unittest.main()
