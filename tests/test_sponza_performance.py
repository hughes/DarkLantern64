"""A slower live scene is valid evidence; frozen gameplay or lost VI timing is not."""
from pathlib import Path
import unittest

from test_profile_report import V2, window_v2
from tools.verify_sponza_performance import analyze


def log(fps=60, terminal=False, tracked=True):
    text = V2 + "DL64 level_start id=sponza_courtyard preset=courtyard\n"
    for index in range(1, 34):
        text += (f"DL64 frame heap=1446064/7000000 caught={int(terminal and index>20)} complete=0\n"
                 f"DL64 enemy id=ground-guard position={index*.1},0,4.7 state=PATROL patrol_index=0\n"
                 f"DL64 enemy id=upper-guard position={index*.1},5.3,-4.7 state=PATROL patrol_index=1\n")
        frame = 60000 // fps
        row = window_v2(index, [{"gameplay": frame-450, "audio_mix": 50, "display_wait": 400}]*fps,
                        vis=60, presents=fps, rate=60000)
        row = row.replace("ticks=1020 max_gap_vis", "ticks=60000 max_gap_vis").replace("max_interval_ticks=17", "max_interval_ticks=1000")
        if not tracked: row = row.replace("tracked=1", "tracked=0")
        text += row
    return text.encode()


class SponzaPerformanceEvidence(unittest.TestCase):
    def test_slower_ordinary_game_is_valid_without_a_60fps_claim(self):
        report = analyze(log(fps=30), Path("test.log"), "courtyard")
        self.assertTrue(report["diagnostic_valid"])
        self.assertFalse(report["meets_60fps_sample"])
        self.assertEqual(report["profile"]["video"]["presented_fps"], 30)

    def test_capture_after_player_caught_cannot_be_headline_gameplay_evidence(self):
        report = analyze(log(terminal=True), Path("test.log"), "courtyard")
        self.assertFalse(report["diagnostic_valid"])
        self.assertFalse(report["checks"]["ordinary_live_gameplay"])

    def test_missing_vi_observer_rejected(self):
        report = analyze(log(tracked=False), Path("test.log"), "courtyard")
        self.assertFalse(report["diagnostic_valid"])
        self.assertFalse(report["checks"]["reliable_vi_observation"])

    def test_crlf_complete_windows_and_true_duration(self):
        report = analyze(log().replace(b"\n", b"\r\n"), Path("test.log"), "courtyard")
        self.assertTrue(report["diagnostic_valid"])
        self.assertEqual(report["profile"]["video"]["seconds"], 30)
        self.assertEqual(report["profile"]["complete_windows"], 30)


if __name__ == "__main__":
    unittest.main()
