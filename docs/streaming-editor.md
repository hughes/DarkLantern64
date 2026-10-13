# Streaming must remain an editor workflow

The level editor is the primary place to develop playable content. Streaming
must fit that workflow: artists edit a canonical scene, assign its spatial
meaning, inspect validation, and play the same cooked result on host and ROM.
A runtime demonstration that requires an engineer to repair every JSON edit
does not complete this architecture.

For the current practical workflow, including changing which rooms stay visible
through an opening, see [Expanding Bafford content by hand](bafford-content-authoring.md).
The sections below also describe future work; the hand-authoring guide labels
the operations the editor supports today.

## Current integration and its limits

`content/streaming_probe.json` is an ordinary editable scene. Its separate
`streaming_probe.mission.json` owns manual region bounds, home entity IDs,
neighbors and entry region. The bundle names the partition manifest with
`{"id":"streaming-probe","mission":"streaming_probe.mission.json"}`.
The same canonical scene remains associated with that manifest when excluded
from the game menu.

The project catalog now resolves mission entries through their canonical scene.
Title changes retain their bundle ID and mission reference. Including a scene
with a partition manifest uses the mission entry. Duplicating a stream scene
copies its partition and repoints `source` in the same publication transaction;
entity IDs remain unchanged inside that independent mission. Deleting a stream
scene archives both files, removes its menu entry and retains shared assets.
Expected source/manifest bytes and the existing rollback mechanism protect
these operations from partial publication.

The editor's **Streaming** panel edits selected objects' home assignment, region
bounds, entry region, visibility policy and neighbor links. **Fit bounds to owned
objects** evaluates transformed geometry and collision in the current scene
draft, with a small coverage margin. Numeric fields retain edits until **Apply
bounds**, so typing does not trigger preparation. **Validate draft** previews scene and
partition changes together. The panel reports bounds/coverage errors, directory
bytes, ROM bytes and authored neighbor overlap. Adding an object leaves it
visibly unassigned until its home is chosen. Moving architecture outside its home bounds makes the region cook
invalid. Both edits remain visible and editable; an invalid partition must not
hide the scene from the catalog or prevent an authoring preview. Partition
changes invalidate the preparation cache independently of scene changes.
Deleting scene objects removes their home IDs in the same Undo/Redo transaction.
Malformed external bounds use safe replacement controls or the bounds-fit
repair; malformed region/home structure stays visible with a repair diagnostic.
Numeric bounds fields retain typed values until **Apply bounds** commits the
change, allowing authors to finish editing before preparation begins.

**Run level in Ares** resolves the mapped bundle ID and invokes the mission
bundle with that start level and selected test start. It cannot silently build
the scene as a resident legacy level. A stream scene excluded from the game
menu must be included before this launch path is available. **Run game menu**
uses the complete bundle as before. ROM cooking validates the partition before
publishing its package.

The authoring viewport displays the complete editable scene. **Native Play
uses the same cooked DLM1/DLR1 package and portable streaming coordinator as
the ROM.** Preparation emits a bootstrap `play.json` bound to the package's
SHA256; architecture and collision are decoded from region chunks during
Play. The Play Inspector exposes current, demanded, physical, candidate and
published masks, queued interactions, slot generations, pins and lifecycle
state. See the [shared coordinator](streaming-coordinator.md) for ownership,
synthetic delay/failure tests and functional host/ROM replay comparisons.

The authored ROM policy supports up to 32 regions with at most five region
chunks resident at once. Each reachable stable set, selected transition union
and partial publication state must fit 1,440 static triangles, 64 nonempty
region/material groups and 400 total box contacts, exact floor triangles and
gate barriers. Floor triangles have a separate 128-record bank bound. Render
instances and vertices retain their ordinary limits. These are explicit
working-set capacities, not total mission geometry limits. The panel distinguishes
a valid portable partition from one that fits the current ROM bridge; a
bridge-capacity failure keeps the authoring preview available and prevents ROM
publication.

Textured region chunks retain evaluated UVs and normals. Shared texture sprites
use the ordinary texture cooker and active-level renderer lifetime; they are
not yet evicted by region. The manifest's optional `visibility` chooses the
original `door-distance` probe policy or conservative `all-regions` residency.
Those legacy policies remain two-region probes. `authored-sets` supports larger
worlds: each region owns separate player activation bounds and a conservative
visible `working_set`, while directed gate bounds define transitions. The
nearest nearby gate supplies one pending target, with hysteresis; the source
set remains loaded until the target set is complete and the player crosses
its gate. Other unavailable gates block movement. Geometry ownership bounds
can overlap without changing activation meaning.

The Streaming panel edits activation bounds, visible-set members and directed
gate endpoints/bounds; gates can be added or deleted with paired Undo/Redo.
Numeric fields use persistent buffers and explicit Apply actions. Validation
reports the reachable working sets and worst slot count. Authored visibility
is a conservative content contract; this metadata does not prove a PVS.

Larger canonical sources opt in to `authoring_profile: "streamed-mission"`.
Their total static geometry and home IDs can exceed a resident scene, while
ordinary global gameplay/material/light caps remain. Editor creation reads
the prepared source limits. Resident compilation rejects this profile;
Native Play requires its valid partition and bounded stream cook.
**Add floor triangle** and the
Inspector's local floor vertices edit exact horizontal or sloping support;
Collision debug draws its transformed edges. **Drop to surface** uses the
highest supporting box top or transformed triangular floor at the object's
footprint. Region cooking preserves these
floor triangles in DLR1 v3 instead of filling a nonrectangular floor with boxes.

Scene and partition drafts share Undo/Redo and unsaved-change handling through
an internal paired history snapshot. The canonical scene JSON does not embed
the partition. **Save + Cook** compares both saved SHA256 identities, then uses
the project's checked publication transaction to save the source, manifest and
prepared preview together. A conflict or replacement failure retains both
drafts and restores earlier published bytes. A structurally valid authoring
draft with invalid region coverage can still be saved and repaired in the
panel; ROM build/play remains blocked until region cooking succeeds.
Single and bulk entity deletion prune their home IDs in the same paired Undo
step. Malformed manifest rows and bounds show repair diagnostics while the
scene remains editable; existing regions with bad bounds support replacement
or fitting.
After a successful publication, an ancillary cache/catalog failure reports a
warning and retains the new saved identities so the next save can proceed.

Region create/rename/delete controls, viewport bounds handles and selection
overlays remain future work. The following
work is necessary before the complete larger-level workflow is accepted.

## Region authoring as first-class scene editing

Add a Regions panel and region overlay to the viewport. Authors need to create,
rename, duplicate and delete regions; edit bounds with handles; choose an entry
region; inspect neighbors; and assign selected objects to a home region from
the Inspector. Region objects should be selectable from either the scene or
region list. Display incomplete assignments and shared boundaries directly on
the geometry. Support snapping, multi-selection and undo for these actions.

Keep authored intent separate from derived compiler output. The authored
manifest contains stable region IDs, canonical entity IDs, bounds/partition
constraints and portal/transition annotations. The compiler determines packed
geometry, triangle ownership, dependency closure and byte estimates. Do not
store compiler array indices as editable object links. Every entity needs one
home; geometry or collision may need explicit cross-boundary dependencies.
Bounds may overlap and must truthfully contain their assigned geometry and
colliders. A doorway's visibility and movement behavior need explicit dynamic
door association rather than depending on its name or scene-list position.

Creating, duplicating or deleting scene objects must update region assignments
within the same authoring transaction, or leave a clear unresolved assignment
that prevents runtime cooking. Choose assignment from the active authoring
region or an explicit spatial rule and show the result. Bulk duplication,
prefabs, imported assets and Blender exchange all need the same behavior.
Deleting a region with owned objects should offer reassignment as part of the
operation. Renaming an entity must rewrite home IDs and gameplay references.

Treat scene JSON and partition JSON as one editable document session. Save,
undo/redo, staged preparation and Blender imports need the source hashes of
both inputs, a project lock and atomic publication with rollback. Prepare a
private candidate scene and candidate manifest together, validate them, then
publish both. No successful save may leave the new scene referring to an old
partition. Project duplication and recovery must retain both files and any
future source spatial annotations.

## Incremental preparation and diagnostics

Use a dependency graph keyed by evaluated geometry, transforms, collision
settings, region assignments, portal annotations, materials, lights and
compiler version. Show why a region is dirty and what its rebuild affects.
Material/UV edits should not unnecessarily rebuild navigation. Wall, floor and
opening edits invalidate bounds, ownership, portals/PVS, collision, navigation
and lighting for the affected region and dependent seams. Source Thief cell
and polygon IDs are provenance; they do not establish valid topology after
an edit. Unclassified structural edits require a conservative rebuild.

Make derived results inspectable in the viewport: region bounds, portal
polygons, visible candidate sets, walkable areas, actor clearance, missing
collision and light/probe influence. A doorway should show its sight,
navigation, collision, light and acoustic associations. Triangle/BVH collision
and richer movement must eventually replace the current box-only demo without
changing how authors inspect a problematic stair or opening.

The resource panel must distinguish ROM storage from resident RAM. Show current
and worst transition sets, their dependency witnesses, decoded bytes, renderer
packing/cache bytes, textures/uploads, collision/navigation/probes, shared
assets, persistent state, slot/alignment/scratch costs, audio/display/code and
reserve. Unknown categories must stay visibly unknown. Per-region estimates
and source counts alone cannot certify an 8 MiB target. Add live residency,
prefetch/retirement status, stalls and frame-time distributions when play uses
the shared loader. Cook diagnostics must link back to the offending authoring
object or boundary.

## One portable Native Play and ROM contract

Native Play reads the same DLM1/DLR1 package, checksums and IDs and uses the
portable loader, residency coordinator, working-scene adapter and mission-owned
persistent state as ROM. Its host file reader supplies bounded reads; synthetic
callbacks exercise delayed/error reads and delayed graphics completion through
that same coordinator. Loading and unavailable collision use the published
bank. Host graphics completion has a desktop implementation, while region
buffers use the same pin/generation/retirement protocol.

Presentation can differ: the authoring viewport uses full source assets, while
runtime preview should expose target packing, texture formats and active draw
groups. Offer distinct authoring and runtime views with clear status. The same
input trace and mission package identity should be usable by the host runner,
Native Play and emulator tests, with deterministic state assertions across
crossings, delayed loads, doors, awareness, loot and objectives.

Acceptance for the editor integration includes creating and assigning a new
object, moving a wall across a boundary, opening an arch, editing a shared
asset, duplicating a stream scene and recovering a deleted one. Rebuild the
affected outputs, compare Native Play with ROM, and test stale source/manifest
hash conflicts and publication rollback. Artist edits must not disappear,
lose their home assignment silently, or launch a different runtime format.
