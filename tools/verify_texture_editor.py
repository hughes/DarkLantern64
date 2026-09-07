"""Exercise palette material editing in an owned editor and private project.

Uses a frozen content seed when available, plus the current backend. The fixture
contains static UV blocks and synthetic color charts, so concurrent guard-art
work cannot affect the result. No Ares or real-project write is performed.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

from PIL import Image, ImageChops

from cook_textures import prepare_texture
from project_levels import starter_level
from verify_project_editor import EditorClient, capture, hash_file, require, tree_hashes, write_json

ROOT = Path(__file__).resolve().parents[1]
LEVEL = "palette_editor_test.json"
OTHER = "palette_editor_other.json"
RED = ["#D61818", "#842121", "#10B529", "#295A29"]
BLUE = ["#185AD6", "#213984", "#D6B529", "#847329"]


def chart(path, palette):
    colors = [tuple(int(color[i:i+2], 16) for i in (1, 3, 5)) for color in palette]
    image = Image.new("RGB", (64, 64))
    image.putdata([colors[(x//16 + y//16) % 4] for y in range(64) for x in range(64)])
    image.save(path)


def verify(executable, root=ROOT):
    root, executable = Path(root).resolve(), Path(executable).resolve()
    require(executable.is_file(), "Build the scene editor executable first")
    parent = root / ".dev/editor/texture-verification"
    require(parent.resolve() == parent, "Verification parent cannot be a symlink or junction")
    private = parent / uuid.uuid4().hex
    private.mkdir(parents=True)
    require(private.resolve().parent == parent, "Private fixture escaped its directory")
    frozen = root / "build/guard-atlas-study/before/content"
    seed = frozen if frozen.is_dir() else root / "content"
    shutil.copytree(seed, private / "content")
    for folder in ("src", "tools"):
        shutil.copytree(root / folder, private / folder, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    content = private / "content"
    block = (content / "models/block.obj").read_text().splitlines()
    first_face = next(i for i, line in enumerate(block) if line.startswith("f "))
    block[first_face:first_face] = ["vt 0 0", "vt 1 0", "vt 1 1", "vt 0 1"]
    block = ["f " + " ".join(f"{vertex}/{i+1}" for i, vertex in enumerate(line.split()[1:]))
             if line.startswith("f ") else line for line in block]
    (content / "models/palette-editor-block.obj").write_text("\n".join(block) + "\n")
    chart(content / "palette-red.png", RED)
    chart(content / "palette-blue.png", BLUE)
    document = starter_level("Palette Editor Verification")
    document["asset_packs"] = []
    document["assets"][0]["uri"] = "models/palette-editor-block.obj"
    material = next(m for m in document["materials"] if m["id"] == "mat-stone")
    material.update(color=[1, 1, 1, 1], emissive=[.25, .25, .25])
    write_json(content / LEVEL, document)
    write_json(content / OTHER, dict(document, title="Palette Reopen Check"))
    write_json(content / "level_bundle.json", {"version": 1, "title": "Private Palette Check",
                                              "levels": [{"id": "palette", "source": LEVEL}]})
    queue = private / ".dev/editor/project"
    write_json(queue / "project-state.json", {"last_level": LEVEL})
    report = {"passed": False, "private_root": private.as_posix(), "content_seed": seed.as_posix(),
              "editor": executable.as_posix(), "editor_sha256": hash_file(executable),
              "input_hashes": {folder: tree_hashes(private / folder) for folder in ("src", "tools")},
              "checks": [], "captures": [], "textures": [], "owned_editor_pids": [],
              "scope": "Private fixture only; live art is concurrently authored and is not used as an immutability oracle."}
    process = None
    started = time.monotonic()
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE

    def passed(label):
        report["checks"].append(label)
        print("PASS " + label, flush=True)

    def state(client):
        result = client.request("inspect")
        require(Path(result["source"]).resolve() == content / LEVEL, "Editor used the wrong source")
        require(Path(result["output"]).resolve().is_relative_to(private), "Cook output escaped the private project")
        return result

    def reject(client, texture, message):
        before = state(client)
        saved = (content / LEVEL).read_bytes()
        result = client.request("set_material", {"id": "mat-stone", "patch": {
            "color": [1, 0, 1, 1], "texture": texture}}, expect_ok=False)
        require(message.lower() in result["error"].lower(), "Rejection did not explain the invalid texture")
        after = state(client)
        require(after["document"] == before["document"] and after["dirty"] == before["dirty"]
                and (content / LEVEL).read_bytes() == saved, "Rejected material patch mutated the document or saved level")

    def check_cook(client, texture):
        current = state(client)
        saved = json.loads((content / LEVEL).read_bytes())
        material = next(m for m in current["document"]["materials"] if m["id"] == "mat-stone")
        require(not current["dirty"] and saved == current["document"] and material["texture"] == texture,
                "Save/reopen lost texture format, dimensions or artist palette")
        expected, png = prepare_texture(texture, content)
        textures = current["report"]["textures"]
        require(len(textures["textures"]) == 1, "Fixture should contain exactly one unique texture")
        cooked = textures["textures"][0]
        for key in ("format", "width", "height", "palette_bytes", "decoded_bytes", "decoded_total_bytes", "png_sha256", "recipe_sha256"):
            require(cooked[key] == expected[key], "Editor texture report disagrees with the cooker: " + key)
        for key in ("palette_bytes", "decoded_bytes", "decoded_total_bytes"):
            require(textures[key] == expected[key], "Editor texture aggregate is incorrect: " + key)
        output = Path(current["output"])
        require((output / cooked["png_path"]).read_bytes() == png, "Editor cook did not publish the exact palette PNG")
        scene = json.loads((private / "build/project/editor-assets" / current["scene_path"]).read_bytes())
        preview_material = next(m for m in scene["materials"] if m["name"] == "mat-stone")
        uri = preview_material["textures"]["albedo"]
        require(uri.startswith("file://textures/"), "Preview material does not reference a cooked texture")
        published = private / "build/project/editor-assets" / uri.removeprefix("file://")
        require(published.read_bytes() == png, "Published preview points to stale or different palette pixels")
        with Image.open(published) as picture:
            require(picture.mode == "P" and picture.size == (texture["width"], texture["height"]), "Palette PNG did not load")
        return cooked

    with (private / "texture-editor.log").open("w", encoding="utf-8") as log:
        def launch():
            nonlocal process
            process = subprocess.Popen([str(executable), str(private)], cwd=private, stdout=log, stderr=log, startupinfo=startup)
            report["owned_editor_pids"].append(process.pid)
            client = EditorClient(queue, process)
            session = client.wait_session()
            require(Path(session["root"]).resolve() == private, "Editor used the real project root")
            return client

        try:
            client = launch()
            ci4 = {"uri": "palette-red.png", "format": "CI4", "width": 64, "height": 64, "palette": RED}
            ci8 = {"uri": "palette-blue.png", "format": "CI8", "width": 64, "height": 32, "palette": BLUE}
            reject(client, dict(ci8, height=64), "2 KiB")
            reject(client, dict(ci4, width=4294967360), "dimensions")
            reject(client, dict(ci4, palette=["red"]), "#RRGGBB")
            reject(client, dict(ci4, palette=RED*5), "palette")
            passed("Oversized CI8, overflowing dimensions and invalid palettes reject atomically")
            for texture in (ci4, ci8):
                client.request("set_material", {"id": "mat-stone", "patch": {"texture": texture}})
                require(state(client)["dirty"], "Accepted texture edit did not mark the level dirty")
                client.request("save")
                report["textures"].append(check_cook(client, texture))
                report["captures"].append(capture(client, private, texture["format"]))
                expected = copy.deepcopy(state(client)["document"])
                client.request("open_level", {"file": OTHER})
                client.request("open_level", {"file": LEVEL})
                check_cook(client, texture)
                require(state(client)["document"] == expected, "Level switching changed the authored texture")
                passed(f'{texture["width"]}x{texture["height"]} {texture["format"]} saves, refreshes the palette preview and survives reopening')
            reject(client, dict(ci8, height=64), "2 KiB")
            with Image.open(report["captures"][0]["path"]) as first, Image.open(report["captures"][1]["path"]) as second:
                require(first.size == second.size, "Capture dimensions changed")
                difference = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
                changed = sum(max(pixel) > 12 for pixel in difference.getdata())
                require(changed >= 1000, "Changing palette textures did not visibly refresh the viewport")
                report["changed_viewport_pixels_over_12"] = changed
            passed("Distinct indexed PNGs visibly replace one another in the live viewport")
            client.request("quit"); process.wait(timeout=15)
            require(process.returncode == 0, "Owned editor did not exit cleanly")
            client = launch()
            check_cook(client, ci8)
            report["captures"].append(capture(client, private, "CI8-restarted"))
            passed("Restarting the editor restores saved CI8 format and authored palette")
            client.request("quit"); process.wait(timeout=15)
            require(process.returncode == 0, "Restarted owned editor did not exit cleanly")
            report["passed"] = True
        except BaseException as error:
            report.update(error=str(error), traceback=traceback.format_exc())
            raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=15)
            report["elapsed_seconds"] = round(time.monotonic() - started, 2)
            write_json(private / "texture-editor.json", report)
            write_json(root / "build/guard-atlas-study/texture-editor.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--editor", type=Path, default=ROOT / "editor/bazel-bin/darklantern64_scene_editor.exe")
    args = parser.parse_args()
    try:
        result = verify(args.editor, args.root)
        print(json.dumps({"passed": result["passed"], "report": str(args.root / "build/guard-atlas-study/texture-editor.json")}, indent=2))
    except (AssertionError, OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"Palette editor verification failed: {error}", file=sys.stderr)
        raise SystemExit(1)
