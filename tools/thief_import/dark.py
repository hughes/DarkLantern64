"""Read Dark Engine tag databases and original WR/WRRGB world geometry.

This is an independent Python reader of the documented binary layouts, with no
game/third-party source or assets embedded. Provenance is in
docs/research/thief-worldrep.md. All coordinates remain in Dark's Z-up units;
conversion into Blender or game coordinates belongs to the scene builder.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from pathlib import Path
import struct
from typing import Mapping

Vec3 = tuple[float, float, float]


class DarkFormatError(ValueError):
    """A malformed or unsupported Dark Engine binary file."""


class Reader:
    """Bounds-checked little-endian cursor with useful file-offset errors."""

    def __init__(self, data: bytes, label: str = "data"):
        self.data = data
        self.label = label
        self.offset = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self.offset + size > len(self.data):
            raise DarkFormatError(
                f"{self.label}: truncated record at {self.offset:#x} "
                f"(need {size} bytes, have {len(self.data) - self.offset})")
        start = self.offset
        self.offset += size
        return self.data[start:self.offset]

    def unpack(self, fmt: str):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))

    def uint32(self) -> int:
        return self.unpack("I")[0]

    def vec3(self) -> Vec3:
        value = self.unpack("3f")
        if not all(math.isfinite(v) for v in value):
            raise DarkFormatError(f"{self.label}: non-finite vector at {self.offset - 12:#x}")
        return value


def _cstring(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("latin1")


@dataclass
class Database:
    chunks: dict[str, bytes]
    versions: dict[str, tuple[int, int]] = field(default_factory=dict)
    offsets: dict[str, int] = field(default_factory=dict)
    path: str = ""

    @classmethod
    def read(cls, path: str | Path) -> Database:
        return cls.from_bytes(Path(path).read_bytes(), str(path))

    @classmethod
    def from_bytes(cls, data: bytes, path: str = "") -> Database:
        r = Reader(data, path or "tag database")
        if len(data) < 272:
            raise DarkFormatError("tag database: missing 272-byte header")
        toc_offset, _zero, _one = r.unpack("3I")
        r.take(256)
        if r.uint32() != 0xEFBEADDE:
            raise DarkFormatError("tag database: invalid header signature")
        if toc_offset < 272 or toc_offset > len(data) - 4:
            raise DarkFormatError("tag database: table of contents outside file")
        r.offset = toc_offset
        count = r.uint32()
        if count > (len(data) - r.offset) // 20:
            raise DarkFormatError("tag database: truncated table of contents")
        entries = [r.unpack("12sII") for _ in range(count)]
        chunks, versions, offsets = {}, {}, {}
        ranges = []
        for raw_name, offset, size in entries:
            name = _cstring(raw_name)
            if not name or name in chunks:
                raise DarkFormatError(f"tag database: invalid/duplicate chunk name {name!r}")
            if offset < 272 or offset + 24 + size > toc_offset:
                raise DarkFormatError(f"tag database: chunk {name} outside data region")
            r.offset = offset
            header_name, major, minor, _pad = r.unpack("12sIII")
            if header_name != raw_name:
                raise DarkFormatError(f"tag database: chunk {name} name differs from inventory")
            chunks[name] = r.take(size)
            versions[name] = (major, minor)
            offsets[name] = offset + 24
            ranges.append((offset, offset + 24 + size, name))
        for previous, following in zip(sorted(ranges), sorted(ranges)[1:]):
            if previous[1] > following[0]:
                raise DarkFormatError(f"tag database: overlapping chunks {previous[2]}/{following[2]}")
        return cls(chunks, versions, offsets, path)


@dataclass(frozen=True)
class Plane:
    normal: Vec3
    distance: float


@dataclass(frozen=True)
class Polygon:
    flags: int
    vertex_indices: tuple[int, ...]
    plane_index: int
    target_cell: int
    is_portal: bool
    clut_id: int = 0
    motion_index: int = 0
    unknown: int = 0


@dataclass(frozen=True)
class RenderPolygon:
    axis_u: Vec3
    axis_v: Vec3
    offset_u: int
    offset_v: int
    texture_id: int
    origin_vertex: int
    scale: float
    center: Vec3
    texture_cache: int = 0


@dataclass(frozen=True)
class Lightmap:
    offset_u: int
    offset_v: int
    width: int
    height: int
    row_width: int
    anim_flags: int
    layers: tuple[bytes, ...]
    bytes_per_pixel: int


@dataclass
class Cell:
    id: int
    vertices: list[Vec3]
    polygons: list[Polygon]
    render_polygons: list[RenderPolygon]
    planes: list[Plane]
    medium: int
    flags: int
    center: Vec3
    radius: float
    portal_count: int
    portal_vertices: int
    flow_group: int
    anim_lights: tuple[int, ...]
    lightmaps: list[Lightmap]
    light_indices: tuple[int, ...]
    file_offset: int

    @property
    def solid_polygon_count(self) -> int:
        # Water boundaries are simultaneously rendered surfaces and portals.
        return min(len(self.render_polygons), len(self.polygons) - self.portal_count)

    def polygon_vertices(self, index: int) -> tuple[Vec3, ...]:
        return tuple(self.vertices[i] for i in self.polygons[index].vertex_indices)


@dataclass(frozen=True)
class BspNode:
    parent_index: int
    flags: int
    cell: int
    plane: int
    front: int
    back: int

    @property
    def is_leaf(self) -> bool:
        return bool(self.flags & 1)

    @property
    def reversed(self) -> bool:
        return bool(self.flags & 4)


@dataclass
class WorldRep:
    chunk_name: str
    cells: list[Cell]
    bsp_planes: list[Plane]
    bsp_nodes: list[BspNode]
    declared_data_size: int
    geometry_end_offset: int
    bsp_end_offset: int
    trailing_data: bytes

    def stats(self) -> dict:
        vertices = [v for cell in self.cells for v in cell.vertices]
        bounds = None
        if vertices:
            bounds = [[min(v[a] for v in vertices), max(v[a] for v in vertices)] for a in range(3)]
        return {
            "chunk": self.chunk_name,
            "cells": len(self.cells),
            "cell_vertices": len(vertices),
            "polygons": sum(len(c.polygons) for c in self.cells),
            "render_polygons": sum(len(c.render_polygons) for c in self.cells),
            "solid_polygons": sum(c.solid_polygon_count for c in self.cells),
            "portals": sum(c.portal_count for c in self.cells),
            "triangles": sum(len(p.vertex_indices) - 2 for c in self.cells for p in c.polygons[:len(c.render_polygons)]),
            "solid_triangles": sum(len(p.vertex_indices) - 2 for c in self.cells for p in c.polygons[:c.solid_polygon_count]),
            "water_cells": sum(c.medium == 2 for c in self.cells),
            "water_faces": sum(len(c.render_polygons) - c.solid_polygon_count for c in self.cells),
            "used_texture_ids": sorted({p.texture_id for c in self.cells for p in c.render_polygons}),
            "bsp_planes": len(self.bsp_planes),
            "bsp_nodes": len(self.bsp_nodes),
            "bounds": bounds,
            "geometry_end_offset": self.geometry_end_offset,
            "bsp_end_offset": self.bsp_end_offset,
            "trailing_bytes": len(self.trailing_data),
        }


def _read_plane(r: Reader) -> Plane:
    normal = r.vec3()
    distance = r.unpack("f")[0]
    if not math.isfinite(distance):
        raise DarkFormatError(f"{r.label}: non-finite plane distance")
    return Plane(normal, distance)


def _read_cell(r: Reader, cell_id: int, bpp: int) -> Cell:
    start = r.offset
    nv, np, nr, nport, nplane, medium, flags, nxn, ni, na, flow, *sphere = r.unpack("7BIHBB4f")
    if nr > np or nport > np or not all(math.isfinite(v) for v in sphere):
        raise DarkFormatError(f"{r.label}: invalid cell {cell_id} header at {start:#x}")
    vertices = [r.vec3() for _ in range(nv)]
    records = [r.unpack("4BH2B") for _ in range(np)]
    render_polygons = []
    for _ in range(nr):
        axis_u, axis_v = r.vec3(), r.vec3()
        u, v, texture, origin, cache, scale, *center = r.unpack("hhBBH4f")
        render_polygons.append(RenderPolygon(axis_u, axis_v, u, v, texture, origin, scale, tuple(center), cache))
    index_count = r.uint32()
    if index_count != ni or index_count != sum(p[1] for p in records):
        raise DarkFormatError(f"{r.label}: inconsistent vertex-list size in cell {cell_id}")
    indices = r.take(index_count)
    if any(i >= nv for i in indices):
        raise DarkFormatError(f"{r.label}: vertex index outside cell {cell_id}")
    polygons = []
    offset = 0
    for index, (pflags, count, plane, clut, target, motion, unknown) in enumerate(records):
        if count < 3 or plane >= nplane:
            raise DarkFormatError(f"{r.label}: invalid polygon {cell_id}/{index}")
        polygons.append(Polygon(pflags, tuple(indices[offset:offset + count]), plane, target,
                                index >= np - nport, clut, motion, unknown))
        offset += count
        if index < nr and render_polygons[index].origin_vertex >= count:
            raise DarkFormatError(f"{r.label}: texture origin outside polygon {cell_id}/{index}")
    planes = [_read_plane(r) for _ in range(nplane)]
    anim_lights = r.unpack(f"{na}H")
    light_records = [r.unpack("hhHBBIII") for _ in range(nr)]
    lightmaps = []
    for u, v, row_width, height, width, _static, _dynamic, anim in light_records:
        # The uint16 width is the actual row stride. The uint8 copy is normally
        # equal, but preserving both handles padded maps without shifting cells.
        size = row_width * height * bpp
        if anim.bit_length() > na:
            raise DarkFormatError(f"{r.label}: lightmap animation mask outside cell {cell_id}")
        layers = tuple(r.take(size) for _ in range(1 + anim.bit_count()))
        lightmaps.append(Lightmap(u, v, width, height, row_width, anim, layers, bpp))
    light_count = r.uint32()
    if light_count > (len(r.data) - r.offset) // 2:
        raise DarkFormatError(f"{r.label}: light list outside cell {cell_id}")
    light_indices = r.unpack(f"{light_count}H")
    return Cell(cell_id, vertices, polygons, render_polygons, planes, medium, flags,
                tuple(sphere[:3]), sphere[3], nport, nxn, flow, anim_lights,
                lightmaps, light_indices, start)


def read_worldrep(database: Database | Mapping[str, bytes]) -> WorldRep:
    chunks = database.chunks if isinstance(database, Database) else database
    if "WREXT" in chunks:
        raise DarkFormatError("NewDark WREXT is not yet supported; use an original WR/WRRGB mission")
    name = "WR" if "WR" in chunks else "WRRGB" if "WRRGB" in chunks else None
    if name is None:
        raise DarkFormatError("mission has no WR/WRRGB geometry chunk")
    r = Reader(chunks[name], name)
    data_size, count = r.unpack("II")
    if count > (len(r.data) - r.offset) // 31:
        raise DarkFormatError(f"{name}: impossible cell count {count}")
    cells = [_read_cell(r, index, 1 if name == "WR" else 2) for index in range(count)]
    geometry_end = r.offset
    extra_count = r.uint32()
    if extra_count > (len(r.data) - r.offset) // 16:
        raise DarkFormatError(f"{name}: impossible extra plane count")
    bsp_planes = [_read_plane(r) for _ in range(extra_count)]
    node_count = r.uint32()
    if node_count > (len(r.data) - r.offset) // 20:
        raise DarkFormatError(f"{name}: impossible BSP node count")
    bsp_nodes = []
    for _ in range(node_count):
        parent_flags, cell, plane, front, back = r.unpack("IiiII")
        bsp_nodes.append(BspNode(parent_flags & 0xFFFFFF, parent_flags >> 24, cell, plane, front, back))
    for cell in cells:
        for polygon in cell.polygons:
            if polygon.is_portal and polygon.target_cell >= count:
                raise DarkFormatError(f"{name}: portal from cell {cell.id} to nonexistent cell {polygon.target_cell}")
    for index, node in enumerate(bsp_nodes):
        if node.is_leaf:
            if node.front >= count:
                raise DarkFormatError(f"{name}: BSP leaf {index} references nonexistent cell")
        else:
            planes = bsp_planes if node.cell < 0 else cells[node.cell].planes if node.cell < count else []
            if node.plane < 0 or node.plane >= len(planes):
                raise DarkFormatError(f"{name}: BSP node {index} references nonexistent plane")
            if any(child != 0xFFFFFF and child >= node_count for child in (node.front, node.back)):
                raise DarkFormatError(f"{name}: BSP node {index} references nonexistent child")
    bsp_end = r.offset
    return WorldRep(name, cells, bsp_planes, bsp_nodes, data_size, geometry_end, bsp_end, r.take(len(r.data) - r.offset))


def _dot(a: Vec3, b: Vec3) -> float:
    return sum(x * y for x, y in zip(a, b))


def polygon_uvs(cell: Cell, polygon_index: int, width: int = 64, height: int = 64,
                *, flip_v: bool = False) -> list[tuple[float, float]]:
    """Original texture repeats for every corner, including skewed UV axes.

    Dark stores world-space *basis vectors*, not projection covectors. Invert
    their Gram matrix to obtain coordinates. Offset is signed 12-bit fixed-point
    per 64 texels, and origin_vertex indexes the polygon's corner list. Blender
    image coordinates conventionally need flip_v=True when loading GIF/PCX art.
    """
    if width <= 0 or height <= 0:
        raise ValueError("texture dimensions must be positive")
    render = cell.render_polygons[polygon_index]
    polygon = cell.polygons[polygon_index]
    origin = cell.vertices[polygon.vertex_indices[render.origin_vertex]]
    u, v = render.axis_u, render.axis_v
    uu, vv, uv = _dot(u, u), _dot(v, v), _dot(u, v)
    determinant = uu * vv - uv * uv
    if determinant <= max(uu * vv, 1e-30) * 1e-12:
        raise DarkFormatError(f"degenerate texture basis on polygon {cell.id}/{polygon_index}")
    values = []
    for vertex in cell.polygon_vertices(polygon_index):
        delta = tuple(a - b for a, b in zip(vertex, origin))
        du, dv = _dot(delta, u), _dot(delta, v)
        tu = ((du * vv - dv * uv) / determinant + render.offset_u / 4096.0) * 64.0 / width
        tv = ((dv * uu - du * uv) / determinant + render.offset_v / 4096.0) * 64.0 / height
        values.append((tu, 1.0 - tv if flip_v else tv))
    return values


def polygon_triangles(cell: Cell, polygon_index: int) -> list[tuple[int, int, int]]:
    """Convex WR polygons have an exact triangle-fan representation."""
    indices = cell.polygons[polygon_index].vertex_indices
    return [(indices[0], indices[i], indices[i + 1]) for i in range(1, len(indices) - 1)]


def read_database(path: str | Path) -> Database:
    return Database.read(path)


def read_textures(database: Database | Mapping[str, bytes]) -> list[dict]:
    """Decode TXLIST resource paths without assuming one directory per texture."""
    chunks = database.chunks if isinstance(database, Database) else database
    if "TXLIST" not in chunks:
        raise DarkFormatError("mission has no TXLIST texture list")
    r = Reader(chunks["TXLIST"], "TXLIST")
    size, count, token_count = r.unpack("III")
    if size != len(r.data) or 12 + token_count * 16 + count * 20 != size:
        raise DarkFormatError("TXLIST: inconsistent texture-list size")
    tokens = [_cstring(r.take(16)) for _ in range(token_count)]
    textures = []
    for texture_id in range(count):
        references = r.unpack("4B")
        name = _cstring(r.take(16))
        parts = []
        for reference in references:
            if reference == 0:
                break
            if reference > token_count:
                raise DarkFormatError(f"TXLIST: invalid path token in texture {texture_id}")
            parts.append(tokens[reference - 1])
        path = "/".join(parts + [name])
        family = parts[-1] if parts else ""
        textures.append({"id": texture_id, "family": family, "name": name,
                         "path": path, "tokens": list(references)})
    return textures


def read_light_table(world: WorldRep) -> dict:
    """Original WR/WRRGB compiled lighting, including stripped editor lights.

    Original worldreps reserve 768 ordinary records and 32 scratch records.
    Scratch slots can contain old compiler data even when dynamic_count is zero;
    they are exposed separately and never mistaken for active static lights.
    Intensity, spotlight cone cosines, and radius remain in source units.
    The subsequent animation-cell mapping and CSG dump are preserved as bytes.
    """
    r = Reader(world.trailing_data, f"{world.chunk_name} light table")
    static_count, dynamic_count = r.unpack("II")
    if static_count > 768 or dynamic_count > 32:
        raise DarkFormatError("original WR light count exceeds its fixed slot capacity")
    records = []
    for index in range(800):
        location, direction = r.vec3(), r.vec3()
        color = r.vec3() if world.chunk_name == "WRRGB" else (r.unpack("f")[0],) * 3
        inner, outer, radius = r.unpack("3f")
        if not all(math.isfinite(v) for v in (*color, inner, outer, radius)):
            raise DarkFormatError(f"invalid compiled light record {index}")
        records.append({"id": index, "position": list(location), "direction": list(direction),
                        "color": list(color), "intensity": max(color),
                        "inner_angle_cos": inner, "outer_angle_cos": outer, "radius": radius})
    map_count = r.uint32()
    if map_count > (len(r.data) - r.offset) // 4:
        raise DarkFormatError("WR animated-light cell mapping outside chunk")
    mappings = [list(r.unpack("HH")) for _ in range(map_count)]
    return {"static_count": static_count, "dynamic_count": dynamic_count,
            "lights": records[:static_count], "dynamic_lights": records[768:768 + dynamic_count],
            "scratch_lights": records[768:], "animation_cell_mapping": mappings,
            "data_end_offset": world.bsp_end_offset + r.offset,
            "trailing_data": r.take(len(r.data) - r.offset)}
