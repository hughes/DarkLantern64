#!/usr/bin/env python3
"""Summarize existing per-level prepare markers from a completed bundle smoke."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def report(bundle_report, output):
    bundle = json.loads(bundle_report.read_text())
    if not bundle.get("passed"):
        raise ValueError("Load evidence requires a completed bundle regression")
    run = next(r for r in bundle["runs"] if r["name"] == "bundle-switching")
    log = ROOT / run["log"]
    raw = log.read_bytes()
    groups, markers, timings = defaultdict(list), [], None
    geometry = {}
    counts = Counter()
    for index, line in enumerate(raw.decode(errors="replace").splitlines(), 1):
        match = re.match(r"^DL64 ([a-z0-9_]+) (.*)$", line)
        if not match:
            continue
        name, payload = match.groups()
        fields = dict(token.split("=", 1) for token in payload.split() if "=" in token)
        if name.startswith(("scene_", "load_", "level_", "menu_")) or name in ("geometry_ready", "shading_ready", "animation_ready", "rsp_ready", "restart"):
            markers.append({"log_line": index, "kind": name, "fields": fields})
            counts[name] += 1
        if name == "geometry_ready": geometry = fields
        if name == "scene_prepared":
            if timings is not None: raise ValueError("Repeated preparation before level start")
            timings = fields
        if name == "level_start":
            if timings is None: raise ValueError("Level start has no preceding prepare timing")
            groups[fields["id"]].append({"preset": fields["preset"], "prepare_ms": float(timings["ms"]),
                "night_cache_bytes": int(timings["night_cache_bytes"]), "door_states": int(timings["door_states"]),
                "textures": int(timings["textures"]), "heap_bytes": int(fields["heap"].split("/")[0]),
                "geometry": {key: int(geometry[key]) for key in ("models", "meshes", "vertices", "triangles")}})
            timings = None
    if timings is not None: raise ValueError("Preparation has no completed level start")
    levels = {}
    for name, rows in groups.items():
        values = [r["prepare_ms"] for r in rows]
        levels[name] = {"starts": len(rows), "presets": dict(Counter(r["preset"] for r in rows)),
            "prepare_ms": {"minimum": min(values), "average": sum(values)/len(values), "maximum": max(values)},
            "night_cache_bytes": sorted({r["night_cache_bytes"] for r in rows}),
            "door_states": sorted({r["door_states"] for r in rows}), "textures": sorted({r["textures"] for r in rows}),
            "heap_bytes": sorted({r["heap_bytes"] for r in rows}), "geometry": rows[0]["geometry"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    journal = output.with_name(output.stem + "-markers.jsonl")
    journal.write_text("".join(json.dumps(row) + "\n" for row in markers))
    result = {"schema_version": 1, "command": "python tools/smoke_bundle.py",
        "analysis_command": "python tools/report_level_loads.py --bundle-report build/bundle-smoke.json",
        "source_log": log.relative_to(ROOT).as_posix(), "source_log_sha256": hashlib.sha256(raw).hexdigest(),
        "rom_sha256": run["rom_sha256"], "settings_source_sha256": run["settings_source_sha256"],
        "levels": levels, "marker_counts": dict(counts), "marker_journal": journal.relative_to(ROOT).as_posix(),
        "measurement": "Existing scene_prepared CP0 elapsed timer. Includes CPU scene transforms, lighting and texture preparation; ends before gpu_prepare and HUD preparation.",
        "limitations": ["This timer is not total level-load latency: release/synchronization, remaining RSP preparation, presentation and host emulator loading are outside it.",
            "menu_open/menu_resume elapsed fields are simulation time and prove pause/resume continuity; they are not load durations or wall-clock timestamps.",
            "Repeated starts reconstruct both door lighting states. No cache reuse should be inferred from stable memory.",
            "The switching ROM is a diagnostic workload; these load markers are not ordinary gameplay FPS measurements."]}
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-report", type=Path, default=ROOT / "build/bundle-smoke.json")
    parser.add_argument("--output", type=Path, default=ROOT / "build/level-loads-before.json")
    args = parser.parse_args()
    result = report(args.bundle_report.resolve(), args.output.resolve())
    print(json.dumps({key: value["prepare_ms"] for key, value in result["levels"].items()}, indent=2))


if __name__ == "__main__":
    main()
