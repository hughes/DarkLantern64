import copy
import json
from pathlib import Path
import unittest

from tools.compile_level import validate
from tools.scale_study import CASES, benchmark_base, make_fixture, parse_microbench

ROOT = Path(__file__).resolve().parents[1]


def micro_log(version=2):
    rows = ["DL64 scale_boot memory=8388608", f'DL64 scale_begin version={version} scene="test scene"']
    cases = ([(name,n) for name in ("guard_patrol","guard_chase") for n in (1,2,4,8,16)] +
             [(name,n) for name in ("surface_lights","visibility_lights") for n in (1,4,8,16)] +
             [("moving_light_vertices",n) for n in (128,512)] +
             [("light_collider_scan",n) for n in (0,16,64,256)])
    if version == 2:
        cases += ([("surface_lights_clear", n) for n in (1,4,8,16)] +
                  [("moving_light_vertices_clear", n) for n in (128,512)])
    for name,n in cases:
        rows.append(f"DL64 scale_case name={name} count={n} samples=2 units_per_sample=4 ticks=150 "
                    "max_ticks=75 audio_ticks=50 ticks_per_second=1000 colliders=26 lights=3 substeps=4 checksum=1.000000")
    return "\n".join(rows+[f"DL64 scale_complete cases={len(cases)} checksum=24.000000"])


class ScaleStudyTests(unittest.TestCase):
    def test_all_fixtures_fit_current_compiler_without_changing_base(self):
        base = json.loads((ROOT/'content/moonlit_courtyard.json').read_text())
        original = copy.deepcopy(base)
        baseline = benchmark_base(base)
        for name in CASES:
            with self.subTest(name=name):
                fixture = make_fixture(base,name)
                state = validate(fixture,ROOT/'content')
                self.assertEqual(len(state['kinds']['guard']),1)
                self.assertEqual(len(state['kinds']['objective']),1)
                self.assertEqual(fixture['views'],base['views'])
                if name.startswith('hidden'):
                    self.assertEqual(len(fixture['entities'])-len(baseline['entities']),6*int(name[6:]))
                if name.startswith('loot'):
                    self.assertEqual(len(fixture['entities'])-len(baseline['entities']),int(name[4:]))
                if name.startswith('lights'):
                    self.assertEqual(len(state['kinds']['light']),int(name[6:]))
        self.assertEqual(base,original)
        with self.assertRaises(ValueError): make_fixture(base,'unknown')

    def test_benchmark_omits_only_decorative_imported_props(self):
        base = json.loads((ROOT/'content/moonlit_courtyard.json').read_text())
        original = copy.deepcopy(base)
        # Use an existing valid mesh path beneath assets/ to mark pack origin;
        # this test exercises filtering only and does not cook the synthetic ID.
        base['assets'].append({'id':'fixture-imported', 'uri':'assets/fixture/model.obj'})
        common = {'model':'fixture-imported', 'material':'mat-stone',
                  'transform':{'position':[0,0,0], 'rotation':[0,0,0], 'scale':[1,1,1]}}
        base['entities'] += [dict(common, id='fixture-decoration', kind='static'),
                             dict(common, id='fixture-wall', kind='static', collider={'shape':'box'}),
                             dict(common, id='fixture-actor', kind='guard')]
        baseline = benchmark_base(base)
        ids = {entity['id'] for entity in baseline['entities']}
        self.assertNotIn('fixture-decoration', ids)
        self.assertIn('fixture-wall', ids)
        self.assertIn('fixture-actor', ids)
        self.assertIn('fixture-imported', {asset['id'] for asset in baseline['assets']})
        self.assertNotIn('test_starts', baseline)
        self.assertEqual(baseline.get('environment'), base.get('environment'))
        self.assertEqual(baseline.get('views'), base.get('views'))
        self.assertEqual([e for e in baseline['entities'] if e['kind']=='light'],
                         [e for e in base['entities'] if e['kind']=='light'])
        # The helper only returns a copy, including when recursively pruning.
        self.assertEqual(base['entities'][:-3], original['entities'])

    def test_micro_units_and_exclusive_totals(self):
        parsed = parse_microbench(micro_log())
        self.assertEqual(parsed['metadata']['scene'],'test scene')
        self.assertEqual(len(parsed['cases']),30)
        self.assertEqual(len(parse_microbench(micro_log(1))['cases']),24)
        for case in parsed['cases']:
            self.assertEqual(case['average_ms'],75)
            self.assertEqual(case['max_ms'],75)
            self.assertEqual(case['ms_per_unit'],18.75)

    def test_partial_duplicate_and_invalid_reports_fail(self):
        good = micro_log()
        bad = [good.replace('memory=8388608','memory=4194304'),
               good.rsplit('\n',1)[0],good+'\n'+good.splitlines()[2],
               good.replace('samples=2','samples=0',1),
               good.replace('ticks=150','ticks=151',1),
               good.replace('ticks=150','ticks=-1',1),
               good.replace('checksum=1.000000','checksum=nan',1),
               good.replace('cases=30','cases=29'),
               good.replace('version=2','version=99'),
               good.replace('name=guard_patrol','name=unknown',1)]
        for value in bad:
            with self.subTest(value=value[:100]):
                with self.assertRaises(ValueError): parse_microbench(value)


if __name__ == '__main__': unittest.main()
