"""Cached native lighting for DLR1 v4 and persistent bootstrap visuals.

The host uses the target's serialized mesh coordinates, quantized normals and
shared C shading kernel, with the complete mission's shadow blockers. The
runtime's 400-collider working-set bound is lifted only in the snapshotted host
game.c; this does not alter target gameplay limits or add streaming barriers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import tempfile
import zlib

try:
    import cook_lighting as common
except ModuleNotFoundError:
    from tools import cook_lighting as common

ROOT = Path(__file__).resolve().parents[1]


def metadata(payload, signature):
    if len(payload) < 64 or payload[:4] != b"DLS1":
        raise ValueError("Invalid stream lighting worker header")
    version, header_bytes = struct.unpack_from(">HH", payload, 4)
    total, models, records, triangles, color_bytes, crc, reserved = struct.unpack_from(">IHHIIII", payload, 40)
    if version != 1 or header_bytes != 64 or reserved or total != len(payload) or total != 64 + records * 12 + color_bytes:
        raise ValueError("Invalid stream lighting worker dimensions")
    if payload[8:40] != bytes.fromhex(signature) or crc != zlib.crc32(payload[64:]):
        raise ValueError("Stream lighting worker fingerprint/checksum mismatch")
    previous = -1
    cursor = 64 + records * 12
    result = {}
    for index in range(records):
        model, count, start, sides, reserved = struct.unpack_from(">HHIHH", payload, 64 + index * 12)
        if model <= previous or model >= models or not count or start + count > triangles or sides != 2 or reserved:
            raise ValueError("Invalid stream lighting model record")
        previous = model
        size = count * 24
        colors = (payload[cursor:cursor + size], payload[cursor + size:cursor + size * 2])
        if any(len(raw) != size or any(alpha != 255 for alpha in raw[3::4]) for raw in colors):
            raise ValueError("Invalid stream lighting RGBA payload")
        result[model] = colors
        cursor += size * 2
    if cursor != len(payload):
        raise ValueError("Stream lighting worker payload length mismatch")
    return dict(version=1, signature=signature, sha256=common.digest(payload), bytes=len(payload),
                rgba_bytes=color_bytes, static_models=records,
                static_triangles=color_bytes // 48, states=2,
                level_models=models, level_triangles=triangles), result


def prepare_stream_lighting(unbaked_header, *, collider_count, floor_count=0, cache_root=None):
    """Return report and model-indexed (closed, open) triangle RGBA bytes."""
    header = unbaked_header.encode("utf-8") if isinstance(unbaked_header, str) else unbaked_header
    sources = common.snapshot_sources()
    for name in ("tools/compile_level.py", "tools/compile_mission.py", "tools/cook_stream_lighting.py", "tools/bake_stream_lighting.c"):
        sources[name] = (ROOT / name).read_bytes()
    compiler, env, identity = common.host_compiler()
    host_limit, host_floors = max(400, collider_count), max(256, floor_count)
    flags = [*common.FLAGS, "-DDL_HOST_LIGHTING_MAX_COLLIDERS=" + str(host_limit),
             "-DDL_HOST_LIGHTING_MAX_FLOORS=" + str(host_floors)]
    recipe = dict(version=2, header_sha256=common.digest(header), host_collider_limit=host_limit,
                  host_floor_limit=host_floors,
                  flags=flags, compiler=identity,
                  sources={name: common.digest(raw) for name, raw in sources.items()})
    signature = common.digest(json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode())
    cache = Path(cache_root) if cache_root is not None else ROOT / "build/stream-lighting-cache"
    cache.mkdir(parents=True, exist_ok=True)
    binary, checksum = cache / (signature + ".bin"), cache / (signature + ".sha256")
    payload = common._cached(binary, checksum)
    if payload is None:
        common_recipe = {key: value for key, value in recipe.items() if key != "header_sha256"}
        common_key = common.digest(json.dumps(common_recipe, sort_keys=True, separators=(",", ":")).encode())
        objects = cache / "objects" / common_key
        with tempfile.TemporaryDirectory(prefix="stream-lighting-", dir=cache) as temporary:
            temporary = Path(temporary)
            for name, raw in sources.items():
                path = temporary / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            (temporary / "demo_level.h").write_bytes(header)
            object_files = []
            for name in ("game", "static_lighting"):
                obj, sha = objects / (name + ".o"), objects / (name + ".sha256")
                if common._cached(obj, sha) is None:
                    candidate = temporary / (name + ".o")
                    common.run([compiler, *flags, "-I" + str(temporary / "src"), "-c", temporary / "src" / (name + ".c"), "-o", candidate], env)
                    raw = candidate.read_bytes()
                    common.atomic(obj, raw)
                    common.atomic(sha, (common.digest(raw) + "\n").encode())
                object_files.append(obj)
            worker = temporary / ("bake_stream_lighting.exe" if os.name == "nt" else "bake_stream_lighting")
            common.run([compiler, *flags, "-I" + str(temporary / "src"), "-I" + str(temporary),
                        temporary / "tools/bake_stream_lighting.c", *object_files, "-lm", "-o", worker], env)
            result = temporary / "lighting.bin"
            common.run([worker, result], env)
            raw = bytearray(result.read_bytes())
            if len(raw) < 64:
                raise ValueError("Truncated stream lighting worker output")
            raw[8:40] = bytes.fromhex(signature)
            struct.pack_into(">I", raw, 56, zlib.crc32(raw[64:]))
            payload = bytes(raw)
            metadata(payload, signature)
            common.atomic(binary, payload)
            common.atomic(checksum, (common.digest(payload) + "\n").encode())
    report, colors = metadata(payload, signature)
    report["recipe"] = recipe
    if any((ROOT / name).read_bytes() != raw for name, raw in sources.items()):
        raise RuntimeError("Stream lighting source code changed during cooking; retry the cook.")
    return report, colors
