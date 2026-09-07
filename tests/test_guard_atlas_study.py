"""Texture-study lineage must survive UV splits without hiding geometry edits."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.guard_atlas_study import checked, geometry_identity, guard_texture


def character():
    return {"bones": [{"id": "root"}, {"id": "head"}], "mesh": {
        "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
        "normals": [[0, 0, 1]] * 3, "uvs": [[0, 0], [1, 0], [0, 1]],
        "joints": [0, 0, 1], "indices": [0, 1, 2]}}


class GuardAtlasStudy(unittest.TestCase):
    def test_shared_material_resolves_renamed_atlas_and_checks_cooked_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder)
            assets = stage / "content/assets/guard"
            assets.mkdir(parents=True)
            (assets / "painted.png").write_bytes(b"painted pixels")
            uri = "assets/guard/painted.png"
            (assets / "pack.json").write_text(json.dumps({"materials": [
                {"id": "mat-guard", "texture": {"uri": uri}}]}))
            texture = {"uri": uri, "source_sha256": hashlib.sha256(b"painted pixels").hexdigest()}
            manifest = {"textures": {"textures": [texture]}}
            self.assertEqual(guard_texture(stage, manifest), texture)
            (assets / "painted.png").write_bytes(b"changed pixels")
            with self.assertRaisesRegex(ValueError, "differs"):
                guard_texture(stage, manifest)
            manifest["textures"]["textures"] = []
            with self.assertRaisesRegex(ValueError, "one cooked"):
                guard_texture(stage, manifest)

    def test_uv_repaint_and_split_vertices_preserve_geometry_identity(self):
        old = character(); new = copy.deepcopy(old)
        for key in ("vertices", "normals", "uvs", "joints"):
            new["mesh"][key].append(copy.deepcopy(new["mesh"][key][0]))
        new["mesh"]["uvs"] = [[.5, .5]] * 4
        new["mesh"]["indices"] = [1, 2, 3]  # Same winding, cyclic start and new UV seam index.
        self.assertEqual(geometry_identity(old), geometry_identity(new))

    def test_winding_normal_position_and_bone_changes_are_visible(self):
        old = character()
        changes = []
        new = copy.deepcopy(old); new["mesh"]["indices"] = [0, 2, 1]; changes.append(new)
        new = copy.deepcopy(old); new["mesh"]["normals"][0] = [1, 0, 0]; changes.append(new)
        new = copy.deepcopy(old); new["mesh"]["vertices"][0][0] = .001; changes.append(new)
        new = copy.deepcopy(old); new["mesh"]["joints"][0] = 1; changes.append(new)
        for new in changes:
            self.assertNotEqual(geometry_identity(old), geometry_identity(new))

    def test_snapshot_rejects_changed_inputs_and_escape_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Path(folder).resolve()
            source = stage / "source.txt"; source.write_bytes(b"baseline")
            manifest = {"files": {"source.txt": hashlib.sha256(b"baseline").hexdigest()}}
            path = stage / "snapshot.json"; path.write_text(json.dumps(manifest))
            self.assertEqual(checked(stage), manifest)
            source.write_bytes(b"edited")
            with self.assertRaisesRegex(ValueError, "input changed"):
                checked(stage)
            manifest["files"] = {"../outside.txt": "a" * 64}
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "input changed"):
                checked(stage)


if __name__ == "__main__":
    unittest.main()
