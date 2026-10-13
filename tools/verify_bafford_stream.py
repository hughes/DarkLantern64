#!/usr/bin/env python3
"""Verify bounded, input-driven traversal of the locally derived Bafford street."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

try:
    from verify_streaming_probe import require, integer, vector, music_evidence
except ModuleNotFoundError:
    from tools.verify_streaming_probe import require, integer, vector, music_evidence

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "DL64 bafford_stream "
COMPLETE = "DL64 bafford_stream_test_complete"
CAPTURE_COMPLETE = "DL64 bafford_capture_complete views=2"
CHECKPOINTS = ("start", "west_ready", "east_crossed", "east_turnaround",
               "west_crossed", "west_return", "east_return")


def decode_log(text, regions=None, require_music=False):
    require(not any(marker in text for marker in ("RSP CRASH |", "ASSERTION FAILED:", "bafford_stream_test_failed")),
            "ROM reported a runtime failure")
    lines = text.splitlines()
    menu = next((i for i, line in enumerate(lines) if line.startswith("DL64 menu_ready ") and "mode=menu" in line), None)
    starts = [(i, line) for i, line in enumerate(lines) if line.startswith("DL64 level_start ")]
    require(menu is not None and len(starts) == 1 and menu < starts[0][0] and "id=bafford-stream " in starts[0][1],
            "Diagnostic did not select Bafford stream through the ordinary menu")
    rows, terminals, checkpoint_lines, terminal_lines = [], [], [], []
    expected_ids = {region["id"] for region in regions} if regions else {1, 2}
    for index, line in enumerate(lines):
        if line.startswith(PREFIX):
            row = json.loads(line[len(PREFIX):])
            require(isinstance(row, dict), "Expected checkpoint object")
            integer(row["tick"], "tick", 1)
            require(type(row["elapsed"]) in (int, float) and math.isfinite(row["elapsed"]) and row["elapsed"] > 0,
                    "Invalid elapsed simulation time")
            point = vector(row["player"], "player")
            require(abs(point[1] + 2.4384) < .03, "Feet left the source street floor")
            require(row["grounded"] == 1 and row["collision_clear"] == 1, "Player lost support or body clearance")
            require(-6.7 <= point[0] <= 2.35 and 49.25 <= point[2] <= 49.95,
                    "Player left the bounded diagnostic corridor")
            for field in ("heap_used", "models", "colliders", "textures"):
                integer(row[field], field, 1)
            stream = row["stream"]
            require(type(stream["current"]) is int and stream["current"] in expected_ids, "Invalid current region")
            require(isinstance(stream["resident"], list) and len(stream["resident"]) == 2 and
                    all(type(ident) is int for ident in stream["resident"]) and set(stream["resident"]) == expected_ids,
                    "Open street did not retain both visible regions")
            for field in ("reserved", "allocated", "completed_loads", "retired", "readbytes", "stalls"):
                integer(stream[field], field)
            require(0 < stream["reserved"] <= stream["allocated"], "Chunk reservation exceeds fixed slots")
            require(stream["completed_loads"] == 2 and stream["retired"] == 0,
                    "Conservative open-street policy unexpectedly loaded or retired a region")
            if regions:
                region = next(region for region in regions if region["id"] == stream["current"])
                lower, upper = region["bounds"]
                require(all(lower[i] <= point[i] <= upper[i] for i in range(3)), "Player outside current region coverage")
                require(stream["reserved"] == sum(region["chunk_bytes"] for region in regions), "Wrong decoded chunk reservation")
                require(stream["readbytes"] == stream["reserved"], "Chunks were not read exactly once")
            rows.append(row)
            checkpoint_lines.append(index)
        elif line.startswith(COMPLETE):
            terminals.append(dict(re.findall(r"([a-z_]+)=([^ ]+)", line)))
            terminal_lines.append(index)
    require(len(terminals) == 1 and all(terminals[0].get(field) == "1" for field in
            ("passed", "input_only", "coverage", "feet_stable", "collision_clear")), "Missing successful input traversal assertions")
    require(int(terminals[0].get("audio_calls", "0")) > 0, "Normal audio callbacks were not serviced")
    require(tuple(row.get("checkpoint") for row in rows) == CHECKPOINTS, "Missing, duplicated or reordered traversal checkpoint")
    require(starts[0][0] < checkpoint_lines[0] and checkpoint_lines[-1] < terminal_lines[0],
            "Traversal checkpoints/completion are outside the launched diagnostic interval")
    for a, b in zip(rows, rows[1:]):
        require(a["tick"] < b["tick"] and a["elapsed"] < b["elapsed"], "Traversal time regressed or restarted")
        require(abs((b["elapsed"] - a["elapsed"]) - (b["tick"] - a["tick"]) / 60) < .01,
                "Elapsed time differs from the fixed 60 Hz input ticks")
        require(math.dist(a["player"], b["player"]) <= 2.15 * (b["elapsed"] - a["elapsed"]) + .02,
                "Movement exceeds ordinary walking speed")
        require(all(a["stream"][field] <= b["stream"][field] for field in ("readbytes", "stalls")), "Stream counters regressed")
    require(len({row["stream"]["allocated"] for row in rows}) == 1, "Region slot allocation changed")
    require(len({row["stream"]["reserved"] for row in rows}) == 1, "Resident payload reservation changed")
    require(len({(row["models"], row["colliders"], row["textures"]) for row in rows}) == 1,
            "Open-street working geometry changed during traversal")
    route = [rows[0]["stream"]["current"]]
    for row in rows[1:]:
        if row["stream"]["current"] != route[-1]:
            route.append(row["stream"]["current"])
    require(len(route) == 4 and route[0] == route[2] and route[1] == route[3] and route[0] != route[1],
            "Expected west/east/backtrack/east crossings")
    named = {row["checkpoint"]: row for row in rows}
    for checkpoint, x in (("west_ready", -6.522049), ("east_crossed", -2.5), ("east_turnaround", 2.2),
                          ("west_crossed", -3.6), ("west_return", -6.522049), ("east_return", 2.2)):
        point = named[checkpoint]["player"]
        require(math.hypot(point[0] - x, point[2] - 49.4) < .13, "Wrong input waypoint: " + checkpoint)
    steady = [named[name]["heap_used"] for name in CHECKPOINTS[3:]]
    require(len(set(steady)) == 1, "Heap grew across repeated street crossings after initial traversal")
    capture = CAPTURE_COMPLETE in lines
    require("DL64 capture_begin " not in text or capture, "Framebuffer export did not complete")
    require(not capture or "DL64 capture_begin " in text, "Capture completion has no framebuffer export")
    music = music_evidence(text) if "DL64 music cue=" in text else None
    require(not require_music or music is not None, "Assigned music cue lacks playback evidence")
    if music and not capture:
        require(music["max_underruns"] == 0, "Music underrun during ordinary Bafford traversal")
    return dict(passed=True, functional_passed=True, checkpoints=rows, terminal=terminals[0], route=route,
                conservative_visibility="Both regions retained; source street has no proven visibility separator",
                loads=2, retirements=0, allocated_slot_bytes=rows[0]["stream"]["allocated"],
                reserved_chunk_bytes=rows[0]["stream"]["reserved"], steady_heap_samples_bytes=steady,
                capture_diagnostic=capture, music=music,
                audio_continuity_passed=music["max_underruns"] == 0 if music else None,
                timing_scope="Framebuffer export diagnostic; excludes ordinary audio/frame acceptance" if capture else
                             "Ordinary input traversal; emulator only, not hardware acceptance")


def verify_build(rom, metadata=None):
    build = json.loads((metadata or ROOT / "build" / rom.stem / "build.json").read_text(encoding="utf-8"))
    require(build.get("rom_sha256") == hashlib.sha256(rom.read_bytes()).hexdigest(), "ROM differs from its recorded build")
    require(build.get("bafford_stream_test") is True and build.get("stream_test") is False,
            "ROM lacks the separate Bafford stream diagnostic")
    require(not any(build.get(field) for field in ("autoplay", "capture", "menu_test")), "Conflicting diagnostic build")
    catalog = build["level_catalog"]["levels"]
    require(not any(row["id"] == "bafford-entry" for row in catalog),
            "Legacy resident Bafford terrain remains in the diagnostic bundle")
    entry = next((row for row in catalog if row["id"] == "bafford-stream"), None)
    require(entry is not None and entry["content"].get("experimental_stream") is True, "Missing derived stream bundle entry")
    content = entry["content"]
    require(content["bootstrap"]["static_model_count"] == 0 and content["bootstrap"]["collider_count"] == 0,
            "Bafford terrain/collision silently became resident bootstrap arrays")
    regions = content["mission"]["report"]["regions"]
    require(content["mission"]["report"].get("visibility") == "all-regions",
            "Open source street lacks the conservative visibility policy")
    require(content["mission"]["report"].get("chunk_version") == 2,
            "Derived terrain lacks the UV/normal payload version")
    require(content.get("textures", {}).get("textures"), "Derived terrain lost its packed textures")
    require(len(regions) == 2 and sum(region["triangle_count"] for region in regions) > 0 and
            sum(region["collider_count"] for region in regions) > 0, "Missing spatial terrain payloads")
    try:
        from compile_mission import prepare_stream_level
    except ModuleNotFoundError:
        from tools.compile_mission import prepare_stream_level
    with tempfile.TemporaryDirectory(prefix="verify-bafford-source-", dir=ROOT / "build") as temporary:
        current = prepare_stream_level(ROOT / "content/thief_bafford_stream.mission.json", Path(temporary))
    require(content["source_sha256"] == current["source_sha256"], "Derived source/compiler differs from built ROM")
    return build, content, regions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, help="Use an existing --bafford-stream-test ROM")
    parser.add_argument("--log", type=Path, help="Analyze a recorded run with its original ROM snapshot")
    parser.add_argument("--capture", action="store_true", help="Build the separate framebuffer diagnostic")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))))
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output", type=Path, default=ROOT / "build/bafford-stream-verification.json")
    args = parser.parse_args(argv)
    report = dict(passed=False, platform="Ares emulator", started_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  hardware_validation="Original N64 and M64 pending; bounded source street is not the complete mission")
    try:
        require(math.isfinite(args.timeout) and 0 < args.timeout <= 600, "Timeout must be 0..600 seconds")
        require(not args.log or args.rom is not None, "--log requires its original --rom")
        if args.rom:
            rom = args.rom.resolve()
        else:
            try:
                from build import build_rom
            except ModuleNotFoundError:
                from tools.build import build_rom
            rom = build_rom(args.sdk.resolve(), bundle=ROOT / "content/level_bundle.json", bafford_stream_test=True,
                            bafford_stream_test_capture=args.capture)
        metadata = args.log.resolve().parent / "verification-build.json" if args.log else None
        build, content, regions = verify_build(rom, metadata if metadata and metadata.is_file() else None)
        report.update(rom=str(rom), rom_sha256=build["rom_sha256"], source_sha256=build["source_sha256"],
                      build_signature=build["signature"], probe_cook_sha256=content["source_sha256"],
                      mission_sha256=content["mission"]["sha256"], mission_bytes=content["mission"]["bytes"], regions=regions)
        if args.log:
            log = args.log.resolve()
            snapshot = log.parent / rom.name
            require(snapshot.is_file() and hashlib.sha256(snapshot.read_bytes()).hexdigest() == build["rom_sha256"],
                    "Recorded log lacks its matching immutable ROM snapshot")
        else:
            require(args.ares.is_file(), "Ares is not installed at the configured path")
            try:
                from smoke_ares import exercise
            except ModuleNotFoundError:
                from tools.smoke_ares import exercise
            settings = ROOT / ".dev/ares/settings-8mb.bml"
            if not settings.is_file():
                settings = args.ares.parent / "settings.bml"
            run = exercise(args.ares, settings, rom, "bafford-stream", COMPLETE, args.timeout, build["rom_sha256"], memory=8)
            report["run"] = run
            log = ROOT / run["log"]
            (log.parent / "verification-build.json").write_text(json.dumps(build, indent=2) + "\n", encoding="utf-8")
        raw = log.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        report.update(log=str(log), log_sha256=hashlib.sha256(raw).hexdigest())
        require(build.get("bafford_stream_test_capture") == (CAPTURE_COMPLETE in text), "Log capture mode differs from recorded build")
        music_map = json.loads((ROOT / "content/music.json").read_text(encoding="utf-8"))["levels"]
        report.update(decode_log(text, regions, require_music="thief_bafford_stream" in music_map))
        if report["capture_diagnostic"]:
            try:
                from capture_ares import decode_captures
            except ModuleNotFoundError:
                from tools.capture_ares import decode_captures
            from PIL import Image
            frames = decode_captures(text, expected_count=2, expected_dimensions=(320, 240))
            report["captures"] = []
            for ident, (width, height, pixels) in sorted(frames.items()):
                path = ROOT / "build" / ("bafford-stream-" + ("west" if ident == 1 else "east") + ".png")
                Image.frombytes("RGB", (width, height), pixels).save(path)
                report["captures"].append(dict(id=ident, image=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        code = 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        report.update(passed=False, error=str(error))
        code = 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(dict(passed=report["passed"], report=str(args.output), error=report.get("error"))))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
