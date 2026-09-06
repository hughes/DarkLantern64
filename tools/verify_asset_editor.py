"""Exercise asset import and prop placement in an isolated LightEngine editor.

Creates a new verification level and a private pack manifest under content/;
archives their JSON beside the viewport capture in .dev/, then removes these
owned fixtures after closing the test editor. Existing levels, editor sessions
and asset files are never modified. Build the scene editor first.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import uuid

from compile_level import compile_level
from editorctl import ROOT, command_queue
from verify_enemy_editor import request


def archive_owned_fixtures(source, fixture, queue):
    """Archive and unlink only the two files created by this verification run."""
    content = (ROOT / "content").resolve()
    source, fixture = source.resolve(), fixture.resolve()
    if source.parent != content or source.suffix != ".json":
        raise ValueError("Refusing to clean a level outside content/")
    if (not fixture.is_relative_to(content / "assets") or fixture.name != "pack.json"
            or not fixture.parent.name.startswith("editor_import_test_")):
        raise ValueError("Refusing to clean a pack outside the owned test asset directory")
    queue.mkdir(parents=True, exist_ok=True)
    archived = {}
    for original, name in ((source, "verification-source.json"), (fixture, "verification-pack.json")):
        if original.is_file():
            destination = queue / name
            destination.write_bytes(original.read_bytes())
            original.unlink()
            archived[name] = str(destination)
    # rmdir only removes an empty owned directory; it never recurses into
    # unexpected files or follows another directory tree.
    if fixture.parent.exists():
        fixture.parent.rmdir()
    return archived


def _exercise(executable, source, pack_uri, fixture, fixture_uri, queue):
    if pack_uri:
        from asset_pack import load_pack
        pack = load_pack(pack_uri)
    else:
        pack = {"version": 1, "assets": [{"id": "mesh-import-test", "uri": "models/block.obj"}],
                "materials": [{"id": "mat-import-test", "color": [1, .8, .2, 1]}],
                "prefabs": [{"id": "import-test", "model": "mesh-import-test",
                             "material": "mat-import-test", "scale": [.25, .25, .25]}]}
    fixture.write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
    data = json.loads((ROOT / "content/first_room.json").read_text())
    data["title"] = "Blender Asset Import Verification"
    source.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    compile_level(source, ROOT / "build/scenes" / source.stem)
    queue.mkdir(parents=True, exist_ok=True)
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    evidence = {"source": str(source), "pack": fixture_uri, "checks": []}
    with (queue / "asset-verification-editor.log").open("w") as log:
        process = subprocess.Popen([str(executable), str(ROOT), str(source)], cwd=ROOT,
                                   stdout=log, stderr=log, startupinfo=startup)
        try:
            initial = request(queue, "inspect")
            assert not initial["dirty"] and initial["prefabs"] == []
            request(queue, "import_asset_pack", {"uri": fixture_uri})
            imported = request(queue, "inspect")
            assert len(imported["prefabs"]) == len(pack["prefabs"])
            again = request(queue, "import_asset_pack", {"uri": fixture_uri})
            assert not again["definitions_changed"]
            request(queue, "import_asset_pack", {"uri": "../pack.json"}, expect_ok=False)
            assert request(queue, "inspect")["document"] == imported["document"]
            conflict = copy.deepcopy(pack)
            conflict["materials"][0]["color"] = [.123, .234, .345, 1]
            fixture.write_text(json.dumps(conflict), encoding="utf-8")
            request(queue, "import_asset_pack", {"uri": fixture_uri}, expect_ok=False)
            failed = request(queue, "inspect")
            assert failed["document"] == imported["document"] and failed["prefabs"] == imported["prefabs"]
            fixture.write_text(json.dumps(pack, indent=2) + "\n", encoding="utf-8")
            evidence["checks"] += ["Asset pack imports all reusable definitions and prefabs",
                "Identical reimport is idempotent", "Conflicting definitions and traversal fail without changing document or catalog"]
            placed = request(queue, "add_prop", {"prefab": pack["prefabs"][0]["id"], "id": "asset-test-prop",
                "position": [-5, 1.5, 1.4], "rotation": [0, 28, 0], "loot_highlight": True})["entity"]
            assert placed["kind"] == "static" and "collider" not in placed
            assert placed["transform"]["scale"] == pack["prefabs"][0]["scale"]
            assert placed["loot_highlight"] is True
            request(queue, "add_prop", {"prefab": pack["prefabs"][0]["id"], "id": "asset-test-prop"}, expect_ok=False)
            request(queue, "add_prop", {"prefab": "unknown"}, expect_ok=False)
            request(queue, "add_prop", {"prefab": pack["prefabs"][0]["id"], "scale": [0, 1, 1]}, expect_ok=False)
            before_invalid = request(queue, "inspect")["document"]
            request(queue, "add_prop", {"prefab": pack["prefabs"][0]["id"], "loot_highlight": 1}, expect_ok=False)
            request(queue, "set_entity", {"id": "asset-test-prop", "patch": {"loot_highlight": "true"}}, expect_ok=False)
            spawn = next(e for e in data["entities"] if e["kind"] == "spawn")
            request(queue, "set_entity", {"id": spawn["id"], "patch": {
                "transform": {"position": [-6, 0, 4]}, "loot_highlight": True}}, expect_ok=False)
            assert request(queue, "inspect")["document"] == before_invalid
            request(queue, "save")
            highlighted = request(queue, "inspect")
            assert highlighted["report"]["ids"]["asset-test-prop"]["loot_highlight"] is True
            header = (Path(highlighted["output"]) / "generated/demo_level.h").read_text()
            assert any('"asset-test-prop"' in line and line.rstrip().endswith("true},") for line in header.splitlines())
            request(queue, "set_entity", {"id": "asset-test-prop", "patch": {"loot_highlight": False}})
            request(queue, "save")
            assert request(queue, "inspect")["report"]["ids"]["asset-test-prop"]["loot_highlight"] is False
            request(queue, "set_entity", {"id": "asset-test-prop", "patch": {"loot_highlight": True}})
            request(queue, "save")
            request(queue, "set_preview_transform", {"id": "asset-test-prop", "transform": {"position": [-5, 1.6, 1.4]}})
            request(queue, "save")
            saved = request(queue, "inspect")
            entity = next(e for e in saved["document"]["entities"] if e["id"] == "asset-test-prop")
            assert all(abs(actual - expected) < 1e-5 for actual, expected in zip(entity["transform"]["position"], [-5, 1.6, 1.4]))
            assert entity["loot_highlight"] is True
            assert not saved["dirty"] and not saved["preview_refresh_pending"]
            evidence["capture"] = request(queue, "capture")
            evidence["checks"] += ["Prefab instantiates a decorative model with its default scale",
                "Boolean loot highlight survives placement, toggle and cook into the runtime model flag",
                "Invalid highlight types and nonstatic targets reject atomically",
                "Invalid, duplicate and unknown placements reject", "Save cooks the new prop; viewport gizmo edits round-trip to canonical JSON"]
            protected = next(e for e in data["entities"] if e["kind"] == "static" and "collider" in e)
            request(queue, "delete_entity", {"id": protected["id"]}, expect_ok=False)
            request(queue, "delete_entity", {"id": "asset-test-prop"})
            request(queue, "save")
            request(queue, "reload")
            assert request(queue, "inspect")["prefabs"] == []
            restored = request(queue, "import_asset_pack", {"uri": fixture_uri})
            assert not restored["definitions_changed"] and not restored["dirty"]
            final = request(queue, "inspect")
            assert final["document"]["entities"] == data["entities"]
            evidence["checks"] += ["Delete removes the prop while protecting colliding level geometry",
                "Reload and reimport restore the catalog without dirtying saved definitions"]
            evidence["counts"] = final["report"]["counts"]
            evidence["source_sha256"] = final["report"]["source_sha256"]
            request(queue, "quit")
            process.wait(timeout=15)
            evidence["passed"] = True
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
            (queue / "asset-verification.json").write_text(json.dumps(evidence, indent=2) + "\n")
    return evidence


def verify(executable, source, pack_uri=None):
    source = source.resolve()
    queue = command_queue(ROOT, source)
    if source.exists():
        raise ValueError("Choose a new filename; existing content will never be overwritten")
    token = uuid.uuid4().hex
    fixture_uri = f"assets/editor_import_test_{token}/pack.json"
    fixture = ROOT / "content" / fixture_uri
    fixture.parent.mkdir(parents=True)
    evidence = None
    try:
        evidence = _exercise(executable, source, pack_uri, fixture, fixture_uri, queue)
    finally:
        # _exercise closes its owned process before unwinding here. This also
        # cleans failed validation or launch attempts before a process existed.
        archived = archive_owned_fixtures(source, fixture, queue)
        report = queue / "asset-verification.json"
        if evidence is None:
            evidence = json.loads(report.read_text()) if report.exists() else {"passed": False, "source": str(source)}
        evidence["archived_fixtures"] = archived
        evidence["content_fixtures_removed"] = True
        report.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--editor", type=Path, default=ROOT / "editor/bazel-bin/darklantern64_scene_editor.exe")
    parser.add_argument("--level", type=Path, default=ROOT / "content" / ("asset_test_" + uuid.uuid4().hex + ".json"))
    parser.add_argument("--pack", help="Optional real asset pack URI; a private copy of its manifest is tested")
    args = parser.parse_args()
    verify(args.editor.resolve(), args.level, args.pack)
