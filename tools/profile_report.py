#!/usr/bin/env python3
"""Summarize complete ROM profiling windows from an Ares stdout log."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TARGET_FPS = 30
VIDEO_FIELDS = {'window', 'tracked', 'vis', 'presents', 'repeats', 'ticks', 'max_gap_vis', 'max_interval_ticks'}
WORKLOAD_NAMES = ('animated', 'full', 'drawn', 'triangles', 'heads')
WORKLOAD_FIELDS = {'window', 'frames', 'geometry_mode'} | {f'{name}_{bound}' for name in WORKLOAD_NAMES for bound in ('min', 'max')}
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
        if key in ('elapsed', 'work'):
            require(re.fullmatch(r'[0-9a-f]+(?:,[0-9a-f]+)*', value) is not None,
                    f'line {line}: {key} must be comma-separated hexadecimal ticks')
            result[key] = [int(v, 16) for v in value.split(',')]
        elif key != "name":
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
    if 'samples' in window:
        samples = window['samples']
        require(len(samples['elapsed']) == len(samples['work']) <= frames,
                f'line {line}: inconsistent sample counts')
        require(all(0 <= work <= elapsed for elapsed, work in zip(samples['elapsed'], samples['work'])),
                f'line {line}: work exceeds elapsed sample')
        if len(samples['elapsed']) == frames:
            require(sum(samples['elapsed']) == total and max(samples['elapsed']) == maximum,
                    f'line {line}: samples disagree with frame totals/maxima')
            require(sum(samples['work']) == total - window['slots']['display_wait']['ticks'],
                    f'line {line}: work samples disagree with display wait')
    if 'video' in window:
        video = window['video']
        require(video['tracked'] in (0, 1) and video['presents'] + video['repeats'] == video['vis'],
                f'line {line}: inconsistent VI counts')
        require(video['max_interval_ticks'] <= video['ticks'] <= video['vis'] * video['max_interval_ticks'],
                f'line {line}: inconsistent VI timing')
    if 'workload' in window:
        workload = window['workload']
        require(workload['geometry_mode'] in (0, 1), f'line {line}: unknown geometry evidence mode')
        require(workload['frames'] <= frames, f'line {line}: excess workload frames')
        for name in WORKLOAD_NAMES:
            require(workload[f'{name}_min'] <= workload[f'{name}_max'],
                    f'line {line}: invalid workload range')


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
        record_start = re.match(r"^DL64 (profile|profile_slot|profile_samples|profile_video|profile_workload|profile_end)(?:\s|$)", line)
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
            expected = {'profile_slot': {'window', 'name', 'ticks', 'max_ticks'},
                        'profile_samples': {'window', 'first', 'count', 'elapsed', 'work'},
                        'profile_video': VIDEO_FIELDS, 'profile_workload': WORKLOAD_FIELDS,
                        'profile_end': {'window'}}[kind]
            record = fields(payload, expected, number)
            require(active is not None, f"line {number}: {kind} outside a window")
            require(record["window"] == active["window"],
                    f"line {number}: mismatched window ID")
            if kind == "profile_slot":
                name = record["name"]
                require(name in SLOTS, f"line {number}: unknown slot {name!r}")
                require(name not in active["slots"], f"line {number}: duplicate slot {name!r}")
                active["slots"][name] = {"ticks": record["ticks"], "max_ticks": record["max_ticks"]}
            elif kind == 'profile_samples':
                samples = active.setdefault('samples', {'elapsed': [], 'work': []})
                require(record['first'] == len(samples['elapsed']), f'line {number}: skipped or repeated sample range')
                require(record['count'] == len(record['elapsed']) == len(record['work']) and record['count'] > 0,
                        f'line {number}: inconsistent sample record')
                for name in ('elapsed', 'work'): samples[name].extend(record[name])
            elif kind in ('profile_video', 'profile_workload'):
                name = kind.removeprefix('profile_')
                require(name not in active, f'line {number}: duplicate {name} record')
                active[name] = record
            else:
                validate_window(active, number)
                windows.append(active)
                active = None
    require(windows, "No complete profiling windows found. Build the instrumented ROM and play for a few seconds.")
    return windows, partial or active is not None


def configuration(text):
    records = re.findall(r'^DL64 profile_enabled (.+)$', text, re.MULTILINE)
    require(len(records) <= 1, 'Repeated profile configuration / multiple boots in one log')
    if not records: return {'version': 1, 'budget_fps': TARGET_FPS}
    values = {}
    for token in records[0].split():
        require(token.count('=') == 1, 'Malformed profile configuration')
        key, value = token.split('=')
        require(key not in values, 'Duplicate profile configuration field')
        values[key] = value
    require(values.get('version') in ('1', '2'), 'Unsupported profiling version')
    require(re.fullmatch(r'[0-9]+', values.get('budget_fps', '')) is not None, 'Invalid configured budget')
    require(1 <= int(values['budget_fps']) <= 240, 'Configured budget must be 1..240 fps')
    return {'version': int(values['version']), 'budget_fps': int(values['budget_fps'])}


def distribution(values, rate, budget):
    ordered = sorted(values)
    return {'samples': len(values), 'average_ms': sum(values) * 1000 / rate / len(values),
            'min_ms': ordered[0] * 1000 / rate, 'max_ms': ordered[-1] * 1000 / rate,
            **{f'p{p}_ms': ordered[math.ceil(len(values)*p/100)-1] * 1000 / rate for p in (50, 95, 99)},
            'over_budget_frames': sum(value * budget > rate for value in values),
            'quantile_method': 'nearest rank of every exact CP0-tick sample'}


def make_report(log_bytes, source_path, *, target_fps=None, skip_windows=0, max_windows=None):
    text = log_bytes.decode('utf-8', errors='replace')
    config = configuration(text)
    target_fps = config['budget_fps'] if target_fps is None else target_fps
    require(isinstance(target_fps, int) and 1 <= target_fps <= 240, 'Target must be 1..240 fps')
    windows, partial = parse_windows(text)
    if config['version'] >= 2:
        require(all(all(name in w for name in ('samples', 'video', 'workload')) for w in windows),
                'Profiling v2 window is missing samples/video/workload')
    require(skip_windows >= 0 and (max_windows is None or max_windows > 0), 'Invalid window selection')
    windows = windows[skip_windows:None if max_windows is None else skip_windows+max_windows]
    require(windows, 'No complete windows after warmup selection')
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
            "percent_of_frame_budget": average_ms * target_fps / 10,
            "percent_of_30fps_budget": average_ms * 3, # preserved for legacy consumers
            "percent_of_frame_elapsed": ticks * 100 / total_ticks if total_ticks else 0.0,
        }

    report = {
        "schema_version": 2,
        "measurement": "Emulated CPU-timer elapsed time; not CPU utilization or host CPU time",
        "source_log": str(Path(source_path).resolve()),
        "source_log_sha256": hashlib.sha256(log_bytes).hexdigest(),
        "source_log_bytes": len(log_bytes),
        "complete_windows": len(windows),
        "selected_window_ids": [w['window'] for w in windows],
        "skipped_warmup_windows": skip_windows,
        "frames": frames,
        "ignored_trailing_partial_window": partial,
        "ticks_per_second": rate,
        "target_fps": target_fps,
        "configured_target_fps": config['budget_fps'],
        "frame_budget_ms": 1000 / target_fps,
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
    exact = all(len(w.get('samples', {}).get('elapsed', [])) == w['frames'] for w in windows)
    report['exact_frame_samples_available'] = exact
    if exact:
        report['distributions'] = {name: distribution([v for w in windows for v in w['samples'][name]], rate, target_fps)
                                   for name in ('elapsed', 'work')}
    if all('video' in w for w in windows):
        video = {name: sum(w['video'][name] for w in windows) for name in ('vis', 'presents', 'repeats', 'ticks')}
        video.update(tracked=all(w['video']['tracked'] for w in windows),
                     max_gap_vis=max(w['video']['max_gap_vis'] for w in windows),
                     max_interval_ms=max(w['video']['max_interval_ticks'] for w in windows)*1000/rate)
        video['seconds'] = video['ticks']/rate
        video['native_refresh_hz'] = video['vis']/video['seconds'] if video['seconds'] else 0
        video['presented_fps'] = video['presents']/video['seconds'] if video['seconds'] else 0
        report['video'] = video
    if all('workload' in w for w in windows):
        modes = sorted({w['workload']['geometry_mode'] for w in windows})
        report['workload'] = {'frames': sum(w['workload']['frames'] for w in windows),
            'geometry_modes': modes, 'full_body_coverage_measured': modes == [0],
            'geometry_evidence': ('CPU post-clip nondegenerate triangle submission' if modes == [0] else
                'RSP model bounds candidates and pre-clip triangle submission; full-body coverage unavailable'),
            **{f'{name}_{bound}': (min if bound == 'min' else max)(w['workload'][f'{name}_{bound}'] for w in windows)
               for name in WORKLOAD_NAMES for bound in ('min', 'max')}}
    return report


def print_report(report):
    print(f"Ares ROM profile: {report['frames']} frames in {report['complete_windows']} complete windows")
    print(f"Debug frames: {report['debug_frames']}; audio callbacks: {report['audio_calls']}")
    print(f"Log: {report['source_log']}")
    print(f"Times are emulated elapsed ms; budget is {report['frame_budget_ms']:.3f} ms at {report['target_fps']} fps, not CPU utilization.")
    print(f"{'Scope':<17} {'Avg ms':>10} {'Max ms':>10} {'% budget':>10} {'% elapsed':>10}")

    def row(name, values):
        print(f"{name:<17} {values['average_ms']:>10.3f} {values['max_ms']:>10.3f} "
              f"{values['percent_of_frame_budget']:>10.2f} {values['percent_of_frame_elapsed']:>10.2f}")

    row("frame elapsed", report["frame"])
    for name, values in sorted(report["slots"].items(), key=lambda item: item[1]["total_ticks"], reverse=True):
        row(name, values)
    if report["ignored_trailing_partial_window"]:
        print("An unfinished trailing profiling window/line was excluded; run again to include it after it finishes.")
    if len(report["debug_states"]) > 1:
        print("This report combines debug-on and debug-off frames. Use separate sessions for comparisons.")
    print("Maxima are worst observed per-frame values, not percentiles; slot maxima do not add to frame maximum.")
    print("Graphics queue stalls remain in submission scopes. Validate conclusions on target hardware.")
    if report.get('video'):
        video = report['video']
        print(f"VI: {video['presented_fps']:.3f} presented fps / {video['native_refresh_hz']:.3f} Hz; {video['repeats']} repeated scans")
    if report.get('distributions'):
        print('Exact work-frame distribution: ' + json.dumps(report['distributions']['work']))


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
    parser.add_argument('--target-fps', type=int, help='Budget override; defaults to ROM configuration (legacy logs: 30)')
    parser.add_argument('--skip-windows', type=int, default=0, help='Exclude complete warmup windows')
    args = parser.parse_args()
    try:
        log = args.log if args.log is not None else latest_log()
        require(log.resolve() != args.output.resolve(), "Output must not overwrite the source log.")
        report = make_report(log.read_bytes(), log, target_fps=args.target_fps, skip_windows=args.skip_windows)
        write_report(args.output, report)
        print_report(report)
        print(f"JSON: {args.output.resolve()}")
    except (OSError, ProfileError) as error:
        print(f"Profile report failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
