"""Check out level architecture to Blender and safely apply staged scene exports.

Blender owns geometry, static transforms and box proxies. Gameplay remains in
the level. Each import is an optimistic, validated project transaction.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
if not __package__:
    sys.path.insert(0, str(ROOT))
from tools.asset_pack import content_path, resolve_asset_packs, valid_id
from tools.blender_assets import find_blender
from tools.compile_level import read_obj, validate, LIMITS
from tools.content_profile import content_limits, STUDY_LIMITS
from tools.project_levels import ProjectLevels, json_bytes, require

OWNED = ("transform", "model", "material", "collider", "floor", "collision_authoring")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def owned(row):
    return {key: copy.deepcopy(row[key]) for key in OWNED if key in row}


def merge_entities(document, baseline, incoming):
    """Pure three-way merge. Absence is meaningful; deletion never erases data."""
    result = copy.deepcopy(document)
    rows = {row["id"]: row for row in result["entities"]}
    require(len(rows) == len(result["entities"]), "Duplicate level entity ID")
    conflicts, added, updated, missing, suppressed = [], [], [], [], []
    used = {row["id"].casefold() for group in ("entities", "assets", "materials") for row in result[group]}
    for ident, new in incoming.items():
        valid_id(ident, "Blender object")
        require(set(new) <= set(OWNED) and "transform" in new, f"{ident}: unsupported exported fields")
        current, base = rows.get(ident), baseline.get(ident)
        if base is None:
            if ident.casefold() in used:
                conflicts.append(f"{ident}: ID already belongs to level content")
                continue
            current = {"id": ident, "kind": "static", **copy.deepcopy(new)}
            result["entities"].append(current)
            used.add(ident.casefold())
            added.append(ident)
        elif current is None:
            # A deletion in the editor stays deleted. Changed source geometry
            # for an absent instance needs deliberate reconciliation.
            if new != base:
                conflicts.append(f"{ident}: deleted in editor, changed in Blender")
            else:
                suppressed.append(ident)
        else:
            before = copy.deepcopy(current)
            for key in OWNED:
                # A tuple encodes presence without a serializable sentinel.
                old, local, remote = [(key in x, x.get(key)) for x in (base, current, new)]
                if local == remote or remote == old:
                    continue
                if local != old:
                    conflicts.append(f"{ident}.{key}: changed in both editor and Blender")
                    continue
                if remote[0]:
                    current[key] = copy.deepcopy(remote[1])
                else:
                    current.pop(key, None)
            if current != before:
                updated.append(ident)
    for ident in baseline.keys() - incoming.keys():
        if ident in rows:
            missing.append(ident)
    require(not conflicts, "Blender import conflicts (nothing applied):\n" + "\n".join(conflicts))
    # Keep baselines for absent objects, including editor deletion tombstones.
    next_base = {**copy.deepcopy(baseline), **copy.deepcopy(incoming)}
    return result, next_base, dict(added=added, updated=updated, retained_missing=missing,
                                   editor_deletions_preserved=suppressed)


def checkout(level, destination, project_root=ROOT, blender=None):
    project = ProjectLevels(project_root)
    destination = project.safe(Path(destination).resolve())
    require(destination.suffix == ".blend" and not destination.exists(),
            "Choose a new .blend filename; checkout never overwrites an artist's source")
    with project.lock():
        path = project.path(level)
        raw, document = project.read_level(path)
        validate(document, project.content, authoring=True)
        resolved, _, _ = resolve_asset_packs(document, project.content, authoring=True)
        entities = [row for row in document["entities"] if row["kind"] == "static"]
        require(entities, "Level has no static architecture to check out")
        models = {row["model"] for row in entities if "model" in row}
        assets = {row["id"]: row for row in resolved["assets"] if row["id"] in models}
        materials = {row["id"]: row for row in resolved["materials"]}
        textured_models = {row['model'] for row in entities if 'model' in row and
                           'texture' in materials[row['material']]}
        meshes = {}
        for ident, asset in assets.items():
            require(asset.get("type", "mesh") == "mesh", f"{ident}: scene checkout currently requires OBJ static meshes")
            source = content_path(asset["uri"], project.content, {".obj"})
            textured = ident in textured_models
            meshes[ident] = dict(uri=asset["uri"], source_sha256=sha(source.read_bytes()),
                                textured=textured, mesh=read_obj(source, textured=textured))
        scene_id = "scene-" + uuid.uuid4().hex
        exchange = project.root / "build/blender" / path.stem
        # Each checkout gets its own identity/output; independent sessions
        # cannot silently replace one another's exports.
        exchange = exchange / scene_id
        exchange.mkdir(parents=True, exist_ok=True)
        data = dict(version=1, scene_id=scene_id, level=path.name, project=str(project.root),
                    source_sha256=sha(raw), entities=entities, meshes=meshes, materials=materials,
                    context=[row for row in document["entities"] if row["kind"] != "static"],
                    authoring_profile=document.get('authoring_profile'),
                    exchange=str(exchange / "scene.json"), destination=str(destination))
        prepared = exchange / "checkout.json"
        prepared.write_bytes(json_bytes(data))
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [str(find_blender(blender)), "--factory-startup", "--background", "--disable-autoexec",
               "--python-exit-code", "1", "--python", str(ROOT / "tools/blender/scene_checkout.py"),
               "--", str(prepared)]
    done = subprocess.run(command, capture_output=True, text=True)
    (exchange / "checkout.log").write_text(done.stdout + done.stderr, encoding="utf-8")
    require(done.returncode == 0, "Blender checkout failed: " + done.stdout + done.stderr)
    return dict(blend=str(destination), exchange=str(exchange / "scene.json"), scene_id=scene_id)


def export_source(source, project_root=ROOT, blender=None):
    """Stage a saved checkout with the same add-on used by Blender's UI.

    Saving the revision back to the source is essential: repeated background
    exports must have the same ordering guarantees as the interactive workflow.
    This operation never changes canonical level or OBJ content.
    """
    project = ProjectLevels(project_root)
    source = project.safe(Path(source).resolve())
    require(source.suffix.lower() == ".blend" and source.is_file(),
            "Choose an existing project .blend checkout to export")
    with tempfile.TemporaryDirectory(prefix="dl64-scene-export-") as temp:
        report = Path(temp) / "report.json"
        command = [str(find_blender(blender)), "--factory-startup", "--background", "--disable-autoexec",
                   str(source), "--python-exit-code", "1", "--python",
                   str(ROOT / "tools/blender/scene_export.py"), "--", str(project.root), str(report)]
        done = subprocess.run(command, capture_output=True, text=True)
        require(done.returncode == 0, "Blender export failed: " + done.stdout + done.stderr)
        require(report.is_file(), "Blender export did not produce its report")
        result = json.loads(report.read_bytes())
    exchange = project.safe(Path(result["exchange"]).resolve())
    require(exchange.is_relative_to(project.root / "build"), "Scene exchange must be inside project build/")
    (exchange.parent / "export.log").write_text(done.stdout + done.stderr, encoding="utf-8")
    return result


def read_exchange(path, max_bytes):
    path = Path(path).resolve()
    require(path.is_file() and path.stat().st_size <= max_bytes, "Missing or oversized Blender scene export")
    data = json.loads(path.read_bytes())
    require(data.get("version") == 1, "Unsupported Blender scene version")
    valid_id(data.get("scene_id"), "Scene")
    require(type(data.get("revision")) is int and data["revision"] > 0, "Invalid export revision")
    return data


def validate_exchange(data, limits):
    for group in ('entities', 'baseline', 'meshes'):
        maximum = limits['models'] if group == 'meshes' else limits['entities']
        require(isinstance(data.get(group), dict) and len(data[group]) <= maximum, f'Invalid scene {group}')
    return data


def load_exchange(path, *, document=None):
    limits = content_limits(document or {}, LIMITS, authoring=True)
    return validate_exchange(read_exchange(path, limits['exchange_bytes']), limits)


def apply_exchange(exchange, project_root=ROOT, *, check=False, staged=None, expected_sha=None):
    project = ProjectLevels(project_root)
    exchange = project.safe(Path(exchange).resolve())
    require(exchange.is_relative_to(project.root / "build"), "Scene exchange must be inside project build/")
    # Read the bounded envelope to locate its saved source, then derive limits
    # from that source. An export cannot grant itself the host study profile.
    incoming = read_exchange(exchange, STUDY_LIMITS['exchange_bytes'])
    path = project.path(incoming["level"])
    with project.lock():
        expected = {project.safe(p): p.read_bytes() for p in sorted(project.content.rglob("*")) if p.is_file()}
        raw = expected[path]
        require(expected_sha is None or sha(raw) == expected_sha, "Level changed on disk; reload before importing")
        document = json.loads(raw)
        limits = content_limits(document, LIMITS, authoring=True)
        require(exchange.stat().st_size <= limits['exchange_bytes'], 'Oversized Blender scene export for this level profile')
        validate_exchange(incoming, limits)
        if staged:
            stage = project.safe(Path(staged).resolve())
            require(stage.is_relative_to(project.root / ".dev") or stage.is_relative_to(project.root / "build"), "Draft must be in .dev/ or build/")
            candidate = json.loads(stage.read_bytes())
            require(candidate.get('authoring_profile') == document.get('authoring_profile'),
                    'A Blender import cannot change the saved level authoring profile')
            document = candidate
        resolved, _, _ = resolve_asset_packs(document, project.content, authoring=True)
        definitions = {row["id"]: row for row in resolved["assets"]}
        states = document.get("blender_imports", {})
        require(isinstance(states, dict), "Invalid Blender import history")
        previous = states.get(incoming["scene_id"], {})
        incoming_hash = sha(json_bytes(incoming))
        if previous:
            require(incoming["revision"] > previous["revision"] or
                    (incoming["revision"] == previous["revision"] and incoming_hash == previous["export_sha256"]),
                    "This export is older than the last import or comes from a conflicting copy of the .blend; reopen the latest saved source or make a new checkout")
        baseline = copy.deepcopy(previous.get("entities", incoming["baseline"]))
        # Only deliberate generated-shape retirements can be recreated without
        # treating their absence as a deletion by the level designer.
        for ident in previous.get('generated_deleted', []):
            if ident in incoming['entities'] and not any(e['id']==ident for e in document['entities']):
                baseline.pop(ident,None)
        result, next_base, report = merge_entities(document, baseline, incoming["entities"])
        removals = incoming.get('generated_removals', [])
        require(isinstance(removals,list) and len(removals)<=limits['entities'], 'Invalid generated collision removals')
        generated_deleted = set(previous.get('generated_deleted', [])) - set(incoming['entities'])
        for removal in removals:
            require(isinstance(removal,dict) and set(removal)=={'id','owner'}, 'Invalid generated collision removal')
            ident,owner = removal['id'],removal['owner']
            valid_id(ident,'Generated collision')
            require(ident not in incoming['entities'], f'{ident}: cannot remove and export the same collision')
            source_owner = incoming['entities'].get(owner,{}).get('collision_authoring',{})
            require(source_owner.get('mode')=='stairs' and ident not in source_owner.get('colliders',[]),
                    f'{ident}: collision retirement needs an explicitly rebuilt staircase')
            current = next((e for e in result['entities'] if e['id']==ident),None)
            if current:
                require(current.get('kind')=='static' and 'model' not in current and 'collider' in current and
                        set(current)<=set(OWNED)|{'id','kind'} and owned(current)==baseline.get(ident),
                        f'{ident}: generated collision was edited in the level; retirement would overwrite it')
                # Group membership is authored too; don't leave a dangling ID.
                def references(value):
                    if isinstance(value,dict):
                        return any(references(v) for v in value.values())
                    if isinstance(value,list):
                        return any(references(v) for v in value)
                    return value==ident
                require(not any(references(value) for key,value in result.items()
                                if key not in ('entities','blender_imports')),
                        f'{ident}: collision is referenced by level authoring data')
                require(not any(references(value) for entity in result['entities'] if entity is not current
                                for key,value in entity.items() if key != 'collision_authoring'),
                        f'{ident}: collision is referenced by another level entity')
                result['entities'].remove(current)
                if ident in report['retained_missing']:
                    report['retained_missing'].remove(ident)
            generated_deleted.add(ident)
        report['removed_generated'] = sorted(generated_deleted)
        candidates, mesh_state, changed_meshes = {}, copy.deepcopy(previous.get("meshes", {})), []
        for ident, model in incoming["meshes"].items():
            valid_id(ident, "Mesh")
            require(ident in definitions and definitions[ident].get("uri") == model["uri"],
                    f"{ident}: level asset reference changed; check out a fresh scene")
            target = content_path(model["uri"], project.content, {".obj"})
            require(target in expected, f"{ident}: missing source asset")
            digest = model["export_sha256"]
            require(isinstance(digest, str) and len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), "Invalid mesh digest")
            payload_path = project.safe(exchange.parent / "meshes" / (digest + ".obj"))
            require(payload_path.is_file() and payload_path.stat().st_size <= 8*1024*1024, f"{ident}: missing or oversized mesh payload")
            payload = payload_path.read_bytes()
            require(sha(payload) == digest, f"{ident}: exported mesh changed; export again")
            base = previous.get("meshes", {}).get(ident, model)
            current_sha = sha(expected[target])
            changed = digest != base["baseline_export_sha256"]
            if changed:
                require(current_sha in (base["source_sha256"], digest),
                        f"{ident}: geometry changed in both the project and Blender; nothing applied")
                if current_sha != digest:
                    candidates[target] = payload
                    changed_meshes.append(ident)
            mesh_state[ident] = dict(source_sha256=(sha(candidates.get(target, expected[target])) if changed else base["source_sha256"]),
                                     baseline_export_sha256=digest)
        # Prevent separate checkouts from claiming the same object identities.
        # Their first merge still compares against their own actual checkout.
        result.setdefault("blender_imports", {})[incoming["scene_id"]] = dict(
            entities=next_base, meshes=mesh_state, exchange=exchange.relative_to(project.root).as_posix(),
            revision=incoming["revision"], export_sha256=incoming_hash,
            generated_deleted=sorted(generated_deleted))
        # The C++ editor orders JSON object keys. Canonicalize here as well so
        # a second import cannot dirty the source just by reordering metadata.
        candidates[path] = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
        # Full validation against a private overlay; no temporary writes into
        # live content, even for invalid meshes or a conflicting patrol route.
        with tempfile.TemporaryDirectory(prefix="dl64-scene-validate-") as temp:
            overlay = Path(temp) / "content"
            for source, payload in expected.items():
                output = overlay / source.relative_to(project.content)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(candidates.get(source, payload))
            validated = []
            for source in overlay.glob("*.json"):
                data = json.loads(source.read_bytes())
                if isinstance(data, dict) and data.get("version") == 2:
                    validate(data, overlay, authoring=True)
                    validated.append(source.name)
        require(set(expected) == {project.safe(p) for p in project.content.rglob("*") if p.is_file()},
                "Content files changed during validation; retry")
        changes = {p: b for p, b in candidates.items() if expected[p] != b}
        # Retain byte-exact previous source files as a recoverable checkpoint.
        if not check and changes:
            backup = project.root / ".dev/editor/blender-backups" / uuid.uuid4().hex
            for source in list(changes):
                recovery = backup / source.relative_to(project.content)
                changes[recovery] = expected[source]
            project.publish(changes, expected)
        report.update(published=not check, changed_meshes=changed_meshes, validated_levels=validated,
                      level=path.name, document=result, collision_review=incoming.get('collision_review',[]))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("checkout")
    create.add_argument("--level", default="sponza_courtyard.json")
    create.add_argument("--output", type=Path, default=ROOT / "art/sponza-edit.blend")
    create.add_argument("--blender")
    export = commands.add_parser("export")
    export.add_argument("--source", type=Path, required=True)
    export.add_argument("--blender")
    apply = commands.add_parser("import")
    apply.add_argument("--exchange", type=Path, required=True)
    apply.add_argument("--check", action="store_true")
    apply.add_argument("--staged", type=Path)
    apply.add_argument("--expected-sha")
    apply.add_argument("--expected-source", type=Path)
    apply.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "checkout":
            result = checkout(args.level, args.output, args.project_root, args.blender)
        elif args.command == "export":
            result = export_source(args.source, args.project_root, args.blender)
        else:
            if args.report:
                project = ProjectLevels(args.project_root)
                report = project.safe(args.report.resolve())
                require(report.is_relative_to(project.root / "build") or report.is_relative_to(project.root / ".dev"), "Report must be in build/ or .dev/")
            expected_sha = sha(args.expected_source.read_bytes()) if args.expected_source else args.expected_sha
            result = apply_exchange(args.exchange, args.project_root, check=args.check,
                                    staged=args.staged, expected_sha=expected_sha)
            if args.report:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_bytes(json_bytes(result))
        print(json.dumps({k: v for k, v in result.items() if k != "document"}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
