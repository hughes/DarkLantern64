"""Small, deterministic RGBA16 texture cook shared by preview and N64 builds.

The content compiler produces quantized PNGs without requiring an N64 SDK.
ROM builds call cook_sprites to convert those exact pixels using mksprite.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile


def check(condition, message):
    if not condition:
        raise ValueError(message)


def validate_texture(spec, source_dir, label="texture"):
    check(isinstance(spec, dict), f"{label}: expected an object")
    uri = spec.get("uri")
    check(isinstance(uri, str) and Path(uri).suffix.lower() in (".png", ".jpg", ".jpeg"),
          f"{label}.uri: expected a relative PNG or JPEG path")
    source_dir = Path(source_dir).resolve()
    path = (source_dir / uri).resolve()
    check(not Path(uri).is_absolute() and path.is_relative_to(source_dir) and path.is_file(),
          f"{label}.uri: missing texture or path outside content directory")
    check(spec.get("format", "RGBA16") == "RGBA16", f"{label}.format: only RGBA16 is supported")
    for axis in ("width", "height"):
        size = spec.get(axis)
        check(type(size) is int and 1 <= size <= 64 and size & (size-1) == 0,
              f"{label}.{axis}: expected a power of two from 1 to 64")
    check(spec["width"] * spec["height"] * 2 <= 4096,
          f"{label}: decoded texture exceeds the 4 KiB TMEM budget")
    check(path.stat().st_size <= 16 * 1024 * 1024, f"{label}: source image exceeds 16 MiB")
    from PIL import Image
    with Image.open(path) as image:
        check(image.format in ("PNG", "JPEG"), f"{label}: expected PNG or JPEG image data")
        check(image.width * image.height <= 16 * 1024 * 1024,
              f"{label}: source image exceeds 16 million pixels")
        image.verify()
    return path


def prepare_texture(spec, source_dir):
    """Return portable metadata and the exact preview/runtime input PNG bytes."""
    import PIL
    from PIL import Image
    path = validate_texture(spec, source_dir)
    source_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    recipe = {"version": 1, "source_sha256": source_sha,
              "width": spec["width"], "height": spec["height"], "format": "RGBA16",
              "resize": "BOX", "quantization": "RGB555-floor-A1-threshold128",
              "pillow": PIL.__version__,
              "cooker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    recipe_sha = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    ident = "texture-" + recipe_sha[:16]
    with Image.open(path) as image:
        image = image.convert("RGBA").resize((spec["width"], spec["height"]), Image.Resampling.BOX)
        # Replicate each five-bit channel into eight bits. mksprite's RGBA16
        # conversion truncates back to the identical five-bit value.
        pixels = [tuple(((v >> 3) << 3) | ((v >> 3) >> 2) for v in p[:3]) +
                  (255 if p[3] >= 128 else 0,) for p in image.getdata()]
        image.putdata(pixels)
        stream = io.BytesIO()
        image.save(stream, format="PNG", compress_level=9, optimize=False)
    png = stream.getvalue()
    record = {"id": ident, "uri": spec["uri"], "width": spec["width"], "height": spec["height"],
              "format": "RGBA16", "decoded_bytes": spec["width"] * spec["height"] * 2,
              "source_sha256": source_sha, "recipe_sha256": recipe_sha,
              "png_sha256": hashlib.sha256(png).hexdigest(),
              "png_path": f"editor-assets/textures/{ident}.png",
              "sprite_path": f"romfs/textures/{ident}.sprite", "recipe": recipe}
    return record, png


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == data:
        return
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def cook_sprites(output, sdk):
    """Cook the compiler's texture manifest into files ready for a DragonFS.

    All conversions succeed before publishing. Cache keys include the converter
    executable bytes, command options, and quantized input image bytes.
    """
    output, sdk = Path(output).resolve(), Path(sdk).resolve()
    source = json.loads((output / "generated/texture_report.json").read_text())
    textures = source["textures"]
    previous_path = output / "generated/texture_runtime_report.json"
    previous = json.loads(previous_path.read_text()) if previous_path.is_file() else {"textures": []}
    tool = sdk / "bin" / ("mksprite.exe" if os.name == "nt" else "mksprite")
    if textures:
        check(tool.is_file(), f"Missing SDK texture converter: {tool}")
    tool_sha = hashlib.sha256(tool.read_bytes()).hexdigest() if textures else None
    flags = ["-f", "RGBA16", "-D", "NONE", "-c", "0"]
    artifacts, cooked = {}, []
    with tempfile.TemporaryDirectory(prefix="dl64-textures-") as temporary:
        for texture in textures:
            png = output / texture["png_path"]
            check(png.resolve().is_relative_to(output) and png.is_file(), "Missing cooked texture PNG")
            check(hashlib.sha256(png.read_bytes()).hexdigest() == texture["png_sha256"],
                  "Cooked texture PNG changed; run the content compiler again")
            signature = hashlib.sha256(json.dumps({"png": texture["png_sha256"], "tool": tool_sha,
                                                  "flags": flags}, sort_keys=True).encode()).hexdigest()
            destination = (output / texture["sprite_path"]).resolve()
            check(destination.is_relative_to(output / "romfs/textures"), "Invalid texture sprite destination")
            cached = next((entry for entry in previous["textures"] if entry.get("signature") == signature
                           and entry.get("id") == texture["id"]), None)
            if cached and destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == cached["sprite_sha256"]:
                payload = destination.read_bytes()
            else:
                # The SDK's basename parser expects '/' even on Windows.
                try:
                    subprocess.run([tool.as_posix(), *flags, "-o", Path(temporary).as_posix(), png.as_posix()],
                                   check=True, capture_output=True, text=True)
                except subprocess.CalledProcessError as error:
                    raise RuntimeError(f"mksprite failed for {texture['uri']}: {error.stderr.strip()}") from error
                payload = (Path(temporary) / (png.stem + ".sprite")).read_bytes()
            artifacts[destination] = payload
            cooked.append(dict(texture, signature=signature, sprite_bytes=len(payload),
                               sprite_sha256=hashlib.sha256(payload).hexdigest()))
    for path, payload in artifacts.items():
        _atomic_write(path, payload)
    # Only remove our own previously recorded outputs, inside this build's
    # texture directory. Other DragonFS files are owned by other cookers.
    for entry in previous["textures"]:
        stale = (output / entry["sprite_path"]).resolve()
        check(stale.is_relative_to(output / "romfs/textures"), "Invalid previous texture sprite destination")
        if stale not in artifacts and stale.is_file():
            stale.unlink()
    report = {"version": 1, "mksprite_sha256": tool_sha, "flags": flags, "textures": cooked,
              "decoded_bytes": sum(t["decoded_bytes"] for t in cooked),
              "sprite_bytes": sum(t["sprite_bytes"] for t in cooked)}
    _atomic_write(previous_path, (json.dumps(report, indent=2) + "\n").encode())
    return report
