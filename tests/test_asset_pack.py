"""Asset imports must not replace authored definitions or escape content/."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from tools.asset_pack import load_pack, merge_pack, place_prop, resolve_asset_packs


class AssetPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "loot.obj").write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        (self.root / "atlas.png").write_bytes(b"Geometry and image decoding happens during the cook")
        self.pack = {"version": 1, "assets": [{"id": "mesh-loot", "uri": "loot.obj"}],
                     "materials": [{"id": "mat-loot", "color": [1, 1, 1, 1],
                                    "texture": {"uri": "atlas.png", "width": 32, "height": 32, "format": "RGBA16"}}],
                     "prefabs": [{"id": "loot", "model": "mesh-loot", "material": "mat-loot", "scale": [1, 2, 1]}]}
        self.level = {"version": 2, "assets": [], "materials": [], "entities": []}
        self.write_pack()

    def write_pack(self):
        (self.root / "pack.json").write_text(json.dumps(self.pack), encoding="utf-8")

    def test_import_is_idempotent_and_does_not_mutate_input(self):
        original = copy.deepcopy(self.level)
        merged, catalog = merge_pack(self.level, "pack.json", self.root)
        self.assertEqual(self.level, original)
        again, same_catalog = merge_pack(merged, "pack.json", self.root, catalog)
        self.assertEqual(again, merged)
        self.assertEqual(catalog, same_catalog)

    def test_conflict_rejects_entire_import_without_partial_mutation(self):
        self.level["materials"] = [{"id": "mat-loot", "color": [.5, .5, .5, 1]}]
        original = copy.deepcopy(self.level)
        with self.assertRaisesRegex(ValueError, "Conflicting definition"):
            merge_pack(self.level, "pack.json", self.root)
        self.assertEqual(self.level, original)

    def test_catalog_conflict_is_atomic(self):
        catalog = [dict(self.pack["prefabs"][0], scale=[2, 2, 2])]
        original = copy.deepcopy(catalog)
        with self.assertRaisesRegex(ValueError, "Conflicting prefab"):
            merge_pack(self.level, "pack.json", self.root, catalog)
        self.assertEqual(catalog, original)
        self.assertEqual(self.level["assets"], [])

    def test_paths_cannot_escape_content_or_use_drive_names(self):
        for uri in ("../pack.json", "C:/pack.json", "/pack.json", "assets\\pack.json"):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                load_pack(uri, self.root)
        self.pack["assets"][0]["uri"] = "../loot.obj"
        self.write_pack()
        with self.assertRaises(ValueError):
            load_pack("pack.json", self.root)

    def test_cross_group_duplicate_and_unknown_references_reject(self):
        self.pack["materials"][0]["id"] = "mesh-loot"
        self.write_pack()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_pack("pack.json", self.root)
        self.pack["materials"][0]["id"] = "mat-loot"
        self.pack["prefabs"][0]["model"] = "missing-model"
        self.write_pack()
        with self.assertRaisesRegex(ValueError, "unknown pack model"):
            load_pack("pack.json", self.root)

    def test_texture_tmem_and_types_are_bounded(self):
        for patch in ({"width": 64, "height": 64}, {"width": 12}, {"width": True}, {"format": "RGBA32"}):
            with self.subTest(patch=patch):
                texture = self.pack["materials"][0]["texture"]
                original = dict(texture)
                texture.update(patch)
                self.write_pack()
                with self.assertRaises(ValueError):
                    load_pack("pack.json", self.root)
                texture.clear(); texture.update(original)

    def test_place_uses_prefab_scale_and_explicit_transform(self):
        document, prefabs = merge_pack(self.level, "pack.json", self.root)
        placed = place_prop(document, prefabs, "loot", "goblet-1", [2, .8, -3], rotation=[0, 37, 0])
        entity = placed["entities"][0]
        self.assertEqual(entity["kind"], "static")
        self.assertEqual(entity["transform"], {"position": [2, .8, -3], "rotation": [0, 37, 0], "scale": [1, 2, 1]})
        self.assertNotIn("collider", entity)
        self.assertIs(entity["loot_highlight"], False)
        self.assertEqual(document["entities"], [])

    def test_loot_highlight_requires_boolean_and_preserves_input(self):
        document, prefabs = merge_pack(self.level, "pack.json", self.root)
        highlighted = place_prop(document, prefabs, "loot", "jewel-1", [0, 0, 0], loot_highlight=True)
        self.assertIs(highlighted["entities"][0]["loot_highlight"], True)
        for invalid in (0, 1, "true", None, []):
            with self.subTest(value=invalid), self.assertRaisesRegex(ValueError, "boolean"):
                place_prop(document, prefabs, "loot", "jewel-1", [0, 0, 0], loot_highlight=invalid)
        self.assertEqual(document["entities"], [])

    def test_place_rejects_duplicate_unknown_and_invalid_transform(self):
        document, prefabs = merge_pack(self.level, "pack.json", self.root)
        for prefab, ident, position, scale in (("missing", "new-prop", [0, 0, 0], None),
                                             ("loot", "mesh-loot", [0, 0, 0], None),
                                             ("loot", "new-prop", [0, float("nan"), 0], None),
                                             ("loot", "new-prop", [0, 0, 0], [0, 1, 1])):
            with self.subTest(prefab=prefab, ident=ident, scale=scale), self.assertRaises(ValueError):
                place_prop(document, prefabs, prefab, ident, position, scale=scale)
        self.assertEqual(document["entities"], [])

    def test_place_enforces_instance_limit(self):
        document, prefabs = merge_pack(self.level, "pack.json", self.root)
        document["entities"] = [{"id": f"model-{i}", "model": "mesh-loot"} for i in range(128)]
        with self.assertRaisesRegex(ValueError, "128 model instances"):
            place_prop(document, prefabs, "loot", "one-too-many", [0, 0, 0])

    def test_prop_can_use_prefab_name_without_blocking_reimport(self):
        document, prefabs = merge_pack(self.level, "pack.json", self.root)
        document = place_prop(document, prefabs, "loot", "loot", [0, 0, 0])
        imported, catalog = merge_pack(document, "pack.json", self.root, prefabs)
        self.assertEqual(imported, document)
        self.assertEqual(catalog, prefabs)

    def test_combined_packs_allow_128_mesh_assets_but_keep_material_limit(self):
        self.level["assets"] = [{"id": f"local-{i}", "uri": "loot.obj"} for i in range(127)]
        linked = dict(self.level, asset_packs=["pack.json"])
        expanded, _, _ = resolve_asset_packs(linked, self.root)
        self.assertEqual(len(expanded["assets"]), 128)
        merged, _ = merge_pack(self.level, "pack.json", self.root)
        self.assertEqual(len(merged["assets"]), 128)
        self.level["assets"].append({"id": "overflow", "uri": "loot.obj"})
        with self.assertRaisesRegex(ValueError, "exceeds 128 assets"):
            resolve_asset_packs(dict(self.level, asset_packs=["pack.json"]), self.root)
        with self.assertRaisesRegex(ValueError, "exceeds 128 assets"):
            merge_pack(self.level, "pack.json", self.root)
        self.level["assets"] = []
        self.level["materials"] = [{"id": f"local-{i}", "color": [1, 1, 1, 1]} for i in range(64)]
        with self.assertRaisesRegex(ValueError, "exceeds 64 materials"):
            resolve_asset_packs(dict(self.level, asset_packs=["pack.json"]), self.root)


if __name__ == "__main__":
    unittest.main()
