# Creator quickstart

For artists and level/game designers with the Windows environment already set up. The default workshop edits **The Lantern Store**, our first playable level. A separate **Moonlit Delivery Yard** explores outdoor lighting and textured models.

## 1. Open the editor

Open the **DarkLantern64 repository folder** in VS Code. Choose **Terminal → Run Task → DarkLantern64: Open editor** and wait for Room Workshop. Keep one editor instance per level. Save before closing; **Escape currently exits the editor**.

All project tasks start with `DarkLantern64:`. Tasks without **courtyard** in their name target **The Lantern Store**, including the default **Ctrl+Shift+B** build:

| Task | Use it to… |
| --- | --- |
| **Open editor** | Build and launch the workshop. |
| **Save + Cook (editor must be open)** | Save the workshop's level edits and refresh generated content. |
| **Validate saved level** | Check the level and referenced models saved on disk. |
| **Build game** | Produce the ROM; also available with **Ctrl+Shift+B**. |
| **Build + Play game** | Build saved content and launch it in Ares. |
| **Open courtyard editor** | Edit the moonlit yard's objects, materials and lighting. |
| **Save + Cook courtyard (editor must be open)** | Save the courtyard workshop's changes. |
| **Validate saved courtyard** | Check the courtyard source, models and textures. |
| **Build + Play courtyard** | Build and play the saved courtyard. |
| **Capture courtyard views** | Export repeatable images from the actual N64 renderer. |

PowerShell alternatives, from the repository folder: `./build.ps1 -Editor -Run` opens the editor; `./build.ps1 -Run` builds and plays.

## 2. Make a level edit

Start with a small experiment:

1. Select **rotated-crate** in **Room Objects**.
2. In **Object Properties**, change the middle **rotation** value from `28` to `40`. This turns the crate around its vertical axis.
3. Click **Save + Cook** in **Build & Diagnostics**. Cooking converts your content for the preview and game.
4. Wait for the saved/refreshed confirmation, then play the change using the next section.

In **Viewport**, **middle-drag** orbits, **Shift + middle-drag** pans, and the **wheel** zooms. Left-click a mesh or choose it in **Entity List** to select its gizmo. Choose **Options → Transform mode → Translate / Rotate / Scale**; **Ctrl+T** cycles modes. Gizmo edits save with the level. **Room Objects** selection controls only the property inspector.

Positions use metres; **Y is up**. Rotations use degrees; scale values must stay positive. Collision boxes currently support Y rotation only: tilting an object with collision around X or Z fails cooking.

Lights expose radius, intensity (0–16), and color; the control exposes **Linked door**. In the courtyard, **Night environment** controls ambient, moon, sky, fog and exposure. **Material** controls color, emission and existing texture dimensions; changes affect every object using that material. Assign a new source image through the saved JSON or API as described in the [texture guide](texture-pipeline.md). Save + Cook refreshes these changes. Check final lighting in Ares; the desktop preview displays cooked textures but uses LightEngine's renderer.

Use **Save + Cook** for level work. Stock **Add/Import/Duplicate/Delete**, material tools, **File → Save scene**, and **Play** mode affect the desktop preview. Check game lighting and collision in Ares.

### Place enemies and author patrols

1. In **Room Objects**, click **Add enemy**. Choose **Enemy type** in Object Properties: **Watchman** or **Scout**. Both use the placeholder guard model; the Scout has faster movement and longer perception ranges.
2. Edit **position** to place its feet on a floor. New enemies start at an existing actor or waypoint, so move them apart. **Save + Cook** creates the viewport model; select it in **Entity List** to use the normal transform gizmo.
3. Leave **Behavior** on **Sentry** for a stationary lookout. Set the middle **rotation** value to choose its facing: `0` faces +Z, `90` faces +X. It can investigate and chase, then returns to its post.
4. For a patrol, click **Create + append waypoint** in the enemy's properties. Click the route entry to edit that waypoint's XYZ position. Reselect the enemy and repeat. With at least two points, choose **Patrol**. The ordered route loops, including the segment from the last point back to the first.
5. Use **Up**, **Down**, **Remove from route**, or **Append existing waypoint** to edit the route. Save + Cook shows new waypoint markers in the viewport. Walk every segment in Ares; the current AI follows straight segments and does not find detours around walls.

**Duplicate enemy** copies its settings and makes independent copies of its route points. Move the new enemy and its route to the intended location. **Delete enemy** removes the instance; route points remain available for reuse. **Delete waypoint** rejects points that any enemy still references. Shared waypoints move for every route that uses them.

Speed, sight and hearing normally inherit the selected code-defined type. Editing a value makes an instance override; **Use type default** restores inheritance. Existing levels retain their previous explicit tuning. A level may contain zero through 16 enemies, subject to geometry and frame-time budgets. See the [enemy authoring reference](enemy-authoring.md) for the example workshop and current limits.

## 3. Build, play, repeat

**Save + Cook first.** VS Code's build/play tasks read saved files; they cannot see unapplied editor changes.

To choose among all three example levels in one ROM, run **DarkLantern64: Build + Play level select**. In its menu, use arrows or the stick to choose a level; left/right chooses a test start where available. Press **A/Start** to launch. During play, **Z + Start** returns to the selector and **B** resumes the paused run. On the keyboard these are **Z + Tab** and **N**. **Build + Play test start** launches one of the enemy workshop's named testing states directly. See the [level-select guide](level-select.md) for bundling your own levels.

Close the previous Ares game window, then run **DarkLantern64: Build + Play game**. The helper builds `build/DarkLantern64.z64` and launches Ares with 8 MiB enabled and these keyboard controls:

| Action | Keys |
| --- | --- |
| Move / strafe | **W/S** / **A/D** |
| Turn / look up and down | **Left/Right** / **Up/Down** arrows |
| Crouch / jump | **Z** / **Space** |
| Interact / make a noise | **E** / **N** |
| Restart / debug overlay | **R** / **Tab** |

Click Ares for keyboard focus. Find the switch, open the gate, and take the relic. **R** restarts the loaded level; build and launch again after edits.

On an N64 controller, the **stick looks in both axes** (up looks up). **C-Up/Down move forward/backward**, and **C-Left/Right strafe**. **Z** crouches, **L** jumps, **A** interacts, **B** makes noise, **R** restarts, and **Start** toggles debug.

The editor's **Build ROM** button saves, cooks and builds without launching Ares. Let each build finish before starting another.

## 4. Add a prop or bring in a model

For Blender-made props, use the [Blender asset workflow](blender-assets.md): edit and save the `.blend`, export an asset pack, import it into the level editor, and place reusable prefab instances. The supplied loot pack shares one small atlas across five modeled props. Enemies and waypoints have the controls described above.

For manual source editing, use [content/first_room.json](../content/first_room.json). **Save and close the workshop first.**

Duplicate `rotated-crate` in the `entities` array, name the copy `crate-two`, and set its position to `[-5, 0.65, -2]`. Every ID must be unique. Preserve existing gameplay IDs and their links; this prototype requires exactly one player start, door, control and objective, and allows zero through 16 enemies.

For your own prop, export a triangulated, Y-up OBJ in metres to `content/models/`. Add `{"id":"mesh-my-prop","uri":"models/my-prop.obj"}` to `assets`, then use `mesh-my-prop` in a static object's `model` field. Textured models need OBJ UV coordinates on every face. Add source images under `content/textures/` and reference them in the material's **texture** recipe, then Save + Cook; see the [texture guide](texture-pipeline.md). Animation remains future work. Adjust its box collider (`half_size` means half width/height/depth before scaling), or omit collision for decoration.

Save the JSON, **Validate saved level**, then **Open editor** to inspect it. Author in `content/`; `build/` and `.dev/` are regenerated.

## When something goes wrong

- **Missing or misplaced panels:** click **Reset Layout** in Build & Diagnostics.
- **Cook/build error:** expand **Last compiler log** or **Last ROM build log**, or read the task terminal. Fix the named object/file and retry. Failed builds can leave an older ROM on disk.
- **Game looks unchanged:** confirm Save + Cook succeeded, then close Ares and Build + Play again.
- **Audio planning:** **Audio Memory → Apply + Recalculate** saves a separate memory plan; it does not change game audio. See the [audio planning guide](audio-memory-planning.md).

For detailed editor behavior and automation, see the [editor reference](../editor/README.md).
