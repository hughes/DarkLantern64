"""Binary-layout regression fixtures generated without copyrighted game data."""
import struct
import unittest

from tools.thief_import.dark import (
    Database, DarkFormatError, polygon_triangles, polygon_uvs, read_light_table, read_textures,
    read_worldrep,
)


def pack(fmt, *values):
    return struct.pack("<" + fmt, *values)


def database_bytes(chunks):
    body = bytearray(272)
    entries = []
    for name, payload in chunks.items():
        raw = name.encode().ljust(12, b"\0")
        entries.append(pack("12sII", raw, len(body), len(payload)))
        body += pack("12sIII", raw, 0, 23, 0) + payload
    toc = len(body)
    struct.pack_into("<III", body, 0, toc, 0, 1)
    struct.pack_into("<I", body, 268, 0xEFBEADDE)
    body += pack("I", len(entries)) + b"".join(entries)
    return bytes(body)


def world_bytes(*, bpp=1, skew=False):
    # One cell, one ordinary face, one face that is both rendered and a portal.
    vertices = [(0., 0., 0.), (2., 0., 0.), (2., 2., 0.), (0., 2., 0.)]
    cell = pack("7BIHBB4f", 4, 2, 2, 1, 1, 1, 0, 4, 8, 1, 0, 1, 1, 1, 2)
    cell += b"".join(pack("3f", *v) for v in vertices)
    cell += pack("4BH2B", 0, 4, 0, 0, 0, 0, 0)
    cell += pack("4BH2B", 20, 4, 0, 0, 0, 0, 0)
    axis_u = (2., 0., 0.)
    axis_v = (1., 2., 0.) if skew else (0., 2., 0.)
    for texture in (1, 247):
        cell += pack("6fhhBBH4f", *axis_u, *axis_v, 2048, -1024, texture, 1, 0, 1, 1, 1, 0)
    cell += pack("I", 8) + bytes([0, 1, 2, 3] * 2)
    cell += pack("4f", 0, 0, 1, 0)
    cell += pack("H", 3)  # animated light ID
    cell += pack("hhHBBIII", 0, 0, 2, 2, 2, 0, 0, 1)
    cell += pack("hhHBBIII", 0, 0, 2, 1, 2, 0, 0, 0)
    cell += bytes([20] * (8 * bpp) + [30] * (2 * bpp))
    cell += pack("I2H", 2, 1, 3)
    # A BSP leaf, then untouched downstream lighting/CSG data.
    bsp = pack("II", 0, 1) + pack("IiiII", 0x01FFFFFF, -1, -1, 0, 0xFFFFFF)
    return pack("II", len(cell), 1) + cell + bsp + b"opaque-lighting-CSG"


class TagDatabaseTests(unittest.TestCase):
    def test_chunks_versions_and_offsets(self):
        encoded = database_bytes({"WR": b"hello", "P$Position": b""})
        db = Database.from_bytes(encoded)
        self.assertEqual(db.chunks, {"WR": b"hello", "P$Position": b""})
        self.assertEqual(db.versions["WR"], (0, 23))
        self.assertEqual(db.offsets["WR"], 296)

    def test_invalid_signature_rejected(self):
        data = bytearray(database_bytes({"WR": b"hello"}))
        data[268] ^= 1
        with self.assertRaisesRegex(DarkFormatError, "signature"):
            Database.from_bytes(bytes(data))

    def test_chunk_size_cannot_cover_table_of_contents(self):
        data = bytearray(database_bytes({"WR": b"hello"}))
        toc = struct.unpack_from("<I", data)[0]
        struct.pack_into("<I", data, toc + 4 + 16, 10000)
        with self.assertRaisesRegex(DarkFormatError, "outside data region"):
            Database.from_bytes(bytes(data))


class WorldRepTests(unittest.TestCase):
    def world(self, **kwargs):
        return read_worldrep({"WR": world_bytes(**kwargs)})

    def test_portals_overlap_render_faces_and_preserve_lighting(self):
        world = self.world()
        cell = world.cells[0]
        self.assertEqual(cell.solid_polygon_count, 1)
        self.assertFalse(cell.polygons[0].is_portal)
        self.assertTrue(cell.polygons[1].is_portal)
        self.assertEqual(cell.polygons[1].target_cell, 0)
        self.assertEqual(cell.anim_lights, (3,))
        self.assertEqual(cell.light_indices, (1, 3))
        self.assertEqual(len(cell.lightmaps[0].layers), 2)
        self.assertEqual(cell.lightmaps[0].layers[0], bytes([20] * 4))
        self.assertEqual(world.trailing_data, b"opaque-lighting-CSG")
        self.assertTrue(world.bsp_nodes[0].is_leaf)
        self.assertEqual(world.bsp_nodes[0].parent_index, 0xFFFFFF)
        self.assertEqual(world.stats()["solid_triangles"], 2)

    def test_rgb_lightmap_stride_does_not_shift_next_record(self):
        world = read_worldrep({"WRRGB": world_bytes(bpp=2)})
        self.assertEqual(world.cells[0].light_indices, (1, 3))
        self.assertEqual(len(world.cells[0].lightmaps[0].layers[0]), 8)

    def test_uv_origin_offset_texture_dimensions_and_image_flip(self):
        cell = self.world().cells[0]
        uvs = polygon_uvs(cell, 0, 128, 64)
        self.assertEqual(uvs, [(-.25, -.25), (.25, -.25), (.25, .75), (-.25, .75)])
        self.assertEqual(polygon_uvs(cell, 0, 128, 64, flip_v=True)[0], (-.25, 1.25))

    def test_skewed_texture_axes_use_inverse_basis(self):
        cell = self.world(skew=True).cells[0]
        # Relative to corner 1, corner 2=(0,2) = -.5 * U + 1 * V.
        self.assertEqual(polygon_uvs(cell, 0)[2], (0., .75))

    def test_convex_faces_have_exact_fan(self):
        self.assertEqual(polygon_triangles(self.world().cells[0], 0), [(0, 1, 2), (0, 2, 3)])

    def test_truncated_geometry_and_bsp_records_are_rejected(self):
        data = world_bytes()
        end = self.world().bsp_end_offset
        for length in (7, 20, 50, 150, end - 1):
            with self.subTest(length=length), self.assertRaises(DarkFormatError):
                read_worldrep({"WR": data[:length]})

    def test_invalid_polygon_vertex_index_is_rejected(self):
        data = bytearray(world_bytes())
        indices_start = 8 + 31 + 4 * 12 + 2 * 8 + 2 * 48 + 4
        data[indices_start] = 4
        with self.assertRaisesRegex(DarkFormatError, "vertex index"):
            read_worldrep({"WR": bytes(data)})

    def test_newdark_geometry_is_explicitly_unsupported(self):
        with self.assertRaisesRegex(DarkFormatError, "WREXT"):
            read_worldrep({"WREXT": b""})


class TextureListTests(unittest.TestCase):
    def test_resource_tokens_are_one_based_and_name_ends_at_nul(self):
        tokens = b"fam\0".ljust(16, b"\0") + b"city\0".ljust(16, b"\0")
        record = bytes([1, 2, 0, 0]) + b"BRICK\0".ljust(16, b"\x02")
        textures = read_textures({"TXLIST": pack("III", 64, 1, 2) + tokens + record})
        self.assertEqual(textures[0]["path"], "fam/city/BRICK")
        self.assertEqual(textures[0]["family"], "city")

    def test_bad_token_is_rejected(self):
        record = bytes([2, 0, 0, 0]) + b"BRICK\0".ljust(16, b"\0")
        with self.assertRaisesRegex(DarkFormatError, "path token"):
            read_textures({"TXLIST": pack("III", 48, 1, 1) + b"fam\0".ljust(16, b"\0") + record})


class LightTableTests(unittest.TestCase):
    def test_static_count_excludes_reserved_and_scratch_slots(self):
        world = read_worldrep({"WR": world_bytes()})
        active = pack("10f", 1, 2, 3, 0, 0, -1, 4, -1, .5, 10)
        scratch = pack("10f", 90, 80, 70, 0, 0, 0, 2, -1, 0, 0)
        world.trailing_data = (pack("II", 1, 0) + active + bytes(767 * 40)
                               + scratch + bytes(31 * 40) + pack("IHH", 1, 0, 3) + b"CSG")
        table = read_light_table(world)
        self.assertEqual(len(table["lights"]), 1)
        self.assertEqual(table["lights"][0]["position"], [1., 2., 3.])
        self.assertEqual(table["lights"][0]["color"], [4., 4., 4.])
        self.assertEqual(table["dynamic_lights"], [])
        self.assertEqual(table["scratch_lights"][0]["position"], [90., 80., 70.])
        self.assertEqual(table["animation_cell_mapping"], [[0, 3]])
        self.assertEqual(table["trailing_data"], b"CSG")

    def test_rgb_light_color_uses_three_floats(self):
        world = read_worldrep({"WRRGB": world_bytes(bpp=2)})
        active = pack("12f", 1, 2, 3, 0, 0, -1, 4, 2, 1, -1, .5, 10)
        world.trailing_data = pack("II", 1, 0) + active + bytes(799 * 48) + pack("I", 0)
        self.assertEqual(read_light_table(world)["lights"][0]["color"], [4., 2., 1.])


if __name__ == "__main__":
    unittest.main()
