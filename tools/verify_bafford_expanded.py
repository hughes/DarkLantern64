#!/usr/bin/env python3
"""Verify actual walking/jumping into the newly imported Bafford street cells."""
import json
import math
import re

try:
    from verify_bafford_stream import main as run_verifier, require, integer, vector, music_evidence, verify_resources, expected_slot_bytes, verify_stream_view
except ModuleNotFoundError:
    from tools.verify_bafford_stream import main as run_verifier, require, integer, vector, music_evidence, verify_resources, expected_slot_bytes, verify_stream_view

PREFIX = "DL64 bafford_expanded "
COMPLETE = "DL64 bafford_expanded_test_complete"
CAPTURE_COMPLETE = "DL64 bafford_expanded_capture_complete views=2"
NAMES = ("start", "west_center", "east_approach", "jump_east_takeoff", "jump_east_air",
         "bridge_east_landed", "bridge_east_edge", "east_street", "west_approach",
         "jump_west_takeoff", "jump_west_air", "bridge_west_landed", "bridge_west_edge",
         "west_street", "west_return")
WAYPOINTS = {"west_center": (-6.522049, -2.4384), "east_approach": (7.8, -2.4384),
             "bridge_east_landed": (9.8, -2.1336), "bridge_east_edge": (11.5, -2.1336),
             "east_street": (15.1, -2.4384), "west_approach": (12.3, -2.4384),
             "bridge_west_landed": (10.8, -2.1336), "bridge_west_edge": (8.8, -2.1336),
             "west_street": (7.8, -2.4384), "west_return": (-6.522049, -2.4384)}


def decode_log(text, regions=None, require_music=False, expected_level="bafford-expanded",
               expected_textures=None, bootstrap_models=0):
    require(not any(marker in text for marker in ("RSP CRASH |", "ASSERTION FAILED:", "bafford_expanded_test_failed")),
            "ROM reported a runtime failure")
    lines = text.splitlines()
    menus = [i for i, line in enumerate(lines) if line.startswith("DL64 menu_ready ") and "mode=menu" in line]
    starts = [(i, line) for i, line in enumerate(lines) if line.startswith("DL64 level_start ")]
    require(len(menus) == 1 and len(starts) == 1 and menus[0] < starts[0][0] and
            f"id={expected_level} " in starts[0][1], "Diagnostic did not select the expanded mission through the menu")
    rows, indices, terminals = [], [], []
    expected_ids = {region["id"] for region in regions} if regions else {1, 2}
    working_view = verify_stream_view(text, regions)
    for index, line in enumerate(lines):
        if line.startswith(PREFIX):
            row = json.loads(line[len(PREFIX):])
            point = vector(row["player"], "player")
            integer(row["tick"], "tick", 1)
            require(type(row["elapsed"]) in (int, float) and math.isfinite(row["elapsed"]) and row["elapsed"] > 0,
                    "Invalid simulation time")
            require(-6.7 <= point[0] <= 15.3 and 48.35 <= point[2] <= 49.95 and -2.4684 <= point[1] <= -1.35,
                    "Player left the audited bridge corridor")
            require(row["grounded"] in (0, 1) and row["collision_clear"] == 1, "Player lost body clearance")
            require(type(row["vertical_velocity"]) in (int, float) and math.isfinite(row["vertical_velocity"]) and
                    -18 <= row["vertical_velocity"] <= 4.7, "Invalid vertical velocity")
            integer(row["jump_inputs"], "jump_inputs")
            for field in ("heap_used", "models", "colliders", "textures"):
                integer(row[field], field, 1)
            verify_resources(row, regions, expected_textures, bootstrap_models)
            stream = row["stream"]
            require(type(stream["current"]) is int and stream["current"] in expected_ids, "Invalid current region")
            require(isinstance(stream["resident"], list) and len(stream["resident"]) == 2 and
                    all(type(ident) is int for ident in stream["resident"]) and set(stream["resident"]) == expected_ids,
                    "Expanded open street lost a visible region")
            for field in ("reserved", "allocated", "completed_loads", "retired", "readbytes", "stalls"):
                integer(stream[field], field)
            require(0 < stream["reserved"] <= stream["allocated"] and stream["completed_loads"] == 2 and stream["retired"] == 0,
                    "Expanded stream violated its bounded conservative working set")
            if regions:
                region = next(region for region in regions if region["id"] == stream["current"])
                lo, hi = region["bounds"]
                require(all(lo[i] <= point[i] <= hi[i] for i in range(3)), "Player outside current region coverage")
                require(stream["reserved"] == stream["readbytes"] == sum(region["chunk_bytes"] for region in regions),
                        "Expanded chunks were not read and reserved exactly once")
                require(stream["allocated"] == expected_slot_bytes(regions), "Slot allocation differs from cooked region maximum")
            rows.append(row)
            indices.append(index)
        elif line.startswith(COMPLETE):
            terminals.append((index, dict(re.findall(r"([a-z_]+)=([^ ]+)", line))))
    require(tuple(row.get("checkpoint") for row in rows) == NAMES, "Missing, duplicated or reordered jump checkpoint")
    require(len(terminals) == 1 and starts[0][0] < indices[0] < indices[-1] < terminals[0][0],
            "Completion/checkpoints are outside the launched route")
    terminal = terminals[0][1]
    require(all(terminal.get(field) == "1" for field in ("passed", "input_only", "coverage", "collision_clear")) and
            terminal.get("bridge_jumps") == "2" and int(terminal.get("audio_calls", "0")) > 0,
            "Missing successful physical jump/audio assertions")
    for a, b in zip(rows, rows[1:]):
        seconds = b["elapsed"] - a["elapsed"]
        require(a["tick"] < b["tick"] and seconds > 0 and abs(seconds - (b["tick"] - a["tick"]) / 60) < .01,
                "Route time differs from 60 Hz input ticks")
        distance = math.hypot(b["player"][0] - a["player"][0], b["player"][2] - a["player"][2])
        require(distance <= 2.15 * seconds + .02, "Route exceeds ordinary horizontal walking speed")
        require(abs(b["player"][1] - a["player"][1]) <= 4.7 * seconds + .02, "Vertical position jumped discontinuously")
        require(a["stream"]["readbytes"] <= b["stream"]["readbytes"] and a["stream"]["stalls"] <= b["stream"]["stalls"],
                "Stream counters regressed")
    require([row["jump_inputs"] for row in rows] == [0, 0, 0] + [1] * 6 + [2] * 6, "Wrong jump input count/order")
    named = {row["checkpoint"]: row for row in rows}
    for name, (x, y) in WAYPOINTS.items():
        row = named[name]
        require(math.hypot(row["player"][0] - x, row["player"][2] - 48.5) < .12 and
                abs(row["player"][1] - y) < .015 and row["grounded"] == 1,
                "Wrong supported waypoint: " + name)
    for direction in ("east", "west"):
        takeoff = named["jump_" + direction + "_takeoff"]
        air = named["jump_" + direction + "_air"]
        require(takeoff["grounded"] == 1 and abs(takeoff["player"][1] + 2.4384) < .015,
                "Jump did not start on the lower source floor")
        require(air["grounded"] == 0 and air["vertical_velocity"] > 0 and
                8.382 < air["player"][0] < 12.0396 and -2.0336 < air["player"][1] < -1.35,
                "Missing rising airborne crossing into source bridge cell3831")
    require(len({row["stream"]["allocated"] for row in rows}) == 1 and len({row["stream"]["reserved"] for row in rows}) == 1,
            "Slot/payload allocation changed during traversal")
    require(len({(row["models"], row["colliders"], row["textures"]) for row in rows}) == 1, "Working geometry changed")
    steady = [named[name]["heap_used"] for name in ("east_street", "west_approach", "bridge_west_landed", "west_street", "west_return")]
    require(len(set(steady)) == 1, "Heap grew after initial traversal/jump warmup")
    route = [rows[0]["stream"]["current"]]
    for row in rows[1:]:
        if row["stream"]["current"] != route[-1]:
            route.append(row["stream"]["current"])
    require(len(route) == 3 and route[0] == route[2] and route[0] != route[1], "No west/east/west region traversal")
    capture = CAPTURE_COMPLETE in lines
    require(("DL64 capture_begin " in text) == capture, "Framebuffer export/completion mismatch")
    music = music_evidence(text) if "DL64 music cue=" in text else None
    require(not require_music or music is not None, "Assigned music lacks playback evidence")
    require(capture or music is None or music["max_underruns"] == 0, "Ordinary expanded route has music underruns")
    return dict(passed=True, functional_passed=True, checkpoints=rows, terminal=terminal, route=route,
                bridge_jumps=2, newly_walked_source_cells=[4366, 3831, 4987], loads=2, retirements=0,
                allocated_slot_bytes=rows[0]["stream"]["allocated"], reserved_chunk_bytes=rows[0]["stream"]["reserved"],
                working_view=working_view,
                steady_heap_samples_bytes=steady, capture_diagnostic=capture, music=music,
                audio_continuity_passed=music["max_underruns"] == 0 if music else None,
                timing_scope="Framebuffer export diagnostic; excludes ordinary audio/frame acceptance" if capture else
                             "Ordinary walking/jumping controls; expanded source street, emulator only")


def main(argv=None):
    return run_verifier(argv, decoder=decode_log, expanded=True)


if __name__ == "__main__":
    raise SystemExit(main())
