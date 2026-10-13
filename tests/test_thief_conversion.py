"""Coordinate and authored gameplay checks independent of commercial assets."""
import unittest

from tools.import_thief import gameplay, level_document


class ThiefConversionTests(unittest.TestCase):
    def source_row(self, ident, position, heading=0):
        return dict(source_id=ident, position=position, facing_degrees=[0,0,heading],
                    model='waypt', archetype={'name':'Marker','id':-36})

    def test_source_heading_and_body_origin_to_feet(self):
        spawn = self.source_row(439, [1,1,4], 45)
        guard = self.source_row(10, [1,1,4], 90)
        data = dict(spawn=spawn,guards=[guard],loot=[],lights=[])
        floor = dict(points=[[0,0,0],[1,0,0],[1,0,-1],[0,0,-1]],
                     normal=[0,1,0],cell=0)
        rows,_ = gameplay(data,[floor],'test',study=True)
        self.assertEqual(rows[0]['transform']['rotation'][1],135)
        self.assertEqual(rows[1]['transform']['rotation'][1],180)
        self.assertEqual(rows[0]['transform']['position'],[.3048,0,-.3048])
        self.assertEqual(rows[1]['source']['position'],[1,1,4])

    def test_scripted_scepter_maps_to_objective_without_loot_value(self):
        row = self.source_row(74,[0,0,0])
        data = dict(spawn=self.source_row(439,[0,0,0]),guards=[],loot=[],lights=[],objectives=[row])
        entities,_ = gameplay(data,[],'test',study=True)
        goal = next(e for e in entities if e['kind']=='objective')
        self.assertEqual(goal['model'],'mesh-loot-scepter')
        self.assertEqual(goal['source']['source_id'],74)
        self.assertFalse(any(e['kind']=='loot' for e in entities))

    def test_study_and_entry_remain_explicitly_distinct(self):
        spawn = dict(id='spawn',kind='spawn',transform=dict(position=[0,0,0],rotation=[0,135,0],scale=[1,1,1]))
        study = level_document('Study',[],{},[],[spawn],study=True)
        entry = level_document('Entry',[],{},[],[spawn],study=False)
        self.assertEqual(study['authoring_profile'],'external-scale-study')
        self.assertNotIn('authoring_profile',entry)
        self.assertEqual(entry['views'][0]['position'],[0,1.5,0])


if __name__ == '__main__':
    unittest.main()
