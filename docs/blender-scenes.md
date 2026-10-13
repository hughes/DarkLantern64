# Edit Sponza in Blender and update the level

Open `art/sponza-edit.blend`. It is a checkout of the actual saved Sponza level:
217 static objects, 65 shared meshes, editable box proxies, and gameplay reference
markers. The original `art/sponza.blend` and its geometry-only exporter remain
available. This workflow supports existing OBJ models and new instances of those
models; new model definitions, material/texture changes and animated characters
continue through the asset workflows.

## The everyday loop

1. Edit meshes in **Edit Mode**, or move/rotate/scale architecture in **Object
   Mode**. Mesh data is shared between repeated instances, so editing one column
   changes every instance of that model. Object names are free to change.
2. In the 3D Viewport sidebar (**N**), open **DarkLantern** and click **Export
   level changes**. Save the `.blend` after exporting so its export revision is
   retained. Export stages the current in-memory meshes; it does not change the
   game level.
3. In DarkLantern, open **Sponza After Hours**, then **Assets → Asset packs /
   props → Import Blender changes**. With one exported checkout, the path is
   automatic. If there are several, enter the desired project-relative
   `build/blender/<level>/<scene-id>/scene.json` in **Blender export path**.
4. Import validates and saves the level, including the current editor draft,
   then refreshes the viewport. Use native **Play** or **Play level** to inspect
   the result. Runtime lighting and collision are checked in the game.

The add-on package is produced by `python tools/blender_assets.py --package-addon` at
`build/darklantern64_export.zip`. Install it through Blender's Preferences →
Add-ons → Install from Disk and enable **DarkLantern64 Asset Pack**. Its scene
panel appears for level checkouts. No embedded script auto-execution is needed.

## What re-import preserves

Blender exports static geometry, instance transforms, existing model/material
references and box proxies. Guards, patrols, lights, doors, controls, objectives,
music, test starts, environment settings and other level data stay in DarkLantern.
Gameplay objects appear as unselectable reference markers in Blender. Moving
those markers through scripts does not change game content.

Object IDs are stored separately from names. Renaming a mesh object preserves
its identity. After duplicating an instance, select **only the duplicate
hierarchy**, including its copied collision children, and click **New instance
ID**. Duplicate IDs stop export. Keep copies linked to the
same mesh: different geometry under one model ID is rejected. If the original
has a collision child, duplicate that child along with the parent, too. The
button updates their relationships together. Generated staircase duplication
is not supported yet; edit the existing east and west assemblies instead.

On each import the tool compares the last imported fields with both the current
level and Blender's new fields:

| Change | Result |
| --- | --- |
| Only Blender changed a field | Update it. |
| Only the editor changed a field | Preserve it. |
| Both changed it to the same value | Accept it. |
| Both changed it differently | Stop the whole import and name the conflicting object/field. |
| Object removed in Blender | Retain it in the level and list it under **Removed in Blender; retained in level**. |
| Step count reduced with **Apply staircase and collision** | Retire only the generated surplus step boxes; reject removal if the level has edited or referenced them. |
| Object deleted in the editor, unchanged in Blender | Keep it deleted. |
| Editor deleted an object that Blender also changed | Stop for reconciliation. |
| Older export or conflicting copy of the same checkout | Reject it. |

Transforms are compared as one field. Resolve a conflict by making the editor
and Blender agree, or by making a fresh checkout with a new filename. There is
no force-overwrite switch. To remove an object from both tools, remove it in
Blender and explicitly delete it in the level editor as well.

Mesh files are shared resources: edits affect every level using that model.
Concurrent project-side changes to a mesh are detected by source hashes. An
unchanged Blender mesh never overwrites newer project geometry. Every authored
level is validated against a private content snapshot before publication.

## Collision and modeling limits

Geometry errors identify the object, face, triangulated face piece, vertex IDs
and measured area. These indices start at zero. The **DarkLantern** sidebar
shows **Select problem face** and **Select triangle vertices** after an error;
either selects and frames the relevant geometry in Edit Mode. Return to Object
Mode before exporting again. Selection is unavailable for evaluated modifier
geometry or when the source topology has changed since the error; export again
for current diagnostics. Validation remains strict: the exporter never removes
collapsed triangles or automatically repairs a polygon with collinear vertices.

Sponza's collision objects are nested under their visual objects in the Outliner,
in the same collection. Expand a visual to find its **Collision** children and
their **Box** proxies. Moving, rotating or scaling the visual carries its boxes
with it. Select a visual, then use these controls in the **DarkLantern** sidebar:

- **Show related** overlays its collision wires on the visible geometry and hides
  unrelated boxes. For a staircase, this includes the shell's boxes too.
- **Select collision** selects those boxes; **Select visual** returns to their
  visual parent. A proxy's position and scale set its centre and half size.
- **Fit collision to mesh bounds** explicitly fits a single linked box to the
  evaluated visual bounds. Use this after editing a solid wall or parapet in
  Edit Mode. It leaves the visual mesh intact. It requires an upright object and
  a mesh with thickness; a box cannot describe a stairwell or other concave shape.
- **Mark collision reviewed** acknowledges a deliberately different manual box
  after checking the overlay. Vertex or collision edits otherwise leave a review
  warning in the sidebar and on export. Export reports these warnings without
  automatically changing either mesh.
- **Link selected collision to active visual** establishes an explicit
  relationship for existing collision objects while preserving world positions.

For a rectangular object, **Rectangular prism** provides local Blender **Size
XYZ** and **Centre XYZ**. **Apply prism and collision** replaces the visual with
a six-sided prism and fits its box from the same dimensions. This deliberately
replaces custom shape and UV edits; it preserves the material. The four parapets
in `art/sponza-edit.blend` use this mode. Free-form edits remain available, followed
by an explicit bounds fit or manual collision review.

Select **sponza-stairs-east** or **sponza-stairs-west** (or their nested shells)
to edit **Staircase dimensions**. Steps per flight, total rise, tread depth,
flight width, landing depth, centre gap and upper headroom describe the whole
two-flight staircase. **Apply staircase and collision** rebuilds the treads,
shell and all associated boxes together. The displayed step rise must stay at or
below the controller's 0.280 m limit. Rebuild replaces custom surface edits and
UVs, preserves materials and object IDs, and supports Blender Undo. Check the
staircase's connections to the surrounding level after changing its dimensions.

Only this explicit staircase rebuild can retire surplus generated boxes during
import. Independently edited boxes or references stop that removal. Restoring
the step count reuses the same IDs. Deleting ordinary objects still follows the
retain-on-import rule above. Relationships and applied shape parameters are
saved in the level and restored by a fresh checkout.

The game currently supports upright boxes with yaw rotation. A tilted collision
box, a sheared transform, a mirrored instance, duplicate IDs, incompatible
shared-mesh variants, and unsupported animation/constraints stop export or
import. Keep metres and Unit Scale 1. New geometry still has to fit the cooker's
vertex, triangle and instance limits. Preserved patrol waypoints may need
deliberate design adjustments after changing a route's geometry.

## Saved checkpoints and recovery

Import is a saved checkpoint, including shared OBJ files. It resets the editor's
document undo history because ordinary document undo cannot restore those files.
Changed source files are backed up byte-for-byte under
`.dev/editor/blender-backups/<checkpoint>/`, preserving their content-relative
paths. Restore a checkpoint's files together with the editor closed, then reopen
and cook the level. The original `.blend` is never changed by the importer.

Exports use immutable, hash-named OBJ payloads and publish `scene.json` last.
Import uses the existing project lock, checks for concurrent file changes,
validates all levels, and rolls back file replacements on an ordinary write
failure. Previous exports and backup files are retained; no automatic cleanup
deletes artist assets. The file transaction does not promise crash recovery
across an OS/power failure during publication.

## Commands and verification

Create another checkout from a saved level (an existing `.blend` is never
overwritten):

```powershell
python tools/blender_scene.py checkout --level sponza_courtyard.json --output art/sponza-next.blend
```

Validate a staged export without writing content, or import it while the editor
is closed:

```powershell
python tools/blender_scene.py import --exchange build/blender/sponza_courtyard/SCENE_ID/scene.json --check
python tools/blender_scene.py import --exchange build/blender/sponza_courtyard/SCENE_ID/scene.json
```

The same add-on export also runs in background Blender:

```powershell
python tools/blender_scene.py export --source art/sponza-edit.blend
```

This stages the saved scene and saves its new export revision back into the
`.blend`. It returns the exchange path for the import command. Embedded script
auto-execution stays disabled; the bundled exporter performs the operation.

For a running editor, use its **Import Blender changes** button or
`python tools/editorctl.py import_blender_scene`. This includes unsaved gameplay
edits and prevents an old editor draft from overwriting the imported result.

`python tools/verify_blender_scene.py` creates a disposable project, checks out
Sponza in background Blender, changes a real mesh, placement and box, imports
through the built editor, and verifies gameplay preservation, cooked mesh
changes, no-op re-import, conflicts, stale exports, invalid actor collision,
retained removals and duplicate-ID rejection. Results go to
`build/blender-scene-verification.json`. Unit merge tests run with
`python -m unittest tests.test_blender_scene`.

`python tools/verify_collision_authoring.py` checks hierarchy preservation,
overlays, fitting, geometry review, prism generation, east/west stair dimensions,
retired-step conflicts, stable recreation, unchanged gameplay and checkout
restoration in a disposable project. Results go to
`build/collision-authoring-verification.json`.

## Large imported source studies

A level with `"authoring_profile": "external-scale-study"` is a host authoring
document. Project discovery, editor preview cooking and the Blender exchange
support its larger architectural scale. Ordinary levels keep the existing
runtime limits. A staged Blender export cannot grant itself the study profile;
the importer checks the saved level's profile before accepting a larger exchange.

Study ceilings are 65,536 entities, 8,192 rendered instances and mesh definitions,
512 materials, 4,096 lights, 32,768 collision boxes, 128 enemy placements, 262,144
instanced vertices and 131,072 instanced triangles. Each mesh still permits at
most 4,096 vertices. Split imported geometry into material groups and mesh
chunks before checkout. Provenance stored in an entity or material `source`
object appears in Blender as its `dl64_source` JSON custom property and remains
owned by the source document during scene reimport.

Study cooking emits editor geometry, textures and reports, with a blocked play
descriptor. It emits no ROM level header or static lighting bake. Native Play
and ROM validation reject source studies, and they cannot be added to the game
bundle through project tools. Supporting surfaces for actor placements are not
validated in a study because imported slopes and approximated boxes are not an
authoritative physics representation. Inspect these placements as source data;
use a separate bounded runtime level to test navigation and gameplay. The
larger preview budgets do not implement runtime streaming or enlarge N64 memory.

Direct Python callers must opt into the host workflow explicitly with
`validate(document, asset_root, authoring=True)` or
`compile_level(source, output, asset_root, authoring=True)`. Without that keyword,
marked studies are rejected before artifact publication.
The compiler CLI exposes the same choice as `--authoring-preview`.
