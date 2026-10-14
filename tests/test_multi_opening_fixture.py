"""A policy-only repair for geometry visible through two independent openings."""
import copy
import json
from pathlib import Path
import unittest

from tools.compile_level import validate
from tools.compile_mission import cook_mission, stream_play_package, validate_stream_bridge


FIXTURES = Path(__file__).parent / "fixtures"


class MultiOpeningFixtureTests(unittest.TestCase):
    def setUp(self):
        self.scene = json.loads((FIXTURES / "stream_multi_opening.json").read_bytes())
        self.manifest = json.loads((FIXTURES / "stream_multi_opening.mission.json").read_bytes())

    def test_two_separate_apertures_have_unoccluded_views_from_one_source(self):
        state = validate(self.scene, FIXTURES, streaming=True)
        # Both rays pass through actual authored openings. Ignore their target
        # wall/landmark when checking occlusion, keeping every intervening box.
        origin = [-7, 1.6, 0]
        for target, target_id in (([8, 1.6, 5], "courtyard-landmark"),
                                  ([9.9, 1.6, -5.4], "north-east-wall")):
            hits = []
            for box in state["colliders"]:
                if box["id"] in (target_id, "north-door"):
                    continue
                self.assertEqual(box["yaw"], 0)
                start, end = 0., 1.
                for axis in range(3):
                    low = box["center"][axis] - box["half_size"][axis]
                    high = box["center"][axis] + box["half_size"][axis]
                    delta = target[axis] - origin[axis]
                    if delta == 0:
                        if not low <= origin[axis] <= high:
                            start, end = 1, 0
                            break
                    else:
                        first = (low - origin[axis]) / delta
                        last = (high - origin[axis]) / delta
                        start = max(start, min(first, last))
                        end = min(end, max(first, last))
                if start < end and end > 0 and start < 1:
                    hits.append(box["id"])
            self.assertEqual(hits, [], target_id)

    def test_working_set_repair_keeps_region_geometry_identical(self):
        raw, before = cook_mission(self.manifest, FIXTURES)
        validate_stream_bridge(before)
        repaired = copy.deepcopy(self.manifest)
        hub = next(row for row in repaired["regions"] if row["id"] == "hub")
        hub["working_set"] = ["hub", "north", "courtyard"]
        changed, after = cook_mission(repaired, FIXTURES)
        validate_stream_bridge(after)
        self.assertEqual(raw[before["directory_bytes"]:], changed[after["directory_bytes"]:])
        self.assertNotEqual(before["package_sha256"], after["package_sha256"])
        self.assertEqual(before["regions"], after["regions"])
        self.assertEqual(before["max_resident_regions"], 3)
        self.assertEqual(after["max_resident_regions"], 3)
        ids = {row["source_id"]: row["id"] for row in before["regions"]}
        hub_index = ids["hub"] - 1
        self.assertEqual(before["policy_regions"][hub_index]["working_mask"], 6)
        self.assertEqual(after["policy_regions"][hub_index]["working_mask"], 7)
        state = validate(self.scene, FIXTURES, streaming=True)
        first = stream_play_package(self.scene, state, before, "before.dlm")
        second = stream_play_package(self.scene, state, after, "after.dlm")
        self.assertNotEqual(first["fingerprint"], second["fingerprint"])
        first_policy = first["stream"]["policy_regions"][hub_index]
        second_policy = second["stream"]["policy_regions"][hub_index]
        self.assertEqual(first_policy["working_mask"], 6)
        self.assertEqual(second_policy["working_mask"], 7)

    def test_reassigning_all_homes_can_end_in_one_region_without_gates(self):
        terminal = copy.deepcopy(self.manifest)
        hub = next(row for row in terminal["regions"] if row["id"] == "hub")
        hub["entities"] = [row["id"] for row in self.scene["entities"]]
        hub["bounds"] = [[-10.11, -.3, -6.11], [10.11, 3.11, 6.11]]
        hub["activation_bounds"] = [[-10.11, -1, -6.11], [10.11, 3.11, 6.11]]
        hub["neighbors"] = []
        hub["working_set"] = ["hub"]
        terminal["regions"] = [hub]
        terminal["transitions"] = []
        _, report = cook_mission(terminal, FIXTURES)
        validate_stream_bridge(report)
        self.assertEqual(report["max_resident_regions"], 1)
        self.assertEqual(report["transitions"], [])
        self.assertEqual(report["policy_regions"][0]["working_mask"], 1)


if __name__ == "__main__":
    unittest.main()
