# First playable

Status: implementation and validation in progress. This slice tests the [vision](vision.md) and [architecture](architecture.md) through the [content workflow](workflow.md), establishing a dependable iteration loop before a larger mission.

## The room

Build one small, fully 3D stealth space with a lit route, a darker hiding position, one patrolling guard, a door operated by a linked control, and a reachable objective. The player can move through the room, make a sound that the guard hears, manipulate the door, and complete the objective. Geometry and gameplay placement use model assets and independent XYZ transforms in LightEngine, with Blender assets where useful. Include rotated geometry and raised surfaces so validation exercises the actual 3D contract.

The prototype now has a content compiler, editor save/build interface, console input, portable simulation, perception, linked interactions, and objective logic. The full 3D revision must pass the checks below before its emulator slice is accepted. Desktop Jolt physics and modern lighting do not establish equivalent N64 behavior.

## Milestones and acceptance

### 1. Prove the content round trip

Establish versioned formats, stable references, validation, and a ROM displaying editor-authored geometry. Change an object's placement and rebuild: the ROM must reflect the edit without manually modifying generated data or game source. Missing assets and broken references must produce actionable diagnostics. Record build duration and output sizes.

Verify normal startup with 8 MiB and a clear Expansion Pak requirement message with 4 MiB in Ares, before expansion-dependent data is accessed.

Verify that a changed model transform reaches both the LightEngine preview and the compiled ROM, including a non-grid-aligned rotation. The console view must show perspective, model depth occlusion, varying heights, and camera pitch. Guards and interactables must use meshes.

### 2. Traverse and interact

Add controller input, player movement, collision, and a door/control relationship. The player must traverse the intended route without passing through walls; the authored control must operate the referenced door. Move the control or retarget its link in the editor and verify the next ROM uses the change. Capture baseline frame timings and runtime memory usage.

### 3. Establish stealth feedback

Add an authored patrol and a small, observable guard behavior model. The guard follows the route, responds to a visible player, and investigates an audible event according to explicit tuning data. Repeated trials from defined positions must distinguish the lit and dark locations and audible and inaudible events. Display the guard's state and triggering perception input. The precise lighting, visibility, sound propagation, and rendering implementations remain open.

### 4. Complete and validate the loop

Add an objective with visible completion feedback and a repeatable way to restart the scenario. Persistence and save strategy remain undecided. Verify a complete run from spawn to objective, then repeat after an editor-authored content change.

The slice must fit the [8 MiB memory baseline](decisions/0001-memory-baseline.md). Record peak measured usage, remaining headroom, frame timings, and workload conditions before choosing detailed performance budgets. Test startup, movement, perception, interaction, and completion in Ares and on original N64 plus Expansion Pak and M64. Hardware checks are **pending** until run; emulator success alone does not complete hardware acceptance.

## Verified prototype checkpoint — 2026-09-05

The canonical v2 blockout contains four reusable meshes, 20 model instances, 14 collision boxes, 410 instantiated vertices, and 624 source triangles. It includes rotated objects and a four-step route to a platform 0.80 m above the floor. Source/content hash: `d8e73eb67179c5fb649361277e7fae1d938bc362bf57545dae5ad0b8f1a2ef4e`.

| Check | Evidence |
| --- | --- |
| Host validation | `./build.ps1 -Test` passes 23 Python tests, 13 synthetic 3D gameplay cases, and the real-room input replay. |
| Editor content round trip | Live preview XYZ/Euler change updates canonical source; Save + Cook changes the generated header; restoration returns the original header. |
| Model preview | A captured LightEngine viewport shows the room meshes, guard, rotated geometry, stairs/platform, and objective. Desktop lighting is not an N64 visual reference. |
| ROM packaging | Manual and autoplay images build with the installed GCC 16.2/libdragon SDK. Each image is 256 KiB for this checkpoint. |
| Expansion Pak gate | Ares with 4 MiB reports `boot_failed reason=expansion_pak` before renderer/audio initialization. |
| Full 3D mission replay | Ares with 8 MiB logs `world=3d`, indexed geometry, and XYZ movement; the input-only route opens the gate, climbs the four steps, and acquires the objective at simulation time 19.70 s with `caught=0`. The portable replay agrees. |
| Audio memory planning | Fourteen of the Python tests cover allocation formulas/lifetimes/schema behavior. Live editor checks cover assumptions, warnings, no-envelope state, and failed edits preserving saved data. |

`python tools/smoke_ares.py` runs the normal cached builds before testing, then records status, ROM hashes and sampled emulator telemetry in `build/ares-smoke.json`. Each emulator case uses its own ROM snapshot and logs under `.dev/ares/runs/`, so a later build cannot relabel earlier test evidence. A failed run records failure rather than retaining an old passing result. Heap samples are not an allocation high-water instrument and exclude static memory and other accounting categories. Emulator timings are diagnostic observations, not original N64 or M64 performance budgets.

Longer play sessions and original N64/M64 acceptance remain outstanding. The renderer and synthesized audio are prototypes. The separate [courtyard study](night-contrast.md) now exercises textured models and colored lighting in actual N64 framebuffer captures; animated models, recorded sound banks, room acoustics, and measured whole-game budgets remain future work.
