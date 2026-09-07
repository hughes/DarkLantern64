"""Exercise the stacked Sponza level in a private copied project editor.

No ROM builds or user-app mutations. Only the fixture's default camera is
framed along the reference courtyard axis for the architectural capture.
"""
from __future__ import annotations
import argparse
import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback
import uuid

from verify_project_editor import EditorClient,capture,hash_file,require,write_json
from verify_character_editor import compare

ROOT=Path(__file__).resolve().parents[1]

def verify(editor,root=ROOT):
    root=Path(root).resolve();editor=Path(editor).resolve()
    require(editor.is_file(),'Build the private scene editor executable first')
    private=root/'.dev/editor/sponza-verification'/uuid.uuid4().hex
    private.mkdir(parents=True)
    for name in ('content','src','tools'):
        shutil.copytree(root/name,private/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    source=private/'content/sponza_courtyard.json'
    canonical_hash=hash_file(root/'content/sponza_courtyard.json')
    compiler=private/'tools/compile_level.py'
    old='"position":[0,19,22],"rotation":quaternion([-42,0,0])'
    new=f'"position":[10.5,1.75,0],"rotation":quaternion([{math.degrees(math.atan2(4.55,12))},90,0])'
    text=compiler.read_text();require(text.count(old)==1,'Default camera recipe changed; update the private framing seam')
    compiler.write_text(text.replace(old,new))
    queue=private/'.dev/editor/project';write_json(queue/'project-state.json',{'last_level':'sponza_courtyard.json'})
    report={'passed':False,'private_root':str(private),'editor':str(editor),'editor_sha256':hash_file(editor),
        'fixture_level_sha256':hash_file(source),'checks':[],'captures':[],
        'camera_fixture_override':'Private compiler default camera framed along courtyard axis; no canonical source/editor-code camera change'}
    process=None;started=time.monotonic()
    startup=subprocess.STARTUPINFO() if os.name=='nt' else None
    if startup is not None:startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=subprocess.SW_HIDE
    def passed(label):report['checks'].append(label);print('PASS '+label,flush=True)
    def row(state,ident):return next(e for e in state['document']['entities'] if e['id']==ident)
    with (private/'sponza-editor.log').open('w') as log:
        try:
            process=subprocess.Popen([str(editor),str(private)],cwd=private,stdout=log,stderr=log,startupinfo=startup)
            report['owned_editor_pid']=process.pid;client=EditorClient(queue,process)
            session=client.wait_session();require(Path(session['root']).resolve()==private,'Editor targeted a different project')
            state=client.request('inspect');require(Path(state['source']).name=='sponza_courtyard.json','Wrong selected level')
            require(not state['dirty'],'Startup dirtied the level')
            report['counts']=state['report']['counts'];report['catalog']=client.request('list_levels')
            require('sponza_courtyard.json' in json.dumps(report['catalog']),'Sponza is absent from editor registration')
            require(row(state,'sponza-gallery-north-0')['transform']['position'][1]==5.3,'Upper floor did not load')
            require(row(state,'sponza-ground-floor-2')['transform']['position'][1]==0,'Ground floor did not load')
            require(len(state['animation']['instances'])==2 and not state['animation']['diagnostics'],'Both shared guards must resolve')
            report['captures'].append(capture(client,private,'courtyard-axis'))
            passed('Registered Sponza opens both stacked floors and two shared character resources')

            proxy='sponza-collision-east-step-0-0'
            before=copy.deepcopy(row(state,proxy));require('model' not in before and 'collider' in before,'Expected collision-only step proxy')
            moved=before['transform']['position'].copy();moved[0]+=.025
            client.request('set_preview_transform',{'id':proxy,'transform':{'position':moved}})
            changed=client.request('inspect')
            require(changed['dirty'],'Gizmo transform did not dirty the collision proxy')
            deadline=time.monotonic()+12;selected=False
            while time.monotonic()<deadline:
                client.alive();state_file=queue/'state.json'
                if state_file.is_file() and json.loads(state_file.read_bytes()).get('selected')==proxy:
                    selected=True;break
                time.sleep(.1)
            require(selected,'Gizmo transform did not select the collision proxy')
            require(abs(row(changed,proxy)['transform']['position'][0]-moved[0])<1e-5,'Proxy gizmo did not round trip')
            client.request('save');saved=client.request('inspect')
            require(not saved['dirty'] and 'model' not in row(saved,proxy),'Saving introduced proxy render geometry')
            require(saved['report']['counts']==state['report']['counts'],'Moving one proxy changed geometry counts')
            client.request('open_level',{'file':'first_room.json'})
            client.request('open_level',{'file':'sponza_courtyard.json'})
            reopened=client.request('inspect')
            require(abs(row(reopened,proxy)['transform']['position'][0]-moved[0])<1e-5 and not reopened['dirty'],'Proxy edit failed save/reopen')
            client.request('set_entity',{'id':proxy,'patch':{'transform':before['transform']}});client.request('save')
            passed('Collision-only step proxy selects, moves through gizmo sync, cooks and survives reopen without render geometry')

            stable_bytes=source.read_bytes()
            for guard,label in (('ground-guard','ground-guard'),('upper-guard','upper-guard')):
                client.request('focus_character',{'id':guard})
                client.request('set_animation',{'id':guard,'clip':'walk','time':.24,'playing':False})
                report['captures'].append(capture(client,private,label))
            first=client.request('get_animation');upper=next(i for i in first['instances'] if i['id']=='upper-guard')
            second=client.request('set_animation',{'id':'upper-guard','head_yaw':40,'head_pitch':-10,'playing':False})
            changed=next(i for i in second['instances'] if i['id']=='upper-guard')
            require(upper['skin_matrices']!=changed['skin_matrices'],'Upper-floor guard attention remained unchanged')
            report['captures'].append(capture(client,private,'upper-attention'))
            report['attention_difference']=compare(report['captures'][-2],report['captures'][-1],80)
            require(not client.request('inspect')['dirty'] and source.read_bytes()==stable_bytes,'Preview altered authored gameplay')
            passed('Both floor guards display the shared walking asset; upper attention changes visibly without changing the saved level')
            client.request('open_level',{'file':'animation_workshop.json'})
            client.request('open_level',{'file':'sponza_courtyard.json'})
            require(not client.request('inspect')['dirty'],'Registered level switching left unsaved edits')
            passed('Registered switching returns to a clean Sponza level')
            client.request('quit');process.wait(timeout=15);require(process.returncode==0,'Owned editor did not close cleanly')
            report['passed']=True
        except BaseException as error:
            report.update(error=str(error),traceback=traceback.format_exc());raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=15)
            report['elapsed_seconds']=round(time.monotonic()-started,2)
            report['canonical_source_unchanged']=hash_file(root/'content/sponza_courtyard.json')==canonical_hash
            write_json(private/'verification.json',report);write_json(root/'build/sponza-editor-verification.json',report)
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--editor',type=Path,default=ROOT/'editor/bazel-bin/darklantern64_scene_editor.exe')
    args=parser.parse_args();verify(args.editor,args.root)
