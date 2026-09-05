# First playable

Status: milestone proposal; the project is documentation only. This slice tests the [vision](vision.md) and [architecture](architecture.md) through the [content workflow](workflow.md), establishing a dependable iteration loop before a larger mission.

## The room

Build one small stealth space with a lit route, a darker hiding position, one patrolling guard, a door operated by a linked control, and a reachable objective. The player can move through the room, make a sound that the guard hears, manipulate the door, and complete the objective. Geometry and gameplay placement originate in LightEngine, with Blender assets where useful.

LightEngine already has inspected authoring, serialization, preview, and reload capabilities. The target exporter, console movement, guard behavior, perception, linked interactions, and objective logic are proposed work. Desktop Jolt physics and modern lighting do not establish equivalent N64 behavior.

## Milestones and acceptance

### 1. Prove the content round trip

Establish versioned formats, stable references, validation, and a ROM displaying editor-authored geometry. Change an object's placement and rebuild: the ROM must reflect the edit without manually modifying generated data or game source. Missing assets and broken references must produce actionable diagnostics. Record build duration and output sizes.

Verify normal startup with 8 MiB and a clear Expansion Pak requirement message with 4 MiB in Ares, before expansion-dependent data is accessed.

### 2. Traverse and interact

Add controller input, player movement, collision, and a door/control relationship. The player must traverse the intended route without passing through walls; the authored control must operate the referenced door. Move the control or retarget its link in the editor and verify the next ROM uses the change. Capture baseline frame timings and runtime memory usage.

### 3. Establish stealth feedback

Add an authored patrol and a small, observable guard behavior model. The guard follows the route, responds to a visible player, and investigates an audible event according to explicit tuning data. Repeated trials from defined positions must distinguish the lit and dark locations and audible and inaudible events. Display the guard's state and triggering perception input. The precise lighting, visibility, sound propagation, and rendering implementations remain open.

### 4. Complete and validate the loop

Add an objective with visible completion feedback and a repeatable way to restart the scenario. Persistence and save strategy remain undecided. Verify a complete run from spawn to objective, then repeat after an editor-authored content change.

The slice must fit the [8 MiB memory baseline](decisions/0001-memory-baseline.md). Record peak measured usage, remaining headroom, frame timings, and workload conditions before choosing detailed performance budgets. Test startup, movement, perception, interaction, and completion in Ares and on original N64 plus Expansion Pak and M64. Hardware checks are **pending** until run; emulator success alone does not complete hardware acceptance.
