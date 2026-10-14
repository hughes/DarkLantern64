"""Check the bounded Native lighting metadata loader with an already built runner."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import uuid

try:
    from compile_level import validate
    from compile_mission import cook_mission, stream_play_package
except ModuleNotFoundError:
    from tools.compile_level import validate
    from tools.compile_mission import cook_mission, stream_play_package

ROOT = Path(__file__).resolve().parents[1]


def buckets(rows):
    return {(row["mode"], row["reason"]): (row["models"], row["triangles"]) for row in rows}


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def verify(runner):
    runner = Path(runner).resolve()
    if not runner.is_file():
        raise FileNotFoundError("Build editor //:play_runner first, or supply --runner")
    output = ROOT / ".dev/native-lighting-metadata-verification" / uuid.uuid4().hex
    output.mkdir(parents=True)
    fixtures = ROOT / "tests/fixtures"
    name = "stream_lighting_single_door"
    document = json.loads((fixtures / (name + ".json")).read_bytes())
    manifest = json.loads((fixtures / (name + ".mission.json")).read_bytes())
    raw, report = cook_mission(manifest, fixtures)
    state = validate(document, fixtures, streaming=True)
    mission = output / "mission.dlm"
    mission.write_bytes(raw)
    packet = stream_play_package(document, state, report, mission.as_posix())
    packet_path = output / "play.json"

    def run(value):
        packet_path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return subprocess.run([str(runner), str(packet_path)], cwd=ROOT, capture_output=True,
                              text=True, timeout=60)

    valid = run(packet)
    if valid.returncode:
        raise AssertionError("Valid metadata rejected: " + valid.stderr)
    observed = json.loads(valid.stdout)["state"]["renderer_runtime"]
    require(observed["supported"] and observed["basis"] == "published_model_policy", "Missing observed published policy")
    require(buckets(observed["cases"]) == buckets(report["renderer_diagnostics"]["cases"]), "Publication policy differs from cook")
    require(buckets(observed["change_cases"]) == buckets(report["renderer_diagnostics"]["change_cases"]), "Change policy differs from cook")
    require(observed["identity"]["renderer_source_sha256"] == report["renderer_diagnostics"]["identity"]["renderer_source_sha256"], "Source identity differs from cook")
    require(observed["identity"]["package_sha256"] == report["package_sha256"], "Package identity differs from cook")
    require(any(row["entity_id"] == "north-door" and row["inline_payload"] for row in observed["model_entries"]), "Bootstrap door RGBA was not retained")
    baked_index = next(index for index, row in enumerate(packet["stream_models"]) if "baked_colors" in row)
    rejections = []

    def reject(label, mutation, message):
        value = copy.deepcopy(packet)
        mutation(value)
        result = run(value)
        require(result.returncode != 0, label + " unexpectedly accepted")
        require(message in result.stderr, label + ": " + result.stderr)
        rejections.append(dict(case=label, error=result.stderr.strip()))

    reject("short model vector", lambda p: p["stream_models"][0].update(position=[1, 2]), "Expected shared XYZ vector")
    reject("overflowed shared float", lambda p: p["stream_models"][0].update(position=[1e39, 0, 0]), "Non-finite shared vector")
    reject("short mesh vector", lambda p: p["stream_meshes"][0]["vertices"].__setitem__(0, [1, 2]), "Expected shared XYZ vector")
    reject("out-of-range color", lambda p: p["stream_models"][0]["color"].__setitem__(0, 256), "Invalid model color channel")
    reject("float emissive", lambda p: p["stream_models"][0]["emissive"].__setitem__(0, .5), "Invalid model emissive channel")
    reject("string sidedness", lambda p: p["stream_models"][0].update(double_sided="true"), "Invalid model sidedness")
    reject("invalid source identity", lambda p: p["stream"].update(renderer_source_sha256="z" * 64), "Invalid renderer source SHA-256")
    reject("short baked hex", lambda p: p["stream_models"][baked_index]["baked_colors"].__setitem__(0,
           p["stream_models"][baked_index]["baked_colors"][0][:-1]), "Model baked lighting length mismatch")
    reject("non-hex baked byte", lambda p: p["stream_models"][baked_index]["baked_colors"].__setitem__(0,
           "g" + p["stream_models"][baked_index]["baked_colors"][0][1:]), "Invalid model baked lighting hexadecimal byte")
    reject("transparent baked alpha", lambda p: p["stream_models"][baked_index]["baked_colors"].__setitem__(0,
           p["stream_models"][baked_index]["baked_colors"][0][:6] + "00" + p["stream_models"][baked_index]["baked_colors"][0][8:]),
           "Model baked lighting requires opaque RGBA")
    reject("one baked state", lambda p: p["stream_models"][baked_index].update(
           baked_colors=p["stream_models"][baked_index]["baked_colors"][:1]), "Model baked lighting capacity exceeded")
    reject("out-of-range mesh index", lambda p: p["stream_meshes"][0]["indices"].__setitem__(0, 65535), "Shared mesh index outside vertices")
    reject("out-of-range model mesh", lambda p: p["stream_models"][0].update(mesh=65535), "Invalid shared model mesh")
    reject("oversized model table", lambda p: p.update(stream_models=p["stream_models"] * 129), "Mission shared visual capacity exceeded")
    final = run(packet)
    require(final.returncode == 0, final.stderr)
    require(buckets(json.loads(final.stdout)["state"]["renderer_runtime"]["cases"]) == buckets(observed["cases"]), "Valid packet failed after malformed input checks")
    evidence = dict(passed=True, fixture=name, runner=str(runner), runner_sha256=hashlib.sha256(runner.read_bytes()).hexdigest(),
                    package_sha256=report["package_sha256"], renderer_source_sha256=observed["identity"]["renderer_source_sha256"],
                    checks=["Valid decoded publication and change policy match the cook with owned bootstrap RGBA",
                            "Malformed metadata rejects before starting a Native session",
                            "A valid packet still loads after the malformed input checks"],
                    observed=observed, rejections=rejections,
                    limitations=["Host loader and shared-policy evidence; no ROM shader or hardware timing measurement"],
                    result=str(output / "result.json"))
    (output / "result.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, default=ROOT / "editor/bazel-bin/play_runner.exe")
    parser.add_argument("--evidence", type=Path, help="Also save the complete result at this path")
    args = parser.parse_args(argv)
    result = verify(args.runner)
    if args.evidence:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(dict(passed=result["passed"], rejections=len(result["rejections"]), result=result["result"]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
