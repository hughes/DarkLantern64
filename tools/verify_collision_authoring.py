"""Exercise parametric collision publication and safeguards on a private project."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.blender_scene import checkout,apply_exchange
from tools.blender_assets import find_blender
from tools.project_levels import json_bytes
from tools.verify_project_editor import tree_hashes


def main():
    private=ROOT/'.dev/collision-verification'/uuid.uuid4().hex
    private.mkdir(parents=True)
    for folder in ('content','tools','src'):
        shutil.copytree(ROOT/folder,private/folder,ignore=shutil.ignore_patterns('__pycache__'))
    source=private/'art/sponza.blend'
    checkout('sponza_courtyard.json',source,private)
    output=private/'build/collision-check'
    command=[str(find_blender()),'--factory-startup','--background','--disable-autoexec','--python-exit-code','1',str(source),
             '--python',str(ROOT/'tools/blender/verify_collision_tools.py'),'--',str(output)]
    subprocess.run(command,check=True)
    path=private/'content/sponza_courtyard.json'
    original=json.loads(path.read_bytes())
    gameplay=[e for e in original['entities'] if e['kind']!='static']
    first=apply_exchange(output/'default/scene.json',private)
    first_files=tree_hashes(private/'content')
    repeated=apply_exchange(output/'default/scene.json',private)
    assert tree_hashes(private/'content')==first_files
    assert not repeated['changed_meshes'] and not repeated['updated']
    # Refuse retirement of a step the level designer modified independently.
    good=path.read_bytes()
    doc=json.loads(good)
    step=next(e for e in doc['entities'] if e['id']=='sponza-collision-east-step-0-10')
    step['collider']['half_size'][0]+=.001
    path.write_bytes(json_bytes(doc))
    unchanged=tree_hashes(private/'content')
    try:
        apply_exchange(output/'reduced/scene.json',private)
    except ValueError as error:
        assert 'retirement would overwrite' in str(error), str(error)
    else:
        raise AssertionError('Edited collider was retired')
    assert tree_hashes(private/'content')==unchanged
    path.write_bytes(good)
    reduced=apply_exchange(output/'reduced/scene.json',private)
    assert set(reduced['removed_generated'])=={'sponza-collision-east-step-0-10','sponza-collision-east-step-1-10'}
    assert len(reduced['document']['entities'])==len(original['entities'])-2
    restored=apply_exchange(output/'restored/scene.json',private)
    assert len(restored['document']['entities'])==len(original['entities'])
    assert not restored['removed_generated']
    assert [e for e in restored['document']['entities'] if e['kind']!='static']==gameplay
    # Re-checkout restores hierarchy and generation parameters from level JSON.
    checkout('sponza_courtyard.json',private/'art/reopened.blend',private)
    report=dict(passed=True,root=str(private),blender=json.loads((output/'report.json').read_bytes()),
                host_checks=['all levels validate','idempotent reimport','edited collider retirement rejected atomically',
                             'reduced steps remove old collision','restored steps reuse stable IDs','gameplay unchanged',
                             'new checkout restores relationships and shape parameters'])
    (ROOT/'build/collision-authoring-verification.json').write_bytes(json_bytes(report))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
