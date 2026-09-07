"""Measure the ordinary animated workshop ROM in an isolated Ares session.

Uses the existing patrol/sentry scene, normal audio, no capture freezing, and
six complete profiling windows. This is not a maximum crowd/hardware benchmark.
"""
from pathlib import Path
import hashlib
import json
import math
import os
import re

from build import ROOT, build_rom
from profile_report import make_report
from smoke_ares import exercise


def fields(line):
    return dict(part.split('=',1) for part in line.split()[2:] if '=' in part)


def main():
    source=ROOT/'content/animation_workshop.json';original=source.read_bytes()
    destination=ROOT/'build/scenes/animation_workshop/runtime-verification.json'
    report={'passed':False,'source_sha256':hashlib.sha256(original).hexdigest()}
    try:
        sdk=Path(os.environ.get('N64_INST','C:/n64-toolchain'))
        rom=build_rom(sdk,level=source)
        executable=Path(os.environ.get('ARES_EXE',str(Path(os.environ['LOCALAPPDATA'])/'ares/ares.exe')))
        settings=ROOT/'.dev/ares/settings-8mb.bml'
        if not settings.is_file(): settings=executable.parent/'settings.bml'
        run=exercise(executable,settings,rom,'guard-animation','DL64 profile_end window=6',120,
                     hashlib.sha256(rom.read_bytes()).hexdigest())
        report['run']=run
        log=ROOT/run['log'];raw=log.read_bytes();text=raw.decode('utf-8',errors='replace')
        profile=make_report(raw,log)
        rows=[fields(line) for line in text.splitlines() if line.startswith('DL64 animation_profile ')]
        ready=[fields(line) for line in text.splitlines() if line.startswith('DL64 animation_ready ')]
        assert len(ready)==1 and int(ready[0]['models'])==2,'Expected two animated guards'
        assert len(rows)>=4 and all(int(row['poses'])>0 for row in rows),'Missing live pose samples'
        ticks_per_second={int(row['ticks_per_second']) for row in rows}
        assert len(ticks_per_second)==1 and min(ticks_per_second)>0,'Inconsistent clock'
        rate=next(iter(ticks_per_second));frames=sum(int(row['frames']) for row in rows)
        poses=sum(int(row['poses']) for row in rows)
        sample=sum(int(row['sample_ticks']) for row in rows)
        skin=sum(int(row['skin_ticks']) for row in rows)
        assert frames>0 and sample>0 and skin>0
        report['animation']={'frames':frames,'poses':poses,'sample_ms_per_frame':sample/rate*1000/frames,
            'sample_ms_per_pose':sample/rate*1000/poses,'deform_normal_instance_ms_per_frame':skin/rate*1000/frames,
            'deform_normal_instance_ms_per_pose':skin/rate*1000/poses,
            'max_individual_pose_sample_ms':max(int(r['per_pose_sample_max_ticks']) for r in rows)/rate*1000,
            'max_individual_pose_deform_ms':max(int(r['per_pose_skin_max_ticks']) for r in rows)/rate*1000,
            'scope':'CPU elapsed ticks excluding audio interrupts, nested within transforms; deformation includes normals and instance transforms.'}
        report['runtime_allocations']=ready[0]
        actors={}
        for match in re.finditer(r'DL64 enemy id=([\w-]+) index=(\d+) position=([-\d.,]+) state=(\w+) patrol_index=(\d+)',text):
            name,index,position,state,patrol=match.groups()
            actors.setdefault(name,[]).append({'index':int(index),'position':[float(v) for v in position.split(',')],
                                              'state':state,'patrol_index':int(patrol)})
        assert set(actors)=={'animated-patrol','animated-sentry'},'Unexpected actor telemetry'
        for name,samples in actors.items():
            assert len(samples)>=4,'Not enough actor samples'
            distance=max(math.dist(samples[0]['position'],s['position']) for s in samples)
            if name=='animated-patrol':
                assert distance>.5 and len({s['patrol_index'] for s in samples})>=2,'Patrol did not traverse its route'
            else: assert distance<.02,'Undisturbed sentry moved'
        assert profile['audio_calls']>0,'Normal audio callback did not execute'
        assert not any(s in text for s in ('caught=1','complete=1','RSP CRASH |')),'Unexpected terminal/error state'
        assert source.read_bytes()==original,'Canonical source changed during verification'
        report.update(passed=True,profile=profile,actor_samples=actors,checks=[
            'Ordinary 8 MiB ROM runs two independently animated guards with audio',
            'Patrol advances through its route while sentry holds its post',
            'Pose and deformation timing records have valid clocks and nonzero work',
            'Bounded pose scratch, shared matrices and actual normal cache bytes reported',
            'Canonical level unchanged; owned Ares process closed by harness'],
            limitations='Short stationary emulator workload; original N64/M64, crowded chases and worst-case audio remain unmeasured.')
    except BaseException as error:
        report['error']=str(error)
        raise
    finally:
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('passed','animation','runtime_allocations','checks')},indent=2))


if __name__=='__main__': main()
