"""Private saved-file Blender worker for tools/export_sponza.py."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
from darklantern64_export import _obj
from darklantern64_export.core import identifier, require


def expected_matrix(transform):
    x, y, z = transform["position"]
    sx, sy, sz = transform["scale"]
    rx, ry, rz = transform["rotation"]
    require(rx == 0 and rz == 0, "Sponza authoring contract expects upright instances")
    return Matrix.Translation((x, -z, y)) @ Matrix.Rotation(math.radians(ry), 4, "Z") @ Matrix.Diagonal((sx, sz, sy, 1))


def check_placement(obj, expected):
    require(obj.parent is None and not obj.constraints and obj.animation_data is None,
            f"{obj.name}: remove parenting, constraints or object animation; placements belong in the level editor")
    require(all(math.isfinite(obj.matrix_world[r][c]) for r in range(4) for c in range(4)),
            f"{obj.name}: object placement contains a non-finite value")
    delta = max(abs(obj.matrix_world[r][c] - expected[r][c]) for r in range(4) for c in range(4))
    require(delta <= 1e-5,
            f"{obj.name}: object placement/origin changed. Export edits in mesh Edit Mode; edit instance placement and collision proxies in the level editor")


def export(contract_path, output):
    geometry = json.loads(contract_path.read_text())
    require(abs(bpy.context.scene.unit_settings.scale_length - 1) < 1e-8, "Sponza geometry uses metres; keep scene unit scale at 1")
    # Hidden collision collections initially retain identity matrix_world until
    # their dependency graph is evaluated. This changes only the private worker.
    for row in geometry["instances"] + geometry["colliders"]:
        obj = bpy.context.scene.objects.get(row["id"])
        if obj is not None:
            for collection in obj.users_collection:
                collection.hide_viewport = False
    bpy.context.view_layer.update()
    rows = {row["id"]: row for row in geometry["instances"]}
    objects = {obj.name: obj for obj in bpy.context.scene.objects if obj.type == "MESH"}
    require(set(objects) == set(rows), "Sponza instances were added, removed or renamed; change level placement in the editor")
    representatives = {}
    for name, row in rows.items():
        obj = objects[name]
        ident = identifier(obj.get("dl64_asset_id"), name + " asset ID")
        require(ident == row["model"], f"{name}: shared asset ID changed")
        check_placement(obj, expected_matrix(row["transform"]))
        require(not obj.modifiers and obj.data.shape_keys is None,
                f"{name}: apply modifiers and remove shape keys before this static geometry export")
        require(obj.mode == "OBJECT", f"{name}: save in Object Mode before exporting")
        require(len(obj.data.materials) == 1 and obj.data.materials[0] is not None and
                obj.data.materials[0].get("dl64_material_id") == row["material"],
                f"{name}: material assignment changed; edit shared pack materials separately")
        require(ident not in representatives or representatives[ident].data == obj.data,
                f"{ident}: repeated instances use unlinked mesh variants; link their mesh data or create a separately authored asset")
        representatives[ident] = obj
    for row in geometry["colliders"]:
        obj = bpy.context.scene.objects.get(row["id"])
        require(obj is not None and obj.type == "EMPTY", "Collision proxy was removed or replaced: " + row["id"])
        transform = dict(row["transform"], scale=row["collider"]["half_size"])
        check_placement(obj, expected_matrix(transform))
    assets = {}
    for ident, obj in representatives.items():
        # The shared exporter normally bakes object rotation/scale. A temporary
        # identity instance keeps the original local mesh origin and leaves
        # level placement entirely outside this geometry-only export.
        local = obj.copy()
        local.name = ident
        local.matrix_world = Matrix.Identity(4)
        bpy.context.scene.collection.objects.link(local)
        try:
            bpy.context.view_layer.update()
            payload = _obj(bpy.context, local, textured=True)
        finally:
            bpy.data.objects.remove(local, do_unlink=True)
        (output / (ident + ".obj")).write_bytes(payload)
        assets[ident] = {"sha256": hashlib.sha256(payload).hexdigest(), "source_object": obj.name}
    report = {"assets": assets, "instances_checked": len(rows), "collision_proxies_checked": len(geometry["colliders"]),
              "blender_version": bpy.app.version_string, "placement_baked": False}
    (output / "meshes.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    export(args.contract, args.output)
