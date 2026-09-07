# Project Editor

For a first editing session, follow the [creator quickstart](../docs/creator-guide.md). This page is the more detailed editor reference.

Run **DarkLantern64: Launch editor** in VS Code, or `python tools/build.py --editor --run`. One project session manages all canonical levels and remembers the last opened level. Its queue, logs, and layout live in `.dev/editor/project/`. The desktop C++23 application uses the pinned LightEngine checkout in `external/LightEngine`.

**Levels** and **File > Open** list every discovered version-2 level directly inside `content/`. **New level** creates a playable starter room with a switch, door, objective, and spawn; **Duplicate** copies a saved level. Open a level to edit its title, objects, patrols, materials, and lighting. **Include in game menu** controls membership in `content/level_bundle.json`; the browser also lists levels outside that bundle.

**Delete** asks for confirmation, removes the source and menu entry, and retains a recovery copy under `.dev/editor/deleted-levels/`. Shared assets remain available. Keep at least one project level and one bundled level. Switching or closing with unsaved changes offers **Save and continue**, **Discard and continue**, and **Cancel**, including audio planning changes.

**Save + Cook**, **File > Save scene**, and **Ctrl+S** save canonical content. **Play level** saves and launches the current level at the selected **Test start**. **Play game menu** saves the current level and builds the saved bundle; **Build ROM only** saves and builds without launching. The separate **Launch game** task builds the saved bundle without requiring the editor.

Generated previews use real level names and distinct asset paths under `build/project/editor-assets/`. Texture paths include pixel hashes so edited images refresh. Runtime outputs retain `build/` for Lantern Store and `build/scenes/<stem>/` for other levels. Shared source models and images stay under `content/`.

`MODULE.bazel` and the pinned engine revision define dependencies. The generated `MODULE.bazel.lock` stays local because LightEngine's module extension caches absolute Vulkan SDK and Bazel checkout paths.

For isolated developer checks, the executable also accepts an absolute source path after its repository argument. Those sessions retain the old per-level queues: `.dev/editor/` for first_room, `.dev/editor/scenes/<stem>/` otherwise. Everyday editing uses the project session. Do not edit one source from two sessions.

## 3D content

**Room Objects** and **Object Properties** edit the selected canonical version-2 level. The project restores the last opened level on launch. Models are freely positioned in XYZ metres, with Euler XYZ rotation in degrees and positive XYZ scale. The Y axis points up. There is no authoritative tile grid or map brush.

The **Viewport** displays meshes cooked from the same OBJ source geometry used by the N64 content header. Use LightEngine's **Entity List** tab to select a viewport gizmo. Transform changes from the viewport or **Selected Entity** are copied back to the matching canonical object by its stable identity; Object Properties transform changes update the preview immediately. Stock structural, name, material and lighting edits remain preview-only. Use the project properties for gameplay tuning, and canonical source assets for geometry/material changes.

## Enemies and patrol routes

Use the project-owned **Room Objects** buttons to **Add enemy**, **Duplicate enemy**, **Add waypoint**, or delete a selected enemy/unused waypoint. These operations edit canonical content. New objects appear in the list immediately; **Save + Cook** adds their models and waypoint markers to the viewport. The panel shows when a viewport refresh is pending. The stock LightEngine Add/Duplicate/Delete commands remain preview-only.

**Add enemy** starts a sentry at the selected enemy, waypoint, or player-spawn position; otherwise it uses an existing enemy or player spawn. Set its XYZ position in **Object Properties** before playing. It reuses an existing guard's model/material/scale and clears numeric overrides so the selected code-defined type supplies the tuning. If there are no guards, the level must retain the `mesh-guard` and `mat-guard` assets. **Duplicate enemy** copies tuning and clones each distinct route waypoint once, preserving route order and repeated references. The initial copy overlaps its source until moved. Moving its spawn later does not move its route points; the command API can translate both during duplication.

Choose **Enemy type** in Object Properties. Watchman and Scout currently share the placeholder 3D model, with separate movement, sight and hearing defaults defined in `src/enemy_types.def`. Changing type preserves explicit per-instance overrides. Each tuning control offers **Use type default**, which removes the override. Adding a type requires a code/catalog update and editor restart to load its fresh catalog.

Choose **Sentry** to face the authored Y rotation and return to that post after investigating. Choose **Patrol** to loop an ordered route. Both can hear, investigate, search and chase. To build a route:

1. Select an enemy and leave it in Sentry while placing points.
2. Use **Create + append waypoint** or **Append existing waypoint**. New points start at the last route point, or at the enemy when the route is empty.
3. Select the point's name in the route list and edit XYZ. After Save + Cook, use its Entity List gizmo in the viewport.
4. Select the enemy again. Use **Up**, **Down**, and **Remove from route** to order the points. Enable Patrol after at least two points exist.

Removing enough points to leave fewer than two automatically switches the enemy to Sentry. Route removal keeps the waypoint object; deleting a waypoint still referenced by any enemy is refused. Existing waypoints may be shared: moving one affects every route that uses it. Deleting an enemy leaves its waypoints available for reuse or explicit deletion.

The current source/runtime capacity is 16 enemies and 32 points per route. This is an authoring bound, not a performance target. Patrol follows straight segments, without a navigation mesh or crowd avoidance; keep routes clear of walls and check encounters in the game. Sentries may retain 0–32 dormant route points while a designer prepares a patrol.

**Save + Cook** validates the staged document against assets in `content/`, saves canonical content, and reloads the preview. Invalid content preserves the last valid source and generated artifacts. The current collision cook accepts upright boxes following translation, yaw and scale; tilted models need separate upright collision proxies. **Build ROM only** also invokes the root build. Desktop authoring checks do not establish that the evolving 3D runtime is playable on hardware.

## Audio Memory

This panel is a **planning tool**, not a measurement of the running game. It reads `build/generated/audio_memory_report.json`, produced from `content/audio_budget.json`. The example assets and encounter scenarios are hypothetical. The initial 256 KiB envelope is illustrative and has not been approved as a project allocation.

Choose a context or transition to inspect its RAM components, active channels, retained pools, overlap, allowances and unknown costs. ROM payload and container storage appear separately. Positive allowance remaining does not establish a verified fit.

**Adjust planning assumptions** exposes output rate, frames and count, retained pool provisioning, the optional RAM envelope, runtime/allocator allowances and reserve. With fixed frame counts, output rate changes buffer duration rather than byte allocation. Apply + Recalculate validates and saves the plan; Discard draft restores saved assumptions. Recalculate saved plan is disabled while a draft is unapplied. These actions do not alter the ROM or allocate game audio memory. Asset, voice, effect and scenario definitions are edited in the JSON manifest.

## Local command interface

`tools/editorctl.py` sends bounded JSON requests through `.dev/editor/project/requests` and waits for atomic responses. Use `--args-file` to avoid shell quoting. IDs and expiry times bound requests; inspect a timed-out response before retrying. No operation evaluates scripts or arbitrary commands.

The default queue stays fixed across level switches. Use `list_levels`, then `open_level` with `{"file":"moonlit_courtyard.json"}`. `inspect` reports source, preview path, catalog, dirty state, pending action, and canonical document. `--root` selects a project; `--level` addresses an isolated diagnostic session only.

`create_level` takes `name`, `title`, and optional `in_bundle`; `duplicate_level` also takes saved source `file`. `update_level` takes `file` with `title` and/or `in_bundle`. `delete_level` requires `file` and `confirmed:true`. Command switching refuses unsaved edits; save/apply them first. `play_level` accepts optional `start_preset`; `play_menu` opens the bundled selector. Both save current content.

Content operations: `import_asset_pack`, `add_prop`, `set_entity`, `add_enemy`, `duplicate_enemy`, `add_waypoint`, `delete_entity`, `set_environment`, `set_material`, `set_preview_transform`, `save`, `build`, `capture`, `get_layout`, `reset_layout`, `reload`, `get_audio_memory`, `set_audio_config`, `recalculate_audio`, and `quit`.

`inspect` also includes code-owned `enemy_types` and `preview_refresh_pending`. `report.enemy_instances` describes the last cook; recook after edits before treating it as current.

Enemy mutation arguments (all coordinates are XYZ metres):

```json
{"id":"yard-watch","enemy_type":"watchman","position":[3,0,-4]}
```

Pass this to `add_enemy`. All fields are optional; `id` is generated when omitted, `enemy_type` defaults to `watchman`, and only `behavior:"sentry"` is accepted at creation. Optional `template_id` chooses the existing guard whose visual model/material/transform is reused. `position` defaults as described above. The response contains `entity`, `dirty`, and `preview_refresh_pending`.

```json
{"id":"store-guard","new_id":"second-watch","position":[4,0,-4]}
```

Pass this to `duplicate_enemy`. `id` is required. `new_id` is generated when omitted; `position` defaults to the source spawn. Supplying a new position translates every cloned waypoint by the same offset. The response also contains `created_waypoints`. No waypoint is shared with the original route.

```json
{"id":"yard-corner","position":[6.5,0,-3]}
```

Pass this to `add_waypoint`. Both fields are optional, using a generated ID and the same placement fallback as Add enemy. It creates an unlinked waypoint; append its returned ID with `set_entity`.

```json
{"id":"yard-watch","patch":{"enemy_type":"scout","behavior":"patrol","patrol":["yard-corner","patrol-ne"],"speed":null}}
```

This `set_entity` patch atomically selects a type, assigns an ordered route, enables patrol, and removes the speed override. `speed`, `sight_range`, and `hearing_range` accept `null` to restore type defaults. Unknown types, broken waypoint references, and fewer than two points with Patrol are refused without modifying the document. An intermediate one-point route is valid for Sentry.

`delete_entity` accepts `{"id":"yard-watch"}`. It supports enemies and unused waypoints only. Referenced waypoints must first be removed from every route. Stable IDs use an ASCII letter followed by up to 63 ASCII letters, digits, `_`, or `-`, and must be unique across assets, materials and entities. Collision, support, and aggregate geometry budgets are checked by Save + Cook; invalid staged content preserves the saved source.

Example `set_entity` arguments:

```json
{"id":"tilted-rafter","patch":{"transform":{"position":[-4,3.3,-2],"rotation":[12,25,8],"scale":[6,0.28,0.3]}}}
```

Existing supported properties may be patched, and enemy type/behavior/route/tuning fields may be added. Scale belongs inside `transform` as an XYZ array.

Example `set_audio_config` arguments:

```json
{"scenario_id":"door-crossfade","patch":{"budget":{"ram_bytes":262144},"output":{"buffer_count":3}}}
```

The audio patch accepts numeric fields in `output`, `budget`, `buffer_pool`, and `allowances`; `{"budget":null}` clears the optional envelope. An unapplied UI draft blocks config patches. Apply also refuses to overwrite an externally changed manifest: discard the draft and recalculate the saved plan before editing again. `set_preview_transform` takes an object ID and a `transform` patch, exercising the same canonical synchronization used by viewport gizmos. `capture` produces a viewport PNG, not a screenshot of editor panels. `quit` refuses unsaved scene changes, unapplied audio changes or an active build.

## Layout and verification

First launch creates a deterministic layout including Levels and Audio Memory. Deliberate layouts persist in `.dev/editor/project/layout-project-v1.ini`; Reset Layout restores defaults. Earlier layouts remain separate.

Compiler tests cover version-2 validation, enemy type defaults, multiple actors and route references, collision placement, asset boundaries, dependency hashing, staged asset resolution, incremental cooking and XYZ changes reaching both targets. Run `python tools/verify_enemy_editor.py` after building the scene editor to create a private three-enemy test fixture, exercise the real file-command interface, then archive its JSON under the session queue’s evidence/ directory and remove the owned source, including on failure. The optional `--level content/<unused-name>.json` chooses a new filename; the verifier refuses to overwrite existing content and closes only its own editor process. Earlier live checks also cover transform synchronization, audio assumptions and preservation of invalid or externally changed plans. Restart checks verify both project tabs and deliberately saved stock tabs; Reset restores the project tabs through the next restart. These checks are distinct from native N64/M64 runtime validation.

Run `python tools/verify_project_editor.py` after building to exercise level management, unsaved edits, source conflicts, and previews in a private copied project. See the [developer tools](../docs/developer-tools.md) for specialist checks.
