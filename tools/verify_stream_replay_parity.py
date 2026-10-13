"""Compare the shared stream coordinator's functional host/ROM replay state.

Settled boundaries isolate simulation decisions from platform loading speed.
This check does not certify IO latency, renderer behavior or N64 performance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid

try:
    from build import build_rom
    from compile_mission import prepare_stream_level
    from input_replay import load as load_input
    from smoke_ares import exercise
except ImportError:
    from tools.build import build_rom
    from tools.compile_mission import prepare_stream_level
    from tools.input_replay import load as load_input
    from tools.smoke_ares import exercise

ROOT = Path(__file__).resolve().parents[1]
NUMERIC_TOLERANCE = .002
GAME_FIELDS = ("tick", "player", "yaw", "pitch", "visibility", "door_open", "complete", "caught",
               "loot_value", "grounded", "crouched")
STREAM_FIELDS = ("current_region", "demand_mask", "published_mask", "active_enemy_mask",
                 "queued_use", "queued_object", "queued_transition")
GUARD_FIELDS = ("position", "state", "awareness", "patrol_index", "sees_player", "heard_sound")
OBJECT_FIELDS = ("open", "collected", "completed")
EVENT_FIELDS = ("kind", "position", "time", "strength", "actor", "detail", "previous")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def functional_state(state):
    """Fail closed on absent declared fields, ignore platform scheduling counters."""
    result = {key: state[key] for key in GAME_FIELDS}
    result["stream"] = {key: state["stream"][key] for key in STREAM_FIELDS}
    for name, fields in (("enemies", GUARD_FIELDS), ("objects", OBJECT_FIELDS), ("events", EVENT_FIELDS)):
        result[name] = [{key: row[key] for key in fields} for row in state[name]]
    return result


def compare(native, console, path="state"):
    if isinstance(console, dict):
        require(isinstance(native, dict) and native.keys() == console.keys(), path + ": fields differ")
        for key, value in console.items():
            compare(native[key], value, path + "." + key)
    elif isinstance(console, list):
        require(isinstance(native, list) and len(native) == len(console), path + ": count differs")
        for index, (first, second) in enumerate(zip(native, console)):
            compare(first, second, path + f"[{index}]")
    elif isinstance(native, float) or isinstance(console, float):
        require(type(native) in (int, float) and type(console) in (int, float) and
                math.isfinite(native) and math.isfinite(console) and abs(native-console) <= NUMERIC_TOLERANCE,
                f"{path}: native={native} console={console}")
    else:
        require(native == console, f"{path}: native={native} console={console}")


def decode_log(text, fingerprint, boundary="settled"):
    require("RSP CRASH |" not in text and "ASSERTION FAILED:" not in text, "ROM reported a runtime failure")
    require(text.count("DL64 input_replay_done") == 1, "Replay completion marker missing or duplicated")
    starts = [line for line in text.splitlines() if "DL64 replay_start " in line]
    require(len(starts) == 1 and ("fingerprint=" + fingerprint + " ") in (starts[0] + " ") and
            ("stream_boundary=" + boundary + " ") in (starts[0] + " "),
            "Replay provenance does not match the cooked fingerprint and streaming boundary")
    return [json.loads(line.split("DL64 replay_tick ", 1)[1]) for line in text.splitlines()
            if "DL64 replay_tick " in line]


def scenario(package, route):
    inputs = [dict(segment["input"]) for segment in route["segments"] for _ in range(segment["ticks"])]
    return dict(format="darklantern-replay", version=1, tick_rate=60, fingerprint=package["fingerprint"],
                start_preset=route["start_preset"], stream_boundary=route["stream_boundary"], inputs=inputs)


def coverage(states, replay):
    require(len(states) == len(replay["inputs"]), "Native replay is incomplete")
    require(all(row["tick"] == index+1 for index, row in enumerate(states)), "Replay ticks are discontinuous")
    require(not any(row["caught"] for row in states), "Recorded route was caught before completing")
    masks = [row["stream"]["published_mask"] for row in states]
    require(1 in masks and 2 in masks and 3 in masks, "Route did not publish west/east/transition views")
    loot = [row["loot_value"] for row in states]
    require(max(loot) > 0, "Route did not collect loot")
    restart = next(index for index, row in enumerate(replay["inputs"]) if row.get("restart"))
    collected = next(index for index, value in enumerate(loot) if value > 0)
    require(all(value == loot[collected] for value in loot[collected:restart]), "Loot changed during retirement/reload")
    require(loot[-1] == 0 and not states[-1]["door_open"], "Restart did not reset mission state")
    after = masks[collected:restart]
    require(1 in after and 2 in after, "Collected loot did not survive both region retirements")
    transitions = sum(first != second for first, second in zip(masks, masks[1:]))
    require(transitions >= 6, "Route lacks repeated retirement and reload")
    return dict(ticks=len(states), published_masks=sorted(set(masks)), publication_changes=transitions,
                loot_persisted=True, reset_passed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, default=ROOT / "editor/bazel-bin/play_runner.exe")
    parser.add_argument("--route", type=Path, default=ROOT / "tests/fixtures/stream_probe_route.json")
    parser.add_argument("--log", type=Path, help="Compare an existing matching ROM replay log")
    parser.add_argument("--native-only", action="store_true", help="Validate the route without claiming ROM parity")
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()
    directory = ROOT / "build/stream-replay-parity" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    prepare_stream_level(ROOT / "content/streaming_probe.mission.json", directory / "content")
    package_path = directory / "content/generated/play.json"
    package = json.loads(package_path.read_bytes())
    replay = scenario(package, json.loads(args.route.read_bytes()))
    replay_path = directory / "input.json"
    replay_path.write_text(json.dumps(replay, indent=2) + "\n", encoding="utf-8")
    load_input(replay_path)
    host_start = time.perf_counter()
    raw = subprocess.check_output([str(args.runner.resolve()), str(package_path), str(replay_path), "--states"], text=True)
    host_seconds = time.perf_counter() - host_start
    (directory / "native.json").write_text(raw, encoding="utf-8")
    native = json.loads(raw)["states"]
    result = dict(coverage(native, replay), native_passed=True, rom_parity_passed=False,
                  numeric_tolerance=NUMERIC_TOLERANCE, stream_boundary="settled",
                  package_sha256=package["stream"]["package_sha256"], fingerprint=package["fingerprint"],
                  replay_sha256=hashlib.sha256(replay_path.read_bytes()).hexdigest(),
                  declared_game_fields=list(GAME_FIELDS), declared_stream_fields=list(STREAM_FIELDS),
                  declared_guard_fields=list(GUARD_FIELDS), declared_object_fields=list(OBJECT_FIELDS),
                  declared_event_fields=list(EVENT_FIELDS),
                  native=str(directory / "native.json"), replay=str(replay_path),
                  host_timing=dict(elapsed_seconds=host_seconds, ticks=len(native),
                                   elapsed_ms_per_tick=host_seconds*1000/len(native),
                                   scope="Headless process wall time includes package/checksum startup, stream service, simulation and state serialization; no Native viewport or N64 timing claim."),
                  limitations=["Settled boundaries do not compare loading speed, frame pacing, graphics or audio.",
                               "Platform IO counters, slot placement, revision and graphics serials are excluded.",
                               "Synthetic delayed IO/fence tests and strict Ares performance/lifecycle checks are separate."])
    if not args.native_only:
        if args.log:
            log = args.log
        else:
            rom = build_rom(Path(os.environ.get("N64_INST", "C:/n64-toolchain")), bundle=ROOT / "content/level_bundle.json",
                            start_level="streaming-probe", start_preset=replay["start_preset"], input_replay=replay_path)
            run = exercise(Path(os.environ.get("ARES_EXE", str(Path(os.environ["LOCALAPPDATA"]) / "ares/ares.exe"))),
                           ROOT / ".dev/ares/settings-8mb.bml", rom, "stream-replay-parity", "DL64 input_replay_done", args.seconds)
            result["rom_run"] = run
            log = ROOT / run["log"]
        console = decode_log(log.read_text(errors="replace"), package["fingerprint"], replay["stream_boundary"])
        require(len(console) == len(native), "ROM replay trace is incomplete")
        for first, second in zip(native, console):
            compare(functional_state(first), functional_state(second), f"tick {first['tick']}")
        result.update(rom_parity_passed=True, rom_log=str(log.resolve()))
    result["result"] = str(directory / "result.json")
    payload = json.dumps(result, indent=2) + "\n"
    (directory / "result.json").write_text(payload, encoding="utf-8")
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(payload, encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
