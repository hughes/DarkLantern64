"""Verify native Play in a private copied project through the real command API."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import uuid
from verify_project_editor import EditorClient, capture, tree_hashes

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor",type=Path,default=ROOT/"editor/bazel-bin/darklantern64_project_editor.exe")
    args=parser.parse_args()
    private=ROOT/".dev/native-verification"/uuid.uuid4().hex
    private.mkdir(parents=True)
    for folder in ("content","src","tools"):
        shutil.copytree(ROOT/folder,private/folder,ignore=shutil.ignore_patterns("__pycache__"))
    original=tree_hashes(private/"content")
    queue=private/".dev/editor/project"
    queue.mkdir(parents=True)
    startup=subprocess.STARTUPINFO() if os.name=="nt" else None
    if startup:
        startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow=subprocess.SW_HIDE
    evidence={"root":str(private),"passed":False,"checks":[]}
    with (private/"editor.log").open("w") as log:
        process=subprocess.Popen([str(args.editor.resolve()),str(private)],
                                 cwd=private,stdout=log,stderr=log,startupinfo=startup)
        client=EditorClient(queue,process)
        try:
            client.wait_session()
            initial=client.request("inspect")
            assert initial["play"]["systems"]==["DarkLantern shared simulation"]
            started=client.request("start_play",{"paused":True})
            assert started["mode"]=="paused" and started["tick"]==0
            state=client.request("step_play",{"ticks":60,"input":{"forward":1}})
            assert state["tick"]==60 and state["player"]!=started["player"]
            assert client.request("inspect_play")["tick"]==60
            client.request("set_entity",{"id":"missing","patch":{}},expect_ok=False)
            evidence["checks"].append("Shared simulation moves with exact ticks; paused inspection is stable; authoring is protected")
            replay=client.request("export_replay")
            replayed=client.request("replay_play")
            assert replayed["player"]==state["player"] and replayed["enemies"]==state["enemies"]
            runner=ROOT/"editor/bazel-bin/play_runner.exe"
            output=subprocess.check_output([str(runner),str(private/"build/generated/play.json"),replay["path"]],text=True)
            headless=json.loads(output)["state"]
            assert headless["player"]==state["player"] and headless["enemies"]==state["enemies"]
            evidence["checks"].append("Recorded commands reproduce identical editor and headless gameplay state")
            evidence["capture"]=capture(client,private,"native-play")
            client.request("stop_play")
            after=client.request("inspect")
            assert after["document"]==initial["document"] and not after["dirty"]
            assert tree_hashes(private/"content")==original
            # Unsaved edits are included in Play without being written to disk.
            spawn=next(e for e in initial["document"]["entities"] if e["kind"]=="spawn")
            position=list(spawn["transform"]["position"]);position[1]+=.2
            client.request("set_entity",{"id":spawn["id"],"patch":{"transform":{"position":position}}})
            dirty=client.request("inspect")
            assert dirty["dirty"]
            started=client.request("start_play",{"paused":True})
            assert abs(started["player"][1]-position[1])<1e-5
            client.request("stop_play")
            assert client.request("inspect")["document"]==dirty["document"]
            assert tree_hashes(private/"content")==original
            evidence["checks"].append("Play snapshots include unsaved edits; stopping preserves document and source files")
            client.request("undo")
            assert client.request("inspect")["document"]==initial["document"]
            box=client.request("add_blockout",{"id":"test-block","size":[1,1,1],"position":[0,4,0]})["created"][0]
            before=client.request("inspect")["document"]
            client.request("transform_selection",{"ids":[box],"translation":[.13,0,.13],"snap":.25,"yaw":90,"scale":.5})
            transformed=client.request("inspect")["document"]
            client.request("undo");assert client.request("inspect")["document"]==before
            client.request("redo");assert client.request("inspect")["document"]==transformed
            client.request("drop_selection",{"ids":[box]})
            dropped=next(e for e in client.request("inspect")["document"]["entities"] if e["id"]==box)
            assert dropped["transform"]["position"][1]<4
            door=client.request("add_game_object",{"id":"test-door","kind":"door","position":[10,1,10],"size":[1,2,.2]})["created"][0]
            control=client.request("add_game_object",{"id":"test-control","kind":"control","position":[10,1,9],"targets":[door]})["created"][0]
            client.request("group_entities",{"ids":[door,control],"name":"test-pair"})
            client.request("save_prefab",{"ids":[door,control],"name":"test-pair"})
            placed=client.request("place_prefab",{"name":"test-pair","position":[12,1,10]})["created"]
            doc=client.request("inspect")["document"]
            rows=[e for e in doc["entities"] if e["id"] in placed]
            copied_door=next(e["id"] for e in rows if e["kind"]=="door")
            assert next(e for e in rows if e["kind"]=="control")["targets"]==[copied_door]
            client.request("delete_entities",{"ids":[copied_door]},expect_ok=False)
            client.request("delete_entities",{"ids":placed})
            client.request("refresh_preview")
            play=client.request("start_play",{"paused":True})
            assert any(o["id"]==door for o in play["objects"])
            client.request("step_play",{"ticks":1,"expected_tick":4},expect_ok=False)
            assert client.request("inspect_play")["tick"]==0
            client.request("inspect_scene");client.request("query_play",{"kind":"visibility","position":[0,1,0]})
            client.request("stop_play")
            assert tree_hashes(private/"content")==original
            evidence["checks"].append("Blockout cook, pivot transform, snap/drop, undo/redo, groups, prefab link remapping, incoming-link protection and stale-tick rejection")
            evidence["passed"]=True
        finally:
            # This process belongs to this verifier and its source is a private copy.
            if process.poll() is None:process.terminate()
            process.wait(timeout=15)
            (private/"result.json").write_text(json.dumps(evidence,indent=2))
    print(json.dumps(evidence,indent=2))
if __name__=="__main__":main()
