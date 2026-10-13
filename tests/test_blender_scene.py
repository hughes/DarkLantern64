"""Regression tests for preserving authored gameplay through scene re-import."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.blender_scene import export_source, merge_entities, owned


class SceneMergeTests(unittest.TestCase):
    def setUp(self):
        self.wall = dict(id="wall", kind="static", model="mesh-wall", material="stone",
                         transform=dict(position=[0,0,0], rotation=[0,0,0], scale=[1,1,1]),
                         collider=dict(shape="box", center=[0,0,0], half_size=[1,1,1]))
        self.guard = dict(id="guard", kind="guard", patrol=["a","b"], speed=1.2)
        self.level = dict(assets=[], materials=[], entities=[self.wall, self.guard], music="keep-me")
        self.base = {"wall": owned(self.wall)}
        self.incoming = copy.deepcopy(self.base)

    def merge(self):
        return merge_entities(self.level, self.base, self.incoming)

    def test_gameplay_and_unrelated_fields_survive_updates(self):
        self.incoming['wall']['transform']['position'][0] = 2
        self.wall.update(kind='door', interaction_radius=2.3, targets=['some-link'])
        before = copy.deepcopy(self.level)
        result, _, report = self.merge()
        self.assertEqual(self.level, before)
        self.assertEqual(result['entities'][1], self.guard)
        self.assertEqual(result['entities'][0]['kind'], 'door')
        self.assertEqual(result['entities'][0]['targets'], ['some-link'])
        self.assertEqual(result['music'], 'keep-me')
        self.assertEqual(report['updated'], ['wall'])

    def test_conflict_does_not_partially_apply(self):
        self.wall['transform']['position'][0] = 3
        self.incoming['wall']['transform']['position'][0] = 2
        self.incoming['new-wall'] = copy.deepcopy(self.incoming['wall'])
        original = copy.deepcopy(self.level)
        with self.assertRaisesRegex(ValueError, 'wall.transform'):
            self.merge()
        self.assertEqual(self.level, original)

    def test_local_override_survives_unchanged_source(self):
        self.wall['transform']['position'][0] = 3
        result, _, _ = self.merge()
        self.assertEqual(result, self.level)

    def test_same_change_in_both_places_is_accepted(self):
        self.wall['transform']['position'][0] = 2
        self.incoming['wall']['transform']['position'][0] = 2
        result, base, _ = self.merge()
        self.assertEqual(result, self.level)
        self.assertEqual(base, self.incoming)

    def test_removal_retains_object_and_tombstone(self):
        self.incoming.clear()
        result, base, report = self.merge()
        self.assertEqual(result, self.level)
        self.assertEqual(base, self.base)
        self.assertEqual(report['retained_missing'], ['wall'])

    def test_editor_deletion_is_not_resurrected(self):
        self.level['entities'].pop(0)
        result, _, report = self.merge()
        self.assertEqual(result, self.level)
        self.assertEqual(report['editor_deletions_preserved'], ['wall'])
        self.incoming['wall']['transform']['position'][0] = 2
        with self.assertRaisesRegex(ValueError, 'deleted in editor'):
            self.merge()

    def test_unowned_id_collision_rejected_even_if_values_match(self):
        self.base.clear()
        with self.assertRaisesRegex(ValueError, 'ID already belongs'):
            self.merge()

    def test_gameplay_removal_of_collider_is_preserved(self):
        self.wall.pop('collider')
        self.wall['kind'] = 'loot'
        self.incoming['wall']['transform']['position'][0] = 1
        result, _, _ = self.merge()
        self.assertNotIn('collider', result['entities'][0])

    def test_repeat_import_is_idempotent(self):
        self.incoming['wall']['transform']['position'][0] = 1
        self.level, self.base, _ = self.merge()
        result, base, report = self.merge()
        self.assertEqual(result, self.level)
        self.assertEqual(base, self.base)
        self.assertEqual(report['updated'], [])

    def test_floor_edits_merge_and_conflicting_editor_edits_are_preserved(self):
        self.wall.pop('collider')
        self.wall['floor'] = dict(vertices=[[0,0,0],[1,.25,0],[0,0,1]])
        self.base = {'wall': owned(self.wall)}
        self.incoming = copy.deepcopy(self.base)
        self.incoming['wall']['floor']['vertices'][1][1] = .5
        result, _, _ = self.merge()
        self.assertEqual(result['entities'][0]['floor']['vertices'][1][1], .5)
        self.wall['floor']['vertices'][1][1] = .75
        with self.assertRaisesRegex(ValueError, 'wall.floor'):
            self.merge()


class BackgroundExportTests(unittest.TestCase):
    def test_export_uses_addon_and_persists_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'content').mkdir()
            source = root / 'art' / 'source.blend'
            source.parent.mkdir()
            source.write_bytes(b'blend source remains owned by Blender')
            exchange = root / 'build' / 'blender' / 'scene' / 'scene.json'
            exchange.parent.mkdir(parents=True)
            expected = dict(blend=str(source), exchange=str(exchange), scene_id='scene-test', revision=2,
                            entities=3, meshes=2, collision_review=[])

            def run(command, **kwargs):
                self.assertLess(command.index('--disable-autoexec'), command.index(str(source)))
                self.assertEqual(Path(command[command.index('--python') + 1]).name, 'scene_export.py')
                self.assertEqual(command[-2], str(root))
                Path(command[-1]).write_text(json.dumps(expected))
                return subprocess.CompletedProcess(command, 0, 'Blender export completed', '')

            with patch('tools.blender_scene.find_blender', return_value=Path('blender.exe')), \
                    patch('tools.blender_scene.subprocess.run', side_effect=run):
                self.assertEqual(export_source(source, root), expected)
            self.assertEqual((exchange.parent / 'export.log').read_text(), 'Blender export completed')

    def test_failed_export_does_not_claim_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'content').mkdir()
            source = root / 'source.blend'
            source.write_bytes(b'source')
            with patch('tools.blender_scene.find_blender', return_value=Path('blender.exe')), \
                    patch('tools.blender_scene.subprocess.run', return_value=subprocess.CompletedProcess([], 1, '', 'wrong project')):
                with self.assertRaisesRegex(ValueError, 'Blender export failed: wrong project'):
                    export_source(source, root)

    def test_outside_project_source_rejected_before_running_blender(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / 'content').mkdir()
            with patch('tools.blender_scene.subprocess.run') as run:
                with self.assertRaisesRegex(ValueError, 'Path escapes'):
                    export_source(root.parent / 'outside.blend', root)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
