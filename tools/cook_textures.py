"""Deterministic RGBA16/CI4/CI8 texture cook shared by preview and N64 builds.

The content compiler produces quantized PNGs without requiring an N64 SDK.
ROM builds call cook_sprites to convert those exact pixels using mksprite.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile


def check(condition, message):
    if not condition:
        raise ValueError(message)


def texture_memory(width, height, fmt):
    """Packed RDRAM pixels and row-aligned TMEM are different allocations."""
    bits = {"RGBA16": 16, "CI4": 4, "CI8": 8}[fmt]
    row_bytes = (width * bits + 7) // 8
    pixel_bytes = row_bytes * height
    palette_bytes = {"RGBA16": 0, "CI4": 32, "CI8": 512}[fmt]
    tmem_row_bytes = (row_bytes + 7) & ~7
    palette_reservation = 2048 if palette_bytes else 0
    return {"decoded_bytes": pixel_bytes, "pixel_bytes": pixel_bytes,
            "palette_bytes": palette_bytes, "decoded_total_bytes": pixel_bytes + palette_bytes,
            "row_bytes": row_bytes, "tmem_row_bytes": tmem_row_bytes,
            "tmem_pixel_bytes": tmem_row_bytes * height,
            "tmem_palette_reserved_bytes": palette_reservation,
            "tmem_bytes": tmem_row_bytes * height + palette_reservation}


def texture_memory_summary(textures):
    pixels = sum(t["decoded_bytes"] for t in textures)
    palettes = sum(t.get("palette_bytes", 0) for t in textures)
    return {"decoded_bytes": pixels, "pixel_bytes": pixels, "palette_bytes": palettes,
            "decoded_total_bytes": pixels + palettes,
            "memory_note": "Unique packed pixel bytes (decoded_bytes) and RGBA16 palette bytes are separate. "
                           "decoded_total_bytes includes both, excluding sprite headers, alignment and allocator overhead. "
                           "TMEM rows align to 8 bytes; indexed textures reserve its upper 2 KiB for palettes. "
                           "The renderer uploads one complete texture at a time."}


def validate_palette(palette, fmt, label="texture.palette"):
    """Explicit palettes use opaque HTML RGB colors and preserve author order."""
    check(fmt in ("CI4", "CI8"), f"{label}: an explicit palette requires CI4 or CI8")
    limit = 16 if fmt == "CI4" else 256
    check(isinstance(palette, list) and 1 <= len(palette) <= limit,
          f"{label}: expected 1 to {limit} colors")
    check(all(isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color) for color in palette),
          f"{label}: each opaque color must be a #RRGGBB string")
    return [color.upper() for color in palette]


def validate_texture(spec, source_dir, label="texture"):
    check(isinstance(spec, dict), f"{label}: expected an object")
    uri = spec.get("uri")
    check(isinstance(uri, str) and Path(uri).suffix.lower() in (".png", ".jpg", ".jpeg"),
          f"{label}.uri: expected a relative PNG or JPEG path")
    source_dir = Path(source_dir).resolve()
    path = (source_dir / uri).resolve()
    check(not Path(uri).is_absolute() and path.is_relative_to(source_dir) and path.is_file(),
          f"{label}.uri: missing texture or path outside content directory")
    fmt = spec.get("format", "RGBA16")
    check(fmt in ("RGBA16", "CI4", "CI8"), f"{label}.format: expected RGBA16, CI4 or CI8")
    if "palette" in spec:
        validate_palette(spec["palette"], fmt, f"{label}.palette")
    for axis in ("width", "height"):
        size = spec.get(axis)
        check(type(size) is int and 1 <= size <= 64 and size & (size-1) == 0,
              f"{label}.{axis}: expected a power of two from 1 to 64")
    memory = texture_memory(spec["width"], spec["height"], fmt)
    check(memory["tmem_bytes"] <= 4096,
          f"{label}: texture exceeds the 4 KiB TMEM budget after 8-byte row alignment" +
          ("; CI pixels must fit the lower 2 KiB, with the upper 2 KiB reserved for the palette" if fmt != "RGBA16" else ""))
    check(path.stat().st_size <= 16 * 1024 * 1024, f"{label}: source image exceeds 16 MiB")
    from PIL import Image
    with Image.open(path) as image:
        check(image.format in ("PNG", "JPEG"), f"{label}: expected PNG or JPEG image data")
        check(image.width * image.height <= 16 * 1024 * 1024,
              f"{label}: source image exceeds 16 million pixels")
        image.verify()
    return path


def _rgb555(color):
    return tuple(((v >> 3) << 3) | ((v >> 3) >> 2) for v in color[:3])


def _palette_image(image, pixels, count, explicit=None):
    """Median-cut opaque RGB, snap to RGB555, then deterministically remap.

    Exact authored colors survive when already within the budget. A transparent
    black entry is reserved when needed, so quantization cannot change the
    thresholded alpha mask. No dithering occurs here or in mksprite.
    """
    from PIL import Image
    transparent = any(p[3] == 0 for p in pixels)
    opaque = [p[:3] for p in pixels if p[3]]
    colors = sorted(set(opaque))
    available = count - int(transparent)
    if explicit is not None:
        check(not transparent, "texture.palette: explicit RGB palettes require an opaque texture; "
                               "use automatic quantization for one-bit transparency")
        colors = list(dict.fromkeys(_rgb555(tuple(int(color[i:i+2], 16) for i in (1, 3, 5)))
                                    for color in explicit))
    elif len(colors) > available:
        strip = Image.new("RGB", (len(opaque), 1))
        strip.putdata(opaque)
        reduced = strip.quantize(colors=available, method=Image.Quantize.MEDIANCUT,
                                 dither=Image.Dither.NONE)
        colors = sorted({_rgb555(p) for p in reduced.convert("RGB").getdata()})
    palette = [(0, 0, 0, 0)] if transparent else []
    palette += [rgb + (255,) for rgb in colors]
    remap = {rgb: min(range(len(colors)), key=lambda i: (sum((rgb[c] - colors[i][c]) ** 2
                                                           for c in range(3)), i)) + int(transparent)
             for rgb in set(opaque)}
    indices = [remap[p[:3]] if p[3] else 0 for p in pixels]
    used = len(set(indices))
    # rdpq_sprite_upload always reads the format's full 16/256-entry palette.
    # Use distinct unused colors for padding: LodePNG's 4-to-8-bit conversion
    # can otherwise remap a used index to a later duplicate palette entry.
    occupied = set(palette)
    candidate = 0
    while len(palette) < count:
        color = tuple((v << 3) | (v >> 2) for v in
                      ((candidate >> 10) & 31, (candidate >> 5) & 31, candidate & 31)) + (255,)
        candidate += 1
        if color not in occupied:
            occupied.add(color)
            palette.append(color)
    result = Image.new("P", image.size)
    result.putdata(indices)
    result.putpalette([channel for p in palette for channel in p[:3]])
    result.info["transparency"] = bytes(p[3] for p in palette)
    packed_palette = _palette_bytes(palette)
    return result, {"palette_colors": count, "palette_used_colors": used,
                    "palette_unique_colors": len(colors) + int(transparent),
                    "palette_mode": "explicit" if explicit is not None else "automatic",
                    "palette_sha256": hashlib.sha256(packed_palette).hexdigest()}


def _palette_bytes(palette):
    return b"".join(struct.pack(">H", (p[0] >> 3) << 11 | (p[1] >> 3) << 6 |
                                (p[2] >> 3) << 1 | int(p[3] >= 128)) for p in palette)


def prepare_texture(spec, source_dir):
    """Return portable metadata and the exact preview/runtime input PNG bytes."""
    import PIL
    from PIL import Image
    path = validate_texture(spec, source_dir)
    fmt = spec.get("format", "RGBA16")
    source_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    recipe = {"version": 1, "source_sha256": source_sha,
              "width": spec["width"], "height": spec["height"], "format": fmt,
              "resize": "BOX", "quantization": "RGB555-floor-A1-threshold128",
              "pillow": PIL.__version__,
              "cooker_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if fmt != "RGBA16":
        recipe.update(version=2, palette_quantization="opaque-RGB555-median-cut-snap-nearest-sorted-v1",
                      palette_colors=16 if fmt == "CI4" else 256,
                      transparency="reserve-black-alpha0", dither="NONE", png="indexed-full-palette")
        if "palette" in spec:
            recipe.update(palette=validate_palette(spec["palette"], fmt),
                          palette_quantization="explicit-RGB555-first-unique-nearest-v1", transparency="opaque-only")
    recipe_sha = hashlib.sha256(json.dumps(recipe, sort_keys=True).encode()).hexdigest()
    ident = "texture-" + recipe_sha[:16]
    with Image.open(path) as image:
        image = image.convert("RGBA").resize((spec["width"], spec["height"]), Image.Resampling.BOX)
        # Replicate each five-bit channel into eight bits. mksprite's RGBA16
        # conversion truncates back to the identical five-bit value.
        pixels = [tuple(((v >> 3) << 3) | ((v >> 3) >> 2) for v in p[:3]) +
                  (255 if p[3] >= 128 else 0,) for p in image.getdata()]
        palette_metadata = {}
        if fmt == "RGBA16":
            image.putdata(pixels)
        else:
            image, palette_metadata = _palette_image(image, pixels, recipe["palette_colors"], recipe.get("palette"))
        stream = io.BytesIO()
        options = {"bits": 4 if fmt == "CI4" else 8} if fmt != "RGBA16" else {}
        image.save(stream, format="PNG", compress_level=9, optimize=False, **options)
    png = stream.getvalue()
    record = {"id": ident, "uri": spec["uri"], "width": spec["width"], "height": spec["height"],
              "format": fmt, **texture_memory(spec["width"], spec["height"], fmt), **palette_metadata,
              "source_sha256": source_sha, "recipe_sha256": recipe_sha,
              "png_sha256": hashlib.sha256(png).hexdigest(),
              "png_path": f"editor-assets/textures/{ident}.png",
              "sprite_path": f"romfs/textures/{ident}.sprite", "recipe": recipe}
    return record, png


def _verify_indexed_sprite(texture, png, payload):
    """Check native mksprite's uncompressed sprite against the preview indices/TLUT.

    The public sprite header and extended-header palette pointer are sufficient;
    no sprite assets or alternate converter are implemented here. Reject an
    incompatible tool output before any artifact is published.
    """
    from PIL import Image
    fmt = texture["format"]
    width, height = texture["width"], texture["height"]
    memory = texture_memory(width, height, fmt)
    ext = 8 + ((memory["pixel_bytes"] + 7) & ~7)
    check(len(payload) >= ext + 8 and struct.unpack_from(">HH", payload) == (width, height)
          and payload[5] & 0x80 and payload[5] & 0x1f == {"CI4": 8, "CI8": 9}[fmt],
          "mksprite indexed sprite header does not match the cooked texture")
    ext_size, version, palette_offset = struct.unpack_from(">HHI", payload, ext)
    check(version == 4 and ext_size >= 66 and palette_offset % 8 == 0
          and palette_offset >= ext + ext_size
          and palette_offset + memory["palette_bytes"] <= len(payload),
          "mksprite indexed sprite has an unsupported or incomplete palette layout")
    sprite_flags = struct.unpack_from(">H", payload, ext + 64)[0]
    check(sprite_flags & 0x20 and not sprite_flags & 7,
          "mksprite indexed sprite does not fit TMEM in full or unexpectedly contains mipmaps")
    with Image.open(png) as image:
        check(image.mode == "P", "Cooked indexed PNG must retain its palette")
        indices = list(image.getdata())
        check(max(indices) < (16 if fmt == "CI4" else 256), "Cooked PNG exceeds its index budget")
        if fmt == "CI4":
            pixels = bytes((indices[y*width+x] << 4) |
                           (indices[y*width+x+1] if x+1 < width else 0)
                           for y in range(height) for x in range(0, width, 2))
        else:
            pixels = bytes(indices)
        colors = image.getpalette()
        alpha = image.info.get("transparency", b"")
        count = memory["palette_bytes"] // 2
        # Pillow represents a single transparent entry as an integer; PNG also
        # permits trailing opaque alpha entries to be omitted from tRNS.
        if isinstance(alpha, int):
            alpha = bytes(0 if i == alpha else 255 for i in range(count))
        check(isinstance(alpha, bytes) and len(alpha) <= count, "Cooked PNG alpha table changed")
        alpha += b"\xff" * (count - len(alpha))
        check(len(colors) == count*3, "Cooked PNG palette size changed")
        palette = _palette_bytes([tuple(colors[i*3:i*3+3]) + (alpha[i],) for i in range(count)])
    check(payload[8:8+len(pixels)] == pixels, "mksprite changed the cooked PNG indices")
    check(payload[palette_offset:palette_offset+len(palette)] == palette,
          "mksprite changed the cooked RGB5551 palette")
    check(hashlib.sha256(palette).hexdigest() == texture["palette_sha256"], "Cooked palette hash changed")


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
    common_flags = ["-D", "NONE", "-c", "0"]
    artifacts, cooked = {}, []
    with tempfile.TemporaryDirectory(prefix="dl64-textures-") as temporary:
        for texture in textures:
            flags = ["-f", texture["format"], *common_flags]
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
            if texture["format"] in ("CI4", "CI8"):
                _verify_indexed_sprite(texture, png, payload)
            artifacts[destination] = payload
            cooked.append(dict(texture, signature=signature, flags=flags,
                               palette_verified=texture["format"] in ("CI4", "CI8"), sprite_bytes=len(payload),
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
    formats = {texture["format"] for texture in cooked}
    flags = ["-f", next(iter(formats)), *common_flags] if len(formats) == 1 else None
    report = {"version": 1, "mksprite_sha256": tool_sha, "flags": flags,
              "common_flags": common_flags, "textures": cooked,
              **texture_memory_summary(cooked),
              "sprite_bytes": sum(t["sprite_bytes"] for t in cooked)}
    _atomic_write(previous_path, (json.dumps(report, indent=2) + "\n").encode())
    return report
