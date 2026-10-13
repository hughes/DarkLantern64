#!/usr/bin/env python3
"""Preserve the edited Bafford entry and add a bounded source-cell street route.

Commercial inputs/outputs remain local ignored content. Full source annotations
are checked against current mesh triangles and source cell planes before use.
"""
from __future__ import annotations

import argparse
import collections
import copy
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
from tools import convert_bafford_stream as converter
from tools.asset_pack import content_path
from tools.project_levels import ProjectLevels, json_bytes
from tools.thief_import.geometry import METRES_PER_UNIT, polygon_box

DEFAULT_CELLS = (3831, 4360, 4366, 4987)
PLANE_EPSILON = 1e-4  # Source units: about 0.00003048 metres.


def semantic(metadata):
    """Keep physical material meanings distinct even if visuals differ."""
    material = metadata.get("material", {})
    return material.get("surface"), material.get("tags", "")


def box_record(entity, scene):
    box = level.collider(entity)
    return dict(lo=[box["center"][i] - box["half_size"][i] for i in range(3)],
                hi=[box["center"][i] + box["half_size"][i] for i in range(3)],
                yaw=box["yaw"], original=copy.deepcopy(entity), semantic=semantic(entity.get("source", {})),
                sources=[dict(scene=scene, entity_id=entity["id"], metadata=copy.deepcopy(entity.get("source", {})),
                              transform=copy.deepcopy(entity["transform"]), collider=copy.deepcopy(entity["collider"]))])


def sort_box(box):
    return tuple(box["lo"] + box["hi"]) + (json.dumps(box["semantic"]),
                                          tuple(sorted(source["scene"] + ":" + source["entity_id"] for source in box["sources"])))


def merge_boxes(original):
    """Exact solid union only: containment or equal slabs plus touching intervals.

    No endpoint tolerance, rounding, hull approximation or gap filling is used.
    Arbitrarily rotated boxes are retained individually. Provenance accumulates
    every input proxy, and physical material semantics must match.
    """
    boxes = [copy.deepcopy(box) for box in original if box["yaw"] == 0]
    fixed = [copy.deepcopy(box) for box in original if box["yaw"] != 0]
    stats = dict(input=len(original), containment=0, interval_union=0, rotated_retained=len(fixed))
    changed = True
    while changed:
        changed = False
        kept = []
        for box in sorted(boxes, key=lambda b: (-math.prod(b["hi"][i] - b["lo"][i] for i in range(3)), sort_box(b))):
            parent = next((candidate for candidate in kept if candidate["semantic"] == box["semantic"] and
                           all(candidate["lo"][i] <= box["lo"][i] and candidate["hi"][i] >= box["hi"][i] for i in range(3))), None)
            if parent is None:
                kept.append(box)
            else:
                parent["sources"] += box["sources"]
                stats["containment"] += 1
                changed = True
        boxes = kept
        for axis in range(3):
            groups = collections.defaultdict(list)
            for box in boxes:
                key = (box["semantic"], tuple(box[end][i] for end in ("lo", "hi") for i in range(3) if i != axis))
                groups[key].append(box)
            boxes = []
            for key in sorted(groups, key=repr):
                rows = sorted(groups[key], key=lambda b: (b["lo"][axis], sort_box(b)))
                current = rows[0]
                for box in rows[1:]:
                    if box["lo"][axis] <= current["hi"][axis]:
                        current["lo"][axis] = min(current["lo"][axis], box["lo"][axis])
                        current["hi"][axis] = max(current["hi"][axis], box["hi"][axis])
                        current["sources"] += box["sources"]
                        stats["interval_union"] += 1
                        changed = True
                    else:
                        boxes.append(current)
                        current = box
                boxes.append(current)
    boxes = sorted(boxes + fixed, key=sort_box)
    stats["output"] = len(boxes)
    return boxes, stats


def proxy_entities(boxes):
    rows = []
    for box in boxes:
        if box["yaw"] != 0:
            rows.append(copy.deepcopy(box["original"]))
            continue
        sources = sorted(box["sources"], key=lambda source: (source["scene"], source["entity_id"]))
        identity = json_bytes(dict(bounds=[box["lo"], box["hi"]], sources=[(s["scene"], s["entity_id"]) for s in sources]))
        rows.append(dict(id="bafford-expanded-collision-" + converter.digest(identity)[:20], kind="static",
                         transform=dict(position=[(a + b) * .5 for a, b in zip(box["lo"], box["hi"])], rotation=[0, 0, 0], scale=[1, 1, 1]),
                         collider=dict(shape="box", center=[0, 0, 0], half_size=[(b - a) * .5 for a, b in zip(box["lo"], box["hi"])]),
                         source=dict(game="Thief Gold", merger="exact-axis-interval-union-v1",
                                     material=dict(surface=box["semantic"][0], tags=box["semantic"][1]), sources=sources)))
    return rows


def source_cells(entry):
    return {cell for entity in entry["entities"] for cell in entity.get("source", {}).get("cells", [])}


def check_triangle_cell(points, cell, label):
    raw = [[point[0] / METRES_PER_UNIT, -point[2] / METRES_PER_UNIT, point[1] / METRES_PER_UNIT] for point in points]
    planes = cell.get("planes", [])
    level.require(planes, label + ": source cell has no containment planes")
    distances = [[sum(a * b for a, b in zip(plane["normal"], point)) + plane["distance"] for point in raw] for plane in planes]
    level.require(all(value >= -PLANE_EPSILON for row in distances for value in row),
                  label + ": triangle leaves annotated source cell; topology must be reconciled after editing")
    level.require(any(all(abs(value) <= PLANE_EPSILON for value in row) for row in distances),
                  label + ": triangle is not on an annotated cell boundary")


def select_new_geometry(full, annotations, topology, selected, asset_root, entry_materials, *, full_scene="thief_bafford.json"):
    """Evaluate only requested full-study static surfaces, never cook the study."""
    level.require(full.get("version") == 2, "Full study requires canonical version 2")
    cells = {cell["id"]: cell for cell in topology["cells"]}
    level.require(len(cells) == len(topology["cells"]), "Duplicate source topology cell ID")
    level.require(set(selected) <= set(cells), "Unknown requested source cell")
    assets = {asset["id"]: asset for asset in full["assets"]}
    materials = {material["id"]: copy.deepcopy(material) for material in full["materials"]}
    materials.update(copy.deepcopy(entry_materials))  # Authored entry material edits win.
    entities, meshes, provenance, faces = [], {}, {}, collections.defaultdict(list)
    per_cell = collections.Counter()
    for entity in sorted(full["entities"], key=lambda row: row["id"]):
        if entity["kind"] != "static" or "model" not in entity or not (set(selected) & set(entity.get("source", {}).get("cells", []))):
            continue
        model = entity["model"]
        level.require(model in assets and model in annotations, entity["id"] + ": missing mesh/triangle annotations")
        uri = assets[model].get("uri")
        path = content_path(uri, asset_root, {".obj"})
        mesh = level.read_obj(path, textured="texture" in materials[entity["material"]])
        records = annotations[model]
        level.require(len(records) == len(mesh["indices"]) // 3, model + ": triangle annotation count changed after mesh editing")
        level.require("normals" in mesh, model + ": source normals are required for polygon provenance")
        chosen = dict(vertices=[], normals=[], indices=[], **({"uvs": []} if "uvs" in mesh else {}))
        chosen_records = []
        for triangle, record in enumerate(records):
            level.require(isinstance(record, list) and len(record) == 2 and all(type(value) is int and value >= 0 for value in record),
                          model + ": invalid triangle cell/polygon annotation")
            cid, polygon = record
            if cid not in selected:
                continue
            level.require(cid in entity.get("source", {}).get("cells", []), model + ": annotation differs from source entity cells")
            corners = mesh["indices"][triangle * 3:triangle * 3 + 3]
            points = [level.transform_point(mesh["vertices"][index], entity["transform"]) for index in corners]
            check_triangle_cell(points, cells[cid], model + f" triangle {triangle}")
            normal = converter.normalized(level.rotate([mesh["normals"][corners[0]][i] / entity["transform"]["scale"][i] for i in range(3)], entity["transform"]["rotation"]))
            faces[(cid, polygon)].append(dict(points=points, normal=normal, material=entity["material"]))
            out_index = len(chosen["indices"]) // 3
            provenance[(entity["id"], out_index)] = dict(source_scene=full_scene, source_entity_id=entity["id"],
                                                       source_model_id=model, source_triangle=triangle, cell=cid, polygon=polygon)
            chosen_records.append(dict(source_triangle=triangle, cell=cid, polygon=polygon))
            per_cell[cid] += 1
            for index in corners:
                chosen["indices"].append(len(chosen["vertices"]))
                for field in ("vertices", "normals", "uvs"):
                    if field in chosen:
                        chosen[field].append(copy.deepcopy(mesh[field][index]))
        if chosen["indices"]:
            new_model = model + "-expanded"
            level.identifier(new_model, "expanded model ID")
            meshes[new_model] = chosen
            row = copy.deepcopy(entity)
            row.pop("collider", None)
            row["model"] = new_model
            row["source"]["cells"] = sorted({record["cell"] for record in chosen_records})
            row["source"]["stream_expansion"] = dict(source_model=model, uri=uri, triangles=chosen_records)
            entities.append(row)
    level.require(all(per_cell[cid] for cid in selected), "Requested source cell has no annotated static geometry")
    proxies, omissions = [], collections.Counter()
    for (cid, polygon), rows in sorted(faces.items()):
        level.require(all(row["material"] == rows[0]["material"] for row in rows), "Source polygon has inconsistent materials")
        level.require(all(row["points"][0] == rows[0]["points"][0] and row["points"][1] == rows[index - 1]["points"][2]
                          for index, row in enumerate(rows) if index),
                      f"Cell {cid} polygon {polygon}: edited triangulation invalidates source polygon fan")
        points = rows[0]["points"] + [row["points"][2] for row in rows[1:]]
        box = polygon_box(points, rows[0]["normal"])
        if box is None:
            omissions["sloped" if max(abs(v) for v in rows[0]["normal"]) < .99999 else "nonrectangular_or_too_thin"] += 1
            continue
        center, half = box
        row = dict(id=f"thief-bafford-collision-{cid:04d}-{polygon:03d}", kind="static",
                   transform=dict(position=center, rotation=[0, 0, 0], scale=[1, 1, 1]),
                   collider=dict(shape="box", center=[0, 0, 0], half_size=half),
                   source=dict(game="Thief Gold", cell=cid, polygon=polygon, material=copy.deepcopy(materials[rows[0]["material"]].get("source", {}))))
        proxies.append(box_record(row, full_scene))
    return entities, meshes, materials, provenance, proxies, dict(per_cell), dict(omissions)


def expansion_state(entry, state, full, annotations, topology, selected, asset_root, *, entry_scene="thief_bafford_entry.json", full_scene="thief_bafford.json"):
    baseline = source_cells(entry)
    level.require(not (baseline & set(selected)), "New source cells overlap the preserved entry")
    cells = {cell["id"]: cell for cell in topology["cells"]}
    connected = set(baseline)
    pending = set(selected)
    while pending:
        reachable = {cid for cid in pending if any(portal["target"] in connected for portal in cells.get(cid, {}).get("portals", []))}
        level.require(reachable, "Requested expansion cells are disconnected from the entry portal graph")
        connected |= reachable
        pending -= reachable
    new, meshes, materials, provenance, proxies, counts, omissions = select_new_geometry(full, annotations, topology, set(selected), asset_root, state["materials"], full_scene=full_scene)
    old_boxes = [box_record(entity, entry_scene) for entity in state["by_id"].values() if "collider" in entity]
    merged, merge_report = merge_boxes(old_boxes + proxies)
    entities = []
    for entity in sorted(state["by_id"].values(), key=lambda row: row["id"]):
        level.require(entity["kind"] in ("static", "spawn", "light"), "Entry has authored dynamic gameplay; expansion needs explicit shared gameplay conversion")
        if "collider" not in entity or "model" in entity:
            row = copy.deepcopy(entity)
            row.pop("collider", None)
            entities.append(row)
    entities += new + proxy_entities(merged)
    result = dict(state, by_id={entity["id"]: entity for entity in entities}, meshes={**state["meshes"], **meshes},
                  materials=materials, triangle_provenance=provenance)
    level.require(len(result["by_id"]) == len(entities), "Expansion creates duplicate canonical entity IDs")
    report = dict(preserved_cells=sorted(baseline), added_cells=sorted(selected), added_triangle_counts={str(cid): counts[cid] for cid in sorted(counts)},
                  new_collision_omissions=omissions, collision_merge=merge_report,
                  provenance_check="Annotation counts, cell IDs, convex containment, boundary planes and polygon fan continuity checked against captured current meshes")
    return result, report


def expand(entry_path, full_path, annotations_path, topology_path, output, manifest_path=None, *, content_root=ROOT / "content", selected=DEFAULT_CELLS, seam=-3.048, replace=False):
    content_root = Path(content_root).resolve()
    entry_path, full_path, annotations_path, topology_path, output = [Path(path).resolve() for path in (entry_path, full_path, annotations_path, topology_path, output)]
    manifest_path = Path(manifest_path).resolve() if manifest_path else output.with_name(output.stem + ".mission.json")
    selected = tuple(sorted(set(selected)))
    level.require(selected and all(type(cid) is int and cid >= 0 for cid in selected), "Select nonnegative source cell IDs")
    project = ProjectLevels(content_root.parent)
    level.require(entry_path.parent == full_path.parent == output.parent == manifest_path.parent == content_root,
                  "Scene source/output/manifest paths must be directly inside content root")
    protected = {entry_path, full_path, content_root / "thief_bafford_stream.json", content_root / "thief_bafford_stream.mission.json"}
    level.require(output not in protected and manifest_path not in protected and output != manifest_path,
                  "Expansion must preserve the entry/full/current stream sources")
    with project.lock():
        input_paths = [project.safe(path) for path in (entry_path, full_path, annotations_path, topology_path)]
        expected = {path: path.read_bytes() for path in input_paths}
        entry, full, annotations, topology = [json.loads(expected[path]) for path in input_paths]
        prior = {path: path.read_bytes() if path.exists() else None for path in (output, manifest_path)}
        level.require(replace or not any(raw is not None for raw in prior.values()), "Expanded source/manifest already exist; retain artist edits or pass --replace explicitly")
        expected.update(prior)
        discovered = level.validate(entry, content_root, authoring=False)
        deps = {uri: Path(path).resolve() for uri, path in discovered["dependencies"]}
        full_assets = {asset["id"]: asset for asset in full["assets"]}
        full_materials = {material["id"]: material for material in full["materials"]}
        for entity in full["entities"]:
            if entity["kind"] == "static" and "model" in entity and set(selected) & set(entity.get("source", {}).get("cells", [])):
                uri = full_assets[entity["model"]]["uri"]
                deps[uri] = content_path(uri, content_root, {".obj"})
                material = discovered["materials"].get(entity["material"], full_materials[entity["material"]])
                if "texture" in material:
                    uri = material["texture"]["uri"]
                    deps[uri] = content_path(uri, content_root, {".png", ".jpg", ".jpeg"})
        captured, external_expected = {}, {}
        for uri, path in sorted(deps.items()):
            if not uri.startswith("code:"):
                level.require(not Path(uri).is_absolute() and ".." not in Path(uri).parts and ":" not in uri and "\\" not in uri,
                              "Expansion dependency must use a content-relative URI")
                level.require(project.safe(content_root / uri) == path and path.is_relative_to(content_root), "Expansion dependency path escapes/aliases content")
            captured[uri] = path.read_bytes()
            if path.is_relative_to(project.root):
                expected[project.safe(path)] = captured[uri]
            else:
                external_expected[path] = captured[uri]
        staging = project.safe(project.editor / "expansion-staging")
        staging.mkdir(parents=True, exist_ok=True)
        changes = {}
        with tempfile.TemporaryDirectory(prefix="bafford-", dir=staging) as temporary:
            overlay = Path(temporary) / "content"
            overlay.mkdir()
            for uri, raw in captured.items():
                if uri.startswith("code:"):
                    continue
                path = overlay / uri
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            state = level.validate(entry, overlay, authoring=False)
            level.require({uri for uri, _ in state["dependencies"]} == {uri for uri, _ in discovered["dependencies"]}, "Entry dependency set changed during expansion; retry")
            expanded_state, expansion_report = expansion_state(entry, state, full, annotations, topology, selected, overlay,
                                                               entry_scene=entry_path.name, full_scene=full_path.name)
            authored = copy.deepcopy(entry)
            if {3831, 4987} <= set(selected):
                starts = authored.setdefault("test_starts", [])
                names = {start["id"] for start in starts}
                for start in (dict(id="east-street", label="East street", position=[14.8, -2.4384, 48.5], yaw=270, pitch=0),
                              dict(id="bridge-west", label="Bridge west", position=[9.3, -2.1336, 48.5], yaw=90, pitch=0)):
                    level.require(start["id"] not in names, "Entry already defines expansion test start: " + start["id"])
                    starts.append(start)
            result, manifest, payloads, report = converter.derive(authored, expanded_state, entry_path.name, converter.digest(expected[entry_path]), seam)
            result["title"] = "Bafford Streets Expanded"
            manifest["source"] = output.name
            hashes = [dict(uri=uri, sha256=converter.digest(raw)) for uri, raw in sorted(captured.items())]
            source_hashes = {path.relative_to(project.root).as_posix(): converter.digest(expected[path]) for path in input_paths}
            expansion_report.update(input_sources=source_hashes, input_dependencies=hashes)
            report["expansion"] = expansion_report
            report["acceptance"] = "Bounded source street expansion preserving edited entry; target traversal and performance require separate verification"
            result["stream_conversion"]["coverage"] = "Edited entry preserved plus selected annotated full-study street cells"
            result["stream_expansion"] = copy.deepcopy(expansion_report)
            for uri, raw in payloads.items():
                path = project.safe(content_root / uri)
                previous = path.read_bytes() if path.exists() else None
                level.require(previous is None or previous == raw, "Immutable expanded OBJ differs from content-addressed payload")
                expected[path] = previous
                if previous is None:
                    changes[path] = raw
                staged = overlay / uri
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(raw)
            level.validate(result, overlay, authoring=False)
        if any(raw is not None for raw in prior.values()):
            recovery = project.safe(project.editor / "replaced-expansions" / uuid.uuid4().hex)
            changes.update({recovery / path.name: raw for path, raw in prior.items() if raw is not None})
        report_path = project.safe(project.root / "build/bafford-expanded-conversion/report.json")
        changes.update({output: json_bytes(result), manifest_path: json_bytes(manifest), report_path: json_bytes(report)})
        for path, raw in external_expected.items():
            level.require(path.read_bytes() == raw, "Compiler dependency changed during expansion; retry")
        project.publish(changes, expected)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", type=Path, default=ROOT / "content/thief_bafford_entry.json")
    parser.add_argument("--full", type=Path, default=ROOT / "content/thief_bafford.json")
    parser.add_argument("--annotations", type=Path, default=ROOT / "build/thief-bafford/face-annotations.json")
    parser.add_argument("--topology", type=Path, default=ROOT / "build/thief-bafford/world-topology.json")
    parser.add_argument("--output", type=Path, default=ROOT / "content/thief_bafford_expanded.json")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--content-root", type=Path, default=ROOT / "content")
    parser.add_argument("--cells", type=int, nargs="+", default=DEFAULT_CELLS)
    parser.add_argument("--seam-x", type=float, default=-3.048)
    parser.add_argument("--replace", action="store_true", help="Explicitly replace expanded artist edits while retaining recovery copies")
    args = parser.parse_args(argv)
    try:
        report = expand(args.entry, args.full, args.annotations, args.topology, args.output, args.manifest,
                        content_root=args.content_root, selected=args.cells, seam=args.seam_x, replace=args.replace)
        summary = {key: report[key] for key in ("output_counts", "regions", "acceptance")}
        summary["expansion"] = {key: report["expansion"][key] for key in ("added_cells", "added_triangle_counts", "new_collision_omissions", "collision_merge")}
        print(json.dumps(summary, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        print("Bafford expansion failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
