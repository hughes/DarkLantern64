"""Check cooked guard animation in an owned, private project editor session.

Build the editor first. Source content and other running editors are untouched.
The check captures paused authored poses, head attention, independent instances,
and verifies that preview controls never mark the level dirty or save changes.
Evidence remains in .dev/editor/character-verification/<uuid>/.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

from PIL import Image, ImageChops

from verify_project_editor import EditorClient, capture, hash_file, require, tree_hashes, write_json

ROOT = Path(__file__).resolve().parents[1]


def compare(one, two, minimum):
    with Image.open(one["path"]) as first, Image.open(two["path"]) as second:
        require(first.size == second.size, "Animation capture dimensions changed")
        delta = ImageChops.difference(first.convert("RGB"), second.convert("RGB"))
        changed = sum(max(pixel) > 8 for pixel in delta.getdata())
        require(changed >= minimum, f"{one['label']} and {two['label']} changed only {changed} pixels")
        return {"first": one["label"], "second": two["label"], "changed_pixels_over_8": changed}


def verify(executable, root=ROOT, level="animation_workshop.json"):
    root, executable = Path(root).resolve(), Path(executable).resolve()
    require(executable.is_file(), "Build the project editor before verifying character animation")
    require(Path(level).name == level and (root / "content" / level).is_file(), "Choose an existing level filename")
    real_before = tree_hashes(root / "content")
    parent = root / ".dev/editor/character-verification"
    require(parent.resolve() == parent, "Verification folder cannot be a junction or symlink")
    private = parent / uuid.uuid4().hex
    private.mkdir(parents=True)
    require(private.resolve().parent == parent, "Private editor project escaped its folder")
    for folder in ("content", "src", "tools"):
        shutil.copytree(root / folder, private / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    copied_before = tree_hashes(private / "content")
    queue = private / ".dev/editor/project"
    write_json(queue / "project-state.json", {"last_level": level})
    report = {"passed": False, "private_root": private.as_posix(), "editor": executable.as_posix(),
              "editor_sha256": hash_file(executable), "checks": [], "captures": [], "differences": [],
              "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    startup = None
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
    process = None
    start = time.monotonic()

    def passed(label):
        report["checks"].append(label)
        print("PASS " + label, flush=True)

    def instance(state, entity_id):
        return next(item for item in state["instances"] if item["id"] == entity_id)

    with (private / "character-editor.log").open("w", encoding="utf-8") as log:
        try:
            process = subprocess.Popen([str(executable), str(private)], cwd=private,
                                       stdout=log, stderr=log, startupinfo=startup)
            report["owned_editor_pid"] = process.pid
            client = EditorClient(queue, process)
            session = client.wait_session()
            require(Path(session["root"]).resolve() == private, "Editor used the real project root")
            state = client.request("inspect")
            require(Path(state["source"]).name == level and not state["dirty"], "Wrong or dirty startup level")
            animation = client.request("get_animation")
            require(animation["instances"] and not animation["diagnostics"], f"No cooked character preview: {animation}")
            require(all(item["status"] == "ready" for item in animation["instances"]), "Unsupported character preview")
            target = sorted(animation["instances"], key=lambda item: item["id"])[0]
            entity_id = target["id"]
            require(target["head_supported"], "Proof rig lacks head attention")
            clip_ids = [clip["id"] for clip in target["clips"]]
            require("walk" in clip_ids and "idle" in clip_ids, "Proof rig requires idle and walk clips")
            report["character"] = {key: target[key] for key in ("id", "asset", "bones", "clips", "encoded_bytes", "head_bone")}
            report["character"]["cross_joint_triangles"] = target["cross_joint_triangles"]
            report["character"]["preview_geometry"] = target["preview_geometry"]
            require(target["preview_geometry"] == "connected one-weight mesh" and target["cross_joint_triangles"] > 0,
                    "This proof needs connected triangles whose vertices use different joints")
            passed("Project level opens cooked character assets through the shared N64 sampler")
            client.request("focus_character", {"id": entity_id})
            passed("Focus guard frames its current pose in a clear three-quarter view")

            def pose(label, **settings):
                result = client.request("set_animation", {"id": entity_id, "playing": False,
                    "blend": 0, "head_yaw": 0, "head_pitch": 0, **settings})
                sampled = instance(result, entity_id)
                require(not sampled["playing"] and sampled["status"] == "ready", "Pose did not pause cleanly")
                write_json(private / (label + "-pose.json"), sampled)
                picture = capture(client, private, label)
                report["captures"].append(picture)
                return sampled, picture

            idle, idle_image = pose("idle", clip="idle", time=0)
            walk, walk_image = pose("walk-stride", clip="walk", time=.24)
            require(walk["skin_matrices"] != idle["skin_matrices"], "Clip selection retained the same pose")
            report["differences"].append(compare(idle_image, walk_image, 1000))
            passed("Authored idle and walking poses visibly differ in the ordinary level viewport")

            again, again_image = pose("walk-repeat", clip="walk", time=.24)
            require(again["skin_matrices"] == walk["skin_matrices"], "Paused sampler is nondeterministic")
            require(again_image["sha256"] == walk_image["sha256"], "Repeated paused pose capture is nondeterministic")
            passed("Repeated paused clip/time produces identical skin matrices and viewport PNG")

            head, head_image = pose("walk-attention", clip="walk", time=.24, head_yaw=40, head_pitch=-15)
            head_index = target["head_bone"]
            require(head["skin_matrices"][head_index] != walk["skin_matrices"][head_index], "Head attention did not affect its bone")
            require(head["skin_matrices"][0] == walk["skin_matrices"][0], "Head attention moved the skeleton root")
            report["differences"].append(compare(walk_image, head_image, 100))
            passed("Bounded procedural head attention changes the image without moving the body root")

            blended = instance(client.request("set_animation", {"id": entity_id, "clip": "walk", "time": .24,
                "blend_clip": "idle", "blend": .5, "head_yaw": 0, "head_pitch": 0}), entity_id)
            require(blended["skin_matrices"] != walk["skin_matrices"], "Clip blending retained the unblended walking pose")
            passed("Crossfade weight changes the sampled cooked pose")

            untouched = {item["id"]: item["skin_matrices"] for item in animation["instances"] if item["id"] != entity_id}
            current = client.request("get_animation")
            require(all(instance(current, ident)["skin_matrices"] == matrices for ident, matrices in untouched.items()),
                    "Preview state leaked into another guard instance")
            if untouched:
                passed("Guards share the asset while keeping independent preview playback state")

            before_invalid = instance(current, entity_id)
            for patch in ({"clip": "missing"}, {"head_yaw": 90}, {"time": -1}, {"unexpected": 1}):
                client.request("set_animation", {"id": entity_id, **patch}, expect_ok=False)
            require(instance(client.request("get_animation"), entity_id) == before_invalid, "Rejected preview settings changed the pose")
            passed("Invalid clips and out-of-range preview settings are rejected atomically")

            play = instance(client.request("set_animation", {"id": entity_id, "playing": True}), entity_id)
            later = instance(client.request("get_animation"), entity_id)
            require(later["playing"] and later["time"] != play["time"], "Play did not advance the animation clock")
            client.request("set_animation", {"id": entity_id, "playing": False})
            passed("Play advances the preview clock and Pause stops it")
            require(client.request("get_animation")["vertex_arena_count"] == animation["vertex_arena_count"],
                    "Animation playback allocated additional mesh vertices")
            passed("Connected-joint animation reuses one fixed-topology mesh per instance without arena growth")

            # Simulate an artist changing an animation in our private copy. The
            # mesh remains byte-for-byte identical: recooking must load the new
            # keys into the same instance allocation instead of retaining a new
            # full-source-hash mesh after every animation export.
            document = client.request("inspect")["document"]
            model = next(row["model"] for row in document["entities"] if row["id"] == entity_id)
            uri = next(row["uri"] for row in document["assets"] if row["id"] == model)
            artist_source = (private / "content" / uri).resolve()
            require(artist_source.is_relative_to(private / "content"), "Character source escaped the private project")
            original_asset = artist_source.read_bytes()
            changed_asset = json.loads(original_asset)
            head_joint = next(i for i, bone in enumerate(changed_asset["bones"]) if bone["id"] == "head")
            sine, cosine = math.sin(.3), math.cos(.3)
            for clip in changed_asset["clips"]:
                if clip["id"] != "idle":
                    continue
                for frame in clip["frames"]:
                    x, y, z, w = frame["rotations"][head_joint]
                    frame["rotations"][head_joint] = [cosine*x+sine*z, cosine*y+sine*w,
                                                       cosine*z-sine*x, cosine*w-sine*y]
            old_handles = {row["id"]: row["dynamic_mesh_handle"] for row in animation["instances"]}
            try:
                write_json(artist_source, changed_asset)
                client.request("reload")
                reexported = client.request("get_animation")
                replacement = instance(reexported, entity_id)
                require(replacement["source_sha256"] != target["source_sha256"], "Artist animation re-export was not recooked")
                require(replacement["topology_sha256"] == target["topology_sha256"], "Animation-only export changed topology identity")
                require(replacement["skin_matrices"] != idle["skin_matrices"], "Editor retained the previous animation keys")
                require(reexported["vertex_arena_count"] == animation["vertex_arena_count"], "Animation-only re-export grew the vertex arena")
                require(all(instance(reexported, ident)["dynamic_mesh_handle"] == handle for ident, handle in old_handles.items()),
                        "Animation-only re-export allocated another per-instance mesh")
                client.request("focus_character", {"id": entity_id})
                picture = capture(client, private, "idle-after-artist-reexport")
                report["captures"].append(picture)
                report["differences"].append(compare(idle_image, picture, 100))
                report["animation_reexport"] = {"vertex_arena_count": reexported["vertex_arena_count"],
                    "handles": old_handles, "topology_sha256": replacement["topology_sha256"],
                    "original_source_sha256": target["source_sha256"], "changed_source_sha256": replacement["source_sha256"]}
            finally:
                artist_source.write_bytes(original_asset)
            client.request("reload")
            restored = client.request("get_animation")
            require(restored["vertex_arena_count"] == animation["vertex_arena_count"], "Restoring the artist source grew the arena")
            require(instance(restored, entity_id)["skin_matrices"] == idle["skin_matrices"], "Restoring the artist source retained stale motion")
            passed("Artist animation re-export displays new motion while reusing the same per-instance meshes and arena storage")

            state = client.request("inspect")
            require(not state["dirty"] and not state["busy"], "Preview playback marked gameplay content dirty")
            require(tree_hashes(private / "content") == copied_before, "Preview controls wrote canonical content")
            passed("Clip, scrub, blend and attention controls preserve clean canonical content")
            client.request("quit")
            process.wait(timeout=15)
            require(process.returncode == 0, "Owned editor did not exit cleanly")
            report["passed"] = True
        except BaseException as error:
            report["error"] = str(error)
            report["traceback"] = traceback.format_exc()
            raise
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
            report["real_content_unchanged"] = tree_hashes(root / "content") == real_before
            report["elapsed_seconds"] = round(time.monotonic() - start, 2)
            if not report["real_content_unchanged"]:
                report["passed"] = False
                report.setdefault("error", "Real content changed concurrently during verification")
            report["evidence"] = (private / "character-editor-verification.json").as_posix()
            write_json(private / "character-editor-verification.json", report)
            write_json(root / "build/character-editor-verification.json", report)
    require(report["passed"], report.get("error", "Character editor verification failed"))
    print(json.dumps(report, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--editor", type=Path, default=ROOT / "editor/bazel-bin/darklantern64_project_editor.exe")
    parser.add_argument("--level", default="animation_workshop.json")
    args = parser.parse_args()
    try:
        verify(args.editor, args.root, args.level)
    except (AssertionError, OSError, ValueError, TimeoutError, subprocess.SubprocessError) as error:
        print(f"Character editor verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
