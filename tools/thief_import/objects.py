"""Read Dark Engine object properties, inheritance, and gameplay placements.

No installed game data is bundled. Inputs are the ``chunks`` mappings provided
by :mod:`tools.thief_import.dark`. Positions remain in Thief's Z-up source units;
the scene converter owns axis and unit conversion. Binary layouts are verified
against OpenDarkEngine's Thief 1 property definitions and original MIS/GAM data.

References:
https://github.com/volca02/opendarkengine/blob/master/scripts/thief1/t1-types.dtype
https://github.com/volca02/opendarkengine/blob/master/src/services/link/Relation.cpp
https://github.com/volca02/opendarkengine/blob/master/src/services/inherit/CachedInheritor.cpp
"""
from __future__ import annotations

from dataclasses import dataclass
import colorsys
import math
import struct
from typing import Mapping


PLACEMENT_PROPERTIES = {
    "P$Position", "P$SymName", "P$ModelName", "P$HasRefs", "P$Scale", "P$RenderTyp",
    "P$Material ", "P$Material", "P$AI", "P$AI_Mode", "P$AI_Team", "P$AI_Patrol",
    "P$DiffDestr", "P$Loot", "P$Light", "P$AnimLight", "P$LightColo",
}


def property_records(data: bytes, name: str = "property") -> dict[int, bytes]:
    """Decode repeated signed object-ID / unsigned byte-length / payload rows."""
    result = {}
    offset = 0
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError(f"{name}: truncated property record header")
        oid, size = struct.unpack_from("<iI", data, offset)
        offset += 8
        if size > len(data) - offset:
            raise ValueError(f"{name}: object {oid} payload exceeds chunk")
        if oid in result:
            raise ValueError(f"{name}: duplicate object {oid}")
        result[oid] = data[offset:offset + size]
        offset += size
    return result


@dataclass(frozen=True)
class Link:
    id: int
    source: int
    target: int
    flavor: int


def link_records(data: bytes, name: str = "link") -> list[Link]:
    if len(data) % 14:
        raise ValueError(f"{name}: truncated 14-byte link record")
    return [Link(*row) for row in struct.iter_unpack("<IiiH", data)]


def link_data_records(data: bytes, name: str = "link data") -> dict[int, bytes]:
    if not data:
        return {}
    if len(data) < 4:
        raise ValueError(f"{name}: missing data-size header")
    size = struct.unpack_from("<I", data)[0]
    if size > 1024 * 1024 or (len(data) - 4) % (size + 4):
        raise ValueError(f"{name}: invalid link-data size")
    result = {}
    for offset in range(4, len(data), size + 4):
        lid = struct.unpack_from("<I", data, offset)[0]
        if lid in result:
            raise ValueError(f"{name}: duplicate link {lid}")
        result[lid] = data[offset + 4:offset + 4 + size]
    return result


def _chunks(database) -> Mapping[str, bytes]:
    return database.chunks if hasattr(database, "chunks") else database


def _label(data: bytes | None, default: str = "") -> str:
    return data.split(b"\0", 1)[0].decode("latin1") if data else default


def _string(data: bytes | None, default: str = "") -> str:
    if data is None:
        return default
    if len(data) < 4:
        raise ValueError("Truncated Dark Engine string property")
    size = struct.unpack_from("<I", data)[0]
    if size > len(data) - 4:
        raise ValueError("Dark Engine string length exceeds property")
    return _label(data[4:4 + size], default)


def _integer(data: bytes | None, default: int = 0) -> int:
    if data is None:
        return default
    if len(data) < 4:
        raise ValueError("Truncated Dark Engine integer property")
    return struct.unpack_from("<i", data)[0]


def decode_position(data: bytes) -> dict:
    if len(data) != 22:
        raise ValueError(f"Position: expected 22 bytes, got {len(data)}")
    x, y, z, cell, unused, rx, ry, rz = struct.unpack("<fffhhHHH", data)
    if not all(math.isfinite(value) for value in (x, y, z)):
        raise ValueError("Position: non-finite coordinates")
    return dict(position=[x, y, z], facing_degrees=[a * 360 / 65536 for a in (rx, ry, rz)],
                cell=cell)


class ObjectDatabase:
    """Mission-over-gamesystem properties and priority-ordered inheritance."""

    def __init__(self, gamesystem, mission):
        self.properties: dict[str, dict[int, bytes]] = {}
        self.links: dict[str, list[Link]] = {}
        self.parents: dict[int, list[tuple[int, int]]] = {}
        self.mission_chunks = _chunks(mission)
        for database in (gamesystem, mission):
            chunks = _chunks(database)
            for name, data in chunks.items():
                if name in PLACEMENT_PROPERTIES:
                    self.properties.setdefault(name, {}).update(property_records(data, name))
                elif name.startswith("L$"):
                    # Link IDs are stable within a relation. A mission entry
                    # overrides the gamesystem entry with the same ID.
                    rows = {row.id: row for row in self.links.get(name[2:], [])}
                    rows.update({row.id: row for row in link_records(data, name)})
                    self.links[name[2:]] = list(rows.values())
            priorities = link_data_records(chunks.get("LD$MetaProp", b""), "LD$MetaProp")
            for link in link_records(chunks.get("L$MetaProp", b""), "L$MetaProp"):
                if link.id not in priorities or len(priorities[link.id]) != 4:
                    raise ValueError(f"MetaProp: missing 4-byte priority for link {link.id}")
                priority = _integer(priorities[link.id])
                old = self.parents.setdefault(link.source, [])
                old[:] = [(p, target) for p, target in old if target != link.target]
                old.append((priority, link.target))
        # Equal-priority tie order follows source insertion order. The normal
        # archetype edge is priority 0; higher-priority metaproperties win.
        for parents in self.parents.values():
            parents.sort(key=lambda pair: -pair[0])
        self._cache: dict[tuple[int, str, bool], tuple[bytes, int] | None] = {}
        names = self.properties.get("P$SymName", {})
        self.texture_objects = {_string(raw).casefold(): oid for oid, raw in names.items()
                                if _string(raw).casefold().startswith("t_fam/")}

    def lineage(self, oid: int, *, archetype_only: bool = False) -> list[int]:
        result, active, seen = [], set(), set()

        def visit(current):
            if current in active:
                raise ValueError(f"MetaProp: inheritance cycle through object {current}")
            if current in seen:
                return
            active.add(current)
            seen.add(current)
            result.append(current)
            for priority, parent in self.parents.get(current, []):
                if not archetype_only or priority == 0:
                    visit(parent)
            active.remove(current)

        visit(oid)
        return result

    def property(self, oid: int, name: str, *, inherit: bool = True,
                 archetype_only: bool = False) -> bytes | None:
        if not inherit:
            return self.properties.get(name, {}).get(oid)
        key = (oid, name, archetype_only)
        if key not in self._cache:
            self._cache[key] = next(((self.properties[name][parent], parent)
                                     for parent in self.lineage(oid, archetype_only=archetype_only)
                                     if parent in self.properties.get(name, {})), None)
        value = self._cache[key]
        return value[0] if value else None

    def name(self, oid: int) -> str:
        return _string(self.properties.get("P$SymName", {}).get(oid), str(oid))

    def archetype(self, oid: int) -> dict:
        parents = [parent for priority, parent in self.parents.get(oid, []) if priority == 0]
        parent = parents[0] if parents else None
        return dict(id=parent, name=self.name(parent) if parent is not None else "")

    def material(self, oid: int) -> dict:
        raw = self.property(oid, "P$Material ") or self.property(oid, "P$Material")
        tags = _label(raw[4:]) if raw else ""
        # Material tags are author-defined. The original string stays available
        # alongside this small vocabulary for mapping to DarkLantern audio.
        token = tags.casefold().split("material ", 1)[-1].split(",", 1)[0].strip()
        return dict(tags=tags, surface=token or None)

    def texture_surface_material(self, family: str, name: str) -> dict:
        key = f"t_fam/{family}/{name}".replace("\\", "/").casefold()
        if "." in key.rsplit("/", 1)[-1]:
            key = key.rsplit(".", 1)[0]
        oid = self.texture_objects.get(key)
        return dict(object_id=oid, **(self.material(oid) if oid is not None
                                     else dict(tags="", surface=None)))


def decode_light(data: bytes, *, animated: bool = False) -> dict:
    """Preserve source brightness/radius; radius zero means unbounded falloff."""
    if animated:
        if len(data) not in (68, 80):
            raise ValueError(f"AnimLight: unsupported {len(data)}-byte record")
        offset = list(struct.unpack_from("<fff", data, 4))
        table_index = struct.unpack_from("<H", data, 24)[0]
        mode = struct.unpack_from("<H", data, 26)[0]
        brighten, dim = struct.unpack_from("<ii", data, 28)
        minimum, maximum = struct.unpack_from("<ff", data, 36)
        inactive = bool(_integer(data[56:60]))
        radius = struct.unpack_from("<f", data, 60)[0]
        inner = struct.unpack_from("<f", data, 76)[0] if len(data) == 80 else 0.0
        brightness = 0.0 if mode == 5 else minimum if mode == 3 else maximum
        result = dict(animated=True, offset=offset, mode=mode, min_brightness=minimum,
                      max_brightness=maximum, brighten_ms=brighten, dim_ms=dim,
                      animation_inactive=inactive, brightness=brightness,
                      radius=radius, inner_radius=inner, light_table_index=table_index)
    else:
        if len(data) not in (20, 28):
            raise ValueError(f"Light: unsupported {len(data)}-byte record")
        brightness, x, y, z, radius = struct.unpack_from("<fffff", data)
        inner = struct.unpack_from("<f", data, 24)[0] if len(data) == 28 else 0.0
        result = dict(animated=False, offset=[x, y, z], brightness=brightness,
                      radius=radius, inner_radius=inner)
    if not all(math.isfinite(result[key]) for key in ("brightness", "radius", "inner_radius")):
        raise ValueError("Light: non-finite brightness or radius")
    if not all(math.isfinite(value) for value in result["offset"]):
        raise ValueError("Light: non-finite offset")
    return result


def _patrol_route(start: int, adjacency: dict[int, list[int]]) -> tuple[list[int], bool]:
    """Walk deterministic source links; report branches the simple route loses."""
    route, seen, branching = [], set(), False
    current = start
    while current not in seen and len(route) < 32:
        seen.add(current)
        route.append(current)
        targets = adjacency.get(current, [])
        branching |= len(targets) > 1
        if not targets:
            break
        current = next((target for target in targets if target not in seen), targets[0])
    return route, branching


def merge_compiled_lights(property_lights: list[dict], compiled_lights: list[dict],
                          *, max_static_match_distance: float = 6.0) -> dict:
    """Keep every baked emitter and attach object provenance conservatively.

    Animated properties store an exact WR light-table index. Static properties
    do not; their model's light VHot can move the baked emitter by almost six
    source units. Match static records one-to-one by brightness and distance,
    and mark those matches as approximations. Unmatched records remain valid
    table lights, including terrain brushes stripped from shipped missions.
    """
    if not math.isfinite(max_static_match_distance) or max_static_match_distance < 0:
        raise ValueError("max_static_match_distance must be finite and nonnegative")
    table = {row["id"]: row for row in compiled_lights}
    if len(table) != len(compiled_lights):
        raise ValueError("Duplicate compiled light table index")
    assigned, kinds, used = {}, {}, set()
    for row in property_lights:
        index = row.get("light_table_index") if row["animated"] else None
        if index not in table:
            continue
        if index in assigned:
            raise ValueError(f"Multiple animated properties reference light table index {index}")
        assigned[index], kinds[index] = row, "animated_table_index"
        used.add(row["source_id"])

    def distance(a, b):
        return sum((a["position"][axis] - b["position"][axis]) ** 2 for axis in range(3))

    candidates = []
    for lamp in compiled_lights:
        if lamp["id"] in assigned:
            continue
        for row in property_lights:
            if row["animated"] or abs(lamp["intensity"] * 32 - row["brightness"]) > 0.01:
                continue
            separation = distance(lamp, row)
            if separation <= max_static_match_distance ** 2:
                candidates.append((separation, lamp["id"], row["source_id"], row))
    candidates.sort(key=lambda pair: pair[:3])
    for _, index, oid, row in candidates:
        if index not in assigned and oid not in used:
            assigned[index], kinds[index] = row, "nearest_static_with_equal_brightness"
            used.add(oid)

    lights = []
    for lamp in compiled_lights:
        match = assigned.get(lamp["id"])
        intensity = lamp["intensity"]
        color = match["color"] if match else [v / intensity if intensity > 0 else 1 for v in lamp["color"]]
        lights.append(dict(lamp, source_id=f"wr-{lamp['id']:03d}", brightness=intensity * 32,
                           color=color, animated=bool(match and match["animated"]),
                           matched_object=match["source_id"] if match else None,
                           matching=kinds.get(lamp["id"], "compiled_table_only"),
                           match_is_approximate=kinds.get(lamp["id"]) == "nearest_static_with_equal_brightness",
                           source_property=match, compiled_light_id=lamp["id"]))
    exact = sum(kind == "animated_table_index" for kind in kinds.values())
    approximate = len(assigned) - exact
    return dict(lights=lights, stats=dict(compiled_lights=len(lights),
                animated_exact_matches=exact, static_approximate_matches=approximate,
                table_only_lights=len(lights) - len(assigned),
                unmatched_property_object_ids=[row["source_id"] for row in property_lights
                                               if row["source_id"] not in used]))


def extract_objects(gamesystem, mission, *, difficulty: int = 2) -> dict:
    """Extract source placements with explicit provenance and omitted mechanics.

    Difficulty is 0=Normal, 1=Hard, 2=Expert. Expert is the default so the scale
    study preserves the largest original guard population. Nearest-route patrol
    assignment is an approximation when the source has no AICurrentPatrol link;
    callers can use only explicit routes if exact behavior is required.
    """
    if difficulty not in (0, 1, 2):
        raise ValueError("difficulty must be 0 (Normal), 1 (Hard), or 2 (Expert)")
    database = ObjectDatabase(gamesystem, mission)
    mission_positions = property_records(database.mission_chunks.get("P$Position", b""), "P$Position")
    positions = {oid: decode_position(raw) for oid, raw in mission_positions.items() if oid > 0}
    containers = {link.target: link.source for link in database.links.get("Contains", [])}
    factories = [link for link in database.links.get("PlayerFac", []) if link.source in positions]
    if len(factories) != 1:
        raise ValueError(f"Expected one positioned PlayerFactory source, found {len(factories)}")
    spawn_id = factories[0].source

    def available(oid):
        raw = database.property(oid, "P$DiffDestr", inherit=False)
        return not (_integer(raw) & (1 << difficulty))

    def in_inventory(oid):
        seen = set()
        while oid in containers:
            if oid in seen:
                raise ValueError("Contains: container cycle")
            seen.add(oid)
            oid = containers[oid]
            if oid == spawn_id:
                return True
        return False

    def record(oid):
        return dict(source_id=oid, name=database.name(oid), archetype=database.archetype(oid),
                    model=_label(database.property(oid, "P$ModelName")),
                    **positions[oid], material=database.material(oid),
                    container_id=containers.get(oid),
                    has_refs=bool(_integer(database.property(oid, "P$HasRefs"), 1)),
                    difficulty_destroy_mask=_integer(database.property(oid, "P$DiffDestr", inherit=False)))

    adjacency = {}
    patrol_links = database.links.get("AIPatrol", [])
    for link in patrol_links:
        if link.source in positions and link.target in positions:
            adjacency.setdefault(link.source, []).append(link.target)
    for targets in adjacency.values():
        targets.sort()
    patrol_ids = sorted(set(adjacency) | {target for targets in adjacency.values() for target in targets})
    current_patrol = {link.source: link.target for link in database.links.get("AICurrent", [])}
    direct_lights = {name: property_records(database.mission_chunks.get(name, b""), name)
                     for name in ("P$Light", "P$AnimLight")}
    objects, lights, guards, loot, objectives, unsupported, inventory_loot = [], [], [], [], [], [], []
    for oid in sorted(positions):
        row = record(oid)
        objects.append(row)
        if not available(oid):
            continue
        if any(database.name(parent).casefold() == "baffordscepter" for parent in database.lineage(oid)):
            # This scripted mission goal is deliberately not P$Loot. Keep it
            # separate from the 1429 points of ordinary Bafford treasure.
            objectives.append(dict(row, objective_role="bafford_scepter", value=0,
                                   source_behavior="scripted_mission_goal"))
        ai = database.property(oid, "P$AI")
        if ai:
            row = dict(row, ai_type=_label(ai),
                       ai_mode=_integer(database.property(oid, "P$AI_Mode"), 3),
                       ai_team=_integer(database.property(oid, "P$AI_Team"), 2),
                       patrol_enabled=bool(_integer(database.property(oid, "P$AI_Patrol"))))
            ancestor_names = {database.name(parent).casefold() for parent in database.lineage(oid)}
            if "guard" in ancestor_names:
                start = current_patrol.get(oid)
                source = "AICurrentPatrol" if start in positions else "none"
                if start not in positions and row["patrol_enabled"] and patrol_ids:
                    start = min(patrol_ids, key=lambda node: sum((positions[node]["position"][axis] -
                                                                 row["position"][axis]) ** 2 for axis in range(3)))
                    source = "nearest_source_patrol_node"
                route, branching = _patrol_route(start, adjacency) if start in positions else ([], False)
                row.update(patrol_node_ids=route, patrol_assignment=source,
                           patrol_branching=branching, behavior="patrol" if len(route) >= 2 else "sentry")
                guards.append(row)
            else:
                unsupported.append(dict(row, reason="AI archetype has no DarkLantern actor mapping"))
        loot_raw = database.property(oid, "P$Loot")
        if loot_raw:
            if len(loot_raw) != 16:
                raise ValueError(f"Loot object {oid}: expected 16-byte record")
            gold, gems, art, special = struct.unpack("<iiiI", loot_raw)
            item = dict(record(oid), gold=gold, gems=gems, art=art, special=special,
                        value=gold + gems + art, placement="source_position")
            if in_inventory(oid):
                inventory_loot.append(item)
            else:
                # A contained pickup may have stale / hidden child placement.
                # Keep that exact position too, and materialize at its container
                # for a proof of concept without chest gameplay.
                parent = containers.get(oid)
                if parent in positions:
                    item.update(source_position=item["position"], position=positions[parent]["position"],
                                placement="container_position")
                loot.append(item)
        # Lights should be concrete placements, not archetype defaults copied
        # onto every descendant. The MIS includes each baked object light.
        for prop, animated in (("P$Light", False), ("P$AnimLight", True)):
            raw = database.property(oid, prop, inherit=False)
            if raw is None or oid not in direct_lights[prop]:
                continue
            lamp = decode_light(raw, animated=animated)
            color_raw = database.property(oid, "P$LightColo")
            hue, saturation = struct.unpack("<ff", color_raw) if color_raw else (0.0, 0.0)
            item = dict(record(oid), **lamp, color=list(colorsys.hsv_to_rgb(hue % 1, max(0, min(1, saturation)), 1)))
            item["object_position"] = item["position"]
            item["position"] = [item["position"][axis] + lamp["offset"][axis] for axis in range(3)]
            lights.append(item)
    return dict(spawn=record(spawn_id), lights=lights, guards=guards, loot=loot, objectives=objectives,
                patrol_nodes=[record(oid) for oid in patrol_ids], objects=objects,
                unsupported_actors=unsupported, omitted_inventory_loot=inventory_loot,
                patrol_links=[dict(source_id=link.source, target_id=link.target) for link in patrol_links],
                stats=dict(difficulty=("Normal", "Hard", "Expert")[difficulty],
                           positioned_objects=len(objects), guards=len(guards), loot=len(loot), objectives=len(objectives),
                           loot_value=sum(item["value"] for item in loot),
                           lights=len(lights), enabled_lights=sum(item["brightness"] > 0 for item in lights),
                           patrol_nodes=len(patrol_ids), unsupported_actors=len(unsupported),
                           inventory_loot=len(inventory_loot)))
