#!/usr/bin/env python3
"""Cook a bounded version-2 scene and manual partitions into DLM1/DLR1 bytes.

This experimental format contains static triangles, upright boxes and home IDs.
It does not contain render resources or enough gameplay data to run a mission.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import zlib

try:
    import compile_level as level
    from asset_pack import content_path
except ModuleNotFoundError:
    from tools import compile_level as level
    from tools.asset_pack import content_path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY_HEADER_BYTES = 64
REGION_RECORD_BYTES = 64
ENTITY_RECORD_BYTES = 16
CHUNK_HEADER_BYTES = 32
SECTION_RECORD_BYTES = 24
ENTITY_KINDS = {"guard": 1, "door": 2, "loot": 3, "objective": 4}
RECORD_BYTES = {1: 44, 2: 32, 3: 4}


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


def fields(value, required, label):
    level.require(isinstance(value, dict), label + ": expected an object")
    level.require(set(value) == set(required), label + ": missing or unknown fields")


def read_source(manifest, content_root):
    path = content_path(manifest["source"], content_root, {".json"})
    level.require(path.stat().st_size <= 8 * 1024 * 1024, "Mission source exceeds 8 MiB")
    raw = path.read_bytes()
    document = json.loads(raw)
    # Run the profile gate before unsupported-feature checks or asset reads.
    level.content_limits(document, level.LIMITS, authoring=False)
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
        level.require("texture" not in material, "DLM1 v1 does not support textures or texture dependencies")
    state = level.validate(document, content_root, authoring=False)
    return document, state, raw, dependencies


def validated_regions(manifest, state):
    regions = manifest["regions"]
    level.require(isinstance(regions, list) and 1 <= len(regions) <= 256, "regions: expected 1..256 manual regions")
    by_id, homes = {}, {}
    for region in regions:
        fields(region, {"id", "bounds", "entities", "neighbors"}, "region")
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
    level.require(set(homes) == set(state["by_id"]), "Every source entity must have exactly one home region; missing: " + ", ".join(sorted(set(state["by_id"]) - set(homes))))
    level.require(isinstance(manifest["entry_region"], str) and manifest["entry_region"] in by_id, "entry_region: unknown region")
    for region in by_id.values():
        level.require(all(neighbor in by_id for neighbor in region["neighbors"]), region["id"] + ": unknown neighbor reference")
    # A home is ownership, not necessarily a disjoint geometric volume.
    for entity, home in homes.items():
        assert_bounds(by_id[home], [state["by_id"][entity]["transform"]["position"]], entity + " position")
    return [by_id[ident] for ident in sorted(by_id)], homes


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


def make_chunk(origin, sections):
    """Section tuples are (kind, payload, count); empty sections are omitted."""
    sections = sorted((row for row in sections if row[2]), key=lambda row: row[0])
    level.require(1 <= len(sections) <= 3, "Region requires at least one section")
    cursor = align16(CHUNK_HEADER_BYTES + SECTION_RECORD_BYTES * len(sections))
    descriptors, payloads = [], []
    for kind, payload, count in sections:
        level.require(len(payload) == count * RECORD_BYTES[kind], "Internal section length mismatch")
        descriptors.append(struct.pack(">HHIIIII", kind, 0, cursor, len(payload), count, RECORD_BYTES[kind], 0))
        payloads.append((cursor, payload))
        cursor = align16(cursor + len(payload))
    # Trailing padding is unnecessary; inter-section padding is deterministic.
    size = payloads[-1][0] + len(payloads[-1][1])
    result = bytearray(size)
    struct.pack_into(">4sHHII3fI", result, 0, b"DLR1", 1, CHUNK_HEADER_BYTES, size, len(sections), *origin, 0)
    for index, descriptor in enumerate(descriptors):
        start = CHUNK_HEADER_BYTES + index * SECTION_RECORD_BYTES
        result[start:start + SECTION_RECORD_BYTES] = descriptor
    for start, payload in payloads:
        result[start:start + len(payload)] = payload
    return bytes(result)


def cook_mission(manifest, content_root=ROOT / "content"):
    """Validate all inputs before returning reproducible package bytes/report."""
    fields(manifest, {"version", "source", "entry_region", "regions"}, "Mission manifest")
    level.require(type(manifest["version"]) is int and manifest["version"] == 1, "Mission manifest version must be 1")
    content_root = Path(content_root).resolve()
    document, state, source_raw, dependencies = read_source(manifest, content_root)
    regions, homes = validated_regions(manifest, state)
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
    chunks, region_reports = [], []
    for region in regions:
        origin = [(a + b) / 2 for a, b in zip(*region["bounds"])]
        triangle_payload, collider_payload = bytearray(), bytearray()
        for ident in region["entities"]:
            entity = state["by_id"][ident]
            if entity["kind"] == "static" and "model" in entity:
                mesh = state["meshes"][entity["model"]]
                points = [level.transform_point(point, entity["transform"]) for point in mesh["vertices"]]
                assert_bounds(region, points, ident + " static geometry")
                for triangle in range(len(mesh["indices"]) // 3):
                    positions = [points[index] for index in mesh["indices"][triangle * 3:triangle * 3 + 3]]
                    local = [value - origin[axis] for point in positions for axis, value in enumerate(point)]
                    triangle_payload.extend(struct.pack(">9fII", *local, material_ids[entity["material"]], surface_ids[(ident, triangle)]))
            if ident in boxes:
                box = boxes[ident]
                assert_bounds(region, collider_corners(box), ident + " collider")
                local = [box["center"][axis] - origin[axis] for axis in range(3)]
                collider_payload.extend(struct.pack(">7fI", *local, *box["half_size"], box["yaw"], entity_ids[ident]))
        entity_payload = b"".join(struct.pack(">I", entity_ids[ident]) for ident in region["entities"])
        chunk = make_chunk(origin, [(1, triangle_payload, len(triangle_payload) // 44),
                                    (2, collider_payload, len(collider_payload) // 32),
                                    (3, entity_payload, len(region["entities"]))])
        chunks.append(chunk)
        region_reports.append(dict(id=region_ids[region["id"]], source_id=region["id"], bounds=region["bounds"], origin=origin,
                                   triangle_count=len(triangle_payload) // 44, collider_count=len(collider_payload) // 32,
                                   entity_count=len(region["entities"]), chunk_bytes=len(chunk), crc32=f"{zlib.crc32(chunk):08x}"))
    canonical_manifest = dict(manifest, regions=regions)
    source_sha = hashlib.sha256(canonical_json(canonical_source(document))).hexdigest()
    identity = hashlib.sha256(canonical_json(dict(format="DLM1/DLR1-v1", source_sha256=source_sha,
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
    report = dict(format="DLM1/DLR1", version=1, experimental=True, source=manifest["source"],
                  source_sha256=source_sha, source_file_sha256=hashlib.sha256(source_raw).hexdigest(), content_identity_sha256=identity.hex(),
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
                  features=["dynamic model geometry and animation", "material colors, UVs and normals", "lighting and probes", "gameplay definitions, transforms and scripts", "visibility/PVS, navigation, BVHs and triangle sweeps"],
                  unsupported_inputs=["external-scale-study", "animated character assets", "textures", "linked asset packs"]),
                  acceptance="Portable package foundation only; neighbor overlap is authored adjacency, not a proven PVS or an 8 MiB runtime budget.")
    return bytes(result), report


def atomic_write(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
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
        atomic_write(args.output, package)
        print(json.dumps(dict(package=str(args.output), report=str(report_path), package_bytes=len(package), directory_bytes=report["directory_bytes"])))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Mission cook failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
