# Mission scale and performance

Prepared **2026-09-05** for the 8 MiB N64/M64 target. This guide distinguishes current software limits, measurements and proposed architecture. We should establish a reliable **33.33 ms frame budget for 30 fps**, including audio and worst-case interactions, before promising a final mission size. Emulator measurements remain diagnostic until repeated on original N64 and M64.

We can design missions with substantially more content than appears on screen at once. The useful question is how much must be rendered, simulated, queried and kept in memory at the same time. Guards, lamps and loot have very different costs; one universal entity cap would conceal those differences.

## What the prototype supports today

| Resource | Current software contract | What it does not establish |
| --- | --- | --- |
| Guard | 0–16 guards with individual patrol/sentry behavior and awareness states | A measured maximum number of guards at the target frame rate |
| Loot | One interactive objective/relic plus decorative imported models | A general inventory or many-item pickup system |
| Doors | Exactly one door and one linked control | Independent states for multiple doors |
| Lights | 1–16 authored point lights; optional directional moon | Sixteen overlapping moving lights at a guaranteed frame rate |
| Models | At most 128 instances | A platform-wide object limit |
| Geometry | At most 4,096 instanced vertices and 4,096 instanced triangles | That all of this geometry is affordable in every view |
| Entities/collision | At most 256 authored entities and 256 collider boxes | A room count or full physics budget |
| Rooms | No explicit cell/portal representation yet | A restriction to one room or grid-aligned geometry |

These limits come from the [compiler](../tools/compile_level.py) and [runtime structures](../src/game.h), not Nintendo's hardware. Raising them requires appropriate data structures and measurements, particularly because several current queries scan complete arrays.

**Implemented in this scale study:** conservative model-bounds rejection before camera vertex processing. This avoids transforming and submitting models entirely outside the view. It complements the existing back-face and triangle clipping paths. It does not yet identify every room hidden behind a wall.

## Measured results

The [recorded scale study](scale-study-results.json) completed 16 render runs and 24 isolated simulation/query cases, followed by a 30-case diagnostic adding six exposed-light configurations. The original results and follow-up have separate ROM hashes and records. Render runs use the same stationary courtyard camera, normal audio, debug overlay off, and three complete profiling windows: **31–65 frames per run**, usually 61. They are short diagnostic samples, not full mission routes or sustained worst-case acceptance tests. Static lighting preparation occurs before these frame windows.

Those recordings describe their recorded source hashes. The courtyard has since gained art, structural geometry and practical lighting, so they are not measurements of the latest scene. Current render fixtures omit decorative imported props without collision to reserve room for the controlled workload; imported collision geometry, gameplay actors and practical lights remain. Reports list excluded entity IDs and the benchmark recipe hash. Microbenchmarks continue using the authored level. Rerun the study before comparing performance across these scene or recipe changes.

Times below use the emulated N64 timer. **“Outside display wait” means average frame elapsed time minus the time attributed to `display_get`.** It includes graphics-queue stalls and other elapsed work; it is not pure CPU execution, CPU utilization or RDP execution time. Full-frame averages include display waiting. Short samples and queue/display scheduling can shift time between categories, so repeated routes and hardware checks remain necessary.

### Guard simulation, excluding rendering

Each sample runs four 1/120-second guard updates, representing the current work for one 30 Hz simulation frame. Sixty samples follow four warm-up frames; guard state persists. The workload uses 26 courtyard colliders, independent copies of the existing guard behavior, and a shared precomputed player-visibility input. Audio callback time is subtracted from these isolated timings.

| Guards in isolated workload | Patrol ms/sample | Chase ms/sample |
| --- | ---: | ---: |
| 1 | 1.31 | 1.40 |
| 2 | 2.68 | 2.85 |
| 4 | 5.28 | 5.63 |
| 8 | 10.27 | 10.89 |
| 16 | 20.22 | 21.38 |

These results show a scaling problem worth addressing before adding many actors: sixteen copies already consume about 21 ms of a 33.33 ms frame allowance in behavior alone. They **do not establish sixteen playable guards**. Rendering, animation, inter-guard behavior, new pathfinding, player visibility queries and the rest of the game are excluded. The runtime still has one functional guard.

### Hidden room shells and bounds culling

Each added “room” is a closed shell of six boxes behind the unchanged camera. All its collision proxies remain in the current global query lists. This is a test of extra hidden geometry and collider scanning, **not connected navigable rooms or portal visibility**. Values show culling disabled → enabled.

| Extra hidden shells | Models / colliders | Camera vertices | Frame ms | Outside display wait ms |
| --- | --- | ---: | ---: | ---: |
| 0 | 41 / 26 | 1,826 → 1,760 | 49.90 → 49.87 | 39.92 → 40.89 |
| 4 | 65 / 50 | 2,402 → 1,760 | 50.08 → 50.05 | 46.32 → 44.49 |
| 12 | 113 / 98 | 3,554 → 1,760 | 58.54 → 50.15 | 58.00 → 50.07 |

The base view rejects only two models. Its outside-display measurement becomes **0.97 ms worse** despite a small reduction in transforms; this change is not a universal speedup. With twelve hidden shells, however, camera vertex work roughly halves and outside-display elapsed time falls by **7.92 ms**. Bounds culling prevents most of the extra transform work, but gameplay and lighting still grow because their queries scan the added colliders. Even this culled result misses 30 fps. The next improvement should address query candidates as well as room visibility.

### Visible loot models

These fixtures add static copies of the relic mesh in front of the camera. They add no pickup logic, animation, physics or new collision proxies. The table uses bounds culling enabled; the baseline retains its original single functional objective.

| Additional static loot models | Camera vertices | Frame ms | Outside display wait ms | Triangle submission ms |
| --- | ---: | ---: | ---: | ---: |
| 0 | 1,760 | 49.87 | 40.89 | 23.32 |
| 8 | 2,144 | 58.45 | 53.59 | 34.49 |
| 32 | 3,296 | 100.14 | 96.67 | 71.68 |

Thirty-two visible copies bring this view to approximately **10 fps**. Submission timings include queue stalls, so this is evidence of the current rendering path's cost rather than an isolated per-item CPU price. It does not rule out hundreds of cheap persistent item records distributed through a culled mission. It does rule out treating many simultaneously drawn placeholder models as free. The 32-copy fixture also raises sampled heap use by 108 KiB over this baseline; that sample is not an allocation high-water measurement or a whole-game memory budget.

### Fixed light sources and runtime probes

The render fixtures keep the same 38 scenery models and 26 colliders, remove decorative light models, and repeat authored lamp positions with repeated intensities divided between sources. These test fixed source-list/query work and cached static lighting, not freely moving lamps. Bounds culling is enabled.

| Point-light records | Frame ms | Outside display wait ms | Gameplay ms | Runtime lighting ms |
| --- | ---: | ---: | ---: | ---: |
| 1 | 49.71 | 38.83 | 3.88 | 2.15 |
| 8 | 49.87 | 42.71 | 6.36 | 3.75 |
| 16 | 49.95 | 46.69 | 9.83 | 5.07 |

The similar frame cadence masks increasing work and shrinking display wait. Sixteen fixed source records run, but that is not a claim that sixteen dynamic shadowed lights are affordable. The growth in gameplay and actor lighting directly motivates local light lists or cooked probes.

The isolated query sweep uses a small patch near the courtyard spawn, moon disabled, with **all tested source-to-probe contributions blocked**: checksums contain ambient illumination only. Each batch contains 32 probes and is repeated eight times. These are specific blocked-query costs, not a representative mix or a worst-case lighting bound.

| Nearby point-light records | 32 surface probes ms | 32 stealth-visibility probes ms |
| --- | ---: | ---: |
| 1 | 3.49 | 3.30 |
| 4 | 12.72 | 12.52 |
| 8 | 25.38 | 24.91 |
| 16 | 50.66 | 49.58 |

A moved-source variant recomputes **128 probes in 13.89 ms** and **512 in 55.63 ms** with one light, four batches each. Those samples also remain blocked. The timer excludes moving the source, color conversion, vertex uploads and drawing. This establishes the expense of naïve recomputation in this configuration, not a working dynamic-light visual effect or its final cost.

The exposed-light follow-up lowers the sources below the shelter roof while retaining the same probe patch and all 26 colliders. Every tested ray is clear, so these queries traverse the full collider list and accumulate direct illumination. The original blocked rays stop at the ninth collider. In the new run, **32 exposed surface probes with 1 / 4 / 8 / 16 sources cost 6.41 / 25.02 / 49.77 / 98.68 ms**. Recomputing **128 / 512 exposed probes for one moving source costs 25.54 / 102.17 ms**, still excluding color conversion and drawing. The nonambient checksums confirm that light reaches these receivers. This is further evidence against whole-scene per-vertex shadow tracing every frame; it does not measure an optimized local-light implementation.

A separate scan fixture keeps four lights and eight surface probes while adding distant axis-aligned collider boxes that every ray misses. With **0 / 16 / 64 / 256 colliders**, the batch takes **0.32 / 3.01 / 9.09 / 31.80 ms**. This deliberately forces complete linear traversal without an early occluder. It is strong evidence for a collision/light spatial index, not a claim that ordinary scenes always take this path.

The practical result is a prioritized set of bottlenecks: expensive visible geometry, guard work repeated at motion frequency, and light/collision queries over whole lists. The measurements justify culling and local query/scheduling work; they do not yet provide final capacities for guards, playable rooms, interactive loot or dynamic lights.

### Reproduce and verify

```powershell
python tools/scale_study.py --compare-culling
python tools/scale_study.py --micro-only
python tools/scale_study.py --skip-micro --cases baseline hidden12 --compare-culling
```

The first command runs the current 30-case workload suite and all 16 render comparisons. The other commands run shorter subsets. These specialist studies are terminal commands, indexed in [developer tools](developer-tools.md). Results, archived logs and generated fixture copies go into ignored build/development directories; `build/scale-study/latest.json` points to the latest completed report. The saved source and controller settings remain unchanged.

Validation passed: host gameplay/profiler/diagnostic tests, six-plane bounds tests including 12,000 visible-point containment cases, N64 ROM builds, the 4 MiB startup error, and the 8 MiB mission replay. All four authored courtyard framebuffers have identical PNG hashes to the stored pre-culling views, including the open gate. These are raw RDP buffers before VI filtering; capture timings are excluded from the performance study.

## The counts the editor should show

| Count | Example | Governs |
| --- | --- | --- |
| Authored | All guards, rooms and valuables in a mission | ROM and persistent state |
| Resident | Currently loaded meshes, textures, sound banks and state | RAM, including temporary transition overlap |
| Potentially visible | Models surviving room and camera tests | Candidate geometry work |
| Drawn | Actual submitted triangles and materials | Geometry, texture traffic, pixels and overdraw |
| Active | Actors doing detailed movement, perception or pathfinding | Simulation cost |
| Local overlap | Lights affecting one actor or surface | Lighting and shadow-query cost |

A hundred valuables distributed through a mansion is a different test from a hundred spinning valuables on one table. Six guards heard through a wall can remain relevant even when none is visible. Additional rooms should chiefly add stored data when their contents cannot affect the current view or simulation.

## Guards: benchmark behavior and rendering separately

The next structural step is a shared mission/world with arrays of guard components: transform, patrol progress, awareness, investigation target, timers and animation state. All guards refer to the same scene geometry and spatial indexes. We should not turn multiple full copies of the single-guard `DlGame` into the shipping architecture.

Movement and perception need different schedules. The current update subdivides motion at approximately 120 Hz and also calls guard behavior during those substeps. Keep the movement/collision stability that substeps provide, but test perception at **5–10 Hz**, staggered across guards. Treat those frequencies as tuning candidates, not an accepted responsiveness target. Sounds, alarms and important state changes enter an event queue immediately; they should not disappear because an actor's next routine vision check is later. Chase and nearby critical interactions may require more frequent checks.

Distant patrols can advance through a coarse schedule without detailed animation or constant sight rays. Preserve elapsed time, patrol position, awareness and event history. Wake actors through proximity, sound or an alarm, and test fast player reversals. Turning the camera away must not stop a pursuer or prevent a guard hearing footsteps.

Extend the isolated sweep to **1, 2, 4, 8 and 16 functional guards**, separately measuring movement/collision, vision, sound events, path requests, model rendering and animation. Then combine them in a chase scene. No guard count becomes a content budget until the combined workload fits the frame and memory targets with headroom.

## Rooms: portals are appropriate for a fully 3D world

Use invisible convex 3D cells connected by polygonal openings. Cells are visibility metadata around authored models: they permit angled walls, stacked floors, stairs, balconies, windows and outdoor yards. One designed room can span several cells. A doorway between indoor and outdoor cells remains a continuous walkable space.

Start with the camera cell and traverse visible openings, narrowing the view through each opening. Test surviving models against their bounds before transforming vertices. Cook large scenery into useful visibility chunks. Conservative assignment is essential for a model straddling cells or a camera sitting in a doorway.

Later, a cooked potentially visible set (PVS) can reject impossible cell pairs before runtime traversal. Keep doors open when computing that conservative upper bound; current opaque door state can reduce visibility further. Large courtyards and long aligned doorways are the difficult case because they legitimately reveal more geometry. The original cell/portal research provides this architectural basis. [Luebke and Georges, *Portals and Mirrors*](https://www.luebke.us/publications/pdf/portals.pdf)

Rendering, hearing, navigation and residency should share stable room/door identities while keeping separate connection properties. A shut wooden door can block sight yet transmit muffled voices. A window can allow sight without permitting a guard to walk through it. A navigation portal's traversability is not its acoustic transmission. Render culling is not permission to freeze simulation.

Test **1, 4, 8, 16, 32 and 64** rooms in two layouts: a winding mission with the same limited foreground, and a deliberately expensive series of aligned openings or broad outdoor views. This establishes the difference between total mission size and visible complexity. Add streaming only when measured residency requires it; culled rooms may remain loaded.

## Loot: many persistent records, few expensive operations

Static loot should use shared meshes/materials and compact per-item state such as identity, location, value and collected status. Uncollected coins or cups need neither continuous physics nor a script update every frame. Find pickup candidates through nearby lists; render only visible items. Group decorative piles where individual interaction has no design value.

**Hundreds of authored items is a sensible experiment**, not a supported capacity today. Try **1, 16, 64, 128 and 256** items with separate tests for hidden storage, visible geometry, dense pickup candidates, and persistent collected state. Dropped objects and physics-active loot form a separate, more expensive class; use sleeping and activation rules if we add them. A table full of unique textures or animated gleams can become expensive even when its item records are tiny.

## Lights: classify what can change

| Class | Recommended approach | Main constraint |
| --- | --- | --- |
| Fixed environment light | Cook static illumination and actor/stealth probes | Bake/probe memory and visible geometry |
| Fixed lamp that switches/dims/flickers | Separate linear contributions on a bounded set of receivers | Local overlap and recombination work |
| Moving lantern or brief flash | Local receiver/light lists and selective updates | Vertices/probes touched and update latency |
| Moving shadow | Restricted occluders and targeted receiver updates | Visibility queries and shadow quality |
| Emissive glass/flame | Material emission, optional restrained glow | Pixels/overdraw rather than illumination queries |

The current courtyard bakes static night colors during content cooking and loads two gate states from ROM. Drawing a static surface does not loop over every authored lamp. Memory and preparation still cost something, and moving actors/player visibility still query lights. **Static lighting is not globally free.** The [loading study](level-loading.md) measures the completed cooker change; spatial light candidates or baked probes remain useful so runtime queries do not scan an entire mission.

For switches and flicker, retain linear irradiance contributions before exposure and clamping. Combine a small number of relevant contributions, then apply the display response. Keep the same light state in stealth calculations. Separately scaled static lighting layers have a useful precedent in Quake II's original lightmap builder; our vertex/probe implementation would be new work. [id Software, `R_BuildLightMap`](https://github.com/id-Software/Quake-2/blob/master/ref_gl/gl_light.c)

Do not extend the two-state gate cache to every combination of N doors: that grows as **2^N**. Instead, cook sparse source/receiver dependencies and invalidate only affected receivers. Restricted opaque blockers can use visibility dependencies; partly open or moving occluders require local updates. Independent lamp intensities can add linearly, but arbitrary door shadows cannot be reproduced by adding independent precomputed door deltas.

For the first moving-light experiment, compare **0, 1, 2 and 4** lights with bounded influence, and **0 versus 1 targeted shadow-casting light**. This is a proposed quality tier to measure, not a hardware limit. Color/intensity animation and moving pools are more approachable than fine moving shadows. Prioritize current gameplay visibility and nearby surfaces; document any delayed presentation updates and test for popping.

Extend the fixed-lamp sweep to **1, 2, 4, 8 and 16** across more configurations, separating out-of-range lamps from lamps overlapping the same receiver. A lamp count without that distinction says little about runtime cost.

## RSP geometry is the next backend comparison

Our current renderer still spends CPU time transforming and preparing triangles. Tiny3D provides an RSP geometry pipeline, compact vertices, culling support and animation. Its current README requires libdragon `preview`; evaluate a pinned compatible build against the installed fork and current content contract before migration. [Tiny3D project](https://github.com/HailToDodongo/tiny3d)

Its current light API supports ambient plus **seven shared directional/point slots per draw state**. That does not mean seven lamps per mission or seven shadowed lights at no cost. Choose relevant sources locally, and preserve separate shadow/stealth handling. Point-light radius is approximate because of precision and per-vertex evaluation. [Tiny3D lighting API](https://github.com/HailToDodongo/tiny3d/blob/main/src/t3d/t3d.h)

Compare identical content and camera paths under both backends, including audio. RSP acceleration can free CPU time but still shares processor and memory resources with other work. Keep materials grouped, exploit shared meshes and cook efficient vertex/index batches. Do not promise a multiplier before measuring it.

## Other limits worth finding early

| Experiment | Why it matters |
| --- | --- |
| Animated/skinned guards, corpses and held items | A static placeholder does not measure animation matrices, vertex work or animation memory |
| Full chase with footsteps, voices and muffled adjacent rooms | Tests audio voices, simulated hearing events, propagation, mixing and underruns together |
| Rain, smoke, lamp halos, glass and water | Transparent screen coverage and repeated pixels can dominate otherwise small scenes |
| Material variety and texture uploads | Total texture bytes differ from per-frame TMEM traffic; many small materials can still cause churn |
| Two-room doorway residency and immediate backtracking | Transition overlap, decompression buffers and audio banks determine peak RAM |
| Vertical overlap and long outdoor sightlines | Exposes weaknesses hidden by short, winding corridors |
| Simultaneous door/light changes | Finds worst-frame stalls and disagreement between visible light and stealth calculations |
| Saving many altered objects, then loading | Tests stable IDs, persistence size and preservation of collected loot, doors and awareness |

The editor should expose cooked resource estimates beside observed runtime captures: resident bytes, high water, visible/active counts, local light overlap, transformed/submitted geometry and query counts. Distinguish estimates from measurements, and show worst interactions as well as averages. See [research notes](scale-research-notes.md) for source details and correctness cases.

The implementation sequence is bounds culling and counters first; spatial collision/light/interaction candidates and guard scheduling next; shared multiple-guard/loot state and a small cell/portal scenario after that; then a controlled RSP backend comparison. Those foundations should let us raise mission totals while keeping visible and active work deliberate.

The follow-up [Kaze performance research](kaze-performance-notes.md) adds source-backed lessons on code footprint, cached collision bounds and static lifetimes, plus an audit of our actual MIPS calls and reserved buffers. It strengthens the case for measuring memory traffic and redundant work alongside polygon counts.
