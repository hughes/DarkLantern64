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
import time

try:
    from compile_level import compile_level, validate, transform_point
    from compile_bundle import MAX_LEVELS, read_manifest
    import editor_cache
    from character_assets import configure_editor_cache
    from content_profile import content_limits, is_study, is_streamed, STUDY_LIMITS, STUDY_PLAY_REASON
    from compile_mission import mission_source, cook_mission, validate_stream_bridge, collider_corners
except ModuleNotFoundError:
    from tools.compile_level import compile_level, validate, transform_point
    from tools.compile_bundle import MAX_LEVELS, read_manifest
    from tools import editor_cache
    from tools.character_assets import configure_editor_cache
    from tools.content_profile import content_limits, is_study, is_streamed, STUDY_LIMITS, STUDY_PLAY_REASON
    from tools.compile_mission import mission_source, cook_mission, validate_stream_bridge, collider_corners

ROOT = Path(__file__).resolve().parents[1]
FILENAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\.json")
RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])", re.IGNORECASE)
STREAM_NATIVE_REASON = ("Native Play for stream missions is not implemented yet. The editor shows the complete authoring scene; "
                       "use Run level in Ares to exercise the ROM chunk loader. Native Play must use the same portable mission loader and working-scene adapter before it is enabled.")


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
        self.cache = self.safe(self.editor / "cache")
        configure_editor_cache(self.cache / "characters")
        # Include the cooker and native lighting inputs, not just the level JSON.
        code_root = Path(__file__).resolve().parents[1]
        code_files = sorted((code_root / "tools").glob("*.py"))
        code_files += sorted(p for p in (code_root / "src").rglob("*") if p.suffix in (".c", ".h", ".inc", ".def"))
        self.code_key = sha256(json_bytes(editor_cache.fingerprint(code_files)))

    def validate_cached(self, path, raw, data):
        limits = content_limits(data, {}, authoring=True)
        require(len(raw) <= limits['level_bytes'], f"Level JSON exceeds {limits['level_bytes']//(1024*1024)} MiB")
        cache = self.safe(self.cache / "validation" / (sha256(str(path).encode()) + ".json"))
        key = [self.code_key, sha256(raw)]
        cached = editor_cache.read(cache, key)
        if cached is not None:
            return cached["dependencies"]
        state = validate(data, self.content, authoring=True)
        dependencies = [str(p.resolve()) for _, p in state["dependencies"]]
        editor_cache.write(cache, key, {"dependencies": dependencies}, dependencies)
        return dependencies

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
        require(len(raw) <= STUDY_LIMITS['level_bytes'], "Level JSON exceeds 128 MiB")
        if expected is not None:
            require(re.fullmatch(r"[a-fA-F0-9]{64}", expected) is not None, "Expected source SHA-256 must contain 64 hexadecimal characters")
            require(sha256(raw) == expected.lower(), "Level changed on disk; refresh the project before modifying it")
        data = json.loads(raw)
        self.validate_cached(path, raw, data)
        return raw, data

    def bundle_snapshot(self):
        self.safe(self.manifest)
        raw = self.manifest.read_bytes()
        data, entries = read_manifest(self.manifest, self.content)
        for row in entries:
            self.path(row["source"])
            if "mission" in row:
                self.safe(row["mission"])
        require(self.manifest.read_bytes() == raw, "The game bundle changed during this operation; refresh and retry")
        return data, raw

    def bundle(self):
        return self.bundle_snapshot()[0]

    def mission_for(self, path):
        """A partition remains associated with its scene when removed from menu."""
        found = []
        for candidate in sorted(self.content.glob("*.mission.json")):
            self.safe(candidate)
            raw = candidate.read_bytes()
            try:
                manifest, source = mission_source(candidate, self.content)
            except (ValueError, OSError, KeyError, TypeError):
                # Unrelated broken manifests should not hide valid scenes.
                continue
            if source == path:
                require(candidate.read_bytes() == raw and json.loads(raw) == manifest,
                        "Mission manifest changed during discovery; refresh and retry")
                found.append(dict(path=candidate, raw=raw, document=manifest))
        require(len(found) <= 1, "A canonical scene must have only one mission partition manifest")
        return found[0] if found else None

    def bundle_row(self, bundle, path):
        for row in bundle["levels"]:
            if row.get("source") == path.name:
                return row
            if "mission" in row:
                _, source = mission_source(self.safe(self.content / row["mission"]), self.content)
                if source == path:
                    return row
        return None

    def stream_metadata(self, path, data, bundle, partition=None):
        partition = partition or self.mission_for(path)
        if partition is None:
            return None
        row = self.bundle_row(bundle, path)
        status = {"valid": False, "portable_valid": False, "rom_valid": False, "error": ""}
        report = None
        try:
            _, report = cook_mission(partition["document"], self.content, source_document=data)
            status = dict(valid=False, portable_valid=True, rom_valid=False, error="", directory_bytes=report["directory_bytes"], package_bytes=report["package_bytes"],
                          peak_neighbor_overlap_chunk_bytes=report["memory"]["peak_neighbor_overlap_chunk_bytes"],
                          max_resident_regions=report["max_resident_regions"],
                          unknown_costs=report["memory"]["unknown_costs"])
            validate_stream_bridge(report)
            status.update(valid=True, rom_valid=True)
        except (ValueError, OSError, KeyError, TypeError) as error:
            status["error"] = str(error)
        return dict(mission_manifest=partition["path"].as_posix(), mission_manifest_sha256=sha256(partition["raw"]),
                    mission_document=copy.deepcopy(partition["document"]), mission_text=partition["raw"].decode("utf-8"),
                    visibility=partition["document"].get("visibility", "door-distance"),
                    bundle_id=row["id"] if row and "mission" in row else None,
                    native_play_supported=False, native_play_reason=STREAM_NATIVE_REASON,
                    preview_mode="Complete authoring scene; streaming residency and visibility are not simulated",
                    regions=copy.deepcopy(partition["document"].get("regions", [])),
                    working_sets=copy.deepcopy(report["working_sets"]) if report else [], status=status)

    def entry(self, path, raw, data, bundle):
        row = self.bundle_row(bundle, path)
        streaming = self.stream_metadata(path, data, bundle)
        return {"file": path.name, "stem": path.stem, "title": data["title"],
                "source": path.as_posix(), "source_sha256": sha256(raw),
                "in_bundle": row is not None, "bundle_id": row["id"] if row else None,
                "authoring_profile": data.get('authoring_profile'), "playable": not is_study(data),
                "runtime_format": "stream-mission-v1" if streaming else "resident-level",
                "native_play_supported": not (is_study(data) or is_streamed(data)) and streaming is None,
                "streaming": streaming,
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
                require(len(raw) <= STUDY_LIMITS['level_bytes'], "Level JSON exceeds 128 MiB")
                data = json.loads(raw)
                if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 2:
                    continue
                self.validate_cached(path, raw, data)
                levels.append(self.entry(path, raw, data, bundle))
            except (ValueError, OSError, KeyError, TypeError) as error:
                warnings.append({"file": path.name, "message": str(error)})
        return {"ok": True, "levels": levels,
                "bundle": {"source": self.manifest.as_posix(), "title": bundle["title"],
                           "source_sha256": sha256(bundle_raw),
                           "levels": bundle["levels"], "max_levels": MAX_LEVELS}, "warnings": warnings}

    def publish(self, changes, expected, *, cleanup_warnings=None):
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
        publication_complete = False
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
                publication_complete = True
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
                try:
                    shutil.rmtree(temporary)
                except OSError as error:
                    if not publication_complete or cleanup_warnings is None:
                        raise
                    cleanup_warnings.append(f"Authoring saved; transaction cleanup failed: {error}")

    def membership(self, bundle, path, included, data=None, mission=None):
        require(type(included) is bool, "Bundle membership must be true or false")
        require(not included or not is_study(data), STUDY_PLAY_REASON)
        result = copy.deepcopy(bundle)
        existing = self.bundle_row(result, path)
        partition = self.mission_for(path) if path.exists() and mission is None else None
        mission = mission or (partition["path"].name if partition else None)
        if included and existing is None:
            require(len(result["levels"]) < MAX_LEVELS,
                    f"The game bundle is full ({MAX_LEVELS} levels). Create this level with In game menu unchecked, or remove another level from the menu first.")
            used = {row["id"].casefold() for row in result["levels"]}
            ident, suffix = path.stem, 2
            while ident.casefold() in used:
                tail = f"-{suffix}"
                ident = path.stem[:64-len(tail)] + tail
                suffix += 1
            result["levels"].append({"id": ident, "mission": mission} if mission else {"id": ident, "source": path.name})
        elif included and existing is not None and mission:
            # An author can attach a new partition to an existing resident
            # scene without changing its saved menu identity.
            existing.pop("source", None)
            existing["mission"] = mission
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
            validate(data, self.content, authoring=True)
            updated = self.membership(bundle, path, in_bundle, data)
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
            validate(data, self.content, authoring=True)
            partition = self.mission_for(source)
            changes, expected = {path: json_bytes(data)}, {source: raw, path: None, self.manifest: bundle_raw}
            mission_name = None
            if partition:
                mission_name = path.stem + ".mission.json"
                mission_path = self.safe(self.content / mission_name)
                require(not any(item.name.casefold() == mission_name.casefold() for item in self.content.iterdir()),
                        "A mission manifest with that duplicate name already exists")
                copied = copy.deepcopy(partition["document"])
                copied["source"] = path.name
                changes[mission_path] = json_bytes(copied)
                expected.update({partition["path"]: partition["raw"], mission_path: None})
            updated = self.membership(bundle, path, in_bundle, data, mission=mission_name)
            changes[self.manifest] = json_bytes(updated)
            self.publish(changes, expected)
            return self.result(path)

    def update(self, level, expected_sha256, title=None, in_bundle=None):
        with self.lock():
            require(isinstance(expected_sha256, str), "Expected source SHA-256 is required")
            path = self.path(level)
            raw, data = self.read_level(path, expected_sha256)
            bundle, bundle_raw = self.bundle_snapshot()
            if title is not None:
                data["title"] = title
            validate(data, self.content, authoring=True)
            updated = self.membership(bundle, path, in_bundle, data) if in_bundle is not None else bundle
            partition = self.mission_for(path)
            self.publish({path: json_bytes(data) if title is not None else raw,
                          self.manifest: json_bytes(updated)}, {path: raw, self.manifest: bundle_raw,
                          **({partition["path"]: partition["raw"]} if partition else {})})
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
                        "bundle_entry": self.bundle_row(bundle, path),
                        "note": "Restore this JSON to content/ and, if desired, restore bundle_entry in content/level_bundle.json. Shared assets were retained."}
            changes = {backup: raw, self.manifest: json_bytes(updated), path: None}
            expected = {path: raw, self.manifest: bundle_raw, backup: None}
            partition = self.mission_for(path)
            if partition:
                manifest_backup = backup.parent / partition["path"].name
                changes.update({manifest_backup: partition["raw"], partition["path"]: None})
                expected.update({manifest_backup: None, partition["path"]: partition["raw"]})
                recovery.update(mission_source="content/" + partition["path"].name,
                                mission_sha256=sha256(partition["raw"]), mission_backup=manifest_backup.name)
                recovery["note"] = "Restore both scene and mission manifest to content/, then restore bundle_entry if desired. Shared assets were retained."
            changes[backup.parent / "recovery.json"] = json_bytes(recovery)
            self.publish(changes, expected)
            return {"ok": True, "level": entry, "backup": backup.as_posix(), "catalog": self.catalog()}

    def save_pair(self, level, expected_sha256, staged, expected_mission_sha256, staged_mission):
        """Commit stream scene, partition and prepared preview under one CAS/rollback."""
        return self.cook(level, expected_sha256, staged, staged_mission=staged_mission,
                         expected_mission_sha256=expected_mission_sha256, publish_pair=True)

    def fit_region_bounds(self, level, region, staged, staged_mission, expected_mission_sha256,
                          expected_sha256=None):
        """Return a bounds repair draft; never publish authoring files."""
        with self.lock():
            path = self.path(level)
            self.read_level(path, expected_sha256)
            partition = self.mission_for(path)
            require(partition is not None and sha256(partition["raw"]) == expected_mission_sha256,
                    "Mission partition changed on disk; reload or reconcile before fitting bounds")
            scene_stage, mission_stage = self.safe(Path(staged).absolute()), self.safe(Path(staged_mission).absolute())
            require(scene_stage != mission_stage and not scene_stage.is_relative_to(self.content) and
                    not mission_stage.is_relative_to(self.content), "Bounds repair requires separate staged drafts outside content")
            _, data = self.read_level(scene_stage)
            draft = json.loads(mission_stage.read_bytes())
            require(draft.get("source") == path.name, "Draft partition source differs from this scene")
            rows = [row for row in draft["regions"] if row["id"] == region]
            require(len(rows) == 1, "Choose one existing region")
            state = validate(data, self.content, authoring=True)
            boxes = {box["id"]: box for box in state["colliders"]}
            floors = {row["id"]: row for row in state.get("floors", [])}
            points = []
            for ident in rows[0]["entities"]:
                require(ident in state["by_id"], "Region contains an unknown entity: " + ident)
                entity = state["by_id"][ident]
                points.append(entity["transform"]["position"])
                if "model" in entity:
                    points.extend(transform_point(p, entity["transform"]) for p in state["meshes"][entity["model"]]["vertices"])
                if ident in boxes:
                    points.extend(collider_corners(boxes[ident]))
                if ident in floors:
                    points.extend(floors[ident]["vertices"])
            require(points, "Assign an object before fitting region bounds")
            rows[0]["bounds"] = [[min(p[axis] for p in points)-.01 for axis in range(3)],
                                 [max(p[axis] for p in points)+.01 for axis in range(3)]]
            return dict(ok=True, mission_document=draft, region=region)

    def cook(self, level, expected_sha256=None, staged=None, *, staged_mission=None,
             expected_mission_sha256=None, publish_pair=False):
        started = time.perf_counter()
        with self.lock():
            path = self.path(level)
            raw, _ = self.read_level(path, expected_sha256)
            candidate = self.safe(Path(staged).absolute()) if staged is not None else path
            if staged is not None:
                require(candidate != path and not candidate.is_relative_to(self.content),
                        "Staged level JSON must be a separate file outside content and inside this project")
            candidate_raw, candidate_data = self.read_level(candidate)
            bundle, bundle_raw = self.bundle_snapshot()
            partition = self.mission_for(path)
            saved_partition = partition
            mission_candidate = None
            if expected_mission_sha256 is not None:
                require(partition is not None, "Scene has no associated mission partition")
                require(isinstance(expected_mission_sha256, str) and
                        re.fullmatch(r"[a-fA-F0-9]{64}", expected_mission_sha256), "Expected mission SHA-256 must contain 64 hexadecimal characters")
                require(sha256(partition["raw"]) == expected_mission_sha256.lower(),
                        "Mission partition changed on disk; edits are retained, refresh or reconcile before saving")
            if staged_mission is not None:
                require(partition is not None and expected_mission_sha256 is not None,
                        "A staged mission requires its associated saved manifest SHA-256")
                mission_candidate = self.safe(Path(staged_mission).absolute())
                require(mission_candidate != candidate and not mission_candidate.is_relative_to(self.content),
                        "Staged mission must be a separate file outside content and inside this project")
                mission_raw = mission_candidate.read_bytes()
                require(len(mission_raw) <= 1024 * 1024, "Mission manifest exceeds 1 MiB")
                mission_document = json.loads(mission_raw)
                require(isinstance(mission_document, dict) and mission_document.get("source") == path.name and
                        mission_document.get("version") == 1, "Draft mission must retain this canonical source and version")
                partition = dict(path=partition["path"], raw=mission_raw, document=mission_document)
            if publish_pair:
                require(staged is not None and mission_candidate is not None and expected_sha256 is not None,
                        "Paired save requires both staged files and both saved SHA-256 identities")
            streaming = self.stream_metadata(path, candidate_data, bundle, partition)
            output = self.safe(self.root / "build" if path.stem == "first_room" else self.root / "build/scenes" / path.stem)
            asset_root = self.safe(self.root / "build/project/editor-assets")
            cache = self.safe(self.cache / "cooked" / path.name)
            key = [self.code_key, sha256(raw), sha256(candidate_raw), staged is not None,
                   sha256(bundle_raw), sha256(partition["raw"]) if partition else None,
                   sha256(saved_partition["raw"]) if saved_partition else None, publish_pair]
            cached = editor_cache.read(cache, key) if not publish_pair else None
            if cached is not None:
                cached.update(catalog=self.catalog(), changed_preview_assets=[], cache_hit=True)
                cached["prepare_ms"] = round((time.perf_counter()-started)*1000, 2)
                return cached
            staging = self.safe(self.editor / "cooking")
            staging.mkdir(parents=True, exist_ok=True)
            warnings = []
            with tempfile.TemporaryDirectory(prefix="cook-", dir=staging, ignore_cleanup_errors=publish_pair) as temporary:
                temporary = Path(temporary)
                snapshot = temporary / "source.json"
                snapshot.write_bytes(candidate_raw)
                cooked = temporary / "output"
                report = compile_level(snapshot, cooked, self.content, authoring=True)
                if streaming:
                    report.update(playable=False, native_play_supported=False, streaming=streaming,
                                  preview_mode="Complete authoring scene; stream simulation requires the ROM pipeline")
                changes = {output / item.relative_to(cooked): item.read_bytes()
                           for item in cooked.rglob("*") if item.is_file() and
                           (not streaming or item.relative_to(cooked).as_posix() not in ("generated/demo_level.h", "generated/play.json"))}
                if streaming:
                    changes[output / "generated/level_report.json"] = json_bytes(report)
                    for blocked in ("generated/demo_level.h", "generated/play.json"):
                        target = output / blocked
                        if target.exists():
                            changes[target] = None
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
                outputs = [target for target, payload in changes.items() if payload is not None]
                preview_fingerprint = sha256(json_bytes({str(target.relative_to(asset_root)): sha256(payload)
                    for target, payload in changes.items() if target.is_relative_to(asset_root)}))
                changed_preview_assets = [target.as_posix() for target, payload in changes.items()
                                          if target.is_relative_to(asset_root) and target.suffix in (".gltf", ".png")
                                          and (not target.exists() or target.read_bytes() != payload)]
                changes = {target: payload for target, payload in changes.items()
                           if payload is None or not target.exists() or target.read_bytes() != payload}
                expected = {path: raw, candidate: candidate_raw, self.manifest: bundle_raw,
                            **({saved_partition["path"]: saved_partition["raw"]} if saved_partition else {})}
                if mission_candidate is not None:
                    expected[mission_candidate] = partition["raw"]
                if publish_pair:
                    changes[path] = candidate_raw
                    changes[partition["path"]] = partition["raw"]
                self.publish(changes, expected, cleanup_warnings=warnings if publish_pair else None)
            result = {"ok": True, "source": path.as_posix(), "source_sha256": sha256(raw), "source_text": raw.decode("utf-8"),
                    "output": output.as_posix(), "asset_root": asset_root.as_posix(),
                    "scene_path": scene_path, "staged": staged is not None,
                    "changed_preview_assets": changed_preview_assets,
                     "report": report, "preview_fingerprint": preview_fingerprint,
                     "runtime_format": "stream-mission-v1" if streaming else "resident-level",
                     "native_play_supported": streaming is None and not (is_study(candidate_data) or is_streamed(candidate_data)), "streaming": streaming}
            if publish_pair:
                result.update(source_sha256=sha256(candidate_raw), source_text=candidate_raw.decode("utf-8"), saved_pair=True)
            try:
                dependencies = self.validate_cached(candidate, candidate_raw, candidate_data)
                editor_cache.write(cache, key, result, [*dependencies, *outputs, *([partition["path"]] if partition else [])])
            except Exception as error:
                if not publish_pair:
                    raise
                warnings.append(f"Authoring saved; preparation cache update failed: {error}")
            try:
                result["catalog"] = self.catalog()
            except Exception as error:
                if not publish_pair:
                    raise
                warnings.append(f"Authoring saved; project catalog refresh failed: {error}")
            result.update(cache_hit=False, prepare_ms=round((time.perf_counter()-started)*1000, 2))
            if warnings:
                result["warnings"] = warnings
            return result


def boolean(value):
    if value.lower() not in ("true", "false"):
        raise argparse.ArgumentTypeError("Expected true or false")
    return value.lower() == "true"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for command in ("create", "duplicate", "update", "delete", "cook", "save_pair", "fit_region_bounds"):
        item = commands.add_parser(command)
        if command != "create":
            item.add_argument("--level", required=True)
            item.add_argument("--expected-sha256", required=command not in ("cook",))
        if command in ("create", "duplicate"):
            item.add_argument("--name", required=True)
        if command in ("create", "duplicate", "update"):
            item.add_argument("--title", required=command != "update")
            item.add_argument("--in-bundle", type=boolean, default=None if command == "update" else True)
        if command in ("cook", "save_pair", "fit_region_bounds"):
            item.add_argument("--staged", type=Path, required=command != "cook")
            item.add_argument("--staged-mission", type=Path, required=command != "cook")
            item.add_argument("--expected-mission-sha256", required=command != "cook")
        if command == "fit_region_bounds":
            item.add_argument("--region", required=True)
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
