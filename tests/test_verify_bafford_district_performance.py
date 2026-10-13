"""Strict maxima, clock conservation and complete streaming timing evidence."""
import unittest
from unittest import mock
import hashlib
import json
from pathlib import Path
import tempfile

from tools import verify_bafford_district_performance as performance
from tools.verify_bafford_district_performance import decode_performance, FRAME, CONFIG, TERMINAL, NAMES


class DistrictPerformanceTests(unittest.TestCase):
    def frames(self):
        # 60kHz test clock: exactly1000 ticks per simulated60Hz step.
        return [dict(seq=1,epoch=0,values=[1000,1000,200,100,500,1000,0,1,0,0,0,1,1000000,0,0]),
                dict(seq=2,epoch=0,values=[1000,1000,200,100,500,1000,1,2,0,0,2,3,1100000,0,0]),
                dict(seq=3,epoch=1,values=[1000,1000,200,100,500,1000,0,1,0,0,3,1,1000000,0,0])]

    def log(self,frames=None,dt='wall',checkpoint_order=None):
        import json
        frames=frames or self.frames();names=checkpoint_order or NAMES
        lines=[CONFIG+f'version=2 ticks_per_second=60000 sim_rate=60 dt={dt} route=continuous frame_budget_hz=30 service_budget_us=8000',
               'DL64 boot_ok memory=8388608 scene="test"',
               'DL64 level_start id=bafford-district heap=1000000/7000000']
        for index,row in enumerate(frames):
            lines.append(FRAME+f"seq={row['seq']} epoch={row['epoch']} samples="+','.join(format(v,'x') for v in row['values']))
            if index==0:
                lines += ['DL64 bafford_district '+json.dumps(dict(checkpoint=name,tick=1,epoch=0)) for name in names[:-1]]
            if index==len(frames)-1:
                lines.append('DL64 bafford_district '+json.dumps(dict(checkpoint=names[-1],tick=3,epoch=1)))
        lines.append(TERMINAL+f'frames={len(frames)} epochs=2 dropped_ticks=0')
        return '\n'.join(lines)+'\n'

    def decode(self,text=None,**kwargs):
        return decode_performance(text or self.log(),functional=dict(functional_passed=True,audio_continuity_passed=True),**kwargs)

    def test_exact_maxima_complete_route_and_heap_evidence(self):
        result=self.decode()
        self.assertTrue(result['accepted']);self.assertTrue(result['simulation_passed'])
        self.assertEqual(result['stages']['gpu_prepare']['frames'],1)
        self.assertEqual(result['peak_heap_bytes'],1100000)
        self.assertEqual(result['frames']['service_cpu_ticks']['max_ms'],100/60)

    def test_thirty_two_ms_deadline_never_hides_a_rare_spike(self):
        frames=self.frames();frames[1]['values'][0]=1921 #32.0167ms
        result=self.decode(self.log(frames),frame_budget_ms=32)
        self.assertFalse(result['accepted']);self.assertIn('frame_ticks exceeds its maximum budget',result['failures'])
        frames[1]['values'][0]=1920
        self.assertTrue(self.decode(self.log(frames),frame_budget_ms=32)['accepted'])
        for budget in ({'frame_budget_ms':40},{'service_budget_ms':9}):
            with self.assertRaisesRegex(ValueError,'only tighten'):self.decode(**budget)

    def test_service_cpu_limit_and_render_elapsed_are_independent(self):
        for field,value,message in [(3,481,'service_cpu_ticks'),(4,2001,'render_ticks'),(1,2001,'interval_ticks')]:
            frames=self.frames();frames[1]['values'][field]=value
            frames[1]['values'][0]=max(frames[1]['values'][0],value)
            if field==3:frames[1]['values'][2]=481
            if field==1:
                # Keep actual wall dt/ticks consistent while demonstrating a
                # slow interval; two simulation ticks plus one accumulator tick.
                frames[1]['values'][5]=2001;frames[1]['values'][7]=3;frames[1]['values'][8]=1
                text=self.log(frames).replace('"tick": 3','"tick": 4')
            else:text=self.log(frames)
            result=self.decode(text)
            self.assertFalse(result['accepted']);self.assertTrue(any(message+' exceeds' in v for v in result['failures']))

    def test_fixed_dt_is_not_no_dropped_wall_time_evidence(self):
        result=self.decode(self.log(dt='fixed'))
        self.assertFalse(result['simulation_passed']);self.assertFalse(result['accepted'])
        frames=self.frames();frames[1]['values'][1]=1500
        self.assertFalse(self.decode(self.log(frames))['simulation_passed'])

    def test_missing_frames_terminal_and_clock_cancellation_are_rejected(self):
        frames=self.frames();frames[1]['seq']=3
        with self.assertRaisesRegex(ValueError,'Missing or repeated performance frame'):self.decode(self.log(frames))
        frames=self.frames();frames[1]['values'][6]=0
        with self.assertRaisesRegex(ValueError,'simulation ticks'):self.decode(self.log(frames))
        frames=self.frames();frames[1]['values'][8]=5
        with self.assertRaisesRegex(ValueError,'accumulator'):self.decode(self.log(frames))
        with self.assertRaisesRegex(ValueError,'Incomplete performance'):self.decode(self.log().replace(TERMINAL,'ignored '))
        with self.assertRaisesRegex(ValueError,'terminal differs'):self.decode(self.log().replace('frames=3 epochs','frames=4 epochs'))

    def test_missing_checkpoint_capture_or_unknown_heap_cannot_pass(self):
        names=list(NAMES);names[5],names[6]=names[6],names[5]
        self.assertFalse(self.decode(self.log(checkpoint_order=names))['accepted'])
        self.assertFalse(self.decode(self.log()+'DL64 capture_begin id=1 width=320 height=240\n')['accepted'])
        self.assertFalse(self.decode(self.log().replace('/7000000','/1000000'))['accepted'])

    def test_loading_barrier_freezes_fail_even_with_smooth_frames(self):
        frames=self.frames();frames[0]['values'][13]=1;frames[1]['values'][13]=1
        result=self.decode(self.log(frames))
        self.assertTrue(result['timing_passed']);self.assertFalse(result['accepted'])
        self.assertEqual(result['loading_barriers']['blocked_ticks'],2)
        self.assertEqual(result['loading_barriers']['max_episode_wall_ms'],1000/30)
        self.assertIn('Loading barriers suppressed requested movement or jump',result['failures'])

    def test_waypoint_readiness_wait_cannot_hide_loading_with_smooth_frames(self):
        frames=self.frames();frames[1]['values'][14]=6
        result=self.decode(self.log(frames))
        self.assertTrue(result['timing_passed']);self.assertEqual(result['loading_barriers']['blocked_ticks'],0)
        self.assertFalse(result['accepted']);self.assertEqual(result['route_waits']['loading_frames'],1)
        self.assertIn('Route readiness waits hide loading latency',result['failures'])
        self.assertFalse(self.decode(self.log().replace('route=continuous','route=settled'))['accepted'])
        frames=self.frames();frames[1]['values'][14]=1
        with self.assertRaisesRegex(ValueError,'Admission wait occurs during active route'):self.decode(self.log(frames))

    def test_configuration_and_completion_bound_every_measured_frame(self):
        text=self.log();lines=text.splitlines();config=lines.pop(0);lines.insert(4,config)
        with self.assertRaisesRegex(ValueError,'precede configuration'):self.decode('\n'.join(lines))
        with self.assertRaisesRegex(ValueError,'follows completion'):
            self.decode(text+next(line for line in text.splitlines() if line.startswith(FRAME))+'\n')

    def test_unstepped_render_frames_do_not_split_a_continuous_block(self):
        frames=self.frames();frames[0]['values'][13]=1
        pause=dict(seq=2,epoch=0,values=[600,600,200,100,300,600,1,1,600,0,1,3,1000000,0,0])
        frames.insert(1,pause);frames[2]['seq']=3;frames[3]['seq']=4
        frames[2]['values'][1]=frames[2]['values'][5]=400;frames[2]['values'][13]=1
        result=self.decode(self.log(frames))
        self.assertEqual(result['loading_barriers']['blocked_ticks'],2)
        self.assertEqual(len(result['loading_barriers']['episodes']),1)
        self.assertEqual(result['loading_barriers']['episodes'][0]['frames'],3)
        self.assertEqual(result['loading_barriers']['max_episode_wall_ms'],1000/30)

    def test_audio_or_functional_failure_preserves_independent_frame_metrics(self):
        result=decode_performance(self.log(),functional=dict(functional_passed=True,audio_continuity_passed=False))
        self.assertFalse(result['accepted']);self.assertEqual(result['frame_count'],3)
        self.assertIn('Ordinary route music has underruns or missing playback evidence',result['failures'])

    def test_incomplete_prefix_reports_actual_metrics_but_cannot_pass(self):
        text=self.log().replace(TERMINAL,'ignored ')
        result=decode_performance(text,allow_incomplete=True,functional=dict(functional_passed=True,audio_continuity_passed=True))
        self.assertFalse(result['accepted']);self.assertFalse(result['complete']);self.assertIsNone(result['terminal'])
        self.assertEqual(result['frame_count'],3);self.assertEqual(result['observed_summary']['dropped_ticks'],0)

    def test_exercise_timeout_preserves_matching_snapshot_sidecar_and_failed_report(self):
        with tempfile.TemporaryDirectory(dir=performance.ROOT/'build',prefix='test-perf-failure-') as tmp:
            root=Path(tmp);rom=root/'diagnostic.z64';rom.write_bytes(b'independent ROM fixture')
            identity=hashlib.sha256(rom.read_bytes()).hexdigest()
            build=dict(rom=str(rom),rom_sha256=identity,signature='test',bafford_district_test=True,
                       bafford_district_performance_test=True,bafford_district_test_capture=False,
                       level_catalog=dict(levels=[dict(id='bafford-district',content=dict(mission=dict(sha256='test')))]))
            metadata=root/'build'/rom.stem/'build.json';metadata.parent.mkdir(parents=True);metadata.write_text(json.dumps(build))
            run=root/'.dev/ares/runs/independent-fixture';run.mkdir(parents=True)
            snapshot=run/rom.name;snapshot.write_bytes(rom.read_bytes())
            log=run/'bafford-district-performance.log';log.write_text(self.log().replace(TERMINAL,'ignored '))
            (run/'settings.bml').write_text('isolated fixture')
            error=RuntimeError('bafford-district-performance: timed out waiting for completion; inspect '+str(log))
            output=root/'result.json'
            with mock.patch.object(performance,'ROOT',root),mock.patch('tools.smoke_ares.exercise',side_effect=error):
                self.assertEqual(performance.main(['--rom',str(rom),'--timeout','1','--output',str(output)]),1)
            report=json.loads(output.read_text())
            self.assertFalse(report['passed']);self.assertFalse(report['accepted']);self.assertFalse(report['complete'])
            self.assertEqual(report['frame_count'],3);self.assertEqual(report['run']['rom_sha256'],identity)
            self.assertEqual(report['error'],str(error));self.assertEqual(json.loads((run/'verification-build.json').read_text()),build)
            snapshot.write_bytes(b'changed')
            with mock.patch.object(performance,'ROOT',root),self.assertRaisesRegex(ValueError,'matching ROM snapshot'):
                performance.failed_run(error,build,root/'settings.bml')

    def test_baseline_reports_initial_and_transition_cost_separately(self):
        text='DL64 scene_load total_ticks=3000 lighting_ticks=2900 alternate_lighting_ticks=0 gpu_ticks=60 ticks_per_second=1000\n'
        text+='DL64 level_load id=bafford-district total_ticks=3050\n'
        text+='DL64 scene_load total_ticks=15000 lighting_ticks=14900 alternate_lighting_ticks=0 gpu_ticks=60 ticks_per_second=1000\n'
        result=decode_performance(text)
        self.assertFalse(result['accepted']);self.assertFalse(result['simulation_passed'])
        self.assertEqual(result['renderer_preparations']['initial_start']['count'],1)
        self.assertEqual(result['renderer_preparations']['transition']['elapsed']['max_ms'],15000)


if __name__=='__main__':unittest.main()
