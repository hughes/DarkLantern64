"""Verify exported guard poses against Blender's evaluated armature modifier.

Run with Blender --background art/guard.blend --python this_file.
Writes evidence under build/guard-blender-verification, never saves the .blend.
"""
from pathlib import Path
import hashlib
import json
import math
import runpy

import bpy
from mathutils import Matrix, Quaternion, Vector

ROOT=Path(__file__).resolve().parents[2]


def main():
    helpers=runpy.run_path(str(ROOT/'tools/blender/guard_character.py'),run_name='guard_check')
    target=ROOT/'content/assets/guard/guard.character.json'
    source=json.loads(target.read_text(encoding='utf-8'))
    scene=bpy.context.scene
    mesh_obj=next(o for o in scene.objects if o.type=='MESH' and o.get('dl64_character_id')=='basic-guard')
    rig=mesh_obj.find_armature();convert=helpers['TO_GAME'];bones=source['bones']
    ids={b['id']:i for i,b in enumerate(bones)}
    def matrix(t,r): return Matrix.Translation(Vector(t))@Quaternion((r[3],r[0],r[1],r[2])).to_matrix().to_4x4()
    bind=[]
    for b in bones:
        local=matrix(b['translation'],b['rotation'])
        bind.append(local if b['parent']<0 else bind[b['parent']]@local)
    inverse=[m.inverted() for m in bind]
    originals={}
    for vertex in mesh_obj.data.vertices:
        group=next(g for g in vertex.groups if g.weight>.999)
        joint=ids[mesh_obj.vertex_groups[group.group].name]
        pos=convert@vertex.co
        originals[(joint,*(round(v,5) for v in pos))]=vertex.index
    original_indices=[originals[(joint,*(round(v,5) for v in pos))]
                      for joint,pos in zip(source['mesh']['joints'],source['mesh']['vertices'])]
    previous=rig.animation_data.action;frame_before=scene.frame_current
    results=[]
    try:
        for clip in source['clips']:
            action=next(a for a in bpy.data.actions if a.get('dl64_clip_id')==clip['id'])
            rig.animation_data.action=action
            frames=clip['frames'];max_key=max_middle=0;worst=None
            for step in range((len(frames)-1)*2+1):
                sample=step/2;lo=int(sample);hi=min(lo+1,len(frames)-1);weight=sample-lo
                globals=[]
                for i,b in enumerate(bones):
                    a,z=frames[lo],frames[hi]
                    translation=[x*(1-weight)+y*weight for x,y in zip(a['translations'][i],z['translations'][i])]
                    qa,qb=a['rotations'][i],z['rotations'][i]
                    sign=-1 if sum(x*y for x,y in zip(qa,qb))<0 else 1
                    rotation=[x*(1-weight)+sign*y*weight for x,y in zip(qa,qb)]
                    length=math.sqrt(sum(v*v for v in rotation));rotation=[v/length for v in rotation]
                    local=matrix(translation,rotation)
                    globals.append(local if b['parent']<0 else globals[b['parent']]@local)
                skins=[g@ib for g,ib in zip(globals,inverse)]
                frame=action['dl64_frame_start']+sample
                scene.frame_set(math.floor(frame),subframe=frame%1);bpy.context.view_layer.update()
                evaluated=mesh_obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
                maximum=0
                for position,joint,original in zip(source['mesh']['vertices'],source['mesh']['joints'],original_indices):
                    expected=convert@evaluated.vertices[original].co
                    actual=skins[joint]@Vector(position)
                    maximum=max(maximum,(actual-expected).length)
                if step%2:
                    if maximum>max_middle: max_middle=maximum;worst=sample/clip['fps']
                else: max_key=max(max_key,maximum)
            assert max_key<.00001,(clip['id'],max_key)
            # This generated fixture uses linear baked samples. Test the gaps
            # too, so a change back to Blender's Bezier default cannot silently
            # pass while the game's linear playback follows a different path.
            assert max_middle<.00001,(clip['id'],'half-frame bake',max_middle)
            results.append({'id':clip['id'],'tested_poses':(len(frames)-1)*2+1,
                            'source_key_max_error_m':max_key,'half_frame_bake_max_error_m':max_middle,
                            'worst_half_frame_time':worst})
    finally:
        rig.animation_data.action=previous;scene.frame_set(frame_before)
    output=ROOT/'build/guard-blender-verification';output.mkdir(parents=True,exist_ok=True)
    helpers['export'](scene,output)
    exported=output/'guard.character.json'
    equal=exported.read_bytes()==target.read_bytes()
    assert equal,'Saved-source export differed from the checked character source'
    report={'passed':True,'blender_version':bpy.app.version_string,'clips':results,
            'saved_source_roundtrip_identical':equal,'source_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'scope':'Source keys and half-frame poses against Blender evaluated mesh; compression error measured separately.'}
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__': main()
