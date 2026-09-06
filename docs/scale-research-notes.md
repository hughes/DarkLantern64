# Scaling research and proposed architecture

Research date: **2026-09-05**. These are source-backed implementation options and a proposed experiment plan, not measured entity limits. The [courtyard study](night-contrast.md) is our existing renderer baseline; the [hardware guide](n64-hardware-guide.md) describes the accepted 8 MiB platform. Ares results should remain labeled emulator observations until checked on original N64 and M64.

## Count the work that reaches each system

“How many guards/rooms/lights?” needs several counts:

| Count | Meaning | Main cost |
| --- | --- | --- |
| Authored | Present anywhere in the mission | ROM, authoring and save-state complexity |
| Resident | Assets and state currently in RAM | RDRAM, including transition overlap |
| Potentially visible | Survives room and camera visibility tests | Bounds tests, transforms and draw preparation |
| Drawn | Submitted geometry after culling | CPU/RSP geometry, RDP pixels and memory traffic |
| Simulated | Receiving movement, perception, interaction or sound work | CPU and event/query budgets |
| Affecting a receiver | Lights or sounds relevant to one actor/surface/listener | Local overlap and occlusion work |

Twenty guards spread across a mission and twenty animated guards chasing the player in one courtyard are different workloads. Fifty furnished rooms need not have fifty rooms' geometry transformed every frame. A hundred shared-mesh valuables on shelves need not have a hundred physics bodies, animated highlights, or individual per-frame scripts. Conversely, even a single large window can expose many rooms at once.

The first playable started with one guard, one objective, one door/control pair, and no room graph. Its compiler/render cache bounds were 128 model instances, 4,096 instanced vertices and 4,096 instanced triangles. These are prototype software contracts, not N64 hardware capacities. Check the current compiler and scale-study results before treating them as authoring limits; simply raising them does not remove whole-scene work.

## Visibility: use rooms and portals, while preserving free 3D geometry

Luebke and Georges' original portal paper describes conservative runtime visibility through connected cells and openings. It reduces geometry sent to the graphics pipeline; it does not replace the meshes with a grid. Its principle fits rooms, angled halls, stairs, balconies, windows and doorways. [Luebke and Georges, *Portals and Mirrors*](https://www.luebke.us/publications/pdf/portals.pdf)

Our proposed first version uses editor-authored convex 3D volumes, connected by convex portal polygons. A gameplay room can contain multiple volumes, while an open yard can be its own group of volumes. Meshes retain arbitrary XYZ positions and rotations. Large meshes spanning many cells should be split during cooking or conservatively assigned to every cell they intersect.

At runtime, locate the camera's cell, traverse visible openings, narrow the allowed view at each opening, and draw conservatively visible models once. Camera-frustum bounds rejection should precede per-vertex transforms. Back-face and triangle clipping tests remain useful inside the surviving models. The depth buffer resolves the remaining overlap; it cannot refund CPU transforms already performed for an unseen room.

An opaque closed door can block a visibility connection only when it really covers that opening. A partly open door, grate or window must preserve the corresponding visibility. Near-plane clipping and camera-on-boundary behavior need conservative handling. Reaching the same cell through a second opening can reveal additional geometry: a simple “visited once” flag is insufficient if the first traversal carried a narrower view. Retain coverage or revisit when coverage expands, with a bounded conservative fallback for cycles.

A precomputed potentially visible set (PVS) is a later option: cook an upper bound assuming relevant doors are open, then narrow it using current portal state and camera view. For perspective on the representation alone, a dense 64-cell by 64-cell bit matrix is **512 bytes**, and a 128-cell matrix is **2,048 bytes**. These are calculated PVS bits only, excluding geometry, portal polygons, metadata and working memory. A PVS can save traversal work but can be loose in large connected outdoor spaces.

Quake II's original source is a useful precedent for combining precomputed visibility, bounds checks and independently updated door-area connectivity. Its `CM_SetAreaPortalState` updates connections, and world traversal checks area bits in addition to visibility and bounds. It is evidence for the separation of these mechanisms, not a benchmark for this game or a reason to import its world format. [id Software, area connectivity](https://github.com/id-Software/Quake-2/blob/master/qcommon/cmodel.c), [world traversal](https://github.com/id-Software/Quake-2/blob/master/ref_gl/gl_rsurf.c)

## A hidden room still exists

Share room IDs and door IDs across systems, but keep their rules separate:

| System | What the connection means |
| --- | --- |
| Rendering | Some geometry might be visible through this opening |
| Sound | Energy passes with material-dependent attenuation/filtering and path distance |
| Navigation | This agent can traverse or operate the connection |
| Simulation relevance | Events, proximity or a schedule require detailed updates |
| Streaming | Assets should be loaded soon enough for movement and backtracking |

A shut wooden door can block vision while transmitting muffled footsteps. Glass can permit sight while blocking navigation. A guard behind the camera must still hear, move and pursue. Culling rendered models must never freeze these behaviors automatically.

For guards, separate movement from expensive decisions. Nearby or alerted guards need frequent movement and timely sound/vision response. Distant routine patrols can advance along coarse schedules, receiving events and preserving elapsed time. Stagger perception and path requests rather than updating every guard's expensive query on one frame. Wake relevant guards on sounds, alarms, opened doors or player approach. Those rules need deterministic replay tests so optimization cannot erase a guard's knowledge or grant the player a turn-around exploit. Thief's original sensory-system account emphasizes retained awareness and meaningful sound inputs, which is the behavior to preserve. [Tom Leonard, *Building an AI Sensory System*](https://www.gamedeveloper.com/programming/building-an-ai-sensory-system-examining-the-design-of-i-thief-the-dark-project-i-)

Use a spatial index for collision, sight rays, light candidates and nearby interactables. An internal grid is one possible accelerator and does not constrain level geometry; a BVH or per-cell lists better match some content. Queries should touch nearby candidates, rather than scan all mission colliders for every guard or light probe.

## Lighting has several very different scaling costs

| Light behavior | Proposed implementation | What to measure |
| --- | --- | --- |
| Fixed moon/street lamps | Bake static receiver colors and actor/stealth probes during cooking | Bake size, probe density, visible vertices |
| Fixed lamp that switches, dims or flickers | Store a bounded number of separate linear contributions for affected receivers; scale and combine locally | Affected samples, overlapping channels, dirty update work |
| Moving lantern or transient flash | Select nearby receivers and a small local light set; update actor probes and selected surface colors | Receiver count, vertices, shadow queries and scheduling |
| Moving shadow caster | Coarse shadow/occlusion proxies, selective receiver updates, explicit visual quality tier | Ray candidates, changed receivers and response latency |
| Flame/glass glow | Emissive material plus a small optional translucent glow | Geometry and screen coverage/overdraw |

Static scenery can therefore have many authored lights without evaluating every source every rendered frame. However, the current actor and stealth queries still iterate the authored light list. A static bake alone does not make that runtime path independent of light count; local candidate lists or baked probes must accompany it.

For independent fixed lights, store **linear irradiance contributions before exposure/clamping**, not separately tone-mapped final colors. Add or scale the relevant contributions, then apply the presentation response. The same authored on/off/intensity state must drive stealth probes. Quake II's original lightmap builder similarly combines separately scaled light styles and adds dynamic contributions; our proposed vertex/probe adaptation would be new implementation work. [id Software, `R_BuildLightMap`](https://github.com/id-Software/Quake-2/blob/master/ref_gl/gl_light.c)

Do not generalize the courtyard's two precomputed gate states into one entire-scene bake for every combination of doors. With N independent binary doors that would require 2^N states. Instead, record local light-to-receiver dependencies and invalidate only receivers whose visibility can change. A fixed source/receiver ray can have a compact set of relevant dynamic blockers; its contribution is enabled only when those blockers permit it. This works for restricted opaque blocker behavior, while partially opened or moving geometry may require fresh local queries. Independent lamp intensity changes can add linearly; arbitrary moving occluders cannot be handled by naïvely adding independent door deltas.

Moving light is easier than convincing moving shadows. Recomputing all surface shadows every frame would spend much more than changing a lamp color. Keep the player light meter and nearby gameplay-critical receivers current; amortize lower-priority presentation updates with a documented latency budget and inspect for visible popping. Avoid claiming arbitrary dynamic shadowing before that experiment exists.

## Evaluate RSP geometry before setting ambitious visible-actor budgets

Tiny3D currently supplies a full RSP geometry pipeline, culling/BVH support, compact vertices, and mesh animation. Its README currently requires libdragon's `preview` branch. It can be locally pinned, but the installed fork/toolchain and our asset/lighting contract need compatibility checks before adopting it. This is a proposed measured backend experiment, not an immediate SDK replacement. [Tiny3D project README](https://github.com/HailToDodongo/tiny3d)

The current Tiny3D API permits ambient plus **seven combined directional/point light slots**. These are active draw-state slots, not seven lights in an entire mission, and not seven free shadow-casting lights. Point-light radius is documented as approximate because of precision and per-vertex evaluation. Select a small relevant subset per region or object; shadow visibility remains a separate problem. Its frustum functions are conservative bounds tests, not collision tests. [Tiny3D lighting API](https://github.com/HailToDodongo/tiny3d/blob/main/src/t3d/t3d.h), [frustum API](https://github.com/HailToDodongo/tiny3d/blob/main/src/t3d/t3dmath.h)

Moving transforms and suitable lighting to the RSP should free CPU time for guards and sound, but it does not remove geometry, pixel, bus or audio contention. Tiny3D's own cooking approach groups vertex loads, reuses a small vertex cache, and emits triangle strips to reduce command work. Keep those optimizations in our cooker rather than asking artists to arrange every triangle manually. [Tiny3D model optimization notes](https://github.com/HailToDodongo/tiny3d/blob/main/docs/modelOpt.md)

libdragon also supports recorded command blocks that reduce repeated CPU command generation and command traffic. Use them for reusable rendering sequences; the current camera-dependent, CPU-clipped triangles cannot simply be recorded once unchanged. [libdragon RDPQ API](https://libdragon.dev/ref/group__rdpq.html)

## Experiments that turn these options into budgets

These counts are **test points**, not promises:

| Experiment | Suggested sweep | Separate the variables |
| --- | --- | --- |
| Guards | 1, 2, 4, 8, 16 | Drawn versus hidden; idle versus chase; sight/hearing/pathfinding; animation; overlap |
| Rooms | 1, 4, 8, 16, 32, 64 | Same visible foreground with more hidden rooms; then long aligned doorways and open yard |
| Loot | 1, 16, 64, 128, 256 | Shared-mesh records versus simultaneously visible objects; pickup and save state; physics only when needed |
| Point lights | 0, 1, 2, 4, 8, 16 | Authored/out-of-range versus overlapping; fixed versus moving; occluded versus unoccluded |
| Door/light changes | 1, 2, 4 simultaneous | Dirty receiver count, worst-frame spike and gameplay/visual agreement |
| Audio | Quiet patrol versus alarm | Audible voices, simulated sound events, streaming, RSP load and underruns |

Hold resolution, camera route, simulation seed, textures and backend fixed within each comparison. Record frame distribution and worst interactions, CPU categories, transformed/drawn triangles, texture uploads, visible/active counts, candidate/ray counts, RDRAM high water and audio underruns. Repeat the combined worst case after isolated sweeps: several alerted guards, overlapping lights, an opening door and spoken audio are the important stealth workload.

Other valuable stress cases include animated guards at different distances, many differently textured props, transparent lamp halos/rain, long sightlines, vertical overlap, repeated room transitions, fast backtracking, dropped objects/corpses, and save/load with many changed doors and collected valuables. Total triangle count alone misses texture changes, near-plane clipping, filled pixels and alpha overdraw.

The immediate architectural order is: bounds culling and counters; local collision/light/interactable candidates; multiple guard and loot state; a small room/portal prototype; then a controlled RSP backend comparison. Add an editor budget view using cooked estimates and measured captures, clearly distinguishing estimated bytes, observed times, and untested cases. Streaming should follow evidence that a well-partitioned resident mission exceeds the memory budget, rather than becoming a prerequisite for the next room.
