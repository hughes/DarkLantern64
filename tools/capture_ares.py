"""Export authored views from the actual N64 RDP framebuffer in isolated Ares.

The diagnostic ROM waits for graphics and dumps pixels; do not use its timings
as a performance benchmark. PNGs are raw 320x240 buffers, before VI filtering.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def validate_timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ValueError("Capture timeout must be a positive finite number of seconds")
    return value


def validate_views(data):
    """Validate cheap authoring inputs before compiling or launching Ares."""
    views = data.get("views") if isinstance(data, dict) else None
    if not isinstance(views, list) or not 1 <= len(views) <= 8:
        raise ValueError("Capture requires 1-8 authored views in the level's views array")
    seen = set()
    reserved = {"con", "prn", "aux", "nul"} | {f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10)}
    for index, view in enumerate(views, 1):
        if not isinstance(view, dict):
            raise ValueError(f"Capture view {index} must be an object")
        name = view.get("id")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name) or name.casefold() in reserved:
            raise ValueError(f"Capture view {index} needs a safe ASCII ID of 1-64 characters")
        if name.casefold() in seen:
            raise ValueError(f"Duplicate capture view ID: {name}")
        seen.add(name.casefold())
        position = view.get("position")
        if not isinstance(position, list) or len(position) != 3:
            raise ValueError(f"Capture view {name} needs an XYZ position array")
        values = position + [view.get("yaw"), view.get("pitch")]
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
            raise ValueError(f"Capture view {name} needs finite XYZ, yaw and pitch")
        if any(abs(v) > 1024 for v in position):
            raise ValueError(f"Capture view {name} position is outside the +/-1024 metre content range")
        if abs(view["yaw"]) > 3600 or abs(view["pitch"]) > 90:
            raise ValueError(f"Capture view {name} angles exceed yaw +/-3600 or pitch +/-90 degrees")
        if "door_open" in view and type(view["door_open"]) is not bool:
            raise ValueError(f"Capture view {name} door_open must be a boolean")
        animation_time = view.get("animation_time", 0)
        if type(animation_time) not in (int, float) or not math.isfinite(animation_time) or not 0 <= animation_time <= 3600:
            raise ValueError(f"Capture view {name} animation_time must be finite seconds in 0..3600")
        clip = view.get("animation_clip", "")
        if not isinstance(clip, str) or (clip and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", clip)):
            raise ValueError(f"Capture view {name} animation_clip must be a safe ASCII clip ID")
        for key, maximum in (("head_yaw", 45), ("head_pitch", 25)):
            angle = view.get(key, 0)
            if type(angle) not in (int, float) or not math.isfinite(angle) or abs(angle) > maximum:
                raise ValueError(f"Capture view {name} {key} must be finite degrees within +/-{maximum}")
    return views


def decode_captures(text, expected_count=None, expected_dimensions=None):
    if expected_count is not None and (type(expected_count) is not int or not 1 <= expected_count <= 8):
        raise ValueError("Expected capture count must be from 1 to 8")
    frames, current, complete = {}, None, False
    for line in text.splitlines():
        if line.startswith("DL64 capture_begin"):
            match = re.fullmatch(r"DL64 capture_begin id=(\d+) width=(\d+) height=(\d+) format=rgba5551", line)
            if not match or current or complete:
                raise ValueError("Malformed or overlapping framebuffer capture")
            ident, width, height = map(int, match.groups())
            if ident in frames or not 1 <= ident <= (expected_count or 8) or not 0 < width <= 640 or not 0 < height <= 480:
                raise ValueError("Invalid capture dimensions/identity")
            if expected_dimensions is not None and (width, height) != expected_dimensions:
                raise ValueError("Unexpected framebuffer capture dimensions")
            current = {"id": ident, "width": width, "height": height, "rows": {}}
        elif line.startswith("DL64 capture_row"):
            match = re.fullmatch(r"DL64 capture_row id=(\d+) y=(\d+) data=([0-9a-f]+)", line)
            if not match or current is None:
                raise ValueError("Invalid capture row")
            ident, y, data = match.groups()
            y = int(y)
            if int(ident) != current["id"] or y in current["rows"] or not 0 <= y < current["height"] or len(data) != current["width"] * 4:
                raise ValueError("Capture row missing pixels, duplicated, or out of bounds")
            current["rows"][y] = data
        elif line.startswith("DL64 capture_end"):
            if current is None or line != f"DL64 capture_end id={current['id']}" or len(current["rows"]) != current["height"]:
                raise ValueError("Incomplete framebuffer capture")
            pixels = bytearray()
            for y in range(current["height"]):
                row = current["rows"][y]
                for x in range(current["width"]):
                    value = int(row[x*4:x*4+4], 16)
                    # Framebuffer alpha stores coverage, not screenshot transparency.
                    pixels.extend(((value >> 11 & 31) * 255 // 31,
                                   (value >> 6 & 31) * 255 // 31,
                                   (value >> 1 & 31) * 255 // 31))
            frames[current["id"]] = (current["width"], current["height"], bytes(pixels))
            current = None
        elif line.startswith("DL64 capture_complete"):
            match = re.fullmatch(r"DL64 capture_complete views=(\d+)", line)
            if not match or current or complete or int(match[1]) != len(frames) or not frames:
                raise ValueError("Invalid framebuffer capture completion record")
            complete = True
        elif line.startswith("DL64 capture_"):
            raise ValueError("Unknown framebuffer capture record")
    if current:
        raise ValueError("Incomplete framebuffer capture at end of log")
    if not frames:
        raise ValueError("No complete framebuffer captures")
    expected = set(range(1, (expected_count or len(frames)) + 1))
    if set(frames) != expected:
        raise ValueError("ROM did not capture the authored view IDs 1 through N")
    return frames


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", type=Path, default=ROOT / "content/moonlit_courtyard.json")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))))
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--renderer", choices=("cpu", "t3d"), default="t3d",
                        help="Renderer to capture; Tiny3D preserves the CPU capture report")
    args = parser.parse_args(argv)
    try:
        validate_timeout(args.timeout)
        views = validate_views(json.loads(args.level.read_text()))
        from PIL import Image
        from build import build_rom, scene_paths
        from smoke_ares import exercise
        level, cooked, _ = scene_paths(args.level)
        rom = build_rom(args.sdk, level=level, capture=True, renderer=args.renderer)
        settings = ROOT / ".dev/ares/settings-8mb.bml"
        if not settings.is_file():
            settings = args.ares.parent / "settings.bml"
        result = exercise(args.ares, settings, rom, "capture-" + level.stem, "DL64 capture_complete", args.timeout,
                          hashlib.sha256(rom.read_bytes()).hexdigest())
        log = ROOT / result["log"]
        frames = decode_captures(log.read_text(), expected_count=len(views), expected_dimensions=(320, 240))
        output = Path(result["tested_rom"]).parent
        captures = []
        for ident, (width, height, pixels) in sorted(frames.items()):
            name = views[ident-1]["id"]
            destination = ROOT / output / (name + ".png")
            Image.frombytes("RGB", (width, height), pixels).save(destination)
            captures.append({"view": views[ident-1], "image": str(destination),
                             "sha256": hashlib.sha256(destination.read_bytes()).hexdigest()})
        report = dict(result, renderer=args.renderer, captures=captures,
                      measurement="Raw RDP framebuffer before VI filtering; capture timings are diagnostic only")
        report_path = cooked / ("captures.json" if args.renderer == "cpu" else "captures-t3d.json")
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Capture failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
