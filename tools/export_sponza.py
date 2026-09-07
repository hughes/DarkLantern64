"""Export saved Sponza mesh edits, preserving level placement and shared IDs.

The worker reads a saved .blend in isolated background Blender. Only owned OBJ
files are published, after the ordinary compiler validates every authored level.
Use --check to validate without changing content. No open Blender session is used.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if not __package__:
    sys.path.insert(0, str(ROOT))
from tools.blender_assets import find_blender
from tools.compile_level import read_obj, validate
from tools.project_levels import ProjectLevels

spec = importlib.util.spec_from_file_location("sponza_export_core", ROOT / "tools/blender/darklantern64_export/core.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def contract(project):
    folder = project.content / "assets/sponza"
    pack = json.loads(project.safe(folder / "pack.json").read_text())
    geometry = json.loads(project.safe(folder / "geometry.json").read_text())
    core.validate_publication(pack, project.content, folder)
    core.require(pack["assets"] == geometry["assets"], "Sponza pack and geometry IDs/URIs differ; reconcile their authoring contract first")
    assets = {}
    for asset in pack["assets"]:
        core.identifier(asset["id"], "Sponza asset")
        path = project.safe(project.content / asset["uri"])
        core.require(path.suffix == ".obj" and path.parent == folder / "models" and path.is_file(),
                     "Sponza geometry exports must retain their owned OBJ paths")
        assets[asset["id"]] = asset["uri"]
    core.require({row["model"] for row in geometry["instances"]} == set(assets),
                 "Every Sponza asset needs an authored Blender instance")
    return geometry, assets


def validate_candidates(project, candidates, expected):
    """Validate a private content overlay; never temporarily replace real OBJs."""
    with tempfile.TemporaryDirectory(prefix="dl64-sponza-validation-") as directory:
        overlay = Path(directory) / "content"
        overlay.mkdir()
        for path, payload in expected.items():
            target = overlay / path.relative_to(project.content)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(candidates.get(path, payload))
        results = {}
        for path in sorted(overlay.glob("*.json")):
            data = json.loads(path.read_text())
            if not isinstance(data, dict) or data.get("version") != 2:
                continue
            state = validate(data, overlay)
            results[path.name] = {
                "models": len(state["models"]),
                "instanced_vertices": sum(len(state["meshes"][e["model"]]["vertices"]) for e in state["models"]),
                "instanced_triangles": sum(len(state["meshes"][e["model"]]["indices"]) // 3 for e in state["models"]),
            }
        core.require("sponza_courtyard.json" in results, "The Sponza level is missing from candidate validation")
        return results


def publish_candidates(project, candidates, expected, *, check=False):
    levels = validate_candidates(project, candidates, expected)
    current = {project.safe(path) for path in project.content.rglob("*") if path.is_file()}
    core.require(current == set(expected) and all(path.read_bytes() == old for path, old in expected.items()),
                 "Content changed during export validation; retry against the current level and assets")
    changes = {path: payload for path, payload in candidates.items() if expected[path] != payload}
    # ProjectLevels verifies expected bytes again and rolls back partial writes.
    # The caller holds its lock through extraction, validation and publication.
    if not check:
        project.publish(changes, expected)
    return {"published": not check, "changed_meshes": len(changes), "validated_levels": levels}


def extract(source, project, destination, blender):
    command = [str(blender), "--factory-startup", "--background", "--disable-autoexec",
               "--python-exit-code", "1", str(source), "--python",
               str(ROOT / "tools/blender/export_sponza_geometry.py"), "--",
               "--contract", str(project.content / "assets/sponza/geometry.json"),
               "--output", str(destination)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    (destination / "blender.log").write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise core.ExportError("Saved Sponza export failed:\n" + result.stdout + result.stderr)
    return json.loads((destination / "meshes.json").read_text())


def export_saved(source, project_root=ROOT, *, blender=None, check=False, report_path=None):
    source = Path(source).resolve()
    core.require(source.suffix.lower() == ".blend" and source.is_file(), "Save a Sponza .blend before exporting")
    project = ProjectLevels(project_root)
    if report_path:
        report_path = project.safe(Path(report_path).resolve())
        core.require(report_path.suffix == ".json" and report_path.is_relative_to(project.root / "build"),
                     "Export reports must be JSON files inside this project's build directory")
    blender = find_blender(blender)
    source_hash = sha(source.read_bytes())
    with project.lock():
        geometry, assets = contract(project)
        # All content is small; snapshotting it gives ordinary full-level
        # validation plus optimistic protection from non-editor concurrent edits.
        expected = {project.safe(path): path.read_bytes() for path in sorted(project.content.rglob("*")) if path.is_file()}
        with tempfile.TemporaryDirectory(prefix="dl64-sponza-export-") as temporary:
            stage = Path(temporary)
            worker = extract(source, project, stage, blender)
            core.require(set(worker["assets"]) == set(assets), "Blender output does not match the shared asset IDs")
            candidates, comparisons = {}, {}
            for ident, uri in assets.items():
                incoming = stage / (ident + ".obj")
                core.require(incoming.is_file() and incoming.stat().st_size <= 8 * 1024 * 1024,
                             f"Missing or oversized Blender OBJ: {ident}")
                payload = incoming.read_bytes()
                core.require(sha(payload) == worker["assets"][ident]["sha256"], "Blender OBJ hash changed")
                target = project.content / uri
                previous, current = read_obj(target, textured=True), read_obj(incoming, textured=True)
                comparisons[ident] = mesh_comparison(previous, current)
                candidates[target] = payload
            core.require(sha(source.read_bytes()) == source_hash, "Saved Blender source changed during export; retry")
            result = publish_candidates(project, candidates, expected, check=check)
    result.update(passed=True, source=str(source), source_sha256=source_hash,
                  assets=len(assets), instances=len(geometry["instances"]), comparisons=comparisons,
                  worker=worker, geometry_only=True, pack_level_and_proxies_unchanged=True,
                  notes=["Only OBJ geometry is exported; materials, level placements and collision proxies remain editor-owned.",
                         "Mesh-local origins are preserved. Moving an origin in Blender normally changes its instance transform and is rejected.",
                         "Save + Cook in the editor after exporting to refresh shared assets."])
    if report_path:
        path = Path(report_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def mesh_comparison(previous, current):
    same = previous["indices"] == current["indices"] and len(previous["vertices"]) == len(current["vertices"])
    result = {"same_topology": same, "vertices": len(current["vertices"]), "triangles": len(current["indices"]) // 3}
    if same:
        for field, name in (("vertices", "max_position_error_m"), ("uvs", "max_uv_error"), ("normals", "max_normal_error")):
            if field in previous and field in current:
                result[name] = max((abs(a-b) for left, right in zip(previous[field], current[field])
                                    for a, b in zip(left, right)), default=0)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "art/sponza.blend")
    parser.add_argument("--blender", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate saved mesh edits without publishing OBJ files")
    parser.add_argument("--project-root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--report", type=Path, default=ROOT / "build/sponza-export.json")
    args = parser.parse_args()
    try:
        result = export_saved(args.source, args.project_root, blender=args.blender, check=args.check, report_path=args.report)
        print(json.dumps({key: result[key] for key in ("passed", "published", "changed_meshes", "assets", "validated_levels")}, indent=2))
        return 0
    except (OSError, ValueError, KeyError) as error:
        print("Sponza export failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
