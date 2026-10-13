#!/usr/bin/env python3
"""Measure the district's complete wall-time route without hiding rare stalls."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re

try:
    from verify_bafford_district import ROOT, COMPLETE, NAMES, decode_log
    from profile_report import parse_windows
except ModuleNotFoundError:
    from tools.verify_bafford_district import ROOT, COMPLETE, NAMES, decode_log
    from tools.profile_report import parse_windows

CONFIG = 'DL64 stream_perf_config '
FRAME = 'DL64 stream_perf_frame '
TERMINAL = 'DL64 stream_perf_complete '
SAMPLE_NAMES = ('frame_ticks', 'interval_ticks', 'service_ticks', 'service_cpu_ticks',
                'render_ticks', 'dt_ticks', 'sim_before', 'sim_after', 'accumulator_ticks',
                'dropped_ticks', 'stage', 'resident_mask', 'heap_used', 'blocked_ticks', 'route_wait')
STAGES = {0: 'idle_or_read', 1: 'assemble', 2: 'gpu_prepare', 3: 'publication'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fields(payload, expected):
    result = {}
    for token in payload.split():
        require(token.count('=') == 1, 'Malformed performance field')
        key, value = token.split('=')
        require(key not in result, 'Duplicate performance field: '+key)
        result[key] = value
    require(result.keys() == set(expected), 'Missing or unknown performance fields')
    return result


def integer(value):
    require(re.fullmatch('[0-9]+', value) is not None, 'Performance integer must be nonnegative')
    return int(value)


def distribution(values, rate, budget_ms):
    if not values:
        return None
    ordered = sorted(values)
    return dict(samples=len(values), average_ms=sum(values)*1000/rate/len(values),
                min_ms=ordered[0]*1000/rate, max_ms=ordered[-1]*1000/rate,
                **{f'p{p}_ms': ordered[math.ceil(len(values)*p/100)-1]*1000/rate for p in (50,95,99)},
                over_budget_samples=sum(value*1000 > rate*budget_ms for value in values),
                quantile_method='nearest rank; acceptance uses every sample and the maximum')


def loading_episodes(frames, rate, predicate, *, bridge_unstepped=False):
    episodes=[];gap=[]
    for sample in frames:
        if predicate(sample):
            continuation=bool(episodes and episodes[-1]['epoch']==sample['epoch'] and
                              episodes[-1]['last_seq']+len(gap)==sample['seq']-1)
            if not continuation:
                episodes.append(dict(first_seq=sample['seq'],last_seq=sample['seq'],epoch=sample['epoch'],
                                     blocked_ticks=0,wall_ticks=0,frames=0,checkpoint=sample.get('latest_checkpoint')))
                gap=[]
            episode=episodes[-1];episode['last_seq']=sample['seq'];episode['blocked_ticks']+=sample['blocked_ticks']
            episode['wall_ticks']+=sample['interval_ticks']+sum(row['interval_ticks'] for row in gap)
            episode['frames']+=1+len(gap);gap=[]
        elif bridge_unstepped and episodes and episodes[-1]['epoch']==sample['epoch'] and sample['sim_after']==sample['sim_before']:
            gap.append(sample)
        else:gap=[]
    for episode in episodes:episode['wall_ms']=episode['wall_ticks']*1000/rate
    return episodes


def renderer_preparations(text, budget_ms):
    """The prepare immediately preceding level_load is startup/restart work."""
    records = []
    for line in text.splitlines():
        if line.startswith('DL64 scene_load '):
            row = {key: int(value) for key,value in re.findall(r'(\w+)=(\d+)',line)}
            require(row.get('ticks_per_second',0)>0 and 'total_ticks' in row, 'Malformed scene load timing')
            row['phase'] = 'transition'
            records.append(row)
        elif line.startswith('DL64 level_load ') and records:
            records[-1]['phase'] = 'initial_start' if not any(r['phase']=='initial_start' for r in records) else 'restart'
    result = {}
    for phase in ('initial_start','restart','transition'):
        rows = [row for row in records if row['phase']==phase]
        if not rows:
            result[phase] = dict(count=0)
            continue
        rate = rows[0]['ticks_per_second']
        require(all(row['ticks_per_second']==rate for row in rows), 'Scene load clock changed')
        total = sum(row['total_ticks'] for row in rows)
        lighting = [row.get('lighting_ticks',0)+row.get('alternate_lighting_ticks',0) for row in rows]
        result[phase] = dict(count=len(rows), total_seconds=total/rate,
            elapsed=distribution([row['total_ticks'] for row in rows],rate,budget_ms),
            lighting=distribution(lighting,rate,budget_ms),
            gpu_prepare=distribution([row.get('gpu_ticks',0) for row in rows],rate,budget_ms),
            lighting_fraction=sum(lighting)/total if total else 0)
    return result


def decode_performance(text, *, frame_budget_ms=1000/30, service_budget_ms=8, functional=None, allow_incomplete=False):
    require(math.isfinite(frame_budget_ms) and 0<frame_budget_ms<=1000/30 and
            math.isfinite(service_budget_ms) and 0<service_budget_ms<=8, 'Performance budgets may only tighten 33.333333ms/8ms maxima')
    result = dict(schema_version=1, accepted=False, timing_passed=False, simulation_passed=False,
                  limits=dict(frame_ms=frame_budget_ms,service_cpu_ms=service_budget_ms),
                  renderer_preparations=renderer_preparations(text,frame_budget_ms),
                  limitations=['Emulated CP0 elapsed ticks; original N64 performance remains unverified.',
                               'Render elapsed includes display/queue waits; it is not direct RSP/RDP execution timing.',
                               'Service CPU excludes audio callbacks; other interrupts and internal waits remain included.',
                               'Instrumentation output overhead remains in following raw frame intervals.'])
    # These exact legacy windows explain the old multi-second stalls, but their
    # fixed diagnostic dt cannot establish wall-time tick conservation.
    if 'DL64 profile ' in text:
        windows,partial = parse_windows(text)
        result['legacy_profile'] = dict(windows=len(windows), partial=partial,
            exact_samples=all(len(w.get('samples',{}).get('elapsed',[]))==w['frames'] for w in windows))
        if result['legacy_profile']['exact_samples']:
            rate = windows[0]['ticks_per_second']
            result['legacy_profile']['elapsed'] = distribution([v for w in windows for v in w['samples']['elapsed']],rate,frame_budget_ms)
            result['legacy_profile']['work'] = distribution([v for w in windows for v in w['samples']['work']],rate,frame_budget_ms)
    configs = [line[len(CONFIG):] for line in text.splitlines() if line.startswith(CONFIG)]
    if not configs:
        result['failures'] = ['Missing wall-time performance instrumentation; fixed-dt route proves no dropped-wall-time claim.']
        return result
    require(len(configs)==1, 'Repeated performance configuration')
    config = fields(configs[0],('version','ticks_per_second','sim_rate','dt','route','frame_budget_hz','service_budget_us'))
    require(config['version']=='2', 'Unsupported performance instrumentation')
    rate,sim_rate = integer(config['ticks_per_second']),integer(config['sim_rate'])
    require(rate>0 and sim_rate==60, 'Invalid performance clock/simulation rate')
    require(config['frame_budget_hz']=='30' and config['service_budget_us']=='8000', 'Unexpected instrumentation target budgets')
    result['configuration'] = config
    frames,terminals=[],[]
    previous=None; total_sim=0; dropped_by_epoch={}; checkpoints=[]; admitted_epochs=set(); config_seen=False
    for line in text.splitlines():
        if line.startswith(CONFIG):
            config_seen=True
        elif line.startswith(FRAME):
            require(config_seen, 'Performance frames precede configuration')
            require(not terminals, 'Performance frame follows completion')
            row = fields(line[len(FRAME):],('seq','epoch','samples'))
            seq,epoch=integer(row['seq']),integer(row['epoch'])
            require(seq==len(frames)+1, 'Missing or repeated performance frame')
            require(re.fullmatch('[0-9a-f]+(?:,[0-9a-f]+)*',row['samples']) is not None, 'Malformed performance samples')
            values=[int(value,16) for value in row['samples'].split(',')]
            require(len(values)==len(SAMPLE_NAMES) and all(value<=0xffffffff for value in values), 'Wrong performance sample shape/range')
            sample=dict(zip(SAMPLE_NAMES,values),seq=seq,epoch=epoch)
            require(sample['route_wait']&~7==0, 'Unknown route wait flags')
            require(not(sample['route_wait']&1 and epoch in admitted_epochs), 'Admission wait occurs during active route')
            require(sample['stage'] in STAGES and sample['resident_mask'] and sample['heap_used']>0, 'Invalid stage/residency/heap sample')
            require(sample['service_cpu_ticks']<=sample['service_ticks']<=sample['frame_ticks'] and
                    sample['render_ticks']<=sample['frame_ticks'], 'Overlapping/impossible performance timing')
            require(sample['sim_before']<=sample['sim_after']<=sample['sim_before']+8, 'Invalid simulation step count')
            if previous and epoch==previous['epoch']:
                require(sample['sim_before']==previous['sim_after'], 'Missing or repeated simulation ticks')
                require(sample['dropped_ticks']>=previous['dropped_ticks'], 'Dropped wall time regressed')
                accumulator=previous['accumulator_ticks']; prior_drop=previous['dropped_ticks']
            else:
                require(epoch==(0 if previous is None else previous['epoch']+1) and sample['sim_before']==0,
                        'Unexpected epoch/tick reset')
                accumulator=prior_drop=0
            delta=sample['sim_after']-sample['sim_before']
            sample['simulation_steps']=delta;sample['dropped_delta_ticks']=sample['dropped_ticks']-prior_drop
            sample['latest_checkpoint']=checkpoints[-1] if checkpoints else 'before_start'
            require(sample['blocked_ticks']<=delta, 'Blocked movement exceeds simulated ticks')
            expected=accumulator+sample['dt_ticks']-(sample['dropped_ticks']-prior_drop)
            require(abs(expected-delta*rate/sim_rate-sample['accumulator_ticks'])<=2,
                    'Simulation accumulator does not conserve submitted wall time')
            sample['dt_matches_wall']=abs(sample['dt_ticks']-sample['interval_ticks'])<=2
            total_sim+=delta;frames.append(sample);previous=sample;dropped_by_epoch[epoch]=sample['dropped_ticks']
        elif line.startswith('DL64 bafford_district '):
            row=json.loads(line[len('DL64 bafford_district '):])
            if row['checkpoint'] in ('start','restart_loaded'):admitted_epochs.add(row['epoch'])
            if frames:
                require(row['tick']==total_sim and row['epoch']==frames[-1]['epoch'], 'Checkpoint differs from measured simulation ticks')
            checkpoints.append(row['checkpoint'])
        elif line.startswith(TERMINAL):
            terminals.append(fields(line[len(TERMINAL):],('frames','epochs','dropped_ticks')))
    require(frames and len(terminals)<=1 and (terminals or allow_incomplete), 'Incomplete performance frame/terminal evidence')
    complete=bool(terminals)
    terminal=({key:integer(value) for key,value in terminals[0].items()} if complete else
              dict(frames=len(frames),epochs=len(dropped_by_epoch),dropped_ticks=sum(dropped_by_epoch.values())))
    require(terminal['frames']==len(frames) and terminal['epochs']==len(dropped_by_epoch) and
            terminal['dropped_ticks']==sum(dropped_by_epoch.values()), 'Performance terminal differs from measured frames/ticks')
    result.update(complete=complete,frame_count=len(frames),simulated_ticks=total_sim,
        terminal=terminal if complete else None,observed_summary=terminal,
        frames={name:distribution([row[name] for row in frames],rate,service_budget_ms if name=='service_cpu_ticks' else frame_budget_ms)
                for name in ('frame_ticks','interval_ticks','service_ticks','service_cpu_ticks','render_ticks')},
        stages={name:dict(frames=sum(row['stage']==stage for row in frames),
            elapsed=distribution([row['service_ticks'] for row in frames if row['stage']==stage],rate,frame_budget_ms),
            cpu=distribution([row['service_cpu_ticks'] for row in frames if row['stage']==stage],rate,service_budget_ms))
            for stage,name in STAGES.items()},
        worst_frames=sorted(frames,key=lambda row:max(row['frame_ticks'],row['interval_ticks']),reverse=True)[:12],
        worst_service_frames=sorted(frames,key=lambda row:row['service_cpu_ticks'],reverse=True)[:12],
        peak_heap_bytes=max(row['heap_used'] for row in frames),
        dt_mismatch_frames=sum(not row['dt_matches_wall'] for row in frames),dropped_ticks=terminal['dropped_ticks'])
    episodes=loading_episodes(frames,rate,lambda row:row['blocked_ticks'],bridge_unstepped=True)
    for episode in episodes:episode['simulation_ms']=episode['blocked_ticks']*1000/sim_rate
    result['loading_barriers']=dict(blocked_ticks=sum(row['blocked_ticks'] for row in frames),
        blocked_frames=sum(bool(row['blocked_ticks']) for row in frames),episodes=episodes,
        max_episode_wall_ms=max((episode['wall_ms'] for episode in episodes),default=0),
        max_frame_blocked_ticks=max(row['blocked_ticks'] for row in frames),
        scope='Requested movement/jump suppressed by stream filtering; zero-step frames between blocked ticks remain in the same episode; physical walls and route holds are separate')
    wait_episodes=loading_episodes(frames,rate,lambda row:row['route_wait']&2)
    result['route_waits']=dict(admission_frames=sum(bool(row['route_wait']&1) for row in frames),
        loading_frames=sum(bool(row['route_wait']&2) for row in frames),
        loading_wall_ms=sum(row['interval_ticks'] for row in frames if row['route_wait']&2)*1000/rate,
        loading_episodes=wait_episodes,max_loading_episode_ms=max((row['wall_ms'] for row in wait_episodes),default=0),
        interaction_frames=sum(bool(row['route_wait']&4) for row in frames),
        scope='Initial/restart admission reported separately; ordinary walking has no readiness waits or waypoint dwell')
    failures=[]
    if not complete:failures.append('Missing performance completion; measured prefix cannot establish acceptance')
    if config['dt']!='wall' or result['dt_mismatch_frames']:
        failures.append('Diagnostic dt does not equal raw wall time')
    if terminal['epochs']!=2 or terminal['dropped_ticks']:
        failures.append('Mission restart or no-dropped-wall-time requirement failed')
    result['simulation_passed']=not failures
    for name in ('frame_ticks','interval_ticks','service_ticks','render_ticks','service_cpu_ticks'):
        if result['frames'][name]['over_budget_samples']:
            failures.append(name+' exceeds its maximum budget')
    result['timing_passed']=not any('exceeds' in failure for failure in failures)
    if result['loading_barriers']['blocked_ticks']:
        failures.append('Loading barriers suppressed requested movement or jump')
    if config['route']!='continuous' or result['route_waits']['loading_frames']:
        failures.append('Route readiness waits hide loading latency')
    if any(line.startswith('DL64 capture_begin ') for line in text.splitlines()):
        failures.append('Framebuffer capture cannot establish ordinary frame/audio performance')
    if not functional or not functional.get('functional_passed'):
        failures.append('Functional route failed: '+str(functional.get('error','missing complete route') if functional else 'missing complete route'))
    if not functional or not functional.get('audio_continuity_passed'):
        failures.append('Ordinary route music has underruns or missing playback evidence')
    if tuple(checkpoints)!=NAMES:
        failures.append('Missing or reordered measured route checkpoints')
    heap_totals=[int(value) for value in re.findall(r'^DL64 level_start .*?heap=\d+/(\d+)',text,re.MULTILINE)]
    if not heap_totals or result['peak_heap_bytes']>min(heap_totals):
        failures.append('Measured memory exceeds the target heap or heap capacity is unreported')
    else:
        result['heap_headroom_bytes']=min(heap_totals)-result['peak_heap_bytes']
    if not re.search(r'^DL64 boot_ok memory=8388608 ',text,re.MULTILINE):
        failures.append('Missing 8MiB target boot evidence')
    result['failures']=failures;result['accepted']=not failures
    return result


def failed_run(error, build, settings):
    """Recover only the exact isolated log named by the exercise failure."""
    match=re.search(r'; inspect (.+)$',str(error))
    if not match:return None
    log=Path(match[1]).resolve();runs=(ROOT/'.dev/ares/runs').resolve()
    require(log.is_relative_to(runs) and log.name=='bafford-district-performance.log' and log.is_file(),
            'Exercise failure does not identify its isolated performance log')
    snapshot=log.parent/Path(build['rom']).name
    require(snapshot.is_file() and hashlib.sha256(snapshot.read_bytes()).hexdigest()==build['rom_sha256'],
            'Failed exercise lacks its matching ROM snapshot')
    (log.parent/'verification-build.json').write_text(json.dumps(build,indent=2)+'\n')
    return dict(name='bafford-district-performance',passed=False,error=str(error),log=str(log.relative_to(ROOT)),
        tested_rom=str(snapshot.relative_to(ROOT)),rom_sha256=build['rom_sha256'],rom_bytes=snapshot.stat().st_size,
        settings=str((log.parent/'settings.bml').relative_to(ROOT)),
        settings_source_sha256=hashlib.sha256(settings.read_bytes()).hexdigest() if settings.is_file() else None,
        terminal_event=None)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path);parser.add_argument('--log',type=Path)
    parser.add_argument('--baseline',action='store_true',help='Report an older immutable fixed-dt run without claiming target acceptance')
    parser.add_argument('--frame-budget-ms',type=float,default=1000/30);parser.add_argument('--service-budget-ms',type=float,default=8)
    parser.add_argument('--timeout',type=float,default=420)
    parser.add_argument('--output',type=Path,default=ROOT/'build/bafford-district-performance.json')
    args=parser.parse_args(argv);report=dict(passed=False,accepted=False,platform='Ares emulator')
    try:
        require(math.isfinite(args.timeout) and 0<args.timeout<=600,'Invalid timeout')
        if args.log:
            log=args.log.resolve();metadata=log.parent/'verification-build.json';build=json.loads(metadata.read_bytes())
            rom=args.rom.resolve() if args.rom else log.parent/Path(build['rom']).name
        else:
            require(not args.baseline,'Baseline analysis requires an existing log')
            from tools.build import build_rom
            rom=args.rom.resolve() if args.rom else build_rom(Path(os.environ.get('N64_INST','C:/n64-toolchain')),
                bundle=ROOT/'content/level_bundle.json',bafford_district_performance_test=True)
            build=json.loads((ROOT/'build'/rom.stem/'build.json').read_bytes())
        require(hashlib.sha256(rom.read_bytes()).hexdigest()==build['rom_sha256'],'ROM differs from immutable recorded build')
        require(build.get('bafford_district_test') and not build.get('bafford_district_test_capture'),'Missing ordinary district diagnostic')
        require(args.baseline or build.get('bafford_district_performance_test'),'Build lacks wall-time performance flag')
        report.update(rom=str(rom),rom_sha256=build['rom_sha256'],build_signature=build['signature'],baseline=args.baseline)
        if not args.log:
            from tools.smoke_ares import exercise
            exe=Path(os.environ.get('ARES_EXE',str(Path(os.environ.get('LOCALAPPDATA',''))/'ares/ares.exe')))
            settings=ROOT/'.dev/ares/settings-8mb.bml'
            try:
                run=exercise(exe,settings,rom,'bafford-district-performance',COMPLETE,args.timeout,build['rom_sha256'],memory=8)
            except RuntimeError as error:
                report['error']=str(error);run=failed_run(error,build,settings)
                if run is None:raise
            report['run']=run;log=ROOT/run['log']
            (log.parent/'verification-build.json').write_text(json.dumps(build,indent=2)+'\n')
        snapshot=log.parent/rom.name
        require(snapshot.is_file() and hashlib.sha256(snapshot.read_bytes()).hexdigest()==build['rom_sha256'],'Log lacks matching ROM snapshot')
        content=next(row['content'] for row in build['level_catalog']['levels'] if row['id']=='bafford-district')
        raw=log.read_bytes();text=raw.decode('utf-8',errors='replace')
        report.update(rom=str(rom),rom_sha256=build['rom_sha256'],build_signature=build['signature'],
            mission_sha256=content['mission']['sha256'],log=str(log),log_sha256=hashlib.sha256(raw).hexdigest(),baseline=args.baseline)
        try:
            functional=decode_log(text,content,capture=False,wall_time=bool(build.get('bafford_district_performance_test')),
                                  require_audio_continuity=False)
        except ValueError as error:
            functional=dict(functional_passed=False,audio_continuity_passed=False,error=str(error))
        report.update(decode_performance(text,frame_budget_ms=args.frame_budget_ms,service_budget_ms=args.service_budget_ms,
                                        functional=functional,allow_incomplete=True))
        report.update(functional=functional,passed=bool(not report.get('error') and
                    ((args.baseline and functional.get('functional_passed')) or report['accepted'])))
    except (ValueError,OSError,KeyError,StopIteration,RuntimeError) as error:
        report['error']=str(error)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({key:report[key] for key in ('passed','accepted','failures','error') if key in report},indent=2))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    raise SystemExit(main())
