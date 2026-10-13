"""Exercise streamed Native Play through the real editor in an owned copy."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import uuid

try:
    from verify_project_editor import EditorClient, capture, tree_hashes
    from verify_stream_replay_parity import scenario, coverage
except ModuleNotFoundError:
    from tools.verify_project_editor import EditorClient, capture, tree_hashes
    from tools.verify_stream_replay_parity import scenario, coverage

ROOT = Path(__file__).resolve().parents[1]


def verify(editor):
    private = ROOT / ".dev/stream-native-verification" / uuid.uuid4().hex
    private.mkdir(parents=True)
    for folder in ("content", "src", "tools"):
        shutil.copytree(ROOT / folder, private / folder, ignore=shutil.ignore_patterns("__pycache__"))
    original = tree_hashes(private / "content")
    queue = private / ".dev/editor/project"
    queue.mkdir(parents=True)
    startup = subprocess.STARTUPINFO() if os.name == "nt" else None
    if startup:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    evidence = dict(root=str(private), passed=False, checks=[])
    with (private / "editor.log").open("w") as log:
        process = subprocess.Popen([str(Path(editor).resolve()), str(private)], cwd=private,
                                   stdout=log, stderr=log, startupinfo=startup)
        client = EditorClient(queue, process)
        try:
            client.wait_session()
            client.request("open_level", {"file": "streaming_probe.json"})
            authored = client.request("inspect")
            start = client.request("start_play", {"paused": True, "start_preset": "west"})
            stream = start["stream"]
            assert stream["published_mask"] == stream["demand_mask"]
            assert stream["slot_count"] == 2 and stream["query_cache_ready"]
            scene = client.request("inspect_scene")
            names = {row["source_id"] for row in scene["entities"]}
            assert "west-floor" in names and "east-floor" not in names and "probe-door" in names
            query = client.request("query_play", {"position": [5, 0, 0]})
            assert not query["standing_clear"]
            assert not client.request("query_play", {"from": [-5, 1.6, 0], "to": [5, 1.6, 0]})["line_of_sight"]
            evidence["checks"].append("Cooked-package startup uses bounded slots, a published collision bank and matching viewport membership")
            # The camera/player is at the exact cooked bootstrap start and
            # fixed ticks provide the same headless and editor schedule.
            state = client.request("step_play", {"ticks": 60, "input": {"forward": 1}})
            replay = client.request("export_replay")
            played = client.request("replay_play")
            assert state["player"] == played["player"] and state["enemies"] == played["enemies"] and state["stream"] == played["stream"]
            runner = ROOT / "editor/bazel-bin/play_runner.exe"
            output = json.loads(subprocess.check_output([str(runner), str(Path(authored["output"]) / "generated/play.json"), replay["path"]], text=True))
            assert output["state"]["player"] == state["player"] and output["state"]["stream"] == state["stream"]
            packet = json.loads((Path(authored["output"]) / "generated/play.json").read_bytes())
            corrupt = bytearray(Path(packet["stream"]["package_path"]).read_bytes());corrupt[-1] ^= 1
            bad_package = private / "corrupt.dlm";bad_package.write_bytes(corrupt)
            packet["stream"]["package_path"] = str(bad_package)
            bad_play = private / "corrupt-play.json";bad_play.write_text(json.dumps(packet))
            rejected = subprocess.run([str(runner), str(bad_play)], capture_output=True, text=True)
            assert rejected.returncode == 1 and "SHA-256 mismatch" in rejected.stderr
            evidence["checks"].append("Streamed editor replay reproduces identical headless gameplay and coordinator state")
            evidence["checks"].append("Host transport rejects a package whose cooked SHA-256 no longer matches")
            recording = json.loads(Path(replay["path"]).read_bytes())
            recording["stream_boundary"] = "invalid"
            Path(replay["path"]).write_text(json.dumps(recording))
            client.request("replay_play", expect_ok=False)
            assert client.request("inspect_play")["tick"] == state["tick"]
            recording["stream_boundary"] = "settled"
            Path(replay["path"]).write_text(json.dumps(recording))
            settled = client.request("replay_play")
            assert settled["stream_boundary"] == "settled" and settled["tick"] == state["tick"]
            exported = client.request("export_replay")
            assert json.loads(Path(exported["path"]).read_bytes())["stream_boundary"] == "settled"
            settled_output = json.loads(subprocess.check_output([str(runner), str(Path(authored["output"]) / "generated/play.json"), exported["path"]], text=True))
            assert settled_output["state"]["player"] == settled["player"] and settled_output["state"]["stream"] == settled["stream"]
            evidence["checks"].append("Editor import/export preserves settled replay boundaries and rejects invalid metadata before changing the session")
            route = scenario(packet, json.loads((ROOT / "tests/fixtures/stream_probe_route.json").read_bytes()))
            Path(replay["path"]).write_text(json.dumps(route))
            routed = client.request("replay_play")
            replay_states = json.loads(subprocess.check_output([str(runner), str(Path(authored["output"]) / "generated/play.json"), replay["path"], "--states"], text=True))
            evidence["route"] = coverage(replay_states["states"], route)
            assert replay_states["state"]["player"] == routed["player"] and replay_states["state"]["enemies"] == routed["enemies"] and replay_states["state"]["stream"] == routed["stream"]
            evidence["checks"].append("The recorded door/loot/retire/reload/restart route reproduces the editor's final gameplay and coordinator state")
            evidence["capture"] = capture(client, private, "stream-native-west")
            client.request("restart_play")
            reset = client.request("inspect_play")
            assert reset["tick"] == 0 and reset["open_objects"] == 0 and reset["collected_objects"] == 0
            assert reset["player"] == start["player"] and reset["stream"] == start["stream"]
            client.request("stop_play")
            after = client.request("inspect")
            assert after["document"] == authored["document"] and not after["dirty"]
            assert tree_hashes(private / "content") == original
            evidence["checks"].append("Restart resets stream/game state; Stop restores authoring without changing sources")
            # Modify both drafts and ensure Play cooks that pair without saving.
            spawn = next(row for row in authored["document"]["entities"] if row["kind"] == "spawn")
            position = list(spawn["transform"]["position"]);position[0] += .25
            client.request("set_entity", {"id": spawn["id"], "patch": {"transform": {"position": position}}})
            client.request("set_streaming", {"patch": {"visibility": "all-regions"}})
            draft = client.request("inspect")
            both = client.request("start_play", {"paused": True})
            assert abs(both["player"][0]-position[0]) < .00001 and both["stream"]["published_mask"] == 3
            assert client.request("query_play", {"position": [5, 0, 0]})["standing_clear"]
            all_names = {row["source_id"] for row in client.request("inspect_scene")["entities"]}
            assert "west-floor" in all_names and "east-floor" in all_names
            client.request("stop_play")
            assert client.request("inspect")["document"] == draft["document"]
            assert tree_hashes(private / "content") == original
            evidence["checks"].append("Native Play cooks unsaved scene and partition drafts together without changing canonical files")
            evidence["checks"].append("Viewport visibility and collision queries reveal a region only after its working bank publishes")
            evidence["passed"] = True
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)
            (private / "result.json").write_text(json.dumps(evidence, indent=2))
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, default=ROOT / "editor/bazel-bin/darklantern64_project_editor.exe")
    args = parser.parse_args()
    print(json.dumps(verify(args.editor), indent=2))
