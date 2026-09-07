"""Isolated courtyard depth correctness fixture; never changes the game or SDK."""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil

from build_tiny3d import REVISION, SOURCE, copy_pinned_inputs, run

ROOT = Path(__file__).resolve().parents[1]
def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def scalar(value):
    result = format(value, ".10g")
    if "." not in result and "e" not in result:
        result += ".0"
    return result + "f"


def fixture(output):
    source = ROOT / "content/moonlit_courtyard.json"
    level = json.loads(source.read_text())
    assets = {a["id"]: a["uri"] for a in level["assets"]}
    paths = [source]
    descriptions, declarations = [], []
    for index, name in enumerate(("west-warehouse", "west-lit-window")):
        entity = next(e for e in level["entities"] if e["id"] == name)
        path = source.parent / assets[entity["model"]]
        paths.append(path)
        vertices, faces = [], []
        for line in path.read_text().splitlines():
            parts = line.split()
            if parts and parts[0] == "v":
                vertices.append(tuple(map(float, parts[1:4])))
            elif parts and parts[0] == "f":
                face = [int(v.split("/")[0]) - 1 for v in parts[1:]]
                if all(vertices[v][2] == .5 for v in face):
                    faces.extend((face[0], face[n], face[n+1]) for n in range(1, len(face)-1))
        used = sorted({v for face in faces for v in face})
        remap = {v: n for n, v in enumerate(used)}
        assert len(used) % 2 == 0 and len(used) <= 70 and faces
        declarations.append(f"static const Vec vertices_{index}[]={{" + ",".join(
            "{" + ",".join(map(scalar, vertices[v])) + "}" for v in used) + "};")
        declarations.append(f"static const uint8_t indices_{index}[]={{" + ",".join(
            str(remap[v]) for face in faces for v in face) + "};")
        transform = entity["transform"]
        assert transform["rotation"] == [0, 90, 0]
        x, y, z = transform["position"]
        sx, sy, sz = transform["scale"]
        matrix = [0, 0, sz, x, 0, sy, 0, y, -sx, 0, 0, z]
        descriptions.append("{" + f"{len(used)},{len(faces)*3},vertices_{index},indices_{index}," +
                            "{" + ",".join(map(scalar, matrix)) + "}}")
    wall = next(e for e in level["entities"] if e["id"] == "west-warehouse")["transform"]
    window = next(e for e in level["entities"] if e["id"] == "west-lit-window")["transform"]
    center = list(window["position"])
    center[0] += window["scale"][2] * .5
    gap = center[0] - (wall["position"][0] + wall["scale"][2] * .5)
    if not math.isclose(gap, .0475, abs_tol=1e-8):
        raise ValueError("Courtyard window gap changed; review the diagnostic fixture")
    views = []
    for distance in (3, 6, 10, 18):
        for angle in (-60, -30, 0, 30, 60):
            for jitter in range(3):
                theta = math.radians(angle)
                eye = (center[0]+distance*math.cos(theta)+(jitter-1)*.007,
                       1.55, center[2]+distance*math.sin(theta)+(jitter-1)*.04)
                views.append("{{" + ",".join(map(scalar, eye)) + "}," + f"{distance},{angle+90},{jitter}" + "}")
    declarations.append("static const Mesh meshes[]={" + ",".join(descriptions) + "};")
    declarations.append("static const Vec window_center={" + ",".join(map(scalar, center)) + "};")
    declarations.append("static const View views[]={" + ",".join(views) + "};")
    (output / "fixture.h").write_text("\n".join(declarations)+"\n")
    return {"inputs": {str(p.relative_to(ROOT)): sha(p) for p in paths}, "gap_metres": gap,
            "camera_count": len(views), "angle_encoding": "stored angle minus90 degrees",
            "surfaces": "positive local Z faces only, original triangulation; no materials/AA/fog/other geometry"}


def replace(path, before, after, count=1):
    text = path.read_text()
    if text.count(before) != count:
        raise ValueError(f"Unexpected source at {path}: {before!r}")
    path.write_text(text.replace(before, after))


def stage_library(output, sdk, precision, source=SOURCE):
    stage = output / "tiny3d"
    build_root, target = (ROOT / "build").resolve(), stage.resolve()
    if (not target.is_relative_to(build_root) or target == build_root or
            SOURCE.resolve().is_relative_to(target) or source.resolve().is_relative_to(target)):
        raise ValueError("Depth library stage must stay in its disposable build directory")
    if target.exists():
        shutil.rmtree(target)
    if source.resolve() == SOURCE.resolve():
        copy_pinned_inputs(stage)
    else:
        shutil.copytree(source / "src", stage / "src",
                        ignore=shutil.ignore_patterns("rsp_tiny3d.h", "rsp_tinypx.h"))
        shutil.copyfile(source / "Makefile", stage / "Makefile")
    if precision:
        apply_viewport_precision(stage)
    temporary = output / "temporary"
    temporary.mkdir(exist_ok=True)
    script = '''set -eu
export PATH="/usr/bin:$PATH"
export N64_INST="$(cygpath -u "$1")"
export PATH="$N64_INST/bin:/usr/bin:$PATH"
export TMPDIR="$(cygpath -u "$3")"
export TEMP="$TMPDIR" TMP="$TMPDIR"
cd "$(cygpath -u "$2")"
make -j4 all BUILD_DIR=build_sdk_study
'''
    run([Path("C:/msys64/usr/bin/bash.exe"), "-c", script, "depth-study", sdk, stage, temporary])
    return stage / "build_sdk_study/libt3d.a", stage / "src"


def apply_viewport_precision(stage):
    path = stage / "src/t3d/t3d.c"
    replace(path, "  float screenShiftFactor = 16.0f;", "  uint16_t normWScale = (uint16_t)roundf(0xFFFF * currentViewport->_normScaleW);\n  float effectiveNormW = (float)normWScale / 0xFFFF;\n  float screenShiftFactor = 256.0f;")
    replace(path, "currentViewport->_normScaleW * 4.0f", "effectiveNormW * 4.0f", 2)
    replace(path, "(int32_t)roundf(screenFactor", "(int32_t)ceilf(screenFactor", 2)
    replace(path, "  uint16_t normWScale = (uint16_t)roundf(0xFFFF * currentViewport->_normScaleW);\n  uint16_t depthScale = (uint16_t)roundf(0xFFFF * currentViewport->_normScaleW * screenShiftFactor * 0.5f);",
            "  uint32_t depthScale = (uint32_t)roundf(0xFFFF * effectiveNormW * screenShiftFactor * 0.5f);\n  if(depthScale > 0x7FFF)depthScale = 0x7FFF;")
    for name in ("rsp_tiny3d.rspl", "clipping.rspl", "rsp_tinypx.rspl"):
        path = stage / "src/t3d/rsp" / name
        count = 2 if name == "rsp_tinypx.rspl" else 1
        replace(path, "screenSize >>= 4;", "screenSize >>= 8;", count)
    for name, registers in (("rsp_tiny3d.S", ("v14", "v13")),
                            ("rsp_tiny3d_clipping.S", ("v02", "v01")),
                            ("rsp_tinypx.S", ("v20", "v19"))):
        path = stage / "src/t3d/rsp" / name
        count = 2 if name == "rsp_tinypx.S" else 1
        replace(path, f"vmudl ${registers[0]}, $v00, $v31.e3", f"vmudl ${registers[0]}, $v00, $v31.e7", count)
        replace(path, f"vmadm ${registers[1]}, ${registers[1]}, $v31.e3", f"vmadm ${registers[1]}, ${registers[1]}, $v31.e7", count)


def build(sdk, output, precision, probe=False, quantized_cpu=False, library_override=None, include_override=None, probe_view=33, single_view=False):
    output.mkdir(parents=True, exist_ok=True)
    actual = run(["git", "-C", SOURCE, "rev-parse", "HEAD"], capture_output=True).stdout.strip()
    if actual != REVISION:
        raise ValueError("Unexpected Tiny3D revision")
    changes = run(["git", "-C", SOURCE, "status", "--porcelain", "--untracked-files=no"], capture_output=True).stdout
    if changes.strip():
        raise ValueError("Depth baseline requires the pristine tracked Tiny3D checkout")
    report = fixture(output)
    if single_view:
        report.update(camera_count=1, single_camera_index=probe_view)
    shutil.copyfile(ROOT / "tools/tiny3d/depth.c", output / "main.c")
    if library_override is not None and precision:
        library, include = stage_library(output, sdk, True, include_override.parent)
    elif library_override is not None:
        library, include = output / "libt3d.a", output / "tiny3d/src"
        shutil.copyfile(library_override, library)
        shutil.copytree(include_override, include, dirs_exist_ok=True)
    else:
        library, include = stage_library(output, sdk, precision)
    bins, libs = sdk / "bin", sdk / "mips64-elf/lib"
    env = dict(os.environ, N64_INST=str(sdk), PATH=str(bins)+os.pathsep+os.environ.get("PATH", ""))
    obj, elf = output / "main.o", output / "depth.elf"
    probe_flags = ["-DDL_DEPTH_PROBE=1"] if probe else []
    probe_flags.append(f"-DDL_DEPTH_PROBE_VIEW={probe_view}")
    if quantized_cpu:
        probe_flags.append("-DDL_DEPTH_QUANTIZED_CPU=1")
    if single_view:
        probe_flags.append("-DDL_DEPTH_SINGLE_VIEW=1")
    run([bins / "mips64-elf-gcc.exe", "-march=vr4300", "-mtune=vr4300", "-mabi=o64", "-std=gnu17", *probe_flags,
         "-O2", "-g", "-Wall", "-Wextra", "-Werror", "-DN64", "-ffunction-sections", "-fdata-sections",
         "-I"+str(sdk / "mips64-elf/include"), "-I"+str(include), "-c", output / "main.c", "-o", obj], env=env)
    run([bins / "mips64-elf-g++.exe", "-mabi=o64", "-g", "-o", elf, obj, library, "-L"+str(libs),
         "-lc", "-ldragon", "-lm", "-ldragonsys", "-Wl,-T,"+str(libs / "n64.ld"),
         "-Wl,--gc-sections,--wrap,__do_global_ctors"], env=env)
    symbols = output / "depth.sym"
    run([bins / "n64sym.exe", elf, symbols], env=env)
    stripped, compressed = output / "uncompressed/program.elf", output / "compressed"
    stripped.parent.mkdir(exist_ok=True)
    compressed.mkdir(exist_ok=True)
    run([bins / "mips64-elf-strip.exe", "-s", "-o", stripped, elf], env=env)
    run([bins / "n64elfcompress.exe", "-o", compressed, "-c", "1", stripped], env=env)
    if not (compressed / "program.elf").is_file():
        raise ValueError("Missing compressed ELF")
    rom = output / "depth.z64"
    run([bins / "n64tool.exe", "--title", "DL64 Depth Study", "--toc", "--output", rom,
         "--align", "256", compressed / "program.elf", "--align", "8", symbols], env=env)
    report.update(tiny3d_revision=REVISION, viewport_precision_backport=precision, quantized_cpu=quantized_cpu,
                  library_override=library_override is not None, probe_view=probe_view,
                  library_sha256=sha(library), rom_sha256=sha(rom), sdk_library_sha256=sha(libs / "libdragon.a"),
                  source_sha256=sha(output / "main.c"), fixture_sha256=sha(output / "fixture.h"),
                  sdk_modified=False, canonical_game_modified=False)
    (output / "provenance.json").write_text(json.dumps(report, indent=2)+"\n")
    return rom, report


def measure(rom, output, report, ares):
    from smoke_ares import exercise
    from capture_ares import decode_captures
    from PIL import Image
    settings = ROOT / ".dev/ares/settings-8mb.bml"
    if not settings.exists():
        settings = ares.parent / "settings.bml"
    result = exercise(ares, settings, rom, "depth-study", "DL64 depth_study complete", 180,
                      expected_sha256=report["rom_sha256"])
    text = (ROOT / result["log"]).read_text()
    rows = [{k: int(v) for k, v in re.findall(r"(\w+)=(\d+)", line)}
            for line in text.splitlines() if line.startswith("DL64 depth_case ")]
    if len(rows) != report["camera_count"]*2*3*2 or any(r["area"] == 0 for r in rows):
        raise ValueError("Missing depth samples or invisible reference windows")
    summaries = []
    for backend in (0, 1):
        for near in (120, 240, 400):
            selected = [r for r in rows if r["backend"] == backend and r["near_mm"] == near]
            summaries.append({"backend": "t3d" if backend else "cpu", "near_mm": near,
                              "cases": len(selected), "cases_with_bleed": sum(r["lost"] > 0 for r in selected),
                              "lost_pixels": sum(r["lost"] for r in selected),
                              "worst_lost_pixels": max(r["lost"] for r in selected),
                              "worst_lost_fraction": max(r["lost"]/r["area"] for r in selected)})
    for ident, (width, height, pixels) in decode_captures(text, 6).items():
        Image.frombytes("RGB", (width, height), pixels).save(output / f"worst-{ident}.png")
    report = dict(report, run=result, samples=rows, summaries=summaries,
                  interpretation="Lost pixels use each backend's window-only mask, testing both draw orders. Not a performance benchmark.")
    (output / "measurement.json").write_text(json.dumps(report, indent=2)+"\n")
    print(json.dumps(summaries, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--precision", action="store_true", help="Stage viewport-only upstream5392 precision backport")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--probe", action="store_true", help="Dump RSP transformed vertices for the worst baseline view")
    parser.add_argument("--probe-view", type=int, default=33, help="Camera index0..59 to probe and use for CPU reference captures")
    parser.add_argument("--single-view", action="store_true", help="Only render --probe-view for a separate representative image run")
    parser.add_argument("--cpu-quantized-z", action="store_true", help="Isolate integer vertexZ loss in CPU path")
    parser.add_argument("--library", type=Path, help="Explicit staged candidate library; requires --include and --label")
    parser.add_argument("--include", type=Path, help="Explicit candidate include root, copied before compiling")
    parser.add_argument("--label", help="Safe output directory label for an explicit candidate")
    parser.add_argument("--measure-existing", action="store_true")
    args = parser.parse_args()
    if any((args.library, args.include, args.label)) and not all((args.library, args.include, args.label)):
        parser.error("Explicit candidate requires --library, --include, and --label together")
    if args.label and not re.fullmatch(r"[a-z][a-z0-9_-]{0,48}", args.label):
        parser.error("Candidate label must be lowercase ASCII")
    if not 0 <= args.probe_view < 60:
        parser.error("Probe camera must be0..59")
    output = ROOT / "build/depth-study" / ((args.label or ("viewport-precision" if args.precision else "pinned")) + ("-probe" if args.probe else "") + (str(args.probe_view) if args.probe_view != 33 else "") + ("-quantized-cpu" if args.cpu_quantized_z else "") + ("-single" if args.single_view else ""))
    if args.measure_existing:
        rom, report = output / "depth.z64", json.loads((output / "provenance.json").read_text())
    else:
        rom, report = build(args.sdk.resolve(), output, args.precision, args.probe, args.cpu_quantized_z,
                            args.library, args.include, args.probe_view, args.single_view)
    if args.run or args.measure_existing:
        measure(rom, output, report, args.ares.resolve())
    print(rom)


if __name__ == "__main__":
    main()
