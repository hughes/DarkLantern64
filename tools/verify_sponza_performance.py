#!/usr/bin/env python3
"""Measure ordinary Sponza gameplay at authored viewpoints, without an FPS promise."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from profile_report import make_report, parse_windows, require, write_report

LEVEL = ROOT / "content/sponza_courtyard.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def records(text, name):
    return [dict(token.split("=", 1) for token in match.split() if "=" in token)
            for match in re.findall(r"^DL64 " + re.escape(name) + r" (.+)$", text, re.MULTILINE)]


def validate_content(manifest):
    """Match the cooker digest without recooking or rewriting shared outputs."""
    digest = hashlib.sha256(LEVEL.read_bytes())
    catalog = manifest["level_catalog"]["levels"]
    require(len(catalog) == 1 and catalog[0]["id"] == "sponza_courtyard", "Unexpected bundled level")
    for dependency in catalog[0]["content"]["dependencies"]:
        uri = dependency["uri"]
        path = (ROOT / uri.removeprefix("code:") if uri.startswith("code:") else LEVEL.parent / uri).resolve()
        require(path.is_relative_to(ROOT) and sha(path) == dependency["sha256"], "Content dependency changed: " + uri)
        digest.update(uri.encode()); digest.update(b"\0"); digest.update(path.read_bytes())
    require(digest.hexdigest() == manifest["source_sha256"], "Cooked content fingerprint differs")


def analyze(raw, log, preset, *, warmup=3, windows=30):
    profile = make_report(raw, log, target_fps=60, skip_windows=warmup, max_windows=windows)
    text = raw.decode(errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    parsed, _ = parse_windows(text)
    selected = parsed[warmup:warmup + windows]
    video = profile.get("video", {})
    starts = records(text, "level_start")
    frame_rows = records(text, "frame")
    segment = text.split(f"DL64 profile_end window={warmup}\n", 1)[-1]
    segment = segment.split(f"DL64 profile_end window={warmup + windows}\n", 1)[0]
    actors = {}
    for row in records(segment, "enemy"):
        actors.setdefault(row["id"], []).append({"position": [float(v) for v in row["position"].split(",")],
                                               "state": row["state"], "patrol_index": int(row["patrol_index"])})
    actor_travel = {key: max((math.dist(rows[0]["position"], row["position"]) for row in rows), default=0)
                    for key, rows in actors.items()}
    terminal = re.search(r"\b(?:caught|complete)=1\b", text) is not None
    checks = {
        "at_least_30_seconds": warmup >= 3 and len(selected) == windows and video.get("seconds", 0) >= 30,
        "ordinary_live_gameplay": profile["debug_states"] == [0] and not terminal and
            all(w["audio_calls"] > 0 and w["slots"]["gameplay"]["ticks"] > 0 for w in selected) and
            not any(token in text for token in ("DL64 capture_", "DL64 replay_", "RSP CRASH |")),
        "exact_frame_samples": profile["exact_frame_samples_available"],
        "reliable_vi_observation": video.get("tracked", False) and 59 <= video.get("native_refresh_hz", 0) <= 61 and
            video.get("max_interval_ms", math.inf) <= 1500 / max(1, video.get("native_refresh_hz", 0)) and
            abs(video.get("presents", 0) - profile["frames"]) <= 3,
        "authored_start": len(starts) == 1 and starts[0].get("id") == "sponza_courtyard" and starts[0].get("preset") == preset,
        "both_guards_active": set(actors) == {"ground-guard", "upper-guard"} and
            all(len(rows) >= 4 and actor_travel[key] > .5 for key, rows in actors.items()),
    }
    work = profile.get("distributions", {}).get("work", {})
    return {"diagnostic_valid": all(checks.values()), "checks": checks, "preset": preset, "profile": profile,
            "terminal_game_state_observed": terminal, "actor_samples": actors, "actor_displacement_metres": actor_travel,
            "heap_bytes": {"sampled_peak": max((int(row["heap"].split("/")[0]) for row in frame_rows), default=None),
                           "sampled_total": max((int(row["heap"].split("/")[1]) for row in frame_rows), default=None)},
            "meets_60fps_sample": all(checks.values()) and work.get("over_budget_frames") == 0 and
                video.get("repeats") == 0 and video.get("max_gap_vis") == 1,
            "scene_work_samples": records(segment, "scene_work"),
            "limitations": ["Ares CP0/VI model; not original hardware or host monitor presentation.",
                "Stationary authored camera, ordinary guards and audio; not a worst-case crowd/combat guarantee.",
                "Work excludes display-buffer wait; VI-origin changes separately measure fresh framebuffer presentation.",
                "RSP workload counters are pre-clip submissions, not pixel visibility proof."]}


def measure(args, preset):
    from build import build_rom
    from smoke_ares import exercise
    source_hash = sha(LEVEL)
    rom = build_rom(args.sdk, level=LEVEL, start_preset=preset, renderer="t3d")
    manifest = json.loads((ROOT / "build" / rom.stem / "build.json").read_text())
    require(manifest["rom_sha256"] == sha(rom), "ROM/manifest hash mismatch")
    require(all(manifest.get(key) is False for key in ("autoplay", "capture", "debug_overlay", "scale_bench", "menu_test")),
            "Ordinary gameplay build required")
    require(all(manifest.get(key, False) is False for key in ("lighting_bake_verify", "disable_lighting_bake")),
            "Ordinary gameplay cannot use lighting bake verification or bypass")
    require(manifest.get("model_culling") is True and manifest.get("start_preset") == preset and
            str(manifest.get("level")).replace("\\", "/") == "content/sponza_courtyard.json", "Incorrect level/start")
    validate_content(manifest)
    settings = ROOT / ".dev/ares/settings-8mb.bml"
    if not settings.is_file(): settings = args.ares.parent / "settings.bml"
    run = exercise(args.ares, settings, rom, "sponza-performance-" + preset,
                   f"DL64 profile_end window={args.warmup + args.windows}", args.timeout, sha(rom))
    log = ROOT / run["log"]
    report = analyze(log.read_bytes(), log, preset, warmup=args.warmup, windows=args.windows)
    require(sha(LEVEL) == source_hash, "Level changed during measurement")
    validate_content(manifest)
    content = manifest["level_catalog"]["levels"][0]["content"]
    report.update(run=run, source_sha256=source_hash,
        build={key: manifest[key] for key in ("signature", "rom_sha256", "rom_bytes", "source_sha256", "static_image_bytes", "renderer", "renderer_dependency")},
        content={key: content[key] for key in ("counts", "compiled_geometry_bytes", "compiled_normal_bytes", "textures")})
    write_report(args.output / (preset + ".json"), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presets", nargs="+", choices=("courtyard", "upper-gallery"), default=["courtyard", "upper-gallery"])
    parser.add_argument("--output", type=Path, default=ROOT / "build/sponza-performance")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))))
    parser.add_argument("--windows", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--workshop", type=Path, default=ROOT / "docs/evidence/guard-60fps.json")
    parser.add_argument("--before", type=Path, default=ROOT / "docs/evidence/sponza-performance-before.json",
                        help="Optional preserved performance baseline; comparisons require unchanged level content")
    args = parser.parse_args()
    require(args.windows > 0 and args.warmup >= 0, "Invalid profile window counts")
    report = {"schema_version": 1, "command": "python tools/verify_sponza_performance.py", "cases": {}}
    before = json.loads(args.before.read_text()) if args.before.is_file() else None
    if before is not None:
        report["before_optimization_reference"] = {
            "path": args.before.resolve().relative_to(ROOT).as_posix() if args.before.resolve().is_relative_to(ROOT) else args.before.name,
            "sha256": sha(args.before), "scope": "Same authored scene and presets; preserved before-optimization ROM and timing evidence."}
    if args.workshop.is_file():
        baseline = json.loads(args.workshop.read_text())
        report["workshop_reference"] = {"evidence_sha256": sha(args.workshop), "passed": baseline["passed"],
            "rom_sha256": baseline["manifest"]["rom_sha256"], "video": baseline["profile"]["video"],
            "work": baseline["profile"]["distributions"]["work"],
            "scope": "Two-guard workshop reference, different geometry and camera; comparative evidence, not an equal-workload speedup."}
    for preset in args.presets:
        case = measure(args, preset)
        if before is not None and preset in before["cases"]:
            previous = before["cases"][preset]
            require(previous["source_sha256"] == case["source_sha256"] and
                    previous["build"]["source_sha256"] == case["build"]["source_sha256"],
                    "Cannot compare optimization after authored level or asset dependencies changed")
            case["before_optimization"] = {"rom_sha256": previous["build"]["rom_sha256"],
                "presented_fps": previous["profile"]["video"]["presented_fps"],
                "work_average_ms": previous["profile"]["distributions"]["work"]["average_ms"]}
            write_report(args.output / (preset + ".json"), case)
        report["cases"][preset] = case
        write_report(args.output / "report.json", report)
        print(json.dumps({"preset": preset, "diagnostic_valid": case["diagnostic_valid"],
            "video": case["profile"].get("video"), "work": case["profile"].get("distributions", {}).get("work"),
            "heap": case["heap_bytes"], "failed_checks": [key for key, value in case["checks"].items() if not value]}, indent=2), flush=True)
    return 0 if all(case["diagnostic_valid"] for case in report["cases"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
