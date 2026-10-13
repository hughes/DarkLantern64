# Mission directory v1 and region chunks v1/v2/v3/v4

Status: first implementation contract, 2026-10-12. This is an experimental
portable streaming foundation, not acceptance of the complete Bafford mission.
All integers and IEEE-754 binary32 floats are big endian. No native pointers,
compiler structures, strings or RSP command blocks are serialized. IDs are
one-based dense numeric IDs assigned by sorted source IDs; source IDs and the
mapping remain in the cook report. Numeric IDs are stable within a package,
not a save-game compatibility promise across changed cooks.

## DLM1 directory

The directory alone is resident. The reader receives directory bytes and the
actual package file length; chunk data is read separately by file offset.

| Header offset | Type | Meaning |
| --- | --- | --- |
| 0 | char[4] | `DLM1` |
| 4 | u16 | Version, 1 |
| 6 | u16 | Header bytes, 64 |
| 8 | u32 | Exact package file bytes |
| 12 | u32 | Region count, nonzero |
| 16 | u32 | Entity count |
| 20 | u32 | Exact directory bytes, excluding alignment padding |
| 24 | u32 | Entry region ID |
| 28 | u32 | IEEE CRC32 of the directory, treating bytes 28..31 as zero |
| 32 | byte[32] | SHA256 content identity supplied by the cook |

The header is followed by region records (64 bytes each), entity records (16
bytes each), then dependency region IDs (u32). All regions occur in ID order;
chunks occur in that same order, start at 16-byte aligned offsets and cannot
overlap the directory or each other. Zero alignment padding is outside CRC
coverage. No compression in v1: stored and decoded chunk sizes are equal.

| Region offset | Type | Meaning |
| --- | --- | --- |
| 0 | u32 | Region ID, row index + 1 |
| 4 | u32 | Chunk file offset |
| 8 | u32 | Stored chunk bytes |
| 12 | u32 | Decoded chunk bytes |
| 16 | u32 | IEEE CRC32 of the complete chunk |
| 20 | u32 | First dependency index |
| 24 | u32 | Dependency count |
| 28 | u32 | Flags, zero |
| 32 | f32[3] | World bounds minimum, metres |
| 44 | f32[3] | World bounds maximum, metres |
| 56 | byte[8] | Reserved, zero |

Bounds contain finite values and minimum must be strictly below maximum on
every axis. They describe conservative ownership coverage; they are not a
validated visibility partition or a complete movement boundary.

Dependency slices are contiguous in region order and cover the dependency
table exactly. Each dependency is a valid, distinct, non-self region ID. These
are authored prefetch neighbors in v1, not a proven PVS or complete sensory
dependency set.

| Entity offset | Type | Meaning |
| --- | --- | --- |
| 0 | u32 | Entity ID, row index + 1 |
| 4 | u32 | Home region ID |
| 8 | u16 | Kind: generic 0, guard 1, door 2, loot 3, objective 4 |
| 10 | u16 | Flags, zero |
| 12 | u32 | Reserved, zero |

## DLR1 region chunk

| Header offset | Type | Meaning |
| --- | --- | --- |
| 0 | char[4] | `DLR1` |
| 4 | u16 | Chunk version, 1, 2, 3 or 4 |
| 6 | u16 | Header bytes, 32 |
| 8 | u32 | Exact chunk bytes |
| 12 | u32 | Section count, 1..3 (v1/v2), 1..4 (v3/v4) |
| 16 | f32[3] | World origin, metres |
| 28 | u32 | Reserved, zero |

Section descriptors (24 bytes each) follow the header, in ascending kind order.
Section payloads begin at 16-byte aligned chunk offsets, cannot overlap the
descriptors or each other, and their length is count times record bytes.

| Section offset | Type | Meaning |
| --- | --- | --- |
| 0 | u16 | Kind: static triangles 1, upright colliders 2, entity IDs 3, floor triangles 4 (v3/v4) |
| 2 | u16 | Reserved, zero |
| 4 | u32 | Chunk-relative payload offset |
| 8 | u32 | Payload bytes |
| 12 | u32 | Record count |
| 16 | u32 | Record bytes: triangles 44 (v1), 80 (v2/v3) or 128 (v4), colliders 32, entity IDs 4, floors 36 |
| 20 | u32 | Reserved, zero |

Version-1 triangle records contain three region-local XYZ positions (9 floats), a
one-based material ID (u32) and one-based source surface ID (u32), totalling
44 bytes. Version 2 retains these first 44 bytes and appends corner attributes:

| Triangle offset | Type | Meaning |
| --- | --- | --- |
| 44 | f32[6] | U,V for each of the three corners |
| 68 | i8[9] | XYZ snorm8 normal for each corner; each component -127..127 |
| 77 | u8 | Attribute flags: UV present 1, normal present 2 |
| 78 | byte[2] | Reserved, zero |

UVs use the existing runtime basis, including the OBJ reader's V conversion;
the chunk decoder does not flip V or clamp coordinates to one texture repeat.
Normals use the cooked geometry's orientation after inverse-scale/rotation,
before quantization. Flagged normals must contain a nonzero vector at each
corner. All UV floats must be finite, unknown flags and nonzero reserved bytes
are rejected, and -128 is invalid in every normal component. Attribute values
are validated lazily before use. Version-1 getters return zero attributes and
flags, preserving the original untextured flat-normal probe contract.

The bounded working-view bridge groups by region, material and attribute flags;
its meshes retain optional UV and normal arrays. Material bootstraps use
`texture_id=0` for untextured material and otherwise a one-based index into the
bootstrap's ordinary `DlTexture` table. Texture pixels remain hash-addressed
sprites packaged by the existing texture cooker. The directory layout remains
v1 for all chunk versions. Tiny3D packing, batch commands and texture pixels
are not serialized into these triangle records. Baked colors are present in
version 4 as described below. The debug
report maps material and surface IDs to canonical source IDs.

Collider records contain region-local center (3 floats), positive half size
(3 floats), yaw in radians (float), and associated entity ID (u32), totalling
32 bytes. Entity ID zero means no association; other IDs must resolve through
the mission directory. Door association is resolved through the entity kind. This
representation retains upright boxes for walls and obstacles.

Version 3 adds floor records: three region-local XYZ vertices (nine finite
binary32 floats, 36 bytes). The projected XZ triangle must have nonzero area
and its squared height gradient must be at most 1.000001 (45 degrees plus
float rounding tolerance). Gameplay evaluates height only inside the exact
triangle, including slopes, jump landings and overhead crossings. A floor
does not fill its rectangular bounds or create an opaque volume beneath it.
The cook transforms local authored `floor.vertices` into world space before
subtracting the chunk origin. It checks the binary32 local records and the
binary32 world vertices reconstructed by adding the encoded origin. The scene
adapter checks the reconstructed footprint and grade before publishing a bank,
because translation rounding can invalidate a usable local plane. Version 3
otherwise retains v2 triangle attributes.

Target preflight rounds every footprint/gradient arithmetic operation to
binary32, matching the C gameplay and adapter query. Checking only rounded
vertices with host double intermediates can miss cancellation in thin floors.

Version 4 retains all v3 geometry and attributes and appends two sets of baked
night colors. Each set stores front-face corner 0/1/2 RGBA, followed by
back-face corner 0/1/2 RGBA. Alpha must be 255 for every corner. Both faces are
present even for a single-sided material, keeping the stride fixed.

| Triangle offset | Type | Meaning |
| --- | --- | --- |
| 80 | u8[24] | Closed-door front/back corner RGBA |
| 104 | u8[24] | Open-door front/back corner RGBA |

An authored `environment` selects v4 automatically in both the command-line
mission cooker and editor/bundle builds. Scalar missions retain v1, v2 or v3
according to their attributes and floor records. The native host worker uses
the shared `dl_static_lighting_model` C implementation, serialized binary32
region-local vertices/origins and snorm8 normals. It shades the complete mission
against every authored collider, every exact floor and all global lights;
temporary unavailable-region barriers are excluded. The host collider limit is
set to the complete scene's count without changing the target's 400-contact
working-set bound.

The same cook emits per-instance closed/open RGBA arrays for eligible persistent
bootstrap visuals such as doors and loot. Shared mesh geometry does not imply
shared colors. Moving guards, objectives and animated meshes retain live
lighting. Runtime scene banks copy the chunk colors into fixed storage and
attach them to model instances, so publishing a resident region does not shade
its static triangles on the console.

Native cook caches include the generated host header, compiler and math-library
identities, shared C lighting sources and compiler/helper implementations.
The lighting signature enters the DLM1 content identity. A failed worker or
input change publishes no updated mission/header artifacts. The host full
shadow scene can exceed target model, triangle and floor working-set limits.
Its wire format supports 65535 models and 32-bit total triangle/payload counts;
each grouped mesh retains the 4096-vertex limit. Host scratch holds only the
largest eligible mesh's colors. Host collider/floor limits cover all authored
blockers while the target's resident working-set limits remain unchanged.

For the expanded district, 104 static groups plus two persistent cube instances
cook 2537 static and 24 persistent triangles against 603 colliders, 118 floors
and 16 lights. Its v4 package is 367272 bytes; the largest chunk is 54736 bytes,
below the 65536-byte slot limit. Bootstrap colors occupy 1152 bytes. These are
storage/cook measurements; transition timing is verified separately on the
target renderer.

The lighting report's compatible `bytes` field and `host_worker_bytes` describe
the DLS1 host cache, including its header/table, rather than a packaged ROM
asset. `storage=embedded` and `standalone_rom_asset_bytes=0` distinguish this
from ordinary standalone lighting assets. `mission_embedded_rgba_bytes` is
already included in mission package bytes. `bootstrap_embedded_rgba_bytes`
counts constant arrays resident with the bootstrap; it is reported separately
as `compiled_baked_color_bytes` and bundle `resident_baked_color_bytes`.
Bundle `resident_known_array_bytes` adds geometry and constant baked colors
only. For this district, 624 geometry bytes plus 1152 color bytes give 1776
known resident array bytes. Descriptors, scene banks, slots, renderer caches,
audio and display buffers require separate measurement.

Prepared reports also record `mission_manifest_sha256` from the exact saved
manifest bytes. The editor matches it and the source file SHA before showing
saved ROM measurements; even whitespace-only partition edits invalidate that
display proof until the matching report is rebuilt.

Entity-ID sections contain the region's home entities exactly once. Region
installation binds these IDs to mission-owned state and never initializes the
mission. Consumers validate record values and ID references before use.

## Loading and ownership

The portable loader reserves one caller-provided slot buffer before reading.
V1 has no decoder allocation. Its work budget limits bytes read and CRC'd per
pump; structural chunk validation has at most four section descriptors.
Zero-byte reads mean temporarily unavailable; an explicit read error aborts
the load. Data is inaccessible until the full chunk and its CRC/layout pass.

A generation-tagged handle pins a resident slot for CPU users. Retirement
prevents new borrows and waits for CPU pins, the last RSP fence and the last
RDP fence. The two fence domains are independent and use monotonically
increasing u64 submission serials. Callers must report actual completion,
including an RDP full-sync boundary for textures; an RSP syncpoint alone is
insufficient. The manager owns no libdragon API and no mission gameplay state.

Slot capacity, directory allocation, read scratch, future render resources and
audio must all be charged to the whole-game ledger. A cook report is not an
8 MiB hardware acceptance result.
