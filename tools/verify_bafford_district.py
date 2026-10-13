#!/usr/bin/env python3
"""Verify the input-driven district tour against the exact cooked working sets."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

try:
    from verify_bafford_stream import require, integer, vector, music_evidence
except ModuleNotFoundError:
    from tools.verify_bafford_stream import require, integer, vector, music_evidence

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'DL64 bafford_district '
COMPLETE = 'DL64 bafford_district_test_complete'
CAPTURE_COMPLETE = 'DL64 bafford_district_capture_complete views=3'
NAMES = ('start','street_center','ramp_start','ramp_top','jump1_takeoff','jump1_air',
         'first_stair','door_open','jump2_takeoff','jump2_air','entrance','main_room',
         'loot_collected','west_exit','rear_entry','courtyard',
         'courtyard_east_approach','courtyard_east_crossed','courtyard_east_back','courtyard_after_east','next_approach','next_deep',
         'next_west_approach','next_west_crossed','next_west_return',
         'courtyard_after_next','west_approach','west_deep','west_return','courtyard_after_west',
         'west_corner_return','west_exit_return','main_room_reloaded','door_return','door_closed',
         'ramp_return','street_return','east_street','east_ramp_top','east_interior','restart_loaded')
WAYPOINTS = {'start':(-6.522049,-2.4384,49.8252936),'street_center':(0,-2.4384,48.5),
    'ramp_start':(0,-2.35,45.98),'ramp_top':(0,-1.0935,40.95),'first_stair':(0,-.6096,40.4),
    'door_open':(0,-.6096,40.4),'entrance':(0,0,39.3),'main_room':(0,0,34.6),
    'loot_collected':(0,0,34.6),'west_exit':(-2.25,0,33.3),'rear_entry':(-2.25,0,32.2),
    'courtyard':(-2.25,0,29.8),'next_approach':(-1.2,0,26.3),'next_deep':(-1.2,0,23.9),
    'courtyard_east_approach':(5,0,29.8),'courtyard_east_crossed':(5,0,33.1),
    'courtyard_east_back':(5,0,29.8),'courtyard_after_east':(-2.25,0,29.8),
    'next_west_approach':(-1.2,0,26.5),'next_west_crossed':(-4.2,0,26.5),'next_west_return':(-1.2,0,26.5),
    'courtyard_after_next':(-1.2,0,28.5),'west_approach':(-5,0,28.5),'west_deep':(-5,0,26.3),
    'west_return':(-5,0,28.5),'courtyard_after_west':(-2.25,0,29.8),'west_corner_return':(-2.25,0,32.2),
    'west_exit_return':(-2.25,0,33.3),'main_room_reloaded':(0,0,34.6),'door_return':(0,0,39.3),
    'door_closed':(0,-.3048,40.4),'ramp_return':(0,-2.35,45.98),'street_return':(0,-2.4384,48.5),
    'east_street':(5,-2.4384,48.5),'east_ramp_top':(5,-.056,36.8),'east_interior':(5,0,35.5),
    'restart_loaded':(-6.522049,-2.4384,49.8252936)}


def decode_log(text, content, *, capture=None, require_music=True, wall_time=False, require_audio_continuity=True):
    require(not any(v in text for v in ('ASSERTION FAILED:', 'RSP CRASH |')), 'ROM reported a runtime failure')
    lines = text.splitlines()
    rows = [json.loads(line[len(PREFIX):]) for line in lines if line.startswith(PREFIX)]
    require(tuple(row.get('checkpoint') for row in rows) == NAMES, 'Missing, duplicated or reordered district checkpoint')
    terminals = [dict(re.findall(r'([a-z_]+)=([^ ]+)',line)) for line in lines if line.startswith(COMPLETE)]
    require(len(terminals) == 1, 'Missing unique district completion')
    terminal = terminals[0]
    require(all(terminal.get(k)=='1' for k in ('passed','input_only','coverage','collision_clear','loot_persist','restart_reload'))
            and terminal.get('stair_jumps') == '2' and int(terminal.get('audio_calls','0')) > 0,
            'Missing district traversal/persistence/audio assertions')
    start_indices = [i for i,line in enumerate(lines) if line.startswith('DL64 level_start ')]
    starts = [lines[i] for i in start_indices]
    require(len(starts)==2 and all('id=bafford-district ' in line for line in starts)
            and any(line.startswith('DL64 menu_ready ') and 'mode=menu' in line for line in lines),
            'District must launch through menu and rebuild on remote restart')
    first_row = lines.index(next(line for line in lines if line.startswith(PREFIX)))
    require(start_indices[0] < first_row and start_indices[1] > first_row
            and lines.index(next(line for line in lines if line.startswith(COMPLETE))) >
                max(i for i,line in enumerate(lines) if line.startswith(PREFIX)), 'Invalid launch/completion ordering')
    mission = content['mission']['report']
    regions = {row['id']:row for row in mission['regions']}
    cases = mission['working_sets']
    slot_count = mission['max_resident_regions']
    allocated = slot_count * ((max(row['chunk_bytes'] for row in regions.values())+15)&~15)
    textures = len(content['textures']['textures'])
    def working(ids):
        require(isinstance(ids,list) and ids and len(set(ids))==len(ids) and
                all(type(i) is int and i in regions for i in ids), 'Invalid resident region IDs')
        matches = [case for case in cases if set(case['regions'])==set(ids)]
        require(matches, 'Resident set is outside cooked transition budget')
        return matches[0]
    views = []
    for line in lines:
        if line.startswith('DL64 stream_view '):
            fields = dict(re.findall(r'([a-z_]+)=([^ ]+)',line))
            mask = int(fields['resident_mask']); ids = [i for i in regions if mask & (1 << (i-1))]
            require(mask == sum(1 << (i-1) for i in ids), 'Unknown stream view region')
            case = working(ids)
            require(int(fields['triangles'])==case['triangle_count'] and
                    int(fields['colliders'])==case['collider_count']+case['barrier_count'],
                    'Published stream view differs from cooked records')
            views.append(dict(resident=ids,triangles=int(fields['triangles']),colliders=int(fields['colliders'])))
    require(views, 'Missing published stream view evidence')
    for row in rows:
        point = vector(row['player'],'player')
        require(-2.47 <= point[1] < .4 and row['collision_clear']==1 and row['grounded'] in (0,1),
                'Player lost floor/body coverage')
        integer(row['tick'],'tick',1); integer(row['heap_used'],'heap_used',1)
        require(type(row['elapsed']) in (float,int) and math.isfinite(row['elapsed']) and row['elapsed']>0,
                'Invalid simulation time')
        stream = row['stream']; visible = stream['view'] if wall_time else stream['resident']; case = working(visible)
        resident = stream['resident']
        require(isinstance(resident,list) and len(resident)<=slot_count and len(set(resident))==len(resident) and
                all(type(i) is int and i in regions for i in resident), 'Invalid physical resident IDs')
        require(stream['current'] in visible and stream['current'] in resident and
                (stream['settled'] in (0,1) if wall_time else stream['settled']==1), 'Checkpoint lacks ready current region')
        if row['checkpoint'] in ('start','restart_loaded'):
            require(stream['settled']==1, 'Mission admission lacks complete published working set')
        reserved_ok = (sum(regions[i]['chunk_bytes'] for i in resident)<=stream['reserved']<=allocated
                       if wall_time else stream['reserved']==case['chunk_bytes'])
        require(stream['slots']==slot_count and stream['allocated']==allocated and reserved_ok,
                'Unbounded or incorrect slot reservation')
        require(row['models']==case['model_count'] and row['triangles']==case['triangle_count'] and
                row['colliders']==case['collider_count']+case['barrier_count'] and
                row['floors']==case['floor_count'] and row['textures']==textures, 'Working resources differ from cook')
        policy = mission['policy_regions'][stream['current']-1]
        resident_mask=sum(1 << (i-1) for i in visible)
        require(resident_mask & policy['working_mask'] == policy['working_mask'], 'Current visible set is not fully ready')
        lo,hi = policy['activation_bounds']
        require(all(lo[i]-.025 <= point[i] <= hi[i]+.025 for i in range(3)), 'Checkpoint outside activation bounds')
        for field in ('completed_loads','retired','readbytes','stalls'):
            integer(stream[field],field)
    for a,b in zip(rows,rows[1:]):
        require(b['tick']>=a['tick'] if wall_time else b['tick']>a['tick'], 'Input ticks regressed')
        if b['epoch'] != a['epoch']:
            require(a['epoch']==0 and b['epoch']==1 and b['checkpoint']=='restart_loaded', 'Unexpected state reset')
            continue
        seconds = b['elapsed']-a['elapsed']
        if wall_time and b['tick']==a['tick']:
            require(seconds==0 and b['player']==a['player'] and b['grounded']==a['grounded'] and
                    b['vertical_velocity']==a['vertical_velocity'], 'Unstepped checkpoint changed physical pose or elapsed time')
        else:
            require(seconds>0 and abs(seconds-(b['tick']-a['tick'])/60)<.012, 'Time differs from ordinary fixed input ticks')
        require(math.hypot(b['player'][0]-a['player'][0],b['player'][2]-a['player'][2]) <= 2.15*seconds+.025,
                'Movement exceeds ordinary walking speed')
        require(all(b['stream'][k]>=a['stream'][k] for k in ('completed_loads','retired','readbytes','stalls')),
                'Stream counters regressed')
    named = {row['checkpoint']:row for row in rows}
    for name,point in WAYPOINTS.items():
        row=named[name]
        require(row['grounded']==1 and math.hypot(row['player'][0]-point[0],row['player'][2]-point[2])<.12 and
                abs(row['player'][1]-point[1])<.045,'Incorrect supported route waypoint: '+name)
    for jump in (1,2):
        takeoff,air = named[f'jump{jump}_takeoff'],named[f'jump{jump}_air']
        require(takeoff['grounded']==1 and air['grounded']==0 and air['vertical_velocity']>0 and
                air['jump_inputs']==takeoff['jump_inputs']==jump and air['tick']>takeoff['tick'] and
                air['elapsed']>takeoff['elapsed'], 'Missing rising physical stair jump')
    source_regions = {row['source_id']: row['id'] for row in regions.values()}
    for name, source in (('next_west_approach','next-passage'), ('next_west_crossed','west-return'),
                         ('next_west_return','next-passage'),('courtyard_east_approach','rear-courtyard'),
                         ('courtyard_east_crossed','lower-east-alley'),('courtyard_east_back','lower-east-alley'),
                         ('courtyard_after_east','rear-courtyard')):
        require(named[name]['stream']['current']==source_regions[source], 'Missing direct district room transition: '+name)
    require(named['door_open']['door_open']==1 and named['door_closed']['door_open']==0, 'Door interaction failed')
    pickup,returned,restart = named['loot_collected'],named['main_room_reloaded'],named['restart_loaded']
    require(pickup['loot_value']==returned['loot_value']==25 and pickup['collected_objects']==returned['collected_objects']!=0
            and returned['loot_retired']==returned['loot_reloaded']==returned['door_open']==1,
            'Door/loot state did not survive retirement and reload')
    home=pickup['loot_home']
    absent=[row for row in rows[rows.index(pickup)+1:rows.index(returned)] if home not in row['stream']['resident']]
    require(absent and home in returned['stream']['resident'] and
            returned['stream']['completed_loads']>absent[-1]['stream']['completed_loads'], 'Loot home did not actually unload and reload')
    require(restart['epoch']==1 and not restart['loot_value'] and not restart['collected_objects'] and not restart['door_open'],
            'Remote restart did not reset mission state')
    visited = {row['stream']['current'] for row in rows if row['epoch']==0}
    require(len(visited)>=5 and int(terminal['visited_regions'])>=len(visited) and
            int(terminal['pre_restart_loads'])>len(visited) and named['east_interior']['stream']['retired']>0,
            'No meaningful region retirement/reload tour')
    actual_capture = CAPTURE_COMPLETE in lines
    require(capture is None or actual_capture==capture, 'Capture mode differs from build')
    require(('DL64 capture_begin ' in text)==actual_capture, 'Incomplete framebuffer diagnostic')
    music = music_evidence(text) if 'DL64 music cue=' in text else None
    require(not require_music or music is not None, 'Missing assigned music playback')
    require(not require_audio_continuity or actual_capture or not music or music['max_underruns']==0,
            'Ordinary district route has music underruns')
    return dict(passed=True,functional_passed=True,checkpoints=rows,terminal=terminal,
                visited_regions=sorted(visited),pre_restart_loads=int(terminal['pre_restart_loads']),
                retirements=named['east_interior']['stream']['retired'],allocated_slot_bytes=allocated,
                peak_heap_bytes=max(row['heap_used'] for row in rows),working_views=views,
                capture_diagnostic=actual_capture,music=music,audio_continuity_passed=bool(music and music['max_underruns']==0),
                checkpoint_scope='Pre-update observations; queued input is not executed until a later fixed tick',
                timing_scope='Framebuffer diagnostic; ordinary timing/audio assessed separately' if actual_capture else
                             'Continuous wall-time input tour on Ares; original N64 performance remains unverified' if wall_time else
                             'Input-only tour on Ares; original N64 performance remains unverified')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rom',type=Path); parser.add_argument('--log',type=Path)
    parser.add_argument('--capture',action='store_true')
    parser.add_argument('--sdk',type=Path,default=Path(os.environ.get('N64_INST','C:/n64-toolchain')))
    parser.add_argument('--ares',type=Path,default=Path(os.environ.get('ARES_EXE',str(Path(os.environ.get('LOCALAPPDATA',''))/'ares/ares.exe'))))
    parser.add_argument('--timeout',type=float,default=420)
    parser.add_argument('--output',type=Path,default=ROOT/'build/bafford-district-verification.json')
    args = parser.parse_args(argv); report = dict(passed=False,platform='Ares emulator')
    try:
        require(math.isfinite(args.timeout) and 0<args.timeout<=600,'Timeout must be 0..600 seconds')
        require(not args.log or args.rom,'--log requires its original --rom')
        from tools.build import build_rom
        rom = args.rom.resolve() if args.rom else build_rom(args.sdk,bundle=ROOT/'content/level_bundle.json',
            bafford_district_test=True,bafford_district_test_capture=args.capture)
        metadata = args.log.resolve().parent/'verification-build.json' if args.log else ROOT/'build'/rom.stem/'build.json'
        build = json.loads(metadata.read_bytes())
        require(build['rom_sha256']==hashlib.sha256(rom.read_bytes()).hexdigest(),'ROM differs from recorded build')
        require(build.get('bafford_district_test') and not any(build.get(k) for k in
                ('menu_test','capture','autoplay','stream_test','bafford_stream_test','bafford_expanded_test')),
                'Conflicting or missing district diagnostic')
        entries = [row for row in build['level_catalog']['levels'] if row['id']=='bafford-district']
        require(len(entries)==1,'Missing district mission entry'); entry = entries[0]; content = entry['content']
        require(content['experimental_stream'] and content['bootstrap']['static_model_count']==0 and
                content['bootstrap']['collider_count']==0 and content['mission']['report']['chunk_version']==4 and
                content['mission']['report'].get('lighting') and
                content['mission']['report']['visibility']=='authored-sets','Terrain is not the expected streamed floor/policy/precomputed-color package')
        from tools.compile_mission import prepare_stream_level
        with tempfile.TemporaryDirectory(dir=ROOT/'build',prefix='verify-district-') as temp:
            current = prepare_stream_level(Path(entry['mission_manifest']),Path(temp))
        require(current['source_sha256']==content['source_sha256'],'Source/compiler differs from built district')
        if args.log:
            log = args.log.resolve()
            require(hashlib.sha256((log.parent/rom.name).read_bytes()).hexdigest()==build['rom_sha256'],
                    'Log lacks matching immutable ROM snapshot')
        else:
            from tools.smoke_ares import exercise
            settings = ROOT/'.dev/ares/settings-8mb.bml'
            run = exercise(args.ares,settings if settings.is_file() else args.ares.parent/'settings.bml',rom,
                'bafford-district',COMPLETE,args.timeout,build['rom_sha256'],memory=8)
            report['run']=run; log=ROOT/run['log']
            (log.parent/'verification-build.json').write_text(json.dumps(build,indent=2)+'\n')
        raw=log.read_bytes(); text=raw.decode('utf-8',errors='replace')
        report.update(rom=str(rom),rom_sha256=build['rom_sha256'],log=str(log),log_sha256=hashlib.sha256(raw).hexdigest(),
                      mission_sha256=content['mission']['sha256'],mission_bytes=content['mission']['bytes'],
                      district_cook_sha256=content['source_sha256'],build_signature=build['signature'])
        report.update(decode_log(text,content,capture=build['bafford_district_test_capture']))
        if report['capture_diagnostic']:
            from tools.capture_ares import decode_captures
            from PIL import Image
            report['captures']=[]
            for ident,(w,h,pixels) in decode_captures(text,expected_count=3,expected_dimensions=(320,240)).items():
                path=ROOT/'build'/('bafford-district-'+{1:'interior',2:'courtyard',3:'east-alley'}[ident]+'.png')
                Image.frombytes('RGB',(w,h),pixels).save(path)
                report['captures'].append(dict(id=ident,image=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    except (ValueError,KeyError,OSError) as error:
        report['error']=str(error)
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('passed','error','visited_regions','retirements','audio_continuity_passed') if k in report},indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    import sys
    sys.path.insert(0,str(ROOT))
    raise SystemExit(main())
