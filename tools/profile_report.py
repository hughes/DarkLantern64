#!/usr/bin/env python3
"""Summarize complete ROM profiling windows from an Ares stdout log."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TARGET_FPS = 30
SLOTS = (
    "input", "gameplay", "audio_events", "audio_mix", "cache", "transforms",
    "lighting", "triangles", "hud", "debug_hud", "render_setup", "display_wait", "other",
)
HEADER_FIELDS = {
    "window", "frames", "ticks_per_second", "frame_ticks", "frame_max_ticks",
    "audio_calls", "debug",
}


class ProfileError(ValueError):
    """A profiling record is invalid or no complete window was recorded."""


def require(condition, message):
    if not condition:
        raise ProfileError(message)


def fields(payload, expected, line):
    result = {}
    for token in payload.split():
        require(token.count("=") == 1, f"line {line}: invalid field {token!r}")
        key, value = token.split("=")
        require(key not in result, f"line {line}: duplicate field {key!r}")
        result[key] = value
    require(result.keys() == expected,
            f"line {line}: fields differ: missing {sorted(expected - result.keys())}, "
            f"unknown {sorted(result.keys() - expected)}")
    for key, value in result.items():
        if key != "name":
            require(re.fullmatch(r"[0-9]+", value) is not None,
                    f"line {line}: {key} must be a nonnegative integer")
            result[key] = int(value)
    return result


def validate_window(window, line):
    require(window["slots"].keys() == set(SLOTS),
            f"line {line}: window {window['window']} is missing slots "
            f"{sorted(set(SLOTS) - window['slots'].keys())}")
    frames = window["frames"]
    total, maximum = window["frame_ticks"], window["frame_max_ticks"]
    require(maximum <= total <= frames * maximum,
            f"line {line}: frame total/max/count are inconsistent")
    require(sum(slot["ticks"] for slot in window["slots"].values()) == total,
            f"line {line}: slot totals do not equal frame_ticks")
    for name, slot in window["slots"].items():
        require(slot["max_ticks"] <= slot["ticks"] <= frames * slot["max_ticks"],
                f"line {line}: {name} total/max/frame count are inconsistent")
        require(slot["max_ticks"] <= maximum,
                f"line {line}: {name} maximum exceeds frame maximum")
    require(sum(slot["max_ticks"] for slot in window["slots"].values()) >= maximum,
            f"line {line}: slot maxima cannot account for frame maximum")
    if window["audio_calls"] == 0:
        require(window["slots"]["audio_mix"]["ticks"] == 0,
                f"line {line}: audio_mix has time but no audio calls")


def parse_windows(text):
    """Reject corrupt records; discard only an unfinished final window/line.

    Ordinary emulator and game output can appear between profiling records.
    A complete window requires its explicit end marker. A final line without a
    newline may have been read while Ares was still writing, so it is deferred.
    """
    windows, active = [], None
    partial = False
    rate = None
    for number, raw_line in enumerate(text.splitlines(keepends=True), 1):
        line = raw_line.strip()
        record_start = re.match(r"^DL64 (profile|profile_slot|profile_end)(?:\s|$)", line)
        if record_start is None:
            continue
        if not raw_line.endswith(("\n", "\r")):
            partial = True
            break
        kind, payload = record_start.group(1), line[record_start.end():]
        if kind == "profile":
            require(active is None, f"line {number}: new window before previous window ended")
            active = fields(payload, HEADER_FIELDS, number)
            require(active["window"] == len(windows) + 1,
                    f"line {number}: expected window {len(windows) + 1}, got {active['window']}")
            require(active["frames"] > 0, f"line {number}: frames must be positive")
            require(active["ticks_per_second"] > 0,
                    f"line {number}: ticks_per_second must be positive")
            require(active["debug"] in (0, 1), f"line {number}: debug must be 0 or 1")
            require(rate is None or rate == active["ticks_per_second"],
                    f"line {number}: ticks_per_second changed within the log")
            rate = active["ticks_per_second"]
            active["slots"] = {}
        else:
            expected = {"window", "name", "ticks", "max_ticks"} if kind == "profile_slot" else {"window"}
            record = fields(payload, expected, number)
            require(active is not None, f"line {number}: {kind} outside a window")
            require(record["window"] == active["window"],
                    f"line {number}: mismatched window ID")
            if kind == "profile_slot":
                name = record["name"]
                require(name in SLOTS, f"line {number}: unknown slot {name!r}")
                require(name not in active["slots"], f"line {number}: duplicate slot {name!r}")
                active["slots"][name] = {"ticks": record["ticks"], "max_ticks": record["max_ticks"]}
            else:
                validate_window(active, number)
                windows.append(active)
                active = None
    require(windows, "No complete profiling windows found. Build the instrumented ROM and play for a few seconds.")
    return windows, partial or active is not None


def make_report(log_bytes, source_path):
    windows, partial = parse_windows(log_bytes.decode("utf-8", errors="replace"))
    frames = sum(window["frames"] for window in windows)
    rate = windows[0]["ticks_per_second"]
    total_ticks = sum(window["frame_ticks"] for window in windows)
    debug_frames = {str(state): sum(w["frames"] for w in windows if w["debug"] == state)
                    for state in sorted({w["debug"] for w in windows})}

    def timing(ticks, maximum):
        average_ms = ticks * 1000 / rate / frames
        return {
            "total_ticks": ticks,
            "max_ticks": maximum,
            "average_ms": average_ms,
            "max_ms": maximum * 1000 / rate,
            "percent_of_30fps_budget": average_ms * TARGET_FPS / 10,
            "percent_of_frame_elapsed": ticks * 100 / total_ticks if total_ticks else 0.0,
        }

    return {
        "schema_version": 1,
        "measurement": "Emulated CPU-timer elapsed time; not CPU utilization or host CPU time",
        "source_log": str(Path(source_path).resolve()),
        "source_log_sha256": hashlib.sha256(log_bytes).hexdigest(),
        "source_log_bytes": len(log_bytes),
        "complete_windows": len(windows),
        "frames": frames,
        "ignored_trailing_partial_window": partial,
        "ticks_per_second": rate,
        "target_fps": TARGET_FPS,
        "frame_budget_ms": 1000 / TARGET_FPS,
        "debug_states": [int(state) for state in debug_frames],
        "debug_frames": debug_frames,
        "audio_calls": sum(window["audio_calls"] for window in windows),
        "frame": timing(total_ticks, max(window["frame_max_ticks"] for window in windows)),
        "slots": {
            name: timing(sum(window["slots"][name]["ticks"] for window in windows),
                         max(window["slots"][name]["max_ticks"] for window in windows))
            for name in SLOTS
        },
        "limitations": [
            "Ares timing model; original N64 and M64 hardware validation remains pending.",
            "Slot maxima are worst observed per-frame totals and must not be added together.",
            "Submission scopes include internal queue stalls; this does not time RDP/GPU execution.",
            "Audio callback time is attributed exclusively; other interrupts remain in the interrupted scope.",
            "Profiling and enabled debug display add overhead. Incomplete trailing records are excluded.",
        ],
    }


def print_report(report):
    print(f"Ares ROM profile: {report['frames']} frames in {report['complete_windows']} complete windows")
    print(f"Debug frames: {report['debug_frames']}; audio callbacks: {report['audio_calls']}")
    print(f"Log: {report['source_log']}")
    print("Times are emulated elapsed ms; budget is 33.333 ms at 30 fps, not CPU utilization.")
    print(f"{'Scope':<17} {'Avg ms':>10} {'Max ms':>10} {'% budget':>10} {'% elapsed':>10}")

    def row(name, values):
        print(f"{name:<17} {values['average_ms']:>10.3f} {values['max_ms']:>10.3f} "
              f"{values['percent_of_30fps_budget']:>10.2f} {values['percent_of_frame_elapsed']:>10.2f}")

    row("frame elapsed", report["frame"])
    for name, values in sorted(report["slots"].items(), key=lambda item: item[1]["total_ticks"], reverse=True):
        row(name, values)
    if report["ignored_trailing_partial_window"]:
        print("An unfinished trailing profiling window/line was excluded; run again to include it after it finishes.")
    if len(report["debug_states"]) > 1:
        print("This report combines debug-on and debug-off frames. Use separate sessions for comparisons.")
    print("Maxima are worst observed per-frame values, not percentiles; slot maxima do not add to frame maximum.")
    print("Graphics queue stalls remain in submission scopes. Validate conclusions on target hardware.")


def write_report(output, report):
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=output.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def latest_log(root=ROOT):
    pointer = root / ".dev/ares/latest-session.json"
    if not pointer.exists():
        return root / ".dev/ares/manual-session.log"
    try:
        session = json.loads(pointer.read_text(encoding="utf-8"))
    except (ValueError, OSError) as error:
        raise ProfileError(f"Cannot read latest session pointer {pointer}: {error}") from error
    require(isinstance(session, dict) and isinstance(session.get("log"), str) and session["log"],
            f"Latest session pointer {pointer} must contain a log path")
    log = Path(session["log"])
    return log if log.is_absolute() else root / log


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, nargs="?", help="Ares log; defaults to the latest project play session")
    parser.add_argument("--output", type=Path, default=ROOT / "build/profile-report.json")
    args = parser.parse_args()
    try:
        log = args.log if args.log is not None else latest_log()
        require(log.resolve() != args.output.resolve(), "Output must not overwrite the source log.")
        report = make_report(log.read_bytes(), log)
        write_report(args.output, report)
        print_report(report)
        print(f"JSON: {args.output.resolve()}")
    except (OSError, ProfileError) as error:
        print(f"Profile report failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
