"""Verify linked guard resources through an owned editor in a copied project.

Changes only the private fixture, closes only its own editor, and never launches
Ares. The saved level must retain asset-pack links instead of resolved copies.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

from verify_character_editor import compare
from verify_project_editor import EditorClient, capture, hash_file, require, tree_hashes, write_json

ROOT = Path(__file__).resolve().parents[1]
PACK = 'assets/guard/pack.json'


def linked(document):
    require(PACK in document.get('asset_packs', []), 'Shared guard pack link is missing')
    require(not any(item['id'].startswith('mesh-guard') for item in document['assets']),
            'A shared guard asset was flattened into the level')
    require(not any(item['id'].startswith('mat-guard') for item in document['materials']),
            'A shared guard material was flattened into the level')


def verify(executable, root=ROOT):
    root, executable = Path(root).resolve(), Path(executable).resolve()
    require(executable.is_file(), 'Build an editor executable first')
    real_before = tree_hashes(root/'content')
    parent = root/'.dev/editor/shared-guard-verification'
    require(parent.resolve() == parent, 'Verification folder cannot be a junction or symlink')
    private = parent/uuid.uuid4().hex
    private.mkdir(parents=True)
    for folder in ('content', 'src', 'tools'):
        shutil.copytree(root/folder, private/folder, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    content, queue = private/'content', private/'.dev/editor/project'
    # Distinct scout material proves type selection actually consults the
    # prefab catalog, even though the production types currently share art.
    pack_path = content/PACK
    pack = json.loads(pack_path.read_bytes())
    scout_material = copy.deepcopy(next(item for item in pack['materials'] if item['id'] == 'mat-guard'))
    scout_material.update(id='mat-guard-scout-test', color=[.2, .7, 1, 1])
    pack['materials'].append(scout_material)
    next(item for item in pack['prefabs'] if item.get('enemy_type') == 'scout')['material'] = scout_material['id']
    write_json(pack_path, pack)
    write_json(queue/'project-state.json', {'last_level': 'first_room.json'})
    report = {'passed': False, 'private_root': private.as_posix(), 'editor': executable.as_posix(),
              'editor_sha256': hash_file(executable), 'checks': [], 'captures': [], 'differences': []}
    startup = None
    if os.name == 'nt':
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    process = None
    started = time.monotonic()

    def passed(label):
        report['checks'].append(label)
        print('PASS '+label, flush=True)

    def inspect(client):
        state = client.request('inspect')
        linked(state['document'])
        return state

    def entity(state, ident):
        return next(item for item in state['document']['entities'] if item['id'] == ident)

    def pose(client):
        client.request('focus_character', {'id': 'new-guard'})
        client.request('set_animation', {'id': 'new-guard', 'clip': 'walk', 'time': .25, 'playing': False})

    with (private/'shared-guard-editor.log').open('w', encoding='utf-8') as log:
        try:
            process = subprocess.Popen([str(executable), str(private)], cwd=private,
                                       stdout=log, stderr=log, startupinfo=startup)
            report['owned_editor_pid'] = process.pid
            client = EditorClient(queue, process)
            session = client.wait_session()
            require(Path(session['root']).resolve() == private, 'Editor used the real project root')
            state = inspect(client)
            require(state['animation']['instances'] and not state['animation']['diagnostics'],
                    'First Room did not resolve its shared animated guard')
            require(all(item['status'] == 'ready' for item in state['animation']['instances']),
                    'Linked character preview is unsupported')
            passed('Existing First Room guards resolve the linked character animation')

            client.request('create_level', {'name': 'shared_guard_test', 'title': 'Shared Guard Verification', 'in_bundle': False})
            state = inspect(client)
            require(not any(item['kind'] == 'guard' for item in state['document']['entities']), 'Starter already contains guards')
            first = client.request('add_enemy', {'id': 'new-guard', 'enemy_type': 'scout', 'position': [0, 0, 1]})['entity']
            require(first['material'] == scout_material['id'] and first['model'] == 'mesh-guard',
                    'Empty-level Add enemy ignored the selected type prefab')
            client.request('set_entity', {'id': 'new-guard', 'patch': {'speed': 1.25, 'enemy_type': 'watchman'}})
            state = inspect(client)
            require(entity(state, 'new-guard')['material'] == 'mat-guard' and entity(state, 'new-guard')['speed'] == 1.25,
                    'Changing type did not update its default visual while retaining tuning')
            client.request('add_waypoint', {'id': 'route-a', 'position': [0, 0, 1]})
            client.request('add_waypoint', {'id': 'route-b', 'position': [1, 0, 1]})
            client.request('set_entity', {'id': 'new-guard', 'patch': {'behavior': 'patrol', 'patrol': ['route-a', 'route-b']}})
            duplicate = client.request('duplicate_enemy', {'id': 'new-guard', 'new_id': 'guard-copy', 'position': [1.5, 0, 1]})['entity']
            require(duplicate['model'] == first['model'] and duplicate['material'] == 'mat-guard', 'Duplicate lost shared visual references')
            require(set(duplicate['patrol']).isdisjoint({'route-a', 'route-b'}) and duplicate['speed'] == 1.25,
                    'Duplicate did not preserve tuning and independent route points')
            client.request('save')
            state = inspect(client)
            saved_path = content/'shared_guard_test.json'
            linked(json.loads(saved_path.read_bytes()))
            require(state['report']['counts']['enemies'] == 2 and len(state['animation']['instances']) == 2,
                    'Two newly placed linked guards did not cook and animate')
            passed('Empty starter creates selected enemy type; type changes and duplicate routes preserve shared references')

            rejected = client.request('set_material', {'id': 'mat-guard', 'patch': {'color': [1, 0, 0, 1]}}, expect_ok=False)
            require(PACK in rejected['error'] and 'read-only' in rejected['error'], 'Shared material API lacks a source diagnostic')
            client.request('import_asset_pack', {'uri': PACK}, expect_ok=False)
            require(not inspect(client)['dirty'], 'Rejected local edits dirtied the shared resource level')
            pose(client)
            before = capture(client, private, 'before-shared-material-edit')
            report['captures'].append(before)
            saved_before = saved_path.read_bytes()
            next(item for item in pack['materials'] if item['id'] == 'mat-guard')['color'] = [.05, 1, .05, 1]
            write_json(pack_path, pack)
            client.request('save')
            state = inspect(client)
            require(saved_path.read_bytes() == saved_before, 'Refreshing shared definitions changed the saved level')
            material = next(item for item in state['report']['resolved_catalog']['materials'] if item['id'] == 'mat-guard')
            require(material['color'] == [.05, 1, .05, 1], 'Save + Cook retained the stale linked material')
            pose(client)
            after = capture(client, private, 'after-shared-material-edit')
            report['captures'].append(after)
            report['differences'].append(compare(before, after, 100))
            passed('Shared material edits visibly refresh through Save + Cook without flattening or changing the level')

            expected = copy.deepcopy(state['document'])
            client.request('open_level', {'file': 'first_room.json'})
            another = inspect(client)
            other_material = next(item for item in another['report']['resolved_catalog']['materials'] if item['id'] == 'mat-guard')
            require(other_material['color'] == material['color'], 'Another level did not inherit the same resource update')
            client.request('open_level', {'file': 'shared_guard_test.json'})
            reopened = inspect(client)
            require(reopened['document'] == expected and not reopened['dirty'], 'Reopening changed placements, routes, tuning or pack links')
            require(len(reopened['animation']['instances']) == 2 and not reopened['animation']['diagnostics'], 'Reopen lost linked animation previews')
            passed('A second level inherits the edit and reopening preserves guard placement, routes and clean linked source')
            imported = client.request('import_asset_pack', {'uri': 'assets/loot/pack.json'})
            prop_prefab = next(item for item in imported['prefabs'] if 'enemy_type' not in item)
            client.request('add_prop', {'prefab': prop_prefab['id'], 'id': 'copied-loot', 'position': [0, 1, 2]})
            client.request('save')
            copied = inspect(client)
            require(entity(copied, 'copied-loot')['model'] == prop_prefab['model'], 'Copy-imported prop placement regressed')
            client.request('open_level', {'file': 'first_room.json'})
            client.request('open_level', {'file': 'shared_guard_test.json'})
            reloaded = inspect(client)
            require(all('enemy_type' in item for item in reloaded['prefabs']), 'Copied prefab catalog unexpectedly persisted after reopen')
            require(any(item['id'] == 'copied-loot' for item in reloaded['document']['entities']), 'Copy-imported prop disappeared after reopen')
            passed('Explicit loot copy-import and placed props coexist with linked guard resources across save/reopen')
            client.request('quit')
            process.wait(timeout=15)
            require(process.returncode == 0, 'Owned editor did not exit cleanly')
            report['passed'] = True
        except BaseException as error:
            report.update(error=str(error), traceback=traceback.format_exc())
            raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            report['real_content_unchanged'] = tree_hashes(root/'content') == real_before
            report['elapsed_seconds'] = round(time.monotonic()-started, 2)
            if not report['real_content_unchanged']:
                report.update(passed=False, error='Real content changed concurrently during verification')
            write_json(private/'shared-guard-editor-verification.json', report)
            write_json(root/'build/shared-guard-editor-verification.json', report)
    require(report['passed'], report.get('error', 'Shared guard editor verification failed'))
    print(json.dumps({'passed': report['passed'], 'report': str(root/'build/shared-guard-editor-verification.json')}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--editor', type=Path, default=ROOT/'editor/bazel-bin/darklantern64_project_editor.exe')
    args = parser.parse_args()
    try:
        verify(args.editor, args.root)
    except (AssertionError, OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        print(f'Shared guard editor verification failed: {error}', file=sys.stderr)
        raise SystemExit(1)
