# Content and development workflow

Status: proposed workflow; this repository contains planning documentation. No DarkLantern64 editor integration, compiler, runtime, or runnable commands exist yet. See the [project overview](../README.md), [vision](vision.md), and [architecture](architecture.md).

## Author in LightEngine

LightEngine is the desktop authoring environment. Its inspected source already implements scene JSON serialization, entity and transform editing, material/light/camera controls, multiple viewports, debug displays, and play mode using a cloned scene. Its Blender exporter writes JSON and glTF, supports export on save, and the engine implements file-watching reload. These capabilities were inspected in code; they have not been exercised as a DarkLantern64 workflow.

Use Blender for geometry and texture preparation, then LightEngine for placement, gameplay properties, relationships, and mission assembly. Rich source content can retain descriptive names, editable inheritance, and authoring metadata. Proposed additions include stable object and asset IDs, guard routes, interactions, objectives, and target diagnostics. Imported objects need a defined ownership model so reimport preserves intentional gameplay edits and references.

Stable authoring IDs must survive save, reload, and import changes. Duplication creates new identities. The compiler may remap IDs into compact runtime indices while retaining a debug mapping to the editor.

## Validate, compile, and build

The proposed loop is:

1. Save source content and identify changed dependencies.
2. Validate schemas, references, required assets, and supported target features. Report failures against named objects in the editor.
3. Compile geometry, textures, collision, gameplay properties, and any required spatial data into versioned target artifacts. Flatten authoring inheritance where useful; do not require a rich property database on the console.
4. Rebuild affected artifacts and the ROM incrementally. Record inputs, tool versions, output sizes, and warnings. A failed build must leave the previous valid ROM available and clearly identify it as stale.
5. Launch the new ROM in Ares, then validate representative changes on hardware.

Renderer selection will determine several conversion steps. Per-system memory allowances and frame-time budgets remain to be established from measurements. The [8 MiB baseline](decisions/0001-memory-baseline.md) includes runtime allocations, buffers, stacks, code, and resident content; desktop authoring richness must compile into that bounded space.

## Make iteration observable

Prioritize tools that answer concrete questions:

- A build report shows which assets changed, compilation time, ROM size, and estimated resident costs; runtime telemetry checks those estimates.
- Editor overlays show guard routes, light influence, sound events, and object links so invalid placement or wiring is visible immediately.
- A runtime overlay records frame time, memory usage, guard state, and the last perception event; source IDs connect a failure to its authored object.
- Repeatable scenario inputs and captured logs make regressions comparable between builds.

Use Ares with its homebrew development facilities for the fast loop. Track separate test results for an original N64 with Expansion Pak and ModRetro M64. Each result should identify the build, content revision, platform, and observed outcome. Hardware coverage remains pending until those tests actually run.
