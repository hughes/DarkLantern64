# Developer tools

The VS Code task list has two everyday entry points: **DarkLantern64: Launch editor** and **DarkLantern64: Launch game**. Level management and test-start selection live in the editor. These specialist commands remain available from a terminal in the repository root; run the checks relevant to your change.

## Build and launch saved content

The editor's **Play level** and **Play game menu** save the current level before building. The commands below read saved source files, so **Save + Cook first** if the editor is open.

```powershell
# Build the game menu without opening Ares; add --run to play
python tools/build.py --bundle content/level_bundle.json

# Build and launch a standalone level at its default spawn
python tools/build.py --level content/moonlit_courtyard.json --run

# Launch directly at the courtyard loot display
python tools/build.py --level content/moonlit_courtyard.json --start-preset loot-counter --run

# Keep every bundled level available, but start in the enemy workshop
python tools/build.py --bundle content/level_bundle.json --start-level enemy-workshop --start-preset store-door-open --run

# Validate saved content without building a ROM
python tools/compile_level.py content/moonlit_courtyard.json --validate-only
```

The [level-select guide](level-select.md) explains stable bundle IDs, named test starts, menu controls and memory limits. Build output reports the actual ROM path; failed builds may leave an older ROM on disk.

## Blender assets

```powershell
python tools/blender_assets.py --open          # Open art/loot.blend
python tools/blender_assets.py                 # Export its saved meshes/materials
python tools/blender_assets.py --package-addon # Package the reusable Blender add-on

# Open/export the saved guard rig and its five Actions
python tools/guard_assets.py --open
python tools/guard_assets.py

# Export another saved source into its own asset pack
python tools/blender_assets.py --source art/my_props.blend --output content/assets/my_props
```

After export, import or reimport the pack through the editor's asset controls, place props, then **Play level**. The [Blender asset guide](blender-assets.md) describes materials, smooth normals and prefab placement. `python tools/blender_assets.py --make-demo` recreates the example source and **overwrites `art/loot.blend`**; use it only when deliberately rebuilding that example, after preserving art edits elsewhere.

The [guard animation study](guard-animation-prototype.md) describes the separate character export. Guard Animation Workshop already references its exported source; use **Save + Cook** after exporting, then preview clips or **Play level**. `python tools/character_assets.py content/assets/guard/guard.character.json` prints its compression and deformation-error comparison.

## Profiles, captures and budgets

```powershell
# Summarize completed profiling windows from the latest Ares play session
python tools/profile_report.py

# Capture authored cameras through the actual N64 renderer
python tools/capture_ares.py --level content/moonlit_courtyard.json

# Full CPU workload and render-culling comparison
python tools/scale_study.py --compare-culling

# Shorter guard/light CPU workload study
python tools/scale_study.py --micro-only

# Recalculate the saved audio memory planning assumptions
python tools/estimate_audio.py
```

Profiles need completed measurement windows; play for several seconds before reporting. Captures and studies create isolated diagnostic builds and save reports under ignored `build/` and `.dev/` directories. See [profiling](profiling.md), [night contrast](night-contrast.md), [mission scale](scaling-guide.md), and [audio memory planning](audio-memory-planning.md) for interpretation and limits.

For streaming investigations, build with `--runtime-trace` and import the Ares
log with `tools/runtime_inspector.py --map <matching-runtime_debug_map.json>`.
The [runtime inspector guide](runtime-inspector.md) covers the offline timeline,
authored ID map, gate/interaction diagnostics and complete-capture checks.

## Verification and automation

For a single acceptance report tied to captured source, content, dependency and
build identities, run the tracked manifest through the project pipeline:

```powershell
python tools/validate_project.py --host-audio-none --output build/project-acceptance.json
python tools/validate_project.py --profile portable --output build/portable-acceptance.json
```

The [validation pipeline guide](validation-pipeline.md) explains required
phases, explicit skips and known fixture errors, object-cache invalidation,
owned emulator jobs, and data-driven routes. The full profile separates
functional replay, strict wall-time performance, framebuffer capture and
menu/lifecycle evidence. The portable profile is a partial run and cannot
certify the required editor or ROM phases. `--host-audio-none` changes an owned
settings copy only; its report labels host audio output disabled while guest
callbacks/underruns remain checked.

```powershell
# Content/compiler Python tests and portable C gameplay/runtime checks
python tools/build.py --test

# Ares startup memory gate and the original mission input replay
python tools/smoke_ares.py

# Bundled levels, test starts, restart/resume and resource lifetime
python tools/smoke_bundle.py

# Static lighting bake parity, per-stage load times and fallback fixtures
python tools/verify_level_loading.py

# Enemy runtime behavior in Ares
python tools/smoke_enemies.py

# Build the desktop editor before exercising its real command interface
python tools/build.py --editor
python tools/verify_project_editor.py
python tools/verify_asset_editor.py --editor editor/bazel-bin/darklantern64_project_editor.exe
python tools/verify_enemy_editor.py --editor editor/bazel-bin/darklantern64_project_editor.exe
python tools/verify_stream_authoring_preview.py

# Shared streaming state machine and every-tick host/MIPS functional replay
node tools/check_stream_coordinator.mjs --proof
python tools/verify_stream_replay_parity.py --evidence docs/evidence/stream-replay-parity.json

# Console streaming behavior and strict frame-stage budgets remain separate
python tools/verify_streaming_probe.py
python tools/verify_bafford_district_performance.py
```

These checks are developer tools rather than individual VS Code tasks. Emulator and editor checks open their own instances and require the corresponding installed dependencies. Inspect each command's report and exit status; a historical checked-in report does not verify later source changes. Original N64 and M64 validation remains a separate hardware step.

The [streaming coordinator guide](streaming-coordinator.md) describes ownership,
the synthetic failure/delay suite, model-checker dependencies and settled
replay boundaries. `--native-only` validates the fixed route without claiming
ROM parity; console budgets and menu/lifecycle checks run independently.

`python tools/editorctl.py inspect` reads the running project editor through the stable `.dev/editor/project/` queue, including after switching levels. Its bounded command interface supports content edits, save/build, captures and layout operations. Omit `--level` for everyday project use; that option selects the queue for an isolated diagnostic editor launched with a level argument. See the [editor reference](../editor/README.md) for argument files and the current operation contract.
