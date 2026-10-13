import unittest

from tools.thief_import.geometry import clean_polygon, game_point, polygon_box, floor_position


class ThiefGeometryTests(unittest.TestCase):
    def test_right_handed_metres(self):
        self.assertEqual(game_point((10, 20, 30)), [3.048, 9.144, -6.096])

    def test_source_edge_splits_do_not_make_degenerate_fans(self):
        points = [[0,0,0], [0,0,1], [0,0,2], [2,0,2], [2,0,0]]
        result = clean_polygon(points, [[p[0],p[2]] for p in points])
        self.assertEqual(len(result[0]), 4)
        self.assertEqual(result[2], [0,1,0])
        center, half = polygon_box(result[0], result[2])
        self.assertEqual(center, [1,-.02,1])
        self.assertEqual(half, [1,.02,1])

    def test_triangle_is_not_filled_by_its_bounds(self):
        points = [[0,0,0], [0,0,2], [2,0,0]]
        self.assertIsNone(polygon_box(points, [0,1,0]))

    def test_snap_actor_to_containing_floor_only(self):
        floor = {"points": [[0,0,0],[0,0,2],[2,0,2],[2,0,0]], "normal": [0,1,0]}
        self.assertEqual(floor_position([1,1,1], [floor]), [1,0,1])
        self.assertIsNone(floor_position([3,1,1], [floor]))


if __name__ == "__main__":
    unittest.main()
