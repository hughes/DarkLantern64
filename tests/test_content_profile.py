"""Source studies can exceed preview budgets without entering the simulation."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tools.blender_scene import load_exchange
from tools.compile_level import ContentError, compile_level, header, play_package, validate
from tools.content_profile import STUDY_PROFILE
from tools.project_levels import ProjectLevels, json_bytes, sha256, starter_level

ROOT = Path(__file__).resolve().parents[1]


def large_study():
    document = starter_level('Imported source study')
    document.pop('asset_packs')
    document['authoring_profile'] = STUDY_PROFILE
    proxy = dict(kind='static', transform=dict(position=[30, 30, 30], rotation=[0, 0, 0], scale=[1, 1, 1]),
                 collider=dict(shape='box', center=[0, 0, 0], half_size=[.2, .2, .2]))
    document['entities'].extend(dict(id=f'source-proxy-{i}', **copy.deepcopy(proxy)) for i in range(260))
    return document


class ContentProfileTests(unittest.TestCase):
    def test_profile_requires_document_opt_in_and_authoring_caller(self):
        document = large_study()
        with self.assertRaisesRegex(ContentError, 'authoring-only source study'):
            validate(document)
        state = validate(document, authoring=True)
        self.assertTrue(state['authoring_only'])
        self.assertGreater(len(state['colliders']), 256)
        document.pop('authoring_profile')
        with self.assertRaisesRegex(ContentError, '256 (objects|entities)'):
            validate(document, authoring=True)

    def test_unknown_profile_and_invalid_geometry_are_rejected(self):
        document = large_study()
        document['authoring_profile'] = 'unlimited'
        with self.assertRaisesRegex(ContentError, 'unsupported profile'):
            validate(document, authoring=True)
        document['authoring_profile'] = STUDY_PROFILE
        document['entities'][0]['transform']['scale'][0] = 0
        with self.assertRaisesRegex(ContentError, 'below minimum'):
            validate(document, authoring=True)

    def test_study_preserves_light_and_enemy_populations_beyond_runtime_capacities(self):
        document = large_study()
        light = next(row for row in document['entities'] if row['kind'] == 'light')
        document['entities'].extend(dict(copy.deepcopy(light), id=f'source-light-{i}') for i in range(20))
        visual = next(row for row in document['entities'] if row['kind'] == 'static' and 'model' in row)
        for i in range(20):
            guard = dict(copy.deepcopy(visual), id=f'source-guard-{i}', kind='guard', behavior='sentry')
            guard.pop('collider', None)
            document['entities'].append(guard)
        state = validate(document, authoring=True)
        self.assertEqual(len(state['enemies']), 20)
        self.assertEqual(len(state['kinds']['light']), 22)

    def test_study_retains_unsupported_actor_placement_without_claiming_runtime_support(self):
        document = large_study()
        spawn = next(row for row in document['entities'] if row['kind'] == 'spawn')
        spawn['transform']['position'][1] = 100
        state = validate(document, authoring=True)
        for exporter in (header, play_package):
            with self.assertRaisesRegex(ContentError, 'bounded runtime level'):
                exporter(document, state)

    def test_study_compile_emits_preview_and_blocked_play_descriptor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output = root / 'study.json', root / 'output'
            document = large_study()
            source.write_bytes(json_bytes(document))
            with patch('tools.compile_level.prepare_lighting', side_effect=AssertionError('Study must not bake ROM lighting')):
                report = compile_level(source, output, ROOT / 'content', authoring=True)
            self.assertFalse(report['playable'])
            self.assertFalse((output / 'generated/demo_level.h').exists())
            play = json.loads((output / 'generated/play.json').read_bytes())
            self.assertEqual(play['format'], 'darklantern-authoring-study')
            self.assertFalse(play['playable'])
            preview = json.loads((output / 'editor-assets/levels/first_room.json').read_bytes())
            self.assertEqual(preview['metadata']['darklantern']['authoring_profile'], STUDY_PROFILE)
            with self.assertRaisesRegex(ContentError, 'authoring-only'):
                compile_level(source, root / 'runtime-output', ROOT / 'content')
            self.assertFalse((root / 'runtime-output').exists())

    def test_exchange_budget_comes_from_saved_document(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'scene.json'
            payload = dict(version=1, scene_id='scene-study', revision=1, meshes={},
                           baseline={}, entities={f'proxy-{i}': {} for i in range(300)},
                           authoring_profile=STUDY_PROFILE)
            path.write_bytes(json_bytes(payload))
            with self.assertRaisesRegex(ValueError, 'Invalid scene entities'):
                load_exchange(path)
            self.assertEqual(len(load_exchange(path, document=large_study())['entities']), 300)

    def test_project_catalog_and_cook_support_study_but_bundle_does_not(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            content = root / 'content'
            (content / 'models').mkdir(parents=True)
            shutil.copy2(ROOT / 'content/models/block.obj', content / 'models/block.obj')
            runtime = starter_level('Runtime room')
            runtime.pop('asset_packs')
            (content / 'runtime.json').write_bytes(json_bytes(runtime))
            study_path = content / 'study.json'
            study_path.write_bytes(json_bytes(large_study()))
            (content / 'level_bundle.json').write_bytes(json_bytes(dict(version=1, title='Test',
                levels=[dict(id='runtime', source='runtime.json')])))
            project = ProjectLevels(root)
            study = next(row for row in project.catalog()['levels'] if row['file'] == 'study.json')
            self.assertFalse(study['playable'])
            cooked = project.cook('study.json')
            self.assertTrue(cooked['report']['authoring_only'])
            self.assertTrue((Path(cooked['asset_root']) / cooked['scene_path']).is_file())
            with self.assertRaisesRegex(ValueError, 'authoring-only'):
                project.update('study.json', sha256(study_path.read_bytes()), in_bundle=True)


if __name__ == '__main__':
    unittest.main()
