"""Reproducible N64 scale experiments, measured serially in isolated Ares.

Guard/query microbenchmarks exclude rendering. Render fixtures add hidden room
shells or visible static loot models; they do not add functional guards/items.
Render baselines omit decorative, non-colliding imported asset-pack props, so
adding showcase art does not silently consume the benchmark's workload budget.
Structural geometry and practical lights remain part of the measured scene.
Canonical source content and the user's emulator settings remain unchanged.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
CASES = ("baseline", "hidden4", "hidden12", "loot8", "loot32", "lights1", "lights8", "lights16")


def parse_microbench(text):
    headers = [line for line in text.splitlines() if line.startswith("DL64 scale_begin ")]
    endings = [line for line in text.splitlines() if line.startswith("DL64 scale_complete ")]
    if len(headers) != 1 or len(endings) != 1 or "DL64 scale_boot memory=8388608" not in text:
        raise ValueError("Scale benchmark lacks unique header/completion or 8 MiB boot evidence")
    metadata = dict(token.split("=", 1) for token in shlex.split(headers[0])[2:])
    result, identities = [], set()
    required = {"name", "count", "samples", "units_per_sample", "ticks", "max_ticks",
                "audio_ticks", "ticks_per_second", "colliders", "lights", "substeps", "checksum"}
    for line in text.splitlines():
        if not line.startswith("DL64 scale_case "):
            continue
        tokens = line.split()[2:]
        fields = dict(token.split("=", 1) for token in tokens)
        if set(fields) != required or len(tokens) != len(fields):
            raise ValueError("Malformed scale benchmark fields")
        for key in required - {"name", "checksum"}:
            if not re.fullmatch(r"[0-9]+", fields[key]):
                raise ValueError("Invalid scale benchmark integer")
            fields[key] = int(fields[key])
        fields["checksum"] = float(fields["checksum"])
        identity = (fields["name"], fields["count"])
        if (identity in identities or not math.isfinite(fields["checksum"]) or
            min(fields["samples"], fields["units_per_sample"], fields["ticks_per_second"]) <= 0 or
            not fields["max_ticks"] <= fields["ticks"] <= fields["samples"] * fields["max_ticks"]):
            raise ValueError("Inconsistent scale benchmark totals or duplicate identity")
        identities.add(identity)
        fields["average_ms"] = fields["ticks"] / fields["samples"] / fields["ticks_per_second"] * 1000
        fields["max_ms"] = fields["max_ticks"] / fields["ticks_per_second"] * 1000
        fields["ms_per_unit"] = fields["average_ms"] / fields["units_per_sample"]
        result.append(fields)
    match = re.fullmatch(r"DL64 scale_complete cases=(\d+) checksum=([-+0-9.eE]+)", endings[0])
    expected = ({(name, n) for name in ("guard_patrol", "guard_chase") for n in (1, 2, 4, 8, 16)} |
                {(name, n) for name in ("surface_lights", "visibility_lights") for n in (1, 4, 8, 16)} |
                {("moving_light_vertices", n) for n in (128, 512)} |
                {("light_collider_scan", n) for n in (0, 16, 64, 256)})
    if metadata.get("version") == "2":
        expected |= ({("surface_lights_clear", n) for n in (1, 4, 8, 16)} |
                     {("moving_light_vertices_clear", n) for n in (128, 512)})
    elif metadata.get("version") != "1":
        raise ValueError("Unsupported scale benchmark version")
    if not match or int(match[1]) != len(result) or identities != expected or not math.isfinite(float(match[2])):
        raise ValueError("Missing or unknown scale benchmark cases")
    return {"metadata": metadata, "cases": result}


def benchmark_base(base):
    """Return the structural scene used for controlled render workloads.

    Imported models with collision or gameplay roles remain. This is a
    benchmark recipe, not a runtime optimization or a claim that decorative
    models are free. Reports retain both authored and recipe source hashes.
    """
    data = copy.deepcopy(base)
    imported = {asset["id"] for asset in data["assets"] if asset["uri"].startswith("assets/")}
    data["entities"] = [entity for entity in data["entities"] if not
                        (entity["kind"] == "static" and entity.get("model") in imported and "collider" not in entity)]
    models = {entity["model"] for entity in data["entities"] if "model" in entity}
    materials = {entity["material"] for entity in data["entities"] if "material" in entity}
    data["assets"] = [asset for asset in data["assets"] if asset["id"] in models]
    data["materials"] = [material for material in data["materials"] if material["id"] in materials]
    data.pop("test_starts", None)
    return data


def make_fixture(base, name):
    if name not in CASES:
        raise ValueError("Unknown scale fixture")
    data = benchmark_base(base)
    data["title"] = "DarkLantern64 scale study - " + name
    if name.startswith("hidden"):
        count = int(name[6:])
        if not any(a["id"] == "night-block" for a in data["assets"]):
            raise ValueError("Room-shell fixtures require the courtyard night-block asset")
        # Six boxes form each closed shell. All are behind the unchanged spawn
        # camera. Their collision proxies remain in the current global scan.
        pieces = [([0, -.2, 0], [10, .4, 10]), ([0, 3.2, 0], [10, .4, 10]),
                  ([-5, 1.5, 0], [.4, 3, 10]), ([5, 1.5, 0], [.4, 3, 10]),
                  ([0, 1.5, -5], [10, 3, .4]), ([0, 1.5, 5], [10, 3, .4])]
        for room in range(count):
            for part, (offset, scale) in enumerate(pieces):
                position = [offset[0], offset[1], offset[2] + 30 + room * 12]
                data["entities"].append({"id": f"study-room-{room}-{part}", "kind": "static",
                    "model": "night-block", "material": "mat-stone",
                    "transform": {"position": position, "rotation": [0, 0, 0], "scale": scale},
                    "collider": {"shape": "box", "center": [0, 0, 0], "half_size": [.5, .5, .5]}})
    elif name.startswith("loot"):
        count = int(name[4:])
        item = next(e for e in data["entities"] if e["kind"] == "objective")
        spawn = next(e for e in data["entities"] if e["kind"] == "spawn")
        x, y, z = spawn["transform"]["position"]
        yaw = math.radians(spawn["transform"]["rotation"][1])
        for index in range(count):
            sideways = ((index % 8) - 3.5) * .4
            forward = 2.5 + (index // 8) * .55
            position = [x + math.sin(yaw) * forward + math.cos(yaw) * sideways,
                        y + .4, z + math.cos(yaw) * forward - math.sin(yaw) * sideways]
            data["entities"].append({"id": f"study-loot-{index}", "kind": "static",
                "model": item["model"], "material": item["material"],
                "transform": {"position": position, "rotation": [0, index * 13, 0], "scale": [.3, .3, .3]}})
    elif name.startswith("lights"):
        count = int(name[6:])
        authored = [e for e in data["entities"] if e["kind"] == "light"]
        data["entities"] = [e for e in data["entities"] if e["kind"] != "light"]
        # Repeat the existing local positions, preserving total intensity.
        # The new lights have no decorative models: isolate source-list cost.
        for index in range(count):
            original = authored[index % len(authored)]
            light = {k: copy.deepcopy(v) for k, v in original.items() if k not in ("model", "material")}
            light["id"] = f"study-light-{index}"
            repetitions = sum(i % len(authored) == index % len(authored) for i in range(count))
            light["intensity"] /= repetitions
            data["entities"].append(light)
    return data


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", type=Path, default=ROOT / "content/moonlit_courtyard.json")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))))
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--micro-only", action="store_true")
    parser.add_argument("--skip-micro", action="store_true")
    parser.add_argument("--compare-culling", action="store_true", help="Run every render fixture with culling enabled and disabled")
    parser.add_argument("--windows", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    from build import build_rom, scene_paths
    from compile_level import validate
    from smoke_ares import exercise
    from profile_report import make_report
    report = {"version": 1, "passed": False, "measurement": "Ares emulated CPU timer; hardware validation pending",
              "render_cases": [], "limitations": ["Guard benchmarks exclude rendering, animation, inter-guard behavior and player visibility queries.",
              "Room fixtures are hidden six-box shells, not connected navigable rooms or portal visibility tests.",
              "Loot fixtures are static drawn models, not additional interactive pickups.",
              "Local light fixtures test current fixed sources and startup caches, not freely moving lights.",
              "Render fixtures exclude decorative non-colliding asset-pack props; microbenchmarks use the authored level.",
              "Historical results describe their recorded source hashes; scene changes require new measurements for comparison."]}
    output = ROOT / "build/scale-study" / uuid.uuid4().hex
    try:
        if not 1 <= args.windows <= 30 or not math.isfinite(args.timeout) or not 0 < args.timeout <= 600:
            raise ValueError("Use 1-30 windows and a finite timeout from 0 to 600 seconds")
        if args.micro_only and args.skip_micro:
            raise ValueError("Cannot combine --micro-only with --skip-micro")
        source, _, _ = scene_paths(args.level)
        raw = source.read_bytes()
        base = json.loads(raw)
        report["source"] = str(source.relative_to(ROOT))
        report["source_sha256"] = hashlib.sha256(raw).hexdigest()
        render_base = benchmark_base(base)
        render_ids = {entity["id"] for entity in render_base["entities"]}
        report["render_baseline"] = {
            "policy": "structural scene; omit decorative non-colliding imported props and test starts",
            "excluded_entity_ids": [entity["id"] for entity in base["entities"] if entity["id"] not in render_ids],
            "entities": len(render_base["entities"]),
            "recipe_sha256": hashlib.sha256(json.dumps(render_base, sort_keys=True).encode()).hexdigest()}
        settings = ROOT / ".dev/ares/settings-8mb.bml"
        if not settings.is_file():
            settings = args.ares.parent / "settings.bml"

        def execute(rom, label, success):
            return exercise(args.ares, settings, rom, label, success, args.timeout,
                            hashlib.sha256(rom.read_bytes()).hexdigest())

        if not args.skip_micro:
            rom = build_rom(args.sdk, level=source, scale_bench=True)
            run = execute(rom, "scale-micro", "DL64 scale_complete")
            report["micro"] = dict(parse_microbench((ROOT / run["log"]).read_text()), run=run)
            write_json(output / "results.json", report)
            print(f"[scale] {len(report['micro']['cases'])} CPU workload cases complete", flush=True)
        if not args.micro_only:
            for name in dict.fromkeys(args.cases):
                fixture = make_fixture(base, name)
                state = validate(fixture, ROOT / "content")
                write_json(output / "cases" / (name + ".json"), fixture)
                payload = json.dumps(fixture, indent=2) + "\n"
                temporary = ROOT / "content" / ("scale_" + name + "_" + output.name[:8] + ".json")
                with temporary.open("x") as file:
                    file.write(payload)
                try:
                    for culling in ((False, True) if args.compare_culling else (True,)):
                        rom = build_rom(args.sdk, level=temporary, disable_model_cull=not culling)
                        run = execute(rom, "scale-" + name, f"DL64 profile_end window={args.windows}")
                        log = ROOT / run["log"]
                        profile = make_report(log.read_bytes(), log)
                        counters = [dict(zip(("models", "visible", "camera_vertices"), map(int, m)))
                            for m in re.findall(r"DL64 scene_work models=(\d+) visible=(\d+) camera_vertices=(\d+)", log.read_text()) if int(m[2]) > 0]
                        build = json.loads((ROOT / "build" / rom.stem / "build.json").read_text())
                        entry = {"case": name, "culling": culling, "run": run, "profile": profile,
                            "work_samples": counters, "models": len(state["models"]), "colliders": len(state["colliders"]),
                            "lights": len(state["kinds"]["light"]), "static_image_bytes": build["static_image_bytes"],
                            "source_sha256": build["source_sha256"]}
                        report["render_cases"].append(entry)
                        write_json(output / "results.json", report)
                        print(f"[scale] {name} culling={culling}: {profile['frame']['average_ms']:.2f} ms/frame", flush=True)
                finally:
                    # Remove only the uniquely created source file, and retain
                    # it if someone edited it while the experiment ran.
                    if temporary.read_text() == payload:
                        temporary.unlink()
        report["passed"] = source.read_bytes() == raw
        if not report["passed"]:
            raise ValueError("Canonical source changed during the scale study")
        write_json(output / "results.json", report)
        write_json(ROOT / "build/scale-study/latest.json", {"report": str(output / "results.json")})
        print(f"Scale study: {output / 'results.json'}")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        report["error"] = str(error)
        write_json(output / "results.json", report)
        print(f"Scale study failed: {error}; partial results: {output}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
