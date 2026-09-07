"""Build the blockout demo with the installed Windows SDK; no shell interpolation."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import re
import subprocess
import sys
import time
import uuid

try:
    from compile_level import compile_level
    from compile_bundle import prepare_bundle, read_manifest, selection
except ModuleNotFoundError:
    from tools.compile_level import compile_level
    from tools.compile_bundle import prepare_bundle, read_manifest, selection

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"


def run(args, *, cwd=ROOT, env=None, capture=False):
    args = [a.as_posix() if isinstance(a, Path) else str(a) for a in args]
    print("[run] " + subprocess.list2cmdline(args), flush=True)
    return subprocess.run(args, cwd=cwd, env=env,
                          check=True, text=True, capture_output=capture)


def fingerprint(paths, flags):
    digest = hashlib.sha256(json.dumps(flags, sort_keys=True).encode())
    for path in paths:
        digest.update(str(path).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def scene_paths(level=None):
    source = Path(level or ROOT / "content/first_room.json").resolve()
    if source.parent != ROOT / "content" or source.suffix != ".json" or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", source.stem):
        raise ValueError("Level must be a content JSON file with an ASCII identifier filename")
    default = source == ROOT / "content/first_room.json"
    return source, BUILD if default else BUILD / "scenes" / source.stem, default


def build_rom(sdk, autoplay=False, debug_overlay=False, level=None, capture=False,
              scale_bench=False, disable_model_cull=False, *, bundle=None,
              start_level=None, start_preset=None, menu_test=False, renderer="t3d"):
    start = time.perf_counter()
    if renderer not in ("cpu", "t3d"):
        raise ValueError("Renderer must be cpu or t3d")
    if bundle is not None and level is not None:
        raise ValueError("--level and --bundle are mutually exclusive")
    if (bundle is not None or start_preset is not None) and (autoplay or capture or scale_bench):
        raise ValueError("Bundles and test starts run separately from replay, capture and scale modes")
    if start_level is not None and bundle is None:
        raise ValueError("--start-level requires --bundle; a single-level build uses --level")
    if menu_test and (bundle is None or start_level is not None or start_preset is not None):
        raise ValueError("--menu-test requires a bundle that starts in its menu")
    if bundle is not None:
        bundle = Path(bundle).resolve()
        bundle_data, entries = read_manifest(bundle)
        # Invalid selection must fail before any cook or SDK invocation.
        selection(entries, start_level, start_preset, menu=True)
        level, cooked_dir, default_scene = None, None, False
    else:
        level, cooked_dir, default_scene = scene_paths(level)
        entries = [{"id": level.stem, "source": level}]
        bundle_data = {"title": "DarkLantern64"}
        selection(entries, start_preset=start_preset)
    if scale_bench and (autoplay or capture or debug_overlay):
        raise ValueError("Scale microbenchmarks run separately from replay, capture and overlay modes")
    if autoplay and not default_scene:
        raise ValueError("The input replay is authored for first_room.json; use a manual or capture build for other scenes")
    name = "DarkLantern64-autoplay" if autoplay else "DarkLantern64"
    if debug_overlay:
        name += "-debug"
    if bundle is not None:
        name += "-bundle-" + bundle.stem
        name += "-start-" + start_level if start_level else "-menu"
    elif not default_scene:
        name += "-" + level.stem
    if start_preset is not None:
        name += "-preset-" + start_preset
    if capture:
        name += "-capture"
    if scale_bench:
        name += "-scale"
    if disable_model_cull:
        name += "-unculled"
    if menu_test:
        name += "-menu-test"
    if renderer != "cpu":
        name += "-" + renderer
    work = BUILD / name
    work.mkdir(parents=True, exist_ok=True)
    bundle_output = work / "catalog"
    catalog, generated_sources = prepare_bundle(entries, bundle_data["title"], bundle_output, sdk,
                                               start_level=start_level, start_preset=start_preset,
                                               menu=bundle is not None,
                                               cooked_dirs=None if bundle is not None else [cooked_dir])
    content = catalog["levels"][0]["content"]
    texture_report = catalog["textures"] if bundle is not None else catalog["levels"][0]["textures"]
    bins = sdk / "bin"
    lib = sdk / "mips64-elf/lib"
    tool_names = ["mips64-elf-gcc", "mips64-elf-g++", "n64sym",
                  "mips64-elf-strip", "n64elfcompress", "n64tool", "mips64-elf-size"]
    tools = {n: bins / (n + ".exe") for n in tool_names}
    if texture_report["textures"]:
        tools["mkdfs"] = bins / "mkdfs.exe"
    for path in list(tools.values()) + [lib / n for n in ("libdragon.a", "libdragonsys.a", "n64.ld")]:
        if not path.is_file():
            raise RuntimeError(f"Missing SDK file: {path}. Set N64_INST or pass -Sdk.")
    env = dict(os.environ, N64_INST=str(sdk), PATH=str(bins) + os.pathsep + os.environ.get("PATH", ""))
    common = ["-march=vr4300", "-mtune=vr4300", "-mabi=o64", "-std=gnu17",
              "-O2", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-error=deprecated-declarations",
              "-falign-functions=32", "-ffunction-sections", "-fdata-sections", "-DN64",
              "-I" + str(sdk / "mips64-elf/include"), "-I" + str(ROOT / "src"),
              "-I" + str(bundle_output / "generated"), "-I" + str(work)]
    renderer_libraries = []
    renderer_inputs = []
    renderer_dependency = None
    if renderer == "t3d":
        try:
            from build_tiny3d import build_library, SOURCE as t3d_source, REVISION as t3d_revision
        except ModuleNotFoundError:
            from tools.build_tiny3d import build_library, SOURCE as t3d_source, REVISION as t3d_revision
        t3d_library = build_library(sdk)
        renderer_libraries.append(t3d_library)
        renderer_inputs = [t3d_library, ROOT / "tools/build_tiny3d.py", ROOT / "dependencies.json"]
        renderer_inputs += sorted((t3d_source / "src").rglob("*.h"))
        common += ["-DDL_RENDER_T3D=1", "-I" + str(t3d_source / "src")]
        renderer_dependency = {"revision": t3d_revision, "library": str(t3d_library),
                               "library_sha256": hashlib.sha256(t3d_library.read_bytes()).hexdigest()}
    if autoplay:
        common += ["-DDL_AUTOPLAY=1"]
    if debug_overlay:
        common += ["-DDL_DEBUG_OVERLAY=1"]
    if scale_bench:
        common += ["-DDL_SCALE_BENCH=1"]
    if disable_model_cull:
        common += ["-DDL_DISABLE_MODEL_CULL=1"]
    if menu_test:
        common += ["-DDL_MENU_TEST=1"]
    extra_headers = []
    if capture:
        common += ["-DDL_CAPTURE=1"]
        data = json.loads(level.read_text())
        from capture_ares import validate_views
        views = validate_views(data)
        rows = []
        for view in views:
            door_open = view.get("door_open", False)
            if not isinstance(door_open, bool):
                raise ValueError("Capture view door_open must be a boolean")
            values = view["position"] + [view["yaw"], view["pitch"]]
            if len(values) != 5 or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                raise ValueError("Capture view needs finite XYZ, yaw and pitch")
            position = ",".join(f"{float(v):.6f}f" for v in values[:3])
            rows.append("{{" + position + "}," + f"{math.radians(values[3]):.6f}f,{math.radians(values[4]):.6f}f," + ("true" if door_open else "false") + f",{float(view.get('animation_time',0)):.6f}f," + json.dumps(view.get("animation_clip", "")) + f",{math.radians(view.get('head_yaw',0)):.6f}f,{math.radians(view.get('head_pitch',0)):.6f}f" + "}")
        capture_header = work / "capture_views.h"
        capture_header.write_text("static const struct { DlVec3 position; float yaw,pitch; bool door_open; float animation_time; const char *animation_clip; float head_yaw,head_pitch; } dl_capture_views[]={" +
                                  ",".join(rows) + "};\n#define DL_CAPTURE_VIEW_COUNT " + str(len(rows)) + "\n")
        extra_headers.append(capture_header)
    sources = [ROOT / "src" / f for f in ("game.c", "main.c", "dl_profile.c", "launch.c", "animation.c",
                                         "render_lighting.c", "render_batches.c", "render_transform.c", "render_texture_packing.c")]
    sources += generated_sources
    if scale_bench:
        sources.append(ROOT / "src/scale_bench.c")
    if (ROOT / "src/render.c").exists():
        sources.append(ROOT / "src/render.c")
    headers = (sorted((ROOT / "src").glob("*.h")) + sorted((ROOT / "src").glob("*.def"))
               + sorted((ROOT / "src").glob("*.inc")) + extra_headers)
    headers += [bundle_output / "generated/bundle.h"]
    for entry in catalog["levels"]:
        entry_output = Path(entry["cooked_dir"])
        headers += [Path(entry["source"]), entry_output / "generated/demo_level.h",
                    entry_output / "generated/texture_runtime_report.json"]
    headers += [bundle_output / texture["sprite_path"] for texture in texture_report["textures"]]
    if bundle is not None:
        headers.append(bundle)
    headers += [ROOT / "tools" / name for name in ("compile_bundle.py", "compile_level.py", "cook_textures.py", "character_assets.py")]
    sdk_inputs = [lib / n for n in ("libdragon.a", "libdragonsys.a", "n64.ld")]
    sdk_inputs += sorted((sdk / "mips64-elf/include").rglob("*.h"))
    compiler_version = run([tools["mips64-elf-gcc"], "--version"], env=env, capture=True).stdout.splitlines()[0]
    kernel_flags = {"render_lighting.c": ["-O3"], "render_transform.c": ["-O3"]}
    signature = fingerprint(sources + headers + sdk_inputs + renderer_inputs + [Path(__file__)],
                            {"flags": common, "kernel_flags": kernel_flags, "compiler": compiler_version, "renderer": renderer,
                             "tools": {n: (p.stat().st_size, p.stat().st_mtime_ns) for n, p in tools.items()}})
    manifest = work / "build.json"
    rom = BUILD / (name + ".z64")
    if manifest.exists() and rom.exists():
        previous = json.loads(manifest.read_text())
        if previous.get("signature") == signature and previous.get("rom_sha256") == hashlib.sha256(rom.read_bytes()).hexdigest():
            print(f"[cached] {rom}")
            return rom
    objects = []
    for source in sources:
        obj = work / (source.stem + ".o")
        run([tools["mips64-elf-gcc"], *common, *kernel_flags.get(source.name, []), "-c", source, "-o", obj], env=env)
        objects.append(obj)
    elf = work / (name + ".elf")
    run([tools["mips64-elf-g++"], "-mabi=o64", "-g", "-o", elf, *objects,
         *renderer_libraries, "-L" + str(lib), "-lc", "-ldragon", "-lm", "-ldragonsys",
         "-Wl,-T," + str(lib / "n64.ld"), "-Wl,--gc-sections,--wrap,__do_global_ctors",
         "-Wl,-Map=" + str(work / (name + ".map"))], env=env)
    symbols = work / (name + ".sym")
    run([tools["n64sym"], elf, symbols], env=env)
    stripped = work / "uncompressed/program.elf"
    compressed_dir = work / "compressed"
    stripped.parent.mkdir(exist_ok=True)
    compressed_dir.mkdir(exist_ok=True)
    run([tools["mips64-elf-strip"], "-s", "-o", stripped, elf], env=env)
    run([tools["n64elfcompress"], "-o", compressed_dir, "-c", "1", stripped], env=env)
    if not (compressed_dir / "program.elf").is_file():
        raise RuntimeError("n64elfcompress did not produce the expected output.")
    candidate = work / (name + ".z64")
    pack = [tools["n64tool"], "--title", "DarkLantern64", "--toc", "--output", candidate,
         "--align", "256", compressed_dir / "program.elf", "--align", "8", symbols,
         "--align", "8"]
    if texture_report["textures"]:
        filesystem = work / "textures.dfs"
        run([tools["mkdfs"], filesystem, bundle_output / "romfs"], env=env, capture=True)
        pack = pack[:-2] + ["--align", "16", filesystem]
    run(pack, env=env)
    size = run([tools["mips64-elf-size"], elf], env=env, capture=True).stdout
    print(size)
    # Keep a bootable image small enough to reach the 4 MiB error screen.
    values = size.splitlines()[-1].split()
    image_bytes = sum(int(v) for v in values[:3])
    if image_bytes > 3 * 1024 * 1024:
        raise RuntimeError("Startup image exceeds the prototype 3 MiB guard; inspect static allocations.")
    rom_temp = rom.with_suffix(".z64.tmp")
    shutil.copyfile(candidate, rom_temp)
    os.replace(rom_temp, rom)
    source_sha256 = content["source_sha256"] if bundle is None else hashlib.sha256(
        bundle.read_bytes() + "".join(entry["content"]["source_sha256"] for entry in catalog["levels"]).encode()).hexdigest()
    report = {"signature": signature, "compiler": compiler_version, "sdk": str(sdk),
              "renderer": renderer, "renderer_dependency": renderer_dependency, "kernel_flags": kernel_flags,
              "source_sha256": source_sha256, "rom": str(rom.relative_to(ROOT)),
              "rom_bytes": rom.stat().st_size, "rom_sha256": hashlib.sha256(rom.read_bytes()).hexdigest(),
              "static_image_bytes": image_bytes, "elapsed_seconds": round(time.perf_counter() - start, 3),
              "autoplay": autoplay, "debug_overlay": debug_overlay, "capture": capture,
              "scale_bench": scale_bench, "model_culling": not disable_model_cull,
              "level": str(level.relative_to(ROOT)) if level is not None else None,
              "bundle": str(bundle.relative_to(ROOT)) if bundle is not None else None,
              "start_level": start_level, "start_preset": start_preset, "menu_test": menu_test,
              "level_catalog": catalog, "textures": texture_report, "size_output": size}
    manifest.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"manifest": str(manifest.relative_to(ROOT)), "rom": report["rom"],
                      "renderer": renderer,
                      "rom_bytes": report["rom_bytes"], "rom_sha256": report["rom_sha256"],
                      "static_image_bytes": image_bytes, "elapsed_seconds": report["elapsed_seconds"],
                      "start_in_menu": catalog["start_in_menu"],
                      "start_level": catalog["levels"][catalog["initial_level"]]["id"],
                      "start_preset": start_preset or "default",
                      "levels": [{"id": entry["id"], "title": entry["content"]["title"],
                                  "models": entry["content"]["counts"]["models"],
                                  "enemies": entry["content"]["counts"]["enemies"],
                                  "test_starts": len(entry["content"].get("test_starts", []))}
                                 for entry in catalog["levels"]],
                      "resident_geometry_bytes": catalog["resident_geometry_bytes"],
                      "packaged_sprite_bytes": catalog["textures"]["sprite_bytes"],
                      "maximum_level_sprite_bytes": catalog["textures"]["maximum_level_sprite_bytes"]}, indent=2))
    return rom


def run_tests():
    BUILD.mkdir(exist_ok=True)
    compile_level()
    run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"])
    compiler = shutil.which("gcc") or "C:/msys64/ucrt64/bin/gcc.exe"
    if not Path(compiler).exists():
        raise RuntimeError("Host GCC required for portable simulation tests (MSYS2 UCRT64 supported).")
    env = dict(os.environ, PATH=str(Path(compiler).parent) + os.pathsep + os.environ.get("PATH", ""))
    for name in ("test_game", "test_first_room"):
        output = BUILD / (name + ".exe")
        run([compiler, "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc", "-Ibuild/generated",
             "src/game.c", f"tests/{name}.c", "-lm", "-o", output], env=env)
        run([output], env=env)
    output = BUILD / "test_animation.exe"
    run([compiler, "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
         "src/animation.c", "tests/test_animation.c", "-lm", "-o", output], env=env)
    run([output], env=env)
    output = BUILD / "test_profile.exe"
    run([compiler, "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-DDL_PROFILE_TEST", "-Isrc",
         "src/dl_profile.c", "tests/test_profile.c", "-lm", "-o", output], env=env)
    run([output], env=env)

    output = BUILD / "test_scale_bench.exe"
    run([compiler, "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
         "-DDL_SCALE_BENCH", "-DDL_SCALE_BENCH_TEST", "src/game.c", "src/scale_bench.c",
         "tests/test_scale_bench.c", "-lm", "-o", output], env=env)
    result = run([output], env=env, capture=True)
    from scale_study import parse_microbench
    parsed = parse_microbench("DL64 scale_boot memory=8388608\n" + result.stdout)
    assert all(case["average_ms"] == 75 for case in parsed["cases"])
    print("Scale benchmark: guard equivalence, immutable inputs, timer wrap and audio subtraction passed.")

    output = BUILD / "test_render_visibility.exe"
    run([compiler, "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
         "tests/test_render_visibility.c", "-lm", "-o", output], env=env)
    run([output], env=env)

    output = BUILD / "test_launch.exe"
    shading_output = BUILD / "test_render_shading.exe"
    run([compiler, "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
         "tests/test_render_shading.c", "-lm", "-o", shading_output], env=env)
    run([shading_output], env=env)

    render_checks = {
        "test_render_lighting": ["src/render_lighting.c", "src/render_transform.c", "src/animation.c"],
        "test_render_batches": ["src/render_batches.c"],
        "test_render_transform": ["src/render_transform.c", "src/animation.c"],
        "test_hud_cache": [],
        "test_render_texture_packing": ["src/render_texture_packing.c", "src/render_batches.c"],
    }
    for name, sources in render_checks.items():
        render_output = BUILD / (name + ".exe")
        run([compiler, "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
             *sources, f"tests/{name}.c", "-lm", "-o", render_output], env=env)
        run([render_output], env=env)

    run([compiler, "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-Isrc",
         "src/launch.c", "tests/test_launch.c", "-lm", "-o", output], env=env)
    run([output], env=env)


def build_editor(launch, level=None):
    from setup_editor import setup
    setup()
    initial_level = scene_paths(level)[0] if level is not None else None
    run([sys.executable, ROOT / "tools/estimate_audio.py", "--manifest", ROOT / "content/audio_budget.json",
         "--output", BUILD / "generated/audio_memory_report.json"])
    bazel = shutil.which("bazelisk") or shutil.which("bazel")
    if not bazel:
        raise RuntimeError("Bazelisk must be on PATH to build the LightEngine editor.")
    target = "darklantern64_project_editor" if initial_level is None else "darklantern64_scene_editor"
    run([bazel, "build", "//:" + target], cwd=ROOT / "editor")
    exe = ROOT / "editor/bazel-bin" / (target + ".exe")
    if launch:
        # This is the visible, interactive editor requested by the user.
        subprocess.Popen([str(exe), str(ROOT)] + ([str(initial_level)] if initial_level else []), cwd=ROOT)
    return exe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--autoplay", action="store_true")
    parser.add_argument("--debug-overlay", action="store_true", help="Start with the timing overlay visible")
    parser.add_argument("--renderer", choices=("cpu", "t3d"), default="t3d",
                        help="Developer renderer selection; Tiny3D writes separate ROM/build outputs")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--level", type=Path, help="Canonical content JSON (default: content/first_room.json)")
    sources.add_argument("--bundle", type=Path, help="Level catalog JSON; starts in the level menu")
    parser.add_argument("--start-level", help="Bundle level ID to launch directly")
    parser.add_argument("--start-preset", help="Authored test start ID (or default for the normal spawn)")
    parser.add_argument("--menu-test", action="store_true", help="Diagnostic scripted menu and level-switch exercise")
    parser.add_argument("--capture", action="store_true", help="Diagnostic framebuffer export at authored views")
    parser.add_argument("--scale-bench", action="store_true", help="Diagnostic CPU workload sweeps, without rendering")
    parser.add_argument("--disable-model-cull", action="store_true", help="Disable early bounds rejection for controlled A/B measurements")
    parser.add_argument("--editor", action="store_true")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    try:
        if args.editor and (args.bundle is not None or args.start_level is not None or args.start_preset is not None or args.menu_test):
            raise ValueError("The editor opens one --level; bundle and test-start options build game ROMs")
        if args.test:
            run_tests()
        if args.editor:
            build_editor(args.run, args.level)
        elif not args.test:
            rom = build_rom(args.sdk.resolve(), args.autoplay, args.debug_overlay, args.level, args.capture,
                            args.scale_bench, args.disable_model_cull, bundle=args.bundle,
                            start_level=args.start_level, start_preset=args.start_preset, menu_test=args.menu_test,
                            renderer=args.renderer)
            if args.run:
                ares = os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))
                if not Path(ares).is_file():
                    raise RuntimeError("Ares not found; set ARES_EXE to its executable.")
                from prepare_ares import prepare_settings
                settings = ROOT / ".dev/ares/settings-8mb.bml"
                if not settings.is_file():
                    settings = prepare_settings(memory=8)
                # Retain hand-tuned controller mappings. Give every launch its
                # own log so two sessions cannot interleave profiler windows.
                session = ROOT / ".dev/ares/sessions" / uuid.uuid4().hex
                session.mkdir(parents=True)
                log = session / "manual-session.log"
                with log.open("w") as output, (session / "stderr.log").open("w") as errors:
                    process = subprocess.Popen([ares, "--settings-file", str(settings), "--no-file-prompt", str(rom)],
                                               cwd=ROOT, stdout=output, stderr=errors)
                session_info = {"log": str(log), "pid": process.pid, "rom": str(rom),
                                "rom_sha256": hashlib.sha256(rom.read_bytes()).hexdigest(),
                                "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                latest = ROOT / ".dev/ares/latest-session.json"
                temp = latest.with_suffix(".json.tmp")
                temp.write_text(json.dumps(session_info, indent=2) + "\n", encoding="utf-8")
                os.replace(temp, latest)
                print(f"Game log: {log}")
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Build failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
