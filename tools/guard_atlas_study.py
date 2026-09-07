"""Preserve and compare ordinary guard-atlas builds without touching live apps.

Each phase owns a private source/tool/ROM snapshot. Capture and performance use
those copied official harnesses, so artists can change the canonical project
while the previous atlas is measured. Visibility review remains an explicit
separate step after inspecting the generated images.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, indent=2) + "\n").encode())


def source_inputs(root):
    paths = [path for folder in ("src", "tools", "content") for path in (root / folder).rglob("*")
             if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".blend", ".blend1")]
    paths += [root / "dependencies.json", root / "art/guard.blend"]
    return {path.relative_to(root).as_posix(): sha(path) for path in paths}


def checked(stage):
    snapshot = json.loads((stage / "snapshot.json").read_text())
    for name, digest in snapshot["files"].items():
        path = (stage / name).resolve()
        if not path.is_relative_to(stage) or sha(path) != digest:
            raise ValueError("Immutable study input changed: " + name)
    return snapshot


def guard_texture(stage, manifest):
    """Resolve the shared material rather than assuming the original atlas name."""
    pack = json.loads((stage / "content/assets/guard/pack.json").read_text())
    materials = [row for row in pack["materials"] if row["id"] == "mat-guard"]
    if len(materials) != 1:
        raise ValueError("Expected one shared guard material")
    uri = materials[0]["texture"]["uri"]
    textures = [row for row in manifest["textures"]["textures"] if row["uri"] == uri]
    if len(textures) != 1:
        raise ValueError("Expected one cooked shared guard atlas")
    path = (stage / "content" / uri).resolve()
    if not path.is_relative_to((stage / "content").resolve()):
        raise ValueError("Guard atlas escapes preserved content")
    if sha(path) != textures[0]["source_sha256"]:
        raise ValueError("Guard atlas differs from its cooked manifest")
    return textures[0]


def freeze(stage, sdk, with_sponza=False):
    if stage.exists():
        raise ValueError("Refusing to overwrite a preserved phase; use a new output directory")
    sys.path.insert(0, str(ROOT / "tools"))
    from build import build_rom
    inputs = source_inputs(ROOT)
    level = ROOT / "content/animation_workshop.json"
    roms = {mode: build_rom(sdk, level=level, start_preset="two-guards" if mode == "ordinary" else None,
                            capture=mode == "capture", renderer="t3d") for mode in ("ordinary", "capture")}
    if with_sponza:
        for preset in ("courtyard", "upper-gallery"):
            roms["sponza-" + preset] = build_rom(sdk, level=ROOT / "content/sponza_courtyard.json", start_preset=preset, renderer="t3d")
    stage.mkdir(parents=True)
    for folder in ("src", "tools", "content"):
        shutil.copytree(ROOT / folder, stage / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.blend", "*.blend1"))
    shutil.copy2(ROOT / "dependencies.json", stage / "dependencies.json")
    (stage / "art").mkdir()
    shutil.copy2(ROOT / "art/guard.blend", stage / "art/guard.blend")
    settings = stage / ".dev/ares"
    settings.mkdir(parents=True)
    shutil.copy2(ROOT / ".dev/ares/settings-8mb.bml", settings / "settings-8mb.bml")
    for mode, rom in roms.items():
        work = ROOT / "build" / rom.stem
        destination = stage / "build" / rom.stem
        shutil.copytree(work, destination)
        shutil.copy2(rom, stage / "build" / rom.name)
        manifest = json.loads((destination / "build.json").read_text())
        if sha(stage / "build" / rom.name) != manifest["rom_sha256"]:
            raise ValueError("ROM changed while freezing phase")
        if not (stage / "tiny3d").exists():
            dependency = manifest["renderer_dependency"]
            shutil.copytree(Path(dependency["include"]), stage / "tiny3d/src")
            shutil.copy2(Path(dependency["library"]), stage / "tiny3d/libt3d.a")
            if sha(stage / "tiny3d/libt3d.a") != dependency["library_sha256"]:
                raise ValueError("Tiny3D dependency changed while freezing phase")
    if inputs != source_inputs(ROOT) or inputs != source_inputs(stage):
        raise ValueError("Source/art changed during freeze; this incomplete phase is not valid evidence")
    snapshot = {"source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "scope": "Immutable character art, content, source, copied official tools, ordinary/capture ROMs, exact objects, sprite assets and Tiny3D dependency.",
        "roms": {mode: {"rom": "build/" + rom.name, "manifest": "build/" + rom.stem + "/build.json"} for mode, rom in roms.items()},
        "files": {path.relative_to(stage).as_posix(): sha(path) for path in sorted(stage.rglob("*")) if path.is_file()}}
    write(stage / "snapshot.json", snapshot)
    checked(stage)
    return snapshot


def capture(stage, ares, timeout):
    snapshot = checked(stage)
    # This command runs in a fresh process: imports resolve to the immutable
    # official tools, whose ROOT is this private snapshot rather than live art.
    sys.path.insert(0, str(stage / "tools"))
    from smoke_ares import exercise
    from capture_ares import decode_captures, validate_views
    from PIL import Image
    level = stage / "content/animation_workshop.json"
    views = validate_views(json.loads(level.read_text()))
    rom = stage / snapshot["roms"]["capture"]["rom"]
    manifest = json.loads((stage / snapshot["roms"]["capture"]["manifest"]).read_text())
    if sha(rom) != manifest["rom_sha256"] or not manifest["capture"]:
        raise ValueError("Capture ROM differs from its preserved manifest")
    run = exercise(ares, stage / ".dev/ares/settings-8mb.bml", rom, "guard-atlas-capture",
                   "DL64 capture_complete", timeout, sha(rom))
    log = stage / run["log"]
    frames = decode_captures(log.read_text(), expected_count=len(views), expected_dimensions=(320, 240))
    atlas = guard_texture(stage, manifest)
    result = {"passed": True, "phase": stage.name, "run": run, "log_sha256": sha(log),
              "source_sha256": sha(level), "asset_sha256": sha(stage / "content/assets/guard/guard.character.json"),
              "atlas_uri": atlas["uri"], "atlas_sha256": atlas["source_sha256"],
              "snapshot_sha256": sha(stage / "snapshot.json"), "captures": [],
              "scope": "Separate fixed-phase capture ROM; raw RDP framebuffer before VI filtering, not performance timing."}
    for index, (width, height, pixels) in sorted(frames.items()):
        view = views[index - 1]
        if view["id"] not in ("two-guards-idle", "two-guards-walk"):
            continue
        image = stage / "captures" / (view["id"] + ".png")
        image.parent.mkdir(exist_ok=True)
        Image.frombytes("RGB", (width, height), pixels).save(image)
        result["captures"].append({"view": view, "image": image.relative_to(stage).as_posix(), "sha256": sha(image)})
    if len(result["captures"]) != 2:
        raise ValueError("Both fixed two-guard captures are required")
    checked(stage)
    write(stage / "captures.json", result)
    return result


def geometry_identity(character):
    """Ignore UV seams/index ordering, retain winding, positions, normals and bones."""
    mesh = character["mesh"]
    rows = []
    for start in range(0, len(mesh["indices"]), 3):
        corners = []
        for index in mesh["indices"][start:start + 3]:
            corners.append(tuple(round(value, 7) for value in (*mesh["vertices"][index], *mesh["normals"][index])) +
                           (character["bones"][mesh["joints"][index]]["id"],))
        rows.append(min(tuple(corners[i:] + corners[:i]) for i in range(3)))
    return hashlib.sha256(json.dumps(sorted(rows), separators=(",", ":")).encode()).hexdigest()


def phase_summary(stage):
    snapshot = checked(stage)
    report = json.loads((stage / "measurement.json").read_text())
    captures = json.loads((stage / "captures.json").read_text())
    profile = report["profile"]
    video = profile.get("video", {})
    timing_only = {"16.667ms_work_budget", "native_vi_presentation"}
    validity = all(row["passed"] for row in report["checks"] if row["name"] not in timing_only)
    validity = validity and video.get("tracked") and 59 <= video.get("native_refresh_hz", 0) <= 61 and (
        video.get("max_interval_ms", math.inf) <= 1500 / max(1, video.get("native_refresh_hz", 0))) and (
        abs(video.get("presents", 0) - profile["frames"]) <= 2)
    manifest = report["manifest"]
    if sha(stage / snapshot["roms"]["ordinary"]["rom"]) != manifest["rom_sha256"]:
        raise ValueError("Measurement ROM changed")
    if captures["source_sha256"] != report["source_sha256"] or captures["asset_sha256"] != report["asset_sha256"]:
        raise ValueError("Phase captures are not matched to the measured content")
    for row in captures["captures"]:
        if sha(stage / row["image"]) != row["sha256"]:
            raise ValueError("Phase capture image changed")
    content = manifest["level_catalog"]["levels"][0]["content"]
    textures = manifest["textures"]
    guard = guard_texture(stage, manifest)
    if captures["atlas_sha256"] != guard["source_sha256"]:
        raise ValueError("Capture atlas metadata differs from the measured shared material")
    character = json.loads((stage / "content/assets/guard/guard.character.json").read_text())
    character_report = next(iter(content["characters"].values()))
    log = stage / report["run"]["log"]
    if sha(log) != profile["source_log_sha256"]:
        raise ValueError("Raw performance log changed")
    text = log.read_text().replace("\r\n", "\n").replace("\r", "\n")
    segment = text.split("DL64 profile_end window=3\n", 1)[-1].split("DL64 profile_end window=33\n", 1)[0]
    def records(name, whole=False):
        return [dict(item.split("=", 1) for item in line.split()[2:] if "=" in item)
                for line in (text if whole else segment).splitlines() if line.startswith("DL64 " + name + " ")]
    uploads = [int(row["uploads"]) for row in records("materials")]
    heaps = [int(row["heap"].split("/")[0]) for row in records("frame")]
    if not uploads or not heaps:
        raise ValueError("Missing sampled texture-upload or memory telemetry")
    texture_fields = ("uri", "width", "height", "format", "decoded_bytes", "pixel_bytes", "palette_bytes", "decoded_total_bytes",
                      "sprite_bytes", "source_sha256", "recipe_sha256", "sprite_sha256", "tmem_pixel_bytes", "tmem_palette_reserved_bytes", "tmem_bytes")
    texture = {key: guard[key] for key in texture_fields if key in guard}
    texture.setdefault("pixel_bytes", texture["decoded_bytes"])
    texture.setdefault("palette_bytes", 0)
    texture.setdefault("decoded_total_bytes", texture["pixel_bytes"] + texture["palette_bytes"])
    texture.setdefault("tmem_bytes", texture["pixel_bytes"])
    return {"diagnostic_valid": bool(validity), "strict_60fps_passed": report["passed"],
            "measurement_sha256": sha(stage / "measurement.json"), "snapshot_sha256": sha(stage / "snapshot.json"),
            "rom_sha256": manifest["rom_sha256"], "capture_rom_sha256": captures["run"]["rom_sha256"],
            "settings_source_sha256": report["run"]["settings_source_sha256"], "compiler": manifest["compiler"],
            "renderer_library_sha256": manifest["renderer_dependency"]["library_sha256"],
            "log_sha256": sha(log), "level_sha256": sha(stage / "content/animation_workshop.json"),
            "gameplay_source_sha256": sha(stage / "src/game.c"), "enemy_types_sha256": sha(stage / "src/enemy_types.def"),
            "character_sha256": sha(stage / "content/assets/guard/guard.character.json"),
            "geometry_identity": geometry_identity(character),
            "animation_identity": hashlib.sha256(json.dumps({key: character[key] for key in ("bones", "clips", "sockets")}, sort_keys=True).encode()).hexdigest(),
            "guard_geometry": {"vertices": len(character["mesh"]["vertices"]), "triangles": len(character["mesh"]["indices"]) // 3,
                "bones": len(character["bones"]), "compiled_geometry_bytes": character_report["geometry_bytes"],
                "attribute_splits": character_report.get("vertex_attribute_splits")},
            "guard_texture": texture, "level_texture_count": len(textures["textures"]),
            "level_texture_pixel_bytes": textures["decoded_bytes"], "level_texture_palette_bytes": textures.get("palette_bytes", 0),
            "level_texture_decoded_total_bytes": textures.get("decoded_total_bytes", textures["decoded_bytes"]),
            "level_sprite_bytes": textures["sprite_bytes"], "static_image_bytes": manifest["static_image_bytes"],
            "sampled_heap_peak_bytes": max(heaps), "texture_upload_samples": {"count": len(uploads), "minimum": min(uploads), "maximum": max(uploads),
                "scope": "Per-frame upload count sampled by ordinary one-second material logs; not every frame."},
            "work": profile["distributions"]["work"], "video": video, "cpu_slots": profile["slots"],
            "nested_timings": report["nested_timings"], "runtime_allocations": report["runtime_allocations"],
            "renderer_startup": {name: records(name, whole=True) for name in ("geometry_ready", "shading_ready", "rsp_ready", "scene_prepared")},
            "workload": profile["workload"], "checks": report["checks"],
            "image_capture_scope": captures["scope"], "captures": captures["captures"]}


def compare(output):
    before, after = (phase_summary(output / phase) for phase in ("before", "after"))
    matched = all(before[key] == after[key] for key in ("level_sha256", "gameplay_source_sha256", "enemy_types_sha256", "animation_identity"))
    environment = all(before[key] == after[key] for key in ("settings_source_sha256", "compiler", "renderer_library_sha256"))
    result = {"schema_version": 1, "diagnostic_valid": before["diagnostic_valid"] and after["diagnostic_valid"] and matched and environment,
              "matched_authored_gameplay_and_animation": matched,
              "matched_settings_compiler_and_rsp_library": environment,
              "same_oriented_geometry_normals_and_bones_at_1e7_precision": before["geometry_identity"] == after["geometry_identity"],
              "before": before, "after": after,
              "changes": {"guard_vertices": after["guard_geometry"]["vertices"] - before["guard_geometry"]["vertices"],
                  "guard_decoded_texture_bytes": after["guard_texture"]["decoded_total_bytes"] - before["guard_texture"]["decoded_total_bytes"],
                  "sampled_heap_peak_bytes": after["sampled_heap_peak_bytes"] - before["sampled_heap_peak_bytes"],
                  "work_average_ms": after["work"]["average_ms"] - before["work"]["average_ms"],
                  "work_max_ms": after["work"]["max_ms"] - before["work"]["max_ms"]},
              "limitations": ["Ares CP0/VI model; original N64/M64 validation remains pending.",
                  "The new atlas, UV splits, texture-format support and any resulting code-layout change are measured together; speed differences are not attributed solely to palette format.",
                  "Geometry identity ignores UVs and vertex numbering, rounds positions/normals to1e-7 and preserves winding/bone ownership.",
                  "Work excludes display-buffer wait; fresh framebuffer presentation is measured independently via VI origins.",
                  "Fixed-phase image captures and sampled upload counts are not a worst-case crowd/combat benchmark."]}
    write(output / "comparison.json", result)
    return result


def publish(output):
    """Publish compact, hash-checked evidence and unchanged framebuffer PNGs."""
    result = compare(output)
    if not result["diagnostic_valid"]:
        raise ValueError("Refusing to publish an invalid controlled comparison")
    result["commands"] = [
        "python tools/guard_atlas_study.py freeze --phase before",
        "python tools/guard_atlas_study.py capture --phase before",
        "Inspect both images and write before/visibility.json using the official visibility-review schema.",
        "python tools/guard_atlas_study.py measure --phase before",
        "Publish the new art/material only after the before snapshot is secured.",
        "python tools/guard_atlas_study.py freeze --phase after --with-sponza",
        "python tools/guard_atlas_study.py capture --phase after",
        "Inspect both images and write after/visibility.json using the official visibility-review schema.",
        "python tools/guard_atlas_study.py measure --phase after",
        "python tools/guard_atlas_study.py sponza --phase after",
        "python tools/guard_atlas_study.py publish",
    ]
    for phase in ("before", "after"):
        stage = output / phase
        snapshot = checked(stage)
        row = result[phase]
        row["source_commit_at_snapshot"] = snapshot["source_commit"]
        row["source_commit_note"] = "File hashes describe actual working inputs, including any uncommitted changes."
        row["source_hashes"] = {name: digest for name, digest in snapshot["files"].items()
            if name.startswith("src/") or name in (
                "tools/build.py", "tools/compile_level.py", "tools/compile_bundle.py", "tools/cook_textures.py",
                "tools/character_assets.py", "tools/verify_guard_performance.py", "tools/profile_report.py",
                "tools/smoke_ares.py", "content/animation_workshop.json", "content/assets/guard/pack.json",
                "content/assets/guard/guard.character.json", "art/guard.blend")}
        row["artifacts_revalidated_locally"] = True
        row["artifact_root"] = stage.relative_to(ROOT).as_posix()
        row["visibility_sha256"] = sha(stage / "visibility.json")
        for capture in row["captures"]:
            source = stage / capture["image"]
            target = ROOT / "docs/images/guard-atlas" / (phase + "-" + source.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            if sha(target) != capture["sha256"]:
                raise ValueError("Published framebuffer copy changed")
            capture["image"] = target.relative_to(ROOT).as_posix()
    historical = output / "sponza-before"
    reference_path = historical / "reference.json"
    if reference_path.exists():
        reference = json.loads(reference_path.read_text())
        for name, digest in reference["files"].items():
            path = (historical / name).resolve()
            if not path.is_relative_to(historical.resolve()) or sha(path) != digest:
                raise ValueError("Preserved Sponza reference changed: " + name)
        old = json.loads((historical / "source-evidence.json").read_text())
        result["sponza"] = {"before_scope": "Previously measured ordinary gameplay on the preserved original guard/runtime; the workshop before run was measured freshly for this study.",
            "before_reference_sha256": sha(reference_path), "before_raw_artifacts_revalidated_locally": True,
            "cases": {}}
        for preset in ("courtyard", "upper-gallery"):
            after_path = output / "after" / ("sponza-" + preset + ".json")
            if not after_path.exists():
                raise ValueError("Both Sponza after cases are required when publishing their comparison")
            new = json.loads(after_path.read_text())
            pair = {}
            for phase, case in (("before", old["cases"][preset]), ("after", new)):
                build, profile = case["build"], case["profile"]
                if not case["diagnostic_valid"] or not all(case["checks"].values()):
                    raise ValueError("Invalid Sponza ordinary-gameplay evidence")
                if phase == "after":
                    log = output / "after" / case["run"]["log"]
                    snapshot = checked(output / "after")
                    rom = output / "after" / snapshot["roms"]["sponza-" + preset]["rom"]
                    if sha(log) != profile["source_log_sha256"] or sha(rom) != build["rom_sha256"]:
                        raise ValueError("Sponza raw measurement changed")
                    content = build["level_catalog"]["levels"][0]["content"]
                    textures = build["textures"]
                else:
                    content = case["content"]
                    textures = content["textures"]
                pair[phase] = {"diagnostic_valid": case["diagnostic_valid"], "checks": case["checks"],
                    "rom_sha256": build["rom_sha256"], "log_sha256": profile["source_log_sha256"],
                    "level_source_sha256": case.get("level_source_sha256", case.get("source_sha256")),
                    "settings_source_sha256": case["run"]["settings_source_sha256"],
                    "renderer_library_sha256": build["renderer_dependency"]["library_sha256"],
                    "work": profile["distributions"]["work"], "video": profile["video"],
                    "cpu_slots": profile["slots"], "heap_bytes": case["heap_bytes"],
                    "static_image_bytes": build["static_image_bytes"], "counts": content["counts"],
                    "texture_count": len(textures["textures"]), "texture_pixel_bytes": textures["decoded_bytes"],
                    "texture_palette_bytes": textures.get("palette_bytes", 0),
                    "texture_decoded_total_bytes": textures.get("decoded_total_bytes", textures["decoded_bytes"]),
                    "actor_displacement_metres": case["actor_displacement_metres"],
                    "terminal_game_state_observed": case["terminal_game_state_observed"]}
            pair["matched_scene_settings_and_rsp_library"] = all(pair["before"][key] == pair["after"][key]
                for key in ("level_source_sha256", "settings_source_sha256", "renderer_library_sha256"))
            if not pair["matched_scene_settings_and_rsp_library"]:
                raise ValueError("Sponza comparison scene or emulator environment changed")
            result["sponza"]["cases"][preset] = pair
    result["limitations"].append("Ignored private snapshots, raw ROMs and logs were rehashed locally during publication; checked-in hashes retain provenance, but those raw artifacts are not bundled with the repository.")
    write(ROOT / "docs/evidence/guard-atlas-study.json", result)
    return result


def measure(stage):
    snapshot = checked(stage)
    if not (stage / "visibility.json").is_file():
        raise ValueError("Inspect captures and create the official visibility-review JSON before measurement")
    command = [sys.executable, str(stage / "tools/verify_guard_performance.py"),
               "--rom", str(stage / snapshot["roms"]["ordinary"]["rom"]),
               "--manifest", str(stage / snapshot["roms"]["ordinary"]["manifest"]),
               "--visibility-report", str(stage / "visibility.json"), "--output", str(stage / "measurement.json")]
    process = subprocess.run(command, cwd=stage)
    checked(stage)
    result = json.loads((stage / "measurement.json").read_text())
    write(stage / "measurement-command.json", {"command": command, "exit_code": process.returncode,
          "snapshot_sha256": sha(stage / "snapshot.json"), "passed": result["passed"]})
    if process.returncode:
        raise RuntimeError("Official ordinary-gameplay performance gate failed; preserved measurement.json contains all failed checks")
    return result


def sponza(stage, ares, timeout):
    """Use the official Sponza validity checks on preserved ordinary ROMs."""
    snapshot = checked(stage)
    sys.path.insert(0, str(stage / "tools"))
    from verify_sponza_performance import analyze, validate_content
    from smoke_ares import exercise
    cases = {}
    for preset in ("courtyard", "upper-gallery"):
        entry = snapshot["roms"]["sponza-" + preset]
        rom = stage / entry["rom"]
        manifest = json.loads((stage / entry["manifest"]).read_text())
        if sha(rom) != manifest["rom_sha256"] or any(manifest.get(key, False) for key in (
                "autoplay", "capture", "scale_bench", "debug_overlay", "menu_test", "lighting_bake_verify", "disable_lighting_bake")):
            raise ValueError("Sponza requires its preserved ordinary ROM")
        if manifest["start_preset"] != preset or not manifest["model_culling"]:
            raise ValueError("Unexpected Sponza start/culling mode")
        validate_content(manifest)
        run = exercise(ares, stage / ".dev/ares/settings-8mb.bml", rom, "atlas-sponza-" + preset,
                       "DL64 profile_end window=33", timeout, sha(rom))
        log = stage / run["log"]
        result = analyze(log.read_bytes(), log, preset, warmup=3, windows=30)
        result.update(run=run, build=manifest, snapshot_sha256=sha(stage / "snapshot.json"),
                      level_source_sha256=sha(stage / "content/sponza_courtyard.json"))
        checked(stage)
        validate_content(manifest)
        write(stage / ("sponza-" + preset + ".json"), result)
        cases[preset] = result
        print(json.dumps({"preset": preset, "diagnostic_valid": result["diagnostic_valid"],
            "video": result["profile"]["video"], "work": result["profile"]["distributions"]["work"]}), flush=True)
        if not result["diagnostic_valid"]:
            raise RuntimeError("Sponza ordinary-workload validation failed")
    return {"passed": all(row["diagnostic_valid"] for row in cases.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "capture", "measure", "sponza", "compare", "publish"))
    parser.add_argument("--phase", choices=("before", "after"))
    parser.add_argument("--output", type=Path, default=ROOT / "build/guard-atlas-study")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))))
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--with-sponza", action="store_true", help="Also preserve both ordinary Sponza presets during freeze")
    args = parser.parse_args()
    if args.command in ("compare", "publish"):
        result = (publish if args.command == "publish" else compare)(args.output.resolve())
        print(json.dumps({"diagnostic_valid": result["diagnostic_valid"], "changes": result["changes"]}, indent=2))
        return
    if args.phase is None:
        parser.error("--phase is required for freeze, capture and measure")
    stage = (args.output / args.phase).resolve()
    if not stage.is_relative_to(ROOT / "build"):
        raise ValueError("Study snapshots must stay under the project build directory")
    result = (freeze(stage, args.sdk, args.with_sponza) if args.command == "freeze" else
              capture(stage, args.ares, args.timeout) if args.command == "capture" else
              sponza(stage, args.ares, args.timeout) if args.command == "sponza" else measure(stage))
    print(json.dumps({"phase": args.phase, "command": args.command, "passed": result.get("passed", True), "output": str(stage)}, indent=2))


if __name__ == "__main__":
    main()
