import json
import unittest

from tools.verify_bafford_expanded import CAPTURE_COMPLETE, COMPLETE, NAMES, PREFIX, decode_log


class ExpandedBaffordLogTests(unittest.TestCase):
    def regions(self):
        return [dict(id=1, bounds=[[-3.0481, -3, 37.8], [15.6, 7, 52.5]], chunk_bytes=18000,
                     collider_count=73, material_ids=list(range(12)), triangle_count=195),
                dict(id=2, bounds=[[-15.7, -3, 32], [-3.0479, 13, 51.5]], chunk_bytes=32000,
                     collider_count=90, material_ids=list(range(19)), triangle_count=398)]

    def rows(self):
        ticks = (90, 180, 680, 681, 702, 745, 800, 910, 1070, 1071, 1080, 1120, 1180, 1220, 1690)
        positions = ((-6.522049, -2.4384, 49.8253), (-6.522049, -2.4384, 48.5),
                     (7.8, -2.4384, 48.5), (7.8, -2.4384, 48.5), (8.5, -1.65, 48.5),
                     (9.8, -2.1336, 48.5), (11.5, -2.1336, 48.5), (15.1, -2.4384, 48.5),
                     (12.3, -2.4384, 48.5), (12.3, -2.4384, 48.5), (11.98, -1.9, 48.5),
                     (10.8, -2.1336, 48.5), (8.8, -2.1336, 48.5), (7.8, -2.4384, 48.5),
                     (-6.522049, -2.4384, 48.5))
        jump_counts = [0] * 3 + [1] * 6 + [2] * 6
        return [dict(checkpoint=name, tick=tick, elapsed=tick / 60, player=list(point),
                     grounded=0 if name.endswith("_air") else 1,
                     vertical_velocity=1 if name.endswith("_air") else 0,
                     jump_inputs=jumps, collision_clear=1, heap_used=1_400_000,
                     models=31, colliders=163, textures=23,
                     stream=dict(current=2 if point[0] < -3.048 else 1, resident=[1, 2],
                                 reserved=50000, allocated=64000, completed_loads=2, retired=0,
                                 readbytes=50000, stalls=8))
                for name, tick, point, jumps in zip(NAMES, ticks, positions, jump_counts)]

    def log(self, rows=None, capture=False, underruns=0):
        lines = ["DL64 menu_ready mode=menu levels=7", "DL64 level_start id=bafford-expanded preset=default"]
        lines.append("DL64 stream_view resident_mask=3 triangles=593 colliders=163 loads=2 retired=0 reserved=50000 allocated_slots=64000")
        lines += [PREFIX + json.dumps(row) for row in (self.rows() if rows is None else rows)]
        lines.append(f"DL64 music cue=lanterns_below consumed_frames=500000 generated_blocks=400 queued=3 underruns={underruns}")
        if capture:
            lines += ["DL64 capture_begin id=1 width=320 height=240", CAPTURE_COMPLETE]
        lines.append(COMPLETE + " passed=1 input_only=1 coverage=1 bridge_jumps=2 collision_clear=1 audio_calls=600")
        return "\n".join(lines)

    def test_actual_jump_and_return_route(self):
        result = decode_log(self.log(), self.regions(), require_music=True, expected_textures=23)
        self.assertEqual(result["route"], [2, 1, 2])
        self.assertEqual(result["bridge_jumps"], 2)
        self.assertEqual(result["newly_walked_source_cells"], [4366, 3831, 4987])

    def test_uniformly_wrong_resources_rejected_against_cook(self):
        for field in ("models", "colliders", "textures"):
            rows = self.rows()
            for row in rows:
                row[field] = 1
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "count differs"):
                decode_log(self.log(rows), self.regions(), expected_textures=23)

    def test_cooked_triangles_and_exact_slot_capacity_required(self):
        rows = self.rows()
        for row in rows:
            row["stream"]["allocated"] = 1_000_000_000
        with self.assertRaisesRegex(ValueError, "Slot allocation"):
            decode_log(self.log(rows), self.regions(), expected_textures=23)
        for text in (self.log().replace("triangles=593", "triangles=1"),
                     "\n".join(line for line in self.log().splitlines() if not line.startswith("DL64 stream_view "))):
            with self.subTest(), self.assertRaisesRegex(ValueError, "stream view"):
                decode_log(text, self.regions(), expected_textures=23)

    def test_airborne_rise_and_landing_cannot_be_replaced_by_views(self):
        for index, field, value in ((4, "grounded", 1), (4, "vertical_velocity", 0),
                                    (4, "player", [8.5, -2.4384, 48.5]),
                                    (10, "jump_inputs", 1), (5, "grounded", 0),
                                    (5, "player", [9.8, -2.4384, 48.5])):
            rows = self.rows()
            rows[index][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                decode_log(self.log(rows))

    def test_extended_endpoint_and_supported_bridge_are_required(self):
        rows = self.rows()
        rows[7]["player"][0] = 7.8
        with self.assertRaises(ValueError):
            decode_log(self.log(rows))
        rows = self.rows()
        rows[6]["player"][1] += .1
        with self.assertRaises(ValueError):
            decode_log(self.log(rows))

    def test_timing_discontinuity_or_reset_is_rejected(self):
        for index, field, value in ((4, "tick", 681), (4, "elapsed", 100),
                                    (4, "player", [8.5, -1.4, 48.5])):
            rows = self.rows()
            rows[index][field] = value
            if field == "player":
                rows[index]["tick"] = rows[index - 1]["tick"] + 1
                rows[index]["elapsed"] = rows[index]["tick"] / 60
            with self.subTest(field=field), self.assertRaises(ValueError):
                decode_log(self.log(rows))

    def test_body_coverage_and_heap_growth_are_rejected(self):
        for field, value in (("collision_clear", 0), ("player", [16, -2.4384, 48.5]),
                             ("heap_used", 1_400_008)):
            rows = self.rows()
            rows[-1][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                decode_log(self.log(rows))

    def test_residency_and_slots_remain_conservative_and_bounded(self):
        for field, value in (("resident", [1]), ("retired", 1), ("completed_loads", 3),
                             ("allocated", 100000), ("reserved", 90000), ("readbytes", 2)):
            rows = self.rows()
            rows[-1]["stream"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                decode_log(self.log(rows))

    def test_completion_belongs_after_launch_and_all_checkpoints(self):
        lines = self.log().splitlines()
        for text in ("\n".join(lines[:2] + [lines[-1]] + lines[2:-1]),
                     self.log().replace("input_only=1", "input_only=0"), self.log(self.rows()[:-1]),
                     self.log() + "\nASSERTION FAILED: floor"):
            with self.subTest(), self.assertRaises(ValueError):
                decode_log(text)

    def test_framebuffer_timing_is_separate_from_ordinary_music(self):
        with self.assertRaises(ValueError):
            decode_log(self.log(underruns=1))
        capture = decode_log(self.log(capture=True, underruns=2))
        self.assertTrue(capture["functional_passed"])
        self.assertFalse(capture["audio_continuity_passed"])


if __name__ == "__main__":
    unittest.main()
