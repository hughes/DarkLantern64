"""Measure level switching and verify responsive, camera-preserving draft refresh."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
from verify_project_editor import EditorClient, tree_hashes

ROOT = Path(__file__).resolve().parents[1]


def main():
    private = ROOT / ".dev/iteration-verification" / uuid.uuid4().hex
    private.mkdir(parents=True)
    for folder in ("content", "tools", "src"):
        shutil.copytree(ROOT/folder, private/folder, ignore=shutil.ignore_patterns("__pycache__"))
    # Two single-sided meshes, clear of the real room, exercise the engine's
    # actual asset arenas and world transforms rather than only math helpers.
    (private/"content/models/pick-triangle.obj").write_text("v -1 -1 0\nv 1 -1 0\nv 0 1 0\nf 1 2 3\n")
    level_path = private/"content/first_room.json"
    level = json.loads(level_path.read_bytes())
    level["assets"].append({"id":"pick-triangle", "uri":"models/pick-triangle.obj"})
    for ident, z, yaw in (("pick-back-wall",2,0),("pick-target",4,180)):
        level["entities"].append({"id":ident,"kind":"static","model":"pick-triangle","material":"mat-wood",
            "transform":{"position":[30,2,z],"rotation":[0,yaw,0],"scale":[1,1,1]}})
    level_path.write_text(json.dumps(level), encoding="utf-8")
    before = tree_hashes(private/"content")
    result = {"passed": False, "root": str(private), "switches": []}
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    with (private/"editor.log").open("w") as log:
        process = subprocess.Popen([str(ROOT/"editor/bazel-bin/darklantern64_scene_editor.exe"), str(private)],
            cwd=private, stdout=log, stderr=log, startupinfo=startup)
        client = EditorClient(private/".dev/editor/project", process)
        def wait_preview():
            deadline = time.monotonic()+60
            while time.monotonic()<deadline:
                state = client.request("inspect")
                if not state["busy"] and not state["preview_refresh_pending"]:
                    return state
                time.sleep(.05)
            raise AssertionError("Preview never finished")
        def camera():
            return next(e for e in client.request("inspect_scene")["entities"] if e["name"]=="Editor camera")
        try:
            client.wait_session()
            with ThreadPoolExecutor(max_workers=1) as worker:
                for file in ("moonlit_courtyard.json", "first_room.json", "moonlit_courtyard.json", "first_room.json"):
                    start = time.perf_counter()
                    pending = worker.submit(client.request, "open_level", {"file": file})
                    observations = []
                    while not pending.done():
                        probe = time.perf_counter()
                        state = client.request("inspect")
                        observations.append({"frame": state["frame"], "busy": state["busy"], "ms": (time.perf_counter()-probe)*1000})
                    opened = pending.result()
                    result["switches"].append({"file": file, "wall_ms": (time.perf_counter()-start)*1000,
                        "preparation": opened["preparation"], "observations": observations})
            assert all(row["preparation"]["cache_hit"] for row in result["switches"][-2:])
            busy = [o for row in result["switches"] for o in row["observations"] if o["busy"]]
            assert len({o["frame"] for o in busy}) >= 2, "No rendering progress while cooking"
            assert max(o["ms"] for o in busy)<1000, busy
            assert client.request("pick_editor", {"from":[30,2,0],"to":[30,2,10]})["source_id"]=="pick-target"
            assert client.request("pick_editor", {"from":[30,2,3],"to":[30,2,0]})["source_id"]=="pick-back-wall"
            assert client.request("pick_editor", {"from":[30.9,2.9,0],"to":[30.9,2.9,10]})["entity_id"] is None
            result["front_surface_picking"] = True
            client.request("focus_character", {"id": "store-guard"})
            focused = camera()
            for kind in ("loot", "static"):
                start = time.perf_counter()
                client.request("set_game_object", {"id":"rotated-crate", "patch":{"kind":kind}})
                state = wait_preview()
                current = camera()
                assert current["position"]==focused["position"] and current["rotation_quaternion"]==focused["rotation_quaternion"]
                result[kind+"_refresh_ms"] = (time.perf_counter()-start)*1000
                assert next(e for e in state["document"]["entities"] if e["id"]=="rotated-crate")["kind"]==kind
            client.request("add_blockout", {"id":"camera-check", "position":[0,4,0]})
            wait_preview()
            current = camera()
            assert current["position"]==focused["position"] and current["rotation_quaternion"]==focused["rotation_quaternion"]
            client.request("undo")
            assert camera()["position"]==focused["position"]
            assert tree_hashes(private/"content")==before
            result.update(passed=True, camera_preserved=True, source_unchanged=True)
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=20)
            (private/"result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
