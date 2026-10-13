"""Compile a level catalog into independent C units and shared ROM assets."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time

try:
    from compile_level import compile_level, identifier, require, validate, atomic_write, character_c, content_limits
    from cook_textures import cook_sprites, texture_memory_summary
    from cook_music import cook_music
    from compile_mission import mission_source, cook_mission, prepare_stream_level
except ModuleNotFoundError:
    from tools.compile_level import compile_level, identifier, require, validate, atomic_write, character_c, content_limits
    from tools.cook_textures import cook_sprites, texture_memory_summary
    from tools.cook_music import cook_music
    from tools.compile_mission import mission_source, cook_mission, prepare_stream_level

ROOT = Path(__file__).resolve().parents[1]
MAX_LEVELS = 8


def replace_directory(source, destination):
    """Windows scanners can briefly hold freshly populated directory handles."""
    for attempt in range(8):
        try:
            os.replace(source, destination)
            return
        except PermissionError as error:
            if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 7:
                raise
            time.sleep(min(.02 * 2 ** attempt, .25))


def read_manifest(path, content_root=None):
    """Only explicit, unique JSON files directly inside the content root qualify."""
    content_root = Path(content_root or ROOT / "content").resolve()
    path = Path(path).resolve()
    require(path.parent == content_root and path.suffix == ".json" and
            re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*", path.stem),
            "Bundle must be a JSON file directly inside content with an ASCII filename")
    data = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(data, dict) and set(data) == {"version", "title", "levels"},
            "Bundle requires exactly version, title and levels")
    require(type(data["version"]) is int and data["version"] == 1, "Bundle version must be 1")
    title = data["title"]
    require(isinstance(title, str) and 1 <= len(title) <= 80 and
            all(32 <= ord(c) < 127 for c in title), "Bundle title needs 1-80 printable ASCII characters")
    require(isinstance(data["levels"], list) and 1 <= len(data["levels"]) <= MAX_LEVELS,
            f"Bundle requires 1-{MAX_LEVELS} levels")
    entries, ids, paths = [], set(), set()
    for entry in data["levels"]:
        require(isinstance(entry, dict) and set(entry) in ({"id", "source"}, {"id", "mission"}),
                "Each bundle level requires id and exactly one of source or mission")
        ident = identifier(entry["id"], "Bundle level id")
        require(ident.casefold() not in ids, f"Duplicate bundle level ID (case-insensitive): {ident}")
        streaming = "mission" in entry
        source = entry["mission"] if streaming else entry["source"]
        pattern = r"[A-Za-z][A-Za-z0-9_-]*\.mission\.json" if streaming else r"[A-Za-z][A-Za-z0-9_-]*\.json"
        require(isinstance(source, str) and re.fullmatch(pattern, source),
                f"{ident}: {'mission' if streaming else 'source'} must name a {'*.mission.json' if streaming else 'JSON'} file directly inside content")
        resolved = (content_root / source).resolve()
        require(resolved.parent == content_root and resolved.is_file(), f"{ident}: missing level or path outside content")
        require(resolved != path, f"{ident}: bundle cannot include itself")
        require(str(resolved).casefold() not in paths, f"Duplicate bundle level source: {source}")
        ids.add(ident.casefold())
        paths.add(str(resolved).casefold())
        if streaming:
            manifest, scene = mission_source(resolved, content_root)
            # Streaming cannot grant the protected host study a Play profile.
            content_limits(json.loads(scene.read_text(encoding="utf-8")), {}, authoring=False, streaming=True)
            entries.append({"id": ident, "source": scene, "mission": resolved})
        else:
            entries.append({"id": ident, "source": resolved})
    return data, entries


def selection(entries, start_level=None, start_preset=None, *, menu=False):
    if start_level is not None:
        identifier(start_level, "Start level ID")
    if start_preset is not None:
        identifier(start_preset, "Test start ID")
    require(not start_preset or not menu or start_level is not None,
            "A bundle --start-preset requires --start-level")
    level_index = 0
    if start_level is not None:
        matches = [i for i, entry in enumerate(entries) if entry["id"] == start_level]
        require(bool(matches), f"Unknown start level ID: {start_level}")
        level_index = matches[0]
    start_index = -1
    if start_preset is not None and start_preset != "default":
        data = json.loads(entries[level_index]["source"].read_text(encoding="utf-8"))
        require(isinstance(data, dict) and isinstance(data.get("test_starts", []), list), "Level test_starts must be an array")
        starts = data.get("test_starts", [])
        matches = [i for i, preset in enumerate(starts) if isinstance(preset, dict) and preset.get("id") == start_preset]
        require(bool(matches), f"Unknown test start ID for {entries[level_index]['id']}: {start_preset}")
        start_index = matches[0]
    return level_index, start_index, bool(menu and start_level is None)


def union_assets(levels, output, music=None):
    """Replace only the bundle's package directory with an exact, checked union.

    Source level cooks remain untouched. Removed sprites and lighting bakes
    cannot leak into DragonFS after a level or recipe changes.
    """
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    artifacts, records, lighting_records, mission_records = {}, {}, {}, {}
    for level in levels:
        cooked = Path(level["cooked_dir"]).resolve()
        for record in level["textures"]["textures"]:
            relative = record["sprite_path"]
            require(isinstance(relative, str) and re.fullmatch(r"romfs/textures/texture-[0-9a-f]{16}\.sprite", relative),
                    "Invalid bundle texture path")
            source = (cooked / relative).resolve()
            require(source.is_relative_to(cooked / "romfs/textures") and source.is_file(), "Missing bundle sprite")
            payload = source.read_bytes()
            require(hashlib.sha256(payload).hexdigest() == record["sprite_sha256"], "Bundle sprite hash mismatch")
            if relative in artifacts:
                require(artifacts[relative] == payload, f"Bundle texture path collision: {relative}")
                require(records[relative]["width"] == record["width"] and records[relative]["height"] == record["height"],
                        f"Bundle texture metadata collision: {relative}")
            else:
                artifacts[relative], records[relative] = payload, record
        lighting = level.get("content", {}).get("lighting")
        if lighting and lighting.get("path"):
            relative = lighting["path"]
            require(isinstance(relative, str) and re.fullmatch(r"romfs/lighting/lighting-[0-9a-f]{64}\.bin", relative),
                    "Invalid bundle lighting path")
            source = (cooked / relative).resolve()
            require(source.is_relative_to(cooked / "romfs/lighting") and source.is_file(), "Missing bundle lighting bake")
            payload = source.read_bytes()
            require(len(payload) == lighting["bytes"] and hashlib.sha256(payload).hexdigest() == lighting["sha256"],
                    "Bundle lighting hash or size mismatch")
            if relative in artifacts:
                require(artifacts[relative] == payload, f"Bundle lighting path collision: {relative}")
            artifacts[relative], lighting_records[relative] = payload, lighting
        mission = level.get("content", {}).get("mission")
        if mission:
            relative = mission["path"]
            require(isinstance(relative, str) and re.fullmatch(r"romfs/missions/mission-[0-9a-f]{64}\.dlm", relative),
                    "Invalid bundle mission path")
            source = (cooked / relative).resolve()
            require(source.is_relative_to(cooked / "romfs/missions") and source.is_file(), "Missing bundle mission")
            payload = source.read_bytes()
            require(len(payload) == mission["bytes"] and hashlib.sha256(payload).hexdigest() == mission["sha256"],
                    "Bundle mission hash or size mismatch")
            require(Path(relative).stem == "mission-" + mission["sha256"], "Bundle mission path must match its content hash")
            if relative in artifacts:
                require(artifacts[relative] == payload, f"Bundle mission path collision: {relative}")
            artifacts[relative], mission_records[relative] = payload, {key: mission[key] for key in ("path", "sha256", "bytes")}
    music = music or {"assets": []}
    music_paths = set()
    for record in music["assets"]:
        relative = record["path"]
        require(re.fullmatch(r"romfs/music/[A-Za-z][A-Za-z0-9_-]*\.xm64", relative), "Invalid music package path")
        payload = Path(record["cooked_path"]).read_bytes()
        require(len(payload) == record["bytes"] and hashlib.sha256(payload).hexdigest() == record["sha256"],
                "Music cache hash or size mismatch")
        require(relative not in artifacts, "Music package collision")
        artifacts[relative] = payload
        music_paths.add(relative)
    with tempfile.TemporaryDirectory(prefix="package-", dir=output) as temporary:
        stage = Path(temporary) / "romfs"
        stage.mkdir()
        for relative, payload in artifacts.items():
            target = stage / Path(relative).relative_to("romfs")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        destination = output / "romfs"
        # Verify the complete resolved deletion target before a recursive delete.
        require(destination.resolve() == destination and destination.parent == output,
                "Bundle package destination escapes its output directory")
        # Keep the old package outside TemporaryDirectory's cleanup until the
        # new directory is published. A failed publish restores it; failed
        # rollback retains it at the explicitly reported recovery path.
        previous = output / (Path(temporary).name + "-previous")
        require(previous.resolve() == previous and previous.parent == output and not previous.exists(),
                "Bundle rollback destination escapes its output directory")
        had_previous = destination.exists()
        if had_previous:
            replace_directory(destination, previous)
        try:
            replace_directory(stage, destination)
        except BaseException as failure:
            if had_previous:
                try:
                    replace_directory(previous, destination)
                except OSError as rollback_error:
                    raise OSError(f"Bundle publication failed ({failure}); rollback failed ({rollback_error}). Previous ROM assets are retained at {previous}") from failure
            raise
        if had_previous:
            require(previous.resolve() == previous and previous.parent == output, "Unsafe bundle rollback cleanup path")
            shutil.rmtree(previous)
    textures = [records[key] for key in sorted(records)]
    texture_report = {"version": 1, "textures": textures,
            **texture_memory_summary(textures),
            "sprite_bytes": sum(t["sprite_bytes"] for t in textures),
            "maximum_level_decoded_bytes": max((level["textures"]["decoded_bytes"] for level in levels), default=0),
            "maximum_level_palette_bytes": max((sum(t.get("palette_bytes", 0) for t in level["textures"]["textures"])
                                                  for level in levels), default=0),
            "maximum_level_decoded_total_bytes": max((sum(t["decoded_bytes"] + t.get("palette_bytes", 0)
                                                          for t in level["textures"]["textures"])
                                                        for level in levels), default=0),
            "maximum_level_sprite_bytes": max((level["textures"]["sprite_bytes"] for level in levels), default=0)}
    lighting = [lighting_records[key] for key in sorted(lighting_records)]
    assets = [{"path": key, "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
               "kind": "music" if key in music_paths else "mission" if key in mission_records else "lighting" if key in lighting_records else "texture"}
              for key, payload in sorted(artifacts.items())]
    return {"textures": texture_report, "rom_assets": assets,
            "missions": {"assets": [mission_records[key] for key in sorted(mission_records)],
                         "bytes": sum(record["bytes"] for record in mission_records.values())},
            "lighting": {"assets": lighting, "bytes": sum(record["bytes"] for record in lighting),
                         "maximum_level_bytes": max((record["bytes"] for record in lighting), default=0)}}


def prepare_bundle(entries, title, output, sdk, *, start_level=None, start_preset=None,
                   menu=False, cooked_dirs=None):
    """Cook levels separately so their static generated symbols never collide."""
    output = Path(output).resolve()
    require(1 <= len(entries) <= MAX_LEVELS, f"Bundle requires 1-{MAX_LEVELS} levels")
    ids = [identifier(entry["id"], "Bundle level id").casefold() for entry in entries]
    require(len(set(ids)) == len(ids), "Duplicate bundle level ID (case-insensitive)")
    if cooked_dirs is not None:
        require(len(cooked_dirs) == len(entries), "Each bundle level needs its own cook directory")
        directories = [str(Path(path).resolve()).casefold() for path in cooked_dirs]
        require(len(set(directories)) == len(directories), "Bundle cook directories must be distinct")
    initial_level, initial_start, start_in_menu = selection(entries, start_level, start_preset, menu=menu)
    # Validate the whole catalog before publishing any generated source.
    for entry in entries:
        if "mission" in entry:
            manifest, source = mission_source(entry["mission"])
            require(source == Path(entry["source"]).resolve(), "Bundle mission source differs from its manifest")
            cook_mission(manifest, Path(entry["mission"]).parent)
        else:
            source = Path(entry["source"])
            validate(json.loads(source.read_text(encoding="utf-8")), source.parent)
    levels, units, shared_characters = [], [], {}
    generated = output / "generated"
    for index, entry in enumerate(entries):
        source = Path(entry["source"]).resolve()
        # Index qualification also avoids Windows device-name directories such
        # as CON and keeps storage identity independent from display IDs.
        cooked = Path(cooked_dirs[index] if cooked_dirs else output / "levels" / f"{index}-{entry['id']}").resolve()
        if "mission" in entry:
            report = prepare_stream_level(entry["mission"], cooked)
            textures = cook_sprites(cooked, sdk)
        else:
            report = compile_level(source, cooked, shared_characters=shared_characters)
            textures = cook_sprites(cooked, sdk)
        unit = generated / "levels" / str(index) / f"bundle_level_{index}.c"
        # An explicit relative include is independent of global -I order and
        # cannot silently select another level's same-named demo_level.h.
        include = Path(os.path.relpath(cooked / "generated/demo_level.h", unit.parent)).as_posix()
        atomic_write(unit, f'#include {json.dumps(include)}\nconst DlLevel *dl_get_level_{index}(void) {{ return &dl_demo_level; }}\n')
        units.append(unit)
        levels.append({"id": entry["id"], "source": str(source), "cooked_dir": str(cooked),
                       "content": report, "textures": textures,
                       **({"mission_manifest": str(entry["mission"])} if "mission" in entry else {})})
    if shared_characters:
        unit = generated / "shared_characters.c"
        atomic_write(unit, '#include <stddef.h>\n#include "animation.h"\n'+
                     "\n".join(character_c(c, shared=True) for c in shared_characters.values())+"\n")
        units.append(unit)
    music = cook_music(levels, output, sdk)
    package = union_assets(levels, output, music)
    package["music"] = music
    textures = package["textures"]
    header = generated / "bundle.h"
    atomic_write(header, '#ifndef DL_GENERATED_BUNDLE_H\n#define DL_GENERATED_BUNDLE_H\n#include "launch.h"\n#include "dl_music.h"\nextern const DlBundle dl_bundle;\nextern const DlMusicCue dl_level_music[];\n#endif\n')
    declarations = "\n".join(f"extern const DlLevel *dl_get_level_{i}(void);" for i in range(len(levels)))
    rows = ",\n".join("    {" + json.dumps(level["id"]) + "," + json.dumps(level["content"]["title"]) +
                        f",dl_get_level_{i}" + "}" for i, level in enumerate(levels))
    descriptor = ('#include "bundle.h"\n' + declarations + '\nstatic const DlLevelEntry dl_bundle_levels[] = {\n' + rows + '\n};\n' +
                  'const DlBundle dl_bundle = {\n' + f'    .title={json.dumps(title)}, .levels=dl_bundle_levels, .level_count={len(levels)},\n' +
                  f'    .initial_level={initial_level}, .initial_start={initial_start}, .start_in_menu={str(start_in_menu).lower()},\n' +
                  f'    .has_rom_assets={str(bool(package["rom_assets"])).lower()}\n' + '};\n')
    cue_rows = [('{'+json.dumps(cue["id"])+','+json.dumps(cue["path"])+f',{float(cue["gain"]):.6f}f'+'}')
                if cue else '{0,0,0}' for cue in music["cues"]]
    descriptor += 'const DlMusicCue dl_level_music[] = {\n    ' + ',\n    '.join(cue_rows) + '\n};\n'
    unit = generated / "bundle.c"
    atomic_write(unit, descriptor)
    units.append(unit)
    report = {"version": 1, "title": title, "levels": levels, "initial_level": initial_level,
              "initial_start": initial_start, "start_in_menu": start_in_menu, **package,
              "resident_geometry_bytes": sum(level["content"]["compiled_geometry_bytes"] for level in levels),
              "resident_bootstrap_geometry_bytes": sum(level["content"]["compiled_geometry_bytes"] for level in levels if level["content"].get("experimental_stream")),
              "stream_rom_bytes": package["missions"]["bytes"],
              "memory_note": "Normal level geometry and all bootstrap descriptors/shared visuals are resident. Stream mission architecture/colliders are ROM chunks. Only active-level sprites are loaded. Directory, stream banks/slots, renderer, global game state, audio and display buffers are additional; these geometry and ROM counts are not a total RDRAM measurement."}
    duplicated_character_geometry = sum(sum({c["source_sha256"]: c["geometry_bytes"]
                                             for c in level["content"].get("characters", {}).values()}.values())
                                        for level in levels)
    unique_character_geometry = sum(c["report"]["geometry_bytes"] for c in shared_characters.values())
    report["resident_geometry_bytes"] += unique_character_geometry-duplicated_character_geometry
    report["characters"] = {"unique_assets": len(shared_characters),
                            "shared_geometry_bytes": unique_character_geometry,
                            "geometry_duplication_avoided_bytes": duplicated_character_geometry-unique_character_geometry,
                            "shared_key_bytes": sum(c["encoded_bytes"] for c in shared_characters.values()),
                            "assets": [c["report"] for c in shared_characters.values()],
                            "memory_note": "Character geometry, skeletons, clips and sockets are emitted once per content hash in a dedicated C unit, shared across levels and actors. Level mesh descriptors and actor render caches remain per instance/level."}
    atomic_write(generated / "bundle_report.json", json.dumps(report, indent=2) + "\n")
    return report, units
