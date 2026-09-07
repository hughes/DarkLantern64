# DarkLantern64

DarkLantern64 is a first-person stealth game for Nintendo 64, ModRetro M64, and compatible emulators, inspired by *Thief: The Dark Project*. The platform's limitations will guide its art, spaces, and interacting gameplay systems.

Our development approach is to extend **LightEngine** into a productive content editor, compile its authored content into compact N64 assets, and run the game on our **libdragon fork**. The creation loop is part of the product: author, build, play, inspect, revise.

**Memory baseline: 8 MiB of RDRAM.** Original N64 hardware requires an Expansion Pak. M64 includes Expansion Pak functionality; see the [accepted memory decision](docs/decisions/0001-memory-baseline.md) for sources and validation requirements.

**Full 3D:** reusable model assets, arbitrary XYZ transforms, varying heights, and freely placed geometry. See the [accepted world decision](docs/decisions/0002-full-3d-world.md).

**Creating levels?** Start with the [creator quickstart](docs/creator-guide.md): launch the project editor, manage levels in its **Levels** panel, and use **Play level** or **Play game menu** to test your changes.

**Creating models?** The [Blender asset guide](docs/blender-assets.md) covers the editable loot set, exporter, material budget, prefab placement, and a direct launch at the courtyard's loot counter.

**Animating characters?** The [guard animation study](docs/guard-animation-prototype.md) covers the editable Blender guard, five Actions, connected joints, editor playback/head attention and measured N64 costs. Its clips compress to 8.61 KiB. The default renderer now uses Tiny3D on the RSP for model transforms and rigid skeletal deformation.

The two-guard workshop [sustains every native refresh in Ares](docs/guard-renderer-performance.md): 1,800 fresh frames over 30 seconds, with no repeats and a 16.276 ms maximum CPU work sample. Original hardware validation remains pending.

## Build and play

**Full 3D prototype:** the model compiler, desktop editor, and both ROM variants build. The editor's XYZ/rotation save round trip and Ares startup/mission replay have passed. Original N64 and M64 validation remains pending; this is still placeholder content and a prototype rendering/audio backend.

The current development host is Windows. Install Python 3.11+ with Pillow (`python -m pip install Pillow`), the Windows libdragon SDK from our fork (default `C:\n64-toolchain`, or set `N64_INST`), and Ares. Open Ares once so it creates its settings file. MSYS2/MinGW GCC is needed for host simulation tests.

Prepare the pinned Tiny3D dependency once with `python tools/build_tiny3d.py --fetch`. This explicitly fetches its source and builds the library locally; ordinary ROM builds require that checkout and never fetch it or install files into the SDK. See the [Tiny3D setup notes](tools/tiny3d/README.md) for its MSYS2 build requirements. `python tools/setup_editor.py --tiny3d` prepares both editor and renderer dependencies instead.

VS Code has two project tasks: **DarkLantern64: Launch editor** and **DarkLantern64: Launch game**. The game task (also **Ctrl+Shift+B**) builds saved levels included in `content/level_bundle.json` and opens the level selector. Save editor edits first when using that task. Within the editor, **Play level** saves and cooks the current level before a direct launch; **Play game menu** saves it before building the bundle. These game launches use the RSP renderer by default; no additional task or editor setting is needed.

```powershell
python tools/build.py --bundle content/level_bundle.json --run
```

Direct launches, Blender export, profiling, captures and verification remain available as commands in the [developer tools guide](docs/developer-tools.md).

For a rendering comparison, add `--renderer cpu` to a `tools/build.py` command. The CPU reference and default `--renderer t3d` use separate ROM/build outputs.

N64 controller: the **stick looks left/right/up/down** (push up to look up); **C-Up/Down move forward/backward**, and **C-Left/Right strafe**. Z crouches, L jumps, A interacts, B makes noise, R restarts, and Start toggles debug. The D-pad also provides digital look controls.

Keyboard controls in the project-local Ares settings: W/S move, A/D strafe, arrow keys turn and look, Z crouch, Space jump, E interact, N make noise, R restart, Tab debug. Ares settings are copied to `.dev/ares`; the installed settings file is left untouched. Set `ARES_EXE` if Ares is installed somewhere other than `%LOCALAPPDATA%\ares`.

For the LightEngine editor, also install Bazelisk, the Visual Studio C++ build tools, and Vulkan SDK (`VULKAN_SDK`). The first build downloads desktop dependencies and compiles the engine.

```powershell
python tools/setup_editor.py # fetch the pinned LightEngine revision if missing
.\build.ps1 -Editor -Run
```

The engine revision is recorded in [dependencies.json](dependencies.json). Project layouts and the saved-tab startup fix merged through [PR #14](https://github.com/hughes/LightEngine/pull/14) and [PR #15](https://github.com/hughes/LightEngine/pull/15). Project level registration and guarded Open/Save/Close merged through [PR #16](https://github.com/hughes/LightEngine/pull/16). Fixed-topology character preview updates and orbit-focus synchronization are published in [PR #17](https://github.com/hughes/LightEngine/pull/17), pending review; this project pins its tested commit. Setup checks an existing checkout without resetting it. See the [editor guide](editor/README.md) and [workflow](docs/workflow.md) for editing and automation.

## Start here

| Document | Purpose |
| --- | --- |
| [Creator quickstart](docs/creator-guide.md) | A short hands-on guide for artists and level/game designers. |
| [Developer tools](docs/developer-tools.md) | Command recipes for Blender export, direct launches, profiling, captures and verification. |
| [Project workflow verification](docs/project-workflow-verification.md) | Tested level management, editor game launches, and the horizontal camera correction. |
| [Level select and test starts](docs/level-select.md) | Bundle levels into one ROM, switch during testing, or launch directly into a named starting state. |
| [Enemy authoring](docs/enemy-authoring.md) | Place multiple enemies, choose code-defined types, and author independent patrols or sentry posts. |
| [Guard animation prototype](docs/guard-animation-prototype.md) | Edit/export the Blender guard, preview clips and head attention, and inspect measured compression and runtime costs. |
| [Character animation architecture](docs/character-animation.md) | Implemented foundations and proposed N64 deformation, audio events, memory and editor extensions. |
| [Vision](docs/vision.md) | Game pillars, collaboration, and scope. |
| [Architecture](docs/architecture.md) | Editor/runtime boundary, content model, and existing foundations. |
| [N64 hardware guide](docs/n64-hardware-guide.md) | Processors, memory/bandwidth, graphics features, lighting, and seamless spaces. |
| [Modern N64 graphics](docs/modern-n64-graphics.md) | Native HDR/bloom, material tricks, shadows and game evidence versus emulator enhancements. |
| [Audio design](docs/audio-design.md) | Output fidelity, acoustics, material cues, positional sound, and AI hearing. |
| [Audio memory planning](docs/audio-memory-planning.md) | Scenario estimates, retained buffers, transition peaks, and budget assumptions. |
| [Game profiling](docs/profiling.md) | Record Ares subsystem timings and compare frame-budget costs. |
| [Mission scale](docs/scaling-guide.md) | Measured guard, room, loot and light costs; culling and larger-mission architecture. |
| [Kaze performance research](docs/kaze-performance-notes.md) | Public SM64 optimization techniques mapped to our source and compiled-code audit. |
| [Texture pipeline](docs/texture-pipeline.md) | Cook StreetLight textures from editor materials into the N64 ROM. |
| [Night contrast study](docs/night-contrast.md) | Moonlight, warm lamps and dark cover in a continuous outdoor yard. |
| [Workflow](docs/workflow.md) | Authoring, compilation, iteration, and debugging. |
| [First playable](docs/first-playable.md) | A small encounter that proves the entire pipeline, with completion criteria. |
| [Memory decision](docs/decisions/0001-memory-baseline.md) | Accepted 8 MiB requirement and its consequences. |
| [World decision](docs/decisions/0002-full-3d-world.md) | Accepted full 3D model and level requirements. |
| [Thief research](docs/research/thief-object-system.md) | Historical ideas and their proposed application. |

## Project status

Prototype implementation in progress, dated 2026-09-06. The repository now contains a portable gameplay module, N64 runtime, content compiler, LightEngine project editor, and build/debug tools. Placeholder mesh assets are authored in `content/`; generated outputs are kept in ignored `build/` and `.dev/` directories.

The default 3D renderer uses Tiny3D on the RSP for model transforms, skeletal deformation and clipping, followed by RDP hardware triangles/depth. CPU code samples animation clips and calculates lighting; the CPU renderer remains available for reference comparisons. The courtyard adds colored vertex lighting, occlusion, emissive surfaces and fog, with source texture pixels cooked into the ROM. The guard workshop uses connected one-influence geometry and bounded head attention. Collision uses upright rotated boxes; sloped mesh collision, richer navigation, and SDK provisioning remain future work. Original N64 and M64 validation is **pending**. See [First playable](docs/first-playable.md) for acceptance details.
