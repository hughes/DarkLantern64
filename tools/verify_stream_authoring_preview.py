"""Exercise region authoring and cooked multi-opening preview in an owned editor.

This launches only its private editor process. Sources in the user's project,
emulator settings and other editor sessions are never modified.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import math
import os
import shutil
import subprocess
import uuid

try:
    from verify_project_editor import EditorClient, capture, tree_hashes
    from compile_mission import canonical_json, canonical_source
except ModuleNotFoundError:
    from tools.verify_project_editor import EditorClient, capture, tree_hashes
    from tools.compile_mission import canonical_json, canonical_source

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "stream_multi_opening.json"
PARTITION = "stream_multi_opening.mission.json"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def region(draft, name):
    return next(row for row in draft["regions"] if row["id"] == name)


def draft(client):
    return client.request("get_streaming")["draft"]


def homes(client):
    return client.request("get_streaming")["homes"]


def published_regions(state):
    return {row["region"] for row in state["rendered_models"] if row.get("region")}


def settle(client, limit=1000):
    for _ in range(limit):
        state = client.request("step_stream_preview", {"ticks": 1})
        if state["stream"]["settled"]:
            return state
    raise AssertionError("Owned stream preview did not settle")


def verify(editor):
    original = tree_hashes(ROOT / "content")
    private = ROOT / ".dev/stream-authoring-preview-verification" / uuid.uuid4().hex
    private.mkdir(parents=True)
    for folder in ("content", "src", "tools"):
        shutil.copytree(ROOT / folder, private / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in (FIXTURE, PARTITION):
        shutil.copyfile(ROOT / "tests/fixtures" / name, private / "content" / name)
    queue = private / ".dev/editor/project"
    queue.mkdir(parents=True)
    source = private / "content" / FIXTURE
    partition_path = private / "content" / PARTITION
    source_before = source.read_bytes()
    evidence = {"private_root": str(private), "editor": str(Path(editor).resolve()),
                "editor_sha256": hashlib.sha256(Path(editor).read_bytes()).hexdigest(),
                "passed": False, "checks": [], "captures": []}
    startup = subprocess.STARTUPINFO() if os.name == "nt" else None
    if startup:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    with (private / "editor.log").open("w") as log:
        process = subprocess.Popen([str(Path(editor).resolve()), str(private)], cwd=private,
                                   stdout=log, stderr=log, startupinfo=startup)
        client = EditorClient(queue, process)
        try:
            client.wait_session()
            client.request("open_level", {"file": FIXTURE})
            initial = client.request("inspect")
            initial_document = copy.deepcopy(initial["document"])
            old = draft(client)
            bounds = region(old, "courtyard")["bounds"]
            activation = region(old, "courtyard")["activation_bounds"]
            client.request("create_region", {"id": "annex", "bounds": bounds,
                           "activation_bounds": activation, "working_set": ["annex"]})
            require(region(draft(client), "annex")["entities"] == [], "New region stole existing homes")
            pair_bounds = [[-.03, -1, 2], [.03, 3, 4]]
            client.request("add_transition_pair", {"id": "hub-to-annex", "reverse_id": "annex-to-hub",
                           "from": "hub", "to": "annex", "bounds": pair_bounds,
                           "from_working_set": ["hub", "north", "annex"], "to_working_set": ["annex"]})
            client.request("assign_region", {"region": "annex", "ids": ["courtyard-landmark"]})
            client.request("rename_region", {"id": "annex", "new_id": "garden"})
            renamed = draft(client)
            require("courtyard-landmark" in region(renamed, "garden")["entities"], "Rename lost home assignment")
            require(all("annex" not in [gate["from"], gate["to"]] for gate in renamed["transitions"]), "Rename left gate endpoints dangling")
            require("garden" in region(renamed, "hub")["neighbors"], "Rename left outgoing neighbor dangling")
            require("garden" in region(renamed, "hub")["working_set"], "Rename lost visibility of the assigned landmark")
            client.request("duplicate_region", {"id": "garden", "new_id": "garden-copy", "translation": [0, 0, 1]})
            copied = region(draft(client), "garden-copy")
            require(copied["entities"] == [] and copied["working_set"] == ["garden-copy"], "Region duplicate copied ownership or retained old self reference")
            require(copied["bounds"][0][2] == bounds[0][2] + 1, "Region duplicate did not move bounds")
            client.request("delete_region", {"id": "garden-copy"})
            before_reject = draft(client)
            client.request("delete_region", {"id": "garden"}, expect_ok=False)
            require(draft(client) == before_reject, "Rejected owned-region deletion changed drafts")
            client.request("delete_region", {"id": "garden", "reassign_to": "courtyard"})
            repaired = draft(client)
            require("courtyard-landmark" in region(repaired, "courtyard")["entities"], "Deletion lost reassigned object")
            require(all("garden" not in [gate["from"], gate["to"]] for gate in repaired["transitions"]), "Deletion retained endpoint gates")
            require("garden" not in region(repaired, "hub")["neighbors"], "Deletion retained outgoing neighbor")
            require("garden" not in region(repaired, "hub")["working_set"] and
                    "courtyard" in region(repaired, "hub")["working_set"], "Deletion hid previously visible geometry after moving its home")
            client.request("undo")
            require(draft(client) == before_reject, "Undo did not restore region, homes and paired gates together")
            client.request("redo")
            require(draft(client) == repaired, "Redo did not repeat atomic region deletion")
            evidence["checks"].append("Region create/rename/duplicate/delete preserves homes, repairs references and participates in paired Undo/Redo")
            # Restore this fixture's intentionally narrow starting visibility
            # after proving deletion preserves the reassigned visible content.
            client.request("set_region", {"id": "hub", "patch": {"working_set": ["hub", "north"]}})

            for kind, ident, field, handle in (("region", "hub", "bounds", "maximum"),
                                               ("region", "hub", "activation_bounds", "maximum"),
                                               ("gate", "hub-to-courtyard", "bounds", "center")):
                before_handle = draft(client)
                selected = client.request("select_stream_bounds", {"kind": kind, "id": ident,
                                         "field": field, "handle": handle})
                require(selected["active"], "Viewport bounds proxy was not selected")
                if kind == "region":
                    client.request("move_stream_bounds", {"position": region(before_handle, ident)[field][0]}, expect_ok=False)
                    require(draft(client) == before_handle, "Invalid completed bounds drag changed paired drafts")
                client.request("move_stream_bounds", {"delta": [.04, .02, .03]})
                moved = draft(client)
                require(moved != before_handle, "Completed gizmo drag did not change partition bounds")
                require(client.request("inspect")["document"] == initial_document, "Partition gizmo moved source geometry")
                client.request("undo")
                require(draft(client) == before_handle, "One Undo did not restore the whole completed bounds drag")
                client.request("redo")
                require(draft(client) == moved, "Redo did not restore completed bounds drag")
                client.request("undo")
                client.request("clear_stream_bounds")
            evidence["checks"].append("Region and gate viewport handles commit one paired history step and leave source geometry unchanged")

            created = client.request("add_blockout", {"id": "unassigned-proof", "shape": "box",
                                   "size": [.4, .4, .4], "position": [-3, .2, 0]})["created"][0]
            require(created in homes(client)["unassigned"], "New object was silently assigned a region")
            client.request("assign_region", {"region": "hub", "ids": [created]})
            require(created not in homes(client)["unassigned"], "Explicit home assignment remained unresolved")
            duplicate = client.request("duplicate_entities", {"ids": [created], "translation": [1, 0, 0]})["created"][0]
            require(duplicate in homes(client)["unassigned"], "Duplicated object was silently assigned a region")
            client.request("undo")
            client.request("save_prefab", {"name": "unassigned-fixture", "ids": [created]})
            placed = client.request("place_prefab", {"name": "unassigned-fixture", "position": [-4, .2, 0]})["created"][0]
            require(placed in homes(client)["unassigned"], "Prefab placement silently inherited a region home")
            for _ in range(4):
                client.request("undo")
            require(client.request("inspect")["document"] == initial_document, "Fixture geometry changed during region-authoring checks")
            evidence["checks"].append("New, duplicated and prefab-placed objects expose missing homes until explicitly assigned; Undo restores the source scene")

            preview = client.request("start_stream_preview", {"start_preset": "hub", "automatic": False})
            preview = client.request("select_stream_preview", {"region": "hub"})
            outgoing = {row["transition"]: row for row in preview["selection"]["outgoing"]}
            require(set(outgoing) == {"hub-to-north", "hub-to-courtyard"}, "Selected region did not expose both independent openings")
            require(set(outgoing["hub-to-north"]["transition_union"]) == {"hub", "north"} and
                    set(outgoing["hub-to-courtyard"]["transition_union"]) == {"hub", "north", "courtyard"} and
                    all(row["union_capacity"] and not row["witness_stale"] for row in outgoing.values()),
                    "Selected region has missing or incorrect destination unions/capacity witnesses")
            require(preview["stream"]["published_mask"] == 6, "Baseline hub working set was not the authored incomplete view")
            require(1 not in published_regions(preview), "Missing courtyard geometry was exposed from an unpublished region")
            require(preview["identity"]["source_saved_sha256"] == hashlib.sha256(source_before).hexdigest(), "Preview source identity did not match its checked saved source")
            require(preview["identity"]["cooked_source_sha256"] == hashlib.sha256(canonical_json(canonical_source(initial_document))).hexdigest(), "Cooked source identity does not bind the actual prepared draft")
            require(preview["identity"]["cooked_partition_sha256"] == hashlib.sha256((queue / "stream-preview-partition.json").read_bytes()).hexdigest(), "Cooked partition identity does not bind the staged paired draft")
            require(preview["cooked_partition"] == draft(client), "Preview's frozen partition does not match its initial prepared draft")
            require(hashlib.sha256(Path(preview["identity"]["package_path"]).read_bytes()).hexdigest() == preview["identity"]["package_sha256"], "Preview package identity does not match cooked DLM bytes")
            evidence["checks"].append("Selected source region exposes both outgoing openings, independent destination unions and checked capacity witnesses")
            evidence["captures"].append(capture(client, private, "multi-opening-missing-courtyard"))
            client.request("set_region", {"id": "hub", "patch": {"working_set": ["hub", "north", "courtyard"]}})
            stale = client.request("inspect_stream_preview")
            require(stale["stale_draft"] and stale["stream"]["published_mask"] == 6, "Draft edit silently changed the prepared runtime preview")
            require(stale["identity"] == preview["identity"] and stale["cooked_partition"] == preview["cooked_partition"], "Stale preview silently adopted newly authored identity or policy")
            require(len(stale["selection"]["outgoing"]) == 2 and all(row["witness_stale"] and row["union_capacity"] is None
                    for row in stale["selection"]["outgoing"]), "Stale region selection presented old capacity witnesses as current")
            # Recook while the old snapshot is still active, exercising the
            # same safe replacement path as the editor's Recook button.
            fixed = client.request("start_stream_preview", {"start_preset": "hub", "automatic": False})
            require(fixed["stream"]["published_mask"] == 7 and 1 in published_regions(fixed), "Working-set repair did not reveal the courtyard through its second opening")
            require(fixed["identity"]["play_fingerprint"] != preview["identity"]["play_fingerprint"] and
                    fixed["identity"]["package_sha256"] != preview["identity"]["package_sha256"], "Policy-only cook did not update prepared mission identity")
            require(not fixed["stale_draft"] and fixed["identity"]["cooked_source_sha256"] == preview["identity"]["cooked_source_sha256"] and
                    fixed["identity"]["cooked_partition_sha256"] != preview["identity"]["cooked_partition_sha256"], "Active recook did not preserve source geometry identity and replace partition identity")
            require(client.request("inspect")["document"] == initial_document, "Visibility repair changed geometry")
            selection = client.request("select_stream_preview", {"transition": "hub-to-courtyard"})
            witness = selection["selection"]
            require(set(witness["stable_set"]) == {"hub", "north", "courtyard"} and
                    witness["destination_set"] == ["courtyard"] and
                    set(witness["transition_union"]) == {"hub", "north", "courtyard"} and
                    witness["stable_capacity"] and witness["union_capacity"], "Selected opening has no matching policy/capacity witness")
            evidence["captures"].append(capture(client, private, "multi-opening-corrected-working-set"))
            evidence["checks"].append("A missing view through the second opening is repaired by working_set alone; stale prepared identity remains explicit until recook")

            client.request("set_stream_preview_camera", {"position": [-.8, 0, -3]})
            opened = client.request("step_stream_preview", {"ticks": 1, "input": {"use": True}})
            require(opened["game"]["door_open"] and opened["game"]["open_objects"], "Preview interaction failed to open its shared door")
            # Cross the actual south gate and retire the hub/north chunks before
            # asking for a slow return. Camera movement uses the shared swept
            # gate policy rather than an arbitrary region-selection override.
            client.request("set_stream_preview_camera", {"position": [-.5, 0, 3]})
            client.request("step_stream_preview", {"ticks": 1})
            client.request("set_stream_preview_camera", {"position": [.5, 0, 3]})
            client.request("step_stream_preview", {"ticks": 1})
            client.request("set_stream_preview_camera", {"position": [7, 0, 3], "yaw": math.pi * 1.5})
            departed = settle(client)
            require(departed["stream"]["published_mask"] == 1, "Remote courtyard failed to retire unrelated room chunks")
            collected = client.request("step_stream_preview", {"ticks": 1, "input": {"use": True}})
            require(collected["game"]["loot_value"] == 25 and collected["game"]["collected_objects"], "Runtime preview failed to collect cooked-region loot")
            client.request("set_stream_preview_transport", {"read_delay_ticks": 12, "read_slice_bytes": 32,
                           "renderer_delay_ticks": 5, "rsp_delay_ticks": 4, "rdp_delay_ticks": 7})
            client.request("set_stream_preview_camera", {"position": [.5, 0, 3]})
            waiting = client.request("step_stream_preview", {"ticks": 1})
            require(waiting["stream"]["published_mask"] == 1, "Slow return exposed an incomplete candidate")
            for _ in range(4):
                state = client.request("step_stream_preview", {"ticks": 1})
                require(state["stream"]["published_mask"] == 1 and published_regions(state) == {1}, "Delay exposed physically loading/pending geometry")
            client.request("set_stream_preview_camera", {"position": [7, 0, 3]})
            reversed_state = client.request("step_stream_preview", {"ticks": 8})
            require(reversed_state["stream"]["published_mask"] == 1, "Reversal published obsolete return work")
            require(reversed_state["stream"]["transport"]["read_stalls"] > 0, "Configured read delay never exercised the actual host transport")
            client.request("set_stream_preview_transport", {"read_delay_ticks": 0, "read_slice_bytes": 256,
                           "renderer_delay_ticks": 8, "rsp_delay_ticks": 4, "rdp_delay_ticks": 7})
            client.request("set_stream_preview_camera", {"position": [.5, 0, 3]})
            renderer_wait = None
            for _ in range(200):
                state = client.request("step_stream_preview", {"ticks": 1})
                require(state["stream"]["published_mask"] == 1, "Candidate published before delayed renderer completion")
                if state["stream"]["preparing"] and state["stream"]["cpu_ready"]:
                    renderer_wait = state
                    break
            require(renderer_wait is not None, "Delayed return never prepared a complete CPU candidate")
            for _ in range(3):
                state = client.request("step_stream_preview", {"ticks": 1})
                require(state["stream"]["published_mask"] == 1 and published_regions(state) == {1}, "CPU-ready candidate leaked into the viewport before its renderer was ready")
            obsolete_job = renderer_wait["stream"]["candidate_job"]
            client.request("set_stream_preview_camera", {"position": [7, 0, 3]})
            renderer_reversal = client.request("step_stream_preview", {"ticks": 1})
            require(renderer_reversal["stream"]["published_mask"] == 1 and any(
                event["kind"] == "candidate_cancel" and event["job"] == obsolete_job
                for event in renderer_reversal["stream"]["events"]), "Renderer preparation reversal did not cancel the obsolete job")
            client.request("set_stream_preview_transport", {"read_delay_ticks": 0, "read_slice_bytes": 256,
                           "renderer_delay_ticks": 0, "rsp_delay_ticks": 0, "rdp_delay_ticks": 0})
            client.request("set_stream_preview_camera", {"position": [.5, 0, 3]})
            returned = settle(client)
            require(returned["stream"]["published_mask"] == 7, "Returning approach failed to reload the repaired union")
            require(returned["stream"]["candidate_job"] > obsolete_job, "Canceled renderer job authorized the later publication")
            require(returned["game"]["loot_value"] == 25 and returned["game"]["door_open"] and
                    returned["game"]["open_objects"] == opened["game"]["open_objects"] and
                    returned["game"]["collected_objects"] == collected["game"]["collected_objects"], "Retirement/reversal/reload reset persistent mission state")
            evidence["checks"].append("Delayed IO/renderer/fence preview retains the published scene; reversal cancels obsolete demand and later reloads the complete working set")
            for slow_domain in ("rsp", "rdp"):
                transport = {"read_delay_ticks": 0, "read_slice_bytes": 256, "renderer_delay_ticks": 0,
                             "rsp_delay_ticks": 0, "rdp_delay_ticks": 0}
                transport[slow_domain + "_delay_ticks"] = 200
                client.request("set_stream_preview_transport", transport)
                submitted = client.request("step_stream_preview", {"ticks": 1})
                serial = submitted["stream"]["transport"]["submitted_serial"]
                old_generations = {row["region"]: row["generation"] for row in submitted["stream"]["slots"] if row["region"] in (2, 3)}
                client.request("set_stream_preview_camera", {"position": [7, 0, 3]})
                fenced = settle(client)
                require(fenced["stream"]["published_mask"] == 1, "Delayed graphics completion prevented removal publication")
                fast_domain = "rdp" if slow_domain == "rsp" else "rsp"
                require(fenced["stream"]["completed_" + fast_domain] >= serial and
                        fenced["stream"]["completed_" + slow_domain] < serial, "Independent graphics delays did not leave exactly the selected domain pending")
                for row in fenced["stream"]["slots"]:
                    if row["region"] in old_generations:
                        require(row["state"] == 5 and row["generation"] == old_generations[row["region"]], "Retiring generation was reused before both graphics domains completed")
                require(sum(row["state"] == 5 for row in fenced["stream"]["slots"]) == 2, "Excluded chunks did not remain RETIRING behind graphics fences")
                held = client.request("step_stream_preview", {"ticks": 4})
                require(sum(row["state"] == 5 for row in held["stream"]["slots"]) == 2, "One completed graphics domain released retirement storage")
                client.request("set_stream_preview_transport", {"rsp_delay_ticks": 0, "rdp_delay_ticks": 0})
                released = client.request("step_stream_preview", {"ticks": 1})
                require(not any(row["state"] == 5 for row in released["stream"]["slots"]), "Completed graphics fences did not release retired chunks")
                client.request("set_stream_preview_camera", {"position": [.5, 0, 3]})
                reloaded = settle(client)
                require(reloaded["stream"]["published_mask"] == 7 and reloaded["game"]["loot_value"] == 25 and
                        reloaded["game"]["door_open"], "Graphics-fenced reload lost the repaired view or persistent mission state")
            evidence["checks"].append("Independent RSP and RDP delays retain retiring slot generations until both domains complete, then permit reload")
            client.request("stop_stream_preview")

            # A changed saved partition rejects the paired Save and retains the
            # complete scene/partition draft for an exact-identity retry.
            saved_partition = partition_path.read_bytes()
            partition_path.write_bytes(saved_partition + b"\n")
            rejected = client.request("save", expect_ok=False)
            require("changed" in str(rejected).lower(), "Checked paired Save did not explain the partition conflict")
            require(source.read_bytes() == source_before and partition_path.read_bytes() == saved_partition + b"\n", "Conflicting Save changed canonical files")
            partition_path.write_bytes(saved_partition)
            client.request("save")
            require(json.loads(source.read_bytes()) == initial_document, "Paired Save changed geometry during visibility repair")
            saved = json.loads(partition_path.read_bytes())
            require(region(saved, "hub")["working_set"] == ["hub", "north", "courtyard"], "Save lost repaired visibility policy")
            require(not client.request("inspect")["dirty"], "Successful paired Save retained dirty identities")
            evidence["checks"].append("Paired checked Save rejects stale partition identity without losing drafts, then publishes the policy-only repair")
            client.request("open_level", {"file": "streaming_probe.json"})
            legacy = client.request("start_stream_preview", {"start_preset": "west", "automatic": False})
            outside = client.request("set_stream_preview_camera", {"position": [100, 0, 100]})
            outside = client.request("step_stream_preview", {"ticks": 2})
            require(outside["active"] and not outside["error"] and outside["camera_outside_regions"] and
                    outside["stream"]["published_mask"] == legacy["stream"]["published_mask"], "Free preview camera outside legacy regions destroyed its published bank")
            client.request("step_stream_preview", {"ticks": 1, "input": {}}, expect_ok=False)
            unchanged = client.request("inspect_stream_preview")
            require(unchanged["stream"] == outside["stream"] and unchanged["game"] == outside["game"], "Rejected outside simulation mutated the preview session")
            client.request("set_stream_preview_camera", {"position": [-7, 0, 0]})
            recovered = client.request("step_stream_preview", {"ticks": 2})
            for invalid in ({"read_slice_bytes": 0}, {"automatic": "invalid"}):
                client.request("set_stream_preview_transport", invalid, expect_ok=False)
                require(client.request("inspect_stream_preview")["stream"] == recovered["stream"], "Rejected transport settings changed coordinator state")
            native = client.request("start_play", {"start_preset": "west", "paused": True})
            require(not client.request("inspect_stream_preview")["active"] and native["stream"]["published_mask"] == 2,
                    "Starting Native Play retained a competing cooked preview or used the wrong west working set")
            client.request("stop_play")
            author_names = {row["source_id"] for row in client.request("inspect_scene")["entities"]}
            require("west-floor" in author_names and "east-floor" in author_names, "Stopping Native Play failed to restore full authored renderers after preview handoff")
            evidence["checks"].append("Legacy free camera can leave and return without losing the published bank; invalid simulation and transport commands reject atomically")
            evidence["checks"].append("Active cooked preview hands off to Native Play, which restores complete authored rendering on Stop")
            client.request("open_level", {"file": FIXTURE})
            before_terminal = draft(client)
            client.request("delete_region", {"id": "north", "reassign_to": "hub"})
            client.request("delete_region", {"id": "courtyard", "reassign_to": "hub"})
            client.request("fit_region_bounds", {"id": "hub"})
            client.request("set_region", {"id": "hub", "patch": {
                "activation_bounds": [[-10.11, -1, -6.11], [10.11, 3.11, 6.11]]}})
            terminal = draft(client)
            require(len(terminal["regions"]) == 1 and not terminal["transitions"], "Terminal region deletion retained dangling travel gates")
            terminal_preview = client.request("start_stream_preview", {"start_preset": "north", "automatic": False})
            require(terminal_preview["stream"]["published_mask"] == 1 and
                    terminal_preview["stream"]["slot_count"] == 1 and not terminal_preview["transitions"], "Valid final authored region could not prepare without gates")
            client.request("stop_stream_preview")
            for _ in range(4):
                client.request("undo")
            require(draft(client) == before_terminal and not client.request("inspect")["dirty"], "Undo of final region collapse did not restore the saved pair")
            evidence["checks"].append("Deleting down to one region with explicit reassignment and fitted coverage produces a valid gate-free preview; Undo restores the saved partition")
            require(tree_hashes(ROOT / "content") == original, "Owned verification touched the user's real content")
            evidence["passed"] = True
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)
            (private / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, default=ROOT / "editor/bazel-bin/darklantern64_project_editor.exe")
    args = parser.parse_args()
    print(json.dumps(verify(args.editor), indent=2))
