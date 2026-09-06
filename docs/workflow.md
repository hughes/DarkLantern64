# Content and development workflow

Status: initial workflow implementation, 2026-09-05. Creators can start with the [quickstart](creator-guide.md). See the [project overview](../README.md) for executable commands and prerequisites, the [editor reference](../editor/README.md) for controls, and the [architecture](architecture.md) for current limits.

## Author in LightEngine

LightEngine is the desktop authoring environment. The DarkLantern64 editor application lives in `editor/` and consumes the pinned engine checkout under `external/LightEngine`. Canonical scene data is `content/first_room.json`; referenced placeholder models live in `content/models/`. The compiler generates the desktop preview and runtime mesh data from the same assets.

Use the 3D viewport and object properties to place models, then **Save + Cook** to validate and update generated assets or **Build ROM** to produce the playable build. Assets use OBJ geometry with per-instance colors or UV-mapped RGBA16 textures; animation conversion remains future work. The [texture pipeline](texture-pipeline.md) and [courtyard study](night-contrast.md) describe the working textured scene. Export compatible placeholder geometry from Blender, reference it by asset ID, and preserve gameplay IDs when revising it. Rich inheritance and general Blender reimport ownership remain planned work.

Stable authoring IDs must survive save, reload, and import changes. Duplication creates new identities. The compiler may remap IDs into compact runtime indices while retaining a debug mapping to the editor.

The **Audio Memory** panel supports an early planning loop alongside scene authoring. Choose a named listening context or transition, review retained allocations and unknowns, edit the assumptions, then **Apply + Recalculate**. Its inputs live in `content/audio_budget.json`; its generated report lives in `build/generated/audio_memory_report.json`. The contexts are authored examples, not an automatic analysis of the scene or measured console heap. See [audio memory planning](audio-memory-planning.md) for the formulas, configuration contract, and illustrative results.

## Validate, compile, and build

The implemented build loop is:

1. Save source content and identify changed dependencies.
2. Validate schemas, references, required assets, and supported target features. Report failures against named objects in the editor.
3. Compile geometry, textures, collision, gameplay properties, and any required spatial data into versioned target artifacts. Flatten authoring inheritance where useful; do not require a rich property database on the console.
4. Rebuild affected artifacts and the ROM incrementally. Record inputs, tool versions, output sizes, and warnings. A failed build must leave the previous valid ROM available and clearly identify it as stale.
5. Launch the new ROM in Ares, then validate representative changes on hardware.

`tools/compile_level.py` handles content validation/cooking. `tools/build.py` invokes the installed SDK without shell interpolation and preserves the last valid ROM if compilation or packaging fails. `build/<ROM-name>/build.json` records source and ROM hashes, tool identity, size, and elapsed build time. Unchanged builds reuse the existing ROM only when input fingerprints and its output hash still match. Generated C data is compiled for the target; a separately versioned streaming asset format is future work.

Per-system memory allowances and frame-time budgets remain to be established from measurements. The [8 MiB baseline](decisions/0001-memory-baseline.md) includes runtime allocations, buffers, stacks, code, and resident content; desktop authoring richness must compile into that bounded space.

## Editor commands and layout

Saved levels can also be assembled into one ROM through an explicit bundle manifest. The [level-select workflow](level-select.md) supports a runtime selector, direct level/preset launches, and repeatable named test starts. Existing editor **Build ROM** commands continue to build the selected source as a standalone level.

`python tools/editorctl.py inspect` reads the running editor through a local request/response interface. It also supports bounded content edits, save/build, capture, layout inspection/reset, reload, and clean quit. Use `--args-file` for structured arguments on Windows. See the editor guide for the current operation contract. Commands operate on the same canonical document as the editor UI; they do not evaluate arbitrary code.

Requests have unique IDs and expirations. A timeout does not prove that a mutation failed: inspect state before retrying. Responses and captures live under `.dev/editor/`, alongside compiler/build logs and the saved layout.

New projects receive a deterministic dock layout. User rearrangements persist in a project-local ImGui settings file, and **Reset Layout** restores the project default. The supporting engine change is [PR #14](https://github.com/hughes/LightEngine/pull/14); LightEngine changes use branches and pull requests for owner review before merging to main.

## Make iteration observable

The current tools and the next feedback improvements are:

- A build report shows which assets changed, compilation time, ROM size, and estimated resident costs; runtime telemetry checks those estimates.
- Object properties and compiler diagnostics expose links and patrol data. Rich in-viewport overlays for routes, light influence, sound events, and links are still to develop.
- A runtime overlay records frame time, memory usage, enemy and perception counts; per-enemy logs connect positions, states and route indices to stable source IDs. The [enemy workflow](enemy-authoring.md) supports placement, types, independent patrols and sentry posts.
- Repeatable scenario inputs and captured logs make regressions comparable between builds.

Use Ares with its homebrew development facilities for the fast loop. The launch helper copies installed settings into `.dev/ares/` and sets keyboard controls, homebrew logging, and 8 MiB there. `tools/smoke_ares.py` runs isolated 4/8 MiB instances and writes `build/ares-smoke.json`; the autoplay ROM follows a fixed input route for the checked-in scene, so intentional scene changes may require updating that test route. It does not teleport the player or force objective state.

Runtime telemetry reports frame time, CPU work time, heap use, guard state, and mission events. Start toggles the on-screen debug display. Track separate test results for an original N64 with Expansion Pak and ModRetro M64. Each result should identify the build, content revision, platform, and observed outcome. Hardware coverage remains pending until those tests actually run.
