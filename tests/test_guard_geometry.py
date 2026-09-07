"""The cloth armband is part of the sleeve, with no intersecting overlay."""
import json
import math
import os
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class GuardGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(os.environ.get('DL64_GUARD_GEOMETRY', ROOT/'content/assets/guard/guard.character.json'))
        cls.data = json.loads(source.read_bytes())
        cls.mesh = cls.data['mesh']
        cls.arm = next(i for i, bone in enumerate(cls.data['bones']) if bone['id'] == 'upper_arm_r')

    def triangles(self, color):
        # This regression concerns geometry, not an artist's atlas layout. Use
        # the sleeve's authored height interval so a new unwrap cannot disable
        # the check or make it mistake painted skin/metal for the cloth band.
        m = self.mesh
        selected = []
        for i in range(0, len(m['indices']), 3):
            triangle = m['indices'][i:i+3]
            if not all(m['joints'][v] == self.arm for v in triangle):
                continue
            ys = [round(m['vertices'][v][1], 5) for v in triangle]
            band = min(ys) == 1.188 and max(ys) == 1.244
            sleeve = min(ys) >= 1.17 and max(ys) <= 1.32 and not band
            if (color == 13 and band) or (color != 13 and sleeve):
                selected.append(triangle)
        return selected

    def test_band_replaces_sleeve_surface_without_underlying_faces_or_caps(self):
        band, sleeve = self.triangles(13), self.triangles(0)
        self.assertEqual(len(band), 12)  # Six side quads; no separate end caps.
        points = {tuple(self.mesh['vertices'][v]) for t in band for v in t}
        self.assertEqual(len(points), 12)
        self.assertEqual({round(p[1], 5) for p in points}, {1.188, 1.244})
        for triangle in band:
            self.assertEqual({round(self.mesh['vertices'][v][1], 5) for v in triangle}, {1.188, 1.244})
        for triangle in sleeve:
            ys = [self.mesh['vertices'][v][1] for v in triangle]
            self.assertFalse(min(ys) < 1.216 < max(ys), 'Olive sleeve geometry remains beneath the red strip')

    def test_band_vertices_follow_original_sleeve_edges_and_smooth_normals(self):
        m = self.mesh
        sleeve_vertices = {v for t in self.triangles(0) for v in t}
        endpoints = [sorted({tuple(m['vertices'][v]) for v in sleeve_vertices
                             if abs(m['vertices'][v][1]-y) < 2e-6}) for y in (1.17, 1.32)]
        self.assertEqual([len(ring) for ring in endpoints], [6, 6])
        for vertex in {v for t in self.triangles(13) for v in t}:
            p = m['vertices'][vertex]
            fraction = (p[1]-1.17)/.15
            expected = [[a+(b-a)*fraction for a, b in zip(lower, upper)]
                        for lower, upper in zip(*endpoints)]
            self.assertLess(min(math.dist(p, q) for q in expected), 2e-6)
            matching = [v for v in sleeve_vertices if math.dist(p, m['vertices'][v]) < 2e-6]
            self.assertTrue(matching, 'Red/olive seam lacks shared sleeve positions')
            normal = tuple(round(n*127) for n in m['normals'][vertex])
            self.assertTrue(all(tuple(round(n*127) for n in m['normals'][v]) == normal for v in matching),
                            'The color seam introduces a hard lighting crease')

    def test_strip_winding_and_original_joint_bridges(self):
        m = self.mesh
        for t in self.triangles(13):
            a, b, c = [m['vertices'][v] for v in t]
            x, y = [b[i]-a[i] for i in range(3)], [c[i]-a[i] for i in range(3)]
            cross = [x[1]*y[2]-x[2]*y[1], x[2]*y[0]-x[0]*y[2], x[0]*y[1]-x[1]*y[0]]
            for vertex in t:
                self.assertGreater(sum(cross[i]*m['normals'][vertex][i] for i in range(3)), 0)
        cross_joint = sum(len({m['joints'][v] for v in m['indices'][i:i+3]}) > 1
                          for i in range(0, len(m['indices']), 3))
        self.assertEqual(cross_joint, 48)


if __name__ == '__main__':
    unittest.main()
