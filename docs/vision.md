# Vision

Status: initial direction, 2026-09-05. The [memory baseline](decisions/0001-memory-baseline.md) and [full 3D world](decisions/0002-full-3d-world.md) are accepted; specific mechanics and technical choices below remain subject to iteration.

## The game

DarkLantern64 is a first-person stealth game inspired by *Thief: The Dark Project*, designed for N64 hardware with 8 MiB of memory. ModRetro M64 and compatible emulators are also targets.

The environment is fully 3D, built from models with arbitrary placement and varying heights. Levels need not follow a grid. This applies to the placeholder prototype as well as the eventual artwork.

The desired experience is tension through observation and deliberate action: reading a space, listening, judging exposure, and using a small set of understandable tools to reach a goal. Light, sound, materials, and guards should form a coherent world that rewards experimentation.

Audio is a primary gameplay information channel. Clear and muffled voices, footsteps on different materials, movement rhythms, and equipment sounds should reveal useful clues about nearby entities and spaces. Player playback and AI hearing should derive from the same world events and compatible acoustic rules. The proposed implementation is described in [Audio design](audio-design.md).

The N64's memory, rendering, audio, and controller constraints will inform the design from the beginning. Readable silhouettes, carefully composed spaces, and useful sound cues are promising directions to explore with the art. Final visual style, setting, story, and campaign structure remain open.

## Game pillars

- **Stealth the player can understand.** Visual and audible cues should explain danger, safety, and changes in a guard's awareness. Unexpected detection should be diagnosable during development.
- **Consistent interactions.** Reusable rules for doors, controls, surfaces, and perception should support different approaches to an encounter.
- **Spaces designed for the hardware.** Geometry, sightlines, resident assets, and active simulation should fit measured budgets. Level design and optimization develop together.
- **A focused first playable.** Prove an enjoyable small encounter and the ability to revise it before expanding the content library.

## The editor is a production tool

LightEngine is our own technology and can be adapted wherever useful. Its role is to let an artist or designer create and modify content with immediate, relevant feedback.

The desired workflow supports reusable object types, traits, visible relationships, and editable gameplay parameters. A programmer implements a capability once; a designer can then reuse it in many situations. Editor feedback must explain unsupported assets and budget overruns in terms of the content that caused them.

Fast desktop previews support experimentation. Playable ROMs and hardware measurements establish whether an idea works on the target. The [workflow](workflow.md) connects these activities.

## Collaboration and scope

The project owner leads art and game design. Technical work covers LightEngine extensions, the libdragon integration, runtime systems, content conversion, build tooling, and debugging. Both sides use the same small playable encounters to evaluate changes.

The [first playable](first-playable.md) is the initial scope boundary. A full campaign, multiplayer, elaborate combat, unrestricted scripting, and broad engine generalization are deferred. They are not prerequisites for proving the game or its creation loop.

The first success is a complete loop: a meaningful change made in the editor appears in a playable N64 ROM, with enough feedback to improve the next revision.
