"""Profiling log integrity and frame-weighted aggregation; no N64 SDK required."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("profile_report", ROOT / "tools/profile_report.py")
profiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profiler)


def window(number=1, samples=None, debug=0, rate=1000, audio_calls=0):
    samples = samples if samples is not None else [{"input": 5, "other": 5}]
    totals = [sum(sample.values()) for sample in samples]
    lines = [f"DL64 profile window={number} frames={len(samples)} ticks_per_second={rate} "
             f"frame_ticks={sum(totals)} frame_max_ticks={max(totals)} "
             f"audio_calls={audio_calls} debug={debug}"]
    for name in profiler.SLOTS:
        values = [sample.get(name, 0) for sample in samples]
        lines.append(f"DL64 profile_slot window={number} name={name} "
                     f"ticks={sum(values)} max_ticks={max(values)}")
    lines.append(f"DL64 profile_end window={number}")
    return "\n".join(lines) + "\n"


class ProfileReportTests(unittest.TestCase):
    def report(self, text):
        return profiler.make_report(text.encode("utf-8"), ROOT / ".dev/test-profile.log")

    def test_frame_weighted_totals_budget_and_provenance(self):
        text = "Loaded DarkLantern64\n" + window(samples=[{"input": 10, "other": 10}] * 2)
        text += window(2, [{"input": 90, "other": 10}], debug=1)
        report = self.report(text)
        self.assertEqual(report["frames"], 3)
        self.assertEqual(report["complete_windows"], 2)
        self.assertEqual(report["frame"]["total_ticks"], 140)
        self.assertAlmostEqual(report["frame"]["average_ms"], 140 / 3)
        self.assertAlmostEqual(report["slots"]["input"]["average_ms"], 110 / 3)
        self.assertAlmostEqual(report["slots"]["input"]["percent_of_30fps_budget"], 110)
        self.assertAlmostEqual(report["slots"]["input"]["percent_of_frame_elapsed"], 11000 / 140)
        self.assertEqual(report["slots"]["input"]["max_ms"], 90)
        self.assertEqual(report["debug_frames"], {"0": 2, "1": 1})
        self.assertEqual(report["debug_states"], [0, 1])
        self.assertEqual(report["source_log_sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(report["source_log_bytes"], len(text.encode()))

    def test_slot_maxima_are_not_added(self):
        report = self.report(window(samples=[{"input": 20}, {"other": 20}]))
        self.assertEqual(report["frame"]["max_ms"], 20)
        self.assertEqual(report["slots"]["input"]["max_ms"], 20)
        self.assertEqual(report["slots"]["other"]["max_ms"], 20)

    def test_trailing_partial_block_is_not_included(self):
        text = window() + window(2).split("DL64 profile_slot window=2 name=transforms")[0]
        report = self.report(text)
        self.assertEqual(report["frames"], 1)
        self.assertTrue(report["ignored_trailing_partial_window"])

    def test_trailing_half_written_line_is_deferred(self):
        for suffix in ("DL64 profile win", window(2).removesuffix("\n")):
            with self.subTest(suffix=suffix):
                report = self.report(window() + suffix)
                self.assertEqual(report["complete_windows"], 1)
                self.assertTrue(report["ignored_trailing_partial_window"])

    def test_only_partial_data_or_old_logs_fail(self):
        for text in ("DL64 frame n=30 cpu_ms=12.0\n", window().split("DL64 profile_end")[0]):
            with self.subTest(text=text), self.assertRaisesRegex(profiler.ProfileError, "No complete"):
                self.report(text)

    def test_replay_completion_and_other_game_records_are_ignored(self):
        report = self.report(window() + "DL64 profile_replay_done complete=1 caught=0\n")
        self.assertEqual(report["complete_windows"], 1)
        self.assertFalse(report["ignored_trailing_partial_window"])

    def test_invalid_sum_is_rejected(self):
        text = window().replace("name=input ticks=5 max_ticks=5", "name=input ticks=4 max_ticks=4")
        with self.assertRaisesRegex(profiler.ProfileError, "totals do not equal"):
            self.report(text)

    def test_missing_slot_is_rejected_by_end_marker(self):
        text = "\n".join(line for line in window().splitlines() if "name=input " not in line) + "\n"
        with self.assertRaisesRegex(profiler.ProfileError, "missing slots"):
            self.report(text)

    def test_duplicate_slot_is_rejected_even_in_trailing_window(self):
        text = window() + window(2).split("DL64 profile_slot window=2 name=gameplay")[0]
        text += "DL64 profile_slot window=2 name=input ticks=5 max_ticks=5\n"
        with self.assertRaisesRegex(profiler.ProfileError, "duplicate slot"):
            self.report(text)

    def test_duplicate_skipped_and_noninitial_window_ids_are_rejected(self):
        for text in (window() + window(), window() + window(3), window(2)):
            with self.subTest(text=text), self.assertRaisesRegex(profiler.ProfileError, "expected window"):
                self.report(text)

    def test_nested_window_and_mismatched_ids_are_rejected(self):
        cases = (
            window().replace("DL64 profile_end window=1\n", "") + window(2),
            window().replace("profile_end window=1", "profile_end window=2"),
            window().replace("profile_slot window=1 name=input", "profile_slot window=2 name=input"),
            "DL64 profile_end window=1\n",
        )
        for text in cases:
            with self.subTest(text=text), self.assertRaises(profiler.ProfileError):
                self.report(text)

    def test_zero_frames_and_tick_rate_are_rejected(self):
        for before, after in (("frames=1", "frames=0"), ("ticks_per_second=1000", "ticks_per_second=0")):
            with self.subTest(field=before), self.assertRaisesRegex(profiler.ProfileError, "must be positive"):
                self.report(window().replace(before, after))

    def test_negative_unknown_and_duplicate_fields_are_rejected(self):
        for before, after in (("frames=1", "frames=-1"), ("debug=0", "debug=2"),
                              ("debug=0", "debug=0 debug=0"), ("debug=0", "unknown=0"),
                              ("name=input", "name=unknown"), ("ticks=5", "ticks=-5")):
            with self.subTest(after=after), self.assertRaises(profiler.ProfileError):
                self.report(window().replace(before, after))

    def test_changed_tick_rate_is_rejected(self):
        with self.assertRaisesRegex(profiler.ProfileError, "changed"):
            self.report(window() + window(2, rate=2000))

    def test_inconsistent_maxima_are_rejected(self):
        cases = (
            window().replace("frame_max_ticks=10", "frame_max_ticks=11"),
            window().replace("name=input ticks=5 max_ticks=5", "name=input ticks=5 max_ticks=4"),
            window().replace("name=input ticks=5 max_ticks=5", "name=input ticks=5 max_ticks=6"),
        )
        for text in cases:
            with self.subTest(text=text), self.assertRaises(profiler.ProfileError):
                self.report(text)

    def test_audio_callback_totals_are_explicit(self):
        text = window(samples=[{"input": 3, "audio_mix": 7}], audio_calls=2)
        report = self.report(text)
        self.assertEqual(report["audio_calls"], 2)
        self.assertEqual(report["slots"]["audio_mix"]["average_ms"], 7)
        with self.assertRaisesRegex(profiler.ProfileError, "no audio calls"):
            self.report(text.replace("audio_calls=2", "audio_calls=0"))

    def test_cli_writes_json_and_rejects_bad_log_without_replacing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "ares.log", Path(directory) / "report.json"
            source.write_text(window(), encoding="utf-8")
            command = [sys.executable, str(ROOT / "tools/profile_report.py"), str(source), "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("% budget", result.stdout)
            self.assertEqual(json.loads(output.read_text())["frames"], 1)
            original = output.read_bytes()
            source.write_text(window().replace("frames=1", "frames=0"), encoding="utf-8")
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(output.read_bytes(), original)
            self.assertIn("Profile report failed", result.stderr)

    def test_latest_session_pointer_and_legacy_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(profiler.latest_log(root), root / ".dev/ares/manual-session.log")
            pointer = root / ".dev/ares/latest-session.json"
            pointer.parent.mkdir(parents=True)
            source = root / ".dev/ares/sessions/example/manual-session.log"
            pointer.write_text(json.dumps({"log": str(source)}), encoding="utf-8")
            self.assertEqual(profiler.latest_log(root), source)
            pointer.write_text(json.dumps({"log": ".dev/ares/sessions/relative/manual-session.log"}), encoding="utf-8")
            self.assertEqual(profiler.latest_log(root), root / ".dev/ares/sessions/relative/manual-session.log")
            pointer.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(profiler.ProfileError, "must contain a log path"):
                profiler.latest_log(root)
            pointer.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(profiler.ProfileError, "Cannot read latest session pointer"):
                profiler.latest_log(root)


if __name__ == "__main__":
    unittest.main()
