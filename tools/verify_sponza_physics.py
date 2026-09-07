#!/usr/bin/env python3
"""Exercise authored Sponza collision through the unchanged portable game core."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import compile_level


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(output, geometry=None, level=None):
    geometry = geometry or ROOT / "content/assets/sponza/geometry.json"
    data = json.loads(geometry.read_text())
    authored = [dict(row, kind="static") for row in data["colliders"]]
    inputs = {geometry.relative_to(ROOT).as_posix(): digest(geometry)}
    if level is not None:
        scene = json.loads(level.read_text())
        entities = {row["id"]: row for row in scene["entities"]}
        for row in authored:
            if compile_level.collider(row) != compile_level.collider(entities[row["id"]]):
                raise ValueError("Level collision differs from geometry: " + row["id"])
        authored = [row for row in scene["entities"] if "collider" in row]
        inputs[level.relative_to(ROOT).as_posix()] = digest(level)
    boxes = [compile_level.collider(row) for row in authored]
    if not 1 <= len(boxes) <= 256:
        raise ValueError("Collider count exceeds actual game core capacity")
    lines = ['#include "game.h"', 'static const DlCollider fixture_boxes[] = {']
    for box in boxes:
        lines.append("{" + compile_level.vec(box["center"]) + "," +
                     compile_level.vec(box["half_size"]) + "," +
                     compile_level.f(box["yaw"]) + "," + str(box["door"]).lower() + "},")
    lines.append("};")
    for side in ("east", "west"):
        route = data["stair_routes"][side]
        if len(route) != 4:
            raise ValueError("Expected four authored flight/landing center points")
        lines.append("static const DlVec3 route_" + side + "[] = {" +
                     ",".join(compile_level.vec(p) for p in route) + "};")
    if level is not None:
        vec, number = compile_level.vec, compile_level.f
        types = {e["id"]: e for e in compile_level.read_enemy_types()}
        guards = [e for e in scene["entities"] if e["kind"] == "guard"]
        for index, guard in enumerate(guards):
            points = [entities[key]["transform"]["position"] for key in guard["patrol"]]
            lines.append(f"static const DlVec3 mission_patrol_{index}[] = {{" + ",".join(vec(p) for p in points) + "};")
        lines.append("static const DlEnemyDef mission_enemies[] = {")
        for index, guard in enumerate(guards):
            defaults = types[guard["enemy_type"]]
            fields = {"id": json.dumps(guard["id"]), "type": "DL_ENEMY_" + defaults["symbol"],
                      "behavior": "DL_BEHAVIOR_" + guard["behavior"].upper(),
                      "spawn": vec(guard["transform"]["position"]),
                      "yaw": number(math.radians(guard["transform"]["rotation"][1])),
                      "patrol": f"mission_patrol_{index}", "patrol_count": str(len(guard["patrol"])),
                      **{key: number(guard.get(key, defaults[key])) for key in ("speed", "sight_range", "hearing_range")}}
            lines.append("{" + ",".join("." + key + "=" + value for key, value in fields.items()) + "},")
        lines.append("};")
        lights = [e for e in scene["entities"] if e["kind"] == "light"]
        lines.append("static const DlLight mission_lights[] = {" + ",".join(
            "{" + vec(e["transform"]["position"]) + "," + number(e["radius"]) + "," + number(e["intensity"]) + "," +
            vec(e.get("color", [1, .75, .4])) + "}" for e in lights) + "};")
        environment = ",".join("." + key + "=" + (vec(value) if isinstance(value, list) else number(value))
                               for key, value in scene["environment"].items())
        kinds = {e["kind"]: e for e in scene["entities"] if e["kind"] in ("spawn", "control", "objective")}
        lines.append("#define SPONZA_MISSION_AVAILABLE 1\nstatic DlLevel mission_level(void) { return (DlLevel){" +
            ".version=2,.title=\"Authored Sponza mission\",.colliders=fixture_boxes,.collider_count=" + str(len(boxes)) +
            ",.spawn=" + vec(kinds["spawn"]["transform"]["position"]) +
            ",.spawn_yaw=" + number(math.radians(kinds["spawn"]["transform"]["rotation"][1])) +
            ",.control=" + vec(kinds["control"]["transform"]["position"]) +
            ",.objective=" + vec(kinds["objective"]["transform"]["position"]) +
            ",.enemies=mission_enemies,.enemy_count=" + str(len(guards)) +
            ",.lights=mission_lights,.light_count=" + str(len(lights)) +
            ",.environment={.enabled=true," + environment + "}}; }")
    output.mkdir(parents=True, exist_ok=True)
    (output / "sponza_physics_fixture.h").write_text("\n".join(lines) + "\n")
    return {"colliders": len(boxes), "input_sha256": inputs,
            "collision_and_routes_sha256": hashlib.sha256(json.dumps(
                [boxes, data["stair_routes"]], sort_keys=True).encode()).hexdigest(),
            "routes": data["stair_routes"], "level_colliders_included": level is not None}


def verify(output, level=None):
    report = fixture(output, level=level)
    compiler = shutil.which("gcc") or "C:/msys64/ucrt64/bin/gcc.exe"
    if not Path(compiler).is_file():
        raise RuntimeError("Host GCC is required for actual game.c traversal verification")
    env = dict(os.environ, PATH=str(Path(compiler).parent) + os.pathsep + os.environ.get("PATH", ""))
    executable = output / "test_sponza_physics.exe"
    sources = [ROOT / "src/game.c", ROOT / "tests/test_sponza_physics.c"]
    command = [compiler, "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "src"),
               "-I" + str(output), *map(str, sources), "-lm", "-o", str(executable)]
    compilation = subprocess.run(command, env=env, check=False, capture_output=True, text=True)
    if compilation.returncode:
        raise RuntimeError("Host compilation failed:\n" + compilation.stdout + compilation.stderr)
    result = subprocess.run([str(executable)], env=env, check=False, capture_output=True, text=True)
    (output / "host.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError("Sponza actual-core traversal failed:\n" + result.stdout + result.stderr)
    report.update(json.loads(result.stdout))
    report["input_sha256"].update({p.relative_to(ROOT).as_posix(): digest(p) for p in
                                   [*sources, ROOT / "src/game.h", ROOT / "src/enemy_types.def", Path(__file__)]})
    report["compiler"] = subprocess.run([compiler, "--version"], env=env, check=True,
                                         capture_output=True, text=True).stdout.splitlines()[0]
    report["scope"] = "Actual dl_game_update and public collision/LOS APIs; host behavior proof, not N64 performance."
    report["command"] = "python tools/verify_sponza_physics.py" + (" --geometry-only" if level is None else "")
    if level is not None:
        report["mission"] = {"route": "ordinary spawn -> east stairs up -> upper gallery control/use -> west stairs down -> vault jewel/use",
                             "inputs": "Crouch, turn, forward and use through dl_game_update; no actor teleportation or AI suppression.",
                             "gameplay_data": "Full authored colliders, enemy types/routes/speed/sight/hearing, lights and environment; render meshes omitted from the host fixture."}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/sponza-physics")
    parser.add_argument("--level", type=Path, default=ROOT / "content/sponza_courtyard.json")
    parser.add_argument("--geometry-only", action="store_true", help="Use provisional geometry before the level is assembled")
    args = parser.parse_args()
    print(json.dumps(verify(args.output.resolve(), None if args.geometry_only else args.level.resolve()), indent=2))


if __name__ == "__main__":
    main()
