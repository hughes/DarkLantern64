"""Verify panel visibility, persistence and Play in a disposable editor project."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
from verify_project_editor import EditorClient, tree_hashes

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor",type=Path,default=ROOT/"editor/bazel-bin/darklantern64_scene_editor.exe")
    parser.add_argument("--keep-open",action="store_true",help="Leave the owned test window open for visual inspection")
    args=parser.parse_args()
    private=ROOT/".dev/workspace-verification"/uuid.uuid4().hex
    private.mkdir(parents=True)
    for folder in ("content","src","tools"):
        shutil.copytree(ROOT/folder,private/folder,ignore=shutil.ignore_patterns("__pycache__"))
    original=tree_hashes(private/"content")
    queue=private/".dev/editor/project";queue.mkdir(parents=True)
    report={"root":str(private),"passed":False,"checks":[]}
    process=None
    with (private/"editor.log").open("w") as log:
        def launch():
            startup=subprocess.STARTUPINFO() if os.name=="nt" else None
            if startup:startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=subprocess.SW_HIDE
            p=subprocess.Popen([str(args.editor.resolve()),str(private)],cwd=private,stdout=log,stderr=log,startupinfo=startup)
            client=EditorClient(queue,p);client.wait_session();return p,client
        def active(layout):return {w["name"] for w in layout["windows"] if w["active"]}
        try:
            process,client=launch()
            layout=client.request("get_layout")
            assert active(layout)=={"Objects","Inspector","Viewport"},active(layout)
            viewport=next(w for w in layout["windows"] if w["name"]=="Viewport")
            assert viewport["size"][1]>800,viewport
            report["default_layout"]=layout
            report["checks"].append("Three-panel default with full-height viewport and no duplicate engine panels")
            for name in layout["panels"]:
                client.request("set_panel",{"name":name,"visible":True})
                opened=client.request("get_layout")
                if name!="Renderer resources":assert name in active(opened),(name,active(opened))
                client.request("set_panel",{"name":name,"visible":False})
                closed=client.request("get_layout")
                assert name not in active(closed),name
            client.request("set_panel",{"name":"missing"},expect_ok=False)
            client.request("reset_layout");client.request("inspect")
            assert active(client.request("get_layout"))=={"Objects","Inspector","Viewport"}
            report["checks"].append("Every secondary tool can be opened/closed; default restoration recovers core panels")
            client.request("select_entities",{"ids":["rotated-crate"]})
            scene=client.request("inspect_scene")
            selected=next(e for e in scene["entities"] if e["source_id"]=="rotated-crate")
            assert client.request("get_layout")["viewport_selection"]==selected["entity_id"]
            # Gizmos write preview transforms; the normal synchronization path
            # must commit that same object's transform to the canonical inspector.
            initial=client.request("inspect")["document"]
            entity=next(e for e in initial["entities"] if e["id"]=="rotated-crate")
            position=list(entity["transform"]["position"]);position[0]+=.125
            client.request("set_preview_transform",{"id":"rotated-crate","transform":{"position":position}})
            edited=client.request("inspect")["document"]
            actual=next(e for e in edited["entities"] if e["id"]=="rotated-crate")["transform"]["position"]
            assert all(abs(a-b)<1e-6 for a,b in zip(actual,position)),(actual,position)
            client.request("undo")
            assert client.request("inspect")["document"]==initial
            report["checks"].append("Object selection reaches engine bounding-box/gizmo selection; preview movement synchronizes and undoes canonically")
            client.request("start_play",{"paused":True})
            play=client.request("get_layout")
            assert "Objects" not in active(play) and "Inspector" not in active(play)
            client.request("set_panel",{"name":"Play Inspector","visible":True})
            assert "Play Inspector" in active(client.request("get_layout"))
            assert client.request("step_play",{"ticks":1})["tick"]==1
            client.request("stop_play");client.request("reset_layout");client.request("inspect")
            assert active(client.request("get_layout"))=={"Objects","Inspector","Viewport"}
            report["checks"].append("Play preserves panel preferences, offers optional inspection and restores editing on stop")
            client.request("set_panel",{"name":"Assets","visible":True})
            client.request("quit");process.wait(timeout=20)
            process,client=launch()
            assert client.request("get_layout")["panels"]["Assets"] is True
            client.request("reset_layout");client.request("inspect")
            assert tree_hashes(private/"content")==original
            report["checks"].append("Visibility persists across editor restart; content stays unchanged")
            report.update(passed=True,pid=process.pid,queue=str(queue))
        finally:
            if process and process.poll() is None and not (args.keep_open and report["passed"]):
                process.terminate();process.wait(timeout=15)
            (private/"result.json").write_text(json.dumps(report,indent=2))
            (ROOT/".dev/workspace-verification/latest.json").write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=="__main__":main()
