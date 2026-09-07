"""Gate sustained two-guard gameplay against a 60 fps work / native-VI budget.

The measured ROM is ordinary gameplay: normal audio, HUD, lights, AI and poses;
no replay, capture freezes, graphics drains, or synthetic reduced geometry.
Visual evidence comes from a separate source-matched capture/review. Frustum
counts and submission alone cannot establish depth-visible guard pixels.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess

from build import ROOT, build_rom
from profile_report import make_report, parse_windows, require, write_report
from smoke_ares import exercise

ACTORS = {'animated-patrol', 'animated-sentry'}
LEVEL = ROOT / 'content/animation_workshop.json'
ASSET = ROOT / 'content/assets/guard/guard.character.json'
DEFAULT_OUTPUT = ROOT / 'build/guard-60fps/performance-verification.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(text, name):
    return [dict(token.split('=', 1) for token in line.split()[2:] if '=' in token)
            for line in text.splitlines() if line.startswith('DL64 ' + name + ' ')]


def visibility_evidence(path, source_sha256, asset_sha256, renderer=None):
    """Validate provenance of an explicit image review, never infer pixel proof.

    Review JSON: method='separate-framebuffer-visual-review', source_sha256,
    asset_sha256, preset='two-guards', renderer='cpu' or 't3d', images=[{path,sha256}],
    visible_guards=[both entity IDs], optional full_body_guards, reviewer.
    Paths may be absolute or relative to the review JSON. The reviewer must
    actually inspect the cited images; this intake validates provenance only.
    """
    value = json.loads(path.read_text(encoding='utf-8'))
    require(value.get('method') == 'separate-framebuffer-visual-review', 'Unsupported visibility evidence method')
    require(value.get('source_sha256') == source_sha256 and value.get('asset_sha256') == asset_sha256,
            'Visibility evidence is for different source content')
    require(value.get('preset') == 'two-guards', 'Visibility evidence uses a different camera preset')
    require(value.get('renderer') in ('cpu', 't3d') and (renderer is None or value['renderer'] == renderer),
            'Visibility evidence uses a different renderer')
    require(set(value.get('visible_guards', [])) == ACTORS,
            'Both guards must be visually confirmed on screen')
    require(isinstance(value.get('reviewer'), str) and value['reviewer'].strip(), 'Visibility evidence needs an explicit reviewer')
    require(isinstance(value.get('images'), list) and value['images'], 'Visibility evidence has no images')
    for image in value['images']:
        image_path = Path(image['path'])
        if not image_path.is_absolute(): image_path = path.parent / image_path
        require(sha(image_path) == image['sha256'], 'Visibility evidence image changed')
    return {'path': str(path.resolve()), 'sha256': sha(path), **value}


def ordinary_manifest(path, rom):
    manifest = json.loads(path.read_text(encoding='utf-8'))
    require(manifest.get('rom_sha256') == sha(rom), 'Build manifest does not match measured ROM')
    require(all(manifest.get(name) is False for name in ('autoplay', 'capture', 'debug_overlay', 'scale_bench', 'menu_test')),
            'Performance measurement requires an ordinary ROM with diagnostics/replay disabled')
    require(manifest.get('model_culling') is True, 'Performance measurement requires ordinary model culling')
    require(str(manifest.get('level')).replace('\\','/') == 'content/animation_workshop.json'
            and manifest.get('start_preset') == 'two-guards',
            'Performance measurement requires authored workshop / two-guards start')
    catalog = manifest.get('level_catalog', {}).get('levels', [])
    require(len(catalog) == 1 and catalog[0].get('id') == 'animation_workshop', 'Unexpected level catalog')
    digest = hashlib.sha256(LEVEL.read_bytes())
    dependencies = catalog[0]['content']['dependencies']
    for dependency in dependencies:
        uri = dependency['uri']
        path = (ROOT/uri.removeprefix('code:') if uri.startswith('code:') else LEVEL.parent/uri).resolve()
        require(path.is_relative_to(ROOT), 'Build dependency is outside the project')
        require(sha(path) == dependency['sha256'], f'Build dependency changed: {uri}')
        digest.update(uri.encode()); digest.update(b'\0'); digest.update(path.read_bytes())
    require(digest.hexdigest() == manifest.get('source_sha256'), 'Build content differs from current canonical fixture')
    return manifest


def analyze(raw, log, *, warmup=3, windows=30):
    """Fail closed on missing samples/present/visibility data; preserve diagnosis."""
    profile = make_report(raw, log, target_fps=60, skip_windows=warmup, max_windows=windows)
    text = raw.decode('utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
    parsed, _ = parse_windows(text)
    selected = parsed[warmup:warmup+windows]
    checks = []

    def check(name, passed, detail):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail})

    seconds = profile['frame']['total_ticks'] / profile['ticks_per_second']
    check('steady_window', warmup >= 3 and len(selected) == windows and seconds >= 30,
          f'{warmup} warmup windows excluded; {len(selected)} complete windows, {seconds:.3f}s measured; minimum 30s')
    exact = profile['exact_frame_samples_available']
    check('complete_frame_distributions', exact, 'Every elapsed/work sample must be present; bounded-buffer overflow cannot pass')
    work = profile.get('distributions', {}).get('work', {})
    check('16.667ms_work_budget', exact and work.get('over_budget_frames') == 0,
          'All measured frames <=1000/60ms excluding display-buffer wait; includes normal audio, logging and queue stalls')
    video = profile.get('video', {})
    check('native_vi_presentation', video.get('tracked') and 59 <= video.get('native_refresh_hz', 0) <= 61
          and video.get('repeats') == 0 and video.get('max_gap_vis') == 1
          and video.get('max_interval_ms', float('inf')) <= 1500/video['native_refresh_hz']
          and abs(video.get('presents', 0) - profile['frames']) <= 2,
          'One new framebuffer per native NTSC VI, zero repeated scans, no callback gap >1.5 VI periods; native refresh is reported precisely')
    workload = profile.get('workload', {})
    check('both_guards_drawn_every_frame', workload.get('frames') == profile['frames']
          and all(workload.get(f'{name}_{bound}') == 2 for name in ('animated', 'drawn')
                  for bound in ('min', 'max')) and workload.get('triangles_min', 0) > 0
          and 0 <= workload.get('heads_min', -1) <= workload.get('heads_max', -1) <= 2,
          'Every frame: two posed guard submissions; active attention head overlays are reported in 0..2 (quiet patrol may be zero); '
          + workload.get('geometry_evidence', 'missing evidence')
          + '; image review separately proves appearance')
    check('ordinary_live_workload', profile['debug_states'] == [0]
          and all(w['audio_calls'] > 0 and w['slots']['gameplay']['ticks'] > 0 for w in selected)
          and not any(token in text for token in ('DL64 capture_', 'DL64 replay_', 'RSP CRASH |', 'caught=1', 'complete=1')),
          'Normal gameplay and audio remain active; no capture/replay/terminal/error state')
    starts = records(text, 'level_start')
    check('authored_camera', len(starts) == 1 and starts[0].get('id') == 'animation_workshop'
          and starts[0].get('preset') == 'two-guards', 'One ordinary launch at authored two-guards preset')

    # Actor log records between complete window boundaries are from the same
    # steady interval, not merely a successful spawn during startup.
    segment = text.split(f'DL64 profile_end window={warmup}\n', 1)[-1] if warmup else text
    segment = segment.split(f'DL64 profile_end window={warmup+windows}\n', 1)[0]
    actors = {}
    for row in records(segment, 'enemy'):
        actors.setdefault(row['id'], []).append({'position': [float(v) for v in row['position'].split(',')],
            'patrol_index': int(row['patrol_index']), 'state': row['state']})
    patrol, sentry = actors.get('animated-patrol', []), actors.get('animated-sentry', [])
    moved = lambda samples: max((math.dist(samples[0]['position'], s['position']) for s in samples), default=0)
    check('independent_live_actors', set(actors) == ACTORS and len(patrol) >= 4 and len(sentry) >= 4
          and moved(patrol) > .5 and len({s['patrol_index'] for s in patrol}) >= 2 and moved(sentry) < .02,
          'Patrol advances along authored route while sentry holds its post during measured interval')
    ready = records(text, 'animation_ready')
    check('two_character_allocations', len(ready) == 1 and ready[0].get('models') == '2', 'Two independently animated model instances')
    nested = {}
    for name, tick_fields in (('rsp_lighting', ('probe_ticks', 'normal_ticks', 'color_upload_ticks')),
                             ('animation_profile', ('sample_ticks', 'skin_ticks'))):
        rows = records(segment, name)
        if not rows: continue
        require(all(int(row['frames']) > 0 and int(row['ticks_per_second']) == profile['ticks_per_second']
                    and all(int(row[field]) >= 0 for field in tick_fields) for row in rows),
                f'Invalid {name} sub-timing clocks/counts')
        frames = sum(int(row['frames']) for row in rows)
        nested[name] = {'frames': frames, 'windows': len(rows),
            'average_ms_per_frame': {field.removesuffix('_ticks'): sum(int(row[field]) for row in rows)*1000/
                                     profile['ticks_per_second']/frames for field in tick_fields},
            'scope': 'Nested CPU elapsed excluding audio; independent report windows selected inside steady interval. Do not add to parent slots.'}
    return {'schema_version': 1, 'passed': all(c['passed'] for c in checks), 'profile': profile,
            'checks': checks, 'actor_samples': actors, 'runtime_allocations': ready,
            'nested_timings': nested,
            'limitations': [
                'Ares CP0/VI timing model, not original N64/M64 hardware validation or host monitor presentation.',
                'VI_ORIGIN is observed once per VI before libdragon updates it, so evidence has one-field latency.',
                'Work excludes waiting for a free display buffer; actual VI-origin changes separately gate graphics throughput.',
                'Exact timing samples and logging add overhead; there are no capture dumps or forced graphics waits in the measured loop.',
                'This verifies the authored two-guard camera/route, not crowded combat or worst-case audio.',
                'Active procedural head attention is counted; quiet patrol/sentry can report zero and does not benchmark alerted head tracking.',
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom', type=Path, help='Use an existing ordinary ROM; never rebuild it')
    parser.add_argument('--manifest', type=Path, help='Matching build.json (required with a renamed --rom)')
    parser.add_argument('--log', type=Path, help='Analyze an existing log without launching/building')
    parser.add_argument('--visibility-report', type=Path, help='Source-matched separate framebuffer image review')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--windows', type=int, default=30, help='Measured windows; shorter runs are diagnostics and cannot pass the 30s gate')
    parser.add_argument('--warmup-windows', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=600)
    parser.add_argument('--sdk', type=Path, default=Path(os.environ.get('N64_INST', 'C:/n64-toolchain')))
    parser.add_argument('--renderer', choices=('cpu', 't3d'), default='t3d', help='Renderer backend when building a fresh ROM')
    args = parser.parse_args()
    report = {'passed': False}
    try:
        require(args.windows > 0 and args.warmup_windows >= 0, 'Invalid window counts')
        require(math.isfinite(args.timeout) and 0 < args.timeout <= 1800, 'Timeout must be 0..1800 seconds')
        source_hash, asset_hash = sha(LEVEL), sha(ASSET)
        run = None
        manifest = None
        if args.log:
            log = args.log
        else:
            rom = args.rom or build_rom(args.sdk, level=LEVEL, start_preset='two-guards', renderer=args.renderer)
            manifest_path = args.manifest or ROOT / 'build' / rom.stem / 'build.json'
            manifest = ordinary_manifest(manifest_path, rom)
            executable = Path(os.environ.get('ARES_EXE', str(Path(os.environ['LOCALAPPDATA']) / 'ares/ares.exe')))
            settings = ROOT / '.dev/ares/settings-8mb.bml'
            if not settings.is_file(): settings = executable.parent / 'settings.bml'
            marker = f'DL64 profile_end window={args.warmup_windows+args.windows}'
            run = exercise(executable, settings, rom, 'guard-performance', marker, args.timeout, sha(rom))
            log = ROOT / run['log']
        report = analyze(log.read_bytes(), log, warmup=args.warmup_windows, windows=args.windows)
        report.update(source_sha256=source_hash, asset_sha256=asset_hash, run=run,
                      manifest=manifest, baseline_report='build/guard-60fps/baseline-two-guards.json')
        evidence = visibility_evidence(args.visibility_report, source_hash, asset_hash,
            manifest.get('renderer', 'cpu') if manifest else None) if args.visibility_report else None
        report['visibility_evidence'] = evidence
        report['checks'].append({'name': 'actual_visible_guard_images', 'passed': evidence is not None,
            'detail': 'Separately reviewed source-matched framebuffer images; geometry counters alone cannot satisfy this gate'})
        report['checks'].append({'name': 'ordinary_rom_provenance', 'passed': manifest is not None,
            'detail': 'Existing-log analysis alone is diagnostic; a measured ROM must match its ordinary build manifest'})
        require(sha(LEVEL) == source_hash and sha(ASSET) == asset_hash, 'Canonical content changed during measurement')
        report['passed'] = all(c['passed'] for c in report['checks'])
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.CalledProcessError) as error:
        report.update(passed=False, error=str(error))
    write_report(args.output, report)
    print(json.dumps({'passed': report['passed'], 'output': str(args.output),
                      'failed_checks': [c for c in report.get('checks', []) if not c['passed']],
                      'error': report.get('error')}, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
