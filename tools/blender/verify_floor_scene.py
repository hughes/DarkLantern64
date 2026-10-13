"""Exercise floor checkout/export in real background Blender, using scratch data."""
import copy
import json
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools/blender'))
from scene_checkout import checkout
from darklantern64_export.scene_bridge import collect, metadata


def main(output):
    output.mkdir(parents=True, exist_ok=True)
    row = dict(id='ramp', kind='static',
               transform=dict(position=[12,2,30], rotation=[0,90,0], scale=[2,1,1]),
               floor=dict(vertices=[[0,0,0],[1,.25,0],[0,0,2]]))
    expected = {key: copy.deepcopy(row[key]) for key in ('transform','floor')}
    checkout(dict(version=1, project=str(ROOT), scene_id='floor-verification',
                  level='scratch.json', entities=[row], meshes={}, materials={}, context=[],
                  exchange=str(output/'scene.json'), destination=str(output/'floor.blend')))
    data = metadata(bpy.context.scene)
    rows, meshes = collect(bpy.context, data)
    assert rows == {'ramp': expected} and meshes == {}, 'Unchanged floor round trip lost exact canonical values'
    parent = bpy.data.objects['ramp']
    proxy = next(obj for obj in bpy.context.scene.objects if obj.get('dl64_floor_proxy'))
    parent.location.x += 3
    bpy.context.view_layer.update()
    moved = collect(bpy.context, data)[0]['ramp']
    assert moved['floor'] == expected['floor'], 'Moving the parent changed local floor vertices'
    assert abs(moved['transform']['position'][0]-15) < 1e-5
    proxy.data.vertices[1].co.z = .5
    proxy.data.update()
    edited = collect(bpy.context, data)[0]['ramp']
    assert edited['floor']['vertices'][1] == [1,.5,0], edited
    proxy.parent = None
    try:
        collect(bpy.context, data)
    except ValueError:
        pass
    else:
        raise AssertionError('Lost floor parent was silently accepted')
    (output/'report.json').write_text(json.dumps(dict(passed=True,
        checks=['exact rotated/scaled round trip','parent placement','floor vertex edit','lost parent rejection']),indent=2)+'\n')
    print('Floor scene checkout/export verification passed')


if __name__ == '__main__':
    main(Path(sys.argv[sys.argv.index('--')+1]).resolve())
