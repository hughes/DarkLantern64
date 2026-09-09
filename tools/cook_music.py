"""Cook explicitly assigned XM cues with the installed SDK, preserving source files."""
from pathlib import Path
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def cook_music(levels, output, sdk, *, root=ROOT):
    root, output, sdk = Path(root).resolve(), Path(output).resolve(), Path(sdk).resolve()
    manifest = root / "content/music.json"
    empty = {"cues": [None] * len(levels), "assets": [], "inputs": []}
    if not manifest.is_file():
        return empty
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if set(data) != {"version", "tracks", "levels"} or data["version"] != 1:
        raise ValueError("Unsupported music manifest")
    if not isinstance(data["tracks"], dict) or not isinstance(data["levels"], dict):
        raise ValueError("Music tracks and levels must be objects")
    for level, ident in data["levels"].items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", level) or ident not in data["tracks"]:
            raise ValueError(f"Invalid music assignment: {level}")
    assignments = [data["levels"].get(Path(level["source"]).stem)
                   if Path(level["source"]).resolve().parent == root / "content" else None for level in levels]
    result = {**empty, "inputs": [str(manifest)]}
    if not any(assignments):
        return result
    converter = sdk / "bin/audioconv64.exe"
    tool_hash = hashlib.sha256(converter.read_bytes()).hexdigest()
    cache = output / "music-cache"
    cache.mkdir(parents=True, exist_ok=True)
    by_id = {}
    for ident in sorted(set(assignments) - {None}):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", ident):
            raise ValueError("Music IDs must be ASCII identifiers")
        track = data["tracks"][ident]
        if not isinstance(track, dict) or set(track) != {"source", "gain"}:
            raise ValueError(f"{ident}: expected source and gain")
        gain = track["gain"]
        if type(gain) not in (int, float) or not math.isfinite(gain) or not 0 <= gain <= 1:
            raise ValueError(f"{ident}: gain must be 0..1")
        source = (root / track["source"]).resolve()
        if not source.is_relative_to(root / "art/music") or source.suffix.lower() != ".xm":
            raise ValueError(f"{ident}: expected an XM source in art/music")
        payload = source.read_bytes()
        if len(payload) < 80 or payload[:17] != b"Extended Module: " or payload[58:60] != b"\x04\x01":
            raise ValueError(f"{ident}: expected compatible XM 1.04")
        channels = struct.unpack_from("<H", payload, 68)[0]
        if not 1 <= channels <= 8:
            raise ValueError(f"{ident}: score must use 1..8 XM channels")
        source_hash = hashlib.sha256(payload).hexdigest()
        key = hashlib.sha256((source_hash + tool_hash + "xm64-v1").encode()).hexdigest()
        cooked, metadata = cache / f"{key}.xm64", cache / f"{key}.json"
        record = json.loads(metadata.read_text()) if metadata.exists() else {}
        if not cooked.exists() or record.get("sha256") != hashlib.sha256(cooked.read_bytes()).hexdigest():
            with tempfile.TemporaryDirectory(prefix="cook-", dir=cache) as stage:
                stage = Path(stage)
                # Always use a stable basename and forward slashes for the Windows converter.
                snapshot = stage / "track.xm"
                snapshot.write_bytes(payload)
                proc = subprocess.run([converter.as_posix(), "-v", "-o", stage.as_posix(), snapshot.as_posix()],
                                      capture_output=True, text=True, timeout=60)
                if proc.returncode:
                    raise ValueError(f"{ident}: music conversion failed\n{proc.stdout}{proc.stderr}")
                converted = stage / "track.xm64"
                binary = converted.read_bytes()
                if binary[:4] != b"XM64":
                    raise ValueError(f"{ident}: converter did not produce XM64")
                record = {"sha256": hashlib.sha256(binary).hexdigest(), "bytes": len(binary),
                          "conversion_log": (proc.stdout + proc.stderr).strip()}
                os.replace(converted, cooked)
                metadata.write_text(json.dumps(record, indent=2) + "\n")
        cue = {"id": ident, "path": f"rom:/music/{ident}.xm64", "gain": gain, "channels": channels}
        by_id[ident] = cue
        result["assets"].append({**record, "id": ident, "path": f"romfs/music/{ident}.xm64",
                                 "cooked_path": str(cooked), "source_sha256": source_hash,
                                 "converter_sha256": tool_hash})
        result["inputs"].extend([str(source), str(converter)])
    result["cues"] = [by_id.get(ident) for ident in assignments]
    return result
