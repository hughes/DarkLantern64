"""Derive the small Sponza material set from the supplied external base colors.

Run from any directory; --source-root selects another copy of the same reference.
The full reference stays external. --verify checks regeneration without writing.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if not __package__:
    sys.path.insert(0, str(ROOT))
from tools.cook_textures import _atomic_write, prepare_texture

# Crop coordinates use the original4096x4096 PNG, left/top inclusive and
# right/bottom exclusive. Rotations follow Pillow's counterclockwise convention.
RECIPES = [
    ("stone", "arch_stone_wall_01", "61a34f79b8485262fc8253f9965d042a3f0a1437bddf97de854777981d19ac61",
     (0, 0, 4096, 4096), 90, (64, 32), 224, (1, 1), "uv", (4, 4),
     "Pale dressed masonry; rotate the long source joints into horizontal courses."),
    ("trim", "stone_trims_02", "d2275ed38589952af8972da830a3f66eaf6214677d2e4d3ff7bd9b0c580bd864",
     (0, 896, 4096, 2432), 0, (64, 32), 224, (1, 1), "u", (4, 0.5),
     "Continuous moulding bands only; repeat along U and map V once across trim height."),
    ("plaster", "ceiling_plaster_02", "1002691be7f95b374120860b5e17cf5cac37fd909b96d27fd701b0dbb69a0e03",
     (1024, 1024, 3072, 3072), 0, (32, 32), 192, (1, 1), "uv", (3, 3),
     "Neutral pale plaster; retain broad mottling for moonlit walls and ceilings."),
    ("brick", "brickwall_01", "45b77509983b55fb20884cdd9535dfe7655b4bcd9ef6889486164bbc48078373",
     (0, 0, 2048, 2048), 0, (32, 32), 224, (1, 1), "uv", (2, 2),
     "Four readable masonry courses, retaining joints instead of averaging the whole sheet."),
    ("wood", "wood_door_01", "fcbc484ebf6e707701ee1fec35c4840f8c00f400270210150d2a05dda8a8a22c",
     (448, 256, 1472, 1024), 0, (32, 32), 160, (3, 2), "uv", (1, 1),
     "Timber from the central front-door island, excluding atlas dilation and metal hardware."),
    ("floor", "floor_tiles_01", "803484416f5db14596e2e9d8ae5405cf983ee0e4a5c13203c2bf2b8febddaaa4",
     (256, 384, 2304, 2432), 90, (64, 32), 192, (1, 1), "uv", (2.5, 2.5),
     "A few large floor slabs with readable joints; exclude the dark padded edge of the source atlas."),
    ("relief", "lionhead_01", "b2297d1e079dfdf449aaebccf144b6077966093474c9be3b62512d72cd30f86b",
     (384, 2816, 1792, 3840), 0, (32, 32), 192, (1, 1), "", (1, 1),
     "Carved rectangular ornament from the lower atlas island; not a flattened lion-face bake. Map once."),
    ("roof", "roof_tiles_01", "3310a7cd14ed82f56bc00cf88d36289a73d743e4594a0e72d4e21f68c2110cf8",
     (768, 768, 2816, 2816), 0, (32, 32), 96, (1, 1), "uv", (2, 2),
     "Central roof-tile patch, excluding dilation; reduce orange chroma so scene lighting remains legible."),
]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def derive(source_root: Path, output: Path):
    import PIL
    from PIL import Image
    artifacts = {}
    entries = []
    materials = []
    previews = []
    with tempfile.TemporaryDirectory(prefix="dl64-sponza-textures-") as temporary:
        temporary = Path(temporary)
        for name, source, expected, crop, rotation, size, chroma, gain, repeat, metres, note in RECIPES:
            source_uri = f"textures/{source}_BaseColor.png"
            source_path = source_root / source_uri
            data = source_path.read_bytes()
            if sha(data) != expected:
                raise ValueError(f"Reference changed: {source_path}; review its recipe and source credit before regenerating")
            with Image.open(io.BytesIO(data)) as image:
                if image.size != (4096, 4096):
                    raise ValueError(f"Unexpected source dimensions: {source_path}")
                tile = image.convert("RGB").crop(crop)
            if rotation:
                tile = tile.transpose(Image.Transpose.ROTATE_90)
            tile = tile.resize(size, Image.Resampling.BOX)
            original = list(tile.getdata())
            mean = sum((77*r+150*g+29*b+128) >> 8 for r, g, b in original) // len(original)
            pixels = []
            for rgb in original:
                grey = (77*rgb[0]+150*rgb[1]+29*rgb[2]+128) >> 8
                neutral = [(v*chroma + grey*(256-chroma)+128) >> 8 for v in rgb]
                # Modest contrast around the mean; no scene illumination/normal-map bake.
                contrasted = [mean + ((v-mean)*5+2)//4 for v in neutral]
                pixels.append(tuple(max(0, min(255, (v*gain[0]+gain[1]//2)//gain[1])) for v in contrasted))
            tile.putdata(pixels)
            if "u" in repeat:
                for y in range(size[1]):
                    pair = tuple((a+b+1)//2 for a, b in zip(tile.getpixel((0, y)), tile.getpixel((size[0]-1, y))))
                    tile.putpixel((0, y), pair)
                    tile.putpixel((size[0]-1, y), pair)
            if "v" in repeat:
                for x in range(size[0]):
                    pair = tuple((a+b+1)//2 for a, b in zip(tile.getpixel((x, 0)), tile.getpixel((x, size[1]-1))))
                    tile.putpixel((x, 0), pair)
                    tile.putpixel((x, size[1]-1), pair)
            stream = io.BytesIO()
            tile.save(stream, "PNG", compress_level=9, optimize=False)
            png = stream.getvalue()
            relative = f"textures/sponza-{name}.png"
            uri = f"assets/sponza/{relative}"
            artifacts[output / relative] = png
            staged = temporary / uri
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(png)
            spec = {"uri": uri, "width": size[0], "height": size[1], "format": "RGBA16"}
            cooked, cooked_png = prepare_texture(spec, temporary)
            materials.append({"id": f"mat-sponza-{name}", "color": [1, 1, 1, 1], "double_sided": False, "texture": spec})
            entries.append({"id": f"sponza-{name}", "material": f"mat-sponza-{name}",
                            "source_path": source_uri, "source_sha256": expected, "source_bytes": len(data),
                            "source_dimensions": [4096, 4096], "derivative_uri": uri,
                            "derivative_sha256": sha(png), "derivative_bytes": len(png),
                            "recipe": {"crop_ltrb": list(crop), "rotate_counterclockwise_degrees": rotation,
                                       "resize": "BOX", "dimensions": list(size), "chroma_numerator_over_256": chroma,
                                       "contrast": "5/4 around integer mean luminance", "brightness_gain": list(gain),
                                       "edge_matching": "opposite border texels averaged, one texel wide",
                                       "repeat_axes": repeat, "pillow": PIL.__version__},
                            "recommended_repeat_metres": list(metres), "usage": note,
                            "target": {k: cooked[k] for k in ("format", "decoded_bytes", "png_sha256", "recipe_sha256")}})
            previews.append((name, Image.open(io.BytesIO(cooked_png)).convert("RGB")))
    total = sum(e["target"]["decoded_bytes"] for e in entries)
    if total != 22*1024 or sum(e["recipe"]["dimensions"] == [64, 32] for e in entries) != 3:
        raise ValueError("Sponza texture budget changed")
    manifest = {"version": 1, "materials": materials, "textures": entries,
                "decoded_bytes": total, "maximum_texture_bytes": 4096,
                "source_credit": {
                    "credit": "Sponza base-color derivatives from the user-supplied Intel New Sponza reference (origin inferred from the local asset).",
                    "reference_root_hint": "LightEngine/examples/assets/external/sponza",
                    "reference_gltf": "NewSponza_Main_glTF_003.gltf",
                    "reference_gltf_sha256": "e04c4c540c74bdddcbd3f590a85c14119bfd5839702246a23b3a737c0cce2400",
                    "source_links": ["https://www.intel.com/content/www/us/en/developer/topic-technology/graphics-research/samples.html",
                                     "https://www.intel.com/content/www/us/en/developer/topic-technology/graphics-research/overview.html"],
                    "terms_observed_on": "2026-09-06",
                    "terms_observed": "Intel Samples Library says Creative Commons Attribution; its Overview page says Academy Software Foundation.",
                    "unresolved": "The exact local download version, license version and original artist attribution were not supplied or recovered. These are not asserted to match another Sponza archive.",
                    "modifications": "Selected crops, optional90degree rotation, BOX reduction, muted chroma, modest contrast, timber brightening, matching repeat edges, and ordinary RGBA16 cooking.",
                    "full_reference_retained_externally": True},
                "generator": {"path": "tools/sponza_textures.py", "sha256": sha(Path(__file__).read_bytes()),
                              "target_cooker": "tools/cook_textures.py", "cooker_sha256": sha((ROOT/"tools/cook_textures.py").read_bytes())}}
    artifacts[output / "textures.json"] = (json.dumps(manifest, indent=2)+"\n").encode()
    return artifacts, manifest, previews


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT.parent/"LightEngine/examples/assets/external/sponza")
    parser.add_argument("--output", type=Path, default=ROOT/"content/assets/sponza")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--preview-dir", type=Path)
    args = parser.parse_args()
    artifacts, manifest, previews = derive(args.source_root.resolve(), args.output.resolve())
    for path, data in artifacts.items():
        if args.verify:
            if not path.is_file() or path.read_bytes() != data:
                raise ValueError(f"Generated output is stale: {path}")
        else:
            _atomic_write(path, data)
    if args.preview_dir:
        from PIL import Image, ImageDraw
        sheet = Image.new("RGB", (1280, 688), (25, 27, 30))
        draw = ImageDraw.Draw(sheet)
        for i, (name, tile) in enumerate(previews):
            x, y = (i % 4)*320, (i//4)*344
            draw.text((x+8, y+8), f"{name} / {tile.width}x{tile.height} RGBA16", fill="white")
            repeated = Image.new("RGB", (tile.width*2, tile.height*2))
            for ox in (0, tile.width):
                for oy in (0, tile.height):
                    repeated.paste(tile, (ox, oy))
            repeated = repeated.resize((304, 304), Image.Resampling.NEAREST)
            sheet.paste(repeated, (x+8, y+32))
        args.preview_dir.mkdir(parents=True, exist_ok=True)
        sheet.save(args.preview_dir/"target-contact.png")
    print(json.dumps({"verified" if args.verify else "written": len(artifacts),
                      "decoded_bytes": manifest["decoded_bytes"], "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
