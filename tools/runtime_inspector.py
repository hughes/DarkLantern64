#!/usr/bin/env python3
"""Import a versioned ROM runtime trace into an offline JSON/HTML inspector."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
STATES = ('free', 'requested', 'reading', 'validating', 'resident', 'retiring', 'failed')
EVENTS = {'request', 'read_progress', 'read_complete', 'resident', 'failed', 'cancel', 'retire',
          'release', 'prepare_begin', 'cpu_ready', 'gpu_ready', 'publish',
          'candidate_cancel', 'demand', 'motion', 'interaction'}
STAGES = {0: 'read', 1: 'assembly', 2: 'gpu', 3: 'publication'}
HEX = re.compile(r'^[0-9a-f]{64}$')
LIGHTING_MODES = {'inline_two_state', 'standalone_baked', 'live_night', 'actor_body_probes',
                  'objective_probe', 'legacy_scalar', 'legacy_actor_probes',
                  'legacy_objective_emissive', 'runtime_two_state', 'invalid'}
LIGHTING_REASONS = {'inline_payload', 'standalone_asset', 'independent_doors', 'guard',
                    'objective', 'animated_mesh', 'night_without_bake', 'legacy_environment',
                    'disabled_build', 'standalone_rejected', 'incomplete_inline_payload',
                    'cached_single_door_states', 'invalid_model'}


class TraceError(ValueError):
    """The imported trace or its identity map is invalid."""


def require(condition, message):
    if not condition:
        raise TraceError(message)


def integer(value, label):
    require(isinstance(value, str) and re.fullmatch(r'[0-9]+', value),
            f'{label}: expected a nonnegative decimal integer')
    return int(value)


def vector(value, label):
    try:
        result = [float(v) for v in value.split(',')]
    except (ValueError, AttributeError) as error:
        raise TraceError(f'{label}: expected finite comma-separated XYZ') from error
    require(len(result) == 3 and all(math.isfinite(v) for v in result),
            f'{label}: expected finite comma-separated XYZ')
    return result


def signed_integer(value, label):
    require(isinstance(value, str) and re.fullmatch(r'-?[0-9]+', value),
            f'{label}: expected a signed decimal integer')
    return int(value)


def fields(payload, line):
    result = {}
    for token in payload.split():
        require(token.count('=') == 1, f'line {line}: malformed field {token!r}')
        key, value = token.split('=', 1)
        require(key and value and key not in result, f'line {line}: duplicate or empty field {key!r}')
        result[key] = value
    return result


def numbers(record, required, line):
    require(set(required) <= record.keys(), f'line {line}: missing fields {sorted(set(required)-record.keys())}')
    for name in required:
        record[name] = integer(record[name], f'line {line} {name}')


def validate_map(mapping):
    require(isinstance(mapping, dict) and mapping.get('schema_version') == 1,
            'Debug map: unsupported schema_version')
    require(isinstance(mapping.get('build_id'), str) and HEX.fullmatch(mapping['build_id']),
            'Debug map: missing SHA-256 build_id; use the sidecar emitted by this ROM build')
    require(isinstance(mapping.get('levels'), list) and mapping['levels'], 'Debug map: missing levels')
    level_ids = set()
    for level in mapping['levels']:
        require(isinstance(level, dict) and isinstance(level.get('id'), str) and level['id'] not in level_ids,
                'Debug map: invalid or duplicate level ID')
        level_ids.add(level['id'])
        if not level.get('package_sha256'):
            continue
        require(HEX.fullmatch(level['package_sha256']) is not None, 'Debug map: invalid package SHA-256')
        regions = level.get('regions', [])
        require(isinstance(regions, list) and 1 <= len(regions) <= 32, 'Debug map: expected 1..32 regions')
        require([row.get('id') for row in regions] == list(range(1, len(regions)+1)),
                'Debug map: region IDs must be dense and ordered')
        for row in regions:
            require(isinstance(row.get('source_id'), str), 'Debug map: missing authored region ID')
            for field in ('bounds', 'activation_bounds'):
                if field not in row:
                    continue
                bounds = row[field]
                require(isinstance(bounds, list) and len(bounds) == 2 and
                        all(isinstance(v, list) and len(v) == 3 and
                            all(type(n) in (int, float) and math.isfinite(n) for n in v) for v in bounds) and
                        all(a < b for a, b in zip(*bounds)), 'Debug map: invalid region bounds')
        entities = level.get('entities', [])
        require(isinstance(entities, list) and len({row.get('id') for row in entities}) == len(entities),
                'Debug map: duplicate entity IDs')
        for entity in entities:
            require(type(entity.get('id')) is int and entity['id'] > 0 and
                    isinstance(entity.get('source_id'), str) and entity.get('home_region') in range(1, len(regions)+1),
                    'Debug map: invalid authored entity mapping')
        gates = level.get('transitions', [])
        require(isinstance(gates, list) and [g.get('index') for g in gates] == list(range(len(gates))),
                'Debug map: transition indices must be dense and ordered')
        for gate in gates:
            require(isinstance(gate.get('id'), str) and gate.get('from_region') in range(1, len(regions)+1) and
                    gate.get('to_region') in range(1, len(regions)+1), 'Debug map: invalid gate mapping')
    return mapping


def parse_lighting_records(text, mapping):
    """Read bounded renderer reports, including those emitted before rt_begin.

    A header binds its buckets and measured transport cost to actual source,
    package and build bytes. These reports have no frame clock: log-line order
    is retained instead of inventing a runtime trace timestamp.
    """
    reports, diagnostics, current = [], [], None
    seen = set()

    def finding(code, message, line):
        diagnostics.append(dict(code=code, message=message, line=line,
                                lighting_report=current['report'] if current else None))

    def finish():
        if current is None:
            return
        current['complete'] = (len(current['cases']) == current['case_count'] and
                               sum(row['models'] for row in current['cases']) == current['models'] and
                               current['cost'] is not None)
        if not current['complete']:
            finding('incomplete_lighting_report',
                    f"Renderer report {current['report']} lacks its declared buckets, model total or cost trailer",
                    current['line'])

    for line, raw in enumerate(text.splitlines(keepends=True), 1):
        match = re.match(r'^DL64 (lighting_policy|lighting_case|lighting_policy_cost)(?:\s|$)', raw.strip())
        if not match:
            continue
        if not raw.endswith(('\n', '\r')):
            finding('partial_lighting_line', 'Unfinished renderer diagnostic record was excluded', line)
            break
        kind, record = match.group(1), fields(raw.strip()[match.end():], line)
        numbers(record, {'version', 'report'}, line)
        require(record['version'] == 1, f'line {line}: unsupported lighting report version')
        record['line'] = line
        if kind == 'lighting_policy':
            finish()
            numbers(record, {'doors', 'revision', 'published', 'door_revision', 'models', 'cases'}, line)
            require(record.get('phase') in ('publication', 'lighting_change'),
                    f'line {line}: unknown lighting report phase')
            require(record.get('build') == mapping['build_id'],
                    f'line {line}: stale debug map: lighting build identity differs')
            require(isinstance(record.get('source'), str) and HEX.fullmatch(record['source']),
                    f'line {line}: lighting report has no exact source SHA-256')
            choices = [level for level in mapping['levels'] if
                       (level.get('renderer_source_sha256') or
                        (level.get('renderer_diagnostics') or {}).get('identity', {}).get('renderer_source_sha256') or
                        level.get('source_sha256')) == record['source'] and
                       (level.get('package_sha256') or 'none') == record.get('package')]
            require(len(choices) == 1, f'line {line}: stale/ambiguous debug map: lighting package/source identity differs')
            key = (record['build'], record['report'])
            require(key not in seen, f'line {line}: repeated lighting report ID')
            seen.add(key)
            require(record['published'] & ~((1 << len(choices[0].get('regions', [])))-1) == 0,
                    f'line {line}: lighting report references unmapped region')
            require(record['cases'] <= record['models'], f'line {line}: lighting bucket count exceeds model count')
            require('standalone' in record, f'line {line}: lighting report missing standalone bake result')
            current = dict(record, level_id=choices[0]['id'], case_count=record['cases'], cases=[],
                           cost=None, basis='runtime_observation', measured_runtime_allocation_bytes=None)
            reports.append(current)
        else:
            require(current is not None, f'line {line}: lighting bucket/cost before header')
            require(record['report'] == current['report'], f'line {line}: lighting report ID differs from header')
            require(current['cost'] is None, f'line {line}: lighting record after cost trailer')
            if kind == 'lighting_case':
                numbers(record, {'models', 'triangles'}, line)
                require(record.get('mode') in LIGHTING_MODES and record.get('reason') in LIGHTING_REASONS,
                        f'line {line}: unknown lighting mode/reason')
                require(record['models'] > 0, f'line {line}: empty lighting bucket')
                require(len(current['cases']) < current['case_count'], f'line {line}: excess lighting buckets')
                require(not any(row['mode'] == record['mode'] and row['reason'] == record['reason']
                                for row in current['cases']), f'line {line}: repeated lighting bucket')
                record['path'] = ('invalid' if record['mode'] == 'invalid' else
                                  'baked' if record['mode'] in ('inline_two_state', 'standalone_baked') else
                                  'fallback' if record['reason'] in ('independent_doors', 'disabled_build',
                                      'standalone_rejected', 'incomplete_inline_payload') else 'live')
                current['cases'].append(record)
            else:
                numbers(record, {'ticks', 'ticks_per_second'}, line)
                require(record['ticks_per_second'] > 0 and record.get('scope') == 'policy_rows_and_transport',
                        f'line {line}: invalid lighting diagnostic cost scope/rate')
                current['cost'] = dict(record, basis='measured_elapsed',
                    elapsed_ms=record['ticks']*1000/record['ticks_per_second'],
                    active_cpu_ms=None, note='Elapsed policy rows and transport; includes interrupts and excludes the cost row itself.')
    finish()
    return reports, diagnostics


def parse_trace(text, mapping):
    """Keep inspectable partial captures, but never label them complete.

    Malformed records and identity mismatches reject import. Missing records,
    an unfinished tail, overflow and stale slot handles are explicit findings.
    Ordinary ROM/emulator output is ignored.
    """
    validate_map(mapping)
    sessions, active, diagnostics = [], None, []
    session_ids = set()

    def finding(code, message, line=None):
        item = {'code': code, 'message': message}
        if line is not None:
            item['line'] = line
        if active is not None:
            item['session'] = active['session']
        diagnostics.append(item)

    def finish_snapshot():
        if active is None or not active['snapshots']:
            return
        snapshot = active['snapshots'][-1]
        if snapshot.get('_validated'):
            return
        snapshot['_validated'] = True
        snapshot['complete'] = len(snapshot['slots']) == active['slots']
        if not snapshot['complete']:
            finding('missing_slots', f"Snapshot {snapshot['seq']} has {len(snapshot['slots'])}/{active['slots']} slot records", snapshot['line'])
        else:
            physical = sum(1 << (s['region']-1) for s in snapshot['slots'] if s['state'] == 'resident')
            if physical != snapshot['physical']:
                finding('physical_mismatch', f"Snapshot {snapshot['seq']} resident slots disagree with physical mask", snapshot['line'])
                snapshot['complete'] = False

    for number, raw in enumerate(text.splitlines(keepends=True), 1):
        match = re.match(r'^DL64 (rt_[a-z_]+)(?:\s|$)', raw.strip())
        if not match:
            continue
        if not raw.endswith(('\n', '\r')):
            finding('partial_line', 'Unfinished final runtime record was excluded', number)
            break
        kind = match.group(1).removeprefix('rt_')
        record = fields(raw.strip()[match.end():], number)
        record['line'] = number
        if kind == 'begin':
            if active is not None and active['end'] is None:
                finish_snapshot()
                finding('missing_end', 'Session ended without rt_end before another session began', number)
            numbers(record, {'version', 'session', 'ticks_per_second', 'slots'}, number)
            require(record['version'] == 1 and record['ticks_per_second'] > 0 and 1 <= record['slots'] <= 5,
                    f'line {number}: invalid trace version/rate/slot capacity')
            require(record['session'] not in session_ids, f'line {number}: repeated session ID')
            require(record.get('build') == mapping['build_id'],
                    f'line {number}: stale debug map: trace build identity differs')
            choices = [level for level in mapping['levels'] if level.get('package_sha256') == record.get('package') and
                       ('level' not in record or level['id'] == record['level'])]
            require(len(choices) == 1, f'line {number}: stale/ambiguous debug map: mission package identity differs')
            session_ids.add(record['session'])
            active = dict(record, level=choices[0], events=[], motions=[], interactions=[], snapshots=[], orphan_slots=[], costs=[], end=None,
                          records=0, last_seq=0, last_time=0, generations={}, missing_ranges=[])
            sessions.append(active)
            continue
        require(active is not None, f'line {number}: runtime record before rt_begin')
        require(active['end'] is None, f'line {number}: runtime record after rt_end')
        numbers(record, {'session', 'seq', 'time'}, number)
        require(record['session'] == active['session'], f'line {number}: mismatched session ID')
        require(record['seq'] > active['last_seq'], f'line {number}: repeated/regressing sequence')
        if record['seq'] != active['last_seq']+1:
            finding('missing_records', f"Sequence skips {active['last_seq']+1}..{record['seq']-1}", number)
            active['missing_ranges'].append((active['last_seq']+1, record['seq']-1))
        require(record['time'] >= active['last_time'], f'line {number}: regressing extended timestamp')
        active['last_seq'], active['last_time'] = record['seq'], record['time']
        if kind != 'slot':
            finish_snapshot()
        regions = active['level']['regions']
        mask_limit = (1 << len(regions))-1
        if kind == 'event':
            numbers(record, {'job', 'region', 'slot', 'generation', 'value', 'total', 'mask'}, number)
            require(record.get('kind') in EVENTS and isinstance(record.get('reason'), str), f'line {number}: unknown event kind/reason')
            require(record['region'] <= len(regions) and record['mask'] & ~mask_limit == 0,
                    f'line {number}: event references unmapped region')
            if record['kind'] in ('request', 'read_progress', 'read_complete', 'resident', 'failed', 'cancel', 'retire', 'release'):
                require(record['region'] > 0, f'line {number}: region lifecycle event has no region')
            if record['region']:
                require(record['slot'] < active['slots'], f'line {number}: invalid slot')
                require(record['generation'] > 0 and record['job'] == (record['generation']<<32)|(record['slot']+1),
                        f'line {number}: region job does not match its slot generation')
                if record['generation'] < active['generations'].get(record['slot'], 0):
                    finding('stale_handle', f"Event for recycled slot {record['slot']} generation {record['generation']}", number)
                active['generations'][record['slot']] = max(record['generation'], active['generations'].get(record['slot'], 0))
            if record['kind'] == 'read_progress':
                require(record['value'] <= record['total'], f'line {number}: read progress exceeds total')
            active['events'].append(record)
        elif kind == 'snapshot':
            numbers(record, {'current', 'wanted', 'physical', 'published', 'candidate', 'gate', 'preparing',
                             'cpu_ready', 'gpu_ready', 'revision', 'slots', 'completed_rsp', 'completed_rdp'}, number)
            require(record['slots'] == active['slots'], f'line {number}: snapshot slot capacity differs from session')
            record['slot_count'] = record['slots']
            require(all(record[key] & ~mask_limit == 0 for key in ('wanted', 'physical', 'published', 'candidate')) and
                    record['current'] <= len(regions), f'line {number}: snapshot references unmapped region')
            require(record['gate'] <= len(active['level'].get('transitions', [])), f'line {number}: unmapped gate')
            require(all(record[key] in (0, 1) for key in ('preparing', 'cpu_ready', 'gpu_ready')), f'line {number}: invalid readiness flag')
            record['player'] = vector(record.get('player'), f'line {number} player')
            record['slots'] = []
            active['snapshots'].append(record)
        elif kind == 'slot':
            numbers(record, {'snapshot', 'slot', 'generation', 'region', 'state', 'bytes', 'total', 'pins', 'rsp', 'rdp', 'job'}, number)
            snapshot = active['snapshots'][-1] if active['snapshots'] else None
            if snapshot is None or snapshot.get('_validated') or record['snapshot'] != snapshot['seq']:
                lost_snapshot = any(start <= record['snapshot'] <= end for start, end in active['missing_ranges'])
                require(lost_snapshot, f'line {number}: slot without adjacent matching snapshot')
                finding('orphan_slot', f"Slot {record['slot']} references missing snapshot {record['snapshot']}; ownership context unavailable", number)
                active['orphan_slots'].append(record)
                active['records'] += 1
                continue
            require(record['slot'] < active['slots'] and record['slot'] not in {s['slot'] for s in snapshot['slots']},
                    f'line {number}: duplicate/invalid slot')
            require(record['state'] < len(STATES) and record['region'] <= len(regions) and record['bytes'] <= record['total'],
                    f'line {number}: invalid slot state/progress/region')
            require(record['state'] == 0 or record['region'] > 0, f'line {number}: active slot has no region')
            require(record['job'] == (0 if record['state'] == 0 else (record['generation']<<32)|(record['slot']+1)),
                    f'line {number}: slot job does not match its generation')
            if record['generation'] < active['generations'].get(record['slot'], 0):
                finding('stale_handle', f"Snapshot slot {record['slot']} generation regressed", number)
            active['generations'][record['slot']] = max(record['generation'], active['generations'].get(record['slot'], 0))
            record['state'] = STATES[record['state']]
            record['rsp_owned'] = record['rsp'] > snapshot['completed_rsp']
            record['rdp_owned'] = record['rdp'] > snapshot['completed_rdp']
            snapshot['slots'].append(record)
        elif kind == 'motion':
            numbers(record, {'gate', 'from', 'to', 'door', 'required', 'missing_physical', 'missing_published',
                             'waiting', 'suppressed', 'jump', 'cpu_ready', 'gpu_ready', 'candidate'}, number)
            require(record.get('direction') in ('none', 'toward', 'parallel', 'away'), f'line {number}: invalid motion direction')
            require(record.get('readiness') in ('ready', 'read', 'cpu', 'gpu', 'publication'), f'line {number}: invalid motion readiness')
            require(record['gate'] <= len(active['level'].get('transitions', [])) and record['from'] <= len(regions) and
                    record['to'] <= len(regions), f'line {number}: motion references unmapped gate/region')
            if record['gate']:
                gate = active['level']['transitions'][record['gate']-1]
                require(record['from'] == gate['from_region'] and record['to'] == gate['to_region'] and
                        record['door'] == gate.get('door_entity_id', 0), f'line {number}: motion gate differs from debug map')
            require(all(record[key] & ~mask_limit == 0 for key in ('required', 'missing_physical', 'missing_published', 'candidate')),
                    f'line {number}: motion references unmapped readiness mask')
            require(all(record[key] in (0, 1) for key in ('waiting', 'suppressed', 'jump', 'cpu_ready', 'gpu_ready')),
                    f'line {number}: invalid motion readiness flag')
            record['position'] = vector(record.get('position'), f'line {number} position')
            record['delta'] = vector(record.get('delta'), f'line {number} delta')
            record['kind'] = 'motion'
            active['motions'].append(record)
        elif kind == 'interaction':
            numbers(record, {'object', 'entity', 'eligible', 'in_range', 'raw_clear', 'effective_clear', 'source_entity',
                             'source_region', 'source_record', 'source_gate', 'query_cache_ready', 'suppressed'}, number)
            for key in ('blocker', 'associated_object'):
                require(key in record, f'line {number}: interaction missing {key}')
                record[key] = signed_integer(record[key], f'line {number} {key}')
            require(record.get('kind') in ('none', 'collider', 'gate_barrier', 'region_barrier', 'floor'),
                    f'line {number}: invalid blocker source kind')
            require(record.get('reason') in ('ready', 'out_of_range', 'consumed', 'loot_required', 'occluded', 'occupied') and
                    record.get('probe') in ('raw', 'effective'), f'line {number}: invalid interaction admission/probe')
            require(all(record[key] in (0, 1) for key in ('eligible', 'in_range', 'raw_clear', 'effective_clear', 'query_cache_ready', 'suppressed')),
                    f'line {number}: invalid interaction flag')
            require(record['source_region'] <= len(regions) and record['source_gate'] <= len(active['level'].get('transitions', [])),
                    f'line {number}: interaction references unmapped blocker')
            entities = {e['id']: e for e in active['level'].get('entities', [])}
            require(record['entity'] in entities and (record['source_entity'] == 0 or record['source_entity'] in entities),
                    f'line {number}: interaction references unmapped entity')
            require(entities[record['entity']].get('object_index', record['object']) == record['object'],
                    f'line {number}: interaction object index differs from debug map')
            object_ids = active['level'].get('object_entity_ids')
            if object_ids is not None:
                require(record['object'] < len(object_ids) and object_ids[record['object']] == record['entity'],
                        f'line {number}: interaction object index differs from debug map')
            if record['associated_object'] != -1:
                indices = {e['object_index'] for e in entities.values() if 'object_index' in e}
                require(record['associated_object'] in indices or
                        (object_ids is not None and 0 <= record['associated_object'] < len(object_ids)),
                        f'line {number}: unmapped associated interactive object')
            record['object_source_id'] = entities[record['entity']]['source_id']
            source = entities.get(record['source_entity'])
            record['blocker_source_id'] = source['source_id'] if source else None
            record['blocker_provenance'] = source.get('source_provenance') if source else None
            if record['source_record']:
                colliders = [c for c in active['level'].get('colliders', []) if c['region'] == record['source_region'] and
                             c['record'] == record['source_record']-1]
                if active['level'].get('colliders') is not None:
                    require(len(colliders) == 1 and colliders[0]['entity_id'] == record['source_entity'],
                            f'line {number}: collider source record differs from debug map')
                record['authored_collider'] = colliders[0] if colliders else None
            active['interactions'].append(record)
        elif kind == 'cost':
            numbers(record, {'job', 'elapsed', 'cpu', 'audio', 'commit', 'startup'}, number)
            record.setdefault('samples', '1')
            record.setdefault('max_cpu', str(record['cpu']))
            numbers(record, {'samples', 'max_cpu'}, number)
            require(record['samples'] > 0 and record['max_cpu'] <= record['cpu'] <= record['samples']*record['max_cpu'],
                    f'line {number}: inconsistent aggregated CPU samples')
            require('stage' in record, f'line {number}: cost missing stage')
            stage = record['stage']
            record['stage'] = STAGES.get(int(stage), 'unknown') if stage.isdecimal() else stage
            require(record['stage'] in STAGES.values(), f'line {number}: invalid service stage')
            require(record['cpu'] + record['audio'] <= record['elapsed'] and record['commit'] <= record['elapsed'] and
                    record['startup'] <= record['elapsed'],
                    f'line {number}: cost attribution exceeds elapsed time')
            active['costs'].append(record)
        elif kind == 'end':
            numbers(record, {'emitted', 'dropped', 'trace_ticks'}, number)
            if record['dropped']:
                finding('overflow', f"Bounded trace queue dropped {record['dropped']} records", number)
            if record['emitted'] != active['records']:
                finding('record_count', f"End marker emitted={record['emitted']}, imported={active['records']}", number)
            if record['seq'] != record['emitted']+record['dropped']+1:
                finding('end_sequence', 'End sequence does not account for emitted and dropped records', number)
            active['end'] = record
        else:
            raise TraceError(f'line {number}: unsupported rt_{kind} record')
        if kind != 'end':
            active['records'] += 1
    require(sessions, 'No runtime trace found. Build with --runtime-trace and capture a play session.')
    finish_snapshot()
    if active['end'] is None:
        finding('missing_end', 'Final runtime session has no rt_end; capture is still active or truncated')
    for session in sessions:
        if not session['snapshots']:
            diagnostics.append(dict(code='missing_snapshots', session=session['session'], message='Session contains no inspectable state snapshot'))
        for snapshot in session['snapshots']:
            snapshot.pop('_validated', None)
        for key in ('last_seq', 'last_time', 'generations', 'records', 'missing_ranges'):
            session.pop(key, None)
    return sessions, diagnostics


def job_report(session, diagnostics):
    """Correlate physical loads and scene candidates without conflating costs."""
    jobs, regions = {}, {r['id']: r for r in session['level']['regions']}
    rate = session['ticks_per_second']
    for event in session['events']:
        kind, job = event['kind'], event['job']
        if kind in ('request', 'prepare_begin'):
            if job in jobs:
                diagnostics.append(dict(code='repeated_job', session=session['session'], message=f'Job {job} began more than once'))
            jobs[job] = dict(job=job, type='region' if kind == 'request' else 'candidate',
                             region=event['region'], mask=event['mask'], requested=event['time'],
                             outcome='unfinished', milestones={}, costs={})
        if job in jobs:
            row = jobs[job]
            if kind == 'publish':
                row['milestones'].setdefault(kind, event['time'])
            else:
                row['milestones'][kind] = event['time']
            if kind in ('cancel', 'candidate_cancel'):
                if row['outcome'] == 'published':
                    row['retirement_reason'] = event['reason']
                else:
                    row.update(outcome='cancelled', reason=event['reason'], ended=event['time'])
            elif kind == 'failed':
                row.update(outcome='failed', reason=event['reason'], ended=event['time'])
            elif kind == 'publish':
                if row['outcome'] in ('cancelled', 'failed'):
                    diagnostics.append(dict(code='stale_publication', session=session['session'],
                                            message=f"Job {job} published after {row['outcome']}", line=event['line']))
                elif row['outcome'] != 'published':
                    row.update(outcome='published', ended=event['time'])
        if kind == 'publish':
            for row in jobs.values():
                if (row['type'] == 'region' and row['outcome'] == 'unfinished' and
                        event['mask'] & (1 << (row['region']-1))):
                    row.update(outcome='published', ended=event['time'])
                    row['milestones']['publish'] = event['time']
        if kind in ('retire', 'release') and job in jobs:
            jobs[job]['milestones'][kind] = event['time']
    for cost in session['costs']:
        if cost['job'] not in jobs:
            continue
        values = jobs[cost['job']]['costs'].setdefault(cost['stage'], dict(elapsed=0, cpu=0, audio=0, commit=0, startup=0, samples=0))
        for key in ('elapsed', 'cpu', 'audio', 'commit', 'startup'):
            values[key] += cost[key]
        values['samples'] += cost['samples']
    for row in jobs.values():
        row['source_region'] = regions[row['region']]['source_id'] if row['region'] else None
        row['latency_ms'] = (row['ended']-row['requested'])*1000/rate if 'ended' in row else None
        row['request_to_publication_ms'] = row['latency_ms'] if row['outcome'] == 'published' else None
        row['milestone_ms'] = {name: (tick-row['requested'])*1000/rate for name, tick in row['milestones'].items()}
        for values in row['costs'].values():
            for name in ('elapsed', 'cpu', 'audio', 'commit', 'startup'):
                values[name+'_ms'] = values[name]*1000/rate
    return list(jobs.values())


def make_report(log_bytes, mapping, source_path, *, map_path=None, rom_bytes=None):
    if rom_bytes is not None:
        require(mapping.get('rom_sha256') == hashlib.sha256(rom_bytes).hexdigest(),
                'Stale debug map: supplied ROM SHA-256 differs')
    text = log_bytes.decode('utf-8', errors='replace')
    validate_map(mapping)
    lighting_reports, lighting_findings = parse_lighting_records(text, mapping)
    if re.search(r'^DL64 rt_', text, re.MULTILINE):
        sessions, diagnostics = parse_trace(text, mapping)
    else:
        require(lighting_reports, 'No runtime trace or renderer diagnostics found. Capture a play session.')
        sessions, diagnostics = [], []
    diagnostics.extend(lighting_findings)
    for session in sessions:
        session['jobs'] = job_report(session, diagnostics)
        rate = session['ticks_per_second']
        session['duration_ms'] = (session['end']['time'] if session['end'] else max(
            [0]+[r['time'] for r in session['events']+session['snapshots']+session['costs']+session['motions']+session['interactions']]))*1000/rate
        session['trace_overhead_ms'] = session['end']['trace_ticks']*1000/rate if session['end'] else None
        totals = {}
        for cost in session['costs']:
            row = totals.setdefault(cost['stage'], dict(samples=0, elapsed_ms=0, cpu_ms=0, audio_ms=0, commit_ms=0, startup_ms=0,
                                                        max_cpu_ms=0, max_elapsed_ms=0))
            row['samples'] += cost['samples']
            for key in ('elapsed', 'cpu', 'audio', 'commit', 'startup'):
                row[key+'_ms'] += cost[key]*1000/rate
            row['max_cpu_ms'] = max(row['max_cpu_ms'], cost['max_cpu']*1000/rate)
            if cost['samples'] > 1 or row['max_elapsed_ms'] is None:
                row['max_elapsed_ms'] = None
            else:
                row['max_elapsed_ms'] = max(row['max_elapsed_ms'], cost['elapsed']*1000/rate)
        session['stage_totals'] = totals
    return dict(schema_version=1, complete=not diagnostics, diagnostics=diagnostics, sessions=sessions,
                lighting_reports=lighting_reports,
                source_resource_reports=[dict(level=level['id'], package_sha256=level.get('package_sha256'),
                    renderer_prediction=level.get('renderer_diagnostics'), bridge_estimate=level.get('bridge_diagnostics'))
                    for level in mapping['levels']],
                source_log=str(Path(source_path).resolve()), source_log_sha256=hashlib.sha256(log_bytes).hexdigest(),
                source_log_bytes=len(log_bytes), debug_map=str(Path(map_path).resolve()) if map_path else None,
                build_id=mapping['build_id'], rom_sha256=mapping.get('rom_sha256'),
                limitations=['Imported emulator trace; original hardware timing is not established.',
                    'Request-to-publication latency is an elapsed span, separate from active service CPU/audio/stage costs.',
                    'Snapshot completeness is verified; state between snapshots is observed only through emitted events.',
                    'An unfinished or overflowed trace remains explicitly incomplete.',
                    'Renderer policy reports use capture-line order, not invented frame timestamps; measured policy transport excludes its cost row.',
                    'Policy buckets classify preparation and lighting revisions; backend-specific steady animation/probe work and visible per-frame counts remain unmeasured.',
                    'ROM storage and resident-array values in cook reports are estimates; absent runtime allocation measurements remain unknown.'])


def write_html(path, report):
    """Write a self-contained, keyboard-accessible inspector; no server needed."""
    def exact(value):
        if isinstance(value, dict):
            return {key: exact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [exact(item) for item in value]
        return str(value) if type(value) is int and abs(value) > (1<<53)-1 else value
    payload = json.dumps(exact(report), separators=(',', ':'), allow_nan=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    page = HTML.replace('__REPORT__', payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding='utf-8')


HTML = r'''<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DarkLantern64 runtime inspector</title><style>
:root{color-scheme:dark;font:15px system-ui;background:#101417;color:#dce4e6}body{margin:24px;max-width:1500px}h1{font-size:25px;margin:0 0 8px}h2{font-size:18px}p{line-height:1.5;color:#aebdc2}.bar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:20px 0}.badge{padding:7px 12px;border:1px solid #5bbc93;border-radius:20px;background:#15372a}.bad{border-color:#ed9570;background:#4b281d}.panel{background:#191f24;border:1px solid #303e45;border-radius:8px;padding:16px;overflow:auto}main{display:grid;grid-template-columns:1fr 1fr;gap:16px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:7px;border-bottom:1px solid #344047;vertical-align:top}th{color:#a2b9c1;position:sticky;top:0;background:#191f24}select,input,button{font:inherit;background:#222d33;color:inherit;border:1px solid #51646d;border-radius:4px;padding:7px}input[type=range]{flex:1;min-width:200px}button{cursor:pointer}canvas{width:100%;height:380px;display:block}.small{font-size:12px}.legend{color:#c5d2d7}.full{grid-column:1/-1}.scroll{max-height:360px;overflow:auto}.muted{color:#94a7af}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}details{margin:8px 0}summary{cursor:pointer}.event{cursor:pointer}.event:hover{background:#27343b}@media(max-width:900px){main{grid-template-columns:1fr}body{margin:12px}}
</style><h1>DarkLantern64 runtime inspector</h1><p>Published geometry, physical chunks and background work on the same captured clock.</p>
<div id="status"></div><div class="bar"><label>Launch <select id="sessions"></select></label><button id="prev" aria-label="Previous snapshot">←</button><input id="scrub" aria-label="Snapshot timeline" type="range" min="0" value="0"><button id="next" aria-label="Next snapshot">→</button><span id="clock"></span></div>
<main><section class="panel"><h2>Authored regions and gates</h2><canvas id="map" width="760" height="380"></canvas><p class="small legend">Blue fill: published · amber border: wanted · green border: physical · dashed white: candidate · pink: pending gate · white dot: player · arrow: last requested movement. X/Z metres.</p><div id="state"></div></section>
<section class="panel"><h2>Slot progress and ownership</h2><div id="slots"></div><h2>Gate motion and interaction</h2><div id="objects"></div><div id="extra"></div></section>
<section class="panel full"><h2>Request → publication</h2><p class="small">Elapsed latency measures waiting plus distributed work. Service CPU, audio and commit are attributed separately. Cancelled candidates retain their actual outcome.</p><div id="jobs" class="scroll"></div><h2>Service stage totals</h2><div id="costs"></div><p id="overhead" class="small"></p></section>
<section class="panel full"><h2>Renderer lighting paths</h2><p class="small">Observed publication and lighting-change decisions, bound to source/package/build identity. Policy transport is measured elapsed time, including interrupts; runtime allocation remains unknown unless measured separately.</p><div id="lighting" class="scroll"></div><details><summary>Exact lighting records and source resource witnesses</summary><pre id="lighting-raw"></pre></details></section>
<section class="panel full"><div class="bar"><h2>Events and service costs</h2><input id="filter" aria-label="Filter events" placeholder="Filter: gate, region, reason…"><label><input id="near" type="checkbox"> ±1 second around snapshot</label></div><div id="events" class="scroll"></div><details><summary>Selected event: exact captured fields</summary><pre id="raw"></pre></details></section>
<section class="panel full"><details><summary>Capture provenance and limitations</summary><pre id="provenance"></pre></details><details><summary>Completeness findings</summary><div id="findings"></div></details></section></main>
<script type="application/json" id="data">__REPORT__</script><script>
const report=JSON.parse(document.getElementById('data').textContent), $=id=>document.getElementById(id), esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let session=report.sessions[0]||null,snapshot=null,index=0;
const ms=t=>(t*1000/session.ticks_per_second).toFixed(3),names=m=>session.level.regions.filter(r=>m&(1<<(r.id-1))).map(r=>r.source_id).join(', ')||'none',table=(heads,rows)=>'<table><thead><tr>'+heads.map(h=>'<th>'+esc(h)+'</th>').join('')+'</tr></thead><tbody>'+rows.map(row=>'<tr>'+row.map(c=>'<td>'+c+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
$('status').innerHTML='<span class="badge '+(report.complete?'':'bad')+'">'+(report.complete?'Complete capture':'Incomplete capture — '+report.diagnostics.length+' finding(s)')+'</span>';
$('sessions').innerHTML=report.sessions.map((s,i)=>'<option value="'+i+'">'+esc(s.level.id)+' · session '+s.session+'</option>').join('');
$('findings').innerHTML=report.diagnostics.length?'<ul>'+report.diagnostics.map(d=>'<li>'+esc(d.code)+': '+esc(d.message)+(d.line?' (log line '+d.line+')':'')+'</li>').join('')+'</ul>':'All captured sequences, session end markers and slot snapshots are complete.';
$('provenance').textContent=JSON.stringify({source_log:report.source_log,source_log_sha256:report.source_log_sha256,build_id:report.build_id,rom_sha256:report.rom_sha256,debug_map:report.debug_map,limitations:report.limitations},null,2);
const lighting=report.lighting_reports||[];
$('lighting').innerHTML=lighting.length?table(['Report / source level','Phase / published mask','Observed path and reason','Affected models / triangles','Measured policy transport'],lighting.map(r=>[esc(r.report+' · '+r.level_id),esc(r.phase+' · '+r.published),r.cases.map(c=>esc(c.path+' · '+c.mode+' · '+c.reason)).join('<br>'),r.cases.map(c=>esc(c.models+' / '+c.triangles)).join('<br>'),esc(r.cost?r.cost.elapsed_ms.toFixed(3)+' ms; active CPU unknown':'unknown; incomplete')])):'No renderer lighting reports captured; decisions and transport cost are unknown.';
$('lighting-raw').textContent=JSON.stringify({reports:lighting,source_resource_witnesses:report.source_resource_reports||[]},null,2);
if(!session){for(const id of ['sessions','prev','scrub','next','filter','near'])$(id).disabled=true;$('clock').textContent='Renderer diagnostics only; no streaming timeline captured.';}
function draw(){const c=$('map'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);const regions=session.level.regions,bounds=regions.filter(r=>r.bounds).map(r=>r.bounds);if(!bounds.length)return;
const lo=[Math.min(...bounds.map(b=>b[0][0])),Math.min(...bounds.map(b=>b[0][2]))],hi=[Math.max(...bounds.map(b=>b[1][0])),Math.max(...bounds.map(b=>b[1][2]))],scale=Math.min((c.width-50)/(hi[0]-lo[0]),(c.height-45)/(hi[1]-lo[1])),point=(x,z)=>[25+(x-lo[0])*scale,c.height-25-(z-lo[1])*scale];
function rect(b,color,width=1,dash=[],fill=null){const a=point(b[0][0],b[1][2]),d=point(b[1][0],b[0][2]);ctx.setLineDash(dash);ctx.lineWidth=width;ctx.strokeStyle=color;if(fill){ctx.fillStyle=fill;ctx.fillRect(a[0],a[1],d[0]-a[0],d[1]-a[1]);}ctx.strokeRect(a[0],a[1],d[0]-a[0],d[1]-a[1]);ctx.setLineDash([]);}
regions.forEach(r=>{if(!r.bounds)return;const bit=1<<(r.id-1);rect(r.bounds,'#46616c',1,[],snapshot&&snapshot.published&bit?'#234765':'#152129');if(snapshot&&snapshot.physical&bit)rect(r.bounds,'#70c79f',2);if(snapshot&&snapshot.wanted&bit)rect(r.activation_bounds||r.bounds,'#ffc168',2);if(snapshot&&snapshot.candidate&bit)rect(r.bounds,'#ecf3f5',2,[5,4]);const p=point((r.bounds[0][0]+r.bounds[1][0])/2,(r.bounds[0][2]+r.bounds[1][2])/2);ctx.fillStyle='#dce6eb';ctx.font='12px system-ui';ctx.fillText(r.source_id,p[0],p[1]);});
(session.level.transitions||[]).forEach(g=>{if(g.bounds)rect(g.bounds,snapshot&&g.index+1===snapshot.gate?'#ff8cbe':'#75556e',snapshot&&g.index+1===snapshot.gate?4:1);});if(snapshot){const p=point(snapshot.player[0],snapshot.player[2]);ctx.beginPath();ctx.fillStyle='#fff';ctx.arc(...p,5,0,Math.PI*2);ctx.fill();const motion=session.motions.filter(e=>e.seq<=snapshot.seq).at(-1);if(motion){const norm=Math.hypot(motion.delta[0],motion.delta[2]);if(norm>0){ctx.beginPath();ctx.moveTo(...p);ctx.lineTo(p[0]+motion.delta[0]/norm*18,p[1]-motion.delta[2]/norm*18);ctx.strokeStyle='#fff';ctx.stroke();}}}ctx.fillStyle='#a5b8c0';ctx.fillText('X →',c.width-42,c.height-5);ctx.fillText('Z ↑',6,16);}
function objectName(entity){return entity?entity.source_id:'unmapped';}
function render(){if(!session)return;snapshot=session.snapshots[index]||null;$('scrub').max=Math.max(0,session.snapshots.length-1);$('scrub').value=Math.max(0,index);$('clock').textContent=snapshot?ms(snapshot.time)+' ms · seq '+snapshot.seq:'No preceding state snapshot';
if(snapshot){const gate=(session.level.transitions||[]).find(g=>g.index+1===snapshot.gate);$('state').innerHTML=table(['State','Authored identity'],[['Current',esc(session.level.regions.find(r=>r.id===snapshot.current)?.source_id)],['Wanted',esc(names(snapshot.wanted))],['Physical',esc(names(snapshot.physical))],['Published',esc(names(snapshot.published))],['Candidate',esc(names(snapshot.candidate))],['Pending gate',esc(gate?.id||'none')],['Readiness',esc('prepare='+snapshot.preparing+' CPU='+snapshot.cpu_ready+' GPU='+snapshot.gpu_ready+' revision='+snapshot.revision)],['Snapshot',snapshot.complete?'Complete':'<strong>Incomplete slot rows</strong>']]);
$('slots').innerHTML=table(['Slot / generation','Region','State / read','CPU pins','Graphics ownership'],snapshot.slots.map(s=>[esc(s.slot+' / '+s.generation),esc(session.level.regions.find(r=>r.id===s.region)?.source_id||'none'),esc(s.state+' · '+s.bytes+'/'+s.total+' bytes'),esc(s.pins),esc('RSP '+s.rsp+(s.rsp_owned?' pending':' complete')+'; RDP '+s.rdp+(s.rdp_owned?' pending':' complete'))]));
const motion=session.motions.filter(e=>e.seq<=snapshot.seq).at(-1),interaction=session.interactions.filter(e=>e.seq<=snapshot.seq).at(-1),motionGate=motion?(session.level.transitions||[]).find(g=>g.index+1===motion.gate):null;let rows=[];if(motion){rows.push(['Motion at '+ms(motion.time)+' ms',esc((motionGate?.id||'no authored gate')+' · '+motion.direction)],['Required view',esc(names(motion.required))],['Readiness',esc(motion.readiness)],['Missing physical / published',esc(names(motion.missing_physical)+' / '+names(motion.missing_published))],['Input',esc('gate waiting='+motion.waiting+' horizontal suppressed='+motion.suppressed+' jump suppressed='+motion.jump)]);}if(interaction){rows.push(['Interaction at '+ms(interaction.time)+' ms',esc(interaction.object_source_id)],['Admission',esc('eligible='+interaction.eligible+' in range='+interaction.in_range+' raw LOS clear='+interaction.raw_clear+' effective LOS clear='+interaction.effective_clear+' reason='+interaction.reason+' suppressed='+interaction.suppressed)],['Blocking source',esc(interaction.kind+' · '+(interaction.blocker_source_id||'none')+' region='+interaction.source_region+' record='+interaction.source_record+' gate='+interaction.source_gate)],['Associated object',esc(objectName((session.level.entities||[]).find(e=>e.object_index===interaction.associated_object)))]);} $('objects').innerHTML=rows.length?table(['Last diagnostic','Captured observation'],rows):'No input/query diagnostic captured before this snapshot.';
const skip=new Set(['slots','line','session','seq','time','current','wanted','physical','published','candidate','gate','preparing','cpu_ready','gpu_ready','revision','completed_rsp','completed_rdp','player','complete']);$('extra').innerHTML='<details><summary>Position, memory and other state</summary><pre>'+esc(JSON.stringify({player:snapshot.player,...Object.fromEntries(Object.entries(snapshot).filter(([k])=>!skip.has(k)))},null,2))+'</pre></details>';
}else{$('state').textContent='State unavailable';$('slots').textContent='State unavailable';$('objects').textContent='State unavailable';$('extra').textContent='';}
 $('jobs').innerHTML=table(['Job / identity','Outcome','Request → publish ms','Milestones after request ms','Attributed active service ms'],session.jobs.map(j=>[esc(j.job+' · '+(j.source_region||names(j.mask))),esc(j.outcome+(j.reason?' · '+j.reason:'')),esc(j.request_to_publication_ms?.toFixed(3)),esc(Object.entries(j.milestone_ms).map(([k,v])=>k+' '+v.toFixed(3)).join('; ')),esc(Object.entries(j.costs).map(([k,v])=>k+': CPU '+v.cpu_ms.toFixed(3)+', audio '+v.audio_ms.toFixed(3)+', commit '+v.commit_ms.toFixed(3)+', startup '+v.startup_ms.toFixed(3)).join('; ')||'No attributed samples')]));$('costs').innerHTML=table(['Stage','Samples','CPU total ms','Audio total ms','Commit total ms','Startup ms','Max service CPU ms'],Object.entries(session.stage_totals).map(([stage,v])=>[esc(stage),esc(v.samples),esc(v.cpu_ms.toFixed(3)),esc(v.audio_ms.toFixed(3)),esc(v.commit_ms.toFixed(3)),esc(v.startup_ms.toFixed(3)),esc(v.max_cpu_ms.toFixed(3))]));$('overhead').textContent='Recorder/flush elapsed overhead: '+(session.trace_overhead_ms===null?'unavailable without end marker':session.trace_overhead_ms.toFixed(3)+' ms')+'; trace queue storage: '+(session.end?.buffer_bytes||'not recorded')+' bytes.';draw();renderEvents();}
function renderEvents(){const filter=$('filter').value.toLowerCase(),near=$('near').checked,entities=session.level.entities||[],all=[...session.events.map(e=>({...e,row_type:'event'})),...session.costs.map(e=>({...e,row_type:'cost'})),...session.motions.map(e=>({...e,row_type:'motion'})),...session.interactions.map(e=>({...e,row_type:'interaction'}))].sort((a,b)=>a.seq-b.seq);const rows=all.filter(e=>{let text=JSON.stringify(e)+' '+(e.region?session.level.regions.find(r=>r.id===e.region)?.source_id:'');for(const key of ['entity','source_entity','collider_entity','door','door_entity','object_entity'])if(e[key])text+=' '+objectName(entities.find(n=>n.id===Number(e[key])));if(e.gate)text+=' '+(session.level.transitions||[]).find(g=>g.index+1===e.gate)?.id;return text.toLowerCase().includes(filter)&&(!near||!snapshot||Math.abs(e.time-snapshot.time)<=session.ticks_per_second);});
$('events').innerHTML='<table><thead><tr><th>Time ms / seq</th><th>Event / stage</th><th>Authored identity</th><th>Reason or cost</th></tr></thead><tbody>'+rows.map(e=>'<tr class="event" tabindex="0" data-seq="'+e.seq+'"><td>'+ms(e.time)+' / '+e.seq+'</td><td>'+esc(e.row_type==='interaction'?'interaction · '+e.kind:e.kind||e.stage)+'</td><td>'+esc(e.object_source_id||(e.gate?(session.level.transitions||[]).find(g=>g.index+1===e.gate)?.id:null)||(e.region?session.level.regions.find(r=>r.id===e.region)?.source_id:(e.mask?names(e.mask):e.job||'—')))+'</td><td>'+esc(e.direction||e.reason||(e.row_type==='interaction'?'eligible='+e.eligible+' raw clear='+e.raw_clear+' effective clear='+e.effective_clear+' blocker='+(e.blocker_source_id||e.kind):(e.row_type==='cost'?'CPU total '+ms(e.cpu)+' ms / '+e.samples+' sample(s), max '+ms(e.max_cpu)+' ms; audio '+ms(e.audio)+' ms; elapsed total '+ms(e.elapsed)+' ms':'')))+'</td></tr>').join('')+'</tbody></table>';
for(const row of $('events').querySelectorAll('[data-seq]')){const select=()=>{const e=rows.find(e=>e.seq===Number(row.dataset.seq));$('raw').textContent=JSON.stringify(e,null,2);const snap=session.snapshots.reduce((best,s,i)=>s.seq<=e.seq?i:best,-1);index=snap;render();};row.onclick=select;row.onkeydown=e=>{if(e.key==='Enter')select();};}}
$('sessions').onchange=()=>{session=report.sessions[Number($('sessions').value)];index=0;render();};$('scrub').oninput=()=>{index=Number($('scrub').value);render();};$('prev').onclick=()=>{index=Math.max(0,index-1);render();};$('next').onclick=()=>{index=Math.min(Math.max(0,session.snapshots.length-1),index+1);render();};$('filter').oninput=renderEvents;$('near').onchange=renderEvents;render();
</script></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log', type=Path, help='Captured emulator log with DL64 rt_* records')
    parser.add_argument('--map', type=Path, required=True, dest='mapping', help='Debug map emitted beside this ROM')
    parser.add_argument('--rom', type=Path, help='Also verify the sidecar against actual ROM bytes')
    parser.add_argument('--output', type=Path, default=ROOT/'build/runtime-inspector.html')
    parser.add_argument('--json', type=Path, help='Machine-readable report; defaults to output with .json suffix')
    parser.add_argument('--require-complete', action='store_true', help='Return failure for incomplete captures after writing the diagnostic report')
    args = parser.parse_args(argv)
    try:
        json_path = args.json or args.output.with_suffix('.json')
        inputs = {p.resolve() for p in (args.log, args.mapping, args.rom) if p is not None}
        require(args.output.resolve() not in inputs and json_path.resolve() not in inputs and
                args.output.resolve() != json_path.resolve(), 'Outputs must be distinct and must not overwrite source inputs')
        mapping = json.loads(args.mapping.read_text(encoding='utf-8'))
        report = make_report(args.log.read_bytes(), mapping, args.log, map_path=args.mapping,
                             rom_bytes=args.rom.read_bytes() if args.rom else None)
        write_html(args.output, report)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
        print(f"Runtime inspector: {'complete' if report['complete'] else 'INCOMPLETE'}; {len(report['sessions'])} launch session(s)")
        for finding in report['diagnostics']:
            print(f"  {finding['code']}: {finding['message']}")
        print(f'HTML: {args.output.resolve()}')
        print(f'JSON: {json_path.resolve()}')
        return 1 if args.require_complete and not report['complete'] else 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f'Runtime inspector failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
