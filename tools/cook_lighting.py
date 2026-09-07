"""Prepare static night colors with shared lighting/normal kernels and the
legacy renderer's exact transform and color arithmetic.

Only generated artifacts are cached. The host sees a standalone copy of the
target header, including its float literals and quantized normals. All worker
inputs are snapshotted before compilation; failures publish no level assets.
DL64_HOST_CC optionally selects a host GCC executable; otherwise the existing
build.py lookup (gcc on PATH, then MSYS2 UCRT64) is used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
FLAGS = ["-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-ffp-contract=off", "-fexcess-precision=standard"]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == raw:
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(raw)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(command, env):
    try:
        return subprocess.run([str(v) for v in command], env=env, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        raise RuntimeError("Lighting cooker failed: " + " ".join(str(v) for v in command[:3]) +
                           "\n" + error.stdout + error.stderr) from error


def host_compiler():
    # Match build.py's established host-test selection. Prepending its actual
    # bin directory is essential for Windows GCC's child compiler DLLs.
    override = os.environ.get("DL64_HOST_CC")
    fallback = next((path for path in ("C:/ProgramData/mingw64/mingw64/bin/gcc.exe",
                                       "C:/msys64/ucrt64/bin/gcc.exe") if Path(path).is_file()), "gcc")
    compiler = Path((shutil.which(override) or override) if override else
                    (shutil.which("gcc") or fallback)).resolve()
    if not compiler.is_file():
        raise RuntimeError("Static night-lighting cook requires host GCC (the existing portable-test compiler).")
    env = dict(os.environ, PATH=str(compiler.parent) + os.pathsep + os.environ.get("PATH", ""))
    identity = {"path": compiler.as_posix(), "sha256": digest(compiler.read_bytes()),
                "version": run([compiler, "--version"], env).stdout,
                "machine": run([compiler, "-dumpmachine"], env).stdout.strip(), "libraries": {}}
    # Include host math/compiler support archives rather than relying only on
    # a GCC version string when the host installation is updated in place.
    for name in ("libm.a", "libgcc.a", "libmingwex.a", "libmsvcrt.a", "crt2.o"):
        path = Path(run([compiler, "-print-file-name=" + name], env).stdout.strip())
        if path.is_file():
            identity["libraries"][name] = digest(path.read_bytes())
    return compiler, env, identity


def snapshot_sources():
    names = ["src/game.c", "src/static_lighting.c", "src/render.c", "tools/bake_lighting.c", "tools/cook_lighting.py"]
    names += [p.relative_to(ROOT).as_posix() for pattern in ("*.h", "*.def") for p in sorted((ROOT / "src").glob(pattern))]
    return {name: (ROOT / name).read_bytes() for name in sorted(set(names))}


def metadata(payload, signature):
    if len(payload) < 64 or payload[:4] != b"DLC1":
        raise ValueError("Invalid lighting worker header")
    version, header_bytes = struct.unpack_from(">HH", payload, 4)
    total, models, records, triangles, rgb_bytes, crc, reserved = struct.unpack_from(">IHHIIII", payload, 40)
    if version != 1 or header_bytes != 64 or reserved or total != len(payload) or total != 64 + records * 12 + rgb_bytes:
        raise ValueError("Invalid lighting worker dimensions")
    if payload[8:40] != bytes.fromhex(signature) or crc != zlib.crc32(payload[64:]):
        raise ValueError("Lighting worker fingerprint/checksum mismatch")
    previous = -1
    static_triangles = expected_rgb = 0
    for i in range(records):
        model, count, start, sides, reserved = struct.unpack_from(">HHIHH", payload, 64 + i * 12)
        if model <= previous or model >= models or not count or start + count > triangles or sides not in (1, 2) or reserved:
            raise ValueError("Invalid lighting worker model record")
        previous = model
        static_triangles += count
        expected_rgb += count * 9 * 2 * sides
    if expected_rgb != rgb_bytes:
        raise ValueError("Lighting worker payload length mismatch")
    return {"version": version, "signature": signature, "path": f"romfs/lighting/lighting-{signature}.bin",
            "sha256": digest(payload), "bytes": len(payload), "rgb_bytes": rgb_bytes,
            "static_models": records, "static_triangles": static_triangles, "states": 2,
            "level_models": models, "level_triangles": triangles}


def _cached(path, checksum_path):
    try:
        raw = path.read_bytes()
        return raw if digest(raw) == checksum_path.read_text().strip() else None
    except (OSError, UnicodeError):
        return None


def prepare_lighting(unbaked_header, *, cache_root=None):
    """Return (report, bytes), without publishing any level output.

    Call only for a night level. Source/header and toolchain hashes are a
    deliberately conservative dependency key, independent of authored content
    provenance. Shared-character bundles supply a standalone header here.
    """
    header = unbaked_header.encode("utf-8") if isinstance(unbaked_header, str) else unbaked_header
    sources = snapshot_sources()
    compiler, env, identity = host_compiler()
    recipe = {"version": 1, "header_sha256": digest(header), "flags": FLAGS,
              "compiler": identity, "sources": {name: digest(raw) for name, raw in sources.items()}}
    signature = digest(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode())
    cache = Path(cache_root) if cache_root is not None else ROOT / "build/lighting-cache"
    cache.mkdir(parents=True, exist_ok=True)
    binary, checksum = cache / (signature + ".bin"), cache / (signature + ".sha256")
    payload = _cached(binary, checksum)
    if payload is None:
        common_recipe = {key: value for key, value in recipe.items() if key != "header_sha256"}
        common_key = digest(json.dumps(common_recipe, sort_keys=True, separators=(",", ":")).encode())
        objects = cache / "objects" / common_key
        with tempfile.TemporaryDirectory(prefix="lighting-", dir=cache) as temporary:
            temporary = Path(temporary)
            for name, raw in sources.items():
                path = temporary / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            (temporary / "demo_level.h").write_bytes(header)
            object_files = []
            for name in ("game", "static_lighting"):
                obj, sha = objects / (name + ".o"), objects / (name + ".sha256")
                if _cached(obj, sha) is None:
                    candidate = temporary / (name + ".o")
                    run([compiler, *FLAGS, "-I" + str(temporary / "src"), "-c", temporary / "src" / (name + ".c"), "-o", candidate], env)
                    raw = candidate.read_bytes()
                    atomic(obj, raw)
                    atomic(sha, (digest(raw) + "\n").encode())
                object_files.append(obj)
            worker = temporary / ("bake_lighting.exe" if os.name == "nt" else "bake_lighting")
            run([compiler, *FLAGS, "-I" + str(temporary / "src"), "-I" + str(temporary),
                 temporary / "tools/bake_lighting.c", *object_files, "-lm", "-o", worker], env)
            result = temporary / "lighting.bin"
            run([worker, result], env)
            raw = bytearray(result.read_bytes())
            if len(raw) < 64:
                raise ValueError("Truncated lighting worker output")
            raw[8:40] = bytes.fromhex(signature)
            struct.pack_into(">I", raw, 56, zlib.crc32(raw[64:]))
            payload = bytes(raw)
            metadata(payload, signature)  # Validate completely before caching.
            atomic(binary, payload)
            atomic(checksum, (digest(payload) + "\n").encode())
    report = metadata(payload, signature)
    report["recipe"] = recipe
    if any((ROOT / name).read_bytes() != raw for name, raw in sources.items()):
        raise RuntimeError("Lighting source code changed during cooking; retry the cook.")
    return report, payload
