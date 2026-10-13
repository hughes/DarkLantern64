import json
import unittest

from tools.verify_bafford_district import NAMES,WAYPOINTS,PREFIX,COMPLETE,CAPTURE_COMPLETE,decode_log


class DistrictLogTests(unittest.TestCase):
    def content(self):
        return dict(textures=dict(textures=[{}]),mission=dict(report=dict(max_resident_regions=5,
            regions=[dict(id=i,chunk_bytes=1000,source_id=name) for i,name in enumerate(
                ('lower-east-alley','main-interior','next-passage','west-return','rear-courtyard'),1)],
            policy_regions=[dict(activation_bounds=[[-100,-4,-100],[100,14,100]],working_mask=1 << i) for i in range(5)],
            working_sets=[dict(regions=[i],model_count=2,triangle_count=100,collider_count=10,
                barrier_count=1,floor_count=2,chunk_bytes=1000) for i in range(1,6)])))

    def rows(self):
        rows=[];previous=list(WAYPOINTS['start'])
        for index,name in enumerate(NAMES):
            point=list(WAYPOINTS.get(name,previous));previous=point
            restart=name=='restart_loaded';collected=index>=NAMES.index('loot_collected') and not restart
            current=1 if restart else min(5,1+index//7)
            if name=='main_room_reloaded':current=2
            if name in ('next_west_approach','next_west_return'):current=3
            if name=='next_west_crossed':current=4
            if name in ('courtyard_east_approach','courtyard_after_east'):current=5
            if name in ('courtyard_east_crossed','courtyard_east_back'):current=1
            rows.append(dict(checkpoint=name,epoch=int(restart),tick=90+400*index,
                elapsed=1.5 if restart else (90+400*index)/60,player=point,
                grounded=0 if name.endswith('_air') else 1,vertical_velocity=2 if name.endswith('_air') else 0,
                jump_inputs=2 if index>=8 else 1 if index>=4 else 0,collision_clear=1,heap_used=1000000,
                models=2,triangles=100,colliders=11,floors=2,textures=1,door_index=0,
                door_open=int(NAMES.index('door_open')<=index<NAMES.index('door_closed')),loot_index=1,loot_home=2,loot_value=25 if collected else 0,
                collected_objects=2 if collected else 0,loot_retired=int(index>=NAMES.index('west_deep')),loot_reloaded=int(index>=NAMES.index('main_room_reloaded')),
                stream=dict(current=current,resident=[current],slots=5,settled=1,reserved=1000,allocated=5040,
                    completed_loads=1 if restart else index+1,retired=0 if restart else max(0,index-7),
                    readbytes=1000 if restart else 1000*(index+1),stalls=0)))
        return rows

    def log(self,rows=None,capture=False,underruns=0):
        rows=rows or self.rows();lines=['DL64 menu_ready mode=menu levels=8','DL64 level_start id=bafford-district preset=default']
        for row in rows:
            if row['epoch']:lines.append('DL64 level_start id=bafford-district preset=default')
            lines.append('DL64 stream_view resident_mask='+str(1 << (row['stream']['current']-1))+' triangles=100 colliders=11')
            lines.append(PREFIX+json.dumps(row))
        lines.append(f'DL64 music cue=lanterns_below consumed_frames=580032 generated_blocks=460 queued=3 underruns={underruns}')
        if capture:lines += ['DL64 capture_begin id=1 width=320 height=240',CAPTURE_COMPLETE]
        lines.append(COMPLETE+' passed=1 input_only=1 coverage=1 collision_clear=1 stair_jumps=2 visited_regions=5 loot_persist=1 restart_reload=1 pre_restart_loads=33 audio_calls=460')
        return '\n'.join(lines)

    def test_complete_input_tour_and_separate_capture_audio_scope(self):
        self.assertTrue(decode_log(self.log(),self.content())['passed'])
        with self.assertRaisesRegex(ValueError,'music underruns'):
            decode_log(self.log(underruns=1),self.content())
        result=decode_log(self.log(capture=True,underruns=1),self.content(),capture=True)
        self.assertTrue(result['functional_passed']);self.assertFalse(result['audio_continuity_passed'])

    def test_missing_reordered_corrupt_or_unready_evidence_rejected(self):
        bad=[]
        rows=self.rows();rows.pop(4);bad.append(rows)
        for group,key,value in [('stream','settled',0),('stream','allocated',999999),
                                (None,'floors',0),(None,'models',99),(None,'collision_clear',0),
                                (None,'door_open',0),(None,'loot_value',0)]:
            rows=self.rows()
            returned=NAMES.index('main_room_reloaded')
            if group:rows[returned][group][key]=value
            else:rows[returned][key]=value
            bad.append(rows)
        rows=self.rows();rows[15]['player'][0]=50;bad.append(rows)
        for rows in bad:
            with self.subTest(rows=rows[4]['checkpoint']),self.assertRaises(ValueError):
                decode_log(self.log(rows),self.content())
        rows=self.rows();rows[NAMES.index('next_west_crossed')]['stream'].update(current=3,resident=[3])
        with self.assertRaisesRegex(ValueError,'direct district'):
            decode_log(self.log(rows),self.content())

    def test_budget_and_actual_home_unload_are_independent_of_rom_claims(self):
        content=self.content();content['mission']['report']['working_sets'].pop()
        with self.assertRaisesRegex(ValueError,'outside cooked'):
            decode_log(self.log(),content)
        rows=self.rows()
        for row in rows:row['loot_home']=6
        with self.assertRaisesRegex(ValueError,'actually unload and reload'):
            decode_log(self.log(rows),self.content())
        with self.assertRaisesRegex(ValueError,'Published stream view'):
            decode_log(self.log().replace('triangles=100','triangles=1'),self.content())
        content=self.content();content['mission']['report']['policy_regions'][0]['working_mask']=3
        with self.assertRaisesRegex(ValueError,'visible set is not fully ready'):
            decode_log(self.log(),content)


if __name__=='__main__':unittest.main()
