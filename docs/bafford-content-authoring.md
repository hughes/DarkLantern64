# Expanding Bafford content by hand

Start with the local **Bafford District** and make one doorway or room change at
a time. The scene editor places geometry, authors region ownership and bounds,
changes visible working sets and creates movement gates. Switch between the
complete authoring scene and the cooked runtime preview to inspect the actual
published working view before checking the rebuilt ROM.

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
4. Click **Validate draft**, then **Cook runtime preview** in Streaming. Enable
   **Live preview camera** and inspect both sides; **Step preview** advances
   pending work explicitly. Missing distant walls/roofs or a view that appears
   only when approaching a different doorway usually needs a broader stable
   `working_set`. Change the visible checkboxes and **Recook runtime preview**
   to compare the result. Check the rebuilt ROM after the preview is correct.

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
- Check **Create both travel directions**, enter a unique **Reverse gate ID**,
  then add the pair in one Undo step. Each direction retains its own visible
  working set. Use **Open gate** for an unobstructed passage, or choose an
  existing canonical door. A door's home must be one endpoint; use the same
  door for both directions of that opening.
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

Use **Streaming > Region authoring**, enter a **New region ID** and click
**Create region**. New regions start empty. Select its viewport label or bounds
handle, assign objects through **Selection home**, and fit or position its real
bounds. Activation bounds and visible sets have separate controls and meaning.
Add the directed opening connections before validating the cook.

For an existing region, **Rename region** rewrites region references throughout
the partition. **Duplicate region** copies bounds and its visible set, replacing
its own ID with the new ID, while leaving homes and connections empty. It does
not duplicate geometry. **Levels > Duplicate** copies the complete paired
mission instead.

Select **Minimum**, **Maximum** or **Center** on a region, activation area or
gate to move its handle with the viewport translation gizmo. Release the drag to
commit one paired Undo step. Numeric bounds and **Apply bounds** remain useful
for exact coordinates. Geometry transforms do not change when these partition
handles move.

Before **Delete region...**, choose **Reassign owned objects to**. **Delete and
repair** moves those home IDs, removes gates that end at the deleted region and
repairs entry/neighbors/visible-set references together. An owned region cannot
be deleted without that destination. Undo restores the complete partition state.
Visible-set references change to the new home rather than dropping the visible
geometry; outgoing movement gates are removed independently.
New, duplicated and imported scene objects remain visibly unassigned until a
valid home is chosen; empty or disconnected drafts remain editable for repair.
Deleting down to one region is valid after fitting its transferred homes and
activation coverage. That final authored region needs no transition gates.

Mesh cuts, visibility sets and region placement are still explicit authoring
work. There is no automatic portal extraction or visibility proof.

## Compare authoring with the published runtime view

**Cook runtime preview** cooks the current scene and partition drafts together.
The viewport shows geometry from complete published banks, rather than hiding
source objects solely by their home labels. **Full authoring view** returns to
the editable scene. Source/partition hashes, package SHA256 and the play
fingerprint identify the snapshot. Editing a draft marks it **STALE DRAFT**;
recook when ready to update the preview.

Enable **Streaming overlays** to see blue geometry bounds, cyan activation
areas, orange gates, green published sets, purple physical residency and yellow
pending work. Select a region or opening to inspect its stable visible set,
destination set, transition union and cooked capacity witnesses. Move the live
camera through the directed gates; arbitrary movement through a wall does not
become a new transition.

Use **Delayed-load controls** to slow reads, renderer preparation or independent
graphics completion. Turn back while a candidate is loading and confirm that
the published architecture remains complete. Leave a region and return; doors
and collected loot retain their mission state. **Native Play** uses the same
cooked coordinator with simulation and interactions. Desktop preview is a
visibility/content check; ROM rendering, audio and performance acceptance remain
separate.

For a small reproducible multiple-opening example without imported Bafford art,
see `tests/fixtures/stream_multi_opening.json` and its `.mission.json`. The hub
can see north and courtyard through separate openings; its intentionally narrow
working set hides the courtyard landmark. Add courtyard to the hub working set
and recook. This fixes the view without changing any source geometry. The owned
editor verifier runs that repair, region authoring, delays and paired Save:

```powershell
python tools/verify_stream_authoring_preview.py
```

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
them supported, clear of blockers and inside an activation area. Ordinary
**Play** starts the streamed Native Play simulation; use the separate runtime
preview while authoring region bounds or visible sets.

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
