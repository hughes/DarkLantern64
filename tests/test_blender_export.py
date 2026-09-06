"""Path/identity guards run without Blender; geometry is verified in Blender."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1] / "tools/blender/darklantern64_export/core.py"
spec = importlib.util.spec_from_file_location("dl64_export_core", CORE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)


class BlenderExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / "assets/loot"
        self.output.mkdir(parents=True)
        self.pack = {"version": 1,
                     "assets": [{"id": "mesh-loot-coin", "uri": "assets/loot/loot-coin.obj"}],
                     "materials": [{"id": "mat-loot", "color": [1, 1, 1, 1],
                                    "texture": {"uri": "assets/loot/mat-loot.png", "width": 32,
                                                "height": 32, "format": "RGBA16"}}],
                     "prefabs": [{"id": "loot-coin", "model": "mesh-loot-coin",
                                  "material": "mat-loot", "scale": [1, 1, 1]}]}

    def validate(self):
        return core.validate_publication(self.pack, self.root, self.output)

    def save_pack(self):
        (self.output / "pack.json").write_text(json.dumps(self.pack), encoding="utf-8")

    def test_texture_tmem_budget(self):
        core.texture_size(64, 32)
        for dimensions in ((64, 64), (31, 32), (128, 1), (0, 32), (True, 32)):
            with self.subTest(dimensions=dimensions), self.assertRaises(core.ExportError):
                core.texture_size(*dimensions)

    def test_identifier_and_finite_bounds(self):
        core.identifier("loot-Coin_1", "test")
        for ident in ("../coin", "1coin", "coin name", "a" * 65, None):
            with self.assertRaises(core.ExportError):
                core.identifier(ident, "test")
        for values in ([float("nan"), 0, 0], [float("inf"), 0, 0], [True, 0, 0], [2, 0, 0]):
            with self.assertRaises(core.ExportError):
                core.bounded(values, 3, "color")

    def test_path_escape_rejected(self):
        with self.assertRaises(core.ExportError):
            core.relative_uri(self.root / "../outside.obj", self.root)
        self.pack["assets"][0]["uri"] = "../../outside.obj"
        with self.assertRaises(core.ExportError):
            self.validate()

    def test_unowned_file_not_overwritten(self):
        (self.output / "loot-coin.obj").write_text("user model")
        with self.assertRaisesRegex(core.ExportError, "not owned"):
            self.validate()

    def test_reexport_and_old_material_copy_allowed(self):
        self.save_pack()
        (self.root / "level.json").write_text(json.dumps(self.pack))
        (self.output / "loot-coin.obj").write_text("old owned mesh")
        self.pack["materials"][0]["color"] = [.5, .8, 1, 1]
        self.validate()

    def test_asset_id_collision_rejected(self):
        other = copy.deepcopy(self.pack)
        other["assets"][0]["uri"] = "models/unrelated.obj"
        (self.root / "level.json").write_text(json.dumps(other))
        with self.assertRaisesRegex(core.ExportError, "Asset ID"):
            self.validate()

    def test_material_id_collision_rejected(self):
        other = copy.deepcopy(self.pack)
        other["materials"][0]["color"] = [.1, .2, .3, 1]
        (self.root / "level.json").write_text(json.dumps(other))
        with self.assertRaisesRegex(core.ExportError, "Material ID"):
            self.validate()

    def test_casefold_duplicate_and_case_collision_rejected(self):
        self.pack["assets"].append({"id": "MESH-LOOT-COIN", "uri": "assets/loot/second.obj"})
        with self.assertRaisesRegex(core.ExportError, "Duplicate"):
            self.validate()
        self.pack["assets"].pop()
        self.save_pack()
        self.pack["assets"][0]["id"] = "MESH-LOOT-COIN"
        with self.assertRaises(core.ExportError):
            self.validate()

    def test_malformed_existing_pack_not_replaced(self):
        for value in ([], {"version": 1}, dict(self.pack, assets=[None]),
                      dict(self.pack, assets=[{"id": "missing-uri"}])):
            (self.output / "pack.json").write_text(json.dumps(value))
            with self.subTest(value=value), self.assertRaises(core.ExportError):
                self.validate()


if __name__ == "__main__":
    unittest.main()
