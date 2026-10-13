#!/usr/bin/env python3
"""Verify input-driven two-region traversal from an isolated Ares ROM run."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "DL64 stream_probe "
COMPLETE = "DL64 stream_test_complete"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, label, minimum=0):
    require(type(value) is int and value >= minimum, label + ": expected an integer")
    return value


def vector(value, label):
    require(isinstance(value, list) and len(value) == 3, label + ": expected XYZ")
    require(all(type(component) in (int, float) and math.isfinite(component) for component in value),
            label + ": expected finite XYZ")
    return value


def music_evidence(text):
    rows = [dict(re.findall(r"([a-z_]+)=([^ ]+)", line)) for line in text.splitlines()
            if line.startswith("DL64 music cue=")]
    require(rows and all(row.get("cue") == "lanterns_below" for row in rows), "Original music cue lacks playback evidence")
    for row in rows:
        for field in ("consumed_frames", "generated_blocks", "queued", "underruns"):
            row[field] = int(row[field])
            integer(row[field], "music." + field)
    require(max(row["consumed_frames"] for row in rows) > 0, "Music cue did not consume sample frames")
    require(max(row["generated_blocks"] for row in rows) > 0, "Music cue did not generate playback blocks")
    return dict(cue="lanterns_below", consumed_frames=max(row["consumed_frames"] for row in rows),
                generated_blocks=max(row["generated_blocks"] for row in rows),
                max_underruns=max(row["underruns"] for row in rows), sample_count=len(rows))


def decode_log(text):
    """Fail closed on incomplete traversal, state loss or unbounded slot telemetry."""
    require("RSP CRASH |" not in text and "ASSERTION FAILED:" not in text and
            "stream_test_failed" not in text, "ROM reported a runtime failure")
    lines = text.splitlines()
    menu_line = next((index for index, line in enumerate(lines)
                      if line.startswith("DL64 menu_ready ") and "mode=menu" in line), None)
    starts = [(index, line) for index, line in enumerate(lines) if line.startswith("DL64 level_start ")]
    require(menu_line is not None and len(starts) == 1 and menu_line < starts[0][0] and
            "id=streaming-probe " in starts[0][1], "Diagnostic did not select the probe through the ordinary menu")
    checkpoints, terminal = [], []
    for number, line in enumerate(lines, 1):
        if line.startswith(PREFIX):
            row = json.loads(line[len(PREFIX):])
            require(isinstance(row, dict), f"line {number}: expected checkpoint object")
            integer(row["tick"], "tick")
            vector(row["player"], "player")
            require(type(row["door_open"]) in (bool, int) and row["door_open"] in (0, 1), "door_open: expected a boolean")
            integer(row["loot_value"], "loot_value")
            integer(row["collected_objects"], "collected_objects")
            require(isinstance(row["guards"], list) and row["guards"], "Expected persistent guard state")
            for guard in row["guards"]:
                vector(guard["position"], "guard.position")
                require(type(guard["awareness"]) in (int, float) and math.isfinite(guard["awareness"]), "Invalid guard awareness")
                require(guard["state"] in ("PATROL", "INVESTIGATE", "SEARCH", "CHASE"), "Invalid guard state")
                integer(guard["patrol_index"], "guard.patrol_index")
            stream = row.get("stream", row)
            integer(stream["current"], "current", 1)
            resident = stream["resident"]
            if isinstance(resident, list):
                require(resident and len(resident) == len(set(resident)), "Invalid resident ID set")
                require(all(type(ident) is int and ident > 0 for ident in resident), "Invalid resident region ID")
                require(stream["current"] in resident, "Current region has no resident collision")
                count = len(resident)
            else:
                count = integer(resident, "resident", 1)
            require(count <= 2, "Two-region probe exceeded its resident slot bound")
            for field in ("reserved", "allocated", "completed_loads", "retired", "readbytes", "stalls"):
                integer(stream[field], field)
            require(stream["reserved"] <= stream["allocated"], "Reserved chunk bytes exceed allocated slots")
            checkpoints.append(dict(row, stream=dict(stream, resident_count=count)))
        elif line.startswith(COMPLETE):
            terminal.append(dict(re.findall(r"([a-z_]+)=([^ ]+)", line[len(COMPLETE):])))
    require(len(terminal) == 1, "Expected exactly one completed traversal marker")
    require(len(checkpoints) >= 6, "Expected at least six traversal checkpoints")
    require(all(a["tick"] < b["tick"] for a, b in zip(checkpoints, checkpoints[1:])), "Checkpoint ticks must increase")
    require(terminal[0].get("passed") == "1", "ROM did not report successful traversal assertions")
    require(terminal[0].get("input_only") == "1", "Traversal did not use the input-only diagnostic")
    require(int(terminal[0].get("audio_calls", "0")) > 0, "Traversal did not service audio")

    current = [row["stream"]["current"] for row in checkpoints]
    route = [current[0]]
    for region in current[1:]:
        if region != route[-1]:
            route.append(region)
    require(len(set(route)) == 2 and len(route) >= 4, "Expected west/east/backtrack/east traversal")
    require(route[0] == route[2] and route[1] == route[3], "Backtracking route is inconsistent")
    require({bool(row["door_open"]) for row in checkpoints} == {False, True}, "Door never opened and closed")
    acquired = next((index for index, row in enumerate(checkpoints) if row["loot_value"] > 0), None)
    require(acquired is not None and acquired > 0, "No input-driven pickup was recorded")
    pickup = checkpoints[acquired]
    require(pickup["collected_objects"] > 0, "Loot value lacks collected object state")
    require(all(row["loot_value"] == pickup["loot_value"] and row["collected_objects"] == pickup["collected_objects"]
                for row in checkpoints[acquired:]), "Loot reset or duplicated after a region reload")
    require(any(row["stream"]["current"] != pickup["stream"]["current"] for row in checkpoints[acquired + 1:]),
            "Collected loot was never left behind during backtracking")
    require(checkpoints[-1]["stream"]["current"] == pickup["stream"]["current"], "Pickup region was not reloaded")

    for field in ("completed_loads", "retired", "readbytes", "stalls"):
        require(all(a["stream"][field] <= b["stream"][field] for a, b in zip(checkpoints, checkpoints[1:])),
                field + ": counter regressed during traversal")
    require(len({row["stream"]["allocated"] for row in checkpoints}) == 1, "Slot allocation changed during traversal")
    require(checkpoints[-1]["stream"]["completed_loads"] >= 4, "No repeated region loading occurred")
    require(checkpoints[-1]["stream"]["retired"] >= 2, "Both sides were not retired")
    require(sum(row["stream"]["resident_count"] == 1 for row in checkpoints) >= 2, "No single-region residency evidence")
    require(terminal[0].get("guard_persisted") == "1", "Guard persistence assertion is missing")
    named = {row.get("checkpoint"): row for row in checkpoints}
    if "east_retired" in named and "east_reload" in named:
        require(named["east_retired"]["guards"] == named["east_reload"]["guards"], "Guard state changed across unload/reload")
    music = music_evidence(text)
    capture_diagnostic = "DL64 capture_complete views=2" in lines
    if not capture_diagnostic:
        require(music["max_underruns"] == 0, "Music underrun during the ordinary streaming route")
    return dict(passed=True, checkpoints=checkpoints, terminal=terminal[0], route=route,
                functional_passed=True, capture_diagnostic=capture_diagnostic, music=music,
                audio_continuity_passed=music["max_underruns"] == 0,
                timing_scope="Framebuffer-export diagnostic; excludes normal frame/audio acceptance" if capture_diagnostic else
                             "Ordinary input route without framebuffer export; emulator continuity only",
                peak_reserved_chunk_bytes=max(row["stream"]["reserved"] for row in checkpoints),
                allocated_slot_bytes=checkpoints[0]["stream"]["allocated"],
                hardware_validation="Original N64 and M64 pending; emulator traversal is not full memory/frame acceptance")


def verify_build(rom, metadata=None):
    report = json.loads((metadata or ROOT / "build" / rom.stem / "build.json").read_text(encoding="utf-8"))
    rom_sha = hashlib.sha256(rom.read_bytes()).hexdigest()
    require(report.get("rom_sha256") == rom_sha, "ROM differs from its build report; rebuild before testing")
    require(report.get("stream_test") is True, "ROM was not built with --stream-test")
    require(report.get("autoplay") is False and report.get("capture") is False, "Conflicting diagnostic build")
    levels = report["level_catalog"]["levels"]
    probe = next((row for row in levels if row["id"] == "streaming-probe"), None)
    require(probe is not None and probe["content"].get("experimental_stream") is True, "Missing streamed menu entry")
    try:
        from compile_mission import prepare_stream_level
    except ModuleNotFoundError:
        from tools.compile_mission import prepare_stream_level
    (ROOT / "build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="verify-stream-source-", dir=ROOT / "build") as temporary:
        current = prepare_stream_level(ROOT / "content/streaming_probe.mission.json", Path(temporary))
    require(probe["content"]["source_sha256"] == current["source_sha256"], "Probe source/compiler differs from built ROM")
    return report, rom_sha


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, help="Verify an existing --stream-test ROM instead of building it")
    parser.add_argument("--log", type=Path, help="Analyze an existing diagnostic log without launching Ares")
    parser.add_argument("--capture", action="store_true", help="Build the separate framebuffer-export diagnostic")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))))
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--output", type=Path, default=ROOT / "build/streaming-probe-verification.json")
    args = parser.parse_args(argv)
    report = dict(passed=False, platform="Ares emulator", started_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  hardware_validation="Original N64 and M64 pending")
    try:
        require(math.isfinite(args.timeout) and 0 < args.timeout <= 600, "Timeout must be 0..600 seconds")
        if args.log:
            require(args.rom is not None, "--log requires --rom for build/hash verification")
        if args.rom:
            rom = args.rom.resolve()
        else:
            try:
                from build import build_rom
            except ModuleNotFoundError:
                from tools.build import build_rom
            rom = build_rom(args.sdk.resolve(), bundle=ROOT / "content/level_bundle.json", stream_test=True,
                            stream_test_capture=args.capture)
        recorded_metadata = args.log.resolve().parent / "verification-build.json" if args.log else None
        build_report, rom_sha = verify_build(rom, recorded_metadata if recorded_metadata and recorded_metadata.is_file() else None)
        report.update(rom=str(rom), rom_sha256=rom_sha, source_sha256=build_report["source_sha256"])
        if args.log:
            log = args.log.resolve()
            snapshot = log.parent / rom.name
            require(snapshot.is_file(), "Recorded log lacks its isolated ROM snapshot")
            require(hashlib.sha256(snapshot.read_bytes()).hexdigest() == rom_sha,
                    "Recorded log's ROM snapshot differs from the supplied ROM; use that run's snapshot")
        else:
            require(args.ares.is_file(), "Ares is not installed at the configured path")
            try:
                from smoke_ares import exercise
            except ModuleNotFoundError:
                from tools.smoke_ares import exercise
            settings = ROOT / ".dev/ares/settings-8mb.bml"
            if not settings.is_file():
                settings = args.ares.parent / "settings.bml"
            run = exercise(args.ares, settings, rom, "streaming-probe", COMPLETE, args.timeout, rom_sha, memory=8)
            report["run"] = run
            log = ROOT / run["log"]
            (log.parent / "verification-build.json").write_text(json.dumps(build_report, indent=2) + "\n", encoding="utf-8")
        report.update(log=str(log), log_sha256=hashlib.sha256(log.read_bytes()).hexdigest())
        text = log.read_text(errors="replace")
        if "stream_test_capture" in build_report:
            require(build_report["stream_test_capture"] == ("DL64 capture_complete views=2" in text),
                    "Log capture mode differs from its recorded build")
        if "DL64 capture_begin " in text:
            require("DL64 capture_complete views=2" in text, "Framebuffer capture did not complete")
            try:
                from capture_ares import decode_captures
            except ModuleNotFoundError:
                from tools.capture_ares import decode_captures
            from PIL import Image
            frames = decode_captures(text, expected_count=2, expected_dimensions=(320, 240))
            captures = []
            for ident, (width, height, pixels) in sorted(frames.items()):
                destination = ROOT / "build" / f"streaming-probe-{'west' if ident == 1 else 'east'}.png"
                Image.frombytes("RGB", (width, height), pixels).save(destination)
                captures.append(dict(id=ident, image=str(destination), sha256=hashlib.sha256(destination.read_bytes()).hexdigest()))
            report["captures"] = captures
        report["music"] = music_evidence(text)
        report.update(decode_log(text))
        return_code = 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        report["error"] = str(error)
        print("Streaming probe verification failed: " + str(error), file=sys.stderr)
        return_code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(passed=report["passed"], report=str(args.output), error=report.get("error"))))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
