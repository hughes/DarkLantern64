# Editor preparation and picking

Level opening used to synchronously launch a catalog validation and then a full
cook, which validated the catalog again. Each new Python process also repeated
character animation compression/error analysis. The console loads artifacts
prepared by that work, so its loading time did not measure the same operations.

Project preparation now caches validated dependencies, cooked character data and
published outputs under `.dev/editor/cache`. Entries check content hashes and
tool source fingerprints. Missing/changed outputs, changed dependencies, source
edits and tool changes invalidate them. Staged drafts have separate keys from
saved content. Cache corruption falls back to cooking; cache write failures do
not fail a successful authoring operation. Publication and source-conflict guards
remain in place.

Normal level opening and automatic draft refresh prepare on a worker. Rendering,
camera navigation and inspection continue during preparation. Other commands
wait in the request queue with their original expiry; editing controls are
temporarily disabled. A newly changed draft prevents a prepared level switch
from discarding those edits. Save/Play and explicit preparation commands retain
their completion semantics.

Same-level refresh keeps the camera transform and projection/orbit settings.
If the cooked preview fingerprint is unchanged, it avoids reloading the scene
entirely. This covers gameplay-only changes such as collectible/decorative
classification. Undo schedules a refresh outside the ImGui render callback.

## Measured verification

`python tools/verify_editor_iteration.py` runs in a private project and records
phase timings, rendering progress, camera preservation, source hashes and real
engine picking queries. The September 8 Windows run measured:

| Operation | Editor preparation/application time |
| --- | --- |
| First courtyard switch, uncached | 2.37 s |
| Warm courtyard/store switches | 0.27–0.31 s |
| Collectible/decorative refresh, including command polling | About 1.2 s |

The request/response verifier polls at 100 ms, so its wall-clock observations
include additional polling latency. These timings describe this machine and
fixture, not a performance guarantee for larger levels. Rendering continued
during preparation; classification edits, added geometry and undo retained a
non-default camera. Native Play/replay and source-preservation checks also pass.

`inspect` exposes `frame`, `preparing`, and `last_preparation` with cache-hit,
cook, scene-application and total timings. This makes future stalls measurable
without relying on screenshots or perceived delay.

## Engine picking

LightEngine uses its BVH to reject unrelated bounds, then tests the candidate
mesh's front-facing triangles and chooses the nearest surface. Back faces and
empty space inside mesh bounds do not intercept clicks. Non-mesh editor markers
retain bounds picking. Triangle winding defines the front even when rendering
is two-sided; alpha cutouts and shader-only deformation are not evaluated.

BVH subdivision also now addresses parent nodes by index after recursive growth;
holding references across vector reallocation could previously lose children.
Tests cover 65 separately pickable objects, front/back faces, empty triangle
space, containing bounds, surface ordering and rays on slab boundaries.

`pick_editor` accepts `{"from":[0,2,0],"to":[0,2,10]}` and returns `entity_id`
and `source_id`, or nulls. It casts an infinite ray toward `to` using the exact
engine query used by viewport clicks, without changing selection. The live
verifier uses two loaded, transformed meshes to check clicking through a back
wall, selecting its front and missing an empty triangle corner.

Caching, preparation jobs and canonical camera preservation belong to the
DarkLantern editor. Triangle/BVH picking and the reusable `PickEditorEntity`
query belong to LightEngine.

The engine change is published in [LightEngine PR #20](https://github.com/hughes/LightEngine/pull/20).

Viewport input is resolved inside the current ImGui frame, after testing the
existing selection's gizmo. A selection click cannot also drag the old or new
selection. Changing selection, loading a scene, and entering Play cancel the
active gizmo gesture; a newly selected object waits for release before it can
be dragged. Missed releases while the viewport is hidden are canceled too.
This engine-wide correction is in [LightEngine PR #21](https://github.com/hughes/LightEngine/pull/21).

`bazelisk test @lightengine//:viewport_interaction_test` from `editor/` runs seven
headless tests through real ImGui/ImGuizmo mouse input. They cover selection
versus dragging, Sponza column/coin transforms, deliberate movement, selection
changes, hover suppression and hidden-viewport releases. The live disposable
Sponza check selected `gallery-coins` from `sponza-column-north-0`, then selected
`sponza-column-south-1`; the complete canonical document remained unchanged
after each click. Workspace selection, undo and Play smoke checks also pass.
