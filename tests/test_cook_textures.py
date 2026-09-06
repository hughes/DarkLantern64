"""Texture budgets, source tracking, UV seams and SDK-independent cook checks."""
import copy
import importlib.util
import io
import json
from pathlib import Path
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
