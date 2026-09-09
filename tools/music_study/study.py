"""Cook and verify Lanterns Below; optionally build a standalone N64 audition.

This bounded experiment does not modify the game's asset catalogue or runtime.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "art/music/lanterns_below"
OUT = ROOT / "build/music-study/lanterns-below"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, timeout=90):
    # audioconv64's basename handling requires forward slashes on Windows.
    result = subprocess.run([a.as_posix() if isinstance(a, Path) else str(a) for a in args], cwd=ROOT, capture_output=True,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"Command failed: {args}\n{result.stdout}\n{result.stderr}")
    return result.stdout + result.stderr


def build_rom(sdk):
    work = OUT / "rom"
    fs = work / "romfs"
    fs.mkdir(parents=True, exist_ok=True)
    for suffix in ("xm64", "wav64"):
        shutil.copyfile(OUT / f"lanterns_below.{suffix}", fs / f"lanterns_below.{suffix}")
    bins, lib = sdk / "bin", sdk / "mips64-elf/lib"
    run([bins / "mips64-elf-gcc.exe", "-march=vr4300", "-mtune=vr4300", "-mabi=o64",
         "-std=gnu17", "-O2", "-g", "-Wall", "-Wextra", "-Werror", "-DN64",
         "-ffunction-sections", "-fdata-sections", "-I" + str(sdk / "mips64-elf/include"),
         "-c", ROOT / "tools/music_study/player.c", "-o", work / "player.o"])
    elf = work / "player.elf"
    run([bins / "mips64-elf-g++.exe", "-mabi=o64", "-g", "-o", elf, work / "player.o",
         "-L" + str(lib), "-lc", "-ldragon", "-lm", "-ldragonsys",
         "-Wl,-T," + str(lib / "n64.ld"), "-Wl,--gc-sections,--wrap,__do_global_ctors"])
    run([bins / "n64sym.exe", elf, work / "player.sym"])
    run([bins / "mips64-elf-strip.exe", "-s", "-o", work / "program.elf", elf])
    compressed = work / "compressed"
    compressed.mkdir(exist_ok=True)
    run([bins / "n64elfcompress.exe", "-o", compressed, "-c", "1", work / "program.elf"])
    run([bins / "mkdfs.exe", work / "music.dfs", fs])
    rom = OUT / "lanterns_below.z64"
    run([bins / "n64tool.exe", "--title", "DL64 Music Study", "--toc", "--output", rom,
         "--align", "256", compressed / "program.elf", "--align", "8", work / "player.sym",
         "--align", "16", work / "music.dfs"])
    return dict(path=rom.relative_to(ROOT).as_posix(), bytes=rom.stat().st_size,
                sha256=digest(rom), player_source_sha256=digest(ROOT / "tools/music_study/player.c"),
                libdragon_sha256=digest(lib / "libdragon.a"),
                linked_size=run([bins / "mips64-elf-size.exe", elf]).strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--libdragon", type=Path, default=ROOT.parent / "libdragon")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--gcc", default="gcc")
    parser.add_argument("--rom", action="store_true")
    parser.add_argument("--compose", action="store_true", help="Regenerate XM/samples from score.json; overwrites generated source assets")
    args = parser.parse_args()
    os.environ["N64_INST"] = args.sdk.as_posix()
    os.environ["PATH"] = str(args.sdk / "bin") + os.pathsep + os.environ.get("PATH", "")
    OUT.mkdir(parents=True, exist_ok=True)
    if args.compose:
        run([sys.executable, ROOT / "tools/music_study/compose.py"])
    xm = SOURCE / "lanterns_below.xm"
    header = xm.read_bytes()[:80]
    if len(header) < 80 or header[:17] != b"Extended Module: ":
        raise ValueError("Expected the study's compatible XM file")
    orders, restart, channels, patterns, instruments, flags, speed, bpm = struct.unpack_from("<8H", header, 64)
    if (orders, restart, channels, patterns, instruments, speed, bpm) != (4, 0, 8, 4, 7, 6, 80):
        raise ValueError("This fixture renderer expects the 48-second, eight-channel study profile; duration traversal for arbitrary songs is not implemented")
    converter = args.sdk / "bin/audioconv64.exe"
    conversion = run([converter, "-v", "-o", OUT, xm])
    (OUT / "conversion.log").write_text(conversion)
    libxm = args.libdragon / "src/audio/libxm"
    renderer = OUT / "render_xm.exe"
    core = [libxm / (name + ".c") for name in ("play", "context", "load")]
    run([args.gcc, "-std=gnu11", "-O2", "-I", libxm,
         ROOT / "tools/music_study/render_xm.c", *core, "-lm", "-o", renderer])
    source_report = json.loads((SOURCE / "source_report.json").read_text())
    frames = round(source_report["duration_seconds"] * 32000)
    renders = {}
    for name, path, block in [("source", xm, 256),
                               ("cooked", OUT / "lanterns_below.xm64", 256),
                               ("partition", OUT / "lanterns_below.xm64", 997)]:
        renders[name] = json.loads(run([renderer, path, OUT / f"{name}.f32", frames * 2 + 1, block]))
    a, b, c = [np.fromfile(OUT / f"{name}.f32", dtype="<f4").reshape(-1, 2)
               for name in ("source", "cooked", "partition")]
    checks = dict(source_cooked_exact=bool(np.array_equal(a, b)),
                  block_partition_exact=bool(np.array_equal(b, c)),
                  two_loops_exact=bool(np.array_equal(b[:frames], b[frames:frames * 2])),
                  loop_boundary_jump=(b[frames] - b[frames-1]).tolist(),
                  finite=bool(np.isfinite(b).all()),
                  clipped_samples=int((abs(b) >= 1).sum()),
                  peak=float(abs(b).max()),
                  rms_dbfs=float(20 * np.log10(np.sqrt(np.mean(b*b)))))
    assert checks["finite"] and not checks["clipped_samples"]
    assert checks["source_cooked_exact"] and checks["block_partition_exact"] and checks["two_loops_exact"]
    reference = OUT / "lanterns_below.wav"
    with wave.open(str(reference), "wb") as out:
        out.setparams((2, 2, 32000, frames, "NONE", "not compressed"))
        out.writeframes(np.rint(b[:frames] * 32767).astype("<i2").tobytes())
    wav_conversion = run([converter, "-v", "--wav-compress", "1", "--wav-loop", "true",
                          "-o", OUT, reference])
    (OUT / "wav_conversion.log").write_text(wav_conversion)
    buffers = [int(n) for n in re.search(r"Samples RAM per channel: \[([^]]+)\]", conversion).group(1).split(",")]
    report = dict(title="Lanterns Below", status="host verified; see separate console evidence",
                  duration_seconds=frames / 32000, channels=8, instruments=7,
                  converter_sha256=digest(converter),
                  host_core_sha256={p.name: digest(p) for p in [*core, libxm / "xm.h", libxm / "xm_internal.h"]},
                  renderer_source_sha256=digest(ROOT / "tools/music_study/render_xm.c"),
                  compiler=run([args.gcc, "--version"]).splitlines()[0],
                  conversion_log=conversion.strip(), converter_channel_buffers_bytes=buffers,
                  converter_total_channel_buffers_bytes=sum(buffers), host_render_checks=checks,
                  renderer_results=renders,
                  caveat="Host libxm loads samples resident and uses its CPU mixer. These checks do not establish RSP PCM parity, target heap peaks, or game performance.",
                  artifacts={})
    for path in [xm, OUT / "lanterns_below.xm64", reference, OUT / "lanterns_below.wav64"]:
        report["artifacts"][path.suffix[1:]] = dict(path=path.relative_to(ROOT).as_posix(),
                                                  bytes=path.stat().st_size, sha256=digest(path))
    if args.rom:
        report["rom"] = build_rom(args.sdk)
    evidence = ROOT / "docs/evidence/music-study.json"
    evidence.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
