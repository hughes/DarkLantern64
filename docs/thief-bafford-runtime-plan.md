# Running the complete Bafford mission

Prepared **2026-10-12** for DarkLantern's **8 MiB N64/M64 target**.

The [implementation architecture](thief-bafford-runtime-architecture.md) defines
the package, ownership and subsystem boundaries, with the initial branch scope
and subsequent delivery gates. The experimental binary contract is documented
in [mission package v1](mission-package-format.md).

The [streaming editor contract](streaming-editor.md) makes region authoring,
validation, transactional saves and matching Native Play behavior delivery
requirements. The [playable two-room probe](streaming-probe.md) is selectable
from the ROM menu; its editor integration is experimental until those gates
are complete.

The conversion proves that the complete architecture, textures, placements and
source topology can be brought into an editable `.blend` and our editor. It does
not yet prove that the complete mission can run within N64 memory, maintain a
frame budget, or be traversed correctly. The playable entry area is a useful
first test of the asset pipeline; it contains no imported guards or loot and
uses incomplete collision at its boundary.

The main engineering task is to make the cost depend on the **current working
set**, rather than the total mission. Streaming, visibility, collision,
lighting and persistent gameplay state must agree on that working set. Raising
the current array limits alone cannot do this.

## Evidence and present limits

These counts come from the checked-in
[conversion evidence](evidence/thief-bafford-import.json). Architecture counts
exclude substituted guard and loot models; instanced counts include them.

| Resource | Complete imported study | Playable entry area | Current normal runtime contract |
| --- | ---: | ---: | ---: |
| Retained source cells | 4,984 | 37 | No runtime cell representation |
| Architecture triangles | 51,268 | 475 | Included in scene triangle limit |
| Instanced vertices | 123,074 | 957 | 6,144 |
| Instanced triangles | 64,457 | 475 | 4,096 |
| Architecture model instances | 1,400 | 47 | Included in model limit |
| All model instances | 1,443 | 47 | 128 |
| Box collision proxies | 16,605 | 155 | 256 |
| Compiled light emitters | 365 | 16 | 16 authored lights |
| Guards | 19 | 0 | 16 |
| Terrain materials | 137 | Subset of the mission | 64 authored materials / texture slots |
| Ordinary loot / scepter | 23 / 1 | 0 / 0 | 64 interactive objects, including doors and controls |
| Compiled geometry arrays | 2,735,204 bytes (2.61 MiB) | Smaller bounded cook | Separate from total runtime memory |

The source also retains **16,381 BSP nodes** and **26,056 directed portal
polygons**. Those are valuable inputs to a spatial compiler, not runtime systems
already in use. The original mission has 4,991 cells; the retained-surface count
is smaller because the adaptation removes water, sky boundaries and tiny slivers.

The full JSON deliberately uses `external-scale-study`. It is accepted for host
authoring, while Native Play, ROM cooking and bundle membership reject it.
Keep that protection until a separate streamed runtime format is implemented.
The current contracts are defined by
[content limits](../src/content_limits.h),
[authoring profiles](../tools/content_profile.py) and
[game structures](../src/game.h). They are software limits, not universal
limits of N64 hardware.

## 1. Memory residency and ROM streaming

The current [bundle compiler](../tools/compile_bundle.py) emits C geometry and
descriptors for every bundled level. **All of those arrays are resident** even
when another level is selected. Only the active level's sprites are loaded;
its lighting bake is decoded into the renderer's existing caches. Adding the
entry area to level select therefore does not introduce mission streaming.

The 2.61 MiB geometry figure counts emitted vertex, index, UV and normal arrays.
It excludes descriptors, collision, strings, animation, textures, rendering
copies, program code, audio and display buffers. A calculation using the
complete mission's counts and today's renderer shows why a direct array-limit
increase is already too large:

| Allocation under a hypothetical full resident scene | Calculation | Bytes |
| --- | --- | ---: |
| Source geometry arrays | Existing host cook estimate | 2,735,204 |
| World vertex cache | 123,074 × 12 | 1,476,888 |
| Face cache | 64,457 × 32 | 2,062,624 |
| Two night-lighting states | 64,457 × 24 × 2 | 3,093,936 |
| **Subtotal** | | **9,368,652 (8.94 MiB)** |

This is a **derived illustration, not a measured full-mission run**. It already
exceeds all 8 MiB before Tiny3D's per-instance packed vertices and matrices,
batch metadata, textures, collision, code, audio or display allocations.
The CPU renderer additionally keeps camera vertices and clip codes. Tiny3D
shares mesh batches, but currently creates three buffered vertex/matrix sets
per instance; two-sided models need another vertex stream.
[Renderer allocation policy](../src/render.c),
[Tiny3D allocation policy](../src/render_t3d.inc).

All 123,074 instanced vertices are referenced by this import. A one-sided
Tiny3D stream alone therefore adds **at least 5,907,552 bytes**
(`123,074 × 16 packed bytes × 3 frames`), before batch duplication/padding and
matrices. Combined with the subtotal above, that is at least **14.57 MiB**.
The three display buffers and depth buffer add another 614,400 pixel bytes.
These are allocation-formula lower bounds, not a prediction for a redesigned
streaming renderer.

There is a boot constraint as well: enlarged static world and face arrays alone
would occupy 3,539,512 bytes. The [build tool](../tools/build.py) currently guards
the linked startup image (`text + data + bss`) at 3 MiB so it can reach the
4 MiB error screen. Removing that guard would not solve the working-memory
problem.

The required replacement is a versioned ROM chunk format with a small resident
directory. Cook compact region geometry, collision, light/probe data and local
resource references into independently loadable chunks. Share immutable guard
assets rather than copy them into every region. Keep bounded pools for renderer
caches and decoded assets; do not build caches for every mission instance at
startup. Preload neighboring areas before entry and retire old data only after
the RSP/RDP stop referencing it. Today's whole-scene release waits for that
ownership boundary; chunk eviction needs equally explicit fences.

Budget the worst overlap, not just a room after loading:

```text
persistent state + shared assets + current region + incoming regions
  + geometry/render caches + textures + collision/probes
  + DMA/decoder scratch + audio/display/code + safety reserve <= 8 MiB
```

Long sightlines may require several regions simultaneously. Collision and
simulation data may need a larger neighborhood than visible geometry. The
loader needs per-frame work limits, measured prefetch distance, a defined
behavior when data is late, and telemetry for peak residency and load stalls.
ROM transfers, decompression and streamed audio must be tested together on the
target device. The [hardware guide](n64-hardware-guide.md) describes the current
DMA and lifetime constraints.

### ROM storage is a different budget

Use **64 MiB as an initial compatibility target** for the packaged main ROM,
then measure the actual cooked code, all bundled levels, textures, lighting,
animation and audio. It is not a universal hardware ceiling: SummerCart64,
for example, advertises ROMs up to 78 MiB in its
[official feature list](https://github.com/Polprzewodnikowy/SummerCart64#features).
The supported cartridge/M64 loader and emulator configurations still need an
explicit tested storage limit. The 2.61 MiB geometry-array estimate is neither
the full mission ROM size nor the amount of free RDRAM. A larger cartridge image
can hold streamed data, but does not increase the 8 MiB working-memory budget.

## 2. Visibility and spatial compilation from Blender

Today's renderer performs conservative model-sphere frustum rejection. That
helps with geometry behind the camera; it does not prevent submission of rooms
hidden behind opaque walls. The caches are also prepared for all instances
before this rejection. A full mansion needs both **bounded residency** and
**occlusion-aware selection** of draw candidates.

Use the preserved cell/BSP/portal data to locate the camera and traverse visible
openings, or to cook a conservative potentially-visible set. Group cells into
practical load regions; a region is not necessarily one of Thief's thousands of
small compiler cells. Keep geometry split tightly enough for useful bounds and
material batching. A visible doorway may expose several rooms; aligned doors,
balconies, streets and exterior-to-interior transitions must be explicit worst
cases. Fog and the current 64 m far plane do not by themselves eliminate work
that was already submitted.

Preserve the `.blend` as the authoring intermediate. Its `thief_cell_id` and
`thief_polygon_id` face attributes, `dl64_source` properties and embedded
topology are provenance. They are not automatically valid spatial metadata
after an artist moves a wall, opens an arch or changes a floor. Extend the
export/import/cook pipeline to rebuild affected bounds, region membership,
portals, collision, navigation and lighting; invalidate derived data by input
hash. If an edit cannot be classified safely, report it and rebuild the region
or use a conservative fallback. Stale portals must never make edited geometry
disappear or allow movement into an unloaded region.
[Blender provenance](../tools/blender/thief_scene.py),
[import workflow](thief-bafford-study.md).

## 3. Collision, navigation and numerical fidelity

The current proxies cover exact axis-aligned rectangular faces only. The
conversion leaves **5,553 sloped faces** and **3,922 nonrectangular or too-thin
faces** without collision. A visually complete floor is therefore not
necessarily a walkable floor. Filling every region with broad bounding boxes
would also close arches, stairs and passages incorrectly.

Add a spatial broadphase and a representation that handles the actual surfaces:
for example, convex cell planes for world solids, or triangle collision with
swept player/guard volumes. Select nearby candidates for movement and support,
and intersect only regions crossed by a sight/light ray. Today's game clamps
box queries to 256 and linearly scans those boxes; merely raising that cap
would multiply work in movement, support, sight, hearing and lighting.
[Current physics and queries](../src/game.c).

Validate slopes, stairs, narrow openings, ceilings, landings and region seams
with the game's standing/crouching body dimensions and 0.28 m step height.
Collision data must remain available before a player or guard can enter it.
Build a connected navigation graph from walkable geometry, with vertical and
door transitions, rather than treating retained portal links or original
waypoints as a ready-made guard route. The source has 54 patrol nodes, but the
adapted guards are currently sentries and the cooked full study has no patrol
points.

Retain the established 0.3048 m source-unit conversion, axis mapping, winding
and UV basis. Tiny3D uses quantized local vertices, fixed-point modelview
matrices and signed packed UVs. Large meshes or long UV spans may need splitting
and region-local origins; test thin gaps and adjacent surfaces after target
packing, not only in Blender. The full traversal also needs a region-aware
fall/out-of-world rule: today's `y < -20` reset is a prototype rule, not a
mission boundary definition.
[Format and packing constraints](guard-renderer-performance.md).

## 4. Lighting that remains consistent with stealth

The imported 365 emitters are locations and approximations, not a reconstruction
of Thief's lightmaps and sensory model. Brightness is scaled into our intensity,
unbounded lights receive an 8 m radius, and animated lights are static snapshots.
Spot cones and source animation remain metadata.

Bake static region lighting offline and stream its data alongside geometry.
Cook local light lists or probes for player visibility and moving actors; do
not scan every source and trace every ray through every mission collider.
Current surface lighting and player visibility use the same occluded sources,
with display exposure/fog kept out of the stealth signal. Preserve that
relationship when replacing the query path. Test lit/shadowed routes and
region boundaries so a guard's perception agrees with the scene.

Door state needs local invalidation. The renderer's two cached global door
states suit the small prototype; with multiple independent doors it currently
recomputes static lighting when object state changes. A mansion should update
only affected receivers, portal connectivity and local probe data. Avoid
prebaking every possible combination of doors. Decide which animated lights
and extinguishable sources are needed for the adapted mission, and give their
updates explicit budgets.
[Static bake format](../src/static_lighting.h),
[bake decoding](../src/lighting_bake.c),
[render lighting policy](../src/render.c).

## 5. Persistent guards, objects and mission state

The 19 guards do not imply that 19 full visual and sensory workloads must be
active at once. They do require stable identity and retained position,
awareness, pursuit/search state and route progress. Keep lightweight global
state separately from region render assets. Give nearby or alerted actors
timely updates; distant actors may use coarse schedules, provided that sounds,
pursuits and region crossings can wake them correctly. An unseen guard can
still affect gameplay.

Current state uses 16 local enemy slots and 64-bit object masks; model links,
events and door target masks refer to local indices. A streaming format needs
global IDs plus explicit mappings to active slots. Unloading a region must not
reset loot, duplicate the scepter, close an already-open door, erase awareness
or discard a delayed event. A load is not a mission restart.

Import or author patrol assignments, navigation and door use. Define what the
adapted mission retains from difficulty goals, containers, keys, starting
inventory and scripts. Ordinary container loot currently sits at the container
position and the scepter is our simple objective. Servants, spiders and other
source behavior are omitted. Completing an adapted full mission and reproducing
all original gameplay are separate acceptance targets.
[Game definitions and state](../src/game.h),
[conversion adaptations](thief-bafford-study.md).

## 6. Route-critical mechanics and surface audio

Water, swimming, ladders, floor trapdoors and source doors were acceptable
omissions for the small entry proof. They cannot silently remain missing from
a claim that the whole mission is traversable. Audit routes through the sewers
and vertical connections. Either implement the required mechanics or author
explicit playable substitutes in Blender/DarkLantern, retaining the intended
connections. Water also needs medium boundaries, buoyancy/movement rules and
appropriate rendering if preserved. Trapdoors need horizontal hinges and
collision transitions beyond the current upright door model.

The terrain retains stone, wood, tile, glass, carpet, metal, earth and vegetation
tags. They currently affect neither footstep playback nor hearing strength:
player steps use crouch/standing strength and the existing single sound.
Carry a surface identifier through collision/support queries, select a small
sample set, and define material-dependent sound propagation and stealth cost.
Closed-door acoustics should not be identical to visual occlusion. These are
gameplay inputs and need deterministic behavior across region boundaries.
[Step events](../src/game.c), [sound playback](../src/main.c).

## 7. Texture and draw cost

The `.blend` preserves original pixels and UVs. The current importer specifies
**32 × 32 RGBA16** terrain textures for N64 cooking; original authoring
resolution is not a runtime guarantee. Each such texture needs 2,048 packed
pixel bytes before sprite/container overhead. The full material set exceeds
the renderer's 64 texture slots, which currently load all textures of the active
level at once. Authoring validation resolves **138 cooked texture entries**
after including the adapted DarkLantern assets; 136 of the 137 authored terrain
materials have texture sources, and the remaining terrain material is untextured.

Use bounded per-region texture residency with shared content hashes and stable
material IDs. Continue respecting the **4 KiB TMEM** upload budget, row
alignment, palette reservations and packed-UV limits. Consider CI4/CI8 where
the visual result is acceptable, rather than assuming all original images can
be uploaded unchanged. Batch visible opaque surfaces by material where useful
and record texture uploads as well as triangles. Large streets and many small
material changes can be expensive even when raw polygon counts are modest.
Add transparent water only with an explicit sorting/fill-rate policy.
[Texture cook](../tools/cook_textures.py),
[current texture residency](../src/render.c),
[Tiny3D submission](../src/render_t3d.inc).

## Prioritized milestones and completion criteria

| Phase | Work | Completion evidence |
| --- | --- | --- |
| 1. Measure and partition | Audit complete routes; cook region membership, dependencies and worst sightlines; establish whole-game RAM and ROM ledgers. | Every retained surface/entity has stable identity and region coverage. Reports show peak overlap, byte categories, omitted behavior and ROM size. Keep the full study protected from direct Play. |
| 2. Stream two connected regions | Implement versioned chunk loading, bounded pools, prefetch and fenced retirement; retain global object/actor state. | Repeatedly cross both ways with audio running. No geometry/collision holes, stale pointers, duplicated loot or growing heap. Peak memory stays within an agreed 8 MiB budget including reserve and transition scratch. |
| Editor gate before content development | Make regions, stable home assignments, bounds and doorway annotations editable with undo and atomic scene/manifest saves. Inspect dependencies, invalidation and complete memory categories. Use the same package/loader in Native Play and ROM. | Create and assign objects, move boundaries, edit openings, duplicate/recover missions and diagnose invalid seams entirely through the editor. Matching input traces preserve gameplay state during delayed loads in Native Play and ROM. Full-scene authoring preview alone does not pass this gate. |
| 3. Establish movement and visibility | Add richer world collision, spatial queries, navigation and conservative portal visibility; rebuild derived metadata after editor or Blender edits. | Walk/crouch through representative slopes, stairs, arches, vertical links and seams. Hidden-room tests reduce submitted work without missing visible surfaces; edited openings rebuild correctly. |
| 4. Establish stealth behavior | Stream baked lighting; add local probes/light lists, door invalidation, surface sound and persistent guard scheduling. | Deterministic route tests preserve guard awareness, sound responses and loot/objective state across unload/reload. Door changes update local sight/light/nav data within the agreed budget. |
| 5. Make all intended routes playable | Implement or replace water, ladders and trapdoors; author patrols and the adapted mission objective flow. | Start-to-scepter-to-completion traversal works without debug teleports. Audit alternate retained routes and record intentional differences from Thief. |
| 6. Accept the whole mission | Test the complete packaged ROM, repeated traversals, busy views and transitions on emulator and original N64/M64. | Record ROM/source hashes, device/firmware, RAM high-water marks, load stalls, frame-time distributions and audio underruns. No unbounded residency, invalid accesses or persistent-state loss. |

Use the project's **30 fps / 33.33 ms** frame goal as a measurement target, not a
promise from the entry-area capture. Include gameplay, renderer, graphics waits,
audio and streaming in that budget. A stationary emulator screenshot, average
frame time, or successful host cook is insufficient. Acceptance routes should
include exterior/interior thresholds, several open doors, simultaneous guards,
lighting changes, pursuit across region boundaries, and repeated backtracking.
Allow geometry consolidation or local LOD where measurements require it, while
keeping a documented comparison with the source `.blend`.

The current conversion tests and Blender roundtrip remain regression coverage
for source parsing and authoring. Add runtime tests around region lifetime,
stable IDs, conservative visibility and seam collision when those systems
exist; retain original-hardware results alongside emulator diagnostics.
For broader planning context see the
[scaling guide](scaling-guide.md),
[hardware guide](n64-hardware-guide.md) and
[audio memory plan](audio-memory-planning.md).
