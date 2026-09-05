# DarkLantern64

DarkLantern64 is a first-person stealth game for Nintendo 64, ModRetro M64, and compatible emulators, inspired by *Thief: The Dark Project*. The platform's limitations will guide its art, spaces, and interacting gameplay systems.

Our development approach is to extend **LightEngine** into a productive content editor, compile its authored content into compact N64 assets, and run the game on our **libdragon fork**. The creation loop is part of the product: author, build, play, inspect, revise.

**Memory baseline: 8 MiB of RDRAM.** Original N64 hardware requires an Expansion Pak. M64 includes Expansion Pak functionality; see the [accepted memory decision](docs/decisions/0001-memory-baseline.md) for sources and validation requirements.

## Start here

| Document | Purpose |
| --- | --- |
| [Vision](docs/vision.md) | Game pillars, collaboration, and scope. |
| [Architecture](docs/architecture.md) | Editor/runtime boundary, content model, and existing foundations. |
| [Workflow](docs/workflow.md) | Authoring, compilation, iteration, and debugging. |
| [First playable](docs/first-playable.md) | A small encounter that proves the entire pipeline, with completion criteria. |
| [Memory decision](docs/decisions/0001-memory-baseline.md) | Accepted 8 MiB requirement and its consequences. |
| [Thief research](docs/research/thief-object-system.md) | Historical ideas and their proposed application. |

## Project status

Initial documentation, dated 2026-09-05. This repository does not yet contain game code, a content compiler, build targets, or a playable ROM. Implementation and platform validation remain pending.

The documents identify accepted decisions, proposed designs, and inspected existing capabilities separately. Proposed milestones are not completed work. Build instructions will be added with the project skeleton once they can be exercised.
