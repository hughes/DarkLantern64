"""Version-2 content validation, source dependencies and canonical round trips."""
import copy
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from level_fixtures import gameplay_baseline, copy_level_dependencies

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("compile_level", ROOT / "tools/compile_level.py")
compiler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compiler)


class CompileLevelTests(unittest.TestCase):
    def setUp(self):
        self.level = gameplay_baseline()

    def entity(self, ident, data=None):
        return next(e for e in (data or self.level)["entities"] if e["id"] == ident)

    def test_duplicate_identity_rejected(self):
        self.level["entities"][-1]["id"] = self.level["entities"][0]["id"]
        with self.assertRaisesRegex(compiler.ContentError, "duplicate ID"):
            compiler.validate(self.level)

    def test_broken_links_and_nonstring_patrol_rejected(self):
        for kind, field, value in [("control", "target", "missing"),
                                    ("guard", "patrol", ["missing", "patrol-ne"]),
                                    ("guard", "patrol", [{}, "patrol-ne"]),
                                    ("guard", "patrol", [1, "patrol-ne"])]:
            with self.subTest(value=value):
                data = copy.deepcopy(self.level)
                next(e for e in data["entities"] if e["kind"] == kind)[field] = value
                with self.assertRaisesRegex(compiler.ContentError, "reference"):
                    compiler.validate(data)

    def test_nonfinite_and_invalid_transform_rejected(self):
        self.entity("store-guard")["speed"] = float("nan")
        with self.assertRaisesRegex(compiler.ContentError, "finite number"):
            compiler.validate(self.level)
        self.setUp()
        self.entity("tilted-rafter")["transform"]["scale"][2] = 0
        with self.assertRaisesRegex(compiler.ContentError, "below minimum"):
            compiler.validate(self.level)
        self.setUp()
        self.entity("tilted-rafter")["transform"]["position"][1] = float("inf")
        with self.assertRaisesRegex(compiler.ContentError, "finite number"):
            compiler.validate(self.level)

    def test_actor_collision_support_and_proxy_rotation(self):
        self.entity("player-start")["transform"]["position"] = [-5, 0, 1.4]
        with self.assertRaisesRegex(compiler.ContentError, "intersects collider"):
            compiler.validate(self.level)
        self.setUp()
        self.entity("player-start")["transform"]["position"][1] = 3
        with self.assertRaisesRegex(compiler.ContentError, "supporting surface"):
            compiler.validate(self.level)
        self.setUp()
        self.entity("rotated-crate")["transform"]["rotation"][0] = 10
        with self.assertRaisesRegex(compiler.ContentError, "upright yaw"):
            compiler.validate(self.level)

    def test_no_authoritative_grid_and_model_path_boundaries(self):
        self.level["grid"] = ["..."]
        with self.assertRaisesRegex(compiler.ContentError, "no authoritative tile grid"):
            compiler.validate(self.level)
        self.setUp()
        self.level["assets"][0]["uri"] = "../escape.obj"
        with self.assertRaisesRegex(compiler.ContentError, "outside content directory"):
            compiler.validate(self.level)

    def test_staged_save_requires_explicit_asset_root(self):
        with tempfile.TemporaryDirectory() as td:
            source, output = Path(td)/"source.json", Path(td)/"out"
            source.write_text(json.dumps(self.level))
            with self.assertRaisesRegex(compiler.ContentError, "missing model"):
                compiler.compile_level(source, output)
            report = compiler.compile_level(source, output, ROOT/"content")
            self.assertEqual(report["version"], 2)
            self.assertEqual(report["counts"]["models"], sum("model" in e for e in self.level["entities"]))

    def test_invalid_build_preserves_previous_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            source, output = Path(td)/"source.json", Path(td)/"out"
            source.write_text(json.dumps(self.level))
            compiler.compile_level(source, output, ROOT/"content")
            previous = {p: p.read_bytes() for p in output.rglob("*") if p.is_file()}
            self.entity("store-guard")["patrol"] = [False, "patrol-ne"]
            source.write_text(json.dumps(self.level))
            with self.assertRaises(compiler.ContentError):
                compiler.compile_level(source, output, ROOT/"content")
            self.assertEqual(previous, {p: p.read_bytes() for p in output.rglob("*") if p.is_file()})

    def start(self, **changes):
        return {"id":"test-entry", "position":list(self.entity("player-start")["transform"]["position"]),
                "yaw":90, "pitch":-4, **changes}

    def test_authored_starts_are_optional_and_compile_degrees_to_radians(self):
        state = compiler.validate(self.level)
        self.assertEqual(state["test_starts"], [])
        self.assertIn(".test_starts=NULL, .test_start_count=0", compiler.header(self.level, state))
        self.level["test_starts"] = [self.start(label="Scout observation", crouched=True),
                                    self.start(id="other-entry", yaw=-3600, pitch=math.degrees(1.35), door_open=True)]
        with tempfile.TemporaryDirectory() as td:
            source, output = Path(td)/"source.json", Path(td)/"out"
            source.write_text(json.dumps(self.level))
            report = compiler.compile_level(source, output, ROOT/"content")
            self.assertEqual(report["counts"]["test_starts"], 2)
            self.assertEqual(report["test_starts"][0]["yaw"], 90)
            self.assertEqual(report["test_starts"][1]["label"], "other-entry")
            self.assertFalse(report["test_starts"][0]["door_open"])
            header = (output/"generated/demo_level.h").read_text()
            self.assertIn("static const DlStartPreset dl_demo_test_starts[]", header)
            self.assertIn(".yaw="+compiler.f(math.pi/2), header)
            self.assertIn(".pitch="+compiler.f(math.radians(-4)), header)
            self.assertIn(".door_open=false, .crouched=true", header)
            self.assertIn(".test_starts=dl_demo_test_starts, .test_start_count=2", header)
        self.level["test_starts"] = []
        self.assertEqual(compiler.validate(self.level)["test_starts"], [])

    def test_start_validation_rejects_bad_fields_ranges_and_identity(self):
        boxes = compiler.validate(self.level)["colliders"]
        invalid = [None, {}, [None], [self.start(id="default")], [self.start(), self.start()],
                   [self.start(id="not an id")], [self.start(label="")], [self.start(label="snow \u2603")],
                   [self.start(extra=True)], [self.start(yaw=True)], [self.start(pitch=False)],
                   [self.start(position=[True,0,0])], [self.start(yaw=float("nan"))],
                   [self.start(yaw=3601)], [self.start(pitch=78)], [self.start(pitch=-78)],
                   [self.start(position=[1025,0,0])], [self.start(position=[0,0])],
                   [self.start(crouched=1)], [self.start(door_open="yes")],
                   [self.start(id=f"start-{i}") for i in range(17)]]
        for value in invalid:
            with self.subTest(value=value), self.assertRaisesRegex(compiler.ContentError, "test_starts"):
                compiler.validate_test_starts({"test_starts":value}, boxes)
        for field in ("id", "position", "yaw", "pitch"):
            start = self.start(); del start[field]
            with self.subTest(missing=field), self.assertRaises(compiler.ContentError):
                compiler.validate_test_starts({"test_starts":[start]}, boxes)
        valid = [self.start(id=f"start-{i}") for i in range(16)]
        self.assertEqual(len(compiler.validate_test_starts({"test_starts":valid}, boxes)), 16)

    def test_start_placement_uses_door_state_and_crouching_headroom(self):
        floor = {"id":"floor", "center":[0,-.25,0], "half_size":[10,.25,10], "yaw":0, "door":False}
        door = {"id":"door", "center":[0,1,0], "half_size":[.5,1,.5], "yaw":math.pi/4, "door":True}
        start = self.start(position=[0,0,0])
        with self.assertRaisesRegex(compiler.ContentError, "intersects collider door"):
            compiler.validate_test_starts({"test_starts":[start]}, [floor, door])
        start["door_open"] = True
        compiler.validate_test_starts({"test_starts":[start]}, [floor, door])
        roof = {"id":"roof", "center":[0,1.3,0], "half_size":[1,.2,1], "yaw":0, "door":False}
        with self.assertRaisesRegex(compiler.ContentError, "intersects collider roof"):
            compiler.validate_test_starts({"test_starts":[start]}, [floor, roof])
        start["crouched"] = True
        compiler.validate_test_starts({"test_starts":[start]}, [floor, roof])
        start["position"] = [0,3,0]
        with self.assertRaisesRegex(compiler.ContentError, "supporting surface"):
            compiler.validate_test_starts({"test_starts":[start]}, [floor])
        # Opening a door also removes it as a supporting surface.
        start["position"] = [0,2,0]
        with self.assertRaisesRegex(compiler.ContentError, "supporting surface"):
            compiler.validate_test_starts({"test_starts":[start]}, [floor, door])
        start["door_open"] = False
        compiler.validate_test_starts({"test_starts":[start]}, [floor, door])

    def test_start_ids_reject_case_aliases_for_windows_rom_paths(self):
        boxes = compiler.validate(self.level)["colliders"]
        for starts in ([self.start(id="Entry"), self.start(id="entry")],
                       [self.start(id="Default")], [self.start(id="DEFAULT")]):
            with self.subTest(starts=starts), self.assertRaisesRegex(compiler.ContentError, "case-insensitive"):
                compiler.validate_test_starts({"test_starts":starts}, boxes)
        self.assertEqual(compiler.validate_test_starts({"test_starts":[self.start(id="Entry")]}, boxes)[0]["id"], "Entry")

    def test_invalid_start_preserves_all_previous_cooked_outputs(self):
        with tempfile.TemporaryDirectory() as td:
            source, output = Path(td)/"source.json", Path(td)/"out"
            source.write_text(json.dumps(self.level))
            compiler.compile_level(source, output, ROOT/"content")
            previous = {p:p.read_bytes() for p in output.rglob("*") if p.is_file()}
            self.level["test_starts"] = [self.start(position=[-5,0,1.4])]
            source.write_text(json.dumps(self.level))
            with self.assertRaisesRegex(compiler.ContentError, "intersects collider"):
                compiler.compile_level(source, output, ROOT/"content")
            self.assertEqual(previous, {p:p.read_bytes() for p in output.rglob("*") if p.is_file()})

    def test_enemy_workshop_provides_distinct_test_states(self):
        data = json.loads((ROOT/"content/enemy_patrols.json").read_text())
        starts = {start["id"]: start for start in compiler.validate(data)["test_starts"]}
        # Additional art-inspection presets may coexist with these gameplay
        # scenarios; preserve the named states rather than freezing the menu.
        self.assertTrue(starts["store-door-open"]["door_open"])
        self.assertTrue(starts["scout-observation"]["crouched"])

    def test_incremental_cook_and_xyz_edit_reaches_both_targets(self):
        with tempfile.TemporaryDirectory() as td:
            source, output = Path(td)/"source.json", Path(td)/"out"
            source.write_text(json.dumps(self.level))
            first = compiler.compile_level(source, output, ROOT/"content")
            self.assertEqual(len(first["changed_artifacts"]), len(self.level["assets"])+2)
            generated = output/"generated/demo_level.h"
            stamp = generated.stat().st_mtime_ns
            second = compiler.compile_level(source, output, ROOT/"content")
            self.assertEqual(second["changed_artifacts"], [])
            self.assertEqual(stamp, generated.stat().st_mtime_ns)
            transform = self.entity("tilted-rafter")["transform"]
            transform["position"] = [-4.25, 3.5, -2.25]
            transform["rotation"] = [16, 31, 9]
            self.entity("store-guard")["speed"] = 1.25
            source.write_text(json.dumps(self.level))
            report = compiler.compile_level(source, output, ROOT/"content")
            self.assertIn("{-4.25f, 3.5f, -2.25f}", generated.read_text())
            self.assertIn("1.25f", generated.read_text())
            self.assertNotEqual(first["source_sha256"], report["source_sha256"])
            scene = json.loads((output/"editor-assets/levels/first_room.json").read_text())
            preview = next(e for e in scene["entities"] if e["name"] == "tilted-rafter")
            self.assertEqual(preview["transform"]["position"], transform["position"])
            self.assertEqual(preview["transform"]["rotation"], compiler.quaternion(transform["rotation"]))
            self.assertEqual(report["ids"]["tilted-rafter"]["transform"], transform)
            self.assertTrue(all(m["shadingModel"] == "PBR" for m in scene["materials"]))

    def test_obj_dependency_changes_source_hash(self):
        authored = json.loads((ROOT / "content/first_room.json").read_text())
        with tempfile.TemporaryDirectory() as td:
            content, output = Path(td)/"content", Path(td)/"out"
            copy_level_dependencies(authored, content)
            source = content/"source.json"
            source.write_text(json.dumps(authored))
            before = compiler.compile_level(source, output)
            uri = authored["assets"][0]["uri"]
            model = content/uri
            model.write_text(model.read_text()+"\n# dependency change\n")
            after = compiler.compile_level(source, output)
            self.assertNotEqual(before["source_sha256"], after["source_sha256"])
            self.assertNotEqual(next(d["sha256"] for d in before["dependencies"] if d["uri"] == uri),
                                next(d["sha256"] for d in after["dependencies"] if d["uri"] == uri))

    def test_night_environment_colored_lights_and_emissive_export(self):
        self.level["environment"] = {"ambient":[.01,.015,.03], "moon_direction":[.2,.5,.3],
            "moon_color":[.3,.45,.8], "moon_intensity":.65, "fog_color":[.015,.025,.05],
            "fog_near":18, "fog_far":48, "sky_top":[.005,.012,.035],
            "sky_bottom":[.04,.08,.14], "exposure":1.3}
        light=next(e for e in self.level["entities"] if e["kind"]=="light")
        light["color"]=[1,.5,.1]
        self.level["materials"][0]["emissive"]=[.25,.5,1]
        state=compiler.validate(self.level)
        output=compiler.header(self.level,state)
        self.assertIn(".enabled=true",output)
        self.assertIn(".fog_far=48.0f",output)
        self.assertIn("{64,128,255}",output)
        scene=compiler.preview_scene(self.level,state)
        preview=next(e for e in scene["entities"] if e["name"]==light["id"])
        self.assertEqual(preview["light"]["color"][:3],light["color"])
        self.assertTrue(any(e["name"]=="Editor moon" for e in scene["entities"]))
        for edit,message in [({"moon_direction":[0,0,0]},"must not be zero"),
                             ({"fog_far":18},"must exceed fog_near"),
                             ({"ambient":[-1,0,0]},"below minimum")]:
            data=copy.deepcopy(self.level)
            data["environment"].update(edit)
            with self.subTest(edit=edit), self.assertRaisesRegex(compiler.ContentError,message):
                compiler.validate(data)

    def test_material_sidedness_defaults_to_legacy_and_exports_per_instance(self):
        material=self.level["materials"][0]
        model=next(e for e in self.level["entities"] if e.get("material")==material["id"])
        def model_row(state):
            return next(line for line in compiler.header(self.level,state).splitlines()
                        if line.startswith('    {"'+model["id"]+'",'))
        state=compiler.validate(self.level)
        self.assertIn(", true, ", model_row(state))
        material["double_sided"]=False
        state=compiler.validate(self.level)
        self.assertIn(", false, ", model_row(state))
        preview=compiler.preview_scene(self.level,state)
        output=next(m for m in preview["materials"] if m["name"]==material["id"])
        self.assertFalse(output["doubleSided"])
        self.assertTrue(all(m["doubleSided"] for m in preview["materials"] if m["name"]!=material["id"]))
        for value in (0,1,"false",None,[]):
            material["double_sided"]=value
            with self.subTest(value=value),self.assertRaisesRegex(compiler.ContentError,"double_sided.*boolean"):
                compiler.validate(self.level)

    def test_light_intensity_supports_bright_pools_and_rejects_invalid_values(self):
        light=next(e for e in self.level["entities"] if e["kind"]=="light")
        for value in (0,2.2,16):
            light["intensity"]=value
            with self.subTest(value=value):
                state=compiler.validate(self.level)
                self.assertEqual(state["by_id"][light["id"]]["intensity"],value)
        for value in (-.01,16.01,float("nan"),float("inf"),True,"2.2"):
            light["intensity"]=value
            with self.subTest(value=value),self.assertRaisesRegex(compiler.ContentError,"intensity"):
                compiler.validate(self.level)

    def test_legacy_enemy_uses_code_type_and_preserves_explicit_overrides(self):
        original = copy.deepcopy(self.level)
        state = compiler.validate(self.level)
        enemy = state["enemies"][0]
        self.assertEqual(enemy["enemy_type"], "watchman")
        self.assertEqual(enemy["behavior"], "patrol")
        for key in ("speed", "sight_range", "hearing_range"):
            self.assertEqual(enemy[key], self.entity("store-guard")[key])
        self.assertEqual(self.level, original, "validation must not silently rewrite authoring data")

    def test_enemy_type_defaults_and_instance_override(self):
        guard = self.entity("store-guard")
        for key in ("speed", "sight_range", "hearing_range"):
            del guard[key]
        for type_id, expected in (("watchman", (.8, 6.5, 7)), ("scout", (1.1, 8, 8))):
            guard["enemy_type"] = type_id
            enemy = compiler.validate(self.level)["enemies"][0]
            self.assertEqual(tuple(enemy[key] for key in ("speed", "sight_range", "hearing_range")), expected)
        guard["speed"] = 1.75
        enemy = compiler.validate(self.level)["enemies"][0]
        self.assertEqual((enemy["speed"], enemy["sight_range"], enemy["hearing_range"]), (1.75, 8, 8))

    def test_multiple_enemies_keep_independent_routes_model_bindings_and_yaw(self):
        first = self.entity("store-guard")
        second = copy.deepcopy(first)
        second.update(id="balcony-scout", enemy_type="scout", patrol=list(reversed(first["patrol"])))
        second["transform"]["rotation"][1] = 90
        second["model"] = self.level["assets"][0]["id"]
        self.level["entities"].insert(0, second)
        with tempfile.TemporaryDirectory() as td:
            source, output = Path(td)/"source.json", Path(td)/"out"
            source.write_text(json.dumps(self.level))
            report = compiler.compile_level(source, output, ROOT/"content")
            self.assertEqual(report["counts"]["enemies"], 2)
            self.assertEqual(report["counts"]["patrol_points"], 8)
            entries = report["enemy_instances"]
            self.assertEqual(entries["balcony-scout"]["enemy_index"], 0)
            self.assertEqual(entries["store-guard"]["enemy_index"], 1)
            self.assertEqual(entries["balcony-scout"]["patrol_ids"], second["patrol"])
            self.assertEqual(entries["store-guard"]["patrol_ids"], first["patrol"])
            self.assertNotEqual(entries["store-guard"]["model_index"], entries["balcony-scout"]["model_index"])
            generated = (output/"generated/demo_level.h").read_text()
            self.assertIn(".type=DL_ENEMY_SCOUT", generated)
            self.assertIn(".yaw=1.57079633f", generated)
            self.assertIn(".patrol=dl_enemy_patrol_0", generated)
            self.assertIn(".patrol=dl_enemy_patrol_1", generated)
            self.assertIn(".enemy_count=2", generated)
            rows = [line for line in generated.splitlines() if line.startswith('    {"')]
            self.assertTrue(next(line for line in rows if '"balcony-scout"' in line).endswith(", 0, false},"))
            self.assertTrue(next(line for line in rows if '"store-guard"' in line).endswith(", 1, false},"))
            self.assertTrue(next(line for line in rows if '"tilted-rafter"' in line).endswith(", -1, false},"))

    def test_empty_enemy_population_and_sentry_route_authoring(self):
        guard = self.entity("store-guard")
        guard["behavior"] = "sentry"
        del guard["patrol"]
        output = compiler.header(self.level, compiler.validate(self.level))
        self.assertIn(".behavior=DL_BEHAVIOR_SENTRY", output)
        self.assertIn(".patrol=NULL, .patrol_count=0", output)
        # Designers can save the first waypoint before adding the second and
        # switching the behavior to patrol; sentries ignore the stored route.
        guard["patrol"] = ["patrol-nw"]
        compiler.validate(self.level)
        guard["behavior"] = "patrol"
        with self.assertRaisesRegex(compiler.ContentError, "2-32 waypoint"):
            compiler.validate(self.level)

        self.level["entities"].remove(guard)
        with tempfile.TemporaryDirectory() as td:
            source, output = Path(td)/"source.json", Path(td)/"out"
            source.write_text(json.dumps(self.level))
            report = compiler.compile_level(source, output, ROOT/"content")
            self.assertEqual(report["counts"]["enemies"], 0)
            self.assertEqual(report["counts"]["patrol_points"], 0)
            self.assertEqual(report["enemy_instances"], {})
            self.assertEqual(report["patrol_ids"], [])
            self.assertIn(".enemies=NULL, .enemy_count=0", (output/"generated/demo_level.h").read_text())

    def test_collision_only_floor_keeps_physics_without_render_model(self):
        before = compiler.validate(self.level)
        floor = next(e for e in self.level["entities"] if e["kind"] == "static" and "collider" in e)
        del floor["model"], floor["material"]
        state = compiler.validate(self.level)
        self.assertEqual(state["colliders"], before["colliders"])
        self.assertEqual(len(state["models"]), len(before["models"]) - 1)
        preview = compiler.preview_scene(self.level, state)
        proxy = next(e for e in preview["entities"] if e["name"] == floor["id"])
        self.assertNotIn("mesh", proxy)
        self.assertEqual(proxy["transform"]["position"], floor["transform"]["position"])
        floor["material"] = self.level["materials"][0]["id"]
        with self.assertRaisesRegex(compiler.ContentError, "collision-only proxy has no material"):
            compiler.validate(self.level)
        del floor["material"], floor["collider"]
        with self.assertRaisesRegex(compiler.ContentError, "requires a model or collision proxy"):
            compiler.validate(self.level)
    def test_enemy_type_behavior_route_and_population_limits(self):
        for field, value, message in (("enemy_type", "wizard", "unknown enemy type"),
                                      ("enemy_type", {}, "unknown enemy type"),
                                      ("behavior", "sleep", "expected patrol or sentry"),
                                      ("behavior", [], "expected patrol or sentry"),
                                      ("patrol", ["patrol-nw"]*33, "2-32 waypoint")):
            with self.subTest(field=field, value=value):
                data = copy.deepcopy(self.level)
                self.entity("store-guard", data)[field] = value
                with self.assertRaisesRegex(compiler.ContentError, message):
                    compiler.validate(data)
        for index in range(16):
            guard = copy.deepcopy(self.entity("store-guard"))
            guard["id"] = f"extra-guard-{index}"
            self.level["entities"].append(guard)
        with self.assertRaisesRegex(compiler.ContentError, "maximum 16 enemies"):
            compiler.validate(self.level)

    def test_code_catalog_is_strict_and_changes_cook_hash_defaults_and_report(self):
        guard = self.entity("store-guard")
        del guard["speed"]
        with tempfile.TemporaryDirectory() as td:
            catalog, source, output = Path(td)/"enemy_types.def", Path(td)/"source.json", Path(td)/"out"
            catalog.write_text(compiler.ENEMY_TYPES_PATH.read_text())
            source.write_text(json.dumps(self.level))
            with patch.object(compiler, "ENEMY_TYPES_PATH", catalog):
                before = compiler.compile_level(source, output, ROOT/"content")
                catalog.write_text(catalog.read_text().replace("0.8f", "0.9f"))
                after = compiler.compile_level(source, output, ROOT/"content")
                self.assertNotEqual(before["source_sha256"], after["source_sha256"])
                self.assertEqual(after["enemy_instances"]["store-guard"]["speed"], .9)
                self.assertEqual(after["enemy_types"][0], {"id":"watchman", "label":"Watchman", "speed":.9,
                                                         "sight_range":6.5, "hearing_range":7})
                dependency = next(d for d in after["dependencies"] if d["uri"] == "code:src/enemy_types.def")
                self.assertNotEqual(next(d for d in before["dependencies"] if d["uri"] == dependency["uri"]), dependency)
                previous = {p:p.read_bytes() for p in output.rglob("*") if p.is_file()}
                catalog.write_text(catalog.read_text()+"\n#error unexpected code\n")
                with self.assertRaisesRegex(compiler.ContentError, "expected DL_ENEMY_TYPE"):
                    compiler.compile_level(source, output, ROOT/"content")
                self.assertEqual(previous, {p:p.read_bytes() for p in output.rglob("*") if p.is_file()})
            catalog.write_text('DL_ENEMY_TYPE(WATCHMAN, "watchman", "Watchman", 0.8f, 6.5f, 7.0f)\n'*2)
            with self.assertRaisesRegex(compiler.ContentError, "duplicate enemy type"):
                compiler.read_enemy_types(catalog)


if __name__ == "__main__":
    unittest.main()
