"""Profiling log integrity and frame-weighted aggregation; no N64 SDK required."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
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


def window_v2(number=1, samples=None, vis=2, presents=2, rate=1000):
    samples = samples or [{'gameplay': 9, 'display_wait': 7, 'audio_mix': 1},
                          {'gameplay': 11, 'display_wait': 5, 'audio_mix': 1}]
    text = window(number, samples, rate=rate, audio_calls=len(samples))
    elapsed = ','.join(f'{sum(s.values()):x}' for s in samples)
    work = ','.join(f'{sum(s.values())-s.get("display_wait",0):x}' for s in samples)
    extra = f'DL64 profile_samples window={number} first=0 count={len(samples)} elapsed={elapsed} work={work}\n'
    extra += (f'DL64 profile_video window={number} tracked=1 vis={vis} presents={presents} '
              f'repeats={vis-presents} ticks={vis*17} max_gap_vis={1 if vis==presents else 2} max_interval_ticks=17\n')
    extra += f'DL64 profile_workload window={number} frames={len(samples)} geometry_mode=0 '
    extra += ' '.join(f'{name}_{bound}={900 if name=="triangles" else 2}'
                      for name in profiler.WORKLOAD_NAMES for bound in ('min','max')) + '\n'
    return text.replace(f'DL64 profile_end window={number}\n', extra + f'DL64 profile_end window={number}\n')


V2 = 'DL64 profile_enabled version=2 budget_fps=60 clock=cp0_count audio=exclusive waits=elapsed sample_capacity=128 video=vi_origin\n'


class ProfileReportTests(unittest.TestCase):
    def report(self, text):
        return profiler.make_report(text.encode("utf-8"), ROOT / ".dev/test-profile.log")

    def test_host_profiler_buffered_protocol_and_parser_roundtrip(self):
        """The real writer must preserve records across its 512-byte chunks."""
        compiler = next((str(path) for path in (Path('C:/msys64/ucrt64/bin/gcc.exe'),)
                         if path.is_file()), None) or shutil.which('gcc')
        if not compiler:
            self.skipTest('Host GCC is unavailable')
        environment = dict(os.environ, PATH=str(Path(compiler).parent)+os.pathsep+os.environ.get('PATH', ''))
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary)/('profile.exe' if os.name == 'nt' else 'profile')
            subprocess.run([compiler, '-std=c17', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-DDL_PROFILE_TEST', '-I'+str(ROOT/'src'), str(ROOT/'src/dl_profile.c'),
                            str(ROOT/'tests/test_profile.c'), '-lm', '-o', str(executable)],
                           env=environment, check=True, capture_output=True)
            raw = subprocess.check_output([str(executable)], env=environment)
        text = raw.decode().replace('\r\n', '\n')
        expected = window(samples=[{'input': 20, 'display_wait': 32, 'audio_mix': 18, 'other': 30}],
                          audio_calls=2)
        extra = 'DL64 profile_samples window=1 first=0 count=1 elapsed=64 work=44\n'
        extra += ('DL64 profile_video window=1 tracked=0 vis=0 presents=0 repeats=0 '
                  'ticks=0 max_gap_vis=0 max_interval_ticks=0\n')
        extra += 'DL64 profile_workload window=1 frames=0 geometry_mode=0 '
        extra += ' '.join(f'{name}_{bound}=0' for name in profiler.WORKLOAD_NAMES
                          for bound in ('min', 'max')) + '\n'
        expected = V2 + expected.replace('DL64 profile_end window=1\n', extra+'DL64 profile_end window=1\n')
        self.assertGreater(len(expected), 1024)
        self.assertEqual(text[:len(expected)], expected)
        report = self.report(text)
        self.assertEqual(report['frames'], 11)
        self.assertEqual(report['complete_windows'], 8)
        self.assertIn('elapsed=0 work=0\n', text)
        self.assertIn('elapsed=ffffffff work=ffffffff\n', text)

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

    def test_v2_exact_distributions_and_native_presents(self):
        report = self.report(V2 + window_v2() + window_v2(2, vis=3, presents=2))
        self.assertEqual(report['target_fps'], 60)
        self.assertAlmostEqual(report['frame_budget_ms'], 1000/60)
        self.assertEqual(report['distributions']['work']['p50_ms'], 10)
        self.assertEqual(report['distributions']['work']['p95_ms'], 12)
        self.assertEqual(report['distributions']['elapsed']['over_budget_frames'], 4)
        self.assertEqual(report['distributions']['work']['over_budget_frames'], 0)
        self.assertEqual(report['video']['presents'], 4)
        self.assertEqual(report['video']['repeats'], 1)
        self.assertEqual(report['video']['max_gap_vis'], 2)
        self.assertAlmostEqual(report['video']['presented_fps'], 4/.085)
        self.assertEqual(report['workload']['heads_min'], 2)
        self.assertAlmostEqual(report['slots']['gameplay']['percent_of_frame_budget'], 60)

    def test_v2_warmup_selection_validates_whole_log_before_discarding(self):
        text = V2 + window_v2() + window_v2(2)
        report = profiler.make_report(text.encode(), ROOT/'fixture.log', skip_windows=1, target_fps=120)
        self.assertEqual(report['selected_window_ids'], [2])
        self.assertEqual(report['configured_target_fps'], 60)
        self.assertEqual(report['target_fps'], 120)
        self.assertEqual(report['frames'], 2)
        self.assertEqual(report['distributions']['work']['over_budget_frames'], 2)
        with self.assertRaises(profiler.ProfileError):
            profiler.make_report(text.replace('presents=2 repeats=0','presents=1 repeats=0',1).encode(),
                                 ROOT/'fixture.log', skip_windows=1)

    def test_v2_corrupt_samples_and_missing_instrumentation_rejected(self):
        for before, after in [('first=0','first=1'), ('count=2','count=1'),
                              ('work=a,c','work=a,12'), ('elapsed=11,11','elapsed=10,11'),
                              ('presents=2','presents=1'), ('full_min=2','full_min=3')]:
            with self.subTest(after=after), self.assertRaises(profiler.ProfileError):
                self.report(V2 + window_v2().replace(before, after))
        with self.assertRaisesRegex(profiler.ProfileError, 'missing samples'):
            self.report(V2 + window())

    def test_v1_configuration_and_explicit_budget_override(self):
        text='DL64 profile_enabled version=1 budget_fps=30 clock=cp0_count audio=exclusive waits=elapsed\n'+window()
        report=profiler.make_report(text.encode(), ROOT/'fixture.log', target_fps=60)
        self.assertFalse(report['exact_frame_samples_available'])
        self.assertEqual(report['target_fps'],60)
        self.assertEqual(report['configured_target_fps'],30)
        with self.assertRaises(profiler.ProfileError):
            self.report(text.replace('budget_fps=30','budget_fps=0'))

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
