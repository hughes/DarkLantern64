"""Verify the project editor in a private copied project, using its command API.

Build the project editor first; --editor optionally selects another executable. This
developer check is intentionally not a creator-facing VS Code task. It copies
only content/, src/ and tools/ into .dev/editor/project-verification/<uuid>,
retains evidence there, and closes only the editor process it starts. No real
levels, user editor sessions, or emulator/controller settings are modified.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def hash_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_hashes(root):
    return {path.relative_to(root).as_posix(): hash_file(path)
            for path in sorted(root.rglob("*")) if path.is_file()}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class EditorClient:
    def __init__(self, queue, process):
        self.queue, self.process = queue, process

    def alive(self):
        code = self.process.poll()
        require(code is None, f"Owned editor exited early with code {code}; inspect project-editor.log")

    def wait_session(self, timeout=90):
        deadline = time.monotonic() + timeout
        path = self.queue / "session.json"
        while time.monotonic() < deadline:
            self.alive()
            if path.exists():
                try:
                    session = json.loads(path.read_bytes())
                    if session.get("pid") == self.process.pid:
                        require(session.get("project_editor") is True, "Executable did not start a project editor session")
                        return session
                except (OSError, ValueError):
                    pass
            time.sleep(.1)
        raise TimeoutError(f"Editor did not publish {path}")

    def request(self, operation, arguments=None, *, timeout=90, expect_ok=True):
        ident = uuid.uuid4().hex
        now = time.time()
        payload = {"id": ident, "op": operation, "args": arguments or {},
                   "created_at": now, "expires_at": now + timeout}
        requests = self.queue / "requests"
        requests.mkdir(parents=True, exist_ok=True)
        stage = requests / (ident + ".tmp")
        stage.write_text(json.dumps(payload), encoding="utf-8")
        stage.replace(requests / (ident + ".json"))
        response = self.queue / "responses" / (ident + ".json")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if response.exists():
                result = json.loads(response.read_bytes())
                require(result.get("id") == ident and result.get("ok") is expect_ok,
                        f"Unexpected {operation} response: {result}")
                return result["result"]
            self.alive()
            time.sleep(.1)
        raise TimeoutError(f"{operation}: inspect {response} before retrying")


def capture(client, private_root, label):
    result = client.request("capture")
    path = Path(result["path"]).resolve()
    require(path.is_relative_to(private_root), "Capture escaped the private project")
    with Image.open(path) as picture:
        width, height = picture.size
        require(width >= 64 and height >= 64, "Viewport capture is too small")
        extrema = picture.convert("RGB").getextrema()
        require(max(high - low for low, high in extrema) > 16, "Viewport capture is blank")
    return {"label": label, "path": path.as_posix(), "sha256": hash_file(path), "size": [width, height]}


def different_captures(one, two):
    with Image.open(one["path"]) as first, Image.open(two["path"]) as second:
        first, second = first.convert("RGB"), second.convert("RGB")
        require(first.size == second.size, "Viewport dimensions changed unexpectedly")
        difference = ImageChops.difference(first, second)
        changed = sum(1 for pixel in difference.getdata() if max(pixel) > 8)
        require(changed > 1000, "Different levels did not visibly change the rendered scene")
        return {"first": one["label"], "second": two["label"], "changed_pixels_over_8": changed,
                "bounds": difference.getbbox()}


def verify(executable, root=ROOT):
    root, executable = Path(root).resolve(), Path(executable).resolve()
    require(executable.is_file(), f"Editor executable does not exist: {executable}")
    content = root / "content"
    real_before = tree_hashes(content)
    parent = root / ".dev/editor/project-verification"
    require(parent.resolve() == parent, "Verification directory cannot be a symlink or junction")
    private = parent / uuid.uuid4().hex
    private.mkdir(parents=True)
    require(private.resolve().parent == parent.resolve(), "Private project escaped the verification directory")
    for folder in ("content", "src", "tools"):
        shutil.copytree(root / folder, private / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    private_content = private / "content"
    queue = private / ".dev/editor/project"
    copied_before = tree_hashes(private_content)
    report = {"passed": False, "private_root": private.as_posix(), "editor": executable.as_posix(),
              "editor_sha256": hash_file(executable), "checks": [], "captures": [], "owned_editor_pids": [],
              "game_launch": "Not exercised here; use the separate final Ares validation.",
              "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    process = None
    started = time.monotonic()

    def passed(label):
        report["checks"].append(label)
        print("PASS " + label, flush=True)

    def identity(client, filename):
        state = client.request("inspect")
        require(Path(state["source"]).resolve() == private_content / filename,
                f"Active canonical source differs from {filename}")
        require(state["scene_path"] == f"levels/{Path(filename).stem}.json",
                f"Scene descriptor does not identify {filename}")
        require(not state["pending_action"], "An API action unexpectedly opened a pending dialog")
        return state

    def open_level(client, filename):
        client.request("open_level", {"file": filename})
        return identity(client, filename)

    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    with (private / "project-editor.log").open("w", encoding="utf-8") as log:
        def launch():
            nonlocal process
            process = subprocess.Popen([str(executable), str(private)], cwd=private,
                                       stdout=log, stderr=log, startupinfo=startup)
            report["owned_editor_pids"].append(process.pid)
            client = EditorClient(queue, process)
            session = client.wait_session()
            require(Path(session["root"]).resolve() == private, "Editor session used the real project root")
            return client

        try:
            client = launch()
            initial = client.request("inspect")
            catalog = client.request("list_levels")
            levels = {entry["file"]: entry for entry in catalog["levels"]}
            require(set(levels) == {"first_room.json", "moonlit_courtyard.json", "enemy_patrols.json"},
                    "Expected the three initial project levels")
            require(not initial["dirty"], "Fresh editor unexpectedly starts dirty")
            for filename, entry in levels.items():
                saved = json.loads((private_content / filename).read_bytes())
                require(entry["title"] == saved["title"], "Catalog title differs from its canonical level")
                require(entry["in_bundle"] is True, "Initial shipped level is absent from the game menu")
            passed("One project session discovers all three canonical levels with their real titles")

            for filename in ("first_room.json", "moonlit_courtyard.json", "enemy_patrols.json"):
                state = open_level(client, filename)
                require(state["document"]["title"] == levels[filename]["title"], "Opened document title differs from catalog")
                require(state["document"] == json.loads((private_content / filename).read_bytes()),
                        "Opening a level changed or loaded the wrong canonical document")
                report["captures"].append(capture(client, private, filename))
            report["capture_difference"] = different_captures(report["captures"][0], report["captures"][1])
            passed("Switching loads distinct namespaced scenes and visibly different level geometry")

            client.request("create_level", {"name": "editor_created", "title": "Editor Starter Verification", "in_bundle": True})
            state = identity(client, "editor_created.json")
            created = private_content / "editor_created.json"
            require(created.is_file() and state["document"]["title"] == "Editor Starter Verification", "Create did not save and open the starter")
            starter_entities = state["document"]["entities"]
            require(all(any(entity["kind"] == kind for entity in starter_entities) for kind in ("spawn", "door", "control", "objective", "light")),
                    "Starter lacks required playable objects")
            require(len(client.request("list_levels")["levels"]) == 4, "New level was not registered")
            passed("Create saves, registers and opens a playable starter using shared project models")

            added_enemy = client.request("add_enemy", {"id": "starter-enemy", "position": [2, 0, 2]})["entity"]
            require(added_enemy["model"] == "mesh-guard" and added_enemy["material"] == "mat-guard",
                    "First enemy did not use the starter's reusable fallback definitions")
            client.request("save")
            enemy_state = identity(client, created.name)
            require(enemy_state["report"]["counts"]["enemies"] == 1, "Starter enemy did not cook into the runtime")
            header = Path(enemy_state["output"]) / "generated/demo_level.h"
            require('"starter-enemy"' in header.read_text(), "Generated runtime data omits the starter enemy")
            client.request("delete_entity", {"id": "starter-enemy"})
            client.request("save")
            require(identity(client, created.name)["report"]["counts"]["enemies"] == 0,
                    "Removing the test enemy did not restore an empty starter")
            passed("A new starter supports Add enemy, runtime export, and removal without an existing enemy template")

            objective = next(entity for entity in starter_entities if entity["kind"] == "objective")
            position = list(objective["transform"]["position"])
            position[0] += .4
            client.request("set_entity", {"id": objective["id"], "patch": {"transform": {"position": position}}})
            dirty = identity(client, "editor_created.json")
            require(dirty["dirty"], "Object edit did not dirty the active document")
            rejected = client.request("open_level", {"file": "first_room.json"}, expect_ok=False)
            require("unsaved" in str(rejected).lower(), "Dirty switch did not explain the unsaved edits")
            retained = identity(client, "editor_created.json")
            require(retained["dirty"] and retained["document"] == dirty["document"], "Rejected switch discarded or changed the draft")
            client.request("save")
            state = identity(client, "editor_created.json")
            require(not state["dirty"], "Save left the current document dirty")
            saved_objective = next(entity for entity in json.loads(created.read_bytes())["entities"] if entity["id"] == objective["id"])
            require(saved_objective["transform"]["position"] == position, "Save did not publish edits to the current source")
            passed("Dirty switching is rejected without data loss; Save writes edits to the active level")

            created_bytes = created.read_bytes()
            client.request("duplicate_level", {"file": created.name, "name": "editor_copy", "title": "Editor Copy Verification", "in_bundle": False})
            state = identity(client, "editor_copy.json")
            copied = private_content / "editor_copy.json"
            expected = json.loads(created_bytes)
            expected["title"] = "Editor Copy Verification"
            require(json.loads(copied.read_bytes()) == expected, "Duplicate did not preserve all saved source data")
            require(created.read_bytes() == created_bytes, "Duplicate modified its original")
            copied_entry = next(entry for entry in client.request("list_levels")["levels"] if entry["file"] == copied.name)
            require(not copied_entry["in_bundle"], "Unbundled duplicate unexpectedly ships in the menu")
            passed("Duplicate preserves saved authored content and keeps the original unchanged")

            position[2] += .2
            client.request("set_entity", {"id": objective["id"], "patch": {"transform": {"position": position}}})
            client.request("update_level", {"file": copied.name, "title": "Renamed Live Copy", "in_bundle": True})
            state = identity(client, copied.name)
            require(state["document"]["title"] == "Renamed Live Copy", "Active title did not update live")
            require(state["dirty"], "Metadata update silently discarded dirty object changes")
            current_objective = next(entity for entity in state["document"]["entities"] if entity["id"] == objective["id"])
            require(current_objective["transform"]["position"] == position, "Metadata update lost dirty transforms")
            client.request("save")
            require(json.loads(copied.read_bytes())["title"] == "Renamed Live Copy", "Saving restored the stale pre-rename title")
            require(created.read_bytes() == created_bytes, "Editing the duplicate modified its original")
            passed("Live title and menu updates preserve dirty object edits and survive the next Save")

            before_invalid = tree_hashes(private_content)
            for name in ("../escape", "bad/name", "EDITOR_CREATED"):
                client.request("create_level", {"name": name, "title": "Must Reject", "in_bundle": True}, expect_ok=False)
                require(tree_hashes(private_content) == before_invalid, "Rejected filename changed project content")
                identity(client, copied.name)
            client.request("duplicate_level", {"file": copied.name, "name": "editor_created", "title": "Collision"}, expect_ok=False)
            require(tree_hashes(private_content) == before_invalid, "Duplicate collision changed project content")
            passed("Traversal, bad names and case-insensitive collisions reject without publishing changes")

            before_external = copied.read_bytes()
            client.request("set_entity", {"id": objective["id"], "patch": {"transform": {"rotation": [0, 12, 0]}}})
            external = json.loads(before_external)
            external["title"] = "Changed Outside Editor"
            write_json(copied, external)
            external_bytes = copied.read_bytes()
            conflict = client.request("save", expect_ok=False)
            require("changed" in str(conflict).lower(), "Save did not explain the external-file conflict")
            require(copied.read_bytes() == external_bytes, "Conflicting Save overwrote an external source edit")
            state = identity(client, copied.name)
            require(state["dirty"], "Conflicting Save discarded the editor draft")
            # Restore our own simulated external edit, then retry the retained draft.
            copied.write_bytes(before_external)
            client.request("save")
            current_objective = next(entity for entity in json.loads(copied.read_bytes())["entities"] if entity["id"] == objective["id"])
            require(current_objective["transform"]["rotation"] == [0, 12, 0], "Save retry lost the retained draft")
            passed("External source edits block Save; restoring the known snapshot permits a safe retry")

            client.request("delete_level", {"file": created.name}, expect_ok=False)
            require(created.read_bytes() == created_bytes, "Unconfirmed deletion touched its source")
            deletion = client.request("delete_level", {"file": created.name, "confirmed": True})
            require(not created.exists(), "Confirmed deletion retained the canonical source")
            archive = Path(deletion["backup"]).resolve()
            require(archive.is_relative_to(private / ".dev/editor/deleted-levels") and archive.read_bytes() == created_bytes,
                    "Deletion did not retain an exact recoverable source copy")
            require(all(entry["file"] != created.name for entry in client.request("list_levels")["levels"]), "Deleted level remained registered")
            manifest = json.loads((private_content / "level_bundle.json").read_bytes())
            require(all(entry["source"] != created.name for entry in manifest["levels"]), "Deleted level remained in the game menu")
            report["deleted_source_backup"] = archive.as_posix()
            passed("Deletion requires confirmation, archives exact source bytes, and removes catalog and menu entries")

            copied_bytes = copied.read_bytes()
            deletion = client.request("delete_level", {"file": copied.name, "confirmed": True})
            require(not copied.exists() and Path(deletion["backup"]).read_bytes() == copied_bytes,
                    "Deleting the active level did not retain its source")
            state = client.request("inspect")
            require(Path(state["source"]).name in levels and not state["dirty"], "Deleting the active level did not open a surviving level")
            require(len(client.request("list_levels")["levels"]) == 3, "Temporary test levels remain in the catalog")
            after_deletions = tree_hashes(private_content)
            for filename, digest in copied_before.items():
                if filename != "level_bundle.json":
                    require(after_deletions.get(filename) == digest, f"Shared/source asset changed during test level deletion: {filename}")
            require(json.loads((private_content / "level_bundle.json").read_bytes()) == json.loads((content / "level_bundle.json").read_bytes()),
                    "Deleting test levels failed to restore the original menu entries")
            passed("Deleting the active level opens a survivor and preserves every original source and shared asset")

            for filename in ("first_room.json", "moonlit_courtyard.json", "first_room.json", "moonlit_courtyard.json"):
                open_level(client, filename)
            report["captures"].append(capture(client, private, "courtyard-after-repeated-switching"))
            repeated = report["captures"][-1]
            require(repeated["size"] == report["captures"][1]["size"], "Repeated switching changed viewport dimensions")
            client.request("play_level", {"start_preset": "missing-test-start"}, expect_ok=False)
            require(not (private / ".dev/ares/latest-session.json").exists(), "Rejected test start launched an emulator")
            passed("Repeated switching retains correct scene identity; an invalid test start cannot launch a game")

            client.request("quit")
            process.wait(timeout=15)
            require(process.returncode == 0, "Editor did not exit cleanly")
            client = launch()
            state = identity(client, "moonlit_courtyard.json")
            require(not state["dirty"], "Reopened project starts dirty")
            client.request("quit")
            process.wait(timeout=15)
            require(process.returncode == 0, "Reopened editor did not exit cleanly")
            passed("Relaunch restores the last selected project level and both owned sessions exit cleanly")
            report["passed"] = True
        except BaseException as error:
            report["error"] = str(error)
            report["traceback"] = traceback.format_exc()
            raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            report["real_content_unchanged"] = tree_hashes(content) == real_before
            report["elapsed_seconds"] = round(time.monotonic() - started, 2)
            if not report["real_content_unchanged"]:
                report["passed"] = False
                report.setdefault("error", "Real project content changed during verification; review concurrent edits")
            report["evidence"] = (private / "project-editor-verification.json").as_posix()
            write_json(private / "project-editor-verification.json", report)
            write_json(root / "build/project-editor-verification.json", report)
    require(report["passed"], report.get("error", "Project editor verification failed"))
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--editor", type=Path, default=ROOT / "editor/bazel-bin/darklantern64_project_editor.exe")
    args = parser.parse_args()
    try:
        verify(args.editor, args.root)
    except (AssertionError, OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"Project editor verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
