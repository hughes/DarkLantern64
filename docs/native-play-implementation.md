# Shared native Play and authoring implementation

Implemented 2026-09-08. Editor and ROM compile the same `src/game.c`, `src/tick.c`
and `src/animation.c`. Native Play runs desktop machine code with the console
controller/collision rules rather than Jolt. See [workspace controls](editor-workspace.md)
for menus and optional panels.

## Engine boundary

LightEngine owns named, ordered `ISystem` registration, enter/exit/pause/step,
active-scene inspection, debug drawing, host authoring-command routing, bounded
document history and pivot/snap helpers. It installs no physics or player system
implicitly. `@lightengine//:jolt_physics` and `@lightengine//:demo_player` are
explicit optional targets. The old hardcoded water behavior is removed.

DarkLantern owns portable physics, perception, guard decisions, interactions,
animation selection/contact events, normalized input and the 60 Hz tick driver.
Projects own simulation cadence; LightEngine provides frame duration and an
explicit single-step flag. The adapter cooks an unsaved snapshot, maps stable
source IDs to scene entities, and allocates independent mutable character meshes
for Play. Renderer asset handles are otherwise shared.

Stream missions also compile `src/stream_coordinator.c`, `src/world_stream.c`
and `src/stream_scene.c` on both platforms. Native Play reads the cooked package
with its SHA256/checksums and authored IDs; working collision, exact floors,
gate barriers and active visuals come from the published bank. The inspector
includes demand/physical/pending/published masks, queued doorway use, slot
generations, CPU pins and load counters. Door/loot/actor state persists
across retirement and reload. See the
[shared coordinator contract](streaming-coordinator.md) for callbacks and
synthetic delay, cancellation and lifetime tests.

## Use Play and authoring

Click **Play** on the menu bar. The active unsaved document is validated and
cooked without saving source files. Pause freezes simulation; **Step** advances
1/60 second; **Restart** restores the selected test start; **Stop** restores
editing. Loading another scene exits Play. WASD moves, mouse/arrows look, Z
crouches, Space jumps, E interacts, N emits noise, R restarts, and Escape pauses
and releases input.

**Window → Diagnostics → Play Inspector**, also opened by **Inspect** during
Play, exposes guard perception/state, exposure, diagnostic history, collision
proxies and sight/target lines. The tick driver retains edges until consumed,
consumes them once, clears pending input on pause, and caps catch-up at eight
ticks/frame while reporting dropped wall time. Recording capacity is 36,000
ticks (ten simulation minutes); reaching it pauses Play for export and restart.
Trace history retains 2,048 entries with an eviction count. The shared event
buffer holds 64 events per tick and reports overflow.

**Window → Blockout & Groups** provides boxes, floors, walls, platforms, stairs
and doorways; selection transforms about a pivot; snapping; drop to a collision
surface; groups; and encounter templates. **Objects** supports Ctrl-click
multiple selection. **Edit** supplies duplication/deletion and undo/redo. History
retains 64 snapshots. Boxes use centre placement; stairs/doorways use floor
origins. Stair rise is limited to the controller's 0.28 m step height.

**Inspector → Interaction & collision** edits links, loot values, objective
requirements/zones, visual references and upright box proxies. Controls target
one or more doors. Closing is atomic across a linked group when a door is
occupied. Loot collects once; all objectives must complete after meeting their
loot requirements. There are at most 64 interactive objects within the existing
scene limits. Test starts with `door_open:true` open all doors.

Encounter templates live in the level's `encounter_prefabs` field. Placement
expands ordinary entities with fresh IDs, remapped internal control/patrol links,
and origin metadata. External links stay external; deletion rejects incoming
links from surviving entities. These are reusable snapshots within one level;
live inheritance and cross-level packaging remain future work.

UI changes, commands, history and save use the canonical document. Structural
edits refresh through a staged cook. Invalid drafts remain inspectable and do
not overwrite saved content. Host-controlled editing hides duplicate stock
entity/property editors and renderer-only import/material/effect/background
editors; use the project's canonical panels.

## Command interface

Use `python tools/editorctl.py OP --args-file args.json`; the normal queue remains
`.dev/editor/project`. Requests use IDs and expirations; inspect a timed-out
mutation before retrying. Arguments are bounded data, with no script evaluation.

| Operations | Arguments |
| --- | --- |
| `start_play` | Optional `start_preset`, `paused` (default true) |
| `inspect_play`, `stop_play`, `restart_play` | Return current state |
| `pause_play` | `paused` boolean |
| `set_play_input` | `input` axes/buttons and `override` boolean |
| `step_play` | `ticks` 1–600, optional `input`; requires pause; optional `expected_tick` rejects stale requests |
| `get_play_trace` | Optional `since_tick` |
| `export_replay`, `replay_play` | Write/replay `replays/latest.json`; replay requires pause and matching content/test start |
| `inspect_scene` | Active entity/source IDs, transforms and rendered status |
| `query_play` | Bounded line-of-sight, clearance and exposure queries; see `editor/native_play.inc` |
| `set_play_debug` | `collision`, `sight` booleans |
| `undo`, `redo`, `refresh_preview` | Canonical history and staged cooking |
| `select_entities`, `group_entities` | `ids`; group also takes `name` |
| `transform_selection` | `ids`, optional `translation`, `yaw` degrees, positive uniform `scale`, `pivot`, `snap` metres |
| `drop_selection`, `duplicate_entities`, `delete_entities` | `ids`; duplicate also accepts `translation` |
| `add_blockout` | Optional `id`, `shape`, `position`, `size`, `steps` |
| `add_game_object` | `kind`, optional `id`, `position`, `size`, `targets`, `value`, `required_loot`, `zone` |
| `set_game_object` | `id`, `patch` of typed interaction fields |
| `save_prefab` | `ids`, `name` |
| `place_prefab` | `name`, optional `position` |
| `get_layout`, `set_panel`, `reset_layout` | Workspace visibility, docking and actual viewport selection; see workspace guide |

Authoring mutations are rejected during Play. Existing `inspect`, build, save,
capture and level-management commands remain available with their existing
guards. `inspect_scene` joins render entities to canonical IDs; `inspect_play`
exposes the actual simulation state rather than inferring it from the viewport.

## Verification and limits

Build the editor, then run `python tools/verify_native_play.py`. Its private
project checks shared simulation, replay, unsaved snapshot preservation,
blockout/group transforms, undo/redo, prefab link remapping and stale-tick
rejection. `--editor` can select a built diagnostic editor executable.

`python tools/verify_workspace.py` checks workspace defaults, selection and
preview-transform synchronization, panel persistence and Play transitions in a
private project. Results live under `.dev/` and do not alter saved source levels.

`python tools/verify_replay_parity.py` compares a native `play_runner` replay with
the same input executed by a MIPS ROM in Ares, using a 0.002 numeric tolerance.
`python tools/verify_stream_replay_parity.py` runs the fixed streamed doorway,
loot, retirement/reload and restart route with the same tolerance. Its explicit
`stream_boundary: "settled"` completes streaming before each input; declared
functional fields compare every tick, while IO speed, slot placement and
graphics counters remain separate console acceptance checks.
Recorded comparisons establish parity for exercised scenarios, not all possible
inputs or original hardware. Renderer, audio, timing and platform behavior still
need console validation. The desktop uses LightEngine rendering; it does not
reproduce the N64 graphics pipeline. Current navigation still follows straight
segments without pathfinding or crowd avoidance. Cross-platform floating point
is checked within tolerance rather than assumed bit-identical.
