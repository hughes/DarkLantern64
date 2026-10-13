"""Exercise real Sponza Blender exports and safe reimport in a disposable project."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def worker(private):
    import bpy
    sys.path.insert(0, str(ROOT / 'tools/blender'))
    from darklantern64_export.scene_bridge import export_scene, metadata
    data = metadata(bpy.context.scene)
    data['project'] = str(private)
    data['revision'] = 0
    scene = bpy.context.scene
    def export(name):
        current = metadata(scene)
        current['exchange'] = str(private / 'build/exports' / name / 'scene.json')
        scene['dl64_scene_checkout'] = json.dumps(current)
        return export_scene(bpy.context)
    scene['dl64_scene_checkout'] = json.dumps(data)
    export('baseline')
    obj = next(o for o in scene.objects if o.get('dl64_model_id') == 'mesh-sponza-parapet-north')
    ident = obj['dl64_entity_id']
    obj.name = 'Artist renamed this parapet'
    obj.location.x += .05
    for vertex in obj.data.vertices:
        vertex.co.z += .12
    obj.data.update()
    # A supported box edit clear of patrol/spawn paths.
    proxy_parent = next(o for o in scene.objects if o.get('dl64_entity_id') == 'sponza-collision-east-step-0-0')
    proxy = next(o for o in proxy_parent.children if o.get('dl64_collision_proxy'))
    proxy.scale.x *= .999
    export('edited')
    obj.location.x += .05
    export('conflict')
    obj.location.x -= .05
    for vertex in obj.data.vertices:
        vertex.co.z += .04
    obj.data.update()
    export('mesh-conflict')
    # Duplication must never silently select one of two competing identities.
    duplicate = obj.copy()
    scene.collection.objects.link(duplicate)
    try:
        export('duplicate')
    except ValueError as error:
        assert 'duplicate ID' in str(error), str(error)
    else:
        raise AssertionError('Duplicate stable ID was accepted')
    bpy.data.objects.remove(duplicate, do_unlink=True)
    # Deleted source instances are staged as absent, never deleted in the level.
    # Keep child collision in world space when deliberately detaching it.
    for child in list(obj.children):
        world=child.matrix_world.copy()
        child.parent=None
        child.matrix_world=world
        child.pop('dl64_visual_owner',None)
    bpy.data.objects.remove(obj, do_unlink=True)
    export('removed')
    (private / 'worker-result.json').write_text(json.dumps(dict(entity=ident, duplicate_rejected=True)))


def main():
    from tools.blender_assets import find_blender
    from tools.blender_scene import apply_exchange, checkout, sha
    from tools.project_levels import json_bytes
    from tools.compile_level import compile_level
    from tools.verify_project_editor import EditorClient, tree_hashes
    private = ROOT / '.dev/blender-scene-verification' / uuid.uuid4().hex
    private.mkdir(parents=True)
    for folder in ('content', 'tools', 'src'):
        shutil.copytree(ROOT / folder, private / folder, ignore=shutil.ignore_patterns('__pycache__'))
    source = private / 'art/sponza-edit.blend'
    checkout('sponza_courtyard.json', source, private)
    command = [str(find_blender()), '--factory-startup', '--background', '--disable-autoexec', '--python-exit-code', '1',
               str(source), '--python', str(Path(__file__).resolve()), '--', '--worker', str(private)]
    subprocess.run(command, check=True, stdout=(private/'blender.log').open('w'), stderr=subprocess.STDOUT)
    path = private / 'content/sponza_courtyard.json'
    original = json.loads(path.read_bytes())
    before_files = tree_hashes(private/'content')
    exchange = lambda name: private / 'build/exports' / name / 'scene.json'
    baseline = apply_exchange(exchange('baseline'), private)
    assert not baseline['changed_meshes']
    # First checkout adds explicit collision relationships, without moving boxes.
    baseline_rows={e['id']:e for e in baseline['document']['entities']}
    for row in original['entities']:
        assert {k:v for k,v in baseline_rows[row['id']].items() if k!='collision_authoring'} == {k:v for k,v in row.items() if k!='collision_authoring'}
    after_baseline = tree_hashes(private/'content')
    assert all(v == after_baseline[k] for k,v in before_files.items() if k != 'sponza_courtyard.json')
    document = json.loads(path.read_bytes())
    guard = next(e for e in document['entities'] if e['kind'] == 'guard')
    guard['speed'] = .91
    light = next(e for e in document['entities'] if e['kind'] == 'light')
    light['intensity'] = .73
    path.write_bytes(json_bytes(document))
    expected_gameplay = [e for e in document['entities'] if e['kind'] != 'static']
    before_cook = compile_level(path, private/'build/before', private/'content')
    details = json.loads((private/'worker-result.json').read_bytes())
    ident = details['entity']
    # Exercise the editor's actual in-memory draft import and preview refresh.
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    report = dict(passed=False, root=str(private), checks=[])
    with (private/'editor.log').open('w') as log:
        process = subprocess.Popen([str(ROOT/'editor/bazel-bin/darklantern64_project_editor.exe'), str(private)],
                                   cwd=private, stdout=log, stderr=log, startupinfo=startup)
        client = EditorClient(private/'.dev/editor/project', process)
        try:
            client.wait_session()
            client.request('open_level', {'file':'sponza_courtyard.json'})
            client.request('set_entity', {'id':guard['id'], 'patch':{'speed':1.03}})
            expected_gameplay[expected_gameplay.index(guard)]['speed'] = 1.03
            # Draft auto-refresh may already be running after the edit.
            deadline = time.monotonic()+60
            while client.request('inspect')['busy']:
                assert time.monotonic()<deadline
                time.sleep(.1)
            imported = client.request('import_blender_scene', {'path':str(exchange('edited'))}, timeout=120)
            deadline = time.monotonic()+60
            while True:
                state = client.request('inspect')
                if not state['busy'] and not state['preview_refresh_pending']:
                    break
                assert time.monotonic()<deadline
                time.sleep(.1)
            assert [e for e in state['document']['entities'] if e['kind'] != 'static'] == expected_gameplay
            assert imported['changed_meshes'] == ['mesh-sponza-parapet-north'], imported
            assert ident in imported['updated'] and 'sponza-collision-east-step-0-0' in imported['updated']
            assert not state['dirty']
            client.request('capture')
            report['checks'].append('Editor imports real mesh, renamed instance placement, and box edits while preserving unsaved guard speed and all gameplay')
            saved = path.read_bytes()
            again = client.request('import_blender_scene', {'path':str(exchange('edited'))}, timeout=120)
            assert not again['changed_meshes'] and not again['updated']
            assert path.read_bytes() == saved
            report['checks'].append('Repeat import is byte-identical')
        finally:
            try:
                client.request('quit')
            except Exception:
                process.terminate()
            process.wait(timeout=30)
    after_cook = compile_level(path, private/'build/after', private/'content')
    before_preview = tree_hashes(private/'build/before/editor-assets/meshes')
    after_preview = tree_hashes(private/'build/after/editor-assets/meshes')
    assert before_preview != after_preview, 'Cooked preview did not change'
    assert all(json.loads(path.read_bytes()).get(k) == original.get(k) for k in original if k not in ('entities','blender_imports'))
    report['checks'].append('Cooked preview geometry changes; level settings, asset links, music, and test starts survive')
    data = json.loads(path.read_bytes())
    wall = next(e for e in data['entities'] if e['id'] == ident)
    wall['transform']['position'][0] += .2
    path.write_bytes(json_bytes(data))
    unchanged = tree_hashes(private/'content')
    try:
        apply_exchange(exchange('conflict'), private)
    except ValueError as error:
        assert ident + '.transform' in str(error), str(error)
    else:
        raise AssertionError('Editor/Blender transform conflict was accepted')
    assert tree_hashes(private/'content') == unchanged
    wall['transform']['position'][0] -= .2
    path.write_bytes(json_bytes(data))
    mesh_path = private/'content/assets/sponza/models/parapet-north.obj'
    mesh_path.write_bytes(mesh_path.read_bytes()+b'\n# Concurrent artist edit\n')
    unchanged = tree_hashes(private/'content')
    try:
        apply_exchange(exchange('mesh-conflict'), private)
    except ValueError as error:
        assert 'geometry changed in both' in str(error), str(error)
    else:
        raise AssertionError('Concurrent mesh conflict accepted')
    assert tree_hashes(private/'content') == unchanged
    report['checks'].append('Conflicting geometry and transforms reject atomically')
    unchanged = tree_hashes(private/'content')
    try:
        apply_exchange(exchange('baseline'), private)
    except ValueError as error:
        assert 'older than the last import' in str(error), str(error)
    else:
        raise AssertionError('Stale export was accepted')
    assert tree_hashes(private/'content') == unchanged
    # A structurally valid export that makes the level unplayable must fail
    # before publishing either its mesh or its placement changes.
    invalid = json.loads(exchange('edited').read_bytes())
    invalid['revision'] = 20
    spawn = next(e for e in original['entities'] if e['kind'] == 'spawn')['transform']['position']
    box = invalid['entities']['sponza-collision-east-step-0-0']
    box['transform']['position'] = spawn
    box['collider']['half_size'] = [4,4,4]
    invalid_path = exchange('edited').with_name('invalid.json')
    invalid_path.write_bytes(json_bytes(invalid))
    try:
        apply_exchange(invalid_path, private)
    except ValueError as error:
        assert 'intersects collider' in str(error), str(error)
    else:
        raise AssertionError('Invalid actor collision accepted')
    assert tree_hashes(private/'content') == unchanged
    report['checks'].append('Stale exports and invalid actor collision fail without changing content')
    removed = apply_exchange(exchange('removed'), private)
    assert ident in removed['retained_missing']
    assert any(e['id'] == ident for e in json.loads(path.read_bytes())['entities'])
    report['checks'].append('Blender removal retains the level object and its asset; duplicate Blender IDs reject')
    report.update(passed=True, imported=imported)
    destination = ROOT/'build/blender-scene-verification.json'
    destination.write_bytes(json_bytes(report))
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    if '--worker' in sys.argv:
        worker(Path(sys.argv[sys.argv.index('--worker')+1]))
    else:
        main()
