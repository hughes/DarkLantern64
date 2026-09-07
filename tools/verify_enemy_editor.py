"""Exercise enemy authoring through an isolated, real LightEngine editor.

Requires the scene-editor executable to have been built. Creates a private
content fixture, exercises a three-enemy example, then archives its JSON under
the session queue's evidence/ directory and removes only the source it created.
Cleanup also runs on failure. Existing content, editors, emulator sessions and
controller settings are untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from compile_level import compile_level
from editorctl import ROOT, command_queue


def request(queue, operation, arguments=None, *, timeout=90, expect_ok=True):
    ident = uuid.uuid4().hex
    now = time.time()
    payload = {"id": ident, "op": operation, "args": arguments or {},
               "created_at": now, "expires_at": now + timeout}
    requests = queue / "requests"
    requests.mkdir(parents=True, exist_ok=True)
    stage = requests / (ident + ".tmp")
    stage.write_text(json.dumps(payload), encoding="utf-8")
    stage.replace(requests / (ident + ".json"))
    response = queue / "responses" / (ident + ".json")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if response.exists():
            result = json.loads(response.read_text())
            if result.get("id") != ident or result.get("ok") is not expect_ok:
                raise AssertionError(f"Unexpected {operation} response: {result}")
            return result["result"]
        time.sleep(.1)
    raise TimeoutError(f"{operation}: inspect {response} before retrying")


def _exercise(executable, source, queue):
    data = json.loads(source.read_text(encoding="utf-8"))
    output = ROOT / "build/scenes" / source.stem
    compile_level(source, output)
    queue.mkdir(parents=True, exist_ok=True)
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    evidence = {"source": str(source), "checks": []}
    with (queue / "verification-editor.log").open("w") as log:
        process = subprocess.Popen([str(executable), str(ROOT), str(source)], cwd=ROOT,
                                   stdout=log, stderr=log, startupinfo=startup)
        try:
            initial = request(queue, "inspect")
            assert not initial["dirty"]
            catalog = initial["enemy_types"]
            assert {entry["id"] for entry in catalog} >= {"watchman", "scout"}
            request(queue, "add_enemy", {"id": "workroom-scout", "enemy_type": "scout",
                                         "position": [-7, 0, -4]})
            route = [("scout-west", [-7, 0, -4]), ("scout-north", [-4, 0, -6]),
                     ("scout-east", [-3, 0, -2])]
            for name, position in route:
                request(queue, "add_waypoint", {"id": name, "position": position})
            request(queue, "set_entity", {"id": "workroom-scout", "patch": {
                "behavior": "patrol", "patrol": [name for name, _ in route]}})
            copied = request(queue, "duplicate_enemy", {"id": "workroom-scout",
                             "new_id": "store-sentry", "position": [-6.5, 0, -4]})
            clone = copied["entity"]
            assert not set(clone["patrol"]) & {name for name, _ in route}
            request(queue, "save")
            saved = json.loads(source.read_text())
            entities = {entity["id"]: entity for entity in saved["entities"]}
            for original, copied_id in zip(route, clone["patrol"]):
                expected = list(original[1]); expected[0] += .5
                assert entities[copied_id]["transform"]["position"] == expected
            evidence["checks"].append("Duplicated enemy owns translated, independent patrol waypoints")
            # Turn that independent copy into a stationary lookout in the store.
            request(queue, "set_entity", {"id": "store-sentry", "patch": {
                "enemy_type": "watchman", "behavior": "sentry", "patrol": [],
                "transform": {"position": [7.5, 0, 5], "rotation": [0, 180, 0]},
                "speed": None, "sight_range": None, "hearing_range": None}})
            for copied_id in clone["patrol"]:
                request(queue, "delete_entity", {"id": copied_id})
            request(queue, "delete_entity", {"id": "scout-west"}, expect_ok=False)
            request(queue, "add_enemy", {"id": "workroom-scout"}, expect_ok=False)
            request(queue, "set_entity", {"id": "workroom-scout", "patch": {
                "enemy_type": "missing-type"}}, expect_ok=False)
            request(queue, "save")
            # A cooked enemy must be bound to a real editable preview transform.
            request(queue, "set_preview_transform", {"id": "workroom-scout", "transform": {
                "position": [-7, 0, -3.5]}})
            request(queue, "save")
            final = request(queue, "inspect")
            assert not final["dirty"] and not final["preview_refresh_pending"]
            entities = {entity["id"]: entity for entity in final["document"]["entities"]}
            assert entities["workroom-scout"]["transform"]["position"] == [-7, 0, -3.5]
            report = final["report"]
            assert report["counts"]["enemies"] == 3
            instances = report["enemy_instances"]
            assert len({enemy["enemy_index"] for enemy in instances.values()}) == 3
            assert len({enemy["model_index"] for enemy in instances.values()}) == 3
            assert instances["store-sentry"]["behavior"] == "sentry"
            assert instances["workroom-scout"]["patrol_ids"] == [name for name, _ in route]
            assert instances["workroom-scout"]["speed"] == next(t["speed"] for t in catalog if t["id"] == "scout")
            evidence["checks"] += ["Three independently mapped models cook from authored content",
                "Code-defined Scout defaults and per-instance sentry behavior survive save",
                "Unsafe deletion, duplicate IDs and unknown types reject without corrupting content",
                "New enemy viewport transform round-trips into the saved level"]
            evidence["capture"] = request(queue, "capture")
            evidence["source_sha256"] = report["source_sha256"]
            evidence["source_file_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
            evidence["counts"] = report["counts"]
            request(queue, "quit")
            process.wait(timeout=15)
            evidence["passed"] = True
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            (queue / "enemy-verification.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


def archive_owned_source(source, queue):
    """Archive an exclusively created fixture; never follow a replacement link."""
    source, queue = Path(source).absolute(), Path(queue).absolute()
    content = (ROOT / "content").resolve()
    if source.resolve() != source or source.parent != content or source.suffix != ".json":
        raise ValueError("Refusing to clean a fixture outside the canonical content directory")
    if queue.resolve() != queue or not queue.is_relative_to((ROOT / ".dev/editor").resolve()):
        raise ValueError("Refusing to archive outside the editor evidence directory")
    if not source.exists():
        return None
    raw = source.read_bytes()
    archive = queue / "evidence" / uuid.uuid4().hex / source.name
    archive.parent.mkdir(parents=True, exist_ok=False)
    with archive.open("xb") as stream:
        stream.write(raw)
    if source.resolve() != source or source.read_bytes() != raw:
        raise ValueError("Fixture changed while archiving; retained its source and archived snapshot")
    source.unlink()
    return str(archive)


def verify(executable, source):
    source = Path(source).absolute()
    if source.resolve() != source:
        raise ValueError("Fixture source must not use a symlink or junction")
    queue = command_queue(ROOT, source)
    if source.exists():
        raise ValueError("Choose a new filename; existing content will never be overwritten")
    data = json.loads((ROOT / "content/first_room.json").read_text())
    data["title"] = "Enemy Patrol Workshop"
    data["views"] = [
        {"id": "workroom-scout", "position": [-6, 0, 2.7], "yaw": 180, "pitch": -4},
        {"id": "store-watch", "position": [4.5, 0, -6.5], "yaw": 12, "pitch": -2},
    ]
    owned = False
    evidence = {"passed": False, "source": str(source), "checks": []}
    report = queue / "enemy-verification.json"
    try:
        # Exclusive creation is the ownership boundary. A simultaneous creator
        # wins without this verifier overwriting or later deleting their file.
        with source.open("x", encoding="utf-8") as stream:
            owned = True
            stream.write(json.dumps(data, indent=2) + "\n")
        queue.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        evidence = _exercise(executable, source, queue)
    except BaseException as error:
        if owned and report.exists():
            evidence = json.loads(report.read_text(encoding="utf-8"))
        evidence["error"] = str(error)
        raise
    finally:
        if owned:
            # _exercise closes its owned editor before unwinding here; early
            # compiler/launch failures are archived through the same path.
            evidence["archived_source"] = archive_owned_source(source, queue)
            evidence["content_fixture_removed"] = not source.exists()
            report.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, default=ROOT / "editor/bazel-bin/darklantern64_scene_editor.exe")
    parser.add_argument("--level", type=Path, default=ROOT / "content" / ("enemy_test_" + uuid.uuid4().hex + ".json"))
    args = parser.parse_args()
    verify(args.editor.resolve(), args.level)
