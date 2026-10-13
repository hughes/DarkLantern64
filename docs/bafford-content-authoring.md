# Expanding Bafford content by hand

Start with the local **Bafford District** and make one doorway or room change at
a time. The scene editor can place geometry, edit existing region ownership,
change visible working sets and author movement gates. The viewport shows the
complete authoring scene; check the rebuilt ROM to see the actual streamed view.

## Open the right source

From the repository root:

```powershell
python tools/build.py --editor --level content/thief_bafford_district.json --run
```

Or open **File > Open level > Bafford District** in an existing project editor.
The paired inputs are:

| File | What to edit |
| --- | --- |
| `content/thief_bafford_district.json` | Placed objects, meshes/material references, transforms, lights, box proxies, exact floor triangles and test starts |
| `content/thief_bafford_district.mission.json` | Region homes/bounds, player activation areas, visible working sets and directed transition gates |
| `content/level_bundle.json` | Game menu entry; the district uses ID `bafford-district` and a `mission` reference |

The local district scene, partition and imported assets are ignored files;
`content/level_bundle.json` is tracked. This guide
contains no imported geometry. Generated headers, `.dlm` packages and cook
reports under `build/` are outputs.

For an independent experiment, save first, open **Window > Levels**, select the
district and choose **Duplicate**. Give the copy a **Source name** and **Title**;
keep **Include in game menu** checked if you want to run its ROM. Duplication
copies the paired partition and shares referenced mesh/texture assets. Make a
new asset copy before changing a shared mesh. Do not rerun
`derive_bafford_district.py --replace` over hand edits; derivation is an import
step and intentionally replaces the derived pair.

## Change a room or opening

1. Open **Window > Objects**, **Window > Inspector** and
   **Create > Blockout & groups...**. Search Objects by ID or kind; Ctrl+click
   selects several objects. Inspector `position`, `rotation` and `scale` change
   the active object. Use the **Blockout & Groups** selection controls to move,
   snap, rotate or scale the whole selection. Coordinates are metres and
   rotations are degrees.
2. For fresh blockout, set **Block size (m)** and use `box`, `wall`, `floor`,
   `platform`, `stairs` or `doorway`. Ordinary blockouts start at world origin;
   move them into position. `doorway` creates two posts and a lintel; it does
   not remove an existing wall. Its outer size must be at least 1.4 m wide and
   2.2 m high. Stairs default to eight steps, each with at most 0.28 m rise.
3. To open an existing wall, move/resize separate wall pieces, or replace the
   affected mesh with a deliberately split mesh authored in Blender/OBJ. The
   editor has no mesh knife, boolean cut or automatic doorway detection.
   Imported district objects can contain many faces; selecting one object is
   not selecting one original room face. Preserve UVs/normals when splitting.
4. Enable **View > Collision proxies**. Change the corresponding box proxies
   as well as the visible mesh. A newly visible hole can still contain an old
   blocking box. A moved wall mesh can leave its collision-only proxy behind.
   Blockouts create their own box proxies; existing collision-only objects can
   be moved, yawed and scaled through Inspector.
5. Open **Window > Diagnostics > Streaming** or click **Stream regions**.
   Select the new/reassigned objects and choose **Selection home**. This assigns
   the complete selection to one existing region. Every entity needs exactly
   one home, including lights, collision-only proxies and floor contacts.
   Newly created, duplicated and prefab-placed objects start unassigned.
6. In each affected region, use **Fit bounds to owned objects**, or edit
   **Minimum (m)** / **Maximum (m)** and click **Apply bounds**. Geometry bounds
   must contain transformed owned static geometry, colliders, floors and entity
   origins/markers.
   Moving an object does not change its home. Fitting geometry bounds does not
   change player activation bounds or visible working sets.

If a long mesh or collider crosses a useful region cut, split it into separately
owned objects, or keep its complete home region in every view that needs it.
Merely shrinking a region's bounds does not clip its mesh or collision.

## Choose which rooms remain visible around a portal

Visibility lives in the **mission manifest**, with
`"visibility": "authored-sets"`. Each region's `activation_bounds` describes
where the player can activate it; its `working_set` lists the region chunks that
must remain available there. The **Streaming** panel exposes these as **Player
activation bounds** and **Visible <region ID>** checkboxes. The region itself
always remains included.

For a doorway between rooms A and B:

1. Identify the activation area on each side. Inspect the opening from the
   whole area, including the corners, crouched views, the top of ramps and jump
   views. Check doors fully open and closed; include views through windows and
   through successive openings.
2. In A's region tree, check **Visible B** if any part of B can be seen from A's
   activation area. Include any further region seen beyond B as well. Repeat
   independently from B toward A; visible sets need not be symmetric.
3. Apply any numeric **Player activation bounds** changes. Keep activation areas
   coherent with the walkable layout. Geometry ownership bounds may overlap;
   they have a separate purpose.
4. Click **Validate draft**, then rebuild and check the ROM from both sides.
   Missing distant walls/roofs or a view that appears only when approaching a
   different doorway usually needs a broader stable `working_set`.

Transition prefetch considers movement direction and proximity, within a 5 m
approach window, and selects only one target. It cannot cover another room
already visible through a different opening. Put those visible rooms in the
stable working set. The district's courtyard/next-passage relationship is an
existing example described in [the district guide](thief-bafford-district.md).

Checking a room loads its whole region, including collision; this is not portal
polygon clipping or an automatically generated PVS. Imported cell/portal IDs
are provenance and do not update these visible sets after manual changes.
Keep the district on `authored-sets`; `door-distance` and `all-regions` are the
legacy two-region probe policies, not a way to make this district fully resident.

## Place the movement cut separately

Under **Streaming > Directed transition gates**, a gate has `from`, `to`,
**Door** and **Gate blocker bounds**. These bounds define a movement/loading
cut, separate from the visible opening and its working set.

- Use **New gate ID**, **New gate from**, **New gate to**, then **Add directed
  gate**. A new gate starts near world origin; immediately place its actual
  **Minimum** / **Maximum** and click **Apply bounds**.
- Cover the full traversable opening, body height and jump range. The gate must
  intersect both endpoint activation boxes. A narrow floor-level strip can
  leave a path over or around the unavailable-region blocker.
- Add a separate reverse gate for return travel. Use **Open gate** for an
  unobstructed passage, or choose an existing canonical door. A door's home must
  be one endpoint; use the same door for the two directions of that opening.
- Gate add/edit/delete regenerates authored **Prefetch neighbors** from outgoing
  destinations. In JSON, keep those neighbor lists equal to the outgoing gate
  destinations. Every region must be reachable from **Entry region**, with at
  most eight outgoing gates per region.

A gate can block crossing while its target is unavailable. It does not carve
the wall, create a floor or hide a room. Widening an aperture usually requires
all three edits: visible geometry/proxies, conservative working sets, and gate
bounds. Keep the existing single district door for initial experiments; the
two-state bake does not represent arbitrary combinations of many independently
opening shadow-casting doors.

## Keep support and sight geometry faithful

Use **Add floor triangle** for nonrectangular support or a ramp. In
**Inspector > Floor triangle**, edit local **Vertex 1/2/3**, then **Apply floor
vertices**. The object's transform applies to these points. This contact is
collision-only; provide a corresponding visible surface separately. A plane
supports only its triangular footprint and must remain nondegenerate with a
slope no steeper than approximately 45 degrees. Tile triangles along the real
footprint rather than filling a diagonal room or hole with a large box.

**Drop to surface** uses the highest supporting box top or exact triangle at
the selected object's footprint. Check both ramp ends and seams, standing and
crouched clearance, jump landings, and travel under overhangs in the ROM.

Retain ceilings, roofs and upper wall faces that are visible through openings
or contribute to occlusion. Actor height is not a valid geometry removal rule.
Runtime sight and light blocking use authored box proxies and exact floors;
visible triangles alone do not automatically become general wall collision.
Use separate upright yawed boxes where appropriate, keeping their real opening
and footprint. A room-sized filled proxy can incorrectly block sight, light and
movement through its interior.

Night ROM cooking automatically produces DLR1 v4 closed/open colors using the
complete mission's colliders/floors and global lights. Structural or light edits
require a fresh cook. The district already uses the 16 global light slots;
adjust/reuse an existing light before adding another.

## Add a new region when existing ones stop fitting

Existing region ownership and policy are editable in the GUI. Creating,
renaming, duplicating or deleting a **region** still requires manifest editing.
Viewport bounds handles are unavailable; use the Streaming panel's numeric
controls. **Levels > Duplicate** copies a whole mission; it does not add a region
inside that mission.

Save both drafts before external editing. In the paired `.mission.json`:

1. Add a unique region row with `id`, `bounds`, `entities`, `neighbors`,
   `activation_bounds` and `working_set`. Give it real, ordered finite XYZ
   bounds and include its own ID in its working set.
2. Move the intended canonical entity IDs into its `entities` list, removing
   their previous homes. Split geometry/proxies first when necessary. Keep at
   least one home entity in each region.
3. Add directed transitions and matching outgoing neighbors, ensure entry
   reachability, and update every affected visible working set.
4. Use **File > Reload saved level** before continuing GUI work. An external
   source/partition change while the editor has drafts produces a hash conflict;
   reconcile the drafts instead of overwriting the changed file.

Mesh cuts and new region rows are explicit manual work today. There is no
automatic portal extraction, visibility proof or streamed Native Play preview.

## Save, check budgets and walk the result

Click every pending **Apply bounds** / **Apply floor vertices**, then
**Validate draft** and **File > Save + Cook** (Ctrl+S). Scene and partition share
Undo/Redo and checked paired publication. An invalid partition can remain saved
and editable for repair; building/running the ROM requires a valid cook.

Each stable view, chosen transition union and intermediate publication case
must fit these current limits:

| Resource | Limit |
| --- | ---: |
| Authored regions | 32 total |
| Resident region slots | 5 |
| Static triangles in a working view | 1,440 |
| Nonempty region/material groups in a working view | 64 |
| Boxes + exact floors + unavailable-gate barriers | 400 |
| Exact floors in a working view | 128 |
| Rendered models / vertices / triangles | 128 / 6,144 / 4,096 |
| Vertices in each cooked region/material mesh | 4,096 |
| Each region chunk / directory | 65,536 bytes each |

Adding a visible room can enlarge several transition unions, so inspect the
reported witness rather than only that room's own count. Whole-mission totals
can exceed resident limits. Splitting a chunk reduces individual chunk size;
it does not reduce the geometry required by a view that still sees both halves.

Choose **Build > Build ROM**, then inspect **Window > Diagnostics > Build &
Diagnostics**. **Refresh ROM cook report** displays the matching saved source
and partition: global regions/triangles, peak view, slots/largest chunk, mission
ROM bytes, embedded RGBA and known resident arrays. Embedded triangle colors
are already included in mission ROM bytes. Shared geometry and constant
bootstrap colors are separate resident categories; renderer/bank/slot/audio
costs need additional measurement.

Use **Test start** in Build & Diagnostics and **Build > Run level in Ares** for
the actual streamed mission. New `test_starts` are authored in scene JSON; keep
them supported, clear of blockers and inside an activation area. The editor's
ordinary **Play** is disabled for stream scenes.

Equivalent district build/run command:

```powershell
python tools/build.py --bundle content/level_bundle.json --start-level bafford-district --run
```

For a quick portable package/report without launching:

```powershell
python tools/compile_mission.py --manifest content/thief_bafford_district.mission.json --content-root content --output build/manual-district.dlm
```

The latter checks portable content and emits `build/manual-district.report.json`;
the ROM build additionally checks the resident bank limits. Its generated
`catalog/levels/.../generated/mission_report.json` lists `working_sets` and the
capacity witnesses. Walk both directions, turn back, examine roofs/side rooms,
exercise the door, then leave and return. Existing scripted district routes are
useful regressions but do not cover a newly added room; structural edits need
new route/view checks. See [the performance workflow](bafford-stream-performance.md)
for timing checks and [the format reference](mission-package-format.md) for
record details.
