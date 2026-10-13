"""Run in background Blender: strict rejection plus precise, non-destructive selection."""
import json
from pathlib import Path
import sys

import bpy
import bmesh

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/blender'))
from darklantern64_export import _obj, TriangleExportError, register, unregister


def snapshot(obj):
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    return dict(vertices=[tuple(v.co) for v in obj.data.vertices],
                polygons=[tuple(p.vertices) for p in obj.data.polygons])


def fixture(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.materials.append(bpy.data.materials.new(name + ' material'))
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def diagnose(obj):
    bpy.context.view_layer.update()
    try:
        _obj(bpy.context, obj, False, local_space=True)
    except TriangleExportError as error:
        assert error.problem['object'] == obj.name
        assert 'indices start at 0' in str(error)
        assert 'Select problem face' in str(error)
        return error.problem
    raise AssertionError('Invalid triangle was silently accepted')


def main():
    register()
    try:
        # This reproduces the user's non-collapsed quad with a redundant point
        # on one straight boundary. It must still reject; no automatic cleanup.
        obj = fixture('Boundary midpoint', [(0,0,2),(2,0,0),(0,0,0),(0,0,1)], [(0,1,2,3)])
        before = snapshot(obj)
        problem = diagnose(obj)
        assert problem['face'] == 0 and problem['area_m2'] == 0
        assert set(problem['vertices']) == {0,2,3}
        bpy.context.scene['dl64_export_problem'] = json.dumps(problem)
        assert bpy.ops.dl64.select_problem_face() == {'FINISHED'}
        bm = bmesh.from_edit_mesh(obj.data)
        assert [face.index for face in bm.faces if face.select] == [0]
        assert snapshot(obj) == before
        assert bpy.ops.dl64.select_problem_face(vertices_only=True) == {'FINISHED'}
        bm = bmesh.from_edit_mesh(obj.data)
        assert {v.index for v in bm.verts if v.select} == {0,2,3}
        assert snapshot(obj) == before
        bpy.ops.object.mode_set(mode='OBJECT')
        collapsed = fixture('Collapsed triangle', [(0,0,0),(1,0,0),(2,0,0)], [(0,1,2)])
        assert diagnose(collapsed)['area_m2'] == 0
        tiny = fixture('Small nonzero triangle', [(0,0,0),(1,0,0),(0,0,1e-7)], [(0,1,2)])
        assert 0 < diagnose(tiny)['area_m2'] < 5e-7
        valid = fixture('Valid triangle', [(0,0,0),(1,0,0),(0,0,1)], [(0,1,2)])
        bpy.context.view_layer.update()
        assert b'f 1 2 3' in _obj(bpy.context, valid, False, local_space=True)
        # Errors raised through the public asset operator must publish their
        # selection metadata, without publishing a pack.
        for o in bpy.context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        obj['dl64_asset_id'] = 'diagnostic-boundary'
        obj.data.materials[0]['dl64_material_id'] = 'mat-diagnostic-boundary'
        out = ROOT / 'build/geometry-diagnostic-fixture'
        out.mkdir(parents=True, exist_ok=True)
        try:
            bpy.ops.export_scene.darklantern64_pack(filepath=str(out/'assets/pack.json'), content_root=str(out))
        except RuntimeError as error:
            assert 'face 0' in str(error)
        assert json.loads(bpy.context.scene['dl64_export_problem'])['object'] == obj.name
        assert not (out/'assets/pack.json').exists()
        print(json.dumps(dict(passed=True, checks=['strict collinear quad rejection','collapsed and tiny triangle rejection',
              'face and triangle-vertex selection','selection preserves geometry','valid mesh exports','operator diagnostics without publication'])))
    finally:
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.mode_set(mode='OBJECT')
        unregister()


if __name__ == '__main__':
    main()
