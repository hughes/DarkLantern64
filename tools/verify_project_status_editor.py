"""Check shared project status in an owned, copied editor project.

This verifier exercises the panel and command boundary. It compares each frozen
editor draft with the read-only Python CLI, retains its evidence, and closes
only the process it starts. No emulator, ROM build or user session is touched.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

if __package__:
    from .verify_project_editor import EditorClient, hash_file, require, tree_hashes, write_json
    from .source_snapshot import inventory, digest_inventory
else:
    from verify_project_editor import EditorClient, hash_file, require, tree_hashes, write_json
    from source_snapshot import inventory, digest_inventory

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "stream_multi_opening.json"
PARTITION = "stream_multi_opening.mission.json"


def comparison_fields(status):
    """Exclude timestamps and independently observed process heartbeat fields."""
    return {key: copy.deepcopy(status[key]) for key in
            ("schema_version", "project_root", "collection", "draft", "scenes",
             "reports", "capabilities", "environment", "summary")}


def compare_shared_status(editor_status, cli_status):
    require(editor_status.get("schema_version") == 1, "Unsupported editor status schema")
    require(editor_status["collection"]["consistent"] is True, "Editor status inputs changed during collection")
    require(comparison_fields(editor_status) == comparison_fields(cli_status),
            "Editor status differs from the shared CLI for its frozen draft")


def scene_row(status, filename=FIXTURE):
    return next(row for row in status["scenes"] if row["file"] == "content/" + filename)


def report_row(status, path):
    return next(row for row in status["reports"] if Path(row["path"]) == path)


def verify(editor, root=ROOT):
    root, editor = Path(root).resolve(), Path(editor).resolve()
    require(editor.is_file(), f"Editor executable does not exist: {editor}")
    original = tree_hashes(root / "content")
    parent = root / ".dev/project-status-editor"
    require(parent.resolve() == parent, "Verification parent is linked")
    private = parent / uuid.uuid4().hex
    private.mkdir(parents=True)
    require(private.resolve().parent == parent, "Private project escaped its owned directory")
    for folder in ("content", "src", "tools"):
        shutil.copytree(root / folder, private / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for filename in (FIXTURE, PARTITION):
        shutil.copyfile(root / "tests/fixtures" / filename, private / "content" / filename)
    # Capability source references remain verifiable in this private copy.
    for filename in ("dependencies.json",):
        if (root / filename).is_file():
            shutil.copyfile(root / filename, private / filename)
    queue = private / ".dev/editor/project"
    queue.mkdir(parents=True)
    source, partition = private / "content" / FIXTURE, private / "content" / PARTITION
    source_before, partition_before = source.read_bytes(), partition.read_bytes()
    evidence = dict(passed=False, private_root=str(private), editor=str(editor),
                    editor_sha256=hash_file(editor), checks=[], snapshots=[],
                    scope="Actual editor panel/API and shared CLI; synthetic report is scoped fixture evidence only",
                    tools_sha256={name: hash_file(root / "tools" / name) for name in
                                  ("project_status.py", "verify_project_status_editor.py", "editorctl.py")})
    process = None
    startup = subprocess.STARTUPINFO() if os.name == "nt" else None
    if startup:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE

    def stable(client):
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = client.request("inspect")
            if not state["busy"]:
                return state
            time.sleep(.1)
        raise TimeoutError("Owned editor authoring preparation did not settle")

    def protected_hashes():
        return {name: tree_hashes(private / name) for name in ("content", "src", "tools", "build")}

    def snapshot(client, label):
        stable(client)
        before = protected_hashes()
        status = client.request("get_project_status", {"include_doctor": False}, timeout=180)
        require(status["editor_observation"]["collecting"] is False, "Completed response still claims a running collector")
        # The editor retains exactly the frozen draft submitted to the CLI.
        requests = sorted((queue / "status").glob("*/draft.json"), key=lambda p: int(p.parent.name))
        require(bool(requests), "Editor did not retain a frozen status draft")
        draft_path = requests[-1]
        completed = subprocess.run([sys.executable, "-B", str(private / "tools/project_status.py"),
                                    "--project", str(private), "--draft-file", str(draft_path), "--no-doctor"],
                                   cwd=private, capture_output=True, text=True, encoding="utf-8", timeout=150)
        require(completed.returncode == 0, completed.stderr)
        cli = json.loads(completed.stdout)
        compare_shared_status(status, cli)
        require(protected_hashes() == before, "Status collection wrote content, code, cook or ROM artifacts")
        index = len(evidence["snapshots"])
        path = private / f"status-{index}-{label}.json"
        write_json(path, status)
        cli_path = private / f"cli-{index}-{label}.json"
        write_json(cli_path, cli)
        evidence["snapshots"].append(dict(label=label, path=str(path), sha256=hash_file(path),
                                         cli_path=str(cli_path), cli_sha256=hash_file(cli_path)))
        return status

    with (private / "editor.log").open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen([str(editor), str(private)], cwd=private,
                                       stdout=log, stderr=log, startupinfo=startup)
            evidence["owned_editor_pid"] = process.pid
            client = EditorClient(queue, process)
            session = client.wait_session()
            require("get_project_status" in session["operations"], "Session does not expose project status")
            require(isinstance(session.get("process_creation_filetime"), int), "Session lacks an exact process creation token")
            client.request("open_level", {"file": FIXTURE}, timeout=180)
            initial = stable(client)
            saved = snapshot(client, "saved")
            require(saved["draft"]["dirty"] is False and saved["draft"]["partition_dirty"] is False,
                    "Saved scene/partition pair is incorrectly dirty")
            require(scene_row(saved)["cook"]["freshness"] == "current", "Prepared saved cook has no current source binding")
            require(scene_row(saved)["rom"]["status"] == "unavailable", "Private project invented a ROM result")
            owned = next(row for row in saved["sessions"] if row.get("pid") == process.pid)
            require(owned["owned"] is True and owned["identity_status"] == "known", "Actual editor process ownership was not verified")
            require(owned["source_sha256"] == hash_file(source), "Session saved-source identity is incorrect")
            evidence["checks"].append("Saved pair, current cook, unavailable ROM and owned process identity agree with the shared CLI; collection writes no project artifacts")

            client.request("set_panel", {"name": "Project Status", "visible": True})
            layout = client.request("get_layout")
            require(any(row["name"] == "Project Status" and row["active"] for row in layout["windows"]),
                    "Project Status panel did not become active")
            # Let the panel's on-open collection finish before issuing edits.
            snapshot(client, "panel-open")
            evidence["checks"].append("Project Status is registered, docked and renders a completed shared status snapshot")

            entity = next(row for row in initial["document"]["entities"] if row["kind"] == "spawn")
            position = list(entity["transform"]["position"]); position[0] += .125
            client.request("set_entity", {"id": entity["id"], "patch": {"transform": {"position": position}}})
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if client.request("inspect")["project_status"]["editor_observation"]["draft_changed_since_collection"]:
                    break
                time.sleep(.1)
            else:
                raise AssertionError("Cached panel snapshot failed to identify a later draft change")
            dirty = snapshot(client, "scene-draft")
            require(dirty["draft"]["dirty"] is True and source.read_bytes() == source_before,
                    "Unsaved scene draft was hidden or saved by status collection")
            client.request("undo")
            client.request("set_region", {"id": "hub", "patch": {"working_set": ["hub", "north", "courtyard"]}})
            dirty = snapshot(client, "partition-draft")
            require(dirty["draft"]["dirty"] is False and dirty["draft"]["partition_dirty"] is True,
                    "Unsaved partition identity was conflated with source geometry")
            require(partition.read_bytes() == partition_before, "Status saved the partition draft")
            client.request("undo")
            evidence["checks"].append("Scene and partition drafts retain independent hashes, stay unsaved, and mark prior panel snapshots out of date")

            # Explicitly scoped synthetic evidence tests UI transport of current,
            # stale and incomplete classifications without claiming a full run.
            files = inventory(private, external=False)
            fixture_report = private / "build/test-results/project-status-fixture.json"
            proof = private / "build/status-fixture-proof.txt"
            proof.write_text("owned fixture proof\n", encoding="utf-8")
            report = dict(scope="project-status fixture only", full_acceptance=False,
                          inputs=dict(files=files, source_sha256=digest_inventory(files), include_external=False),
                          results=[dict(id="fixture", required=True, status="passed", passed=True,
                                        evidence=[dict(path=str(proof), sha256=hash_file(proof))])])
            write_json(fixture_report, report)
            current = snapshot(client, "report-current")
            require(report_row(current, fixture_report)["status"] == "passing", "Current fixture report was not passing")
            private_change = private / "tools/status-fixture-added.txt"
            private_change.write_text("changes the source inventory\n", encoding="utf-8")
            stale = snapshot(client, "report-stale")
            require(report_row(stale, fixture_report)["status"] == "stale", "Changed source inventory retained current certification")
            private_change.unlink(); proof.unlink()
            missing = snapshot(client, "report-incomplete")
            require(report_row(missing, fixture_report)["status"] == "incomplete", "Missing evidence was reported as passing")
            fixture_report.unlink()
            evidence["checks"].append("Scoped fixture report current, stale and missing-evidence classifications are preserved by panel/API transport")

            preview = client.request("start_stream_preview", {"start_preset": "hub", "automatic": False}, timeout=180)
            preview_status = snapshot(client, "stream-preview")
            owned = next(row for row in preview_status["sessions"] if row.get("pid") == process.pid)
            require(owned["stream_preview"]["active"] is True and
                    owned["stream_preview"]["package_sha256"] == preview["identity"]["package_sha256"],
                    "Session did not bind the active cooked preview package")
            client.request("stop_stream_preview")
            play = client.request("start_play", {"start_preset": "hub", "paused": True}, timeout=180)
            play_status = snapshot(client, "native-play")
            owned = next(row for row in play_status["sessions"] if row.get("pid") == process.pid)
            require(owned["native_play"]["active"] is True and owned["native_play"]["mode"] == "paused" and
                    owned["native_play"]["package_sha256"] == play["renderer_runtime"]["identity"]["package_sha256"],
                    "Session did not bind the active Native Play package")
            require(owned["build"] is None and owned["native_play"]["build_id"] is None,
                    "Host preview invented a ROM build identity")
            client.request("stop_play")
            evidence["checks"].append("Live cooked preview and paused Native Play sessions identify their exact package while ROM build identity remains unknown")

            source.unlink()
            try:
                unknown = snapshot(client, "missing-saved-source")
                require(unknown["draft"]["dirty"] is None, "Missing saved scene was presented as clean")
                client.request("get_layout")  # Rendering the nullable draft must remain safe.
                client.alive()
            finally:
                source.write_bytes(source_before)
            restored = snapshot(client, "restored")
            require(restored["draft"]["dirty"] is False and restored["draft"]["partition_dirty"] is False,
                    "Restored saved pair did not revalidate")
            require(source.read_bytes() == source_before and partition.read_bytes() == partition_before,
                    "Status verifier failed to restore its private saved pair")
            evidence["checks"].append("Missing saved-source identity remains unknown and safely renders; restoring exact bytes revalidates the saved pair")
            client.request("quit", timeout=180)
            process.wait(timeout=130)
            require(process.returncode == 0, f"Owned editor quit with {process.returncode}")
            evidence["checks"].append("Owned editor exits after status collection without leaving a status worker running")
            evidence["passed"] = True
        except BaseException:
            evidence["error"] = traceback.format_exc()
            raise
        finally:
            if process and process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            require(tree_hashes(root / "content") == original, "Verification touched canonical user content")
            write_json(private / "result.json", evidence)
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, default=ROOT / "editor/bazel-bin/darklantern64_project_editor.exe")
    args = parser.parse_args(argv)
    result = verify(args.editor)
    print(json.dumps(dict(passed=result["passed"], checks=result["checks"],
                          result=str(Path(result["private_root"]) / "result.json")), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
