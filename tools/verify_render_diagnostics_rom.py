"""Verify single/two-door policy predictions in actual Tiny3D Ares replays.

ROM builds and launches are opt-in. This snapshots content and source into an
owned private project, records every result, and never changes user settings.
Run only after source freeze, separately from district performance/Native GUI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

try:
    from cook_lighting import host_compiler
    from compile_mission import prepare_stream_level
    from runtime_inspector import make_report, write_html
    from smoke_ares import exercise
    from verify_render_diagnostics import NAMES, bucket_values, require
    from verify_project_editor import EditorClient
    from verify_stream_replay_parity import functional_state, compare as compare_state
except ModuleNotFoundError:
    from tools.cook_lighting import host_compiler
    from tools.compile_mission import prepare_stream_level
    from tools.runtime_inspector import make_report, write_html
    from tools.smoke_ares import exercise
    from tools.verify_render_diagnostics import NAMES, bucket_values, require
    from tools.verify_project_editor import EditorClient
    from tools.verify_stream_replay_parity import functional_state, compare as compare_state

ROOT = Path(__file__).resolve().parents[1]


def inputs(doors):
    rows = [dict(strafe=-1) for _ in range(86)] + [dict(forward=1) for _ in range(177)]
    rows += [dict(use=True)] + [{} for _ in range(12)]
    if doors == 1:
        rows += [dict(use=True)] + [{} for _ in range(12)]
    else:
        for direction in (1, -1, 1):
            rows += [dict(strafe=direction) for _ in range(172)] + [dict(use=True)] + [{} for _ in range(12)]
    return rows


def prepare():
    # Keep SDK/compiler paths below Windows' legacy path limit.
    private = ROOT / ".dev/rd4-rom" / uuid.uuid4().hex[:8]
    private.mkdir(parents=True)
    project = private / "p"
    for folder in ("content", "src", "tools"):
        shutil.copytree(ROOT / folder, project / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(ROOT / "dependencies.json", project / "dependencies.json")
    shutil.copytree(ROOT / "external/tiny3d", project / "external/tiny3d")
    entries, fixtures = [], []
    for name, doors, ident in zip(NAMES, (1, 2), ("single", "multi")):
        for suffix in (".json", ".mission.json"):
            shutil.copyfile(ROOT / "tests/fixtures" / (name + suffix), project / "content" / (name + suffix))
        entries.append(dict(id=ident, mission=name + ".mission.json"))
        output = private / name
        cooked = prepare_stream_level(project / "content" / (name + ".mission.json"), output, project / "content")
        package = json.loads((output / "generated/play.json").read_bytes())
        replay = dict(format="darklantern-replay", version=1, tick_rate=60, stream_boundary="settled",
                      fingerprint=package["fingerprint"], start_preset="default", inputs=inputs(doors))
        replay_path = private / (name + "-replay.json")
        replay_path.write_text(json.dumps(replay, indent=2) + "\n", encoding="utf-8")
        fixtures.append(dict(id=ident, name=name, doors=doors, replay=str(replay_path),
                             fingerprint=package["fingerprint"], cooked_dir=str(output)))
    bundle = project / "content/rd4.json"
    bundle.write_text(json.dumps(dict(version=1, title="Renderer diagnostics fixtures", levels=entries), indent=2) + "\n", encoding="utf-8")
    plan = dict(private_root=str(private), project=str(project), bundle=str(bundle), fixtures=fixtures,
                passed=False, platform="Ares Tiny3D target renderer", results=[])
    (private / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    return plan


def verify(plan, sdk, ares, timeout, runner=None, settings=None):
    private, project = Path(plan["private_root"]), Path(plan["project"])
    evidence = dict(plan)
    try:
        for fixture in plan["fixtures"]:
            print("Building owned ROM fixture " + fixture["name"], flush=True)
            command = [sys.executable, str(project / "tools/build.py"), "--sdk", str(sdk),
                       "--bundle", plan["bundle"], "--start-level", fixture["id"],
                       "--input-replay", fixture["replay"], "--renderer", "t3d"]
            built = subprocess.run(command, cwd=project, text=True, capture_output=True)
            (private / (fixture["name"] + "-build.log")).write_text(built.stdout + built.stderr, encoding="utf-8")
            require(built.returncode == 0, "Fixture ROM build failed: " + built.stdout[-2000:] + built.stderr[-4000:])
            stem = "DarkLantern64-input-replay-bundle-rd4-start-" + fixture["id"] + "-preset-default-t3d"
            rom = project / "build" / (stem + ".z64")
            build_path = project / "build" / stem / "build.json"
            build = json.loads(build_path.read_bytes())
            require(hashlib.sha256(rom.read_bytes()).hexdigest() == build["rom_sha256"], "Fixture ROM differs from its exact build metadata")
            print("Running owned Ares fixture " + fixture["name"], flush=True)
            profile = settings or ROOT / ".dev/ares/settings-8mb.bml"
            run = exercise(ares, profile if profile.is_file() else ares.parent / "settings.bml", rom,
                           fixture["name"], "DL64 input_replay_done", timeout, build["rom_sha256"], memory=8)
            log = ROOT / run["log"]
            mapping_path = project / build["runtime_debug_map"]
            mapping = json.loads(mapping_path.read_bytes())
            inspector = make_report(log.read_bytes(), mapping, log, map_path=mapping_path, rom_bytes=rom.read_bytes())
            require(inspector["complete"], "Target lighting report incomplete: " + str(inspector["diagnostics"]))
            reports = inspector["lighting_reports"]
            selected = next(row for row in build["level_catalog"]["levels"] if row["id"] == fixture["id"])
            prediction = selected["content"]["mission"]["report"]["renderer_diagnostics"]
            require(reports and all(row["doors"] == fixture["doors"] for row in reports), "Actual target report lacks correct independent door count")
            require(bucket_values(reports[0]["cases"]) == bucket_values(prediction["cases"]), "Actual target initial shader policy differs from cook")
            changes = [row for row in reports if row["phase"] == "lighting_change"]
            require(len(changes) >= fixture["doors"] * 2, "Actual target did not render every independent door change")
            require(all(bucket_values(row["cases"]) == bucket_values(prediction["change_cases"]) for row in changes), "Actual target door-change shader policy differs from cook")
            states = [json.loads(line.split("DL64 replay_tick ", 1)[1]) for line in log.read_text().splitlines()
                      if line.startswith("DL64 replay_tick ")]
            require(len(states) == len(json.loads(Path(fixture["replay"]).read_bytes())["inputs"]), "Target replay state capture incomplete")
            definitions = json.loads((Path(fixture["cooked_dir"]) / "generated/play.json").read_bytes())["objects"]
            indices = [index for index, row in enumerate(definitions) if row["kind"] == "door"]
            combinations = {tuple(row["objects"][index]["open"] for index in indices) for row in states}
            require(len(combinations) == 1 << fixture["doors"], "Target gameplay did not exercise all independent door combinations")
            native_evidence = None
            if runner is not None and runner.is_file():
                target_package = project / "build" / stem / "catalog/levels" / (
                    ("0-" if fixture["doors"] == 1 else "1-") + fixture["id"]) / "generated/play.json"
                native = subprocess.run([str(runner), str(target_package), fixture["replay"], "--states"],
                                        text=True, capture_output=True, check=True)
                native_path = private / (fixture["name"] + "-native.json")
                native_path.write_text(native.stdout, encoding="utf-8")
                native_states = json.loads(native.stdout)["states"]
                require(len(native_states) == len(states), "Native/target replay lengths differ")
                for first, second in zip(native_states, states):
                    compare_state(functional_state(first), functional_state(second), f"tick {first['tick']}")
                native_evidence = dict(passed=True, states=len(states), path=str(native_path),
                    runner_sha256=hashlib.sha256(runner.read_bytes()).hexdigest(),
                    scope="Functional state parity on identical package and settled input ticks; host speed is separate.")
            write_html(private / (fixture["name"] + "-inspector.html"), inspector)
            (private / (fixture["name"] + "-inspector.json")).write_text(json.dumps(inspector, indent=2) + "\n", encoding="utf-8")
            evidence["results"].append(dict(fixture=fixture["name"], passed=True, run=run,
                build_id=build["signature"], rom_sha256=build["rom_sha256"], build_metadata=str(build_path),
                package_sha256=prediction["identity"]["package_sha256"], door_combinations=len(combinations),
                reports=reports, native_parity=native_evidence,
                inspector=str(private / (fixture["name"] + "-inspector.html"))))
        evidence["passed"] = True
        return evidence
    except Exception as error:
        evidence["error"] = str(error)
        raise
    finally:
        (private / "result.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def saved_rom_checks(plan, editor):
    """Run after Ares, preserving the private source and exact ROM/map bytes."""
    private, project = Path(plan["private_root"]), Path(plan["project"])
    queue = project / ".dev/editor/project"
    queue.mkdir(parents=True, exist_ok=True)
    evidence = dict(passed=False, editor=str(editor), editor_sha256=hashlib.sha256(editor.read_bytes()).hexdigest(), checks=[])
    startup = subprocess.STARTUPINFO() if os.name == "nt" else None
    if startup:
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    with (private / "saved-rom-editor.log").open("w") as log:
        process = subprocess.Popen([str(editor), str(project)], cwd=project, stdout=log, stderr=log, startupinfo=startup)
        client = EditorClient(queue, process)
        try:
            client.wait_session()
            for fixture in plan["fixtures"]:
                client.request("open_level", {"file": fixture["name"] + ".json"})
                state = client.request("refresh_render_diagnostics")
                saved = state["saved_rom"]
                require(saved["current"], "Current saved fixture build was not recognized by actual editor")
                rom = Path(saved["rom"])
                mapping_path = Path(saved["runtime_debug_map"])
                build = json.loads((mapping_path.parent / "build.json").read_bytes())
                require(saved["identity"]["build_id"] == build["signature"], "Saved editor build identity differs")
                require(saved["identity"]["rom_sha256"] == hashlib.sha256(rom.read_bytes()).hexdigest(), "Saved editor ROM identity differs")
                mapped = next(row for row in json.loads(mapping_path.read_bytes())["levels"] if row["id"] == fixture["id"])
                require(saved["identity"]["package_sha256"] == mapped["package_sha256"], "Saved editor package identity differs")
                require(bucket_values(saved["renderer_diagnostics"]["cases"]) == bucket_values(mapped["renderer_diagnostics"]["cases"]), "Saved ROM prediction differs from exact map")
                for path in (rom, mapping_path):
                    before = path.read_bytes()
                    try:
                        path.write_bytes(before + b"\nowned hash-binding verification\n")
                        require(not client.request("get_render_diagnostics")["saved_rom"]["current"], "Changed owned ROM/map retained editor certification")
                    finally:
                        path.write_bytes(before)
                    require(client.request("get_render_diagnostics")["saved_rom"]["current"], "Restored exact ROM/map did not revalidate")
                partition = project / "content" / (fixture["name"] + ".mission.json")
                partition_before = partition.read_bytes()
                try:
                    partition.write_bytes(partition_before + b"\n")
                    require(not client.request("get_render_diagnostics")["saved_rom"]["current"], "Changed saved partition retained ROM certification")
                finally:
                    partition.write_bytes(partition_before)
                require(client.request("get_render_diagnostics")["saved_rom"]["current"], "Restored partition did not revalidate saved ROM")
                evidence["checks"].append(dict(fixture=fixture["name"], identity=saved["identity"],
                    render_options=saved["render_options"], rom=str(rom), map=str(mapping_path),
                    verified="Exact build/ROM/package policy; ROM/map/partition tampering rejects and exact restoration revalidates"))
            evidence["passed"] = True
            return evidence
        except Exception as error:
            evidence["error"] = str(error)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=15)
            (private / "saved-rom-editor-result.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Build and run actual ROMs; requires coordinated source freeze")
    parser.add_argument("--plan", type=Path, help="Reuse an already prepared immutable private project plan")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))))
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--settings", type=Path, help="Owned Ares settings source; active user settings remain unchanged")
    parser.add_argument("--runner", type=Path, default=ROOT / "editor/bazel-bin/play_runner.exe")
    parser.add_argument("--verify-saved-rom", action="store_true", help="Run actual editor identity checks after target tests; no Ares launch")
    parser.add_argument("--editor", type=Path, default=ROOT / "editor/bazel-bin/darklantern64_project_editor.exe")
    args = parser.parse_args(argv)
    if not os.environ.get("DL64_HOST_CC"):
        compiler, _, _ = host_compiler()
        # Windows sandbox/desktop PATH enumeration can spell GCC.EXE and
        # gcc.exe differently. Its argv[0] then changes the version string and
        # therefore the recipe. Keep prepare and build on one explicit path.
        if os.name == "nt":
            compiler = compiler.with_name(compiler.name.lower())
        os.environ["DL64_HOST_CC"] = str(compiler)
    plan = json.loads(args.plan.read_bytes()) if args.plan else prepare()
    require(not (args.run and args.verify_saved_rom), "Run Ares and actual editor verification separately")
    if args.verify_saved_rom:
        result = saved_rom_checks(plan, args.editor.resolve())
        print(json.dumps(dict(passed=result["passed"], result=str(Path(plan["private_root"]) / "saved-rom-editor-result.json")), indent=2))
    elif args.run:
        result = verify(plan, args.sdk.resolve(), args.ares.resolve(), args.timeout, args.runner.resolve(),
                        args.settings.resolve() if args.settings else None)
        print(json.dumps(dict(passed=result["passed"], result=str(Path(plan["private_root"]) / "result.json")), indent=2))
    else:
        print(json.dumps(dict(prepared=True, plan=str(Path(plan["private_root"]) / "plan.json"),
                              note="No ROM build or Ares launch performed."), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
