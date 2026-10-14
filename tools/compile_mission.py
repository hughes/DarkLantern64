#!/usr/bin/env python3
"""Cook a bounded version-2 scene and manual partitions into DLM1/DLR1 bytes.

Night missions use DLR1 v4 with native-cooked two-state triangle lighting.
Persistent gameplay and shared dynamic visuals live in the stream bootstrap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import zlib

try:
    import compile_level as level
    from asset_pack import content_path
    from cook_stream_lighting import prepare_stream_lighting
    import cook_diagnostics as diagnostics
except ModuleNotFoundError:
    from tools import compile_level as level
    from tools.asset_pack import content_path
    from tools.cook_stream_lighting import prepare_stream_lighting
    from tools import cook_diagnostics as diagnostics

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY_HEADER_BYTES = 64
REGION_RECORD_BYTES = 64
ENTITY_RECORD_BYTES = 16
CHUNK_HEADER_BYTES = 32
SECTION_RECORD_BYTES = 24
ENTITY_KINDS = {"guard": 1, "door": 2, "loot": 3, "objective": 4}
RECORD_BYTES = {1: 44, 2: 32, 3: 4, 4: 36}
COOK_VERSION = 2


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_source(document):
    """Only unordered definition arrays are sorted; route/triangle order matters."""
    result = dict(document)
    for key in ("assets", "materials", "entities"):
        if isinstance(result.get(key), list):
            result[key] = sorted(result[key], key=lambda item: item.get("id", ""))
    return result


def align16(value):
    return (value + 15) & ~15


def fields(value, required, label, optional=()):
    level.require(isinstance(value, dict), label + ": expected an object")
    level.require(set(required) <= set(value) <= set(required) | set(optional), label + ": missing or unknown fields")


def read_source(manifest, content_root, source_document=None):
    path = content_path(manifest["source"], content_root, {".json"})
    level.require(path.stat().st_size <= 64 * 1024 * 1024, "Mission source exceeds 64 MiB")
    raw = path.read_bytes()
    document = json.loads(raw) if source_document is None else source_document
    if source_document is not None:
        raw = canonical_json(source_document)
    # Run the profile gate before unsupported-feature checks or asset reads.
    limits = level.content_limits(document, level.LIMITS, streaming=True)
    level.require(len(raw) <= limits["level_bytes"], "Mission source exceeds its source profile byte limit")
    level.require(not document.get("asset_packs"), "DLM1 v1 does not support linked asset packs; use local bounded definitions")
    dependencies = {}
    for asset in document.get("assets", []):
        level.require(isinstance(asset, dict), "asset: expected an object")
        level.require(asset.get("type") != "character", "DLM1 v1 does not support animated character assets")
        if asset.get("type") == "primitive":
            continue
        dependency = content_path(asset.get("uri"), content_root, {".obj"})
        dependencies[dependency.relative_to(content_root).as_posix()] = hashlib.sha256(dependency.read_bytes()).hexdigest()
    for material in document.get("materials", []):
        level.require(isinstance(material, dict), "material: expected an object")
        if "texture" in material:
            level.require(isinstance(material["texture"], dict), "material.texture: expected an object")
            dependency = content_path(material["texture"].get("uri"), content_root, {".png", ".jpg", ".jpeg"})
            dependencies[dependency.relative_to(content_root).as_posix()] = hashlib.sha256(dependency.read_bytes()).hexdigest()
    state = level.validate(canonical_source(document), content_root, streaming=True)
    for entity in state["models"]:
        if entity["kind"] == "static" and "texture" in state["materials"][entity["material"]]:
            level.require("uvs" in state["meshes"][entity["model"]],
                          entity["id"] + ": textured stream geometry requires evaluated UVs")
    return document, state, raw, dependencies


def validated_regions(manifest, state):
    regions = manifest["regions"]
    level.require(isinstance(regions, list) and 1 <= len(regions) <= 256, "regions: expected 1..256 manual regions")
    by_id, homes = {}, {}
    for region in regions:
        fields(region, {"id", "bounds", "entities", "neighbors"}, "region", {"activation_bounds", "working_set"})
        ident = level.identifier(region["id"], "region.id")
        level.require(ident not in by_id, ident + ": duplicate region ID")
        bounds = region["bounds"]
        level.require(isinstance(bounds, list) and len(bounds) == 2, ident + ".bounds: expected [minimum, maximum]")
        minimum, maximum = [level.vector(row, ident + ".bounds") for row in bounds]
        level.require(all(a < b for a, b in zip(minimum, maximum)), ident + ".bounds: minimum must be below maximum")
        entities, neighbors = region["entities"], region["neighbors"]
        level.require(isinstance(entities, list) and entities, ident + ".entities: expected a nonempty home ID list")
        level.require(isinstance(neighbors, list), ident + ".neighbors: expected an array")
        for entity in entities:
            level.require(isinstance(entity, str) and entity in state["by_id"], ident + ": unknown entity reference")
            level.require(entity not in homes, entity + ": must have exactly one home region")
            homes[entity] = ident
        level.require(all(isinstance(neighbor, str) for neighbor in neighbors), ident + ": invalid neighbor ID")
        level.require(len(neighbors) == len(set(neighbors)), ident + ": duplicate neighbor")
        level.require(ident not in neighbors, ident + ": self neighbor is invalid")
        by_id[ident] = dict(id=ident, bounds=[minimum, maximum], entities=sorted(entities), neighbors=sorted(neighbors))
        if manifest.get("visibility") == "authored-sets":
            activation = region.get("activation_bounds")
            level.require(isinstance(activation, list) and len(activation) == 2, ident + ": activation_bounds need minimum/maximum XYZ")
            activation = [level.vector(row, ident + ".activation_bounds") for row in activation]
            level.require(all(a < b for a, b in zip(*activation)), ident + ": activation bounds must be ordered")
            working = region.get("working_set")
            level.require(isinstance(working, list) and working and all(isinstance(row, str) for row in working) and
                          len(working) == len(set(working)) and ident in working, ident + ": working_set needs distinct region IDs including self")
            by_id[ident].update(activation_bounds=activation, working_set=sorted(working))
    level.require(set(homes) == set(state["by_id"]), "Every source entity must have exactly one home region; missing: " + ", ".join(sorted(set(state["by_id"]) - set(homes))))
    level.require(isinstance(manifest["entry_region"], str) and manifest["entry_region"] in by_id, "entry_region: unknown region")
    for region in by_id.values():
        level.require(all(neighbor in by_id for neighbor in region["neighbors"]), region["id"] + ": unknown neighbor reference")
        if "working_set" in region:
            level.require(all(ident in by_id for ident in region["working_set"]), region["id"] + ": unknown working_set region")
    # A home is ownership, not necessarily a disjoint geometric volume.
    for entity, home in homes.items():
        assert_bounds(by_id[home], [state["by_id"][entity]["transform"]["position"]], entity + " position")
    return [by_id[ident] for ident in sorted(by_id)], homes


def authored_policy(manifest, regions, state, homes):
    if manifest.get("visibility") != "authored-sets":
        level.require(not manifest.get("transitions"), "Transitions require authored-sets visibility")
        return None
    by_id = {row["id"]: row for row in regions}
    source = manifest.get("transitions")
    level.require(isinstance(source, list) and len(source) <= 256, "Authored transitions need 0..256 directed gates")
    gates, ids = [], set()
    for row in source:
        fields(row, {"id", "from", "to", "bounds"}, "transition", {"door"})
        ident = level.identifier(row["id"], "transition.id")
        level.require(ident not in ids, ident + ": duplicate transition ID")
        ids.add(ident)
        level.require(isinstance(row["from"], str) and isinstance(row["to"], str) and row["from"] in by_id and
                      row["to"] in by_id and row["from"] != row["to"], ident + ": invalid transition regions")
        level.require(row["to"] in by_id[row["from"]]["neighbors"], ident + ": destination must be an authored neighbor")
        bounds = row["bounds"]
        level.require(isinstance(bounds, list) and len(bounds) == 2, ident + ": transition bounds need minimum/maximum XYZ")
        bounds = [level.vector(value, ident + ".bounds") for value in bounds]
        level.require(all(a < b for a, b in zip(*bounds)), ident + ": gate bounds must be ordered")
        for side in (row["from"], row["to"]):
            activation = by_id[side]["activation_bounds"]
            level.require(all(max(bounds[0][a], activation[0][a]) <= min(bounds[1][a], activation[1][a]) for a in range(3)),
                          ident + ": gate must intersect both activation regions")
        gate=dict(id=ident, **{"from": row["from"], "to": row["to"]}, bounds=bounds)
        if row.get("door") is not None:
            door=row["door"]
            level.require(isinstance(door,str) and door in state["by_id"] and state["by_id"][door]["kind"]=="door",
                          ident + ": door must identify a canonical door")
            level.require(homes[door] in (row["from"],row["to"]),ident + ": door home must match an endpoint")
            level.require(not any(other.get("door")==door and other["from"]==row["from"] for other in gates),
                          ident + ": one door can select only one target from a region")
            gate["door"]=door
        gates.append(gate)
    gates.sort(key=lambda row: row["id"])
    reached = {manifest["entry_region"]}
    while True:
        expanded = reached | {row["to"] for row in gates if row["from"] in reached}
        if expanded == reached:
            break
        reached = expanded
    level.require(reached == set(by_id), "Every authored region must be reachable from entry through directed transitions")
    for region in regions:
        outgoing = [row for row in gates if row["from"] == region["id"]]
        level.require(len(outgoing) <= 8, region["id"] + ": at most 8 outgoing gates are supported")
        level.require(set(region["neighbors"]) == {row["to"] for row in outgoing}, region["id"] + ": every neighbor needs a directed gate")
    starts = [("spawn", state["kinds"]["spawn"][0]["transform"]["position"])] + [(row["id"], row["position"]) for row in state["test_starts"]]
    for label, point in starts:
        level.require(any(all(row["activation_bounds"][0][a] <= point[a] <= row["activation_bounds"][1][a] for a in range(3)) for row in regions),
                      label + ": start is outside all activation bounds")
    return dict(regions=regions, transitions=gates)


def working_sets(policy, region_reports, state, homes):
    """Stable/chosen-transition unions, including partial publication."""
    rows = {row["source_id"]: row for row in region_reports}
    definitions = {row["id"]: row for row in policy["regions"]} if policy else {}
    gates = policy["transitions"] if policy else []
    requests = []
    if policy:
        for region in policy["regions"]:
            outgoing = [row for row in gates if row["from"] == region["id"]]
            for selected in [[],*[[gate] for gate in outgoing]]:
                wanted = set(region["working_set"])
                for gate in selected:
                    wanted.update(definitions[gate["to"]]["working_set"])
                requests.append((region["id"], set(region["working_set"]), wanted, [row["id"] for row in selected]))
    else:
        requests = [(name, {name}, set(rows), []) for name in rows]
    result, seen = [], set()
    for anchor, stable, wanted, gate_ids in requests:
        optional = sorted(wanted-stable)
        candidates = [stable,wanted] if not policy or len(wanted)>5 else [
            stable | {name for index,name in enumerate(optional) if bits & (1 << index)} for bits in range(1 << len(optional))]
        for resident in candidates:
            key = (anchor, tuple(sorted(resident)))
            if key in seen:
                continue
            seen.add(key)
            if policy:
                barriers = sum(row["from"] in resident and not set(definitions[row["to"]]["working_set"]) <= resident for row in gates)
            else:
                barriers = len(rows)-len(resident)
            dynamic = [entity for entity in state["models"] if entity["kind"] != "static" and
                       (entity["kind"] in ("door", "guard") or homes[entity["id"]] in resident)]
            triangles = sum(rows[name]["triangle_count"] for name in resident)
            groups = sum(len(rows[name]["groups"]) for name in resident)
            result.append(dict(anchor=anchor, gates=gate_ids, regions=[rows[name]["id"] for name in sorted(resident)],
                source_regions=sorted(resident), triangle_count=triangles, group_count=groups,
                collider_count=sum(rows[name]["collider_count"] for name in resident),
                floor_count=sum(rows[name].get("floor_count", 0) for name in resident), barrier_count=barriers,
                model_count=groups+len(dynamic), scene_vertices=triangles*3+sum(len(state["meshes"][entity["model"]]["vertices"]) for entity in dynamic),
                scene_triangles=triangles+sum(len(state["meshes"][entity["model"]]["indices"])//3 for entity in dynamic),
                chunk_bytes=sum(rows[name]["chunk_bytes"] for name in resident),
                groups=[group for name in sorted(resident) for group in rows[name]["groups"]],
                contributors=[row for name in sorted(resident) for row in rows[name]["contributors"]] +
                    [dict(id=entity["id"], kind=entity["kind"], home_region=homes[entity["id"]],
                          static_triangles=0, model_triangles=len(state["meshes"][entity["model"]]["indices"])//3,
                          model_vertices=len(state["meshes"][entity["model"]]["vertices"]), colliders=0, floors=0,
                          membership="global bootstrap potential") for entity in dynamic if homes[entity["id"]] not in resident]))
    return result, max(len(wanted) for _, _, wanted, _ in requests)


def assert_bounds(region, points, label):
    minimum, maximum = region["bounds"]
    level.require(all(minimum[axis] <= point[axis] <= maximum[axis] for point in points for axis in range(3)),
                  region["id"] + ": bounds do not cover " + label)


def collider_corners(box):
    c, s = math.cos(box["yaw"]), math.sin(box["yaw"])
    x, y, z = box["center"]
    hx, hy, hz = box["half_size"]
    return [[x + c * dx + s * dz, y + dy, z - s * dx + c * dz]
            for dx in (-hx, hx) for dy in (-hy, hy) for dz in (-hz, hz)]


def make_chunk(origin, sections, version=1):
    """Section tuples are (kind, payload, count); empty sections are omitted."""
    sections = sorted((row for row in sections if row[2]), key=lambda row: row[0])
    level.require(1 <= len(sections) <= (4 if version >= 3 else 3), "Region requires at least one section")
    cursor = align16(CHUNK_HEADER_BYTES + SECTION_RECORD_BYTES * len(sections))
    descriptors, payloads = [], []
    for kind, payload, count in sections:
        record_bytes = (128 if version >= 4 else 80) if kind == 1 and version >= 2 else RECORD_BYTES[kind]
        level.require(len(payload) == count * record_bytes, "Internal section length mismatch")
        descriptors.append(struct.pack(">HHIIIII", kind, 0, cursor, len(payload), count, record_bytes, 0))
        payloads.append((cursor, payload))
        cursor = align16(cursor + len(payload))
    # Trailing padding is unnecessary; inter-section padding is deterministic.
    size = payloads[-1][0] + len(payloads[-1][1])
    result = bytearray(size)
    struct.pack_into(">4sHHII3fI", result, 0, b"DLR1", version, CHUNK_HEADER_BYTES, size, len(sections), *origin, 0)
    for index, descriptor in enumerate(descriptors):
        start = CHUNK_HEADER_BYTES + index * SECTION_RECORD_BYTES
        result[start:start + SECTION_RECORD_BYTES] = descriptor
    for start, payload in payloads:
        result[start:start + len(payload)] = payload
    return bytes(result)


def triangle_attributes(mesh, corners, transform, positions):
    """World-oriented normals and the runtime UV convention used by read_obj."""
    uvs = [value for index in corners for value in mesh.get("uvs", [[0, 0]] * len(mesh["vertices"]))[index]]
    normals = []
    if "normals" not in mesh:
        a = [positions[1][axis] - positions[0][axis] for axis in range(3)]
        b = [positions[2][axis] - positions[0][axis] for axis in range(3)]
        face = [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]
    for index in corners:
        normal = level.rotate([mesh["normals"][index][axis] / transform["scale"][axis] for axis in range(3)],
                              transform["rotation"]) if "normals" in mesh else face
        length = math.hypot(*normal)
        level.require(length > 0 and math.isfinite(length), "Stream triangle has a degenerate normal")
        normals.extend(level.quantize_normal([value / length for value in normal]))
    return struct.pack(">6f9bB2x", *uvs, *normals, 3)


def target_float(value):
    return struct.unpack(">f", struct.pack(">f", value))[0]


def stream_lighting_scene(document, state, regions, payloads, material_ids):
    """Emit the full shadow scene with exactly the stream's target geometry.

    Static instances are flattened into the same region/material meshes that
    the runtime publishes. Their vertices remain local to the serialized
    origin; quantized normals are copied directly, without a second rotation.
    """
    full = dict(state, models=[], meshes={}, colliders=[], floors=[], streamed_source=False)
    full.pop("baked_lighting", None)
    full["materials"] = {ident: dict(material, emissive=[0, 0, 0]) for ident, material in state["materials"].items()}
    material_names = {value: ident for ident, value in material_ids.items()}
    groups = []
    for region, payload in zip(regions, payloads):
        origin = [target_float(value) for value in payload["origin"]]
        grouped = {}
        triangles = payload["triangles"]
        for offset in range(0, len(triangles), 128):
            material, = struct.unpack_from(">I", triangles, offset + 36)
            grouped.setdefault(material, []).append(offset)
        for material, offsets in grouped.items():
            ident = f"stream-{region['id']}-material-{material}"
            vertices, normals, uvs = [], [], []
            for offset in offsets:
                values = struct.unpack_from(">9f", triangles, offset)
                vertices.extend([list(values[index:index + 3]) for index in range(0, 9, 3)])
                values = struct.unpack_from(">6f", triangles, offset + 44)
                uvs.extend([list(values[index:index + 2]) for index in range(0, 6, 2)])
                values = struct.unpack_from(">9b", triangles, offset + 68)
                normals.extend([[channel / 127 for channel in values[index:index + 3]] for index in range(0, 9, 3)])
            level.require(len(vertices) <= 4096, ident + ": full lighting group exceeds 4096 mesh vertices")
            full["meshes"][ident] = dict(vertices=vertices, indices=list(range(len(vertices))), normals=normals, uvs=uvs)
            full["materials"][ident] = dict(state["materials"][material_names[material]], emissive=[0, 0, 0])
            full["texture_indices"] = dict(full["texture_indices"])
            if material_names[material] in full["texture_indices"]:
                full["texture_indices"][ident] = full["texture_indices"][material_names[material]]
            full["models"].append(dict(id=ident, kind="static", model=ident, material=ident,
                                       transform=dict(position=origin, rotation=[0, 0, 0], scale=[1, 1, 1])))
            groups.append((payload, offsets))
        for offset in range(0, len(payload["colliders"]), 32):
            values = struct.unpack_from(">7fI", payload["colliders"], offset)
            ident = payload["collider_entities"][offset // 32]
            full["colliders"].append(dict(id=ident,
                center=[target_float(values[axis] + origin[axis]) for axis in range(3)],
                half_size=list(values[3:6]), yaw=values[6], door=state["by_id"][ident]["kind"] == "door"))
        for offset in range(0, len(payload["floors"]), 36):
            values = struct.unpack_from(">9f", payload["floors"], offset)
            full["floors"].append(dict(vertices=[[target_float(values[index * 3 + axis] + origin[axis])
                                                  for axis in range(3)] for index in range(3)]))
    bootstrap_models = [entity for entity in state["models"] if entity["kind"] != "static"]
    for entity in bootstrap_models:
        full["models"].append(entity)
        full["meshes"][entity["model"]] = state["meshes"][entity["model"]]
        # Persistent visuals retain the authored material, including emission.
        full["materials"][entity["material"]] = state["materials"][entity["material"]]
    level.require(len(full["models"]) <= 65535, "Full mission lighting worker supports at most 65535 models")
    return level.header(document, full), groups, bootstrap_models


def bake_stream_payloads(document, state, regions, payloads, material_ids):
    header, groups, bootstrap_models = stream_lighting_scene(document, state, regions, payloads, material_ids)
    report, colors = prepare_stream_lighting(header, collider_count=len(state["colliders"]), floor_count=len(state.get("floors", [])))
    for model, (payload, offsets) in enumerate(groups):
        closed, opened = colors[model]
        level.require(len(closed) == len(offsets) * 24, "Stream lighting group size mismatch")
        for triangle, offset in enumerate(offsets):
            payload["triangles"][offset + 80:offset + 104] = closed[triangle * 24:triangle * 24 + 24]
            payload["triangles"][offset + 104:offset + 128] = opened[triangle * 24:triangle * 24 + 24]
    report["bootstrap_models"] = [dict(id=entity["id"], triangles=len(colors[index][0]) // 24,
        closed_rgba=colors[index][0].hex(), open_rgba=colors[index][1].hex())
        for index, entity in enumerate(bootstrap_models, len(groups)) if index in colors]
    report.update(full_scene_colliders=len(state["colliders"]), full_scene_floors=len(state.get("floors", [])),
                  global_lights=len(state["kinds"]["light"]), stream_groups=len(groups),
                  stream_triangles=sum(len(offsets) for _, offsets in groups),
                  storage="embedded", host_worker_format="DLS1", host_worker_bytes=report["bytes"],
                  standalone_rom_asset_bytes=0,
                  mission_embedded_rgba_bytes=sum(len(offsets) for _, offsets in groups) * 48,
                  bootstrap_embedded_rgba_bytes=sum(len(bytes.fromhex(row[key])) for row in report["bootstrap_models"]
                                                    for key in ("closed_rgba", "open_rgba")),
                  storage_note="bytes and sha256 describe the host worker cache, including its header/table. Triangle RGBA is already included in mission package bytes; bootstrap RGBA is resident constant array data. No standalone lighting ROM asset is emitted.")
    return report


def runtime_debug_map(package_report, state):
    """Map target IDs/indices to authored content without adding ROM payloads."""
    entity_ids = {row["source_id"]: row["id"] for row in package_report["entity_ids"]}
    object_indices = {row["id"]: index for index, row in enumerate(state["objects"])}
    enemy_indices = {row["id"]: index for index, row in enumerate(state["enemies"])}
    policy = {row["id"]: row for row in package_report.get("policy_regions", [])}
    entities = []
    for row in package_report["entity_ids"]:
        entity = state["by_id"][row["source_id"]]
        mapped = dict(row)
        if entity["id"] in object_indices:
            mapped["object_index"] = object_indices[entity["id"]]
        if entity["id"] in enemy_indices:
            mapped["enemy_index"] = enemy_indices[entity["id"]]
        # Preserve importer object IDs and original scene/entity references.
        # Per-triangle provenance belongs to surfaces to avoid duplicating it.
        if isinstance(entity.get("source"), dict):
            mapped["source_provenance"] = {key: value for key, value in entity["source"].items()
                                            if key != "triangle_provenance"}
        entities.append(mapped)
    surfaces = []
    for row in package_report["surface_ids"]:
        mapped = dict(row)
        source = state["by_id"][row["entity"]].get("source")
        provenance = source.get("triangle_provenance", []) if isinstance(source, dict) else []
        if row["triangle"] < len(provenance):
            mapped["source_provenance"] = provenance[row["triangle"]]
        surfaces.append(mapped)
    regions = [dict(row, **policy.get(row["id"], {})) for row in package_report["regions"]]
    boxes = {box["id"]: box for box in state["colliders"]}
    entities_by_id = {row["id"]: row["source_id"] for row in package_report["entity_ids"]}
    colliders = [dict(region=row["id"], record=record, entity_id=ident,
                      source_id=entities_by_id[ident],
                      **{key: boxes[entities_by_id[ident]][key] for key in ("center", "half_size", "yaw")})
                 for row in regions for record, ident in enumerate(row["collider_entity_ids"])]
    transitions = [dict(row, index=index, door_entity_id=entity_ids.get(row.get("door"), 0))
                   for index, row in enumerate(package_report.get("transitions", []))]
    return dict(schema_version=1, package_sha256=package_report["package_sha256"],
                content_identity_sha256=package_report["content_identity_sha256"],
                source=package_report["source"], source_sha256=package_report["source_sha256"],
                source_file_sha256=package_report["source_file_sha256"],
                entry_region=package_report["entry_region"], regions=regions,
                transitions=transitions, entities=entities, surfaces=surfaces, colliders=colliders,
                object_entity_ids=[entity_ids[row["id"]] for row in state["objects"]],
                enemy_entity_ids=[entity_ids[row["id"]] for row in state["enemies"]])


def cook_mission(manifest, content_root=ROOT / "content", *, source_document=None):
    """Validate all inputs before returning reproducible package bytes/report."""
    fields(manifest, {"version", "source", "entry_region", "regions"}, "Mission manifest", {"visibility", "transitions"})
    level.require(type(manifest["version"]) is int and manifest["version"] == 1, "Mission manifest version must be 1")
    level.require(manifest.get("visibility", "door-distance") in ("door-distance", "all-regions", "authored-sets"), "Unsupported mission visibility policy")
    content_root = Path(content_root).resolve()
    document, state, source_raw, dependencies = read_source(manifest, content_root, source_document)
    chunk_version = 4 if document.get("environment") is not None else 3 if state.get("floors") else 2 if state["textures"] else 1
    triangle_bytes = 128 if chunk_version >= 4 else 80 if chunk_version >= 2 else 44
    regions, homes = validated_regions(manifest, state)
    policy = authored_policy(manifest, regions, state, homes)
    region_ids = {row["id"]: index + 1 for index, row in enumerate(regions)}
    entity_ids = {ident: index + 1 for index, ident in enumerate(sorted(state["by_id"]))}
    material_ids = {ident: index + 1 for index, ident in enumerate(sorted(state["materials"]))}
    surface_rows = []
    for ident in sorted(state["by_id"]):
        entity = state["by_id"][ident]
        if entity["kind"] == "static" and "model" in entity:
            mesh = state["meshes"][entity["model"]]
            for triangle in range(len(mesh["indices"]) // 3):
                surface_rows.append(dict(source_id=f"{ident}/triangle-{triangle:06d}", entity=ident, model=entity["model"], triangle=triangle))
    surface_rows.sort(key=lambda row: row["source_id"])
    surface_ids = {(row["entity"], row["triangle"]): index + 1 for index, row in enumerate(surface_rows)}
    boxes = {box["id"]: box for box in state["colliders"]}
    floors = {floor["id"]: floor for floor in state.get("floors", [])}
    chunks, region_reports, payloads = [], [], []
    for region in regions:
        origin = [(a + b) / 2 for a, b in zip(*region["bounds"])]
        triangle_payload, collider_payload, floor_payload = bytearray(), bytearray(), bytearray()
        collider_entities = []
        region_materials = set()
        group_rows, contributors = {}, []
        for ident in region["entities"]:
            entity = state["by_id"][ident]
            mesh = state["meshes"].get(entity.get("model"))
            static_triangles = len(mesh["indices"]) // 3 if mesh and entity["kind"] == "static" else 0
            contributors.append(dict(id=ident, kind=entity["kind"], home_region=region["id"],
                static_triangles=static_triangles, model_triangles=len(mesh["indices"])//3 if mesh and not static_triangles else 0,
                model_vertices=len(mesh["vertices"]) if mesh and entity["kind"] != "static" else 0,
                colliders=int(ident in boxes), floors=int(ident in floors)))
            if entity["kind"] == "static" and "model" in entity:
                mesh = state["meshes"][entity["model"]]
                material = entity["material"]
                flags = 3 if chunk_version >= 2 else 0
                group = group_rows.setdefault((material, flags), dict(
                    id=region["id"] + "/" + material + "/attributes-" + str(flags),
                    region=region["id"], region_id=region_ids[region["id"]], material=material,
                    material_id=material_ids[material], attribute_flags=flags,
                    triangles=0, source_entity_ids=[], contributors=[]))
                group["triangles"] += static_triangles
                group["source_entity_ids"].append(ident)
                group["contributors"].append(dict(id=ident, triangles=static_triangles))
                points = [level.transform_point(point, entity["transform"]) for point in mesh["vertices"]]
                assert_bounds(region, points, ident + " static geometry")
                for triangle in range(len(mesh["indices"]) // 3):
                    corners = mesh["indices"][triangle * 3:triangle * 3 + 3]
                    positions = [points[index] for index in corners]
                    local = [value - origin[axis] for point in positions for axis, value in enumerate(point)]
                    triangle_payload.extend(struct.pack(">9fII", *local, material_ids[entity["material"]], surface_ids[(ident, triangle)]))
                    if chunk_version >= 2:
                        triangle_payload.extend(triangle_attributes(mesh, corners, entity["transform"], positions))
                    if chunk_version >= 4:
                        triangle_payload.extend(bytes(48))
                    region_materials.add(material_ids[entity["material"]])
            if ident in boxes:
                box = boxes[ident]
                assert_bounds(region, collider_corners(box), ident + " collider")
                local = [box["center"][axis] - origin[axis] for axis in range(3)]
                collider_payload.extend(struct.pack(">7fI", *local, *box["half_size"], box["yaw"], entity_ids[ident]))
                collider_entities.append(ident)
            if ident in floors:
                points = floors[ident]["vertices"]
                assert_bounds(region, points, ident + " floor")
                local = [value-origin[axis] for point in points for axis, value in enumerate(point)]
                payload = struct.pack(">9f", *local)
                decoded = struct.unpack(">9f", payload)
                target_origin = struct.unpack(">3f", struct.pack(">3f", *origin))
                target_local = [list(decoded[vertex*3:vertex*3+3]) for vertex in range(3)]
                level.validate_floor_vertices(target_local, ident + " floor at target local float precision")
                target_world = [[struct.unpack(">f", struct.pack(">f", decoded[vertex*3+axis]+target_origin[axis]))[0]
                                 for axis in range(3)] for vertex in range(3)]
                level.validate_floor_vertices(target_world, ident + " floor at target reconstructed float precision", target_float=True)
                floor_payload.extend(payload)
        entity_payload = b"".join(struct.pack(">I", entity_ids[ident]) for ident in region["entities"])
        payloads.append(dict(origin=origin, triangles=triangle_payload, colliders=collider_payload,
                             floors=floor_payload, entities=entity_payload, collider_entities=collider_entities))
        region_reports.append(dict(id=region_ids[region["id"]], source_id=region["id"], bounds=region["bounds"], origin=origin,
                                   chunk_version=chunk_version, triangle_count=len(triangle_payload) // triangle_bytes, collider_count=len(collider_payload) // 32,
                                   material_ids=sorted(region_materials), floor_count=len(floor_payload)//36,
                                   entity_count=len(region["entities"]),
                                   groups=[group_rows[key] for key in sorted(group_rows)], contributors=contributors,
                                   collider_entity_ids=[entity_ids[ident] for ident in collider_entities]))
    lighting = bake_stream_payloads(document, state, regions, payloads, material_ids) if chunk_version >= 4 else None
    for payload, region_report in zip(payloads, region_reports):
        sections = [(1, payload["triangles"], len(payload["triangles"]) // triangle_bytes),
                    (2, payload["colliders"], len(payload["colliders"]) // 32), (3, payload["entities"], len(payload["entities"]) // 4)]
        if chunk_version >= 3:
            sections.append((4, payload["floors"], len(payload["floors"]) // 36))
        chunk = make_chunk(payload["origin"], sections, version=chunk_version)
        chunks.append(chunk)
        region_report.update(chunk_bytes=len(chunk), crc32=f"{zlib.crc32(chunk):08x}")
    canonical_manifest = dict(manifest, regions=regions)
    if policy:
        canonical_manifest["transitions"] = policy["transitions"]
    source_sha = hashlib.sha256(canonical_json(canonical_source(document))).hexdigest()
    state["renderer_source_sha256"] = source_sha
    identity = hashlib.sha256(canonical_json(dict(format=f"DLM1/DLR1-v{chunk_version}", cook_version=COOK_VERSION,
                                                 lighting_signature=lighting["signature"] if lighting else None, source_sha256=source_sha,
                                                 manifest=canonical_manifest, content_dependencies=dependencies))).digest()
    dependency_ids = [region_ids[neighbor] for region in regions for neighbor in region["neighbors"]]
    directory_bytes = DIRECTORY_HEADER_BYTES + len(regions) * REGION_RECORD_BYTES + len(entity_ids) * ENTITY_RECORD_BYTES + len(dependency_ids) * 4
    offsets, cursor = [], align16(directory_bytes)
    for chunk in chunks:
        offsets.append(cursor)
        cursor = align16(cursor + len(chunk))
    package_bytes = offsets[-1] + len(chunks[-1])
    result = bytearray(package_bytes)
    struct.pack_into(">4sHHIIIIII32s", result, 0, b"DLM1", 1, DIRECTORY_HEADER_BYTES, package_bytes, len(regions), len(entity_ids), directory_bytes,
                     region_ids[manifest["entry_region"]], 0, identity)
    first_dependency = 0
    for index, (region, chunk) in enumerate(zip(regions, chunks)):
        start = DIRECTORY_HEADER_BYTES + index * REGION_RECORD_BYTES
        struct.pack_into(">8I6f8x", result, start, index + 1, offsets[index], len(chunk), len(chunk), zlib.crc32(chunk),
                         first_dependency, len(region["neighbors"]), 0, *region["bounds"][0], *region["bounds"][1])
        first_dependency += len(region["neighbors"])
        region_reports[index]["file_offset"] = offsets[index]
        region_reports[index]["dependencies"] = [region_ids[neighbor] for neighbor in region["neighbors"]]
        result[offsets[index]:offsets[index] + len(chunk)] = chunk
    start = DIRECTORY_HEADER_BYTES + len(regions) * REGION_RECORD_BYTES
    for ident in sorted(entity_ids):
        entity = state["by_id"][ident]
        struct.pack_into(">IIHHI", result, start, entity_ids[ident], region_ids[homes[ident]], ENTITY_KINDS.get(entity["kind"], 0), 0, 0)
        start += ENTITY_RECORD_BYTES
    for ident in dependency_ids:
        struct.pack_into(">I", result, start, ident)
        start += 4
    struct.pack_into(">I", result, 28, zlib.crc32(result[:directory_bytes]))
    overlaps = [dict(region=region["id"], regions=[region["id"]] + region["neighbors"],
                     decoded_chunk_bytes=sum(len(chunks[region_ids[name] - 1]) for name in [region["id"]] + region["neighbors"])) for region in regions]
    peak = max(overlaps, key=lambda row: (row["decoded_chunk_bytes"], row["region"]))
    omitted_models = [ident for ident in sorted(state["by_id"]) if "model" in state["by_id"][ident] and state["by_id"][ident]["kind"] != "static"]
    try:
        from .cook_identity import recipe_sources
    except ImportError:
        from cook_identity import recipe_sources
    report = dict(format="DLM1/DLR1", version=1, cook_version=COOK_VERSION, chunk_version=chunk_version, lighting=lighting,
                  recipe_sources=recipe_sources(ROOT),
                  canonical_partition_sha256=hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest(),
                  visibility=manifest.get("visibility", "door-distance"), experimental=True, source=manifest["source"],
                  source_sha256=source_sha, source_file_sha256=hashlib.sha256(source_raw).hexdigest(),
                  source_file_sha256_basis="canonical_json" if source_document is not None else "saved_bytes", content_identity_sha256=identity.hex(),
                  package_sha256=hashlib.sha256(result).hexdigest(), package_bytes=package_bytes, directory_bytes=directory_bytes,
                  directory_crc32=f"{struct.unpack_from('>I', result, 28)[0]:08x}",
                  entry_region=region_ids[manifest["entry_region"]], regions=region_reports,
                  entity_ids=[dict(id=entity_ids[ident], source_id=ident, kind=state["by_id"][ident]["kind"], home_region=region_ids[homes[ident]]) for ident in sorted(entity_ids)],
                  material_ids=[dict(id=material_ids[ident], source_id=ident) for ident in sorted(material_ids)],
                  surface_ids=[dict(id=index + 1, **row) for index, row in enumerate(surface_rows)],
                  content_dependencies=[dict(path=path, sha256=dependencies[path]) for path in sorted(dependencies)],
                  neighbor_overlap=overlaps, memory=dict(directory_bytes=directory_bytes, decoded_chunk_bytes=sum(map(len, chunks)),
                  peak_neighbor_overlap_chunk_bytes=peak["decoded_chunk_bytes"], peak_neighbor_overlap_regions=peak["regions"],
                  directory_plus_peak_neighbor_chunks_bytes=directory_bytes + peak["decoded_chunk_bytes"],
                  renderer_bytes=None, audio_bytes=None, code_display_buffers_bytes=None, loader_slots_and_scratch_bytes=None,
                  unknown_costs=["renderer caches, packing and textures", "audio", "code and display/depth buffers", "loader slot padding and scratch", "visibility, navigation and richer collision"]),
                  omissions=dict(nonstatic_visual_entities=omitted_models,
                  features=["dynamic model geometry and animation", "material colors in chunk payloads", "gameplay definitions, transforms and scripts", "visibility/PVS, navigation, BVHs and triangle sweeps"] + (["lighting and probes"] if not lighting else ["lighting probes"]) + (["UVs and normals"] if chunk_version == 1 else []),
                  unsupported_inputs=["external-scale-study", "animated character assets", "linked asset packs"]),
                  acceptance="Portable package foundation only; neighbor overlap is authored adjacency, not a proven PVS or an 8 MiB runtime budget.")
    report["working_sets"], report["max_resident_regions"] = working_sets(policy, region_reports, state, homes)
    if policy:
        report["policy_regions"] = [dict(id=region_ids[row["id"]], activation_bounds=row["activation_bounds"],
            working_mask=sum(1 << (region_ids[name]-1) for name in row["working_set"])) for row in regions]
        report["transitions"] = [dict(row, from_region=region_ids[row["from"]], to_region=region_ids[row["to"]]) for row in policy["transitions"]]
    baked_ids = {row["id"] for row in (lighting or {}).get("bootstrap_models", [])}
    door_ids = [row["id"] for row in state["objects"] if row["kind"] == "door"]
    model_entries = []
    for region in region_reports:
        for group in region["groups"]:
            model_entries.append(dict(group_id=group["id"], source_entity_ids=group["source_entity_ids"],
                role="static", region_id=region["id"], region_source_id=region["source_id"],
                material_id=group["material_id"], material_source_id=group["material"],
                environment_enabled=lighting is not None, animated_mesh=False,
                decision_contributors=door_ids if len(door_ids)>1 else [],
                inline_payload=bool(lighting), **diagnostics.lighting_decision(night=lighting is not None,
                    role="static", triangles=group["triangles"], inline_payload=bool(lighting), doors=len(door_ids))))
    for entity in state["models"]:
        if entity["kind"] == "static":
            continue
        model_entries.append(dict(entity_id=entity["id"], source_entity_ids=[entity["id"]], role=entity["kind"],
            region_id=region_ids[homes[entity["id"]]], region_source_id=homes[entity["id"]],
            material_id=material_ids[entity["material"]], material_source_id=entity["material"],
            environment_enabled=lighting is not None, animated_mesh=False,
            decision_contributors=door_ids if len(door_ids)>1 else [],
            inline_payload=entity["id"] in baked_ids,
            **diagnostics.lighting_decision(night=lighting is not None, role=entity["kind"],
                triangles=len(state["meshes"][entity["model"]]["indices"])//3,
                inline_payload=entity["id"] in baked_ids, doors=len(door_ids))))
    report["renderer_diagnostics"] = diagnostics.renderer_report(model_entries, door_ids=door_ids,
        identity=dict(renderer_source_sha256=source_sha, source_sha256=source_sha,
            source_file_sha256=report["source_file_sha256"], source_file_sha256_basis=report["source_file_sha256_basis"],
            package_sha256=report["package_sha256"], build_id=None))
    for working in report["working_sets"]:
        ids = {row["id"] for row in working["contributors"]}
        entries = [row for row in model_entries if row.get("group_id") in {group["id"] for group in working["groups"]}
                   or row.get("entity_id") in ids]
        working["renderer"] = dict(basis="potential_membership_upper_bound", model_entries=entries,
            mode_counts=diagnostics.counts(entries)[0], cases=diagnostics.counts(entries)[1],
            change_mode_counts=diagnostics.counts(entries, changed=True)[0],
            change_cases=diagnostics.counts(entries, changed=True)[1],
            renderer_night_cache_bytes=48 * working["scene_triangles"] if lighting and
                any(row["mode"] != "inline_two_state" for row in entries) else 0)
    report["bridge_diagnostics"] = diagnostics.bridge_report(report)
    meshes = {entity["model"] for entity in state["models"] if entity["kind"] != "static"}
    geometry_bytes = sum(len(state["meshes"][ident]["vertices"])*12 + len(state["meshes"][ident]["indices"])*2 +
        len(state["meshes"][ident].get("uvs", []))*8 + len(state["meshes"][ident].get("normals", []))*3 for ident in meshes)
    slot_bytes = align16(max(row["chunk_bytes"] for row in region_reports)) * report["max_resident_regions"]
    bootstrap_rgba = (lighting or {}).get("bootstrap_embedded_rgba_bytes", 0)
    rgba_banks = 2 * 1440 * 48
    cache_peak = max(row["renderer"]["renderer_night_cache_bytes"] for row in report["working_sets"])
    report["bridge_diagnostics"]["memory"] = dict(basis="cooked_estimate", rom_package_bytes=package_bytes,
        embedded_rgba_in_package_bytes=(lighting or {}).get("mission_embedded_rgba_bytes", 0),
        bootstrap_rgba_resident_bytes=bootstrap_rgba, bootstrap_geometry_resident_bytes=geometry_bytes,
        standalone_lighting_rom_bytes=0, host_worker_cache_bytes=(lighting or {}).get("host_worker_bytes", 0),
        directory_resident_bytes=directory_bytes, slot_buffer_resident_bytes=slot_bytes,
        scene_banks_rgba_resident_bytes=rgba_banks, renderer_night_cache_peak_bytes=cache_peak,
        known_target_rom_bytes=package_bytes + geometry_bytes + bootstrap_rgba,
        known_target_resident_bytes=directory_bytes + slot_bytes + geometry_bytes + bootstrap_rgba + rgba_banks + cache_peak,
        total_target_resident_bytes=None, total_target_rom_bytes=None,
        unknown_target_costs=["ROM metadata, textures, code and audio", "remaining scene banks/coordinator and gameplay structs", "DMA staging and allocator overhead",
            "renderer packed geometry and texture allocations", "audio", "code and display/depth buffers"],
        accounting_note="Package bytes include embedded triangle RGBA; embedded_rgba_in_package_bytes is a subset, never added again. Host worker cache is excluded from target totals. Slot copies, fixed scene-bank RGBA arrays, bootstrap constants and renderer night caches occupy separate target allocations. Known bytes are a subtotal, not an RDRAM certification.")
    report["runtime_debug_map"] = runtime_debug_map(report, state)
    report["runtime_debug_map"].update(renderer_source_sha256=source_sha,
        renderer_diagnostics=report["renderer_diagnostics"], bridge_diagnostics=report["bridge_diagnostics"])
    return bytes(result), report


def mission_source(manifest_path, content_root=None):
    """Resolve the canonical scene without allowing a manifest to escape content."""
    manifest_path = Path(manifest_path).resolve()
    content_root = Path(content_root or manifest_path.parent).resolve()
    level.require(manifest_path.is_relative_to(content_root) and manifest_path.is_file(),
                  "Mission manifest must stay inside content")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # Discovery must keep an editable source accessible while its region data
    # is invalid. The actual cook applies the complete manifest schema gate.
    level.require(isinstance(manifest, dict), "Mission manifest must be an object")
    source = content_path(manifest.get("source"), content_root, {".json"})
    return manifest, source


def bootstrap_header(document, state, package_report, package_path):
    """Emit persistent gameplay/shared visuals, with architecture only in ROM."""
    bootstrap = dict(state)
    bootstrap["models"] = [entity for entity in state["models"] if entity["kind"] != "static"]
    level.require(all(entity["kind"] in level.ROLES for entity in bootstrap["models"]),
                  "Stream bootstrap supports only guard, door, control, loot and objective visuals")
    model_ids = {entity["model"] for entity in bootstrap["models"]}
    bootstrap["meshes"] = {ident: state["meshes"][ident] for ident in sorted(model_ids)}
    bootstrap["colliders"] = []
    bootstrap["floors"] = []
    bootstrap["streamed_source"] = False
    bootstrap["renderer_source_sha256"] = package_report["renderer_diagnostics"]["identity"]["renderer_source_sha256"]
    bootstrap.pop("baked_lighting", None)
    text = level.header(document, bootstrap)
    baked = {row["id"]: row for row in (package_report.get("lighting") or {}).get("bootstrap_models", [])}
    color_arrays = []
    if baked:
        # Colors belong to instances, since door and loot may share one mesh
        # while using different transforms, materials and shadowing.
        model_rows = text.split("static const DlModelInstance dl_demo_models[] = {\n", 1)[1].split("\n};", 1)[0]
        for index, entity in enumerate(bootstrap["models"]):
            row = baked.get(entity["id"])
            if not row:
                continue
            pointers = []
            for side, key in enumerate(("closed_rgba", "open_rgba")):
                name = f"dl_stream_baked_model_{index}_{side}"
                values = ",".join(str(value) for value in bytes.fromhex(row[key]))
                color_arrays.append(f"static const uint8_t {name}[] = {{{values}}};")
                pointers.append(name)
            lines = model_rows.splitlines()
            lines[index] = lines[index].replace("{NULL,NULL}", "{" + ",".join(pointers) + "}")
            model_rows = "\n".join(lines)
        text = re.sub(r"static const DlModelInstance dl_demo_models\[\] = \{\n.*?\n\};",
                      "\n".join(color_arrays) + "\nstatic const DlModelInstance dl_demo_models[] = {\n" + model_rows + "\n};",
                      text, count=1, flags=re.S)
    # The existing helper emits legacy collision declarations even at count=0.
    # The stream bootstrap contains neither those arrays nor static geometry.
    text = re.sub(r"static const DlCollider dl_demo_colliders\[\] = \{\s*\};\n", "", text)
    text = re.sub(r"static const int16_t dl_collider_objects\[\] = \{\s*\};\n", "", text)
    text = text.replace(".colliders=dl_demo_colliders", ".colliders=NULL")
    text = text.replace(".collider_objects=dl_collider_objects", ".collider_objects=NULL")
    text = text.replace('#include "game.h"', '#include "game.h"\n#include "stream_scene.h"')
    entity_ids = {row["source_id"]: row["id"] for row in package_report["entity_ids"]}
    links = []
    for name, rows in (("model", bootstrap["models"]), ("object", state["objects"]), ("enemy", state["enemies"])):
        values = ",".join(str(entity_ids[row["id"]]) for row in rows)
        links.append(f"static const uint32_t dl_stream_{name}_ids[] = {{" + (values or "0") + "};")
    materials = []
    for row in package_report["material_ids"]:
        material = state["materials"][row["source_id"]]
        color = ",".join(str(round(component * 255)) for component in material["color"])
        materials.append("    {" + str(row["id"]) + ",{ " + color + " }," + str(material.get("double_sided", True)).lower() + "," + str(state["texture_indices"].get(row["source_id"], -1) + 1) + "}")
    links.append("static const DlStreamMaterial dl_stream_materials[] = {\n" + ",\n".join(materials) + "\n};")
    if package_report.get("visibility") == "authored-sets":
        def vector(point):
            return "{" + ",".join(level.f(value) for value in point) + "}"
        values = ["    {" + vector(row["activation_bounds"][0]) + "," + vector(row["activation_bounds"][1]) +
                  "," + str(row["working_mask"]) + "u}" for row in package_report["policy_regions"]]
        links.append("static const DlStreamPolicyRegion dl_stream_policy_regions[] = {\n" + ",\n".join(values) + "\n};")
        values = ["    {" + str(row["from_region"]) + "," + str(row["to_region"]) + "," +
                  vector(row["bounds"][0]) + "," + vector(row["bounds"][1]) + "," +
                  str(entity_ids[row["door"]] if row.get("door") else 0) + "}" for row in package_report["transitions"]]
        if values:
            links.append("static const DlStreamTransition dl_stream_transitions[] = {\n" + ",\n".join(values) + "\n};")
    links.append("static const DlStreamDefinition dl_stream_definition = {\n" +
                 "    .package_path=" + json.dumps(package_path) + ",\n" +
                 "    .package_sha256=" + json.dumps(package_report["package_sha256"]) + ",\n" +
                 f"    .directory_bytes={package_report['directory_bytes']}, .file_bytes={package_report['package_bytes']}, " +
                 f".max_region_bytes={max(row['chunk_bytes'] for row in package_report['regions'])}, .region_count={len(package_report['regions'])},\n" +
                 "    .model_entity_ids=dl_stream_model_ids, .object_entity_ids=dl_stream_object_ids, .enemy_entity_ids=dl_stream_enemy_ids,\n" +
                 "    .keep_neighbors_resident=" + str(package_report.get("visibility") == "all-regions").lower() + ",\n" +
                 (f"    .policy=DL_STREAM_POLICY_AUTHORED, .policy_regions=dl_stream_policy_regions, .transitions={'dl_stream_transitions' if package_report['transitions'] else 'NULL'}, .transition_count={len(package_report['transitions'])},\n"
                  if package_report.get("visibility") == "authored-sets" else "") +
                 f"    .max_resident_regions={package_report['max_resident_regions']},\n" +
                 f"    .materials=dl_stream_materials, .material_count={len(materials)}\n" + "};")
    text = text.replace("static const DlLevel dl_demo_level = {", "\n".join(links) + "\nstatic const DlLevel dl_demo_level = {\n    .stream=&dl_stream_definition,")
    meshes = list(bootstrap["meshes"].values())
    geometry_bytes = sum(len(mesh["vertices"]) * 12 + len(mesh["indices"]) * 2 + len(mesh.get("uvs", [])) * 8 + len(mesh.get("normals", [])) * 3 for mesh in meshes)
    return text, dict(compiled_geometry_bytes=geometry_bytes,
                      baked_color_bytes=sum(len(bytes.fromhex(row[key])) for row in baked.values() for key in ("closed_rgba", "open_rgba")),
                      mesh_count=len(meshes), model_count=len(bootstrap["models"]),
                      models=[entity["id"] for entity in bootstrap["models"]],
                      model_entity_ids=[entity_ids[entity["id"]] for entity in bootstrap["models"]],
                      object_entity_ids=[entity_ids[entity["id"]] for entity in state["objects"]],
                      enemy_entity_ids=[entity_ids[entity["id"]] for entity in state["enemies"]],
                      collider_count=0, static_model_count=0,
                      memory_note="compiled_geometry_bytes counts shared dynamic geometry; baked_color_bytes counts resident constant RGBA arrays separately. Descriptors, global gameplay state, stream scene banks, slots and renderer/audio/display costs are additional.")


def validate_stream_bridge(package_report):
    """The first ROM adapter is deliberately smaller than the portable format."""
    diagnostics_report = diagnostics.bridge_report(package_report)
    level.require(diagnostics_report["valid"], diagnostics_report["failures"][0]["message"] if diagnostics_report["failures"] else "Invalid stream bridge")


def stream_play_package(document, state, report, package_path):
    """Desktop bootstrap for the shared bounded loader, never flattened collision.

    Architecture and exact floors are decoded only from the checked DLM bytes.
    The JSON holds the same resident gameplay and dynamic visual definitions as
    bootstrap_header; it also binds replay identity to the entire cooked file.
    """
    validate_stream_bridge(report)
    bootstrap = dict(state, colliders=[], floors=[], streamed_source=False)
    result = level.play_package(document, bootstrap)
    dynamic = [entity for entity in state["models"] if entity["kind"] != "static"]
    baked = {row["id"]: row for row in (report.get("lighting") or {}).get("bootstrap_models", [])}
    mesh_ids = sorted({entity["model"] for entity in dynamic})
    enemy_indices = {row["id"]: index for index, row in enumerate(state["enemies"])}
    result["stream_meshes"] = [dict(id=ident, **{key: state["meshes"][ident][key]
        for key in ("vertices", "indices")}) for ident in mesh_ids]
    result["stream_models"] = [dict(id=entity["id"], mesh=mesh_ids.index(entity["model"]),
        role=entity["kind"], position=entity["transform"]["position"],
        rotation=[math.radians(value) for value in entity["transform"]["rotation"]],
        scale=entity["transform"]["scale"], enemy_index=enemy_indices.get(entity["id"], -1)) for entity in dynamic]
    for model, entity in zip(result["stream_models"], dynamic):
        material = state["materials"][entity["material"]]
        model.update(color=[round(value*255) for value in material["color"]],
            emissive=[round(value*255) for value in material.get("emissive", [0,0,0])],
            double_sided=material.get("double_sided", True))
        if entity["id"] in baked:
            row = baked[entity["id"]]
            expected = len(state["meshes"][entity["model"]]["indices"]) // 3 * 24
            colors = [row["closed_rgba"], row["open_rgba"]]
            level.require(all(len(bytes.fromhex(value)) == expected for value in colors),
                          entity["id"] + ": bootstrap lighting color count mismatch")
            model["baked_colors"] = colors
    entity_ids = {row["source_id"]: row["id"] for row in report["entity_ids"]}
    result["stream"] = dict(package_path=str(package_path), package_sha256=report["package_sha256"],
        renderer_source_sha256=report["renderer_diagnostics"]["identity"]["renderer_source_sha256"],
        directory_bytes=report["directory_bytes"], file_bytes=report["package_bytes"],
        max_region_bytes=max(row["chunk_bytes"] for row in report["regions"]), region_count=len(report["regions"]),
        max_resident_regions=report["max_resident_regions"], visibility=report["visibility"],
        model_entity_ids=[entity_ids[row["id"]] for row in dynamic],
        object_entity_ids=[entity_ids[row["id"]] for row in state["objects"]],
        enemy_entity_ids=[entity_ids[row["id"]] for row in state["enemies"]],
        materials=[dict(id=row["id"], color=[round(value*255) for value in state["materials"][row["source_id"]]["color"]],
            double_sided=state["materials"][row["source_id"]].get("double_sided", True)) for row in report["material_ids"]],
        policy_regions=report.get("policy_regions", []),
        transitions=[dict(from_region=row["from_region"], to_region=row["to_region"], bounds=row["bounds"],
            door_entity_id=entity_ids[row["door"]] if row.get("door") else 0) for row in report.get("transitions", [])],
        runtime_debug_map=report["runtime_debug_map"])
    identity = dict(result)
    identity.pop("fingerprint", None)
    identity["stream"] = {key: value for key, value in result["stream"].items()
                          if key not in ("package_path", "runtime_debug_map")}
    result["fingerprint"] = hashlib.sha256(canonical_json(identity)).hexdigest()
    return result


def prepare_stream_level(manifest_path, output, content_root=None):
    """Build the menu bootstrap and a checked, hash-addressed ROM package."""
    manifest_path, output = Path(manifest_path).resolve(), Path(output).resolve()
    content_root = Path(content_root or manifest_path.parent).resolve()
    manifest, source = mission_source(manifest_path, content_root)
    manifest_raw, source_raw = manifest_path.read_bytes(), source.read_bytes()
    package, package_report = cook_mission(manifest, content_root)
    document = canonical_source(json.loads(source_raw))
    state = level.validate(document, content_root, streaming=True)
    validate_stream_bridge(package_report)
    relative = "romfs/missions/mission-" + package_report["package_sha256"] + ".dlm"
    rom_path = "rom:/missions/" + Path(relative).name
    text, bootstrap = bootstrap_header(document, state, package_report, rom_path)
    # Package identity and compiler inputs enter the build fingerprint. This
    # includes primitive recipes and enemy defaults absent from source JSON.
    recipe_inputs = [Path(__file__), Path(level.__file__)]
    recipe_inputs.extend(path for uri, path in state["dependencies"] if uri.startswith("code:"))
    digest = hashlib.sha256(package)
    digest.update(text.encode("utf-8"))
    for path in sorted(set(recipe_inputs)):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    report = dict(version=2, title=document["title"], source_sha256=digest.hexdigest(),
                  recipe_sources=package_report["recipe_sources"],
                  mission_manifest_sha256=hashlib.sha256(manifest_raw).hexdigest(),
                  authoring_only=False, playable=True, experimental_stream=True,
                  compiled_geometry_bytes=bootstrap["compiled_geometry_bytes"],
                  compiled_baked_color_bytes=bootstrap["baked_color_bytes"],
                  compiled_known_array_bytes=bootstrap["compiled_geometry_bytes"] + bootstrap["baked_color_bytes"], bootstrap=bootstrap,
                  counts=dict(entities=len(state["by_id"]), models=bootstrap["model_count"], meshes=bootstrap["mesh_count"],
                              colliders=0, lights=len(state["kinds"]["light"]), enemies=len(state["enemies"]), test_starts=len(state["test_starts"])),
                  characters={}, lighting=package_report.get("lighting"), test_starts=state["test_starts"],
                  mission=dict(path=relative, sha256=package_report["package_sha256"], bytes=len(package), report=package_report),
                  memory_note="Bootstrap shared visuals/global definitions are resident. Static architecture and all colliders live in mission chunks. No full mission or hardware performance acceptance is implied.")
    texture_report = dict(version=1, textures=state["textures"], **level.texture_memory_summary(state["textures"]))
    report["textures"] = texture_report
    level.require(source.read_bytes() == source_raw and manifest_path.read_bytes() == manifest_raw,
                  "Mission source or manifest changed during preparation; retry")
    for dependency in package_report["content_dependencies"]:
        path = content_path(dependency["path"], content_root, {".obj", ".png", ".jpg", ".jpeg"})
        level.require(hashlib.sha256(path.read_bytes()).hexdigest() == dependency["sha256"],
                      "Mission asset changed during preparation; retry: " + dependency["path"])
    for path, png in state["texture_artifacts"].items():
        atomic_write(output / path, png)
    atomic_write(output / "generated/texture_report.json", (json.dumps(texture_report, indent=2) + "\n").encode())
    atomic_write(output / relative, package)
    play = stream_play_package(document, state, package_report, (output / relative).as_posix())
    atomic_write(output / "generated/play.json", (json.dumps(play, indent=2, allow_nan=False) + "\n").encode())
    atomic_write(output / "generated/demo_level.h", text.encode("utf-8"))
    atomic_write(output / "generated/mission_report.json", (json.dumps(package_report, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    atomic_write(output / "generated/runtime_debug_map.json", (json.dumps(package_report["runtime_debug_map"], indent=2, sort_keys=True) + "\n").encode("utf-8"))
    atomic_write(output / "generated/level_report.json", (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return report


def atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        # Hash-addressed package filenames are already long. Repeating them
        # in a sibling temporary name exceeds Windows MAX_PATH in bundle cooks.
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="dl-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="DLM1 package file; report uses .report.json beside it")
    parser.add_argument("--content-root", type=Path, default=ROOT / "content")
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        package, report = cook_mission(manifest, args.content_root)
        report_path = args.output.with_suffix(".report.json")
        level.require(report_path.resolve() != args.output.resolve(), "Output package must differ from its .report.json path")
        atomic_write(report_path, (json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
        debug_map_path = args.output.with_suffix(".runtime_debug_map.json")
        atomic_write(debug_map_path, (json.dumps(report["runtime_debug_map"], indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
        atomic_write(args.output, package)
        print(json.dumps(dict(package=str(args.output), report=str(report_path), runtime_debug_map=str(debug_map_path),
                              package_bytes=len(package), directory_bytes=report["directory_bytes"])))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        print("Mission cook failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
