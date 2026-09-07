# Creator quickstart

For artists and level/game designers with the Windows environment already set up. One project editor manages all levels, including **The Lantern Store**, **Moonlit Delivery Yard**, **Enemy Patrol Workshop**, and **Guard Animation Workshop**.

## 1. Open the editor

Open the **DarkLantern64 repository folder** in VS Code. Choose **Terminal → Run Task → DarkLantern64: Launch editor** and wait for the editor. Use its **Levels** panel to select a level and click **Open**. The panel shows the title and source filename; the preview also uses the actual level name. Keep one project editor open and switch levels inside it. Escape or the window close button asks how to handle any unsaved changes.

There are two everyday tasks:

| Task | Use it to… |
| --- | --- |
| **Launch editor** | Build and open the project editor, then choose a level. |
| **Launch game** | Build saved levels included in the game menu and open that menu in Ares; also available with **Ctrl+Shift+B**. |

PowerShell alternatives, from the repository folder: `python tools/build.py --editor --run` opens the editor; `python tools/build.py --bundle content/level_bundle.json --run` builds and launches the game menu. Tests, captures and other specialist commands live in the [developer tools guide](developer-tools.md).

### Manage levels

The **Levels** panel lists source levels in `content/`, including levels that are not yet included in the game menu. **New level** creates a bounded starter room with a player spawn, switch, door and objective, built from the shared block model. **Duplicate** creates an independent copy of the selected saved level. Enter a unique **Source name** and **Title**, then click **Create**; the new level opens for editing. Shared meshes and textures remain shared.

Edit the open level's **Level title** and use **Save + Cook** to save it. **Include in game menu** controls whether it is bundled by **Launch game** and **Play game menu**. You can still use **Play level** while a level is excluded from the menu. New and duplicated levels default to inclusion. A game bundle currently holds up to eight levels; if it is full, uncheck **Include in game menu** in the creation dialog to continue creating the level.

When you switch with unsaved edits, choose **Save and continue**, **Discard and continue**, or **Cancel**. Unsaved audio planning edits are protected too. **Delete** asks for confirmation, removes the level from the project and game menu, and archives its saved source under `.dev/editor/deleted-levels/`. Shared models and textures remain in place. The editor prevents deleting the last project level, or deleting or excluding the last level in the game menu. Keep committed source files as the durable history; deletion archives are local recovery files.

## 2. Make a level edit

Open **The Lantern Store**, then start with a small experiment:

1. Select **rotated-crate** in **Room Objects**.
2. In **Object Properties**, change the middle **rotation** value from `28` to `40`. This turns the crate around its vertical axis.
3. Click **Save + Cook** in **Build & Diagnostics**. Cooking converts your content for the preview and game.
4. Wait for the saved/refreshed confirmation, then play the change using the next section.

In **Viewport**, **middle-drag** orbits, **Shift + middle-drag** pans, and the **wheel** zooms. Left-click a mesh or choose it in **Entity List** to select its gizmo. Choose **Options → Transform mode → Translate / Rotate / Scale**; **Ctrl+T** cycles modes. Gizmo edits save with the level. **Room Objects** selection controls only the property inspector.

Positions use metres; **Y is up**. Rotations use degrees; scale values must stay positive. Collision boxes currently support Y rotation only: tilting an object with collision around X or Z fails cooking.

Lights expose radius, intensity (0–16), and color; the control exposes **Linked door**. In the courtyard, **Night environment** controls ambient, moon, sky, fog and exposure. **Material** controls color, emission and existing texture dimensions; changes affect every object using that material. Assign a new source image through the saved JSON or API as described in the [texture guide](texture-pipeline.md). Save + Cook refreshes these changes. Check final lighting in Ares; the desktop preview displays cooked textures but uses LightEngine's renderer.

Use **Save + Cook**, **File → Save scene**, or **Ctrl+S** to save canonical level content. Stock **Add/Import/Duplicate/Delete**, material tools, and native **Play** mode affect the desktop preview; use the project panels for saved game authoring. Check game lighting and collision in Ares.

### Place enemies and author patrols

1. In **Room Objects**, click **Add enemy**. Choose **Enemy type** in Object Properties: **Watchman** or **Scout**. Both use the level's guard asset; the Scout has faster movement and longer perception ranges. Guard Animation Workshop supplies the animated model, while the earlier levels retain their blockout guard.
2. Edit **position** to place its feet on a floor. New enemies start at an existing actor or waypoint, so move them apart. **Save + Cook** creates the viewport model; select it in **Entity List** to use the normal transform gizmo.
3. Leave **Behavior** on **Sentry** for a stationary lookout. Set the middle **rotation** value to choose its facing: `0` faces +Z, `90` faces +X. It can investigate and chase, then returns to its post.
4. For a patrol, click **Create + append waypoint** in the enemy's properties. Click the route entry to edit that waypoint's XYZ position. Reselect the enemy and repeat. With at least two points, choose **Patrol**. The ordered route loops, including the segment from the last point back to the first.
5. Use **Up**, **Down**, **Remove from route**, or **Append existing waypoint** to edit the route. Save + Cook shows new waypoint markers in the viewport. Walk every segment in Ares; the current AI follows straight segments and does not find detours around walls.

**Duplicate enemy** copies its settings and makes independent copies of its route points. Move the new enemy and its route to the intended location. **Delete enemy** removes the instance; route points remain available for reuse. **Delete waypoint** rejects points that any enemy still references. Shared waypoints move for every route that uses them.

Speed, sight and hearing normally inherit the selected code-defined type. Editing a value makes an instance override; **Use type default** restores inheritance. Existing levels retain their previous explicit tuning. A level may contain zero through 16 enemies, subject to geometry and frame-time budgets. See the [enemy authoring reference](enemy-authoring.md) for the example workshop and current limits.

## 3. Build, play, repeat

Use **Build & Diagnostics** for the fastest loop:

- **Play level** saves and cooks the open level, then builds and launches it directly. Choose its default spawn or a named start in **Test start** before playing.
- **Play game menu** saves and cooks the open level, then builds the saved levels included in the game menu and launches the selector.
- **Save + Cook** saves and refreshes content without launching a game. **Build ROM only** saves, cooks and builds the open level without launching Ares.

The **Launch game** VS Code task also opens the selector, but reads saved files directly. **Save + Cook first** when launching from VS Code. Close the previous Ares game window before launching another, and let each build finish before starting another.

In the game menu, use arrows or the stick to choose a level; left/right chooses a test start where available. Press **A/Start** to launch. During play, **Z + Start** returns to the selector and **B** resumes the paused run. On the keyboard these are **Z + Tab** and **N**. See the [level-select guide](level-select.md) for the menu and authored test starts.

Ares launches with 8 MiB enabled and these keyboard controls:

| Action | Keys |
| --- | --- |
| Move / strafe | **W/S** / **A/D** |
| Turn / look up and down | **Left/Right** / **Up/Down** arrows |
| Crouch / jump | **Z** / **Space** |
| Interact / make a noise | **E** / **N** |
| Restart / debug overlay | **R** / **Tab** |

Click Ares for keyboard focus. Find the switch, open the gate, and take the relic. **R** restarts the loaded level; build and launch again after edits.

On an N64 controller, the **stick looks in both axes** (up looks up). **C-Up/Down move forward/backward**, and **C-Left/Right strafe**. **Z** crouches, **L** jumps, **A** interacts, **B** makes noise, **R** restarts, and **Start** toggles debug.

## 4. Add a prop or bring in a model

For Blender-made props, use the [Blender asset workflow](blender-assets.md): edit and save the `.blend`, export an asset pack, import it into the level editor, and place reusable prefab instances. The supplied loot pack shares one small atlas across five modeled props. Enemies and waypoints have the controls described above.

For animated characters, start with [the guard animation workflow](guard-animation-prototype.md). Open **Guard Animation Workshop**, select a guard in **Room Objects**, and use **Character animation → Focus guard**. Choose a clip, play or scrub it, experiment with **Blend preview**, and adjust **Attention yaw/pitch**. These controls preview motion without changing saved behavior. The current game uses idle and walking; its CPU animation renderer is still a slow prototype.

Edit and save [art/guard.blend](../art/guard.blend), then run `python tools/guard_assets.py` from the repository root. Return to the editor and **Save + Cook**, then **Play level**. The exporter reads the saved Blender file, including its five named Actions and atlas; unsaved Blender edits are not exported.

For manual source editing, use the source shown in **Levels**; [content/first_room.json](../content/first_room.json) is The Lantern Store example. **Save and close the editor first.**

Duplicate `rotated-crate` in the `entities` array, name the copy `crate-two`, and set its position to `[-5, 0.65, -2]`. Every ID must be unique. Preserve existing gameplay IDs and their links; this prototype requires exactly one player start, door, control and objective, and allows zero through 16 enemies.

For your own static prop, export a triangulated, Y-up OBJ in metres to `content/models/`. Add `{"id":"mesh-my-prop","uri":"models/my-prop.obj"}` to `assets`, then use `mesh-my-prop` in a static object's `model` field. Textured models need OBJ UV coordinates on every face. Add source images under `content/textures/` and reference them in the material's **texture** recipe, then Save + Cook; see the [texture guide](texture-pipeline.md). Animated characters use the separate typed asset described in the guard workflow. Adjust the prop's box collider (`half_size` means half width/height/depth before scaling), or omit collision for decoration.

Save the JSON, run `python tools/compile_level.py content/first_room.json --validate-only` (substitute your source), then **Launch editor** and open the level to inspect it. Author in `content/`; generated previews in `build/` and `.dev/` are refreshed from source.

## When something goes wrong

- **Missing or misplaced panels:** click **Reset Layout** in Build & Diagnostics.
- **Cook/build error:** expand **Last compiler log** or **Last ROM build log**, or read the task terminal. Fix the named object/file and retry. Failed builds can leave an older ROM on disk.
- **Game looks unchanged:** check the active level, close the previous Ares window, then use **Play level**. If launching from VS Code, confirm **Save + Cook** succeeded first.
- **Audio planning:** **Audio Memory → Apply + Recalculate** saves a separate memory plan; it does not change game audio. See the [audio planning guide](audio-memory-planning.md).

For detailed editor behavior and automation, see the [editor reference](../editor/README.md).
