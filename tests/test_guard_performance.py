"""A fast submission loop, hidden guard, rare hitch, or absent VI evidence cannot pass."""
import sys
import hashlib
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import verify_guard_performance as verifier
from test_profile_report import V2, window


def recording(*, hitch=False, repeats=0, full=2, drawn=2, heads=0, tracked=1):
    text = V2 + 'DL64 animation_ready models=2\n'
    text += 'DL64 level_start id=animation_workshop preset=two-guards\n'
    for number in range(1, 34):
        samples = [{'gameplay': 800, 'audio_mix': 100, 'display_wait': 100} for _ in range(60)]
        if hitch and number == 20: samples[30]['gameplay'] = 901
        text += (f'DL64 enemy id=animated-patrol position={-1-number%2},0,1.2 '
                 f'patrol_index={number%2} state=PATROL\n')
        text += 'DL64 enemy id=animated-sentry position=1.45,0,1.2 patrol_index=0 state=PATROL\n'
        row = window(number, samples, rate=60000, audio_calls=30)
        elapsed = ','.join(f'{sum(s.values()):x}' for s in samples)
        work = ','.join(f'{sum(s.values())-s["display_wait"]:x}' for s in samples)
        extra = f'DL64 profile_samples window={number} first=0 count=60 elapsed={elapsed} work={work}\n'
        extra += (f'DL64 profile_video window={number} tracked={tracked} vis=60 presents={60-repeats} '
                  f'repeats={repeats} ticks=60000 max_gap_vis={2 if repeats else 1} max_interval_ticks=1000\n')
        extra += f'DL64 profile_workload window={number} frames=60 geometry_mode=0 '
        extra += ' '.join(f'{name}_{bound}={900 if name=="triangles" else full if name=="full" else drawn if name=="drawn" else heads if name=="heads" else 2}'
                          for name in ('animated','full','drawn','triangles','heads') for bound in ('min','max'))+'\n'
        text += row.replace(f'DL64 profile_end window={number}\n',extra+f'DL64 profile_end window={number}\n')
    return text.encode()


class GuardPerformanceTests(unittest.TestCase):
    def analyze(self, **kwargs):
        return verifier.analyze(recording(**kwargs), ROOT/'fixture.log')

    def test_all_exact_frames_with_new_vi_present_and_two_guards_pass_measurement(self):
        report = self.analyze()
        self.assertTrue(report['passed'], report['checks'])
        self.assertEqual(report['profile']['frames'], 1800)
        self.assertEqual(report['profile']['distributions']['work']['max_ms'], 15)
        self.assertEqual(report['profile']['workload']['heads_max'],0)
        self.assertTrue(self.analyze(heads=2)['passed'])
        self.assertFalse(self.analyze(heads=3)['passed'])

    def test_single_hitch_fails_even_if_p99_and_average_are_good(self):
        report = self.analyze(hitch=True)
        self.assertFalse(report['passed'])
        work = report['profile']['distributions']['work']
        self.assertEqual(work['p99_ms'], 15)
        self.assertEqual(work['over_budget_frames'], 1)
        self.assertFalse(next(c for c in report['checks'] if c['name']=='16.667ms_work_budget')['passed'])

    def test_fast_cpu_with_repeated_display_or_untracked_vi_cannot_pass(self):
        for kwargs in ({'repeats':30}, {'tracked':0}):
            with self.subTest(kwargs=kwargs):
                report=self.analyze(**kwargs)
                self.assertEqual(report['profile']['distributions']['work']['over_budget_frames'],0)
                self.assertFalse(report['passed'])
                self.assertFalse(next(c for c in report['checks'] if c['name']=='native_vi_presentation')['passed'])
        # A blocked/coalesced VI interrupt can hide repeated physical scans;
        # long observation gaps cannot be accepted merely because origins differ.
        raw=recording().replace(b'max_interval_ticks=1000',b'max_interval_ticks=2000')
        report=verifier.analyze(raw,ROOT/'fixture.log')
        self.assertFalse(next(c for c in report['checks'] if c['name']=='native_vi_presentation')['passed'])

    def test_hidden_guard_and_short_measurement_cannot_pass(self):
        self.assertFalse(self.analyze(drawn=1)['passed'])
        self.assertTrue(self.analyze(full=1)['passed']) # a partially clipped but visible guard meets the user's scope
        report=verifier.analyze(recording(),ROOT/'fixture.log',windows=5)
        self.assertFalse(report['passed'])
        self.assertEqual(report['profile']['frames'],300)

    def test_capture_output_invalidates_ordinary_measurement(self):
        report=verifier.analyze(recording()+b'DL64 capture_begin id=1\n',ROOT/'fixture.log')
        self.assertFalse(report['passed'])

    def test_crlf_warmup_motion_cannot_substitute_for_live_steady_patrol(self):
        prefix,steady=recording().decode().split('DL64 profile_end window=3\n',1)
        steady=re.sub(r'id=animated-patrol position=[^ ]+ patrol_index=[01]',
                      'id=animated-patrol position=-1,0,1.2 patrol_index=0',steady)
        raw=(prefix+'DL64 profile_end window=3\n'+steady).replace('\n','\r\n').encode()
        report=verifier.analyze(raw,ROOT/'fixture.log')
        self.assertFalse(next(c for c in report['checks'] if c['name']=='independent_live_actors')['passed'])

    def test_manifest_rejects_stale_content_and_capture_rom(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);level=root/'content/animation_workshop.json'
            level.parent.mkdir();level.write_bytes(b'{"fixture":1}')
            asset=level.parent/'guard.json';asset.write_bytes(b'guard')
            rom=root/'ordinary.z64';rom.write_bytes(b'ROM')
            digest=hashlib.sha256(level.read_bytes());digest.update(b'guard.json\0guard')
            manifest={'rom_sha256':verifier.sha(rom), 'source_sha256':digest.hexdigest(),
                **{name:False for name in ('autoplay','capture','debug_overlay','scale_bench','menu_test')},
                'model_culling':True, 'level':'content\\animation_workshop.json', 'start_preset':'two-guards',
                'level_catalog':{'levels':[{'id':'animation_workshop','content':{
                    'dependencies':[{'uri':'guard.json','sha256':verifier.sha(asset)}]}}]}}
            path=root/'build.json';path.write_text(json.dumps(manifest))
            with patch.multiple(verifier,ROOT=root,LEVEL=level):
                self.assertEqual(verifier.ordinary_manifest(path,rom)['start_preset'],'two-guards')
                for flag in ('lighting_bake_verify','disable_lighting_bake'):
                    manifest[flag]=True;path.write_text(json.dumps(manifest))
                    with self.assertRaisesRegex(ValueError,'ordinary ROM'): verifier.ordinary_manifest(path,rom)
                    manifest[flag]=False
                path.write_text(json.dumps(manifest))
                asset.write_bytes(b'changed guard')
                with self.assertRaisesRegex(ValueError,'dependency changed'): verifier.ordinary_manifest(path,rom)
                asset.write_bytes(b'guard');manifest['capture']=True;path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError,'ordinary ROM'): verifier.ordinary_manifest(path,rom)

    def test_visibility_review_must_match_renderer_content_and_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);image=root/'capture.png';image.write_bytes(b'known reviewed image')
            review={'method':'separate-framebuffer-visual-review','source_sha256':'scene','asset_sha256':'guard',
                'preset':'two-guards','renderer':'t3d','visible_guards':sorted(verifier.ACTORS),
                'images':[{'path':'capture.png','sha256':verifier.sha(image)}],'reviewer':'fixture reviewer'}
            path=root/'review.json';path.write_text(json.dumps(review))
            self.assertEqual(verifier.visibility_evidence(path,'scene','guard','t3d')['renderer'],'t3d')
            with self.assertRaisesRegex(ValueError,'renderer'): verifier.visibility_evidence(path,'scene','guard','cpu')
            with self.assertRaisesRegex(ValueError,'source content'): verifier.visibility_evidence(path,'old','guard','t3d')
            image.write_bytes(b'changed image')
            with self.assertRaisesRegex(ValueError,'image changed'): verifier.visibility_evidence(path,'scene','guard','t3d')


if __name__=='__main__': unittest.main()
