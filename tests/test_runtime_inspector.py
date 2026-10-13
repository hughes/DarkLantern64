"""Runtime trace integrity, provenance and independently attributed costs."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools import runtime_inspector as inspector

BUILD = 'a'*64
PACKAGE = 'b'*64


def debug_map():
    return dict(schema_version=1, build_id=BUILD, rom_sha256=hashlib.sha256(b'ROM').hexdigest(), levels=[dict(
        id='doorway-fixture', package_sha256=PACKAGE, content_identity_sha256='c'*64,
        regions=[dict(id=1, source_id='west', bounds=[[-5, -1, -5], [0.1, 3, 5]], chunk_bytes=100),
                 dict(id=2, source_id='east', bounds=[[0, -1, -5], [5, 3, 5]], chunk_bytes=100)],
        transitions=[dict(index=0, id='west-to-east', from_region=1, to_region=2,
                          door_entity_id=1, bounds=[[-0.2, -0.5, -1], [0.2, 3, 1]])],
        entities=[dict(id=1, source_id='shared-door', kind='door', home_region=1, object_index=0,
                       source_provenance=dict(object_id=44)),
                  dict(id=2, source_id='east-loot', kind='loot', home_region=2, object_index=1)]
    )])


class Capture:
    def __init__(self, session=1):
        self.session, self.seq = session, 0
        self.lines = [f'DL64 rt_begin version=1 session={session} build={BUILD} package={PACKAGE} ticks_per_second=1000 slots=2']

    def record(self, record, time, **fields):
        self.seq += 1
        text = ' '.join(f'{key}={value}' for key, value in fields.items())
        self.lines.append(f'DL64 rt_{record} session={self.session} seq={self.seq} time={time} {text}')
        return self.seq

    def event(self, kind, time, **fields):
        common = dict(kind=kind, job=0, region=0, slot=0, generation=0, value=0, total=0, mask=0, reason='test')
        common.update(fields)
        return self.record('event', time, **common)

    def snapshot(self, time, state=4, generation=1, second=None, **fields):
        common = dict(current=1, wanted=1, physical=1, published=1, candidate=0, gate=0, preparing=0,
                      cpu_ready=1, gpu_ready=1, revision=1, slots=2, completed_rsp=5, completed_rdp=7, player='-2,0,0',
                      doors=1, used=0, loot=0, heap_used=123456)
        common.update(fields)
        seq = self.record('snapshot', time, **common)
        self.record('slot', time, snapshot=seq, slot=0, generation=generation, region=1, state=state,
                    bytes=100, total=100, pins=2, rsp=6, rdp=7, job=(generation<<32)|1)
        slot = dict(snapshot=seq, slot=1, generation=0, region=0, state=0,
                    bytes=0, total=0, pins=0, rsp=0, rdp=0, job=0)
        slot.update(second or {})
        self.record('slot', time, **slot)

    def end(self, time=500, dropped=0):
        emitted = self.seq
        self.seq += dropped
        self.record('end', time, emitted=emitted, dropped=dropped, trace_ticks=3)

    def text(self):
        return '\n'.join(self.lines)+'\n'


def complete_capture():
    capture = Capture()
    job = (1<<32)|1
    candidate = (1<<63)|1
    capture.event('request', 0, job=job, region=1, generation=1, total=100)
    capture.event('read_progress', 10, job=job, region=1, generation=1, value=40, total=100)
    capture.event('read_complete', 30, job=job, region=1, generation=1, value=100, total=100)
    capture.event('resident', 31, job=job, region=1, generation=1)
    capture.event('prepare_begin', 32, job=candidate, mask=1)
    capture.record('cost', 35, job=candidate, stage=1, elapsed=10, cpu=8, audio=2, commit=0, startup=0)
    capture.event('cpu_ready', 80, job=candidate, mask=1)
    capture.record('cost', 90, job=candidate, stage=2, elapsed=5, cpu=4, audio=1, commit=0, startup=0)
    capture.event('gpu_ready', 110, job=candidate, mask=1)
    capture.record('cost', 120, job=candidate, stage=3, elapsed=2, cpu=2, audio=0, commit=2, startup=0)
    capture.event('publish', 120, job=candidate, mask=1)
    capture.snapshot(120)
    capture.end()
    return capture


class RuntimeInspectorTests(unittest.TestCase):
    def report(self, capture, mapping=None):
        text = capture.text() if isinstance(capture, Capture) else capture
        return inspector.make_report(text.encode(), mapping or debug_map(), Path('captured.log'))

    def test_complete_trace_has_provenance_correlated_jobs_and_graphics_ownership(self):
        capture = complete_capture()
        report = self.report(capture)
        self.assertTrue(report['complete'], report['diagnostics'])
        self.assertEqual(report['source_log_sha256'], hashlib.sha256(capture.text().encode()).hexdigest())
        session = report['sessions'][0]
        self.assertEqual(session['level']['regions'][0]['source_id'], 'west')
        self.assertEqual(session['trace_overhead_ms'], 3)
        slot = session['snapshots'][0]['slots'][0]
        self.assertTrue(slot['rsp_owned'])
        self.assertFalse(slot['rdp_owned'])
        self.assertEqual(slot['pins'], 2)
        self.assertEqual(session['level']['entities'][0]['source_provenance']['object_id'], 44)

    def test_elapsed_publication_span_is_distinct_from_cpu_distributed_audio_and_commit(self):
        report = self.report(complete_capture())
        region, candidate = report['sessions'][0]['jobs']
        self.assertEqual(region['request_to_publication_ms'], 120)
        self.assertEqual(candidate['request_to_publication_ms'], 88)
        self.assertEqual(candidate['costs']['assembly']['cpu_ms'], 8)
        self.assertEqual(candidate['costs']['gpu']['audio_ms'], 1)
        self.assertEqual(candidate['costs']['publication']['commit_ms'], 2)
        self.assertEqual(candidate['milestone_ms']['gpu_ready'], 78)

    def test_published_load_keeps_latency_when_later_cancel_retires_owned_chunk(self):
        capture = complete_capture()
        capture.lines.pop()
        capture.seq -= 1
        capture.event('cancel', 160, job=(1<<32)|1, region=1, generation=1, reason='resident_retirement')
        capture.event('retire', 161, job=(1<<32)|1, region=1, generation=1, reason='demand_removed')
        capture.event('release', 200, job=(1<<32)|1, region=1, generation=1, reason='ownership_complete')
        capture.end()
        report = self.report(capture)
        self.assertTrue(report['complete'])
        region = report['sessions'][0]['jobs'][0]
        self.assertEqual(region['outcome'], 'published')
        self.assertEqual(region['request_to_publication_ms'], 120)
        self.assertEqual(region['milestone_ms']['release'], 200)
        self.assertEqual(region['retirement_reason'], 'resident_retirement')

    def test_candidate_and_following_region_identity_rows_share_first_publication_time(self):
        capture = Capture()
        region, candidate = (1<<32)|1, (1<<63)|1
        capture.event('request', 0, job=region, region=1, generation=1)
        capture.event('prepare_begin', 1, job=candidate, mask=1)
        capture.event('publish', 10, job=candidate, mask=1)
        capture.event('publish', 12, job=region, region=1, generation=1, mask=1)
        capture.snapshot(13)
        capture.end(20)
        report = self.report(capture)
        self.assertTrue(report['complete'])
        row = report['sessions'][0]['jobs'][0]
        self.assertEqual(row['request_to_publication_ms'], 10)
        self.assertEqual(row['milestones']['publish'], 10)

    def test_aggregated_cost_retains_actual_cpu_maximum_and_sample_count(self):
        capture = Capture()
        capture.snapshot(0)
        capture.record('cost', 100, job=0, stage=0, elapsed=80, cpu=60, audio=20, commit=0,
                       startup=0, samples=10, max_cpu=7)
        capture.record('cost', 110, job=0, stage=0, elapsed=8, cpu=6, audio=2, commit=0, startup=0)
        capture.end(120)
        report = self.report(capture)
        row = report['sessions'][0]['stage_totals']['read']
        self.assertTrue(report['complete'])
        self.assertEqual(row['cpu_ms'], 66)
        self.assertEqual(row['samples'], 11)
        self.assertEqual(row['max_cpu_ms'], 7)
        self.assertIsNone(row['max_elapsed_ms'])
        with self.assertRaisesRegex(inspector.TraceError, 'aggregated CPU'):
            self.report(capture.text().replace('max_cpu=7', 'max_cpu=5'))

    def test_reversal_cancellation_keeps_previous_scene_and_new_generation(self):
        capture = Capture()
        old, new = (1<<32)|2, (2<<32)|2
        candidate = (1<<63)|1
        capture.event('request', 10, job=old, region=2, slot=1, generation=1, total=100)
        capture.event('prepare_begin', 15, job=candidate, mask=3)
        capture.record('motion', 17, gate=1, **{'from': 1, 'to': 2}, door=1, required=2, missing_physical=2,
                       missing_published=2, waiting=0, suppressed=0, jump=0, direction='away', readiness='read',
                       position='-1,0,0', delta='-1,0,0', cpu_ready=0, gpu_ready=0, candidate=3)
        capture.event('candidate_cancel', 18, job=candidate, mask=3, reason='demand_reversed')
        capture.event('cancel', 19, job=old, region=2, slot=1, generation=1, reason='demand_reversed')
        capture.event('request', 20, job=new, region=2, slot=1, generation=2, total=100)
        capture.snapshot(30, wanted=3, gate=1, second=dict(generation=2, region=2, state=1, total=100, job=new))
        capture.end()
        report = self.report(capture)
        self.assertTrue(report['complete'], report['diagnostics'])
        jobs = report['sessions'][0]['jobs']
        self.assertEqual([j['outcome'] for j in jobs], ['cancelled', 'cancelled', 'unfinished'])
        self.assertIsNone(jobs[0]['request_to_publication_ms'])
        self.assertEqual(jobs[0]['reason'], 'demand_reversed')
        self.assertEqual(report['sessions'][0]['snapshots'][0]['published'], 1)

    def test_overflow_and_dropped_tail_are_explicitly_incomplete(self):
        capture = Capture()
        capture.snapshot(20)
        capture.end(dropped=4)
        report = self.report(capture)
        self.assertFalse(report['complete'])
        self.assertEqual({d['code'] for d in report['diagnostics']}, {'missing_records', 'overflow'})

    def test_io_and_crc_failures_are_recorded_as_failed_loads(self):
        for reason in ('io', 'corrupt'):
            with self.subTest(reason=reason):
                capture = Capture()
                job = (1<<32)|2
                capture.event('request', 0, job=job, region=2, slot=1, generation=1, total=100)
                capture.event('failed', 20, job=job, region=2, slot=1, generation=1, reason=reason)
                capture.snapshot(21, second=dict(generation=1, region=2, state=6, total=100, job=job))
                capture.end()
                report = self.report(capture)
                self.assertTrue(report['complete'])
                row = report['sessions'][0]['jobs'][0]
                self.assertEqual(row['outcome'], 'failed')
                self.assertEqual(row['reason'], reason)
                self.assertIsNone(row['request_to_publication_ms'])

    def test_cancelled_candidate_cannot_be_reported_as_successfully_published(self):
        capture = Capture()
        job = (1<<63)|1
        capture.event('prepare_begin', 0, job=job, mask=3)
        capture.event('candidate_cancel', 20, job=job, mask=3, reason='approach_reversed')
        capture.event('publish', 21, job=job, mask=3, reason='obsolete')
        capture.snapshot(22)
        capture.end()
        report = self.report(capture)
        self.assertFalse(report['complete'])
        self.assertEqual(report['sessions'][0]['jobs'][0]['outcome'], 'cancelled')
        self.assertIn('stale_publication', {d['code'] for d in report['diagnostics']})

    def test_missing_middle_record_and_missing_snapshot_slot_rows(self):
        capture = complete_capture()
        text = capture.text().replace(capture.lines[2]+'\n', '')
        report = self.report(text)
        self.assertFalse(report['complete'])
        self.assertIn('missing_records', {d['code'] for d in report['diagnostics']})
        text = capture.text().replace(capture.lines[-2]+'\n', '')
        report = self.report(text)
        self.assertIn('missing_slots', {d['code'] for d in report['diagnostics']})
        self.assertFalse(report['sessions'][0]['snapshots'][0]['complete'])

    def test_missing_snapshot_retains_orphan_slots_without_inventing_state(self):
        capture = complete_capture()
        snapshot_line = next(line for line in capture.lines if line.startswith('DL64 rt_snapshot '))
        report = self.report(capture.text().replace(snapshot_line+'\n', ''))
        self.assertFalse(report['complete'])
        session = report['sessions'][0]
        self.assertFalse(session['snapshots'])
        self.assertEqual(len(session['orphan_slots']), 2)
        self.assertIn('orphan_slot', {d['code'] for d in report['diagnostics']})

    def test_truncated_tail_and_missing_end_are_inspectable_and_incomplete(self):
        capture = complete_capture()
        for text in ('\n'.join(capture.lines[:-1])+'\n', capture.text().rstrip('\n')):
            with self.subTest(text=text[-70:]):
                report = self.report(text)
                self.assertFalse(report['complete'])
                self.assertIn('missing_end', {d['code'] for d in report['diagnostics']})
        self.assertIn('partial_line', {d['code'] for d in self.report(capture.text().rstrip('\n'))['diagnostics']})

    def test_identity_map_and_rom_hash_must_match_before_import(self):
        capture = complete_capture()
        for key in ('build_id', 'package_sha256'):
            mapping = debug_map()
            if key == 'build_id':
                mapping[key] = 'd'*64
            else:
                mapping['levels'][0][key] = 'd'*64
            with self.subTest(key=key), self.assertRaisesRegex(inspector.TraceError, 'stale'):
                self.report(capture, mapping)
        inspector.make_report(capture.text().encode(), debug_map(), 'log', rom_bytes=b'ROM')
        with self.assertRaisesRegex(inspector.TraceError, 'ROM SHA-256'):
            inspector.make_report(capture.text().encode(), debug_map(), 'log', rom_bytes=b'stale ROM')

    def test_stale_generation_is_not_silently_attached_to_reused_slot(self):
        capture = Capture()
        capture.event('request', 10, job=(2<<32)|1, region=1, generation=2)
        capture.event('read_progress', 11, job=(1<<32)|1, region=1, generation=1, value=20, total=100)
        capture.snapshot(20, generation=2)
        capture.end()
        report = self.report(capture)
        self.assertFalse(report['complete'])
        self.assertIn('stale_handle', {d['code'] for d in report['diagnostics']})

    def test_actual_rom_serialization_motion_and_interaction_provenance(self):
        """Field spellings/order match stream_rom.c trace_sink's v1 writer."""
        mapping = debug_map()
        mapping['levels'][0]['colliders'] = [dict(region=1, record=0, entity_id=1, source_id='shared-door',
                                                   center=[0, 0, 0], half_size=[0.1, 1, 1], yaw=0)]
        text = (f'DL64 rt_begin version=1 session=1 build={BUILD} package={PACKAGE} ticks_per_second=1000 slots=2\n'
                'DL64 rt_event session=1 seq=1 time=1 kind=request reason=demand job=4294967297 region=1 slot=0 generation=1 value=0 total=100 mask=0\n'
                'DL64 rt_event session=1 seq=2 time=3 kind=resident reason=validated job=4294967297 region=1 slot=0 generation=1 value=100 total=100 mask=0\n'
                'DL64 rt_event session=1 seq=3 time=4 kind=publish reason=startup job=4294967297 region=1 slot=0 generation=1 value=100 total=100 mask=1\n'
                'DL64 rt_snapshot session=1 seq=4 time=5 current=1 wanted=1 physical=1 published=1 candidate=0 gate=1 preparing=0 cpu_ready=0 gpu_ready=0 revision=1 slots=2 completed_rsp=10 completed_rdp=20 player=-2.00000,0.00000,0.00000\n'
                'DL64 rt_slot session=1 seq=5 time=6 snapshot=4 job=4294967297 slot=0 generation=1 region=1 state=4 bytes=100 total=100 pins=0 rsp=12 rdp=19\n'
                'DL64 rt_slot session=1 seq=6 time=7 snapshot=4 job=0 slot=1 generation=0 region=0 state=0 bytes=0 total=0 pins=0 rsp=0 rdp=0\n'
                'DL64 rt_cost session=1 seq=7 time=8 job=0 stage=0 elapsed=7 cpu=6 audio=1 commit=2 startup=7\n'
                'DL64 rt_motion session=1 seq=8 time=10 gate=1 from=1 to=2 door=1 required=2 missing_physical=2 missing_published=2 waiting=1 suppressed=0 jump=0 direction=parallel readiness=read position=-0.25000,0.00000,0.00000 delta=0.00000,0.00000,0.05000 cpu_ready=0 gpu_ready=0 candidate=0\n'
                'DL64 rt_interaction session=1 seq=9 time=11 object=0 entity=1 eligible=1 in_range=1 raw_clear=0 effective_clear=1 blocker=0 kind=collider source_entity=1 source_region=1 source_record=1 source_gate=0 associated_object=0 query_cache_ready=1 reason=ready suppressed=0 probe=raw\n'
                'DL64 rt_interaction session=1 seq=10 time=12 object=1 entity=2 eligible=0 in_range=1 raw_clear=0 effective_clear=0 blocker=2 kind=gate_barrier source_entity=1 source_region=0 source_record=0 source_gate=1 associated_object=0 query_cache_ready=1 reason=occluded suppressed=1 probe=effective\n'
                'DL64 rt_end session=1 seq=11 time=15 emitted=10 dropped=0 trace_ticks=3 buffer_bytes=10000\n')
        report = self.report(text, mapping)
        self.assertTrue(report['complete'], report['diagnostics'])
        session = report['sessions'][0]
        self.assertEqual(session['motions'][0]['direction'], 'parallel')
        self.assertFalse(session['motions'][0]['suppressed'])
        first, second = session['interactions']
        self.assertEqual(first['authored_collider']['record'], 0)
        self.assertEqual(first['blocker_source_id'], 'shared-door')
        self.assertEqual(first['blocker_provenance'], {'object_id': 44})
        self.assertEqual(second['source_gate'], 1)
        self.assertTrue(second['suppressed'])
        self.assertEqual(session['stage_totals']['read']['startup_ms'], 7)
        self.assertEqual(session['stage_totals']['read']['audio_ms'], 1)
        with self.assertRaisesRegex(inspector.TraceError, 'collider source record'):
            self.report(text.replace('source_record=1', 'source_record=2'), mapping)

    def test_html_keeps_64_bit_candidate_ids_exact(self):
        report = self.report(complete_capture())
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'inspect.html'
            inspector.write_html(output, report)
            text = output.read_text(encoding='utf-8')
        self.assertIn('"job":"9223372036854775809"', text)

    def test_malformed_repeated_sequences_wrong_sessions_and_progress_rejected(self):
        text = complete_capture().text()
        variants = [text.replace('seq=2 ', 'seq=1 '), text.replace('seq=2 time=10', 'seq=2 time=0').replace('seq=3 time=30', 'seq=3 time=0').replace('seq=4 time=31', 'seq=4 time=0').replace('seq=5 time=32', 'seq=5 time=0').replace('seq=6 time=35', 'seq=6 time=34').replace('seq=7 time=80', 'seq=7 time=33'),
                    text.replace('value=40 total=100', 'value=101 total=100'),
                    text.replace('slot=0 generation=1 region=1 state=4', 'slot=9 generation=1 region=1 state=4'),
                    text.replace('snapshot=12', 'snapshot=999'),
                    text.replace('player=-2,0,0', 'player=nan,0,0'),
                    text.replace('cpu=8 audio=2', 'cpu=9 audio=2')]
        for variant in variants:
            with self.subTest(tail=variant[-300:]), self.assertRaises(inspector.TraceError):
                self.report(variant)

    def test_restart_sessions_are_independent_and_repeated_ids_rejected(self):
        first, second = complete_capture(), Capture(session=2)
        second.snapshot(0)
        second.end(10)
        report = self.report(first.text()+second.text())
        self.assertTrue(report['complete'])
        self.assertEqual(len(report['sessions']), 2)
        with self.assertRaisesRegex(inspector.TraceError, 'repeated session'):
            self.report(first.text()+first.text())

    def test_missing_capture_or_unknown_protocol_is_not_a_successful_empty_report(self):
        for text in ('ordinary emulator output\n', complete_capture().text().replace('rt_event', 'rt_mystery', 1)):
            with self.assertRaises(inspector.TraceError):
                self.report(text)

    def test_offline_html_escapes_imported_authored_names_and_contains_controls(self):
        mapping = debug_map()
        mapping['levels'][0]['regions'][0]['source_id'] = '</script><script>alert(1)</script>'
        report = self.report(complete_capture(), mapping)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'inspect.html'
            inspector.write_html(output, report)
            text = output.read_text(encoding='utf-8')
        self.assertNotIn('</script><script>alert(1)</script>', text)
        self.assertIn('\\u003c/script\\u003e', text)
        for control in ('id="scrub"', 'id="filter"', 'id="map"', 'id="jobs"', 'id="findings"'):
            self.assertIn(control, text)
        self.assertNotIn('<script src=', text)

    def test_cli_preserves_inputs_and_require_complete_still_writes_findings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log, mapping, output = root/'play.log', root/'map.json', root/'inspect.html'
            capture = complete_capture()
            log.write_text('\n'.join(capture.lines[:-1])+'\n', encoding='utf-8')
            mapping.write_text(json.dumps(debug_map()), encoding='utf-8')
            result = inspector.main([str(log), '--map', str(mapping), '--output', str(output), '--require-complete'])
            self.assertEqual(result, 1)
            self.assertTrue(output.exists())
            self.assertFalse(json.loads(output.with_suffix('.json').read_text())['complete'])
            before = log.read_bytes()
            self.assertEqual(inspector.main([str(log), '--map', str(mapping), '--output', str(log)]), 1)
            self.assertEqual(log.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
