"""Authored normal seams, compact runtime data, preview fidelity and loot flags."""
import base64
import copy
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tools import compile_level as compiler

ROOT = Path(__file__).resolve().parents[1]
POSITIONS = "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\n"
UVS = "vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\n"
NORMALS = "vn 0 0 8\nvn 0 5 5\n"


class MeshNormalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.obj = self.root / "mesh.obj"

    def read(self, text, textured=False):
        self.obj.write_text(text)
        return compiler.read_obj(self.obj, textured)

    def test_flat_legacy_layout_is_unchanged_and_explicit_normals_split_hard_edges(self):
        flat = self.read(POSITIONS + "f 1 2 3 4\n")
        self.assertEqual(flat, {"vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                                "indices": [0, 1, 2, 0, 2, 3]})
        smooth = self.read(POSITIONS + NORMALS + "f 1//1 2//1 3//1\nf 1//1 3//1 4//1\n")
        self.assertEqual(smooth["vertices"], flat["vertices"])
        self.assertEqual(smooth["indices"], flat["indices"])
        self.assertEqual(smooth["normals"], [[0, 0, 1]] * 4)
        hard = self.read(POSITIONS + NORMALS + "f 1//1 2//1 3//1\nf 1//2 3//2 4//2\n")
        self.assertEqual(hard["indices"], list(range(6)))
        self.assertEqual(hard["vertices"][0], hard["vertices"][3])
        self.assertEqual(hard["vertices"][2], hard["vertices"][4])
        self.assertNotEqual(hard["normals"][0], hard["normals"][3])
        self.assertAlmostEqual(hard["normals"][3][1], math.sqrt(.5))

    def test_uv_and_normal_seams_each_preserve_independent_corner_values(self):
        smooth = self.read(POSITIONS + UVS + NORMALS + "f 1/1/1 2/2/1 3/3/1\nf 1/4/1 3/3/1 4/4/1\n", True)
        self.assertEqual(len(smooth["vertices"]), 5)
        self.assertEqual(smooth["vertices"][0], smooth["vertices"][3])
        self.assertNotEqual(smooth["uvs"][0], smooth["uvs"][3])
        self.assertEqual(smooth["normals"][0], smooth["normals"][3])
        hard = self.read(POSITIONS + UVS + NORMALS + "f -4/-4/-2 -3/-3/-2 -2/-2/-2\nf -4/-1/-1 -2/-2/-1 -1/-1/-1\n", True)
        self.assertEqual(len(hard["vertices"]), 6)
        self.assertNotEqual(hard["normals"][2], hard["normals"][4])
        self.assertEqual(hard["uvs"][2], hard["uvs"][4])
        self.assertEqual(hard["vertices"][2], hard["vertices"][4])
        untextured = self.read(POSITIONS + NORMALS + "f -4//-2 -3//-2 -2//-2\nf -4//-1 -2//-1 -1//-1\n")
        self.assertEqual(untextured["vertices"], hard["vertices"])
        self.assertEqual(untextured["normals"], hard["normals"])
        self.assertNotIn("uvs", untextured)

    def test_malformed_zero_nonfinite_and_missing_normals_reject(self):
        for normal in ("0 0 0", "0 nan 1", "inf 0 0", "-inf 0 0", "one 0 0", "0 1", "0 1 0 1"):
            with self.subTest(normal=normal), self.assertRaises(compiler.ContentError):
                self.read(POSITIONS + "vn " + normal + "\nf 1//1 2//1 3//1\n")
        for ref in ("1//0", "1//2", "1//-2", "1//bad", "1//1.0", "1///1", "1//", "1/", "//1"):
            with self.subTest(ref=ref), self.assertRaises(compiler.ContentError):
                self.read(POSITIONS + "vn 0 0 1\nf " + ref + " 2//1 3//1\n")
        for faces in ("f 1//1 2 3//1\n", "f 1 2 3\nf 1//1 3//1 4//1\n", "f 1 2 3\n"):
            with self.subTest(faces=faces), self.assertRaisesRegex(compiler.ContentError, "every face corner"):
                self.read(POSITIONS + "vn 0 0 1\n" + faces)
        with self.assertRaisesRegex(compiler.ContentError, "vt index"):
            self.read(POSITIONS + "vn 0 0 1\nf 1//1 2//1 3//1\n", True)

    def test_normalization_handles_extreme_finite_magnitudes_and_quantization(self):
        for magnitude in ("1e308", "1e-308"):
            mesh = self.read(POSITIONS + f"vn {magnitude} {magnitude} 0\nf 1//1 2//1 3//1\n")
            self.assertAlmostEqual(mesh["normals"][0][0], math.sqrt(.5))
            self.assertEqual(compiler.quantize_normal(mesh["normals"][0]), [90, 90, 0])
        self.assertEqual(compiler.quantize_normal([-1, 0, 0]), [-127, 0, 0])
        self.assertEqual(compiler.quantize_normal([0, 1, 0]), [0, 127, 0])
        normal = [-.3, .4, math.sqrt(.75)]
        for value, encoded in zip(normal, compiler.quantize_normal(normal)):
            self.assertLessEqual(abs(value - encoded / 127), .5 / 127 + 1e-12)

    def test_gltf_retains_authored_normals_and_corner_uvs(self):
        mesh = self.read(POSITIONS + UVS + NORMALS + "f 1/1/1 2/2/1 3/3/1\nf 1/1/2 3/3/2 4/4/2\n", True)
        gltf = compiler.mesh_gltf(mesh)
        payload = base64.b64decode(gltf["buffers"][0]["uri"].partition(",")[2])
        normal_view = gltf["bufferViews"][gltf["accessors"][1]["bufferView"]]
        actual = struct.unpack_from("<18f", payload, normal_view["byteOffset"])
        expected = [value for index in mesh["indices"] for value in mesh["normals"][index]]
        for a, b in zip(actual, expected):
            self.assertAlmostEqual(a, b, places=6)
        # The second geometric face is coplanar but intentionally has a tilted
        # authored normal, proving preview did not replace it with a flat one.
        self.assertGreater(actual[10], .7)
        uv_view = gltf["bufferViews"][2]
        uv = struct.unpack_from("<12f", payload, uv_view["byteOffset"])
        self.assertEqual(list(uv), [v for index in mesh["indices"] for v in mesh["uvs"][index]])

    def compile_level_with_mesh(self, mesh):
        content = self.root / "content"
        content.mkdir(exist_ok=True)
        level = json.loads((ROOT / "content/first_room.json").read_text())
        uris = [asset["uri"] for asset in level["assets"]]
        uris += [material["texture"]["uri"] for material in level["materials"] if "texture" in material]
        for uri in uris:
            target = content / uri
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "content" / uri, target)
        (content / "models/normals.obj").write_text(mesh)
        level["assets"].append({"id": "mesh-normal-test", "uri": "models/normals.obj"})
        prop = next(entity for entity in level["entities"] if entity["id"] == "tilted-rafter")
        prop["model"] = "mesh-normal-test"
        prop["loot_highlight"] = True
        source = content / "level.json"
        source.write_text(json.dumps(level))
        output = self.root / "out"
        return compiler.compile_level(source, output), output, level

    def test_generated_header_uses_three_byte_normals_and_memory_report_counts_them(self):
        flat, _, _ = self.compile_level_with_mesh(POSITIONS + "f 1 2 3 4\n")
        report, output, _ = self.compile_level_with_mesh(POSITIONS + "vn -3 4 0\nf 1//1 2//1 3//1 4//1\n")
        self.assertEqual(report["compiled_normal_bytes"] - flat["compiled_normal_bytes"], 4 * 3)
        self.assertEqual(report["compiled_geometry_bytes"] - flat["compiled_geometry_bytes"], 4 * 3)
        self.assertTrue(report["ids"]["tilted-rafter"]["loot_highlight"])
        self.assertFalse(report["ids"]["store-guard"]["loot_highlight"])
        generated = (output / "generated/demo_level.h").read_text()
        self.assertIn("static const DlNormal dl_normals_", generated)
        self.assertIn("{-76, 102, 0}", generated)
        compiler_path = shutil.which("gcc") or next((str(p) for p in [
            Path("C:/ProgramData/mingw64/mingw64/bin/gcc.exe"), Path("C:/msys64/ucrt64/bin/gcc.exe")]
            if p.is_file()), None)
        if not compiler_path:
            self.skipTest("Host GCC unavailable for runtime ABI check")
        probe = self.root / "probe.c"
        probe.write_text('#include "demo_level.h"\n#include <assert.h>\n#include <string.h>\n'
                         '_Static_assert(sizeof(DlNormal)==3,"normal ABI");\n'
                         'int main(void) { for(int i=0;i<dl_demo_level.model_count;++i) {'
                         'const DlModelInstance *p=&dl_demo_level.models[i];'
                         'if(strcmp(p->id,"tilted-rafter")==0) {'
                         'const DlMesh *m=&dl_demo_level.meshes[p->mesh];'
                         'assert(p->loot_highlight); assert(m->normals); assert(m->normals[0].x==-76);'
                         'assert(m->normals[0].y==102); return 0; }} return 1; }\n')
        executable = self.root / "probe.exe"
        env = dict(os.environ, PATH=str(Path(compiler_path).parent) + os.pathsep + os.environ.get("PATH", ""))
        subprocess.run([compiler_path, "-std=c17", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "src"),
                        "-I" + str(output / "generated"), str(probe), "-o", str(executable)],
                       check=True, capture_output=True, text=True, env=env)
        subprocess.run([executable], check=True, capture_output=True, env=env)

    def test_loot_highlight_is_strict_and_only_for_rendered_statics(self):
        source = json.loads((ROOT / "content/first_room.json").read_text())
        for value in (1, 0, "true", None, [], {}):
            level = copy.deepcopy(source)
            next(e for e in level["entities"] if e["id"] == "tilted-rafter")["loot_highlight"] = value
            with self.subTest(value=value), self.assertRaisesRegex(compiler.ContentError, "expected a boolean"):
                compiler.validate(level)
        for ident in ("store-guard", "player-start"):
            for value in (True, False):
                level = copy.deepcopy(source)
                next(e for e in level["entities"] if e["id"] == ident)["loot_highlight"] = value
                with self.subTest(ident=ident, value=value), self.assertRaisesRegex(compiler.ContentError, "rendered static"):
                    compiler.validate(level)


if __name__ == "__main__":
    unittest.main()
