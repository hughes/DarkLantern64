"""Character validation, measured deformation and shared pipeline regression."""
import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools import character_assets as character
from tools import compile_level as compiler
from tools import compile_bundle as bundle
from tools import project_levels as project
from level_fixtures import gameplay_baseline, copy_level_dependencies

ROOT = Path(__file__).resolve().parents[1]


def host_gcc():
    # Keep the compiler and helper DLL directory explicit for this ABI check.
    installed = Path("C:/msys64/ucrt64/bin/gcc.exe")
    return str(installed) if installed.is_file() else (shutil.which("gcc") or "gcc")


def fixture():
    bones = [{"id": "root", "parent": -1, "translation": [0, 0, 0], "rotation": [0, 0, 0, 1]},
             {"id": "head", "parent": 0, "translation": [0, 1, 0], "rotation": [0, 0, 0, 1]}]
    frames = []
    for i in range(5):
        angle = math.sin(i*math.pi/2)*.5
        frames.append({"translations": [[0, 0, 0], [0, 1, 0]],
                       "rotations": [[0, 0, 0, 1], [0, math.sin(angle/2), 0, math.cos(angle/2)]]})
    return {"version": 1, "id": "tiny-guard", "skeleton_id": "test-humanoid", "coordinates": character.FORMAT,
            "bones": bones, "mesh": {"vertices": [[0, 1, 0], [.3, 1, 0], [0, 1.3, 0]],
                                       "normals": [[0, 0, 1]]*3, "uvs": [[0, 0], [1, 0], [0, 1]],
                                       "indices": [0, 1, 2], "joints": [1, 1, 1]},
            "clips": [{"id": "walk", "fps": 30, "loop": True, "stride_length": .1, "frames": frames,
                       "events": [{"id": "foot-left", "time": 0}, {"id": "foot-right", "time": 2/30}]}],
            "sockets": [{"id": "head-tip", "bone": 1, "translation": [0, .4, .1], "rotation": [0, 0, 0, 1]}]}


class CharacterAssetTests(unittest.TestCase):
    def test_blender_guard_generated_c_matches_cooked_reference(self):
        gcc = host_gcc()
        if not Path(gcc).is_file():
            self.skipTest("Portable C compiler unavailable")
        source = ROOT/"content/assets/guard/guard.character.json"
        if not source.is_file():
            self.skipTest("Blender guard example is not installed")
        result = character.load_character(source, compare_15hz=False)
        symbol = result["symbol"]
        samples = []
        # Test exact keys and interpolated poses across every authored clip,
        # sampling vertices throughout the atlas/normal/joint split topology.
        for clip_index, clip in enumerate(result["clips"]):
            for phase in (0, .173, .5, .873):
                pose = character.globals_for(result["bones"], character.cooked_pose(result["bones"], clip, phase))
                skin = [character.compose(a, b["inverse_bind"]) for a, b in zip(pose, result["bones"])]
                for vertex_index in range(0, len(result["mesh"]["vertices"]), 31):
                    joint = result["mesh"]["joints"][vertex_index]
                    expected = character.point(skin[joint], result["mesh"]["vertices"][vertex_index])
                    samples.append((clip_index, phase*clip["duration"], vertex_index, expected))
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            statements = []
            for clip, timestamp, vertex, _ in samples:
                statements.append(f"if(!dl_animation_pose(&{symbol},{clip},{character.cf(timestamp)},-1,0,0,0,0,pose)){{return 2;}}\n"+
                                  f"p=dl_animation_point(&pose[{symbol}.vertex_bones[{vertex}]],{symbol}_vertices[{vertex}]);"+
                                  'printf("%.9g %.9g %.9g\\n",p.x,p.y,p.z);')
            main = root/"check.c"
            main.write_text('#include <stdio.h>\n#include "animation.h"\n'+character.emit_c(result)+
                            f'\nstatic const DlMesh mesh = {{{symbol}_vertices,{len(result["mesh"]["vertices"])},{symbol}_indices,{len(result["mesh"]["indices"])},{symbol}_uvs,{symbol}_normals,&{symbol}}};\n'+
                            '\nint main(void) { if(!dl_animation_validate(mesh.animation,mesh.vertex_count))return 1; DlAnimMatrix pose[32]; DlVec3 p;\n'+
                            "\n".join(statements)+'\nreturn 0; }\n', encoding="utf-8")
            env = dict(os.environ, PATH=str(Path(gcc).parent)+os.pathsep+os.environ.get("PATH", ""))
            executable = root/"check.exe"
            compiled = subprocess.run([gcc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-fmax-errors=3", "-I", str(ROOT/"src"),
                            str(main), str(ROOT/"src/animation.c"), "-lm", "-o", str(executable)],
                           capture_output=True, text=True, env=env)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            output = subprocess.run([str(executable)], check=True, capture_output=True, text=True, env=env).stdout.splitlines()
            self.assertEqual(len(output), len(samples))
            for row, (_, _, _, expected) in zip(output, samples):
                for actual, target in zip(map(float, row.split()), expected):
                    self.assertAlmostEqual(actual, target, delta=0.000005)

    def test_constant_rest_removal_quantization_and_vertex_socket_error(self):
        result = character.cook_source(fixture())
        clip, report = result["clips"][0], result["report"]["clips"][0]
        self.assertEqual([(t["bone"], t["channel"]) for t in clip["tracks"]], [(1, 0)])
        self.assertEqual(report["constant_stripped_key_bytes"], 5*4*2)
        self.assertEqual(report["dense_float_key_bytes"], 2*5*7*4)
        self.assertLess(report["quantization_error"]["max_vertex_error_m"], .00002)
        self.assertLess(report["quantization_error"]["max_socket_error_m"], .00002)
        self.assertEqual(result["mesh"]["uvs"], [[0, 1], [1, 1], [0, 0]])
        self.assertEqual(result["report"]["cross_joint_triangles"], 0)

    def test_antipodal_quaternions_do_not_introduce_motion(self):
        source = fixture()
        for i, frame in enumerate(source["clips"][0]["frames"]):
            frame["rotations"][1] = [0, 0, 0, -1 if i % 2 else 1]
        result = character.cook_source(source)
        self.assertEqual(result["clips"][0]["tracks"], [])
        self.assertEqual(result["encoded_bytes"], 0)

    def test_nonrest_constant_stored_once(self):
        source = fixture()
        for frame in source["clips"][0]["frames"]:
            frame["translations"][0] = [.25, 0, 0]
        result = character.cook_source(source)
        track = next(t for t in result["clips"][0]["tracks"] if t["channel"] == 1)
        self.assertEqual(track["sample_count"], 1)
        self.assertEqual(track["values"], [1024, 0, 0])

    def test_rejects_bad_hierarchy_weights_scale_and_nonfinite_sources(self):
        for mutate in (
            lambda s: s["bones"][1].update(parent=1),
            lambda s: s["bones"][1].update(parent=-1),
            lambda s: s["mesh"]["joints"].__setitem__(1, 2),
            lambda s: s["mesh"].update(weights=[[.5, .5]]*3),
            lambda s: s["bones"][0].update(scale=[1, 1, 1]),
            lambda s: s["clips"][0]["frames"][0]["translations"][0].__setitem__(0, 8),
            lambda s: s["mesh"]["vertices"][0].__setitem__(0, float("nan")),
            lambda s: s["clips"][0]["frames"][-1]["rotations"][1].__setitem__(0, .1),
            lambda s: s["clips"][0]["events"].append({"id": "foot-left", "time": 4/30}),
            lambda s: s["clips"][0]["events"].append({"id": "foot-left", "time": 4/30-1e-9}),
            lambda s: s["bones"][1].update(id="root"),
        ):
            source = fixture()
            mutate(source)
            with self.assertRaises(ValueError):
                character.cook_source(source)

    def test_inverse_bind_and_motion_bound_include_articulated_extent(self):
        source = fixture()
        source["bones"][0]["rotation"] = [0, 0, math.sqrt(.5), math.sqrt(.5)]
        for frame in source["clips"][0]["frames"]:
            frame["rotations"][0] = source["bones"][0]["rotation"]
        result = character.cook_source(source)
        globals_ = character.globals_for(result["bones"], result["bones"])
        for bone, global_ in zip(result["bones"], globals_):
            identity = character.compose(global_, bone["inverse_bind"])
            for actual, expected in zip(character.point(identity, [.6, .2, .9]), [.6, .2, .9]):
                self.assertAlmostEqual(actual, expected, places=6)
        for clip in result["report"]["clips"]:
            error = clip["quantization_error"]
            self.assertGreater(result["bounds_radius"], max(abs(v) for key in ("bounds_min", "bounds_max") for v in error[key]))

    def setup_level(self, root):
        content = root/"content"
        content.mkdir()
        level = gameplay_baseline()
        copy_level_dependencies(level, content)
        (content/"guard.character.json").write_text(json.dumps(fixture()), encoding="utf-8")
        asset_id = next(e["model"] for e in level["entities"] if e["kind"] == "guard")
        next(a for a in level["assets"] if a["id"] == asset_id).update(type="character", uri="guard.character.json")
        (content/"first_room.json").write_text(json.dumps(level), encoding="utf-8")
        return content, level, asset_id

    def test_level_and_project_publish_exact_cooked_preview_and_dependency_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            content, level, asset_id = self.setup_level(root)
            catalog = {"version": 1, "title": "Test", "levels": [{"id": "first", "source": "first_room.json"}]}
            (content/"level_bundle.json").write_text(json.dumps(catalog), encoding="utf-8")
            report = compiler.compile_level(content/"first_room.json", root/"cooked")
            self.assertEqual(len(report["characters"]), 1)
            preview = json.loads((root/f"cooked/editor-assets/characters/{asset_id}.json").read_text())
            self.assertEqual(preview["bones"][1]["name"], "head")
            self.assertEqual(preview["clips"][0]["tracks"][0]["channel"], "rotation")
            self.assertEqual(preview["joint_meshes"], [{"joint": 1, "uri": f"meshes/{asset_id}-joint-1.gltf"}])
            self.assertEqual(preview["bind_mesh"]["indices"], [0, 1, 2])
            self.assertEqual(preview["bind_mesh"]["uvs"], [[0, 1], [1, 1], [0, 0]])
            source_before = (content/"first_room.json").read_bytes()
            project.ProjectLevels(root).cook("first_room.json")
            target = root/f"build/project/editor-assets/characters/first_room/{asset_id}.json"
            self.assertTrue(target.is_file())
            projected = json.loads(target.read_text())
            self.assertEqual(projected["joint_meshes"][0]["uri"], f"meshes/first_room/{asset_id}-joint-1.gltf")
            self.assertEqual((content/"first_room.json").read_bytes(), source_before)
            source = fixture()
            source["clips"][0]["stride_length"] = .2
            (content/"guard.character.json").write_text(json.dumps(source), encoding="utf-8")
            second = compiler.compile_level(content/"first_room.json", root/"cooked")
            self.assertNotEqual(report["source_sha256"], second["source_sha256"])
            updated = json.loads((root/f"cooked/editor-assets/characters/{asset_id}.json").read_text())
            self.assertNotEqual(preview["source_sha256"], updated["source_sha256"])
            self.assertEqual(preview["topology_sha256"], updated["topology_sha256"])
            # Bind attributes also update in-place, but an index change needs
            # another allocation because the engine API preserves topology.
            source["mesh"]["vertices"][1][0] = .4
            (content/"guard.character.json").write_text(json.dumps(source), encoding="utf-8")
            compiler.compile_level(content/"first_room.json", root/"cooked")
            rebound = json.loads((root/f"cooked/editor-assets/characters/{asset_id}.json").read_text())
            self.assertEqual(updated["topology_sha256"], rebound["topology_sha256"])
            self.assertNotEqual(updated["bind_mesh"]["vertices"], rebound["bind_mesh"]["vertices"])
            source["mesh"]["indices"] = [1, 2, 0]
            (content/"guard.character.json").write_text(json.dumps(source), encoding="utf-8")
            compiler.compile_level(content/"first_room.json", root/"cooked")
            changed = json.loads((root/f"cooked/editor-assets/characters/{asset_id}.json").read_text())
            self.assertNotEqual(rebound["topology_sha256"], changed["topology_sha256"])

    def test_cross_joint_triangles_are_retained_in_full_target_preview(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            content, _, asset_id = self.setup_level(root)
            source = fixture()
            source["mesh"]["joints"] = [0, 1, 1]
            (content/"guard.character.json").write_text(json.dumps(source), encoding="utf-8")
            report = compiler.compile_level(content/"first_room.json", root/"cooked")
            self.assertEqual(report["characters"][asset_id]["cross_joint_triangles"], 1)
            preview = json.loads((root/f"cooked/editor-assets/characters/{asset_id}.json").read_text())
            self.assertEqual(preview["cross_joint_triangles"], 1)
            self.assertEqual(preview["joint_meshes"], [])
            self.assertEqual(preview["vertex_bones"], [0, 1, 1])
            self.assertEqual(preview["bind_mesh"]["vertices"], source["mesh"]["vertices"])
            self.assertEqual(preview["bind_mesh"]["indices"], source["mesh"]["indices"])

    def test_character_aliases_do_not_duplicate_geometry_or_memory_estimates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            content, level, asset_id = self.setup_level(root)
            first = compiler.compile_level(content/"first_room.json", root/"cooked")
            level["assets"].append({"id": "mesh-guard-alias", "type": "character", "uri": "guard.character.json"})
            (content/"first_room.json").write_text(json.dumps(level), encoding="utf-8")
            second = compiler.compile_level(content/"first_room.json", root/"cooked")
            self.assertEqual(first["compiled_geometry_bytes"], second["compiled_geometry_bytes"])
            self.assertEqual(first["compiled_normal_bytes"], second["compiled_normal_bytes"])
            emitted = (root/"cooked/generated/demo_level.h").read_text()
            self.assertEqual(emitted.count("static const DlAnimationAsset "), 1)
            (content/"second.json").write_text(json.dumps(level), encoding="utf-8")
            entries = [{"id": "one", "source": content/"first_room.json"}, {"id": "two", "source": content/"second.json"}]
            report, _ = bundle.prepare_bundle(entries, "Aliases", root/"bundle", root/"unused-sdk")
            self.assertEqual(report["resident_geometry_bytes"], second["compiled_geometry_bytes"]*2-75)
            self.assertEqual(report["characters"]["geometry_duplication_avoided_bytes"], 75)

    def test_bundle_emits_character_geometry_and_keys_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            content, level, asset_id = self.setup_level(root)
            (content/"second.json").write_text(json.dumps(level), encoding="utf-8")
            entries = [{"id": "one", "source": content/"first_room.json"}, {"id": "two", "source": content/"second.json"}]
            report, units = bundle.prepare_bundle(entries, "Two guards", root/"cooked", root/"unused-sdk")
            self.assertEqual(report["characters"]["unique_assets"], 1)
            self.assertEqual(report["characters"]["geometry_duplication_avoided_bytes"], 75)
            unit = next(p for p in units if p.name == "shared_characters.c")
            source = unit.read_text()
            self.assertEqual(source.count("const DlAnimationAsset "), 1)
            for i in range(2):
                header = (root/f"cooked/levels/{i}-{'one' if i == 0 else 'two'}/generated/demo_level.h").read_text()
                self.assertIn("extern const DlAnimationAsset", header)
                self.assertNotIn("static const DlAnimClip", header)
            gcc = host_gcc()
            if Path(gcc).is_file():
                env = dict(os.environ, PATH=str(Path(gcc).parent)+os.pathsep+os.environ.get("PATH", ""))
                main = root/"check.c"
                mesh_index = next(i for i, a in enumerate(level["assets"]) if a["id"] == asset_id)
                main.write_text('#include "game.h"\nextern const DlLevel *dl_get_level_0(void);\nextern const DlLevel *dl_get_level_1(void);\nint main(void) { return !dl_get_level_0()->meshes['+str(mesh_index)+'].animation || dl_get_level_0()->meshes['+str(mesh_index)+'].animation != dl_get_level_1()->meshes['+str(mesh_index)+'].animation; }\n')
                # Build the two independent levels and shared character unit,
                # excluding the unrelated launch descriptor.
                check = root/"check.exe"
                subprocess.run([gcc, "-std=c11", "-Wall", "-Werror", "-I", str(ROOT/"src"), str(main),
                                *[str(p) for p in units if p.name != "bundle.c"], "-o", str(check)], check=True, capture_output=True, env=env)
                subprocess.run([str(check)], check=True, env=env)


if __name__ == "__main__":
    unittest.main()
