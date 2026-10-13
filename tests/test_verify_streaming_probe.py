"""Diagnostic-log verification must reject incomplete or contradictory evidence."""
import copy
import json
import unittest

from tools.verify_streaming_probe import decode_log


class StreamingProbeLogTests(unittest.TestCase):
    def setUp(self):
        guard = dict(position=[6, 0, 2], awareness=0, state="PATROL", patrol_index=0)
        self.rows = []
        for tick, current, resident, opened, loot, loads, retired in (
            (0, 2, [2], False, 0, 1, 0),
            (30, 2, [1, 2], True, 0, 2, 0),
            (120, 1, [1, 2], False, 25, 2, 0),
            (200, 1, [1], False, 25, 2, 1),
            (400, 2, [2], False, 25, 3, 2),
            (600, 1, [1, 2], True, 25, 4, 2),
        ):
            self.rows.append(dict(tick=tick, player=[-2 if current == 2 else 2, 0, 0], door_open=opened,
                                  loot_value=loot, collected_objects=4 if loot else 0, guards=[copy.deepcopy(guard)],
                                  stream=dict(current=current, resident=resident, reserved=64 * len(resident), allocated=128,
                                              completed_loads=loads, retired=retired, readbytes=64 * loads, stalls=0)))
        self.terminal = "DL64 stream_test_complete passed=1 input_only=1 audio_calls=600 guard_persisted=1"

    def log(self):
        return "\n".join(["DL64 menu_ready levels=7 initial=lantern-store mode=menu",
                           "DL64 level_start id=streaming-probe preset=default elapsed=0"] +
                         ["DL64 stream_probe " + json.dumps(row) for row in self.rows] +
                         ["DL64 music cue=lanterns_below consumed_frames=40000 generated_blocks=40 queued=4 underruns=0", self.terminal])

    def test_complete_traversal(self):
        result = decode_log(self.log())
        self.assertTrue(result["passed"])
        self.assertEqual(result["route"], [2, 1, 2, 1])
        self.assertEqual(result["allocated_slot_bytes"], 128)

    def test_no_terminal_no_acceptance(self):
        with self.assertRaises(ValueError):
            decode_log(self.log().split(self.terminal)[0])

    def test_reloaded_loot_cannot_reset_or_duplicate(self):
        for value in (0, 50):
            with self.subTest(value=value):
                self.rows[-1]["loot_value"] = value
                with self.assertRaises(ValueError):
                    decode_log(self.log())

    def test_reserved_bytes_and_resident_coverage(self):
        self.rows[-1]["stream"]["reserved"] = 129
        with self.assertRaises(ValueError):
            decode_log(self.log())
        self.rows[-1]["stream"]["reserved"] = 128
        self.rows[-1]["stream"]["resident"] = [2]
        with self.assertRaises(ValueError):
            decode_log(self.log())

    def test_counter_regression_and_missing_audio(self):
        self.rows[-1]["stream"]["readbytes"] = 0
        with self.assertRaises(ValueError):
            decode_log(self.log())
        self.rows[-1]["stream"]["readbytes"] = 256
        self.terminal = self.terminal.replace("audio_calls=600", "audio_calls=0")
        with self.assertRaises(ValueError):
            decode_log(self.log())

    def test_guard_assertion_and_input_only_required(self):
        self.terminal = self.terminal.replace("guard_persisted=1", "guard_persisted=0")
        with self.assertRaises(ValueError):
            decode_log(self.log())
        self.terminal = self.terminal.replace("guard_persisted=0", "guard_persisted=1").replace("input_only=1", "input_only=0")
        with self.assertRaises(ValueError):
            decode_log(self.log())

    def test_menu_selection_and_guard_snapshot_required(self):
        with self.assertRaises(ValueError):
            decode_log(self.log().replace("mode=menu", "mode=direct"))
        self.rows[-2]["checkpoint"] = "east_retired"
        self.rows[-1]["checkpoint"] = "east_reload"
        self.rows[-1]["guards"][0]["awareness"] = .5
        with self.assertRaises(ValueError):
            decode_log(self.log())

    def test_ordinary_route_requires_music_without_underruns(self):
        with self.assertRaises(ValueError):
            decode_log(self.log().replace("underruns=0", "underruns=1"))
        with self.assertRaises(ValueError):
            decode_log(self.log().replace("consumed_frames=40000", "consumed_frames=0"))
        capture = decode_log(self.log().replace("underruns=0", "underruns=1") + "\nDL64 capture_complete views=2")
        self.assertTrue(capture["functional_passed"])
        self.assertFalse(capture["audio_continuity_passed"])


if __name__ == "__main__":
    unittest.main()
