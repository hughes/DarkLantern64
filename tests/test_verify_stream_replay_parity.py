"""The parity verifier must reject missing functional state and false evidence."""
import copy
import json
import math
import unittest
from tools.verify_stream_replay_parity import compare, decode_log, functional_state, GAME_FIELDS, STREAM_FIELDS


class StreamReplayParityTests(unittest.TestCase):
    def state(self):
        row = dict.fromkeys(GAME_FIELDS, 0)
        row.update(player=[0.0, 0.0, 0.0], yaw=0.0, pitch=0.0, visibility=.5, enemies=[], objects=[], events=[],
                   stream=dict.fromkeys(STREAM_FIELDS, 0))
        return row

    def test_numeric_tolerance_does_not_hide_policy_change(self):
        native = self.state()
        console = copy.deepcopy(native)
        console["player"][0] = .0019
        compare(functional_state(native), functional_state(console))
        console["stream"]["published_mask"] = 1
        with self.assertRaisesRegex(ValueError, "published_mask"):
            compare(functional_state(native), functional_state(console))

    def test_large_and_nonfinite_numeric_divergence_rejected(self):
        for value in (.0021, math.inf, math.nan):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    compare(.0, value)

    def test_no_completion_or_runtime_failure_is_not_parity(self):
        line = "DL64 replay_tick " + json.dumps(self.state())
        with self.assertRaises(ValueError):
            decode_log(line, "test")
        with self.assertRaises(ValueError):
            decode_log(line + "\nDL64 input_replay_done\nDL64 input_replay_done", "test")
        with self.assertRaises(ValueError):
            decode_log("RSP CRASH |\n" + line + "\nDL64 input_replay_done", "test")
        log = "DL64 replay_start fingerprint=test stream_boundary=settled\n" + line + "\nDL64 input_replay_done"
        self.assertEqual(len(decode_log(log, "test")), 1)
        with self.assertRaises(ValueError):
            decode_log(log, "other-content")
        with self.assertRaises(ValueError):
            decode_log(log, "test", "frame")

    def test_missing_required_stream_state_rejected(self):
        row = self.state()
        del row["stream"]["queued_transition"]
        with self.assertRaises(KeyError):
            functional_state(row)

    def test_platform_counters_excluded_and_event_counts_exact(self):
        native, console = self.state(), self.state()
        native["stream"]["revision"] = 2
        console["stream"]["revision"] = 200
        compare(functional_state(native), functional_state(console))
        native["events"] = [dict(kind=3, position=[0., 0., 0.], time=1., strength=1., actor=0, detail=0, previous=0)]
        with self.assertRaisesRegex(ValueError, "events: count differs"):
            compare(functional_state(native), functional_state(console))


if __name__ == "__main__":
    unittest.main()
