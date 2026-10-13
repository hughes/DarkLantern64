"""Check collision helpers on an isolated level checkout in background Blender."""
import copy
import json
from pathlib import Path
import sys

import bpy
from mathutils import Matrix

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools/blender'))
from darklantern64_export import register, unregister
from darklantern64_export.collision_tools import (objects, spec, proxies, organize_sponza,
    fit_bounds, apply_stairs, apply_prism, review_status, show_collision, fingerprint)
from darklantern64_export.scene_bridge import collect, metadata, export_scene


def main(output):
    output.mkdir(parents=True,exist_ok=True)
    register()
    try:
        data = metadata(bpy.context.scene)
        # All publications go to test-only exchange folders. Never save the source.
        data['exchange'] = str(output/'scene.json')
        bpy.context.scene['dl64_scene_checkout'] = json.dumps(data)
        rows = objects(bpy.context.scene)
        before,meshes = collect(bpy.context,data)
        organize_sponza(bpy.context.scene)
        after,_ = collect(bpy.context,data)
        assert before==after, 'Organizing an already linked scene must be idempotent'
        east = rows['sponza-parapet-east']
        owner = rows['sponza-collision-parapet-east']
        assert owner.parent == east
        saved_mesh = fingerprint(east)
        fit_bounds(bpy.context,east)
        assert fingerprint(east)==saved_mesh, 'Fit changed visible geometry'
        assert review_status(bpy.context.scene,east)=='Geometry and collision reviewed together'
        saved_collision = copy.deepcopy(collect(bpy.context,data)[0][owner['dl64_entity_id']])
        # Move one top vertex to trigger an explicit review warning.
        vertex = max(east.data.vertices,key=lambda v:v.co.z)
        vertex.co.z+=.1
        east.data.update();bpy.context.view_layer.update()
        assert review_status(bpy.context.scene,east)=='Geometry changed: review collision'
        assert collect(bpy.context,data)[0][owner['dl64_entity_id']]==saved_collision
        fit_bounds(bpy.context,east)
        assert review_status(bpy.context.scene,east)=='Geometry and collision reviewed together'
        related = show_collision(bpy.context.scene,east)
        assert len(related)==1
        assert all(not p.hide_get() for p in related)
        # Parenting moves the fitted box with its visual, including yaw and scale.
        original = east.matrix_world.copy()
        east.location.x+=.3
        east.rotation_euler.z+=.2
        east.scale.x*=1.1
        bpy.context.view_layer.update()
        moved = collect(bpy.context,data)[0]
        assert abs(moved[owner['dl64_entity_id']]['transform']['position'][0]-moved[east['dl64_entity_id']]['transform']['position'][0])<1e-5
        assert review_status(bpy.context.scene,east)=='Geometry and collision reviewed together'
        east.matrix_world=original
        bpy.context.view_layer.update()
        apply_prism(bpy.context,east)
        assert len(east.data.vertices)==8 and len(east.data.polygons)==12
        assert spec(east)['mode']=='box'
        assert review_status(bpy.context.scene,east)=='Geometry and collision reviewed together'
        for side in ('east','west'):
            root = rows['sponza-stairs-'+side]
            step = apply_stairs(bpy.context,root)
            assert abs(step-5.3/22)<1e-6
            assert len(spec(root)['colliders'])==23
            assert len(spec(rows['sponza-stair-shell-'+side])['colliders'])==5
            # Default geometry matches all original treads' collision footprints.
            generated = collect(bpy.context,data)[0]
            for ident in spec(root)['colliders']:
                row = generated[ident]
                old = before[ident]
                for axis in range(3):
                    c = row['transform']['position'][axis] + row['collider']['center'][axis]
                    oldc = old['transform']['position'][axis] + old['collider']['center'][axis]
                    assert abs(c-oldc)<1e-5,(ident,c,oldc)
                    assert abs(row['collider']['half_size'][axis]-old['collider']['half_size'][axis])<1e-5
        d=metadata(bpy.context.scene);d['exchange']=str(output/'default/scene.json');bpy.context.scene['dl64_scene_checkout']=json.dumps(d)
        export_scene(bpy.context)
        root=rows['sponza-stairs-east']
        snapshot = collect(bpy.context,metadata(bpy.context.scene))
        root['dl64_stair_steps']=2
        try:
            apply_stairs(bpy.context,root)
        except ValueError as error:
            assert '0.28' in str(error)
        else:
            raise AssertionError('Unwalkable stair parameters accepted')
        assert collect(bpy.context,metadata(bpy.context.scene))==snapshot
        # Removing a step from each flight is explicit, unlike ordinary source deletion.
        root['dl64_stair_steps']=10;root['dl64_stair_rise']=5.3
        apply_stairs(bpy.context,root)
        retired=json.loads(bpy.context.scene['dl64_generated_removals'])
        assert {r['id'] for r in retired}=={'sponza-collision-east-step-0-10','sponza-collision-east-step-1-10'}
        d=metadata(bpy.context.scene);d['exchange']=str(output/'reduced/scene.json');bpy.context.scene['dl64_scene_checkout']=json.dumps(d)
        export_scene(bpy.context)
        root['dl64_stair_steps']=11;root['dl64_stair_rise']=5.3
        apply_stairs(bpy.context,root)
        assert json.loads(bpy.context.scene['dl64_generated_removals'])==[]
        d=metadata(bpy.context.scene);d['exchange']=str(output/'restored/scene.json');bpy.context.scene['dl64_scene_checkout']=json.dumps(d)
        export_scene(bpy.context)
        # Assigning duplicate IDs must also remap collision ownership, without
        # moving or relinking the original visual's boxes.
        original_owner=proxies(bpy.context.scene,east)[0].parent
        duplicate=east.copy();east.users_collection[0].objects.link(duplicate)
        duplicate_owner=original_owner.copy();east.users_collection[0].objects.link(duplicate_owner)
        duplicate_owner.parent=duplicate
        duplicate_proxy=original_owner.children[0].copy();east.users_collection[0].objects.link(duplicate_proxy)
        duplicate_proxy.parent=duplicate_owner
        bpy.ops.object.select_all(action='DESELECT')
        for obj in (duplicate,duplicate_owner):
            obj.select_set(True)
        bpy.context.view_layer.objects.active=duplicate
        assert bpy.ops.dl64.new_instance_id()=={'FINISHED'}
        assert spec(duplicate)['colliders']==[duplicate_owner['dl64_entity_id']]
        assert duplicate_owner['dl64_visual_owner']==duplicate['dl64_entity_id']
        assert original_owner.parent==east
        collect(bpy.context,metadata(bpy.context.scene))
        for obj in (duplicate_proxy,duplicate_owner,duplicate):
            bpy.data.objects.remove(obj,do_unlink=True)
        report=dict(passed=True,checks=['idempotent parenting','fit preserves mesh','geometry-change warning',
            'simultaneous overlays','parent motion/rotation/scale','default east/west step bounds match level',
            'generated treads and shell','unwalkable stair rejection before mutation','stable step retirement and recreation',
            'duplicated hierarchy ownership remapping'])
        (output/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(report))
    finally:
        unregister()


if __name__=='__main__':
    main(Path(sys.argv[sys.argv.index('--')+1]))
