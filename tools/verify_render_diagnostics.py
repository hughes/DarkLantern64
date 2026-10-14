"""Compare lit fixture cooks with the shared renderer policy and editor diagnostics.

Uses an owned output directory and, when requested, an owned private editor.
Host policy timing is evidence for the host helper only; target performance
requires the separately captured strict ROM workload.
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
import uuid

try:
    from compile_level import validate
    from compile_mission import cook_mission, bootstrap_header, runtime_debug_map, validate_stream_bridge
    from cook_lighting import FLAGS, host_compiler, run
    from runtime_inspector import parse_lighting_records, write_html, make_report
    from verify_project_editor import EditorClient, capture, tree_hashes
except ModuleNotFoundError:
    from tools.compile_level import validate
    from tools.compile_mission import cook_mission, bootstrap_header, runtime_debug_map, validate_stream_bridge
    from tools.cook_lighting import FLAGS, host_compiler, run
    from tools.runtime_inspector import parse_lighting_records, write_html, make_report
    from tools.verify_project_editor import EditorClient, capture, tree_hashes

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
NAMES = ("stream_lighting_single_door", "stream_lighting_multiple_doors")


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def bucket_values(rows):
    return {(row["mode"], row["reason"]): (row["models"], row["triangles"]) for row in rows}


def host_policy(private):
    compiler, env, compiler_identity = host_compiler()
    sources = ("game", "mission", "world_stream", "stream_scene", "stream_coordinator", "lighting_policy")
    artifacts = []
    for name, doors in zip(NAMES, (1, 2)):
        output = private / name
        output.mkdir()
        scene = json.loads((FIXTURES / (name + ".json")).read_bytes())
        manifest = json.loads((FIXTURES / (name + ".mission.json")).read_bytes())
        raw, cooked = cook_mission(manifest, FIXTURES)
        validate_stream_bridge(cooked)
        state = validate(scene, FIXTURES, streaming=True)
        state["renderer_source_sha256"] = cooked["source_sha256"]
        package = output / "mission.dlm"
        package.write_bytes(raw)
        header, _ = bootstrap_header(scene, state, cooked, "fixture.dlm")
        (output / "demo_level.h").write_text(header, encoding="utf-8")
        executable = output / ("policy.exe" if os.name == "nt" else "policy")
        run([compiler, *FLAGS, "-I" + str(ROOT / "src"), "-I" + str(output),
             ROOT / "tools/verify_lighting_policy.c", *[ROOT / "src" / (file + ".c") for file in sources],
             "-lm", "-o", executable], env)
        build = hashlib.sha256(executable.read_bytes()).hexdigest()
        observed = run([executable, package, build], env).stdout
        (output / "policy.log").write_text(observed, encoding="utf-8")
        mapping = runtime_debug_map(cooked, state)
        mapping["id"] = name
        mapping["renderer_diagnostics"] = cooked["renderer_diagnostics"]
        mapping["bridge_diagnostics"] = cooked["bridge_diagnostics"]
        mapping = dict(schema_version=1, build_id=build, levels=[mapping])
        (output / "map.json").write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
        reports, findings = parse_lighting_records(observed, mapping)
        require(not findings and all(row["complete"] for row in reports), f"Incomplete host policy report: {findings}")
        require(len(reports) == 1 + (1 << doors), "Host did not test every independent door combination")
        prediction = cooked["renderer_diagnostics"]
        require(prediction["door_object_count"] == doors, "Cook counted the wrong independent doors")
        require(bucket_values(reports[0]["cases"]) == bucket_values(prediction["cases"]),
                "Cook prediction differs from actual shared policy on decoded DLM publication")
        for row in reports[1:]:
            require(bucket_values(row["cases"]) == bucket_values(prediction["change_cases"]),
                    "Cook door-change prediction differs from actual shared policy")
        expected = ("inline_two_state", "inline_payload") if doors == 1 else ("live_night", "independent_doors")
        require(expected in bucket_values(reports[0]["cases"]), "Fixture did not exercise the intended renderer path")
        inspector = make_report(observed.encode(), mapping, output / "policy.log")
        inspector["limitations"].insert(0, "Host shared-policy fixture driver, not emulator or original-hardware performance evidence.")
        write_html(output / "inspector.html", inspector)
        (output / "inspector.json").write_text(json.dumps(inspector, indent=2) + "\n", encoding="utf-8")
        (output / "cook.json").write_text(json.dumps(cooked, indent=2) + "\n", encoding="utf-8")
        artifacts.append(dict(fixture=name, package_sha256=cooked["package_sha256"],
            source_sha256=cooked["source_sha256"], host_build_sha256=build, host_compiler=compiler_identity,
            door_combinations=1 << doors, reports=reports, predicted=prediction,
            map_path=str(output / "map.json"), log_path=str(output / "policy.log"),
            inspector_path=str(output / "inspector.html")))
    return artifacts


def verify(editor=None):
    private = ROOT / ".dev/render-diagnostics-verification" / uuid.uuid4().hex
    private.mkdir(parents=True)
    evidence = dict(private_root=str(private), passed=False, checks=[], host_policy=[])
    try:
        evidence["host_policy"] = host_policy(private)
        evidence["checks"].append("Single/two independent doors: cooked model buckets match shared C renderer policy on decoded package and every door-state combination")
        if editor:
            evidence.update(editor=str(Path(editor).resolve()), editor_sha256=hashlib.sha256(Path(editor).read_bytes()).hexdigest())
            editor_checks(private, Path(editor).resolve(), evidence)
        evidence["passed"] = True
        return evidence
    except Exception as error:
        evidence["error"] = str(error)
        raise
    finally:
        (private / "result.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def editor_checks(private, editor, evidence):
    original = tree_hashes(ROOT / "content")
    project = private / "project"
    project.mkdir()
    for folder in ("content", "src", "tools"):
        shutil.copytree(ROOT / folder, project / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in NAMES:
        for suffix in (".json", ".mission.json"):
            shutil.copyfile(FIXTURES / (name + suffix), project / "content" / (name + suffix))
    queue = project / ".dev/editor/project"
    queue.mkdir(parents=True)
    startup = subprocess.STARTUPINFO() if os.name == "nt" else None
    if startup:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    with (private / "editor.log").open("w") as log:
        process = subprocess.Popen([str(editor), str(project)], cwd=project,
                                   stdout=log, stderr=log, startupinfo=startup)
        client = EditorClient(queue, process)
        try:
            client.wait_session()
            for name, doors in zip(NAMES, (1, 2)):
                client.request("open_level", {"file": name + ".json"})
                current = client.request("refresh_render_diagnostics")
                require(not current["cooked_stale"] and current["dependencies_current"], "Fresh diagnostics were not source-bound")
                require(current["draft_door_count"] == doors, "Immediate editor independent-door count differs")
                prediction = current["cooked_renderer_diagnostics"]
                require(prediction["door_object_count"] == doors, "Editor cook prediction counted wrong doors")
                if doors == 2:
                    require("independent" in str(current["immediate_notice"]).lower(), "Second door lacked a pre-Play limitation notice")
                    require("live" in str(current["immediate_notice"]).lower(), "Second door notice omitted resulting live path")
                capacity = current["capacity_diagnostics"]
                require(capacity["valid"] and capacity["maxima"], "Current capacity witnesses missing")
                witness = capacity["maxima"]["triangle_count"]["witnesses"][0]
                for kind, ident in (("region", witness["anchor"]),
                                    ("group", witness["groups"][0]["id"]),
                                    ("object", witness["groups"][0]["source_entity_ids"][0]),
                                    ("transition", "hub-to-north")):
                    navigation = client.request("select_resource_witness", {"kind": kind, "id": ident})
                    require(navigation["kind"] == kind and navigation["id"] == ident, "Witness navigation returned another source")
                    if kind == "region":
                        require(navigation["selected_region"] == ident, "Region witness did not select Streaming source row")
                    elif kind == "transition":
                        require(navigation["selected_transition"] == ident, "Gate witness did not select directed transition")
                    else:
                        require(navigation["selected_ids"], "Object/group witness selected no authored source geometry")
                evidence["checks"].append(f"{name}: pre-Play path notice, source-bound cook and region/gate/group/object navigation")
                client.request("set_region", {"id": "hub", "patch": {"working_set": ["hub"]}})
                stale = client.request("get_render_diagnostics")
                require(stale["cooked_stale"], "Modified working set remained certified by previous report")
                client.request("select_resource_witness", {"kind": "group", "id": witness["groups"][0]["id"],
                    "expected_package_sha256": prediction["identity"]["package_sha256"]}, expect_ok=False)
                client.request("undo")
                current = client.request("refresh_render_diagnostics")
                require(not current["cooked_stale"], "Undo/recook did not restore current source diagnostics")
                evidence["checks"].append(f"{name}: stale cooked groups cannot navigate or certify modified content; repaired draft recooks")
                preview = client.request("start_stream_preview", {"automatic": False})
                require(preview["active"] and not preview["stale_draft"], "Lighting fixture preview failed to start current cook")
                # The editor reports the shared target policy on its actual
                # published bank; desktop presentation is a separate renderer.
                runtime = preview["renderer_runtime"]
                require(bucket_values(runtime["cases"]) == bucket_values(prediction["cases"]), "Editor published-model policy differs from cook")
                evidence.setdefault("editor_policies", []).append(dict(fixture=name, prediction=prediction, runtime=runtime,
                    identity=preview["identity"]))
                evidence.setdefault("captures", []).append(capture(client, project, name))
                interactions = ((-3, 1), (-3, 0)) if doors == 1 else ((-3, 1), (3, 2), (-3, 1), (3, 0))
                states = [preview["game"]["open_objects"]]
                for z, opened_count in interactions:
                    client.request("set_stream_preview_camera", {"position": [-.8, 0, z]})
                    changed = client.request("step_stream_preview", {"ticks": 1, "input": {"use": True}})
                    states.append(changed["game"]["open_objects"])
                    require(int(states[-1]).bit_count() == opened_count, "Actual editor interaction did not change the independent door state")
                    require(bucket_values(changed["renderer_runtime"]["change_cases"]) == bucket_values(prediction["change_cases"]),
                            "Actual door-state change classification differs from cooked prediction")
                evidence["editor_policies"][-1]["door_state_bits"] = states
                evidence["checks"].append(f"{name}: published target policy and actual door interactions match cooked publication/change buckets")
                client.request("stop_stream_preview")
            # Six reachable homes require more slots than the target adapter.
            # Every annex is a legitimate editable authored region and owns
            # real source geometry, so portable cooking must retain witnesses.
            client.request("open_level", {"file": NAMES[0] + ".json"})
            before = client.request("get_streaming")["draft"]
            north = next(row for row in before["regions"] if row["id"] == "north")
            for index in range(3):
                ident = f"capacity-annex-{index}"
                client.request("create_region", {"id": ident, "bounds": north["bounds"],
                    "activation_bounds": north["activation_bounds"], "working_set": [ident]})
                created = client.request("duplicate_entities", {"ids": ["north-east-wall"], "translation": [0, 0, 0]})["created"]
                client.request("assign_region", {"region": ident, "ids": created})
                client.request("add_transition_pair", {"id": "hub-to-" + ident, "reverse_id": ident + "-to-hub",
                    "from": "hub", "to": ident, "bounds": [[-.03, -1, -4], [.03, 3, -2]]})
            client.request("set_region", {"id": "hub", "patch": {"working_set": ["hub", "north", "courtyard"] +
                [f"capacity-annex-{index}" for index in range(3)]}})
            invalid = client.request("refresh_render_diagnostics")
            require(not invalid["cooked_stale"] and not invalid["capacity_diagnostics"]["valid"], "Over-capacity authored draft lost its diagnostic report")
            failures = [row for row in invalid["capacity_diagnostics"]["failures"] if row["metric"] == "resident_regions"]
            require(any(row["usage"] == 6 and row["cap"] == 5 and row["anchor"] == "hub" for row in failures), "Capacity failure omitted concrete working-set witness")
            require(client.request("inspect")["document"]["entities"], "Invalid capacity draft hid authoring scene")
            navigation = client.request("select_resource_witness", {"kind": "region", "id": "hub"})
            require(navigation["selected_region"] == "hub", "Invalid capacity witness could not navigate to repair region")
            client.request("start_stream_preview", {"automatic": False}, expect_ok=False)
            client.request("set_region", {"id": "hub", "patch": {"working_set": ["hub", "north", "courtyard"]}})
            require(client.request("get_render_diagnostics")["cooked_stale"], "Repaired content retained invalid report as current")
            repaired = client.request("refresh_render_diagnostics")
            require(repaired["capacity_diagnostics"]["valid"], "Capacity repair did not restore a valid report")
            evidence["checks"].append("Six-slot invalid target draft stays editable and source-navigable; preview rejects it; stale repair recooks to valid capacity")
            evidence["invalid_capacity"] = invalid
            evidence["repaired_capacity"] = repaired
            # Undo the capacity experiment to return to its checked pair before
            # switching to a mission with an external geometry dependency.
            for _ in range(14):
                client.request("undo")
            require(client.request("get_streaming")["draft"] == before, "Capacity experiment did not undo the complete paired draft")
            client.request("open_level", {"file": "streaming_probe.json"})
            dependency_current = client.request("refresh_render_diagnostics")
            require(dependency_current["dependencies_current"], "Imported-geometry report was initially stale")
            dependency = project / "content/models/guard.obj"
            dependency_before = dependency.read_bytes()
            dependency.write_bytes(dependency_before + b"\n# owned diagnostics dependency verification\n")
            dependency_stale = client.request("get_render_diagnostics")
            require(dependency_stale["cooked_stale"] and not dependency_stale["dependencies_current"], "Changed imported geometry retained certification")
            dependency.write_bytes(dependency_before)
            restored = client.request("refresh_render_diagnostics")
            require(restored["dependencies_current"] and not restored["cooked_stale"], "Restored asset dependency did not revalidate")
            evidence["checks"].append("External imported mesh edits invalidate source-bound diagnostics; restoring exact dependency and recooking revalidates")
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            require(tree_hashes(ROOT / "content") == original, "Verification modified user canonical content")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, help="Also verify an actual owned editor; omit for host policy only")
    args = parser.parse_args(argv)
    result = verify(args.editor)
    print(json.dumps({"passed": result["passed"], "checks": result["checks"],
                      "result": str(Path(result["private_root"]) / "result.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
