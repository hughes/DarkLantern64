"""Build the pinned Tiny3D dependency locally, without installing into the SDK.

Run --fetch once to acquire the exact source revision. Normal builds never
fetch or move a checkout. --proof also links a small isolated RSP geometry ROM.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCY = json.loads((ROOT / "dependencies.json").read_text())["tiny3d"]
REVISION = DEPENDENCY["commit"]
URL = DEPENDENCY["repository"]
SOURCE = (ROOT / DEPENDENCY["path"]).resolve()
if not SOURCE.is_relative_to(ROOT) or SOURCE == ROOT:
    raise ValueError("Tiny3D dependency path must stay inside the project")
LIBRARY = SOURCE / "build_sdk_main/libt3d.a"
GENERATED_METADATA = ("src/t3d/rsp/rsp_tiny3d.h", "src/t3d/rsp/rsp_tinypx.h")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def library_identity(sdk: Path, bash: Path, env: dict) -> dict:
    """Make does not track compiler commands, SDK switches, or RSP .inc inputs.

    Hash content, including GCC's real cc1/assembler/linker and internal headers,
    so an in-place toolchain replacement also invalidates the dependency cache.
    A timestamp-only stamp would miss SDK copies that preserve file dates.
    """
    target = env.get("N64_TARGET", "mips64-elf")
    prefix = Path(env.get("N64_GCCPREFIX", str(sdk))).resolve()
    inputs = {Path(__file__).resolve(), SOURCE / "Makefile", bash.resolve()}
    for directory in (sdk / "include", sdk / target / "include",
                      prefix / "lib/gcc", prefix / "libexec/gcc"):
        if directory.is_dir():
            inputs.update(path for path in directory.rglob("*") if path.is_file())
    inputs.update((sdk / target / "lib").glob("*.ld"))
    for tool in ("gcc", "as", "ld", "objcopy", "objdump"):
        for suffix in ("", ".exe"):
            inputs.add(prefix / "bin" / (target + "-" + tool + suffix))
    inputs.update((prefix / "bin").glob("*.dll"))
    for directory in (sdk / "bin", bash.parent):
        inputs.update(directory / name for name in ("make", "make.exe"))
    digest = hashlib.sha256()
    for path in sorted(inputs, key=lambda item: str(item)):
        digest.update(str(path.resolve()).encode() + b"\0")
        digest.update((_sha256(path) if path.is_file() else "missing").encode() + b"\0")
    flag_names = {"CFLAGS", "CPPFLAGS", "CXXFLAGS", "ASFLAGS", "RSPASFLAGS", "LDFLAGS",
                  "D", "CCACHE", "MAKEFLAGS", "MFLAGS", "GCC_EXEC_PREFIX", "COMPILER_PATH",
                  "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH"}
    flags = {key: value for key, value in env.items()
             if key in flag_names or (key.startswith("N64_") and key != "N64_INST")}
    return {"version": 1, "revision": REVISION, "sdk": str(sdk.resolve()),
            "compiler_prefix": str(prefix), "inputs_sha256": digest.hexdigest(),
            "environment": flags}


def _invalidate_library() -> None:
    # Delete only this dependency's disposable outputs, never source or SDK files.
    # Resolve every target before doing any deletion, including junction targets.
    source = SOURCE.resolve()
    targets = [LIBRARY.parent.resolve(), *((SOURCE / name).resolve() for name in GENERATED_METADATA)]
    if (source == ROOT.resolve() or not source.is_relative_to(ROOT.resolve()) or
            any(path == source or not path.is_relative_to(source) for path in targets)):
        raise RuntimeError("Tiny3D cache outputs must stay inside its project checkout")
    if targets[0].exists():
        shutil.rmtree(targets[0])
    for path in targets[1:]:
        path.unlink(missing_ok=True)


def run(args, **kwargs):
    args = [a.as_posix() if isinstance(a, Path) else str(a) for a in args]
    print("[run] " + subprocess.list2cmdline(args), flush=True)
    return subprocess.run(args, check=True, text=True, **kwargs)


def build_library(sdk: Path, *, fetch: bool = False, bash: Path | None = None) -> Path:
    sdk = sdk.resolve()
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1")
    if fetch:
        if not SOURCE.exists():
            SOURCE.parent.mkdir(parents=True, exist_ok=True)
            run(["git", "init", SOURCE], env=env)
            run(["git", "-C", SOURCE, "remote", "add", "origin", URL], env=env)
        changes = run(["git", "-C", SOURCE, "status", "--porcelain", "--untracked-files=no"],
                      env=env, capture_output=True).stdout
        if changes.strip():
            raise RuntimeError("Tiny3D has local tracked edits; preserve them before changing revisions")
        run(["git", "-C", SOURCE, "fetch", "--depth", "1", URL, REVISION], env=env)
        run(["git", "-C", SOURCE, "checkout", "--detach", REVISION], env=env)
    if not (SOURCE / ".git").exists():
        raise RuntimeError("Tiny3D is missing; run python tools/build_tiny3d.py --fetch")
    actual = run(["git", "-C", SOURCE, "rev-parse", "HEAD"], capture_output=True).stdout.strip()
    if actual != REVISION:
        raise RuntimeError(f"Tiny3D must be pinned to {REVISION}; found {actual}")
    changes = run(["git", "-C", SOURCE, "status", "--porcelain", "--untracked-files=no"],
                  capture_output=True).stdout
    if changes.strip():
        raise RuntimeError("Tiny3D has local tracked edits; pinned builds require the reviewed source")
    bash = bash or Path(os.environ.get("MSYS2_BASH", "C:/msys64/usr/bin/bash.exe"))
    if not bash.is_file():
        raise RuntimeError("MSYS2 bash is missing; set MSYS2_BASH or pass --bash")
    identity = library_identity(sdk, bash, env)
    stamp = LIBRARY.parent / "dl64-build-identity.json"
    try:
        cached = json.loads(stamp.read_text())
    except (OSError, ValueError):
        cached = {}
    reusable = (isinstance(cached, dict) and cached.get("identity") == identity and
                LIBRARY.is_file() and cached.get("library_sha256") == _sha256(LIBRARY) and
                all((SOURCE / name).is_file() for name in GENERATED_METADATA))
    if not reusable:
        print("[tiny3d] SDK/toolchain identity changed or cache is incomplete; rebuilding dependency", flush=True)
        _invalidate_library()
    temporary = ROOT / ".dev/tiny3d-tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    # Pass paths as positional arguments, not interpolated shell code. MSYS2's
    # make needs POSIX paths but the cross compiler is a native Windows binary.
    script = """set -eu
export PATH="/usr/bin:$PATH"
export N64_INST="$(cygpath -u "$1")"
export PATH="$N64_INST/bin:/usr/bin:$PATH"
export TMPDIR="$(cygpath -u "$3")"
export TEMP="$TMPDIR" TMP="$TMPDIR"
cd "$(cygpath -u "$2")"
make -j4 all BUILD_DIR=build_sdk_main
"""
    run([bash, "-c", script, "tiny3d-build", sdk, SOURCE, temporary], env=env)
    if not LIBRARY.is_file():
        raise RuntimeError("Tiny3D build did not produce its library")
    library_sha256 = _sha256(LIBRARY)
    pending_stamp = stamp.with_suffix(".tmp")
    pending_stamp.write_text(json.dumps({"identity": identity, "library_sha256": library_sha256}, indent=2) + "\n")
    pending_stamp.replace(stamp)
    report = {
        "revision": REVISION, "source_url": URL, "sdk": str(sdk),
        "library": str(LIBRARY), "include": str(SOURCE / "src"),
        "library_sha256": library_sha256, "build_identity": identity,
        "dependency_cache_rebuilt": not reusable,
        "sdk_modified": False,
    }
    (ROOT / "build").mkdir(exist_ok=True)
    (ROOT / "build/tiny3d-dependency.json").write_text(json.dumps(report, indent=2) + "\n")
    return LIBRARY


def build_proof(sdk: Path, library: Path) -> Path:
    output = ROOT / "build/tiny3d-proof"
    output.mkdir(parents=True, exist_ok=True)
    bins, lib = sdk / "bin", sdk / "mips64-elf/lib"
    env = dict(os.environ, N64_INST=str(sdk), PATH=str(bins) + os.pathsep + os.environ.get("PATH", ""))
    obj, elf = output / "main.o", output / "proof.elf"
    run([bins / "mips64-elf-gcc.exe", "-march=vr4300", "-mtune=vr4300", "-mabi=o64",
         "-std=gnu17", "-O2", "-g", "-Wall", "-Wextra", "-Werror", "-DN64",
         "-ffunction-sections", "-fdata-sections", "-I" + str(sdk / "mips64-elf/include"),
         "-I" + str(SOURCE / "src"), "-c", ROOT / "tools/tiny3d/proof.c", "-o", obj], env=env)
    run([bins / "mips64-elf-g++.exe", "-mabi=o64", "-g", "-o", elf, obj, library,
         "-L" + str(lib), "-lc", "-ldragon", "-lm", "-ldragonsys",
         "-Wl,-T," + str(lib / "n64.ld"), "-Wl,--gc-sections,--wrap,__do_global_ctors",
         "-Wl,-Map=" + str(output / "proof.map")], env=env)
    symbols = output / "proof.sym"
    run([bins / "n64sym.exe", elf, symbols], env=env)
    stripped = output / "uncompressed/program.elf"
    compressed = output / "compressed"
    stripped.parent.mkdir(exist_ok=True)
    compressed.mkdir(exist_ok=True)
    run([bins / "mips64-elf-strip.exe", "-s", "-o", stripped, elf], env=env)
    run([bins / "n64elfcompress.exe", "-o", compressed, "-c", "1", stripped], env=env)
    if not (compressed / "program.elf").is_file():
        raise RuntimeError("n64elfcompress did not produce the expected program.elf")
    rom = output / "tiny3d-proof.z64"
    run([bins / "n64tool.exe", "--title", "DL64 Tiny3D proof", "--toc", "--output", rom,
         "--align", "256", compressed / "program.elf", "--align", "8", symbols], env=env)
    run([bins / "mips64-elf-size.exe", elf], env=env)
    return rom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--bash", type=Path)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--proof", action="store_true")
    args = parser.parse_args()
    sdk = args.sdk.resolve()
    library = build_library(sdk, fetch=args.fetch, bash=args.bash)
    print(build_proof(sdk, library) if args.proof else library)


if __name__ == "__main__":
    main()
