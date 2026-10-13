"""Focused binary and inheritance checks; optional installed-game validation."""
import os
from pathlib import Path
import struct
import unittest

from tools.thief_import.objects import (ObjectDatabase, decode_light, decode_position,
                                       extract_objects, link_records, merge_compiled_lights, property_records)


def props(rows):
    return b"".join(struct.pack("<iI", oid, len(raw)) + raw for oid, raw in rows)


def links(rows, flavor=1):
    return b"".join(struct.pack("<IiiH", lid, source, target, flavor) for lid, source, target in rows)


def parents(rows):
    return {"L$MetaProp": links([(lid, source, target) for lid, source, target, _ in rows]),
            "LD$MetaProp": struct.pack("<I", 4) + b"".join(struct.pack("<Ii", lid, priority)
                                                          for lid, _, _, priority in rows)}


def string(value):
    raw = value.encode("ascii") + b"\0"
    return struct.pack("<I", len(raw)) + raw


def position(x=0, y=0, z=0, yaw=0):
    return struct.pack("<fffhhHHH", x, y, z, -1, -1, 0, 0, round(yaw * 65536 / 360) % 65536)


class ObjectFormatTests(unittest.TestCase):
    def test_variable_property_rows_and_signed_ids(self):
        self.assertEqual(property_records(props([(-15, b"a"), (7, b"bc")])), {-15: b"a", 7: b"bc"})

    def test_truncated_and_duplicate_records_are_rejected(self):
        for raw in (b"a", struct.pack("<iI", 7, 5) + b"abc", props([(7, b"a"), (7, b"b")])):
            with self.assertRaises(ValueError):
                property_records(raw)
        with self.assertRaises(ValueError):
            link_records(b"a" * 13)

    def test_position_uses_unsigned_binary_angles_and_z_up(self):
        row = decode_position(position(1, 2, -3, 270))
        self.assertEqual(row["position"], [1.0, 2.0, -3.0])
        self.assertEqual(row["facing_degrees"], [0.0, 0.0, 270.0])
        with self.assertRaises(ValueError):
            decode_position(position(float("nan")))

    def test_mission_override_and_priority_ordered_metaproperties(self):
        gam = {**parents([(1, 7, -1, 0), (2, 7, -2, 1024), (3, -2, -3, 0)]),
               "P$AI": props([(-1, b"Default\0"), (-3, b"Human with bow\0")])}
        db = ObjectDatabase(gam, {})
        self.assertEqual(db.property(7, "P$AI"), b"Human with bow\0")
        self.assertEqual(db.property(7, "P$AI", archetype_only=True), b"Default\0")
        self.assertEqual(db.archetype(7)["id"], -1)
        db = ObjectDatabase(gam, {"P$AI": props([(7, b"instance\0")])})
        self.assertEqual(db.property(7, "P$AI"), b"instance\0")

    def test_inheritance_cycle_is_rejected(self):
        db = ObjectDatabase(parents([(1, -1, -2, 0), (2, -2, -1, 0)]), {})
        with self.assertRaisesRegex(ValueError, "cycle"):
            db.property(-1, "P$AI")

    def test_surface_material_uses_texture_archetype_not_filename_guess(self):
        gam = {**parents([(1, -20, -5, 0)]),
               "P$SymName": props([(-20, string("t_fam/core/wdplnk2"))]),
               "P$Material ": props([(-5, struct.pack("<I", 0x12345678) + b"Material Wood\0" + bytes(238))])}
        db = ObjectDatabase(gam, {})
        self.assertEqual(db.texture_surface_material("CORE", "WDPLNK2.PCX"),
                         {"object_id": -20, "tags": "Material Wood", "surface": "wood"})
        self.assertIsNone(db.texture_surface_material("core", "metal_floor")["surface"])

    def test_original_static_and_animated_light_layouts(self):
        lamp = decode_light(struct.pack("<5f", 125, 1, 2, 3, 0))
        self.assertEqual(lamp["brightness"], 125)
        self.assertEqual(lamp["offset"], [1, 2, 3])
        self.assertEqual(lamp["radius"], 0)
        raw = bytearray(68)
        struct.pack_into("<3f", raw, 4, 1, 2, 3)
        struct.pack_into("<H", raw, 26, 3)
        struct.pack_into("<ii", raw, 28, 100, 200)
        struct.pack_into("<ff", raw, 36, 25, 100)
        struct.pack_into("<f", raw, 60, 12)
        lamp = decode_light(bytes(raw), animated=True)
        self.assertEqual(lamp["brightness"], 25)
        self.assertEqual(lamp["radius"], 12)
        struct.pack_into("<H", raw, 26, 5)
        self.assertEqual(decode_light(bytes(raw), animated=True)["brightness"], 0)

    def test_light_provenance_uses_exact_animation_index_and_unique_static_matches(self):
        properties = [dict(source_id=1, animated=True, light_table_index=10,
                           position=[0, 0, 0], brightness=100, color=[1, 1, 1]),
                      dict(source_id=2, animated=False, position=[20, 0, 0],
                           brightness=125, color=[1, .5, .25])]
        table = [dict(id=10, position=[100, 0, 0], intensity=100 / 32, color=[100 / 32] * 3),
                 dict(id=11, position=[25.9, 0, 0], intensity=125 / 32, color=[125 / 32] * 3),
                 dict(id=12, position=[20, 0, 0], intensity=127 / 32, color=[127 / 32] * 3),
                 dict(id=13, position=[26, 0, 0], intensity=125 / 32, color=[125 / 32] * 3)]
        result = merge_compiled_lights(properties, table)
        self.assertFalse(result["lights"][0]["match_is_approximate"])
        self.assertEqual(result["lights"][0]["matching"], "animated_table_index")
        self.assertEqual(result["lights"][1]["matched_object"], 2)
        self.assertEqual(result["lights"][1]["position"], [25.9, 0, 0])
        self.assertEqual(result["lights"][1]["color"], [1, .5, .25])
        self.assertIsNone(result["lights"][2]["matched_object"])
        self.assertIsNone(result["lights"][3]["matched_object"])
        self.assertEqual(result["stats"]["table_only_lights"], 2)


class ObjectExtractionTests(unittest.TestCase):
    def fixture(self):
        gam = {**parents([(1, -2, -1, 0)]),
               "P$SymName": props([(-1, string("Object")), (-2, string("guard")), (-3, string("GoldCoin"))]),
               "P$AI": props([(-2, b"Human with sword\0")]),
               "P$Loot": props([(-3, struct.pack("<iiiI", 50, 0, 0, 0))])}
        mis = {**parents([(10, 8, -2, 0), (11, 9, -3, 0), (12, 10, -3, 0)]),
               "P$Position": props([(7, position()), (8, position(1, 0)), (9, position(99, 99)),
                                     (10, position(0, 0)), (11, position(2, 0)), (12, position(3, 0)),
                                     (13, position(4, 4))]),
               "L$PlayerFac": links([(40, 7, -100)]),
               "L$Contains": links([(41, 7, 10), (42, 13, 9)]),
               "L$AIPatrol": links([(43, 11, 12), (44, 12, 11)]),
               "P$AI_Patrol": props([(8, struct.pack("<i", 1))]),
               "P$Light": props([(13, struct.pack("<5f", 125, 1, 0, 0, 0))])}
        return gam, mis

    def test_inventory_loot_is_omitted_and_container_loot_has_provenance(self):
        result = extract_objects(*self.fixture())
        self.assertEqual(result["stats"]["loot_value"], 50)
        self.assertEqual(result["omitted_inventory_loot"][0]["source_id"], 10)
        item = result["loot"][0]
        self.assertEqual(item["position"], [4, 4, 0])
        self.assertEqual(item["source_position"], [99, 99, 0])
        self.assertEqual(item["placement"], "container_position")
        self.assertEqual(result["lights"][0]["position"], [5, 4, 0])

    def test_nearest_patrol_inference_is_distinguished_from_explicit_link(self):
        gam, mis = self.fixture()
        guard = extract_objects(gam, mis)["guards"][0]
        self.assertEqual(guard["patrol_assignment"], "nearest_source_patrol_node")
        self.assertEqual(guard["patrol_node_ids"], [11, 12])
        mis["L$AICurrent"] = links([(45, 8, 12)])
        guard = extract_objects(gam, mis)["guards"][0]
        self.assertEqual(guard["patrol_assignment"], "AICurrentPatrol")
        self.assertEqual(guard["patrol_node_ids"], [12, 11])

    def test_difficulty_destroy_mask_omits_actors(self):
        gam, mis = self.fixture()
        mis["P$DiffDestr"] = props([(8, struct.pack("<i", 1))])
        self.assertEqual(len(extract_objects(gam, mis, difficulty=0)["guards"]), 0)
        self.assertEqual(len(extract_objects(gam, mis, difficulty=2)["guards"]), 1)

    def test_bafford_scepter_is_a_scripted_goal_separate_from_loot(self):
        gam, mis = self.fixture()
        gam["P$SymName"] += props([(-4, string("BaffordScepter"))])
        mis["L$MetaProp"] += links([(14, 74, -4)])
        mis["LD$MetaProp"] += struct.pack("<Ii", 14, 0)
        mis["P$Position"] += props([(74, position(1, 2, 3))])
        result = extract_objects(gam, mis)
        self.assertEqual(result["objectives"][0]["source_id"], 74)
        self.assertEqual(result["objectives"][0]["objective_role"], "bafford_scepter")
        self.assertEqual(result["stats"]["loot_value"], 50)
        self.assertEqual(result["stats"]["objectives"], 1)


@unittest.skipUnless(os.environ.get("DL_THIEF_GOLD"), "Set DL_THIEF_GOLD to verify locally installed source assets")
class InstalledThiefObjectsTests(unittest.TestCase):
    def test_bafford_placements_and_loot_total(self):
        from tools.thief_import.dark import Database, read_light_table, read_worldrep
        install = Path(os.environ["DL_THIEF_GOLD"])
        mission = Database.read(install / "miss2.mis")
        result = extract_objects(Database.read(install / "DARK.GAM"), mission)
        self.assertEqual(result["stats"]["guards"], 19)
        self.assertEqual(result["stats"]["loot"], 23)
        self.assertEqual(result["stats"]["loot_value"], 1429)
        self.assertEqual(result["stats"]["lights"], 259)
        self.assertEqual(result["stats"]["objectives"], 1)
        self.assertEqual(result["objectives"][0]["source_id"], 74)
        self.assertEqual(result["spawn"]["source_id"], 439)
        lighting = merge_compiled_lights(result["lights"], read_light_table(read_worldrep(mission))["lights"])
        self.assertEqual(lighting["stats"], dict(compiled_lights=365, animated_exact_matches=84,
                         static_approximate_matches=174, table_only_lights=107,
                         unmatched_property_object_ids=[955]))


if __name__ == "__main__":
    unittest.main()
