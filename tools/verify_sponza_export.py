"""Check saved Sponza geometry export on disposable copies, without a game build."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import export_sponza
from tools.compile_level import compile_level, validate


def worker(output, contract):
    import bpy
    sys.path.insert(0, str(ROOT / "tools/blender"))
    import export_sponza_geometry
    geometry = json.loads(contract.read_text())
    ident = "mesh-sponza-ground-column"
    names = [row["id"] for row in geometry["instances"] if row["model"] == ident]
    obj = bpy.context.scene.objects[names[0]]
    checks = []

    def rejected(label):
        try:
            export_sponza_geometry.export(contract, output.parent)
        except ValueError as error:
            checks.append({"name": label, "rejected": True, "message": str(error)})
        else:
            raise ValueError("Unsupported edit was accepted: " + label)

    obj.location.x += .01
    bpy.context.view_layer.update()
    rejected("instance_placement")
    obj.location.x -= .01
    original = obj.data
    obj.data = original.copy()
    bpy.context.view_layer.update()
    rejected("unlinked_shared_variant")
    copied = obj.data
    obj.data = original
    bpy.data.meshes.remove(copied)
    proxy = bpy.context.scene.objects[geometry["colliders"][0]["id"]]
    proxy.scale.x += .01
    bpy.context.view_layer.update()
    rejected("collision_proxy_edit")
    proxy.scale.x -= .01
    before = list(obj.data.vertices[0].co)
    obj.data.vertices[0].co.x += .01
    obj.data.update()
    bpy.context.view_layer.update()
    bpy.ops.wm.save_as_mainfile(filepath=str(output))
    (output.parent / "edit.json").write_text(json.dumps({"asset": ident, "instances": names,
        "local_vertex": 0, "blender_before": before, "delta_x_m": .01, "rejections": checks}, indent=2)+"\n")


def verify(source, report_path):
    blender = export_sponza.find_blender()
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    canonical = {p: p.read_bytes() for p in (ROOT / "content").rglob("*") if p.is_file()}
    (ROOT / "build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sponza-export-proof-", dir=ROOT / "build") as directory:
        scratch = Path(directory).resolve()
        shutil.copytree(ROOT / "content", scratch / "content")
        baseline = export_sponza.export_saved(source, scratch, blender=blender, check=True)
        for ident, comparison in baseline["comparisons"].items():
            if (not comparison["same_topology"] or comparison.get("max_position_error_m", 1) > 1e-5 or
                    comparison.get("max_uv_error", 1) > 1e-5 or comparison.get("max_normal_error", 1) > 2e-4):
                raise ValueError("Unchanged saved Blender mesh failed parity: " + ident + " " + str(comparison))
        changed = scratch / "edited.blend"
        command = [str(blender), "--factory-startup", "--background", "--disable-autoexec", "--python-exit-code", "1",
                   str(source), "--python", str(Path(__file__).resolve()), "--", "--worker", "--source", str(changed),
                   "--contract", str(scratch / "content/assets/sponza/geometry.json")]
        subprocess.run(command, cwd=ROOT, check=True)
        edited = export_sponza.export_saved(changed, scratch, blender=blender)
        edit = json.loads((scratch / "edit.json").read_text())
        comparison = edited["comparisons"][edit["asset"]]
        if not .0099 < comparison.get("max_position_error_m", 0) < .0101:
            raise ValueError("The edited shared vertex did not reach its cooked geometry")
        cooked = compile_level(scratch / "content/sponza_courtyard.json", scratch / "cooked")
        state = validate(json.loads((scratch / "content/sponza_courtyard.json").read_text()), scratch / "content")
        instances = [row["id"] for row in state["models"] if row["model"] == edit["asset"]]
        if set(instances) != set(edit["instances"]):
            raise ValueError("Edited mesh did not retain every shared level instance")
        result = {"passed": True, "source_sha256": original_hash, "unchanged_source": baseline,
                  "edited_mesh": comparison, "edit": edit, "shared_cooked_instances": instances,
                  "cooked_level_source_sha256": cooked["source_sha256"], "cooked_counts": cooked["counts"],
                  "canonical_content_unchanged": True, "open_blender_untouched": True}
    if hashlib.sha256(source.read_bytes()).hexdigest() != original_hash:
        raise ValueError("Canonical saved Blender source changed during the proof")
    if any(path.read_bytes() != payload for path, payload in canonical.items()):
        raise ValueError("Canonical content changed during the proof")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({"passed": True, "report": str(report_path), "shared_instances": len(instances)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "art/sponza.blend")
    parser.add_argument("--report", type=Path, default=ROOT / "build/sponza-export-verification.json")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--contract", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else None)
    if args.worker:
        worker(args.source, args.contract)
    else:
        verify(args.source.resolve(), args.report.resolve())
