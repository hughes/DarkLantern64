# Mission directory v1 and region chunks v1/v2

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
| 4 | u16 | Chunk version, 1 or 2 |
| 6 | u16 | Header bytes, 32 |
| 8 | u32 | Exact chunk bytes |
| 12 | u32 | Section count, 1..3 |
| 16 | f32[3] | World origin, metres |
| 28 | u32 | Reserved, zero |

Section descriptors (24 bytes each) follow the header, in ascending kind order.
Section payloads begin at 16-byte aligned chunk offsets, cannot overlap the
descriptors or each other, and their length is count times record bytes.

| Section offset | Type | Meaning |
| --- | --- | --- |
| 0 | u16 | Kind: static triangles 1, upright colliders 2, entity IDs 3 |
| 2 | u16 | Reserved, zero |
| 4 | u32 | Chunk-relative payload offset |
| 8 | u32 | Payload bytes |
| 12 | u32 | Record count |
| 16 | u32 | Record bytes: triangles 44 (v1) or 80 (v2), colliders 32, entity IDs 4 |
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
v1 for both chunk versions. Tiny3D packing, baked colors, batch commands and
texture pixels are not serialized into these triangle records. The debug
report maps material and surface IDs to canonical source IDs.

Collider records contain region-local center (3 floats), positive half size
(3 floats), yaw in radians (float), and associated entity ID (u32), totalling
32 bytes. Entity ID zero means no association; other IDs must resolve through
the mission directory. Door association is resolved through the entity kind. This
initial representation retains the existing upright boxes; triangle sweeps and
BVHs are a later compiler/runtime increment.

Entity-ID sections contain the region's home entities exactly once. Region
installation binds these IDs to mission-owned state and never initializes the
mission. Consumers validate record values and ID references before use.

## Loading and ownership

The portable loader reserves one caller-provided slot buffer before reading.
V1 has no decoder allocation. Its work budget limits bytes read and CRC'd per
pump; structural chunk validation has at most three section descriptors.
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
