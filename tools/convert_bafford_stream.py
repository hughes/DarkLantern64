#!/usr/bin/env python3
"""Derive two editable stream regions from the bounded local Bafford entry.

Commercial geometry/textures stay in ignored content. This preserves the entry
scene's present coverage and omissions; it does not convert the complete mission.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
if not __package__:
    sys.path.insert(0, str(ROOT))
from tools import compile_level as level
from tools.project_levels import ProjectLevels, json_bytes

EPSILON = 1e-6
BOUND_PADDING = 1e-4
SIDES = ("west", "east")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def normalized(vector):
    length = math.sqrt(sum(component * component for component in vector))
    level.require(length > 1e-12, "Cannot preserve a zero normal")
    return [component / length for component in vector]


def interpolate(a, b, seam):
    t = (seam - a["position"][0]) / (b["position"][0] - a["position"][0])
    result = {key: [x + t * (y - x) for x, y in zip(a[key], b[key])] for key in a}
    result["position"][0] = seam
    result["normal"] = normalized(result["normal"])
    return result


def area_squared(triangle):
    a, b, c = [corner["position"] for corner in triangle]
    u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
    return sum(x * x for x in (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]))


def clip_triangle(triangle, seam, side):
    """Clip evaluated corners; source UV orientation is already image-down."""
    level.require(side in SIDES, "Unknown clip side")
    polygon = copy.deepcopy(triangle)
    for corner in polygon:
        if abs(corner["position"][0] - seam) <= EPSILON:
            corner["position"][0] = seam
    # Coplanar surfaces have one owner rather than two overlapping copies.
    if all(corner["position"][0] == seam for corner in polygon) and side == "east":
        return []
    inside = lambda corner: corner["position"][0] <= seam if side == "west" else corner["position"][0] >= seam
    result = []
    for a, b in zip(polygon, polygon[1:] + polygon[:1]):
        if inside(a):
            result.append(a)
        if inside(a) != inside(b):
            result.append(interpolate(a, b, seam))
    unique = []
    for corner in result:
        if not unique or any(abs(a - b) > 1e-10 for a, b in zip(corner["position"], unique[-1]["position"])):
            unique.append(corner)
    if len(unique) > 1 and all(abs(a - b) <= 1e-10 for a, b in zip(unique[0]["position"], unique[-1]["position"])):
        unique.pop()
    return [piece for index in range(1, len(unique) - 1)
            if area_squared(piece := [unique[0], unique[index], unique[index + 1]]) > 1e-12]


def box_corners(box):
    c, s = math.cos(box["yaw"]), math.sin(box["yaw"])
    x, y, z = box["center"]
    hx, hy, hz = box["half_size"]
    return [[x + c * dx + s * dz, y + dy, z - s * dx + c * dz]
            for dx in (-hx, hx) for dy in (-hy, hy) for dz in (-hz, hz)]


def clip_box(box, seam):
    """Return world boxes by side; arbitrary yaw can be retained off the seam."""
    xs = [point[0] for point in box_corners(box)]
    lo, hi = min(xs), max(xs)
    if abs(lo - seam) <= EPSILON:
        lo = seam
    if abs(hi - seam) <= EPSILON:
        hi = seam
    if hi <= seam:
        return {"west": copy.deepcopy(box)}
    if lo >= seam:
        return {"east": copy.deepcopy(box)}
    level.require(abs(math.remainder(box["yaw"], 2 * math.pi)) <= 1e-8,
                  box["id"] + ": rotated collider crosses the seam; choose another seam or author axis-aligned proxies")
    result = {}
    for side, start, end in (("west", lo, seam), ("east", seam, hi)):
        half = (end - start) * .5
        level.require(half >= .001, box["id"] + ": seam creates a collider thinner than the authoring minimum")
        part = copy.deepcopy(box)
        part["center"][0], part["half_size"][0] = (start + end) * .5, half
        result[side] = part
    return result


def derived_id(ident, side):
    value = ident + "-" + side
    if len(value) > 64:
        value = ident[:64 - len(side) - 10] + "-" + digest(ident.encode())[:8] + "-" + side
    return value


def obj_payload(triangles, anchor):
    lines = ["# Local editable Bafford stream geometry; source provenance is in canonical JSON."]
    textured = "uv" in triangles[0][0]
    for triangle in triangles:
        for corner in triangle:
            lines.append("v " + " ".join(format(corner["position"][i] - anchor[i], ".12g") for i in range(3)))
            if textured:
                lines.append("vt " + " ".join(format(v, ".12g") for v in (corner["uv"][0], 1 - corner["uv"][1])))
            lines.append("vn " + " ".join(format(v, ".12g") for v in corner["normal"]))
    for index in range(len(triangles)):
        corners = [index * 3 + offset + 1 for offset in range(3)]
        lines.append("f " + " ".join(f"{corner}/{corner}/{corner}" if textured else f"{corner}//{corner}" for corner in corners))
    return ("\n".join(lines) + "\n").encode("utf-8")


def derive(document, state, source_name, source_sha256, seam=-3.048):
    """Pure geometry conversion; returns source, partition, OBJ bytes and report."""
    level.number(seam, "seam_x", -1024, 1024)
    groups, proxies, markers = {}, {side: [] for side in SIDES}, {side: [] for side in SIDES}
    source_counts = {"triangles": 0, "colliders": 0, "crossing_triangles": 0, "crossing_colliders": 0}
    for ident in sorted(state["by_id"]):
        entity = state["by_id"][ident]
        if entity["kind"] != "static":
            level.require(entity["kind"] in ("spawn", "light"), ident + ": entry converter supports spawn/lights; authored dynamic gameplay needs explicit shared-asset conversion")
            side = "west" if entity["transform"]["position"][0] <= seam else "east"
            markers[side].append(copy.deepcopy(entity))
            continue
        if "model" in entity:
            mesh = state["meshes"][entity["model"]]
            t = entity["transform"]
            positions = [level.transform_point(point, t) for point in mesh["vertices"]]
            normals = [normalized(level.rotate([normal[i] / t["scale"][i] for i in range(3)], t["rotation"])) for normal in mesh.get("normals", [])]
            for index in range(len(mesh["indices"]) // 3):
                indices = mesh["indices"][index * 3:index * 3 + 3]
                a, b, c = [positions[i] for i in indices]
                u, v = [b[i] - a[i] for i in range(3)], [c[i] - a[i] for i in range(3)]
                flat = normalized([u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]])
                triangle = [dict(position=positions[i], normal=normals[i] if normals else flat,
                                 **({"uv": mesh["uvs"][i]} if "uvs" in mesh else {})) for i in indices]
                source_counts["triangles"] += 1
                if min(point[0] for point in (a, b, c)) < seam - EPSILON and max(point[0] for point in (a, b, c)) > seam + EPSILON:
                    source_counts["crossing_triangles"] += 1
                for side in SIDES:
                    group = groups.setdefault((side, entity["material"]), {"triangles": [], "provenance": [], "sources": {}})
                    for part, clipped in enumerate(clip_triangle(triangle, seam, side)):
                        group["triangles"].append(clipped)
                        provenance = dict(source_entity_id=ident, source_model_id=entity["model"], source_triangle=index, part=part)
                        provenance.update(state.get("triangle_provenance", {}).get((ident, index), {}))
                        group["provenance"].append(provenance)
                        group["sources"][ident] = copy.deepcopy(entity.get("source", {}))
        if "collider" in entity:
            source_counts["colliders"] += 1
            parts = clip_box(level.collider(entity), seam)
            source_counts["crossing_colliders"] += len(parts) - 1
            for side, box in parts.items():
                provenance = copy.deepcopy(entity.get("source", {}))
                provenance["stream_conversion"] = dict(source_entity_id=ident, source_transform=entity["transform"], source_collider=entity["collider"], side=side, seam_x=seam)
                proxies[side].append(dict(id=derived_id(ident, side), kind="static",
                                         transform=dict(position=box["center"], rotation=[0, math.degrees(box["yaw"]), 0], scale=[1, 1, 1]),
                                         collider=dict(shape="box", center=[0, 0, 0], half_size=box["half_size"]), source=provenance))
    groups = {key: value for key, value in groups.items() if value["triangles"]}
    bounds, anchors = {}, {}
    for side in SIDES:
        points = [corner["position"] for (owner, _), group in groups.items() if owner == side for triangle in group["triangles"] for corner in triangle]
        points += [point for proxy in proxies[side] for point in box_corners(level.collider(proxy))]
        points += [entity["transform"]["position"] for entity in markers[side]]
        points += [[entity["transform"]["position"][0], entity["transform"]["position"][1] + 1.65, entity["transform"]["position"][2]]
                   for entity in markers[side] if entity["kind"] == "spawn"]
        for start in document.get("test_starts", []):
            owner = "west" if start["position"][0] <= seam else "east"
            if owner == side:
                points += [start["position"], [start["position"][0], start["position"][1] + 1.65, start["position"][2]]]
        level.require(points and proxies[side], side + ": conversion needs geometry/collision coverage in both regions")
        # Ownership clips at the exact plane. Authoring bounds need a small
        # overlap there for OBJ anchor round trips and target float rounding.
        minimum = [min(point[i] for point in points) - BOUND_PADDING for i in range(3)]
        maximum = [max(point[i] for point in points) + BOUND_PADDING for i in range(3)]
        # Region membership follows feet, while camera/body queries need head
        # clearance even when a region contains only a floor and no markers.
        maximum[1] += 1.65
        if side == "west":
            maximum[0] = seam + BOUND_PADDING
        else:
            minimum[0] = seam - BOUND_PADDING
        bounds[side] = [minimum, maximum]
        anchors[side] = [(a + b) * .5 for a, b in zip(minimum, maximum)]
    entities, assets, payloads, home_ids = [], [], {}, {side: [] for side in SIDES}
    for (side, material), group in sorted(groups.items()):
        ident = "bafford-stream-" + side + "-" + digest(material.encode())[:16]
        model = "mesh-" + ident
        payload = obj_payload(group["triangles"], anchors[side])
        uri = "assets/thief_bafford/models/stream-" + side + "-" + digest(material.encode())[:16] + "-" + digest(payload)[:16] + ".obj"
        payloads[uri] = payload
        assets.append(dict(id=model, uri=uri))
        entities.append(dict(id=ident, kind="static", model=model, material=material,
                             transform=dict(position=anchors[side], rotation=[0, 0, 0], scale=[1, 1, 1]),
                             source=dict(converter="bafford-stream-v1", source_scene=source_name, region=side,
                                         sources=[dict(id=key, metadata=group["sources"][key]) for key in sorted(group["sources"])],
                                         triangle_provenance=group["provenance"])))
        home_ids[side].append(ident)
    for side in SIDES:
        entities += proxies[side] + markers[side]
        home_ids[side] += [entity["id"] for entity in proxies[side] + markers[side]]
    used = {material for _, material in groups}
    result = {key: copy.deepcopy(document[key]) for key in ("version", "environment", "test_starts", "views") if key in document}
    result.update(title="Bafford Street Streaming", assets=assets,
                  materials=[copy.deepcopy(state["materials"][ident]) for ident in sorted(used)], entities=sorted(entities, key=lambda entity: entity["id"]))
    result["stream_conversion"] = dict(version=1, source_scene=source_name, source_sha256=source_sha256, seam_x=seam,
                                     bounds_padding=BOUND_PADDING,
                                     coordinates="Region-local OBJ vertices plus canonical anchor preserve evaluated world geometry",
                                     coverage="Complete current entry geometry; original omitted collision and gameplay remain omitted")
    spawn = next(entity for entity in entities if entity["kind"] == "spawn")
    entry_region = "west" if spawn["transform"]["position"][0] <= seam else "east"
    manifest = dict(version=1, source="", entry_region=entry_region, visibility="all-regions",
                    regions=[dict(id=side, bounds=bounds[side], entities=sorted(home_ids[side]), neighbors=[other for other in SIDES if other != side]) for side in SIDES])
    triangles = sum(len(group["triangles"]) for group in groups.values())
    level.require(triangles <= 1024, "Derived entry exceeds the 1024-triangle conversion budget")
    level.require(len(groups) <= 64, "Derived entry exceeds 64 region/material groups")
    level.require(len(entities) <= 256, "Derived entry exceeds 256 home entities")
    level.require(all(len(proxies[side]) <= 255 for side in SIDES), "Derived entry exceeds 255 colliders per region")
    level.require(sum(len(values) for values in proxies.values()) <= 256,
                  "Derived entry exceeds 256 total colliders when both regions are resident")
    report = dict(version=1, source_scene=source_name, source_sha256=source_sha256, seam_x=seam, bounds_padding=BOUND_PADDING, source_counts=source_counts,
                  output_counts=dict(triangles=triangles, colliders=sum(len(values) for values in proxies.values()), entities=len(entities),
                                     architecture_models=len(groups), materials=len(used), lights=sum(entity["kind"] == "light" for entity in entities)),
                  regions=[dict(id=side, bounds=bounds[side], anchor=anchors[side], entities=len(home_ids[side]),
                                triangles=sum(len(group["triangles"]) for (owner, _), group in groups.items() if owner == side),
                                colliders=len(proxies[side]), material_groups=sum(owner == side for owner, _ in groups)) for side in SIDES],
                  omissions=["Source entry already omits collision on slopes/nonrectangular surfaces and omitted cells remain outside its scope",
                             "No imported guard, loot, source door, swimming, ladder, trapdoor or original mission-script behavior is added",
                             "Source cell/polygon metadata is provenance; this clip does not rebuild source portals/BSP/navigation",
                             "Near-degenerate clipped fragments below the existing OBJ reader area threshold are discarded"],
                  acceptance="Bounded two-region authoring conversion; target cooking/traversal/performance require separate verification")
    return result, manifest, payloads, report


def convert(source, output, manifest_path=None, *, content_root=ROOT / "content", seam=-3.048, replace=False):
    content_root = Path(content_root).resolve()
    source, output = Path(source).resolve(), Path(output).resolve()
    manifest_path = Path(manifest_path).resolve() if manifest_path else output.with_name(output.stem + ".mission.json")
    level.require(source.parent == content_root and output.parent == content_root and manifest_path.parent == content_root,
                  "Source, output and manifest must be directly inside the content root")
    level.require(source not in (output, manifest_path) and output != manifest_path, "Conversion must leave its original source untouched")
    project = ProjectLevels(content_root.parent)
    with project.lock():
        project.safe(source)
        existing = {path: path.read_bytes() if path.exists() else None for path in (output, manifest_path)}
        level.require(replace or not any(raw is not None for raw in existing.values()),
                      "Derived source/manifest already exist; keep artist edits or pass --replace explicitly")
        raw = source.read_bytes()
        document = json.loads(raw)
        # The first pass discovers inputs. The second pass below evaluates only
        # the captured content bytes, avoiding meshes from one edit and packs
        # or textures from another. A changed pack introducing an uncaptured
        # dependency fails validation in the closed overlay rather than reading
        # an original file opportunistically.
        discovered = level.validate(document, content_root, authoring=False)
        dependencies = {}
        dependency_paths = {}
        for uri, path in sorted(discovered["dependencies"]):
            path = Path(path).resolve()
            if uri in dependencies:
                level.require(dependency_paths[uri] == path, "Dependency URI has inconsistent source paths")
                continue
            level.require(path.is_file(), "Missing conversion dependency: " + uri)
            if not uri.startswith("code:"):
                level.require(not Path(uri).is_absolute() and ".." not in Path(uri).parts and ":" not in uri and "\\" not in uri,
                              "Conversion dependency requires a content-relative URI: " + uri)
                level.require(path.is_relative_to(content_root), "Conversion dependency escapes the content root")
                level.require(project.safe(content_root / uri) == path, "Conversion dependency path changed while resolving: " + uri)
            dependencies[uri] = path.read_bytes()
            dependency_paths[uri] = path
        changes, external_expected = {}, {}
        expected = {source: raw, **existing}
        for uri, payload in dependencies.items():
            path = dependency_paths[uri]
            if path.is_relative_to(project.root):
                expected[project.safe(path)] = payload
            else:
                # Synthetic fixture roots do not contain the shared compiler's
                # code inputs. Bind them too without passing an external path
                # to the project transaction API.
                external_expected[path] = payload
        staging = project.safe(project.editor / "conversion-staging")
        staging.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="bafford-", dir=staging) as temporary:
            source_overlay = Path(temporary) / "source-content"
            source_overlay.mkdir()
            for uri, payload in dependencies.items():
                if uri.startswith("code:"):
                    continue
                target = source_overlay / uri
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            state = level.validate(document, source_overlay, authoring=False)
            level.require({uri for uri, _ in state["dependencies"]} == set(dependencies),
                          "Source dependency set changed during conversion; refresh and retry")
            result, manifest, payloads, report = derive(document, state, source.name, digest(raw), seam)
            manifest["source"] = output.name
            dependency_hashes = {uri: digest(payload) for uri, payload in dependencies.items()}
            report["input_dependencies"] = [dict(uri=uri, sha256=dependency_hashes[uri]) for uri in sorted(dependencies)]
            result["stream_conversion"]["input_dependencies"] = copy.deepcopy(report["input_dependencies"])
            overlay = Path(temporary) / "derived-content"
            for uri, payload in payloads.items():
                target = project.safe(content_root / uri)
                prior = target.read_bytes() if target.exists() else None
                level.require(prior is None or prior == payload, "Immutable converted mesh differs from its content-addressed payload")
                expected[target] = prior
                if prior is None:
                    changes[target] = payload
                target = overlay / uri
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            for material in result["materials"]:
                if "texture" not in material:
                    continue
                uri = material["texture"]["uri"]
                target = overlay / uri
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(dependencies[uri])
            level.validate(result, overlay, authoring=False)
        report_path = project.safe(project.root / "build/bafford-stream-conversion/report.json")
        if any(raw is not None for raw in existing.values()):
            recovery = project.safe(project.editor / "replaced-streams" / uuid.uuid4().hex)
            for path, previous in existing.items():
                if previous is not None:
                    changes[recovery / path.name] = previous
        changes.update({output: json_bytes(result), manifest_path: json_bytes(manifest), report_path: json_bytes(report)})
        for path, previous in external_expected.items():
            level.require(path.read_bytes() == previous, "Compiler files changed during this operation; refresh and retry")
        project.publish(changes, expected)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "content/thief_bafford_entry.json")
    parser.add_argument("--output", type=Path, default=ROOT / "content/thief_bafford_stream.json")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--content-root", type=Path, default=ROOT / "content")
    parser.add_argument("--seam-x", type=float, default=-3.048)
    parser.add_argument("--replace", action="store_true", help="Explicitly replace derived artist edits, retaining source/manifest recovery copies")
    args = parser.parse_args(argv)
    try:
        report = convert(args.source, args.output, args.manifest, content_root=args.content_root, seam=args.seam_x, replace=args.replace)
        print(json.dumps({key: report[key] for key in ("source_sha256", "output_counts", "regions", "acceptance")}, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Bafford stream conversion failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
