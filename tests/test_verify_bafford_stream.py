import json
import unittest

from tools.verify_bafford_stream import CAPTURE_COMPLETE, COMPLETE, PREFIX, decode_log


class BaffordStreamLogTests(unittest.TestCase):
    def rows(self):
        names = ("start", "west_ready", "east_crossed", "east_turnaround", "west_crossed", "west_return", "east_return")
        ticks = (90, 200, 450, 600, 850, 1050, 1450)
        positions = ((-6.522049, 49.825294), (-6.522049, 49.4), (-2.5, 49.4), (2.2, 49.4),
                     (-3.6, 49.4), (-6.522049, 49.4), (2.2, 49.4))
        return [dict(checkpoint=name, tick=tick, elapsed=tick / 60,
                     player=[x, -2.4384, z], grounded=1, collision_clear=1,
                     heap_used=1_000_000, models=12, colliders=40, textures=8,
                     stream=dict(current=2 if x < -3.048 else 1, resident=[1, 2], reserved=4000,
                                 allocated=4096, completed_loads=2, retired=0, readbytes=4000, stalls=8))
                for name, tick, (x, z) in zip(names, ticks, positions)]

    def log(self, rows=None, terminal=True, music=True, underruns=0, capture=False):
        lines = ["DL64 menu_ready mode=menu levels=8", "DL64 level_start id=bafford-stream preset=default"]
        lines += [PREFIX + json.dumps(row) for row in (rows or self.rows())]
        if music:
            lines.append(f"DL64 music cue=lanterns_below consumed_frames=580032 generated_blocks=460 queued=3 underruns={underruns}")
        if capture:
            lines += ["DL64 capture_begin id=1 width=320 height=240", CAPTURE_COMPLETE]
        if terminal:
            lines.append(COMPLETE + " passed=1 input_only=1 coverage=1 feet_stable=1 collision_clear=1 audio_calls=465")
        return "\n".join(lines)

    def test_complete_bounded_input_traversal(self):
        regions = [dict(id=1, bounds=[[-3.048, -3, 43], [3, 4, 52]], chunk_bytes=2000),
                   dict(id=2, bounds=[[-8, -3, 43], [-3.048, 4, 52]], chunk_bytes=2000)]
        result = decode_log(self.log(), regions, require_music=True)
        self.assertEqual(result["route"], [2, 1, 2, 1])
        self.assertEqual(result["loads"], 2)
        self.assertEqual(result["retirements"], 0)
        self.assertTrue(result["audio_continuity_passed"])

    def test_missing_or_failed_assertions_rejected(self):
        for text in (self.log(terminal=False), self.log().replace("input_only=1", "input_only=0"),
                     self.log().replace("audio_calls=465", "audio_calls=0"),
                     self.log() + "\nASSERTION FAILED: floor", self.log() + "\n" + COMPLETE):
            with self.subTest(text=text[-100:]), self.assertRaises(ValueError):
                decode_log(text)

    def test_menu_and_exact_checkpoint_sequence_required(self):
        for text in (self.log().replace("mode=menu", "mode=direct"),
                     self.log().replace("id=bafford-stream ", "id=bafford-entry "),
                     self.log(self.rows()[:-1]), self.log(self.rows() + [self.rows()[-1]])):
            with self.subTest(text=text[:80]), self.assertRaises(ValueError):
                decode_log(text)

    def test_floor_body_and_coverage_required(self):
        for field, value in (("grounded", 0), ("collision_clear", 0), ("player", [3, -2.4384, 49.4]),
                             ("player", [2.2, -3, 49.4]), ("player", [float("nan"), -2.4384, 49.4])):
            rows = self.rows()
            rows[-1][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                decode_log(self.log(rows))

    def test_open_street_requires_both_regions_and_fixed_payload(self):
        for field, value in (("resident", [1]), ("retired", 1), ("completed_loads", 3),
                             ("allocated", 8192), ("reserved", 5000), ("readbytes", 10)):
            rows = self.rows()
            rows[-1]["stream"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                decode_log(self.log(rows))

    def test_walk_speed_time_and_waypoints_reject_teleports_or_resets(self):
        rows = self.rows()
        rows[2]["elapsed"] = rows[1]["elapsed"] + .01
        with self.assertRaisesRegex(ValueError, "walking speed|60 Hz"):
            decode_log(self.log(rows))
        rows = self.rows()
        rows[4]["tick"] = rows[3]["tick"]
        with self.assertRaisesRegex(ValueError, "regressed"):
            decode_log(self.log(rows))
        rows = self.rows()
        rows[3]["player"][0] -= .5
        with self.assertRaisesRegex(ValueError, "waypoint"):
            decode_log(self.log(rows))

    def test_records_must_follow_launch_and_precede_completion(self):
        lines = self.log().splitlines()
        early_completion = lines[:2] + [lines[-1]] + lines[2:-1]
        early_checkpoints = lines[2:-2] + lines[:2] + lines[-2:]
        for reordered in (early_completion, early_checkpoints):
            with self.subTest(), self.assertRaisesRegex(ValueError, "interval"):
                decode_log("\n".join(reordered))

    def test_fixed_tick_time_cannot_be_inflated(self):
        rows = self.rows()
        for row in rows:
            row["elapsed"] *= 100
        with self.assertRaisesRegex(ValueError, "60 Hz"):
            decode_log(self.log(rows))

    def test_heap_stable_after_initial_traversal(self):
        rows = self.rows()
        rows[0]["heap_used"] -= 1024
        self.assertTrue(decode_log(self.log(rows))["passed"])
        rows[5]["heap_used"] += 32
        with self.assertRaisesRegex(ValueError, "Heap grew"):
            decode_log(self.log(rows))

    def test_music_and_capture_timing_are_separate(self):
        with self.assertRaisesRegex(ValueError, "underrun"):
            decode_log(self.log(underruns=1))
        with self.assertRaisesRegex(ValueError, "lacks playback"):
            decode_log(self.log(music=False), require_music=True)
        result = decode_log(self.log(underruns=2, capture=True), require_music=True)
        self.assertTrue(result["functional_passed"])
        self.assertFalse(result["audio_continuity_passed"])
        self.assertIsNone(decode_log(self.log(music=False))["audio_continuity_passed"])


if __name__ == "__main__":
    unittest.main()
