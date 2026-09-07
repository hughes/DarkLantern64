"""Texture budgets, source tracking, UV seams and SDK-independent cook checks."""
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from tools import cook_textures
from level_fixtures import gameplay_baseline, copy_level_dependencies

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("compile_level", ROOT / "tools/compile_level.py")
compiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compiler)

UV_OBJ = """v 0 0 0
v 1 0 0
v 1 0 1
v 0 0 1
vt 0 0
vt 1 0
vt 1 1
vt 0 1
vt 2 0
f 1/1 2/2 3/3
f 1/5 3/3 4/4
"""


class TextureCookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.content = Path(self.temp.name) / "content"
        self.output = Path(self.temp.name) / "out"
        self.baseline = gameplay_baseline()
        copy_level_dependencies(self.baseline, self.content)
        Image.new("RGBA", (4, 4), (129, 94, 35, 255)).save(self.content / "test.png")
        self.spec = {"uri": "test.png", "width": 32, "height": 32, "format": "RGBA16"}

    def level(self):
        level = copy.deepcopy(self.baseline)
        # Preserve the floor's position topology but add explicit UV corners.
        floor = self.content / "models/floor.obj"
        lines = floor.read_text().splitlines()
        first_face = next(i for i,line in enumerate(lines) if line.startswith("f "))
        lines[first_face:first_face] = ["vt 0 0", "vt 1 0", "vt 1 1", "vt 0 1"]
        lines = ["f "+" ".join(f"{ref}/{i+1}" for i,ref in enumerate(line.split()[1:]))
                 if line.startswith("f ") else line for line in lines]
        floor.write_text("\n".join(lines)+"\n")
        next(m for m in level["materials"] if m["id"] == "mat-floor")["texture"] = copy.deepcopy(self.spec)
        return level

    def compile(self, level):
        source = self.content / "level.json"
        source.write_text(json.dumps(level))
        return compiler.compile_level(source, self.output)

    def test_tmem_limits_and_path_validation(self):
        self.assertEqual(cook_textures.validate_texture(dict(self.spec, width=64), self.content).name, "test.png")
        for change, message in [({"width":64,"height":64}, "4 KiB"), ({"width":31}, "power of two"),
                                ({"height":True}, "power of two"), ({"format":"RGBA32"}, "RGBA16"),
                                ({"uri":"../outside.png"}, "outside content")]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                cook_textures.validate_texture(dict(self.spec, **change), self.content)

    def test_quantized_preview_pixels_and_recipe_are_deterministic(self):
        first, png = cook_textures.prepare_texture(self.spec, self.content)
        self.assertEqual((first, png), cook_textures.prepare_texture(self.spec, self.content))
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (32, 32))
            self.assertEqual(image.getpixel((0, 0)), (132, 90, 33, 255))
        self.assertEqual(first["decoded_bytes"], 2048)
        resized, _ = cook_textures.prepare_texture(dict(self.spec, width=64), self.content)
        self.assertNotEqual(first["recipe_sha256"], resized["recipe_sha256"])
        Image.new("RGB", (4, 4), (255, 0, 0)).save(self.content / "test.png")
        altered, _ = cook_textures.prepare_texture(self.spec, self.content)
        self.assertNotEqual(first["source_sha256"], altered["source_sha256"])
        self.assertNotEqual(first["id"], altered["id"])

    def test_indexed_tmem_reserves_palette_half_and_aligns_every_row(self):
        ci4 = dict(self.spec, width=64, height=64, format="CI4")
        record, _ = cook_textures.prepare_texture(ci4, self.content)
        self.assertEqual((record["decoded_bytes"], record["palette_bytes"], record["decoded_total_bytes"]),
                         (2048, 32, 2080))
        self.assertEqual((record["tmem_pixel_bytes"], record["tmem_palette_reserved_bytes"], record["tmem_bytes"]),
                         (2048, 2048, 4096))
        with self.assertRaisesRegex(ValueError, "lower 2 KiB"):
            cook_textures.validate_texture(dict(ci4, format="CI8"), self.content)
        ci8, _ = cook_textures.prepare_texture(dict(ci4, format="CI8", height=32), self.content)
        self.assertEqual((ci8["decoded_bytes"], ci8["palette_bytes"], ci8["decoded_total_bytes"]), (2048, 512, 2560))
        narrow, _ = cook_textures.prepare_texture(dict(ci4, width=1), self.content)
        self.assertEqual((narrow["decoded_bytes"], narrow["row_bytes"], narrow["tmem_row_bytes"], narrow["tmem_bytes"]),
                         (64, 1, 8, 2560))

    def test_palette_preserves_exact_small_palette_and_thresholded_alpha(self):
        pixels = [(129, 94, 35, alpha) for alpha in (0, 127, 128, 255)]
        image = Image.new("RGBA", (4, 4)); image.putdata(pixels*4)
        image.save(self.content / "test.png")
        for fmt, count in (("CI4", 16), ("CI8", 256)):
            with self.subTest(fmt=fmt):
                record, png = cook_textures.prepare_texture(dict(self.spec, width=4, height=4, format=fmt), self.content)
                with Image.open(io.BytesIO(png)) as preview:
                    self.assertEqual(preview.mode, "P")
                    self.assertEqual(len(preview.getpalette()), count*3)
                    self.assertEqual(list(preview.convert("RGBA").getdata()),
                                     [(0, 0, 0, 0), (0, 0, 0, 0), (132, 90, 33, 255), (132, 90, 33, 255)]*4)
                self.assertEqual(record["palette_used_colors"], 2)
        Image.new("RGBA", (4, 4), (255, 17, 0, 0)).save(self.content / "test.png")
        record, png = cook_textures.prepare_texture(dict(self.spec, format="CI4"), self.content)
        self.assertEqual(record["palette_used_colors"], 1)
        self.assertEqual(set(Image.open(io.BytesIO(png)).convert("RGBA").getdata()), {(0, 0, 0, 0)})

    def test_palette_reduction_is_deterministic_rgb5551_and_recipe_tracks_format(self):
        image = Image.new("RGBA", (64, 32))
        image.putdata([(x*4, y*8, (x^y)*4, 255) for y in range(32) for x in range(64)])
        image.save(self.content / "test.png")
        results = []
        for fmt, limit in (("CI4", 16), ("CI8", 256)):
            spec = dict(self.spec, width=64, height=32, format=fmt)
            record, png = cook_textures.prepare_texture(spec, self.content)
            self.assertEqual((record, png), cook_textures.prepare_texture(spec, self.content))
            colors = set(Image.open(io.BytesIO(png)).convert("RGBA").getdata())
            self.assertGreater(len(colors), 8)
            self.assertLessEqual(len(colors), limit)
            self.assertEqual(record["palette_used_colors"], len(colors))
            for color in colors:
                self.assertEqual(color[:3], tuple(((v >> 3) << 3) | ((v >> 3) >> 2) for v in color[:3]))
                self.assertEqual(color[3], 255)
            results.append(record)
        self.assertNotEqual(results[0]["id"], results[1]["id"])
        self.assertEqual(results[0]["source_sha256"], results[1]["source_sha256"])
        import PIL
        with patch.object(PIL, "__version__", "changed-test-quantizer"):
            changed, _ = cook_textures.prepare_texture(dict(self.spec, width=64, height=32, format="CI4"), self.content)
        self.assertNotEqual(changed["recipe_sha256"], results[0]["recipe_sha256"])

    def test_explicit_palette_preserves_artist_colors_and_tracks_recipe(self):
        image = Image.new("RGB", (4, 4)); image.putdata([(80, 90, 100), (180, 20, 20), (180, 150, 40), (40, 45, 20)]*4)
        image.save(self.content / "test.png")
        spec = dict(self.spec, format="CI4", width=4, height=4,
                    palette=["#526373", "#B51818", "#B59C29", "#293118"])
        record, png = cook_textures.prepare_texture(spec, self.content)
        with Image.open(io.BytesIO(png)) as preview:
            self.assertEqual(list(preview.getdata()), [0, 1, 2, 3]*4)
            self.assertEqual(list(preview.convert("RGBA").getdata()),
                             [(82, 99, 115, 255), (181, 24, 24, 255), (181, 156, 41, 255), (41, 49, 24, 255)]*4)
        self.assertEqual((record["palette_mode"], record["palette_unique_colors"], record["palette_used_colors"]),
                         ("explicit", 4, 4))
        self.assertEqual((record, png), cook_textures.prepare_texture(dict(spec, palette=[p.lower() for p in spec["palette"]]), self.content))
        changed, changed_png = cook_textures.prepare_texture(dict(spec, palette=["#000000"]), self.content)
        self.assertNotEqual(record["recipe_sha256"], changed["recipe_sha256"])
        self.assertNotEqual(png, changed_png)
        duplicates, _ = cook_textures.prepare_texture(dict(spec, palette=["#010101", "#000000", "#FFFFFF"]), self.content)
        self.assertEqual(duplicates["palette_unique_colors"], 2)

    def test_explicit_palette_rejects_invalid_colors_count_format_and_transparency(self):
        for palette in ([], None, ["red"], ["#00000000"], [[0, 0, 0]], ["#000000"]*17):
            with self.subTest(palette=palette), self.assertRaisesRegex(ValueError, "palette"):
                cook_textures.prepare_texture(dict(self.spec, format="CI4", palette=palette), self.content)
        with self.assertRaisesRegex(ValueError, "requires CI4 or CI8"):
            cook_textures.prepare_texture(dict(self.spec, palette=["#FFFFFF"]), self.content)
        Image.new("RGBA", (4, 4), (255, 255, 255, 0)).save(self.content / "test.png")
        with self.assertRaisesRegex(ValueError, "opaque texture"):
            cook_textures.prepare_texture(dict(self.spec, format="CI4", palette=["#FFFFFF"]), self.content)

    def test_ci4_level_report_and_preview_include_palette_memory(self):
        level = self.level()
        next(m for m in level["materials"] if m["id"] == "mat-floor")["texture"].update(
            width=64, height=64, format="CI4")
        report = self.compile(level)["textures"]
        self.assertEqual((report["decoded_bytes"], report["palette_bytes"], report["decoded_total_bytes"]),
                         (2048, 32, 2080))
        with Image.open(self.output / report["textures"][0]["png_path"]) as preview:
            self.assertEqual(preview.mode, "P")

    @unittest.skipUnless((Path(os.environ.get("N64_INST", "C:/n64-toolchain")) / "bin" /
                          ("mksprite.exe" if os.name == "nt" else "mksprite")).is_file(), "Native mksprite is not installed")
    def test_native_indexed_sprites_match_preview_and_corruption_cannot_publish(self):
        sdk = Path(os.environ.get("N64_INST", "C:/n64-toolchain"))
        image = Image.new("RGBA", (64, 64))
        image.putdata([(x*4, y*4, (x^y)*4, 255 if x % 3 else 0) for y in range(64) for x in range(64)])
        image.save(self.content / "test.png")
        records = []
        for fmt, width, height in (("CI4", 64, 64), ("CI8", 64, 32), ("CI4", 1, 64)):
            record, png = cook_textures.prepare_texture(dict(self.spec, width=width, height=height, format=fmt), self.content)
            path = self.output / record["png_path"]; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(png)
            records.append(record)
        image.convert("RGB").save(self.content / "opaque.png")
        explicit, png = cook_textures.prepare_texture(dict(self.spec, uri="opaque.png", format="CI4",
                                                          palette=["#526373", "#B51818", "#B59C29", "#293118"]), self.content)
        path = self.output / explicit["png_path"]; path.write_bytes(png); records.append(explicit)
        generated = self.output / "generated"; generated.mkdir()
        (generated / "texture_report.json").write_text(json.dumps({"textures": records}))
        report = cook_textures.cook_sprites(self.output, sdk)
        self.assertEqual([t["sprite_bytes"] for t in report["textures"][:2]], [2216, 2696])
        self.assertEqual(report["palette_bytes"], 608)
        for record in report["textures"]:
            self.assertTrue(record["palette_verified"])
            payload = (self.output / record["sprite_path"]).read_bytes()
            width, height = record["width"], record["height"]
            ext = 8 + ((record["decoded_bytes"] + 7) & ~7)
            palette_offset = struct.unpack_from(">I", payload, ext+4)[0]
            palette = struct.unpack_from(">" + "H"*(record["palette_bytes"]//2), payload, palette_offset)
            decoded = []
            for y in range(height):
                for x in range(width):
                    value = payload[8 + y*record["row_bytes"] + (x//2 if record["format"] == "CI4" else x)]
                    index = ((value >> 4) if x % 2 == 0 else (value & 15)) if record["format"] == "CI4" else value
                    color = palette[index]
                    decoded.append(tuple((v << 3) | (v >> 2) for v in ((color >> 11)&31, (color >> 6)&31, (color >> 1)&31)) +
                                   (255 if color & 1 else 0,))
            with Image.open(self.output / record["png_path"]) as preview:
                self.assertEqual(decoded, list(preview.convert("RGBA").getdata()))
        with patch.object(cook_textures.subprocess, "run", side_effect=AssertionError("Cache must not invoke converter")):
            self.assertEqual(cook_textures.cook_sprites(self.output, sdk), report)
        # Force one conversion; a changed palette must be rejected before any
        # output or runtime report is replaced, even if the converter succeeds.
        last = self.output / records[-1]["sprite_path"]
        last.write_bytes(b"corrupted cached sprite")
        before = {p: p.read_bytes() for p in self.output.rglob("*") if p.is_file()}
        native_run = cook_textures.subprocess.run
        def wrong_palette(arguments, **kwargs):
            result = native_run(arguments, **kwargs)
            destination = Path(arguments[arguments.index("-o")+1]) / (Path(arguments[-1]).stem+".sprite")
            data = bytearray(destination.read_bytes()); data[-1] ^= 2; destination.write_bytes(data)
            return result
        with patch.object(cook_textures.subprocess, "run", side_effect=wrong_palette), self.assertRaisesRegex(ValueError, "changed.*palette"):
            cook_textures.cook_sprites(self.output, sdk)
        self.assertEqual(before, {p: p.read_bytes() for p in self.output.rglob("*") if p.is_file()})

    def test_obj_seams_keep_distinct_uvs_and_legacy_positions(self):
        path = self.content / "uv.obj"
        path.write_text(UV_OBJ)
        legacy, textured = compiler.read_obj(path), compiler.read_obj(path, textured=True)
        self.assertEqual(len(legacy["vertices"]), 4)
        self.assertEqual(len(textured["vertices"]), 5)
        self.assertEqual(textured["vertices"][0], textured["vertices"][3])
        self.assertEqual(textured["uvs"][0], [0, 1])
        self.assertEqual(textured["uvs"][3], [2, 1])
        self.assertEqual(textured["indices"], [0, 1, 2, 3, 2, 4])
        gltf = compiler.mesh_gltf(textured)
        self.assertEqual(gltf["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"], 2)
        self.assertEqual(gltf["accessors"][2]["count"], 6)
        path.write_text(UV_OBJ.replace("1/5", "1"))
        with self.assertRaisesRegex(compiler.ContentError, "vt index"):
            compiler.read_obj(path, textured=True)

    def test_texture_reaches_runtime_and_editor_and_invalid_cook_preserves_files(self):
        level = self.level()
        report = self.compile(level)
        texture = report["textures"]["textures"][0]
        self.assertEqual(report["textures"]["decoded_bytes"], 2048)
        self.assertIn("test.png", [d["uri"] for d in report["dependencies"]])
        header = (self.output / "generated/demo_level.h").read_text()
        self.assertIn("rom:/textures/"+texture["id"]+".sprite", header)
        self.assertIn("static const DlVec2", header)
        scene = json.loads((self.output / "editor-assets/levels/first_room.json").read_text())
        material = next(m for m in scene["materials"] if m["name"] == "mat-floor")
        self.assertEqual(material["textures"]["albedo"], "file://textures/"+texture["id"]+".png")
        self.assertEqual(self.compile(level)["changed_artifacts"], [])
        previous = {p: p.read_bytes() for p in self.output.rglob("*") if p.is_file()}
        next(m for m in level["materials"] if m["id"] == "mat-floor")["texture"]["height"] = 128
        with self.assertRaisesRegex(compiler.ContentError, "power of two"):
            self.compile(level)
        self.assertEqual(previous, {p: p.read_bytes() for p in self.output.rglob("*") if p.is_file()})

    def test_sprite_cache_tracks_converter_and_png_integrity(self):
        self.compile(self.level())
        sdk = Path(self.temp.name) / "sdk"
        tool = sdk / "bin" / ("mksprite.exe" if cook_textures.os.name == "nt" else "mksprite")
        tool.parent.mkdir(parents=True)
        tool.write_bytes(b"test converter version 1")

        def convert(arguments, **kwargs):
            # mksprite's Windows basename parser still requires POSIX separators.
            self.assertNotIn("\\", arguments[-1])
            self.assertNotIn("\\", arguments[arguments.index("-o")+1])
            destination = Path(arguments[arguments.index("-o")+1])
            png = Path(arguments[-1])
            (destination / (png.stem+".sprite")).write_bytes(b"sprite"+png.read_bytes())

        with patch.object(cook_textures.subprocess, "run", side_effect=convert) as run:
            first = cook_textures.cook_sprites(self.output, sdk)
            second = cook_textures.cook_sprites(self.output, sdk)
            self.assertEqual(first, second)
            self.assertEqual(run.call_count, 1)
            tool.write_bytes(b"test converter version 2")
            third = cook_textures.cook_sprites(self.output, sdk)
            self.assertNotEqual(first["mksprite_sha256"], third["mksprite_sha256"])
            self.assertEqual(run.call_count, 2)
            png = self.output / first["textures"][0]["png_path"]
            png.write_bytes(b"modified input")
            with self.assertRaisesRegex(ValueError, "PNG changed"):
                cook_textures.cook_sprites(self.output, sdk)
            self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
