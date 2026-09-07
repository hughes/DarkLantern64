#!/usr/bin/env python3
"""Cook version-2, freely placed 3D content to the N64 and LightEngine.

Canonical JSON contains assets (stable id + relative OBJ uri), materials (id +
RGBA floats), and entities (id, kind, transform). Transform uses XYZ metres,
Euler XYZ degrees, positive XYZ scale: scale -> Rx -> Ry -> Rz -> translation.
An optional model/material references reusable geometry. Optional upright box
colliders have local center/half_size. They follow the same translation, yaw,
and scale. There is no gameplay grid. OBJ polygons are triangulated on import.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import time

try:
    from character_assets import load_character, emit_c as character_c, joint_meshes
except ModuleNotFoundError:
    from tools.character_assets import load_character, emit_c as character_c, joint_meshes

try:
    from cook_textures import prepare_texture, validate_texture
    from asset_pack import resolve_asset_packs
except ModuleNotFoundError:
    from tools.cook_textures import prepare_texture, validate_texture
    from tools.asset_pack import resolve_asset_packs

ROOT = Path(__file__).resolve().parents[1]
KINDS = {"static", "spawn", "guard", "waypoint", "door", "control", "objective", "light"}
ROLES = {"guard":"DL_MODEL_GUARD", "door":"DL_MODEL_DOOR", "control":"DL_MODEL_CONTROL", "objective":"DL_MODEL_OBJECTIVE"}
ENEMY_TYPES_PATH = ROOT/"src/enemy_types.def"
CONTENT_LIMITS_PATH = ROOT/"src/content_limits.h"
LIMITS = {name: int(re.search(r"^#define " + macro + r" (\d+)$", CONTENT_LIMITS_PATH.read_text(), re.MULTILINE)[1])
          for name, macro in (("mesh_vertices", "DL_MAX_MESH_VERTICES"),
                              ("scene_vertices", "DL_MAX_SCENE_VERTICES"),
                              ("scene_triangles", "DL_MAX_SCENE_TRIANGLES"), ("models", "DL_MAX_MODELS"))}
MAX_ENEMIES = 16
MAX_TEST_STARTS = 16


class ContentError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ContentError(message)


def number(value, label, minimum=None, maximum=None):
    require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
            f"{label}: expected a finite number")
    require(minimum is None or value >= minimum, f"{label}: below minimum {minimum}")
    require(maximum is None or value <= maximum, f"{label}: above maximum {maximum}")
    return float(value)


def vector(value, label, minimum=-1024, maximum=1024, size=3):
    require(isinstance(value, list) and len(value) == size, f"{label}: expected {size} numbers")
    return [number(v, f"{label}[{i}]", minimum, maximum) for i,v in enumerate(value)]


def identifier(value, label):
    require(isinstance(value,str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value),
            f"{label}: expected a stable ASCII identifier")
    return value


def read_enemy_types(path=None):
    """Read the deliberately small C X-macro catalog without executing code."""
    path = Path(path or ENEMY_TYPES_PATH)
    numeric = r"([0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?[fF]?)"
    row = re.compile(r'DL_ENEMY_TYPE\(\s*([A-Z][A-Z0-9_]*)\s*,\s*"([A-Za-z][A-Za-z0-9_-]{0,63})"\s*,\s*"([^"\\]+)"\s*,\s*'+
                     numeric+r"\s*,\s*"+numeric+r"\s*,\s*"+numeric+r"\s*\)")
    result, symbols = [], set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.partition("//")[0].strip()
        if not line: continue
        label = f"{path.name}:{line_number}"
        match = row.fullmatch(line)
        require(match is not None, label+": expected DL_ENEMY_TYPE(symbol, \"id\", \"label\", speed, sight_range, hearing_range)")
        symbol, ident, display, speed, sight, hearing = match.groups()
        require(symbol not in symbols and all(t["id"] != ident for t in result), label+": duplicate enemy type symbol or ID")
        require(0 < len(display) <= 80 and all(32 <= ord(c) < 127 for c in display), label+": label must contain 1-80 printable ASCII characters")
        symbols.add(symbol)
        result.append({"symbol":symbol, "id":ident, "label":display,
                       "speed":number(float(speed.rstrip("fF")), label+".speed", .05, 4),
                       "sight_range":number(float(sight.rstrip("fF")), label+".sight_range", .1, 128),
                       "hearing_range":number(float(hearing.rstrip("fF")), label+".hearing_range", .1, 128)})
    require(1 <= len(result) <= 32, f"{path.name}: expected 1-32 enemy types")
    require(any(t["id"] == "watchman" for t in result), f"{path.name}: default enemy type watchman is required")
    return result


def rotate(point, degrees):
    x,y,z = point
    a,b,c = map(math.radians, degrees)
    y,z = math.cos(a)*y-math.sin(a)*z, math.sin(a)*y+math.cos(a)*z
    x,z = math.cos(b)*x+math.sin(b)*z, -math.sin(b)*x+math.cos(b)*z
    x,y = math.cos(c)*x-math.sin(c)*y, math.sin(c)*x+math.cos(c)*y
    return [x,y,z]


def transform_point(point, transform):
    p = rotate([point[i]*transform["scale"][i] for i in range(3)], transform["rotation"])
    return [p[i]+transform["position"][i] for i in range(3)]


def quaternion(degrees):
    x,y,z = [math.radians(v)/2 for v in degrees]
    cx,cy,cz,sx,sy,sz = math.cos(x),math.cos(y),math.cos(z),math.sin(x),math.sin(y),math.sin(z)
    return [cx*cy*cz+sx*sy*sz, sx*cy*cz-cx*sy*sz, cx*sy*cz+sx*cy*sz, cx*cy*sz-sx*sy*cz]


def read_obj(path, textured=False):
    require(path.stat().st_size <= 8*1024*1024, f"{path.name}: OBJ exceeds 8 MiB source limit")
    vertices, texcoords, source_normals, corners = [], [], [], []

    def scalar(token, label):
        try:
            value = float(token)
        except ValueError as error:
            raise ContentError(f"{label}: expected a finite number") from error
        return number(value, label)

    def index(token, count, label):
        require(re.fullmatch(r"[+-]?[0-9]+", token) is not None, f"{label}: malformed index")
        try:
            raw = int(token)
        except ValueError as error:
            raise ContentError(f"{label}: malformed index") from error
        resolved = raw - 1 if raw > 0 else count + raw
        require(raw != 0 and 0 <= resolved < count, f"{label}: invalid index {raw}")
        return resolved

    for line_number,line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        fields = line.partition("#")[0].split()
        if not fields: continue
        label = f"{path.name}:{line_number}"
        if fields[0] == "v":
            require(len(fields) in (4,5), f"{label}: vertex must contain XYZ")
            vertex = vector([scalar(v,label) for v in fields[1:4]],label)
            require(len(fields)==4 or scalar(fields[4],label)==1, f"{label}: homogeneous vertex w must be 1")
            vertices.append(vertex)
        elif fields[0] == "vt":
            require(3 <= len(fields) <= 4, f"{label}: texture coordinate must contain UV")
            texcoords.append(vector([scalar(v,label+".uv") for v in fields[1:3]], label+".uv", -1024, 1024, 2))
            if len(fields) == 4:
                scalar(fields[3], label+".uv")
        elif fields[0] == "vn":
            require(len(fields) == 4, f"{label}: normal must contain XYZ")
            normal = [scalar(v,label+".normal") for v in fields[1:]]
            # Scale first so any finite, nonzero input normal can be normalized
            # without overflowing or underflowing its squared length.
            magnitude = max(abs(v) for v in normal)
            require(magnitude > 0, f"{label}: normal must not be zero")
            scaled = [v / magnitude for v in normal]
            length = math.sqrt(sum(v*v for v in scaled))
            source_normals.append([v / length for v in scaled])
        elif fields[0] == "f":
            require(3 <= len(fields)-1 <= 64, f"{label}: face needs 3-64 corners")
            face = []
            for ref in fields[1:]:
                parts = ref.split("/")
                require(1 <= len(parts) <= 3 and parts[0] and
                        (len(parts) != 2 or parts[1]) and (len(parts) != 3 or parts[2]),
                        f"{label}: malformed face corner {ref!r}")
                vertex_index = index(parts[0], len(vertices), label+" vertex")
                uv_index = index(parts[1], len(texcoords), label+" texture coordinate") if len(parts) >= 2 and parts[1] else None
                normal_index = index(parts[2], len(source_normals), label+" normal") if len(parts) == 3 else None
                if textured:
                    require(uv_index is not None, f"{label}: textured faces require a vt index on every corner")
                face.append((vertex_index, uv_index, normal_index))
            # Fan triangulation is deterministic for the convex blockout faces.
            # Reject concave polygons instead of silently producing bad geometry.
            origin = vertices[face[0][0]]
            normal = None
            for i in range(1,len(face)-1):
                a,b = vertices[face[i][0]],vertices[face[i+1][0]]
                u,v = [a[j]-origin[j] for j in range(3)],[b[j]-origin[j] for j in range(3)]
                cross = [u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
                require(sum(c*c for c in cross) > 1e-12, f"{label}: degenerate face")
                if normal is None: normal=cross
                require(sum(normal[j]*cross[j] for j in range(3)) > 0, f"{label}: triangulate concave face in modeling tool")
                corners.extend((face[0],face[i],face[i+1]))
    if source_normals:
        require(all(corner[2] is not None for corner in corners),
                f"{path.name}: meshes with vn normals require a normal index on every face corner")
    indices, seam_vertices, seam_uvs, seam_normals, seam_indices = [], [], [], [], {}
    if textured or source_normals:
        for vertex_index, uv_index, normal_index in corners:
            key = (vertex_index, uv_index if textured else None, normal_index)
            if key not in seam_indices:
                seam_indices[key] = len(seam_vertices)
                seam_vertices.append(vertices[vertex_index])
                if textured:
                    # OBJ V grows upward; glTF and RDP image data grow down.
                    seam_uvs.append([texcoords[uv_index][0], 1-texcoords[uv_index][1]])
                if source_normals:
                    seam_normals.append(source_normals[normal_index])
            indices.append(seam_indices[key])
        vertices = seam_vertices
    else:
        # Keep legacy, untextured flat meshes' source vertex ordering exactly.
        indices = [corner[0] for corner in corners]
    require(3 <= len(vertices) <= LIMITS["mesh_vertices"], f"{path.name}: expected 3-{LIMITS['mesh_vertices']} vertices")
    require(indices and len(indices)//3 <= LIMITS["scene_triangles"], f"{path.name}: expected 1-{LIMITS['scene_triangles']} triangles")
    result = {"vertices":vertices,"indices":indices}
    if textured: result["uvs"] = seam_uvs
    if source_normals: result["normals"] = seam_normals
    return result


def quantize_normal(normal):
    """Map normalized source normals to the three-byte DlNormal runtime ABI."""
    return [max(-127, min(127, round(component * 127))) for component in normal]


def collider(entity):
    local,t = entity["collider"],entity["transform"]
    return {"id":entity["id"], "center":transform_point(local["center"],t),
            "half_size":[local["half_size"][i]*t["scale"][i] for i in range(3)],
            "yaw":math.radians(t["rotation"][1]), "door":entity["kind"]=="door"}


def local_xz(point, box):
    x,z = point[0]-box["center"][0],point[2]-box["center"][2]
    c,s = math.cos(box["yaw"]),math.sin(box["yaw"])
    return c*x-s*z,s*x+c*z


def validate_actor_placement(position, boxes, label, radius=.18, height=1.65, door_open=False):
    supported = False
    for box in boxes:
        if door_open and box["door"]: continue
        x,z=local_xz(position,box); hx,hy,hz=box["half_size"]; cy=box["center"][1]
        near=(max(abs(x)-hx,0)**2+max(abs(z)-hz,0)**2)<radius**2
        require(not(near and position[1]+height>cy-hy+.001 and position[1]<cy+hy-.001),
                f"{label}: actor placement intersects collider {box['id']}")
        if abs(x)<=hx and abs(z)<=hz and 0<=position[1]-(cy+hy)<.29: supported=True
    require(supported,f"{label}: actor needs a supporting surface within step height")


def validate_test_starts(data, boxes):
    starts = data.get("test_starts", [])
    require(isinstance(starts, list) and len(starts) <= MAX_TEST_STARTS,
            f"test_starts: expected 0-{MAX_TEST_STARTS} authored starts")
    result, seen = [], {"default"}
    fields = {"id", "label", "position", "yaw", "pitch", "door_open", "crouched"}
    for index, start in enumerate(starts):
        label = f"test_starts[{index}]"
        require(isinstance(start, dict), label+": expected an object")
        require(not (set(start) - fields), label+": unknown fields "+", ".join(sorted(set(start) - fields)))
        ident = identifier(start.get("id"), label+".id")
        require(ident.casefold() not in seen, label+".id: duplicate or reserved ID (case-insensitive; 'default' is the ordinary spawn)")
        seen.add(ident.casefold())
        display = start.get("label", ident)
        require(isinstance(display,str) and 0 < len(display) <= 80 and all(32 <= ord(c) < 127 for c in display),
                label+".label: expected 1-80 printable ASCII characters")
        position = vector(start.get("position"), label+".position")
        yaw = number(start.get("yaw"), label+".yaw", -3600, 3600)
        pitch = number(start.get("pitch"), label+".pitch", -math.degrees(1.35), math.degrees(1.35))
        for field in ("door_open", "crouched"):
            require(type(start.get(field, False)) is bool, label+"."+field+": expected a boolean")
        door_open, crouched = start.get("door_open", False), start.get("crouched", False)
        validate_actor_placement(position, boxes, label+" ("+ident+")", height=.95 if crouched else 1.65, door_open=door_open)
        result.append({"id":ident, "label":display, "position":position, "yaw":yaw, "pitch":pitch,
                       "door_open":door_open, "crouched":crouched})
    return result


def validate(data, source_dir=None):
    source_dir = Path(source_dir or ROOT/"content").resolve()
    enemy_types = read_enemy_types()
    enemy_type_map = {t["id"]:t for t in enemy_types}
    require(isinstance(data,dict) and type(data.get("version")) is int and data["version"]==2,
            "version: expected 2 (3D model scene)")
    require("grid" not in data, "grid: version 2 has no authoritative tile grid")
    try:
        data, resolved_catalog, pack_dependencies = resolve_asset_packs(data, source_dir)
    except ValueError as error:
        raise ContentError(str(error)) from error
    defaults_by_type = {}
    for prefab in resolved_catalog["prefabs"]:
        if "enemy_type" not in prefab:
            continue
        enemy_type = prefab["enemy_type"]
        require(enemy_type in enemy_type_map, f"{prefab['id']}: unknown prefab enemy_type {enemy_type}")
        require(enemy_type not in defaults_by_type, f"Multiple visual prefabs define enemy_type {enemy_type}")
        defaults_by_type[enemy_type] = prefab["id"]
        enemy_type_map[enemy_type]["visual_prefab"] = prefab["id"]
    require(isinstance(data.get("title"),str) and 0<len(data["title"])<=120 and
            all(32<=ord(c)<127 for c in data["title"]), "title: expected 1-120 printable ASCII characters")
    assets, materials, entities = data.get("assets"),data.get("materials"),data.get("entities")
    require(isinstance(assets,list) and 1<=len(assets)<=64,"assets: expected 1-64 assets")
    require(isinstance(materials,list) and 1<=len(materials)<=64,"materials: expected 1-64 materials")
    require(isinstance(entities,list) and 1<=len(entities)<=256,"entities: expected 1-256 objects")
    seen, meshes, dependencies, mesh_paths, characters = set(),{},list(pack_dependencies),{},{}
    textures, texture_artifacts, texture_indices = [], {}, {}
    environment = data.get("environment")
    if environment is not None:
        require(isinstance(environment, dict), "environment: expected an object")
        for field in ("ambient", "moon_color", "fog_color", "sky_top", "sky_bottom"):
            vector(environment.get(field), "environment."+field, 0, 1)
        direction = vector(environment.get("moon_direction"), "environment.moon_direction", -1, 1)
        require(sum(v*v for v in direction) > .0001, "environment.moon_direction: direction must not be zero")
        number(environment.get("moon_intensity"), "environment.moon_intensity", 0, 16)
        number(environment.get("fog_near"), "environment.fog_near", 0, 1024)
        number(environment.get("fog_far"), "environment.fog_far", 0, 1024)
        require(environment["fog_far"] > environment["fog_near"], "environment.fog_far: must exceed fog_near")
        number(environment.get("exposure"), "environment.exposure", .05, 8)
    def unique(item, label):
        require(isinstance(item,dict),f"{label}: expected an object")
        ident=identifier(item.get("id"),label+".id")
        require(ident not in seen,f"{ident}: duplicate ID")
        seen.add(ident)
        return ident
    for asset in assets:
        ident=unique(asset,"asset")
        uri=asset.get("uri")
        character = asset.get("type") == "character"
        require(asset.get("type", "mesh") in ("mesh", "character"), f"{ident}.type: expected mesh or character")
        require(isinstance(uri,str) and Path(uri).suffix.lower()==(".json" if character else ".obj"),
                f"{ident}.uri: expected a relative {'character JSON' if character else 'OBJ'} path")
        path=(source_dir/uri).resolve()
        require(path.is_relative_to(source_dir) and path.is_file(),f"{ident}.uri: missing model or path outside content directory")
        if character:
            try:
                characters[ident] = load_character(path)
            except ValueError as error:
                raise ContentError(f"{ident}: {error}") from error
            meshes[ident] = characters[ident]["mesh"]
        else:
            meshes[ident]=read_obj(path)
        require(len(meshes[ident]["vertices"]) <= LIMITS["mesh_vertices"],
                f"{ident}: maximum {LIMITS['mesh_vertices']} vertices per mesh")
        mesh_paths[ident]=path
        dependencies.append((uri,path))
    material_map={}
    for material in materials:
        ident=unique(material,"material")
        vector(material.get("color"),ident+".color",0,1,4)
        vector(material.get("emissive", [0,0,0]), ident+".emissive", 0, 1)
        require(type(material.get("double_sided", True)) is bool,
                ident+".double_sided: expected a boolean")
        if "texture" in material:
            try:
                path = validate_texture(material["texture"], source_dir, ident+".texture")
            except ValueError as error:
                raise ContentError(str(error)) from error
            dependencies.append((material["texture"]["uri"], path))
        material_map[ident]=material
    by_id, kinds = {},{kind:[] for kind in KINDS}
    for e in entities:
        eid=unique(e,"entity"); kind=e.get("kind")
        require(isinstance(kind,str) and kind in KINDS,f"{eid}: unknown kind")
        t=e.get("transform")
        require(isinstance(t,dict),f"{eid}.transform: required")
        vector(t.get("position"),eid+".position")
        vector(t.get("rotation"),eid+".rotation",-3600,3600)
        vector(t.get("scale"),eid+".scale",.001,1024)
        if "model" in e:
            require(e["model"] in meshes,f"{eid}.model: unknown model reference")
            require(e.get("material") in material_map,f"{eid}.material: unknown material reference")
            for v in meshes[e["model"]]["vertices"]:
                vector(transform_point(v,t),eid+".world_vertex")
        if kind in ("static","guard","door","control","objective"):
            require("model" in e,f"{eid}: rendered {kind} requires a model")
        if "loot_highlight" in e:
            require(type(e["loot_highlight"]) is bool, f"{eid}.loot_highlight: expected a boolean")
            require(kind == "static" and "model" in e,
                    f"{eid}.loot_highlight: only rendered static models support this presentation hint")
        if "collider" in e:
            proxy=e["collider"]
            require(isinstance(proxy,dict) and proxy.get("shape")=="box",f"{eid}.collider: expected box")
            vector(proxy.get("center"),eid+".collider.center")
            vector(proxy.get("half_size"),eid+".collider.half_size",.001,1024)
            require(abs(t["rotation"][0])<.0001 and abs(t["rotation"][2])<.0001,
                    f"{eid}.collider: box collision supports upright yaw; use separate upright proxies for tilted models")
            require(kind in ("static","door"),f"{eid}: interactive markers and actors must not have opaque static colliders")
        if kind=="door": require("collider" in e,f"{eid}: door requires collision proxy")
        if kind=="light":
            number(e.get("radius"),eid+".radius",.1,128)
            number(e.get("intensity"),eid+".intensity",0,16)
            vector(e.get("color", [1,.75,.4]), eid+".color", 0, 1)
        if kind=="guard":
            enemy_type = e.get("enemy_type", "watchman")
            require(isinstance(enemy_type, str) and enemy_type in enemy_type_map,
                    f"{eid}.enemy_type: unknown enemy type {enemy_type!r}; choose "+", ".join(enemy_type_map))
            behavior = e.get("behavior", "patrol")
            require(isinstance(behavior, str) and behavior in ("patrol", "sentry"),
                    f"{eid}.behavior: expected patrol or sentry")
            defaults = enemy_type_map[enemy_type]
            number(e.get("speed", defaults["speed"]),eid+".speed",.05,4)
            number(e.get("sight_range", defaults["sight_range"]),eid+".sight_range",.1,128)
            number(e.get("hearing_range", defaults["hearing_range"]),eid+".hearing_range",.1,128)
        by_id[eid]=e; kinds[kind].append(e)
    for kind in ("spawn","door","control","objective"):
        require(len(kinds[kind])==1,f"entities: exactly one {kind} required")
    require(len(kinds["guard"]) <= MAX_ENEMIES, f"entities: maximum {MAX_ENEMIES} enemies (guard objects); reduce enemy placements")
    require(1<=len(kinds["light"])<=16,"entities: expected 1-16 lights")
    control,door=(kinds[k][0] for k in ("control","door"))
    require(control.get("target")==door["id"],f"{control['id']}.target: invalid door reference")
    enemies = []
    for guard in kinds["guard"]:
        behavior = guard.get("behavior", "patrol")
        patrol = guard.get("patrol", [])
        minimum = 0 if behavior == "sentry" else 2
        require(isinstance(patrol,list) and minimum <= len(patrol) <= 32,
                f"{guard['id']}.patrol: expected {minimum}-32 waypoint references for {behavior} behavior")
        for ref in patrol:
            require(isinstance(ref,str) and ref in by_id and by_id[ref]["kind"]=="waypoint",f"{guard['id']}.patrol: invalid waypoint reference {ref!r}")
        defaults = enemy_type_map[guard.get("enemy_type", "watchman")]
        enemies.append({"id":guard["id"], "enemy_type":defaults["id"], "symbol":defaults["symbol"], "behavior":behavior,
                        "patrol_ids":list(patrol), "enemy_index":len(enemies),
                        **{key:float(guard.get(key, defaults[key])) for key in ("speed", "sight_range", "hearing_range")}})
    models=[e for e in entities if "model" in e]
    for enemy in enemies:
        enemy["model_index"] = next(i for i, e in enumerate(models) if e["id"] == enemy["id"])
    for model in {e["model"] for e in models if "texture" in material_map[e["material"]]}:
        if model not in characters:
            meshes[model] = read_obj(mesh_paths[model], textured=True)
    boxes=[collider(e) for e in entities if "collider" in e]
    require(1<=len(boxes)<=256,"colliders: expected 1-256 boxes")
    require(len(models)<=LIMITS["models"],f"models: maximum {LIMITS['models']} instances")
    require(sum(len(meshes[e["model"]]["vertices"]) for e in models)<=LIMITS["scene_vertices"],
            f"models: maximum {LIMITS['scene_vertices']} instanced vertices")
    require(sum(len(meshes[e["model"]]["indices"])//3 for e in models)<=LIMITS["scene_triangles"],
            f"models: maximum {LIMITS['scene_triangles']} instanced triangles")
    for e in kinds["spawn"]+kinds["guard"]+kinds["waypoint"]:
        validate_actor_placement(e["transform"]["position"], boxes, e["id"], radius=.18 if e["kind"]=="spawn" else .17)
    test_starts = validate_test_starts(data, boxes)
    # Prepare all pixels before publishing any generated file, so invalid image
    # input preserves the previous complete content build.
    for material in materials:
        if "texture" not in material: continue
        texture, png = prepare_texture(material["texture"], source_dir)
        existing = next((i for i,t in enumerate(textures) if t["id"] == texture["id"]), None)
        if existing is None:
            existing = len(textures)
            textures.append(texture)
            texture_artifacts[texture["png_path"]] = png
        texture_indices[material["id"]] = existing
    dependencies.append(("code:src/enemy_types.def", ENEMY_TYPES_PATH))
    dependencies.append(("code:src/content_limits.h", CONTENT_LIMITS_PATH))
    return {"by_id":by_id,"kinds":kinds,"meshes":meshes,"materials":material_map,"models":models,"colliders":boxes,"dependencies":dependencies,
            "textures":textures,"texture_artifacts":texture_artifacts,"texture_indices":texture_indices,
            "enemy_types":enemy_types,"enemies":enemies,"test_starts":test_starts,"characters":characters,
            "resolved_document":data,"resolved_catalog":resolved_catalog}


def atomic_write(path, data):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    payload=data if isinstance(data,bytes) else data.encode("utf-8")
    if path.exists() and path.read_bytes()==payload: return False
    temporary=path.with_name(path.name+f".{os.getpid()}.tmp")
    temporary.write_bytes(payload); os.replace(temporary,path)
    return True


def f(value):
    result=format(float(value),".9g")
    return result+("" if "." in result or "e" in result.lower() else ".0")+"f"


def vec(values): return "{"+", ".join(f(v) for v in values)+"}"


def header(data, state):
    lines=['/* Generated 3D content. Edit canonical sources, not this file. */','#ifndef DL_DEMO_LEVEL_GENERATED_H','#define DL_DEMO_LEVEL_GENERATED_H','#include <stddef.h>','#include "game.h"']
    mesh_ids=list(state["meshes"])
    if state["characters"]:
        lines.append('#include "animation.h"')
        emitted = set()
        for character in state["characters"].values():
            if character["symbol"] not in emitted:
                lines.append(character_c(character, declarations=state.get("shared_characters", False)))
                emitted.add(character["symbol"])
    enemy_indices = {e["id"]:e["enemy_index"] for e in state["enemies"]}
    for i,mesh in enumerate(state["meshes"].values()):
        if mesh_ids[i] in state["characters"]:
            continue
        lines.append(f"static const DlVec3 dl_vertices_{i}[] = {{\n"+",\n".join("    "+vec(v) for v in mesh["vertices"])+"\n};")
        lines.append(f"static const uint16_t dl_indices_{i}[] = {{"+",".join(map(str,mesh["indices"]))+"};")
        if "uvs" in mesh:
            lines.append(f"static const DlVec2 dl_uvs_{i}[] = {{"+", ".join(vec(v) for v in mesh["uvs"])+"};")
        if "normals" in mesh:
            lines.append(f"static const DlNormal dl_normals_{i}[] = {{"+", ".join(
                "{"+", ".join(map(str,quantize_normal(normal)))+"}" for normal in mesh["normals"])+"};")
    mesh_rows = []
    for i, (ident, mesh) in enumerate(state["meshes"].items()):
        character = state["characters"].get(ident)
        if character:
            s = character["symbol"]
            mesh_rows.append(f"    {{{s}_vertices, {len(mesh['vertices'])}, {s}_indices, {len(mesh['indices'])}, {s}_uvs, {s}_normals, &{s}}}")
        else:
            mesh_rows.append(f"    {{dl_vertices_{i}, {len(mesh['vertices'])}, dl_indices_{i}, {len(mesh['indices'])}, "+
                             (f"dl_uvs_{i}" if "uvs" in mesh else "NULL")+", "+
                             (f"dl_normals_{i}" if "normals" in mesh else "NULL")+", NULL}")
    lines.append("static const DlMesh dl_demo_meshes[] = {\n"+",\n".join(mesh_rows)+"\n};")
    if state["textures"]:
        lines.append("static const DlTexture dl_demo_textures[] = {\n"+",\n".join(
            "    {"+json.dumps("rom:/textures/"+t["id"]+".sprite")+f", {t['width']}, {t['height']}"+"}"
            for t in state["textures"])+"\n};")
    lines.append("static const DlModelInstance dl_demo_models[] = {")
    for e in state["models"]:
        t=e["transform"]; material=state["materials"][e["material"]]; color=material["color"]
        emissive="{"+",".join(str(round(v*255)) for v in material.get("emissive", [0,0,0]))+"}"
        sides="true" if material.get("double_sided",True) else "false"
        highlight = str(e.get("loot_highlight", False)).lower()
        lines.append("    {"+f"{json.dumps(e['id'])}, {mesh_ids.index(e['model'])}, {vec(t['position'])}, {vec([math.radians(v) for v in t['rotation']])}, {vec(t['scale'])}, "+"{"+",".join(str(round(v*255)) for v in color)+"}, "+ROLES.get(e["kind"],"DL_MODEL_STATIC")+f", {state['texture_indices'].get(e['material'], -1)}, {emissive}, {sides}, {enemy_indices.get(e['id'], -1)}, {highlight}"+"},")
    lines.append("};\nstatic const DlCollider dl_demo_colliders[] = {")
    for box in state["colliders"]:
        lines.append("    {"+f"{vec(box['center'])}, {vec(box['half_size'])}, {f(box['yaw'])}, "+("true" if box["door"] else "false")+"},")
    lines.append("};")
    kinds,by_id=state["kinds"],state["by_id"]
    spawn=kinds["spawn"][0]
    for enemy in state["enemies"]:
        if enemy["patrol_ids"]:
            lines.append(f"static const DlVec3 dl_enemy_patrol_{enemy['enemy_index']}[] = {{"+",".join(vec(by_id[ref]["transform"]["position"]) for ref in enemy["patrol_ids"])+"};")
    if state["enemies"]:
        lines.append("static const DlEnemyDef dl_demo_enemies[] = {")
        for enemy in state["enemies"]:
            t = by_id[enemy["id"]]["transform"]
            route = f"dl_enemy_patrol_{enemy['enemy_index']}" if enemy["patrol_ids"] else "NULL"
            lines.append("    {"+f".id={json.dumps(enemy['id'])}, .type=DL_ENEMY_{enemy['symbol']}, .behavior=DL_BEHAVIOR_{enemy['behavior'].upper()}, "+
                         f".spawn={vec(t['position'])}, .yaw={f(math.radians(t['rotation'][1]))}, .patrol={route}, .patrol_count={len(enemy['patrol_ids'])}, "+
                         f".speed={f(enemy['speed'])}, .sight_range={f(enemy['sight_range'])}, .hearing_range={f(enemy['hearing_range'])}"+"},")
        lines.append("};")
    lines.append("static const DlLight dl_demo_lights[] = {"+",".join("{"+vec(e["transform"]["position"])+", "+f(e["radius"])+", "+f(e["intensity"])+", "+vec(e.get("color",[1,.75,.4]))+"}" for e in kinds["light"])+"};")
    if state["test_starts"]:
        lines.append("static const DlStartPreset dl_demo_test_starts[] = {")
        for start in state["test_starts"]:
            lines.append("    {"+f".id={json.dumps(start['id'])}, .label={json.dumps(start['label'])}, "+
                         f".position={vec(start['position'])}, .yaw={f(math.radians(start['yaw']))}, .pitch={f(math.radians(start['pitch']))}, "+
                         f".door_open={str(start['door_open']).lower()}, .crouched={str(start['crouched']).lower()}"+"},")
        lines.append("};")
    env=data.get("environment")
    environment="{.enabled=false}"
    if env is not None:
        direction=env["moon_direction"]; length=math.sqrt(sum(v*v for v in direction))
        env=dict(env,moon_direction=[v/length for v in direction])
        environment="{.enabled=true, "+", ".join("."+key+"="+(vec(value) if isinstance(value,list) else f(value)) for key,value in env.items() if key in ("ambient","moon_direction","moon_color","moon_intensity","fog_color","fog_near","fog_far","sky_top","sky_bottom","exposure"))+"}"
    lines.append("static const DlLevel dl_demo_level = {\n    "+f".version=2, .title={json.dumps(data['title'])}, .colliders=dl_demo_colliders, .collider_count={len(state['colliders'])},\n    .meshes=dl_demo_meshes, .mesh_count={len(mesh_ids)}, .models=dl_demo_models, .model_count={len(state['models'])},\n    .spawn={vec(spawn['transform']['position'])}, .spawn_yaw={f(math.radians(spawn['transform']['rotation'][1]))},\n    .enemies="+("dl_demo_enemies" if state["enemies"] else "NULL")+f", .enemy_count={len(state['enemies'])}, .control={vec(kinds['control'][0]['transform']['position'])}, .objective={vec(kinds['objective'][0]['transform']['position'])},\n    .lights=dl_demo_lights, .light_count={len(kinds['light'])},\n    .environment={environment}, .textures="+("dl_demo_textures" if state["textures"] else "NULL")+f", .texture_count={len(state['textures'])},\n    .test_starts="+("dl_demo_test_starts" if state["test_starts"] else "NULL")+f", .test_start_count={len(state['test_starts'])}\n"+"};\n#endif\n")
    return "\n".join(lines)


def mesh_gltf(mesh):
    # Expand triangle corners for the preview while retaining authored smooth
    # normals/hard seams. Legacy meshes still derive geometric flat normals.
    vertices,normals,uvs=[],[],[]
    for i in range(0,len(mesh["indices"]),3):
        points=[mesh["vertices"][j] for j in mesh["indices"][i:i+3]]
        if "normals" in mesh:
            normals.extend(mesh["normals"][j] for j in mesh["indices"][i:i+3])
        else:
            a,b=[[points[k][j]-points[0][j] for j in range(3)] for k in (1,2)]
            n=[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
            length=math.sqrt(sum(v*v for v in n)); n=[v/length for v in n]
            normals.extend([n,n,n])
        vertices.extend(points)
        if "uvs" in mesh: uvs.extend(mesh["uvs"][j] for j in mesh["indices"][i:i+3])
    pb=struct.pack("<"+"f"*len(vertices)*3,*(v for p in vertices for v in p))
    nb=struct.pack("<"+"f"*len(normals)*3,*(v for p in normals for v in p))
    ub=struct.pack("<"+"f"*len(uvs)*2,*(v for p in uvs for v in p)) if uvs else b""
    result={"asset":{"version":"2.0","generator":"DarkLantern64 OBJ compiler"},"buffers":[{"byteLength":len(pb+nb+ub),"uri":"data:application/octet-stream;base64,"+base64.b64encode(pb+nb+ub).decode()}],"bufferViews":[{"buffer":0,"byteOffset":0,"byteLength":len(pb)},{"buffer":0,"byteOffset":len(pb),"byteLength":len(nb)}],"accessors":[{"bufferView":0,"componentType":5126,"count":len(vertices),"type":"VEC3","min":[min(p[j] for p in vertices) for j in range(3)],"max":[max(p[j] for p in vertices) for j in range(3)]},{"bufferView":1,"componentType":5126,"count":len(vertices),"type":"VEC3"}],"meshes":[{"primitives":[{"attributes":{"POSITION":0,"NORMAL":1}}]}],"nodes":[{"mesh":0}],"scenes":[{"nodes":[0]}],"scene":0}
    if uvs:
        result["bufferViews"].append({"buffer":0,"byteOffset":len(pb+nb),"byteLength":len(ub)})
        result["accessors"].append({"bufferView":2,"componentType":5126,"count":len(uvs),"type":"VEC2"})
        result["meshes"][0]["primitives"][0]["attributes"]["TEXCOORD_0"]=2
    return result


def preview_scene(data,state):
    data=state["resolved_document"]
    mats=list(state["materials"])
    materials=[{"id":i+1,"name":m["id"],"shadingModel":"PBR","baseColor":m["color"],"roughness":.85,"metallic":0,"ambient":.3,"displacementIntensity":0} for i,m in enumerate(data["materials"])]
    environment=data.get("environment")
    for material,source in zip(materials,data["materials"]):
        material["emissive"]=source.get("emissive",[0,0,0])+[1]
        # Preserve material intent in preview metadata. The current desktop
        # renderer remains two-sided; N64 rendering applies this culling flag.
        material["doubleSided"]=source.get("double_sided",True)
        if environment: material["ambient"]=sum(environment["ambient"])/3
        if source["id"] in state["texture_indices"]:
            texture=state["textures"][state["texture_indices"][source["id"]]]
            material["textures"]={"albedo":"file://textures/"+texture["id"]+".png"}
    entities=[]
    for e in data["entities"]:
        t=e["transform"]
        out={"name":e["id"],"transform":{"position":t["position"],"rotation":quaternion(t["rotation"]),"scale":t["scale"]}}
        if "model" in e:
            out.update(mesh="file://meshes/"+e["model"]+".gltf",material="gen://basic",materialId=mats.index(e["material"])+1)
        if e["kind"]=="light":
            out["light"]={"type":"point","color":e.get("color",[1,.75,.4])+[e["intensity"]*12],"radius":.15,"range":e["radius"]*2}
        entities.append(out)
    entities.append({"name":"Editor camera","transform":{"position":[0,19,22],"rotation":quaternion([-42,0,0]),"scale":[1,1,1]},"camera":{"active":True,"fov":55,"nearPlane":.1,"farPlane":200,"exposure":environment["exposure"] if environment else 1,"orbitDistance":29}})
    moon_rotation=[-25,0,0]; moon_color=[.75,.85,1,1.5]
    if environment:
        direction=environment["moon_direction"]; length=math.sqrt(sum(v*v for v in direction))
        moon_rotation=[-math.degrees(math.asin(direction[1]/length)),math.degrees(math.atan2(direction[0],direction[2])),0]
        moon_color=environment["moon_color"]+[environment["moon_intensity"]]
    entities.append({"name":"Editor moon" if environment else "Editor fill","transform":{"position":[0,0,0],"rotation":quaternion(moon_rotation),"scale":[1,1,1]},"light":{"type":"directional","color":moon_color,"castShadows":bool(environment),"cascadeCount":1,"shadowDistance":30,"shadowDepth":50}})
    return {"entities":entities,"materials":materials,"effects":[],
            "metadata":{"darklantern":{"asset_packs":data.get("asset_packs",[]),"resolved_catalog":state["resolved_catalog"]}}}


def compile_level(source=ROOT/"content/first_room.json",output=ROOT/"build",asset_root=None, *, shared_characters=None):
    started=time.perf_counter(); source,output=Path(source),Path(output)
    raw=source.read_bytes(); data=json.loads(raw)
    state=validate(data,asset_root or source.parent)
    data=state["resolved_document"]
    state["shared_characters"] = shared_characters is not None
    if shared_characters is not None:
        for character in state["characters"].values():
            shared_characters[character["symbol"]] = character
    digest=hashlib.sha256(raw)
    for uri,path in state["dependencies"]: digest.update(uri.encode()); digest.update(b"\0"); digest.update(path.read_bytes())
    artifacts={output/"generated/demo_level.h":header(data,state),output/"editor-assets/levels/first_room.json":json.dumps(preview_scene(data,state),indent=2)+"\n"}
    artifacts.update((output/path,png) for path,png in state["texture_artifacts"].items())
    for ident,mesh in state["meshes"].items(): artifacts[output/f"editor-assets/meshes/{ident}.gltf"]=json.dumps(mesh_gltf(mesh),separators=(",",":"))+"\n"
    for ident, character in state["characters"].items():
        preview = {k: character[k] for k in ("id", "head_bone", "bounds_center", "bounds_radius", "encoded_bytes")}
        bind_mesh = {key: character["mesh"][key] for key in ("vertices", "uvs", "indices")}
        # Dynamic editor skinning consumes the target normal directions, not
        # the higher-precision Blender normals discarded by the N64 format.
        bind_mesh["normals"] = []
        for normal in character["mesh"]["normals"]:
            target = quantize_normal(normal)
            length = math.sqrt(sum(v*v for v in target))
            bind_mesh["normals"].append([v/length for v in target])
        preview.update(schema_version=1, source_sha256=character["source_sha256"], vertex_bones=character["mesh"]["joints"],
                       bind_mesh=bind_mesh,
                       bones=[{"name": b["id"], "parent": b["parent"],
                               "rest": {k: b[k] for k in ("translation", "rotation")}, "inverse_bind": b["inverse_bind"]}
                              for b in character["bones"]], sockets=character["sockets"], clips=[], joint_meshes=[],
                       cross_joint_triangles=character["report"]["cross_joint_triangles"])
        # Animation, skin weights and bind attributes can change in-place in a
        # fixed-topology preview. Only the vertex count and index sequence need
        # a different GPU allocation; a full source hash would leak one mesh
        # per character instance on every artist animation re-export.
        preview["topology_sha256"] = hashlib.sha256(json.dumps(
            {"vertex_count": len(character["mesh"]["vertices"]), "indices": character["mesh"]["indices"]},
            separators=(",", ":")).encode("utf-8")).hexdigest()
        for clip in character["clips"]:
            cooked_clip = {k: clip[k] for k in ("id", "duration", "stride_length", "translation_scale", "sample_count", "loop")}
            cooked_clip["tracks"] = [{**t, "channel": "rotation" if t["channel"] == 0 else "translation"} for t in clip["tracks"]]
            cooked_clip["events"] = [{**e, "kind": "foot_left" if e["kind"] == 0 else "foot_right"} for e in clip["events"]]
            preview["clips"].append(cooked_clip)
        for joint, mesh in joint_meshes(character).items():
            uri = f"meshes/{ident}-joint-{joint}.gltf"
            artifacts[output/"editor-assets"/uri] = json.dumps(mesh_gltf(mesh), separators=(",", ":"))+"\n"
            preview["joint_meshes"].append({"joint": joint, "uri": uri})
        artifacts[output/f"editor-assets/characters/{ident}.json"] = json.dumps(preview, separators=(",", ":"))+"\n"
    changed=[str(path.relative_to(output)) for path,text in artifacts.items() if atomic_write(path,text)]
    vertices=sum(len(state["meshes"][e["model"]]["vertices"]) for e in state["models"])
    triangles=sum(len(state["meshes"][e["model"]]["indices"])//3 for e in state["models"])
    # Two local aliases of one character share the emitted source-hash arrays.
    # Static OBJ aliases retain their existing independently emitted arrays.
    compiled_meshes = [mesh for ident, mesh in state["meshes"].items() if ident not in state["characters"]]
    unique_characters = {c["source_sha256"]: c for c in state["characters"].values()}
    compiled_meshes.extend(c["mesh"] for c in unique_characters.values())
    normal_bytes=sum(len(m.get("normals",[]))*3 for m in compiled_meshes)
    arrays=sum(len(m["vertices"])*12+len(m["indices"])*2+len(m.get("uvs",[]))*8 for m in compiled_meshes)+normal_bytes
    report={
        "version":2, "source_sha256":digest.hexdigest(), "title":data["title"],
        "resolved_catalog":state["resolved_catalog"], "limits":dict(LIMITS),
        "dependencies":[{"uri":uri,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()} for uri,path in state["dependencies"]],
        "counts":{"entities":len(data["entities"]), "meshes":len(state["meshes"]), "models":len(state["models"]),
                  "colliders":len(state["colliders"]), "instanced_vertices":vertices, "instanced_triangles":triangles,
                  "lights":len(state["kinds"]["light"]), "enemies":len(state["enemies"]),
                  "test_starts":len(state["test_starts"]),
                  "patrol_points":sum(len(enemy["patrol_ids"]) for enemy in state["enemies"])},
        "compiled_geometry_bytes":arrays,
        "compiled_normal_bytes":normal_bytes,
        "characters": {ident: character["report"] for ident, character in state["characters"].items()},
        "memory_note":"Geometry arrays only; runtime buffers, state and other data need separate RDRAM measurement. Baseline 8 MiB.",
        "ids":{e["id"]:{"kind":e["kind"],"source_index":i,"transform":e["transform"],
                         "loot_highlight":e.get("loot_highlight", False),
                         "model_index":next((j for j,m in enumerate(state["models"]) if m["id"]==e["id"]),None)}
               for i,e in enumerate(data["entities"])},
        "enemy_types":[{key:value for key,value in enemy_type.items() if key != "symbol"} for enemy_type in state["enemy_types"]],
        "enemy_instances":{enemy["id"]:{key:value for key,value in enemy.items() if key not in ("id", "symbol")}
                           for enemy in state["enemies"]},
        "test_starts":state["test_starts"],
        # Kept for older clients; new authoring tools use each enemy's patrol_ids.
        "patrol_ids":state["enemies"][0]["patrol_ids"] if state["enemies"] else [],
        "changed_artifacts":changed, "elapsed_ms":round((time.perf_counter()-started)*1000,2),
    }
    texture_report={"version":1,"textures":state["textures"],"decoded_bytes":sum(t["decoded_bytes"] for t in state["textures"]),
                    "memory_note":"Unique decoded pixel bytes only. Sprite headers, allocation overhead and renderer buffers are additional; TMEM holds one texture at a time."}
    report["textures"]=texture_report
    atomic_write(output/"generated/character_report.json", json.dumps({"version": 1, "characters": report["characters"]}, indent=2)+"\n")
    atomic_write(output/"generated/texture_report.json",json.dumps(texture_report,indent=2)+"\n")
    atomic_write(output/"generated/level_report.json",json.dumps(report,indent=2)+"\n")
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("source",nargs="?",type=Path,default=ROOT/"content/first_room.json")
    p.add_argument("--output",type=Path,default=ROOT/"build")
    p.add_argument("--asset-root",type=Path)
    p.add_argument("--validate-only",action="store_true")
    a=p.parse_args()
    try:
        if a.validate_only: validate(json.loads(a.source.read_text()),a.asset_root or a.source.parent); print('{"ok":true}')
        else: print(json.dumps(compile_level(a.source,a.output,a.asset_root),indent=2))
    except (ContentError,ValueError,OSError,KeyError,TypeError) as error:
        print(f"Content error: {error}",file=sys.stderr); return 1
    return 0


if __name__=="__main__": raise SystemExit(main())
