"""Build provenance keeps authored debug maps bound to the tested ROM."""
import copy
import unittest

from tools.build import build_runtime_debug_map


class RuntimeTraceBuildTests(unittest.TestCase):
    def test_stream_map_keeps_authored_identity_and_adds_executable_identity(self):
        authored = dict(schema_version=1, package_sha256="a" * 64,
                        source_sha256="b" * 64, content_identity_sha256="c" * 64,
                        regions=[dict(id=1, source_id="courtyard")],
                        transitions=[dict(index=0, id="entry-gate", from_region=1, to_region=2)],
                        entities=[dict(id=3, source_id="front-door", object_index=0,
                                       source_provenance=dict(object_id=472))],
                        colliders=[dict(region=1, record=0, entity_id=3, source_id="front-door")])
        catalog = dict(levels=[dict(id="district", source="content/district.json",
                                   mission_manifest="content/district.mission.json",
                                   content=dict(source_sha256="d" * 64, mission_manifest_sha256="e" * 64,
                                                mission=dict(report=dict(runtime_debug_map=authored))))])
        original = copy.deepcopy(catalog)
        mapped = build_runtime_debug_map(catalog, "f" * 64, "1" * 64)
        self.assertEqual(mapped["build_id"], "f" * 64)
        self.assertEqual(mapped["rom_sha256"], "1" * 64)
        level = mapped["levels"][0]
        self.assertEqual(level["id"], "district")
        self.assertEqual(level["package_sha256"], "a" * 64)
        self.assertEqual(level["source_sha256"], "b" * 64)
        self.assertEqual(level["compiled_source_sha256"], "d" * 64)
        self.assertEqual(level["mission_manifest_sha256"], "e" * 64)
        self.assertEqual(level["transitions"], authored["transitions"])
        self.assertEqual(level["colliders"], authored["colliders"])
        self.assertEqual(level["entities"][0]["source_provenance"]["object_id"], 472)
        self.assertEqual(catalog, original)
        replacement = build_runtime_debug_map(catalog, "2" * 64, "3" * 64)
        self.assertNotEqual(mapped["build_id"], replacement["build_id"])
        self.assertNotEqual(mapped["rom_sha256"], replacement["rom_sha256"])
        self.assertEqual(mapped["levels"], replacement["levels"])

    def test_catalog_with_resident_level_needs_no_mission_package(self):
        catalog = dict(levels=[dict(id="first-room", source="content/first_room.json",
                                   content=dict(source_sha256="a" * 64))])
        mapped = build_runtime_debug_map(catalog, "b" * 64, "c" * 64)
        self.assertEqual(mapped["levels"][0]["id"], "first-room")
        self.assertEqual(mapped["levels"][0]["regions"], [])
        self.assertNotIn("package_sha256", mapped["levels"][0])


if __name__ == "__main__":
    unittest.main()
