"""Architectural export invariants; actual movement is tested against game.c separately."""
import unittest

from tools import compile_level, sponza_geometry


class SponzaGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.meshes, cls.manifest = sponza_geometry.build()

    def test_exported_budget_and_stable_ids(self):
        cooked = {}
        for asset in self.manifest['assets']:
            compile_level.identifier(asset['id'], 'asset')
            path = sponza_geometry.ROOT/'content'/asset['uri']
            self.assertTrue(path.is_file(), path)
            cooked[asset['id']] = compile_level.read_obj(path, textured=True)
        instances = self.manifest['instances']
        # This reserve is a generator target, not an extra restriction on
        # subsequent artist edits. Export-back validates the full level budget.
        vertices = sum(len(self.meshes[i['model'].removeprefix('mesh-sponza-')].vertices) for i in instances)
        triangles = sum(len(self.meshes[i['model'].removeprefix('mesh-sponza-')].indices)//3 for i in instances)
        self.assertLessEqual(vertices, 3350)
        self.assertLessEqual(triangles, 2630)
        self.assertLessEqual(len(instances), 106)
        self.assertLessEqual(len(self.manifest['colliders']), 120)
        ids = [row['id'] for row in instances+self.manifest['colliders']]
        self.assertEqual(len(ids), len(set(ids)))
        for ident in ids: compile_level.identifier(ident, 'entity')

    def test_triangle_winding_matches_authored_normals(self):
        for name, mesh in self.meshes.items():
            for n in mesh.normals:
                self.assertAlmostEqual(sponza_geometry.dot(n,n), 1, places=6, msg=name)
            for i in range(0,len(mesh.indices),3):
                a,b,c = mesh.indices[i:i+3]
                face = sponza_geometry.cross(sponza_geometry.sub(mesh.vertices[b],mesh.vertices[a]),
                                            sponza_geometry.sub(mesh.vertices[c],mesh.vertices[a]))
                self.assertGreater(sponza_geometry.dot(face,face), 1e-10, name)
                average = tuple(sum(mesh.normals[v][axis] for v in (a,b,c))/3 for axis in range(3))
                self.assertGreater(sponza_geometry.dot(face,average), 1e-7, name)

    def test_real_stair_treads_and_clear_loop_endpoints(self):
        rise = 5.3/22
        self.assertLess(rise, .28)
        for side in ('east','west'):
            steps = [c for c in self.manifest['colliders'] if c['id'].startswith('sponza-collision-'+side+'-step-')]
            self.assertEqual(len(steps),22)
            tops = sorted(round(c['transform']['position'][1]+c['collider']['half_size'][1],7) for c in steps)
            for i,top in enumerate(tops):self.assertAlmostEqual(top,(i+1)*rise,places=6)
            self.assertEqual(self.manifest['stair_routes'][side][0][1],0)
            self.assertEqual(self.manifest['stair_routes'][side][-1][1],5.3)
            self.assertEqual(len(self.manifest['stair_routes'][side]),4)
        for height in (0,5.3):
            route=self.manifest['landmarks']['ground_loop' if height==0 else 'upper_loop']
            self.assertTrue(all(p[1]==height for p in route))
        # A gallery floor and its underside occupy exactly the same XZ shape.
        top=self.meshes['gallery-north-0'];bottom=self.meshes['ceiling-north']
        self.assertTrue(all(abs(v[1]+top.origin[1]-5.3)<1e-7 for v in top.vertices))
        self.assertTrue(all(abs(v[1]+bottom.origin[1]-5.1)<1e-7 for v in bottom.vertices))

    def test_trim_does_not_wrap_across_moulding_rows(self):
        names={i['model'].removeprefix('mesh-sponza-') for i in self.manifest['instances']
               if i['material']=='mat-sponza-trim'}
        for name in names:
            self.assertTrue(all(0<=uv[1]<=1 for uv in self.meshes[name].uvs),name)

    def test_tread_and_wall_centers_have_unblocked_light_samples(self):
        def unblocked(point):
            for row in self.manifest['colliders']:
                center=row['transform']['position'];half=row['collider']['half_size']
                self.assertFalse(all(abs(point[a]-center[a])<half[a]-1e-6 for a in range(3)),row['id'])
        for side in ('east','west'):
            mesh=self.meshes['stairs-'+side]
            for row in self.manifest['colliders']:
                if not row['id'].startswith('sponza-collision-'+side+'-step-'):continue
                center=row['transform']['position'];half=row['collider']['half_size']
                top=(center[0],center[1]+half[1],center[2])
                matches=[i for i,v in enumerate(mesh.vertices) if max(abs(v[a]+mesh.origin[a]-top[a]) for a in range(3))<1e-6]
                self.assertEqual(len(matches),1,row['id'])
                self.assertEqual(mesh.indices.count(matches[0]),4,'Tread center must sample all four fan triangles')
                unblocked((top[0],top[1]+.035,top[2]))
        for name,mesh in self.meshes.items():
            if not name.startswith('aisle-wall-'):continue
            for y in (1.5,4.05,6.85,9.45):
                matches=[i for i,v in enumerate(mesh.vertices) if abs(v[0])<1e-6 and abs(v[1]+mesh.origin[1]-y)<1e-6]
                self.assertEqual(len(matches),1,name)
                i=matches[0];self.assertEqual(mesh.indices.count(i),4)
                unblocked(tuple(mesh.vertices[i][a]+mesh.origin[a]+.035*mesh.normals[i][a] for a in range(3)))

    def test_roofs_have_courtyard_visible_soffits(self):
        roofs=[mesh for name,mesh in self.meshes.items() if name.startswith('roof-')]
        self.assertEqual(len(roofs),6)
        for mesh in roofs:
            downward=sum(all(mesh.normals[v][1]<-.99 for v in mesh.indices[i:i+3]) for i in range(0,len(mesh.indices),3))
            self.assertEqual(downward,2,mesh.name)


if __name__ == '__main__':unittest.main()
