#!/usr/bin/env python3
"""Assemble the authored Sponza starting scene from its small asset manifests.

The resulting JSON is an ordinary editable level. Rerunning this authoring tool
replaces that level, so keep designer edits in git before regenerating it.
"""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def transform(position, scale=(1, 1, 1), yaw=0):
    return {"position": list(position), "rotation": [0, yaw, 0], "scale": list(scale)}


def entity(name, kind, position, *, scale=(1, 1, 1), yaw=0, **fields):
    return {"id": name, "kind": kind, "transform": transform(position, scale, yaw), **fields}


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def assemble():
    folder = ROOT / "content/assets/sponza"
    geometry = json.loads((folder / "geometry.json").read_text())
    textures = json.loads((folder / "textures.json").read_text())
    write_json(folder / "pack.json", {
        "version": 1, "assets": geometry["assets"], "materials": textures["materials"],
        "prefabs": [{"id": "sponza-column", "model": "mesh-sponza-ground-column",
                     "material": "mat-sponza-stone", "scale": [1, 1, 1]}],
    })
    scenery = [dict(copy.deepcopy(item), kind="static")
               for item in geometry["instances"] + geometry["colliders"]]
    scene = {
        "version": 2, "title": "DarkLantern64 - Sponza After Hours",
        "asset_packs": ["assets/sponza/pack.json", "assets/guard/pack.json", "assets/loot/pack.json"],
        "assets": [{"id": "sponza-block", "uri": "models/night_block.obj"}],
        "materials": [
            {"id": "mat-sponza-lamp", "color": [1, .73, .31, 1],
             "emissive": [1, .68, .22], "double_sided": False},
            {"id": "mat-sponza-control", "color": [.18, .62, .42, 1],
             "emissive": [.06, .22, .12], "double_sided": False},
        ],
        "environment": {
            "ambient": [.050, .062, .09], "moon_direction": [-.22, .969, .108],
            "moon_color": [.40, .57, .95], "moon_intensity": 1.15,
            "fog_color": [.04, .06, .10], "fog_near": 25, "fog_far": 64,
            "sky_top": [.009, .016, .050], "sky_bottom": [.050, .080, .15], "exposure": 1.35,
        },
        "entities": scenery,
        "test_starts": [
            {"id": "upper-gallery", "label": "Upper gallery - control nearby", "position": [-4, 5.3, -3.5], "yaw": 315, "pitch": 0},
            {"id": "east-stairs", "label": "East stairs - lower entrance", "position": [10.5, 0, 3.4], "yaw": 90, "pitch": 8},
            {"id": "west-stairs", "label": "West stairs - upper exit", "position": [-10.5, 5.3, -4.6], "yaw": 270, "pitch": -15},
            {"id": "courtyard", "label": "Courtyard - architecture stress view", "position": [7.0, 0, 0], "yaw": 270, "pitch": 18},
            {"id": "open-vault", "label": "West niche - unlocked", "position": [-12.5, 0, 0], "yaw": 270, "pitch": -8, "door_open": True},
        ],
        "views": [
            {"id": "entrance-axis", "position": [10.1, 0, 0], "yaw": 270, "pitch": 18},
            {"id": "courtyard-arcades", "position": [5.8, 0, -1.9], "yaw": 288, "pitch": 22},
            {"id": "lower-gallery", "position": [7, 0, 4.7], "yaw": 270, "pitch": 5},
            {"id": "upper-lookdown", "position": [7.3, 5.3, -4.7], "yaw": 300, "pitch": -14},
            {"id": "gallery-control", "position": [-4.7, 5.3, -4.5], "yaw": 225, "pitch": -12},
            {"id": "east-stair", "position": [11, 0, 3.4], "yaw": 90, "pitch": 22},
            {"id": "west-stair", "position": [-10.7, 5.3, -4.6], "yaw": 270, "pitch": -25},
            {"id": "vault-jewel", "position": [-13.2, 0, 0], "yaw": 270, "pitch": -16, "door_open": True},
        ],
    }
    entities = scene["entities"]
    entities.append(entity("player-start", "spawn", geometry["landmarks"]["entry"], yaw=270))
    entities.append(entity("west-vault-door", "door", [-11.85, 1.5, 0],
                           scale=(.16, 3, 5), model="sponza-block", material="mat-sponza-wood",
                           collider={"shape": "box", "center": [0, 0, 0], "half_size": [.5, .5, .5]}))
    entities.append(entity("gallery-vault-control", "control", geometry["landmarks"]["upper_control"],
                           scale=(.32, .38, .18), model="sponza-block", material="mat-sponza-control", target="west-vault-door"))
    entities.append(entity("vault-plinth", "static", [-14.7, .42, 0], scale=(.70, .84, .70),
                           model="sponza-block", material="mat-sponza-trim",
                           collider={"shape": "box", "center": [0, 0, 0], "half_size": [.5, .5, .5]}))
    entities.append(entity("palace-jewel", "objective", [-14.7, .86, 0], scale=(1.2, 1.2, 1.2),
                           model="mesh-loot-jewel", material="mat-loot-atlas", yaw=25))
    entities.append(entity("vault-carved-plaque", "static", [-15.33, 1.85, 0], scale=(.10, 1.1, 1.0),
                           model="sponza-block", material="mat-sponza-relief"))
    # The single-objective prototype only collects the jewel. Coins dress the
    # shadowed gallery detour until the game has an inventory of loot objects.
    entities.append(entity("gallery-coins", "static", [6.6, 5.32, 5.35], scale=(1.4, 1.4, 1.4),
                           model="mesh-loot-coins", material="mat-loot-atlas", yaw=35, loot_highlight=True))
    lamps = [
        ("entry-lantern", [10.2, 2.8, .9], 6.5, 2.8),
        ("south-arcade-lantern", [-3.3, 2.9, 5.6], 6.0, 2.8),
        ("upper-gallery-lantern", [3.0, 8.0, -5.55], 6.0, 2.8),
        ("vault-lantern", [-14.6, 2.8, .6], 4.0, 1.7),
        ("east-stair-lantern", [15.6, 4.6, 4.0], 6.5, 2.4),
        ("west-stair-lantern", [-15.6, 4.6, -4.0], 6.5, 2.4),
        ("east-stair-entry-lantern", [11.7, 2.55, 3.05], 5.0, 2.8),
        ("west-stair-entry-lantern", [-11.7, 2.55, -3.05], 5.0, 2.8),
    ]
    for name, position, radius, intensity in lamps:
        entities.append(entity(name, "light", position, scale=(.22, .34, .22),
                               model="sponza-block", material="mat-sponza-lamp",
                               radius=radius, intensity=intensity, color=[1, .53, .19]))
    for floor, enemy_type, offset in (("ground", "watchman", 0), ("upper", "scout", 2)):
        points = geometry["landmarks"][floor + "_loop"]
        route = [f"{floor}-patrol-{i}" for i in range(len(points))]
        for name, point in zip(route, points):
            entities.append(entity(name, "waypoint", point))
        entities.append(entity(f"{floor}-guard", "guard", points[offset], yaw=270 if offset == 0 else 90,
                               enemy_type=enemy_type, behavior="patrol", model="mesh-guard", material="mat-guard",
                               patrol=route[offset:] + route[:offset], speed=.70 if floor == "ground" else .80,
                               sight_range=6.0, hearing_range=6.5))
    output = ROOT / "content/sponza_courtyard.json"
    write_json(output, scene)
    print(f"Wrote {output}: {len(entities)} entities")


if __name__ == "__main__":
    assemble()
