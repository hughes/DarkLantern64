"""Project-level authoring: discover, create, duplicate, save metadata and cook levels.

Canonical scenes live directly in content/. The bundle is an explicit subset of
those scenes, so unfinished work need not ship in the game menu. Destructive
operations require the raw source SHA from list and retain a recoverable copy.
All operations use a project lock; file publications roll back on failure.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

try:
    from compile_level import compile_level, validate
    from compile_bundle import MAX_LEVELS, read_manifest
except ModuleNotFoundError:
    from tools.compile_level import compile_level, validate
    from tools.compile_bundle import MAX_LEVELS, read_manifest

ROOT = Path(__file__).resolve().parents[1]
FILENAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\.json")
RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])", re.IGNORECASE)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def json_bytes(data):
    return (json.dumps(data, indent=2, ensure_ascii=True) + "\n").encode("utf-8")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def starter_level(title):
    """A bounded room with a working switch, door and objective; no copied art."""
    def entity(ident, kind, position, scale=(1, 1, 1), material="mat-stone", collision=False):
        result = {"id": ident, "kind": kind, "transform": {
            "position": list(position), "rotation": [0, 0, 0], "scale": list(scale)}}
        if kind not in ("spawn", "light"):
            result.update(model="mesh-block", material=material)
        if collision:
            result["collider"] = {"shape": "box", "center": [0, 0, 0], "half_size": [.5, .5, .5]}
        return result

    entities = [entity("floor", "static", (0, -.1, 0), (8.4, .2, 8.4), collision=True)]
    for ident, position, scale in (
        ("west-wall", (-4, 1.5, 0), (.2, 3, 8.2)),
        ("east-wall", (4, 1.5, 0), (.2, 3, 8.2)),
        ("north-wall", (0, 1.5, -4), (8.2, 3, .2)),
        ("south-wall", (0, 1.5, 4), (8.2, 3, .2)),
        ("divider-west", (-2.425, 1.5, 0), (3.15, 3, .2)),
        ("divider-east", (2.425, 1.5, 0), (3.15, 3, .2)),
    ):
        entities.append(entity(ident, "static", position, scale, collision=True))
    entities.append(entity("door", "door", (0, 1.5, 0), (1.6, 3, .15), "mat-wood", True))
    entities.append(dict(entity("door-switch", "control", (-1.05, .9, .25), (.22, .3, .18), "mat-switch"), target="door"))
    entities.append(entity("mission-relic", "objective", (0, .65, -2.5), (.35, .65, .35), "mat-gold"))
    spawn = entity("player-start", "spawn", (0, 0, 2.7))
    spawn["transform"]["rotation"][1] = 180
    entities.append(spawn)
    for ident, z in (("entry-light", 2), ("relic-light", -2)):
        entities.append(dict(entity(ident, "light", (0, 2.6, z)), radius=5, intensity=.8, color=[1, .78, .5]))
    return {"version": 2, "title": title, "asset_packs": ["assets/guard/pack.json"],
            "assets": [{"id": "mesh-block", "uri": "models/block.obj"}],
            "materials": [{"id": ident, "color": color} for ident, color in (
                ("mat-stone", [.3, .35, .42, 1]), ("mat-wood", [.35, .2, .08, 1]),
                ("mat-switch", [.15, .7, .35, 1]), ("mat-gold", [.95, .66, .12, 1]))],
            "entities": entities, "test_starts": []}


class ProjectLevels:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        require(self.root.is_dir(), "Project root does not exist")
        self.content = self.safe(self.root / "content")
        require(self.content.is_dir(), "Project has no content directory")
        self.manifest = self.safe(self.content / "level_bundle.json")
        self.editor = self.safe(self.root / ".dev/editor")

    def safe(self, path):
        """Reject aliases, junctions and links, including inside generated output."""
        path = Path(path).absolute()
        require(path.is_relative_to(self.root), "Path escapes the project directory")
        require(path.resolve() == path, "Symlinks or junctions are not allowed for project level operations")
        for part in (path, *path.parents):
            if part == self.root:
                break
            require(not part.is_symlink(), "Symlinks are not allowed for project level operations")
        return path

    @contextmanager
    def lock(self):
        self.safe(self.editor).mkdir(parents=True, exist_ok=True)
        path = self.safe(self.editor / "project-levels.lock")
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise ValueError("Another project level operation is running. If it crashed, remove .dev/editor/project-levels.lock after checking no editor operation is active.") from error
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(json.dumps({"pid": os.getpid()}))
            yield
        finally:
            path.unlink()

    def filename(self, value, *, new=False):
        require(isinstance(value, (str, Path)), "Expected a level filename")
        path = Path(value)
        if path.is_absolute():
            require(path.parent == self.content, "Level must be directly inside content")
            value = path.name
        else:
            value = str(value)
        if new and not value.endswith(".json"):
            value += ".json"
        require(FILENAME.fullmatch(value) is not None and not RESERVED.fullmatch(Path(value).stem),
                "Use an ASCII level filename such as my_level.json (letters, numbers, underscores or hyphens)")
        require(value.casefold() != self.manifest.name.casefold(), "The game bundle is not a level")
        return value

    def path(self, value, *, new=False):
        name = self.filename(value, new=new)
        matches = [p.name for p in self.content.iterdir() if p.name.casefold() == name.casefold()]
        if new:
            require(not matches, "A file with that name already exists (case-insensitive)")
        else:
            require(matches == [name], "Level is missing or its filename case differs from the source")
        result = self.safe(self.content / name)
        require(new or result.is_file(), "Level source is not a file")
        return result

    def read_level(self, path, expected=None):
        raw = self.safe(path).read_bytes()
        require(len(raw) <= 8 * 1024 * 1024, "Level JSON exceeds 8 MiB")
        if expected is not None:
            require(re.fullmatch(r"[a-fA-F0-9]{64}", expected) is not None, "Expected source SHA-256 must contain 64 hexadecimal characters")
            require(sha256(raw) == expected.lower(), "Level changed on disk; refresh the project before modifying it")
        data = json.loads(raw)
        validate(data, self.content)
        return raw, data

    def bundle_snapshot(self):
        self.safe(self.manifest)
        raw = self.manifest.read_bytes()
        data, _ = read_manifest(self.manifest, self.content)
        for row in data["levels"]:
            self.path(row["source"])
        require(self.manifest.read_bytes() == raw, "The game bundle changed during this operation; refresh and retry")
        return data, raw

    def bundle(self):
        return self.bundle_snapshot()[0]

    def entry(self, path, raw, data, bundle):
        row = next((row for row in bundle["levels"] if row["source"] == path.name), None)
        return {"file": path.name, "stem": path.stem, "title": data["title"],
                "source": path.as_posix(), "source_sha256": sha256(raw),
                "in_bundle": row is not None, "bundle_id": row["id"] if row else None,
                "test_starts": copy.deepcopy(data.get("test_starts", []))}

    def catalog(self):
        bundle, bundle_raw = self.bundle_snapshot()
        levels, warnings = [], []
        for path in sorted(self.content.iterdir(), key=lambda p: p.name.casefold()):
            if path.suffix != ".json" or not FILENAME.fullmatch(path.name) or path == self.manifest:
                continue
            try:
                self.path(path.name)
                raw = path.read_bytes()
                require(len(raw) <= 8 * 1024 * 1024, "Level JSON exceeds 8 MiB")
                data = json.loads(raw)
                if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 2:
                    continue
                validate(data, self.content)
                levels.append(self.entry(path, raw, data, bundle))
            except (ValueError, OSError, KeyError, TypeError) as error:
                warnings.append({"file": path.name, "message": str(error)})
        return {"ok": True, "levels": levels,
                "bundle": {"source": self.manifest.as_posix(), "title": bundle["title"],
                           "source_sha256": sha256(bundle_raw),
                           "levels": bundle["levels"], "max_levels": MAX_LEVELS}, "warnings": warnings}

    def publish(self, changes, expected):
        """Replace files individually, restoring every prior byte on an error.

        All payloads and rollback copies are prepared before publication. The
        project lock serializes cooperating readers and writers; expected bytes
        additionally catch edits made outside the editor while validation ran.
        """
        changes = {self.safe(path): payload for path, payload in changes.items()}
        for path, old in expected.items():
            self.safe(path)
            actual = path.read_bytes() if path.exists() else None
            require(actual == old, "Project files changed during this operation; refresh and retry")
        originals = {path: path.read_bytes() if path.exists() else None for path in changes}
        for path in changes:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.safe(path)
        staging = self.safe(self.editor / "transactions")
        staging.mkdir(parents=True, exist_ok=True)
        temporary = self.safe(Path(tempfile.mkdtemp(prefix="publish-", dir=staging)))
        retain_backup = False
        try:
            staged, backups = {}, {}
            recovery = []
            for index, (path, payload) in enumerate(changes.items()):
                if payload is not None:
                    staged[path] = temporary / f"new-{index}"
                    staged[path].write_bytes(payload)
                if originals[path] is not None:
                    backups[path] = temporary / f"old-{index}"
                    backups[path].write_bytes(originals[path])
                recovery.append({"target": path.relative_to(self.root).as_posix(),
                                 "original": f"old-{index}" if originals[path] is not None else None})
            (temporary / "recovery.json").write_bytes(json_bytes({"files": recovery,
                "note": "Original bytes for an interrupted publication. Restore each original to target; an original of null means the target did not exist before this operation."}))
            for path, old in expected.items():
                require((path.read_bytes() if path.exists() else None) == old,
                        "Project files changed during this operation; refresh and retry")
            completed = []
            try:
                for path, payload in changes.items():
                    self.safe(path)
                    require((path.read_bytes() if path.exists() else None) == originals[path],
                            "Project files changed during publication; refresh and retry")
                    if payload is None:
                        if path.exists():
                            path.unlink()
                    else:
                        os.replace(staged[path], path)
                    completed.append(path)
            except BaseException as failure:
                try:
                    for path in reversed(completed):
                        self.safe(path)
                        if path in backups:
                            # Keep the backup until the entire rollback succeeds.
                            restore = temporary / "restore"
                            shutil.copy2(backups[path], restore)
                            os.replace(restore, path)
                        elif path.exists():
                            path.unlink()
                except BaseException as rollback_error:
                    retain_backup = True
                    raise OSError(f"Publication failed ({failure}) and automatic rollback failed ({rollback_error}). Original files and recovery.json were retained at {temporary.as_posix()}") from failure
                raise
        finally:
            if not retain_backup:
                # This exact directory was allocated by this operation, directly
                # beneath the checked transaction directory; never remove a
                # caller-provided or computed scene directory recursively.
                require(self.safe(temporary).parent == staging, "Unsafe transaction cleanup path")
                shutil.rmtree(temporary)

    def membership(self, bundle, path, included):
        require(type(included) is bool, "Bundle membership must be true or false")
        result = copy.deepcopy(bundle)
        existing = next((row for row in result["levels"] if row["source"] == path.name), None)
        if included and existing is None:
            require(len(result["levels"]) < MAX_LEVELS,
                    f"The game bundle is full ({MAX_LEVELS} levels). Create this level with In game menu unchecked, or remove another level from the menu first.")
            used = {row["id"].casefold() for row in result["levels"]}
            ident, suffix = path.stem, 2
            while ident.casefold() in used:
                tail = f"-{suffix}"
                ident = path.stem[:64-len(tail)] + tail
                suffix += 1
            result["levels"].append({"id": ident, "source": path.name})
        elif not included and existing is not None:
            require(len(result["levels"]) > 1, "Keep at least one level in the game menu")
            result["levels"].remove(existing)
        return result

    def result(self, path):
        catalog = self.catalog()
        entry = next(row for row in catalog["levels"] if row["file"] == path.name)
        return {"ok": True, "level": entry, "catalog": catalog}

    def list(self):
        with self.lock():
            return self.catalog()

    def create(self, name, title, in_bundle=True):
        with self.lock():
            path = self.path(name, new=True)
            bundle, bundle_raw = self.bundle_snapshot()
            data = starter_level(title)
            validate(data, self.content)
            updated = self.membership(bundle, path, in_bundle)
            self.publish({path: json_bytes(data), self.manifest: json_bytes(updated)},
                         {path: None, self.manifest: bundle_raw})
            return self.result(path)

    def duplicate(self, level, name, title, expected_sha256, in_bundle=True):
        with self.lock():
            require(isinstance(expected_sha256, str), "Expected source SHA-256 is required")
            source, path = self.path(level), self.path(name, new=True)
            raw, data = self.read_level(source, expected_sha256)
            bundle, bundle_raw = self.bundle_snapshot()
            data["title"] = title
            validate(data, self.content)
            updated = self.membership(bundle, path, in_bundle)
            self.publish({path: json_bytes(data), self.manifest: json_bytes(updated)},
                         {source: raw, path: None, self.manifest: bundle_raw})
            return self.result(path)

    def update(self, level, expected_sha256, title=None, in_bundle=None):
        with self.lock():
            require(isinstance(expected_sha256, str), "Expected source SHA-256 is required")
            path = self.path(level)
            raw, data = self.read_level(path, expected_sha256)
            bundle, bundle_raw = self.bundle_snapshot()
            if title is not None:
                data["title"] = title
            validate(data, self.content)
            updated = self.membership(bundle, path, in_bundle) if in_bundle is not None else bundle
            self.publish({path: json_bytes(data) if title is not None else raw,
                          self.manifest: json_bytes(updated)}, {path: raw, self.manifest: bundle_raw})
            return self.result(path)

    def delete(self, level, expected_sha256):
        with self.lock():
            require(isinstance(expected_sha256, str), "Expected source SHA-256 is required")
            path = self.path(level)
            raw, data = self.read_level(path, expected_sha256)
            require(len(self.catalog()["levels"]) > 1, "Keep at least one level in the project")
            bundle, bundle_raw = self.bundle_snapshot()
            entry = self.entry(path, raw, data, bundle)
            updated = self.membership(bundle, path, False)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:12]
            backup = self.safe(self.editor / "deleted-levels" / stamp / path.name)
            recovery = {"source": f"content/{path.name}", "source_sha256": sha256(raw),
                        "bundle_entry": next((row for row in bundle["levels"] if row["source"] == path.name), None),
                        "note": "Restore this JSON to content/ and, if desired, restore bundle_entry in content/level_bundle.json. Shared assets were retained."}
            self.publish({backup: raw, backup.parent / "recovery.json": json_bytes(recovery),
                          self.manifest: json_bytes(updated), path: None},
                         {path: raw, self.manifest: bundle_raw, backup: None})
            return {"ok": True, "level": entry, "backup": backup.as_posix(), "catalog": self.catalog()}

    def cook(self, level, expected_sha256=None, staged=None):
        with self.lock():
            path = self.path(level)
            raw, _ = self.read_level(path, expected_sha256)
            candidate = self.safe(Path(staged).absolute()) if staged is not None else path
            if staged is not None:
                require(candidate != path and not candidate.is_relative_to(self.content),
                        "Staged level JSON must be a separate file outside content and inside this project")
            candidate_raw, _ = self.read_level(candidate)
            output = self.safe(self.root / "build" if path.stem == "first_room" else self.root / "build/scenes" / path.stem)
            asset_root = self.safe(self.root / "build/project/editor-assets")
            staging = self.safe(self.editor / "cooking")
            staging.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="cook-", dir=staging) as temporary:
                temporary = Path(temporary)
                snapshot = temporary / "source.json"
                snapshot.write_bytes(candidate_raw)
                cooked = temporary / "output"
                report = compile_level(snapshot, cooked, self.content)
                changes = {output / item.relative_to(cooked): item.read_bytes()
                           for item in cooked.rglob("*") if item.is_file()}
                preview = json.loads((cooked / "editor-assets/levels/first_room.json").read_bytes())
                texture_folder = cooked / "editor-assets/textures"
                texture_names = {item.name: f"{item.stem}-{sha256(item.read_bytes())[:16]}.png"
                                 for item in texture_folder.iterdir() if item.is_file()} if texture_folder.exists() else {}
                def namespace(value):
                    if isinstance(value, dict):
                        return {key: namespace(child) for key, child in value.items()}
                    if isinstance(value, list):
                        return [namespace(child) for child in value]
                    if isinstance(value, str):
                        for folder in ("meshes", "textures"):
                            prefix = f"file://{folder}/"
                            if value.startswith(prefix):
                                name = value[len(prefix):]
                                # LightEngine's asynchronous material image cache
                                # keys by URI. Content-addressed PNGs make edited
                                # pixels visible without invalidating unchanged
                                # textures on every level switch.
                                if folder == "textures":
                                    require(name in texture_names, "Missing cooked preview texture")
                                    name = texture_names[name]
                                return f"{prefix}{path.stem}/" + name
                    return value
                scene_path = f"levels/{path.stem}.json"
                for folder in ("meshes", "textures", "characters"):
                    source_folder = cooked / "editor-assets" / folder
                    if source_folder.exists():
                        for item in source_folder.iterdir():
                            require(item.is_file(), "Unexpected nested compiler preview output")
                            name = texture_names[item.name] if folder == "textures" else item.name
                            payload = item.read_bytes()
                            if folder == "characters":
                                character = json.loads(payload)
                                for joint in character["joint_meshes"]:
                                    require(joint["uri"].startswith("meshes/") and "/" not in joint["uri"][7:],
                                            "Unexpected character joint preview URI")
                                    joint["uri"] = f"meshes/{path.stem}/" + joint["uri"][7:]
                                payload = json_bytes(character)
                            changes[asset_root / folder / path.stem / name] = payload
                # Scene descriptor appears only after its referenced files exist.
                changes[asset_root / scene_path] = json_bytes(namespace(preview))
                changes = {self.safe(target): payload for target, payload in changes.items()}
                changed_preview_assets = [target.as_posix() for target, payload in changes.items()
                                          if target.is_relative_to(asset_root) and target.suffix in (".gltf", ".png")
                                          and (not target.exists() or target.read_bytes() != payload)]
                changes = {target: payload for target, payload in changes.items()
                           if not target.exists() or target.read_bytes() != payload}
                self.publish(changes, {path: raw, candidate: candidate_raw})
            return {"ok": True, "source": path.as_posix(), "source_sha256": sha256(raw),
                    "output": output.as_posix(), "asset_root": asset_root.as_posix(),
                    "scene_path": scene_path, "staged": staged is not None,
                    "changed_preview_assets": changed_preview_assets,
                    "report": report, "catalog": self.catalog()}


def boolean(value):
    if value.lower() not in ("true", "false"):
        raise argparse.ArgumentTypeError("Expected true or false")
    return value.lower() == "true"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for command in ("create", "duplicate", "update", "delete", "cook"):
        item = commands.add_parser(command)
        if command != "create":
            item.add_argument("--level", required=True)
            item.add_argument("--expected-sha256", required=command not in ("cook",))
        if command in ("create", "duplicate"):
            item.add_argument("--name", required=True)
        if command in ("create", "duplicate", "update"):
            item.add_argument("--title", required=command != "update")
            item.add_argument("--in-bundle", type=boolean, default=None if command == "update" else True)
        if command == "cook":
            item.add_argument("--staged", type=Path)
    args = vars(parser.parse_args(argv))
    root, command = args.pop("root"), args.pop("command")
    try:
        result = getattr(ProjectLevels(root), command)(**args)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
