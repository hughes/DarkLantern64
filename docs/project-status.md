# Project status and maintained capabilities

Run `python tools/project_status.py` from the project root for the checkout,
saved scene/partition identities, matching cook/ROM artifacts, validation
reports and owned sessions. Use `--project PATH` for another checkout,
`--draft-file PATH` for a private draft comparison, or `--no-doctor` to omit
tool probes. Reading status does not cook, build, launch, or terminate sessions.

Status separates freshness from outcome: a passing report for older source is
stale; an interrupted run is incomplete; a missing artifact has no result.
An unknown process identity or missing hash remains unknown. Source hashes,
package/build bindings and persisted outcome fields establish a match, rather
than filenames or modification time alone. A passing host preview is not a
ROM timing result. See the [validation pipeline](validation-pipeline.md) for
source-bound acceptance and its exact known-fixture and capability-skip rules.

Cook and ROM matches use the saved scene/partition; unsaved draft comparisons
are shown separately. New cooks capture the conservative Python/C source recipe.
Older cooks without that provenance are incomplete until recooked. Streamed
editor cooks explicitly identify canonical JSON hashes, while saved-byte hashes
retain their own basis. A target capacity failure can coexist with a valid full
authoring preview. Once an input proves a historical ROM stale, remaining large
artifact bytes are not rehashed; that shortcut never establishes a current match.
If observed inputs change during collection, the result reports an inconsistent
snapshot and withdraws current artifact matches until refreshed.

In the editor, open **Window > Diagnostics > Project Status** or **Help >
Project status**, then use **Refresh**. Collection runs asynchronously and
includes the current in-memory scene/partition. `editorctl.py get_project_status`
uses the same collector; optional arguments `{"include_doctor":false}` omit
tool probes. Its `editor_observation` identifies collection in progress, errors,
the captured scene and draft changes since collection, so a completed snapshot
cannot certify a newer unsaved edit. Opening or refreshing status does not
save, cook or build.

For tool setup use the [environment bootstrap](bootstrap.md). Run
`python tools/development_environment.py` to inspect resolved tools and
actionable missing requirements, and `python tools/run_tests.py` for the
standard test runner with workspace-owned temporary files and UTF-8 output.
Locally installed source assets, local SDK/editor executables and an actual
test failure are reported separately; missing optional data cannot certify a
feature that was not exercised.

The capability declarations below are maintained separately from generated
artifacts. Every numerical limit has a source path and symbol checked without
importing or executing project compilers. `python tools/check_project_capabilities.py`
fails on source-limit or marked-document drift. After reviewing an intentional
change to `tools/project_capabilities.json`, use `--write` to refresh the marked
sections. That option updates documentation only and refuses a mismatched
source limit.

<!-- project-capabilities:begin -->
This section is maintained in [project_capabilities.json](../tools/project_capabilities.json); run `python tools/check_project_capabilities.py` to check it.

| Capability | State | Current behavior and limits |
| --- | --- | --- |
| Full authoring preview | implemented | Canonical scene editing, import, history and desktop preview include host-only large studies. A source preview is not cooked ROM simulation or target lighting/performance validation. Unassigned streaming homes and invalid capacity drafts stay editable. |
| Ordinary Native Play | limited | Bounded ordinary levels use shared portable gameplay, tick and animation with independent runtime state. Large external-scale-study sources are rejected; desktop rendering/lighting approximate the target. |
| Streamed Native Play | limited | Cooked DLM1 package chunks and shared coordinator drive demand, queued doors, floor support, preparation/publication/retirement and persistent mission state. CRC/SHA identity checks, delay injection and inspector snapshots are available. Rendering policy classification is shared; host shader work and timing do not certify the ROM. |
| ROM play | limited | Ordinary resident C assets and bounded streamed DLM1 missions run through libdragon/Tiny3D. The default RSP backend transforms, rigidly deforms and clips; the CPU reference remains available. Source-bound Ares functional/lifecycle/strict workload checks exist. Larger missions and original hardware need separate acceptance. |
| Static lighting and diagnostics | limited | Baked inline closed/open states support at most one global independent door. Standalone baked assets and live night fallbacks have explicit policy reasons; multiple doors disable the inline shortcut and lighting changes can require live work. Publication/revision reports identify source/package/build and cost scope. Estimates and unknown costs remain distinct from measured timing/heap. |
| Resident character animation | limited | Shared immutable meshes/skeletons/quantized keys, independent actor playback, two-clip blending, connected one-influence geometry, bounded head attention and traveled-distance foot contacts are implemented. Host cooked-character caches use source/recipe identity; runtime pose/render caches stay bounded by scene storage. Animated scale, multiple vertex influences, general graphs/IK and cold-clip streaming are unavailable. |
| Streamed animated assets | unavailable | The DLM cooker rejects animated character assets and linked asset packs. Resident character support does not imply character/clip streaming in a streamed mission. |
| Runtime regions and visibility | limited | Manual ownership, activation bounds, directed paired gates and independent authored visible sets feed the shared transactional coordinator. Stable sets and transition unions must satisfy bridge capacity. Visibility authoring has no automatic BSP/PVS compiler. Portable packages can exceed the runtime region cap and then remain inspectable with target failures. |
| Original N64 and M64 validation | pending validation | Expansion Pak startup and measured Ares workloads are verified separately from original N64/M64 visual, audio and performance acceptance. Those physical target checks remain pending. |

These are software validation/storage limits, not measured capacity for every workload. Host authoring budgets do not certify ROM residency or frame time.

| Limit | Value | Scope | Source |
| --- | ---: | --- | --- |
| `mesh_vertices` | 4,096 vertices | Resident renderer; placed instances count separately, including bootstrap/dynamic models. | [DL_MAX_MESH_VERTICES](../src/content_limits.h) |
| `scene_vertices` | 6,144 vertices | Resident renderer; placed instances count separately, including bootstrap/dynamic models. | [DL_MAX_SCENE_VERTICES](../src/content_limits.h), [CAPS](../tools/cook_diagnostics.py) |
| `scene_triangles` | 4,096 triangles | Resident renderer; placed instances count separately, including bootstrap/dynamic models. | [DL_MAX_SCENE_TRIANGLES](../src/content_limits.h), [CAPS](../tools/cook_diagnostics.py) |
| `scene_models` | 128 models | Resident renderer; placed instances count separately, including bootstrap/dynamic models. | [DL_MAX_MODELS](../src/content_limits.h), [CAPS](../tools/cook_diagnostics.py) |
| `ordinary_entities` | 256 entities | Ordinary saved level validation; host study and streamed authoring use separate budgets. | [content_limits](../tools/content_profile.py) |
| `ordinary_materials` | 64 materials | Ordinary saved level validation; host study and streamed authoring use separate budgets. | [content_limits](../tools/content_profile.py) |
| `ordinary_lights` | 16 lights | Ordinary saved level validation; host study and streamed authoring use separate budgets. | [content_limits](../tools/content_profile.py) |
| `ordinary_colliders` | 256 colliders | Ordinary saved level validation; host study and streamed authoring use separate budgets. | [content_limits](../tools/content_profile.py) |
| `ordinary_floors` | 128 floor triangles | Ordinary saved level validation; host study and streamed authoring use separate budgets. | [content_limits](../tools/content_profile.py) |
| `ordinary_enemies` | 16 enemies | Ordinary saved level validation; host study and streamed authoring use separate budgets. | [content_limits](../tools/content_profile.py), [DL_MAX_ENEMIES](../src/game.h) |
| `ordinary_interactive_objects` | 64 objects | Ordinary saved level validation; host study and streamed authoring use separate budgets. | [content_limits](../tools/content_profile.py), [DL_MAX_OBJECTS](../src/game.h) |
| `study_models` | 8,192 models | external-scale-study host authoring only; Native Play and ROM compilation reject this profile. | [STUDY_LIMITS](../tools/content_profile.py) |
| `study_scene_vertices` | 262,144 vertices | external-scale-study host authoring only; Native Play and ROM compilation reject this profile. | [STUDY_LIMITS](../tools/content_profile.py) |
| `study_scene_triangles` | 131,072 triangles | external-scale-study host authoring only; Native Play and ROM compilation reject this profile. | [STUDY_LIMITS](../tools/content_profile.py) |
| `mission_models` | 1,024 models | streamed-mission total authoring budget, not a resident runtime allocation. | [STREAM_LIMITS](../tools/content_profile.py) |
| `mission_entities` | 2,048 entities | streamed-mission total authoring budget, not a resident runtime allocation. | [STREAM_LIMITS](../tools/content_profile.py) |
| `mission_scene_vertices` | 131,072 vertices | streamed-mission total authoring budget, not a resident runtime allocation. | [STREAM_LIMITS](../tools/content_profile.py) |
| `mission_scene_triangles` | 32,768 triangles | streamed-mission total authoring budget, not a resident runtime allocation. | [STREAM_LIMITS](../tools/content_profile.py) |
| `portable_package_regions` | 256 regions | Portable DLM cooker; target/Native bridge is stricter. | [validated_regions](../tools/compile_mission.py) |
| `runtime_regions` | 32 regions | Authored runtime bridge; stable sets and transition unions must fit. | [DL_STREAM_SCENE_MAX_REGIONS](../src/stream_scene.h), [CAPS](../tools/cook_diagnostics.py) |
| `resident_regions` | 5 regions | Authored runtime bridge; stable sets and transition unions must fit. | [DL_STREAM_SCENE_MAX_RESIDENT_REGIONS](../src/stream_scene.h), [CAPS](../tools/cook_diagnostics.py) |
| `stream_static_triangles` | 1,440 triangles | Authored runtime bridge; stable sets and transition unions must fit. | [DL_STREAM_SCENE_MAX_TRIANGLES](../src/stream_scene.h), [CAPS](../tools/cook_diagnostics.py) |
| `stream_groups` | 64 groups | Authored runtime bridge; stable sets and transition unions must fit. | [DL_STREAM_SCENE_MAX_GROUPS](../src/stream_scene.h), [CAPS](../tools/cook_diagnostics.py) |
| `stream_colliders` | 400 records | Authored runtime bridge working set; collider count includes synthetic unavailable-gate barriers. | [DL_STREAM_SCENE_MAX_COLLIDERS](../src/stream_scene.h), [CAPS](../tools/cook_diagnostics.py) |
| `stream_floors` | 128 floor triangles | Authored runtime bridge; stable sets and transition unions must fit. | [DL_STREAM_SCENE_MAX_FLOORS](../src/stream_scene.h), [CAPS](../tools/cook_diagnostics.py) |
| `stream_directory_bytes` | 65,536 bytes | N64 directory storage cap. | [MAX_DIRECTORY](../src/stream_rom.c), [CAPS](../tools/cook_diagnostics.py) |
| `stream_chunk_bytes` | 65,536 bytes | Maximum individual runtime region chunk. | [MAX_CHUNK](../src/stream_rom.c), [CAPS](../tools/cook_diagnostics.py) |
| `directed_gates` | 256 gates | Authored runtime policy; zero gates allowed for a single region. | [authored_policy](../tools/compile_mission.py) |
| `outgoing_gates` | 8 gates per region | Directed outgoing gates; paired openings occupy one gate in each direction. | [authored_policy](../tools/compile_mission.py) |
| `animation_bones` | 32 bones | Shared resident character skeleton and caller-owned sampler matrix scratch. | [DL_ANIMATION_MAX_BONES](../src/animation.h), [MAX_BONES](../tools/character_assets.py) |
| `animation_clips` | 32 clips per asset | Character source validation; no streamed animated asset support. | [validate](../tools/character_assets.py) |
| `animation_samples` | 4,096 samples per clip | Character source validation; no streamed animated asset support. | [validate](../tools/character_assets.py) |
| `animation_events` | 256 events per clip | Character source validation; no streamed animated asset support. | [validate](../tools/character_assets.py) |
| `animation_sockets` | 32 sockets per asset | Character source validation; no streamed animated asset support. | [validate](../tools/character_assets.py) |
| `inline_independent_doors` | 1 door | Global independent-door count for inline closed/open lighting states; multiple doors require another path. | [dl_lighting_inline_payload](../src/lighting_policy.h) |
| `mission_directory_version` | 1 version | DLM1 directory format. | [DL_MISSION_VERSION](../src/mission.h) |
| `mission_chunk_version` | 4 version | Newest DLR1 chunk produced when baked night environment is present; decoder accepts earlier versions. | [cook_mission](../tools/compile_mission.py) |
| `required_memory` | 8,388,608 bytes | N64 startup requires Expansion Pak before substantial initialization. | [REQUIRED_MEMORY](../src/main.c) |
<!-- project-capabilities:end -->
