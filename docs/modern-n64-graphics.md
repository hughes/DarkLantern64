# Modern graphics techniques on N64

Research checked **2026-09-05** for DarkLantern64's original N64 + Expansion Pak / M64 / accurate-emulator target. This is source-based feasibility research; we have not benchmarked these external implementations on our hardware or integrated them into the game.

**Several modern-looking techniques are practical candidates for a game. Their usefulness depends on choosing restricted effects and preparing content for them.** Native HDR/bloom is real; general moving, shadowed PBR lighting is a different and much larger workload. A demonstration establishes that an effect can execute. A game budget also has to include interaction, animation, audio, difficult camera angles and stable frame delivery.

## What “HDR on N64” can mean

Keep three different claims separate:

| Claim | Where the work happens | Relevance here |
| --- | --- | --- |
| Higher-range scene lighting, exposure and bloom in a ROM | N64 CPU/RSP/RDP and RDRAM | A native effect we can investigate |
| Ray tracing, high-resolution rendering or replacement materials in a PC renderer | Host GPU and host memory | An optional future PC presentation path |
| HDR television output or display filters | Emulator/output hardware after game rendering | Does not give the game extra lights or shadow computation |

HDR scene arithmetic, the halo called bloom, and HDR display signalling are separate features. A dark game can have bright, convincing lamps without any of them; our existing contrast study already demonstrates authored warm/cool separation and emissive surfaces.

## Native HDR and bloom: a serious experiment

Max Bebök / HailToDodongo's [Tiny3D HDR and bloom demonstration](https://www.youtube.com/watch?v=XP8g2ngHftY), published July 20, 2025, is a likely match for the remembered footage. The [example explanation](https://github.com/HailToDodongo/tiny3d/tree/main/examples/24_hdr_bloom) describes an integer encoding: render into RGBA32 at one eighth normal brightness, reserving headroom above ordinary white; custom RSP code applies exposure and resolves to RGBA16 for display. It is not a floating-point framebuffer or HDR10 output. Bloom downsamples the image, blurs it, and combines it during the resolve.

The [allocation code](https://github.com/HailToDodongo/tiny3d/blob/main/examples/24_hdr_bloom/src/postProcess.cpp) uses padded buffers. These are **calculated pixel-storage costs**, excluding allocator metadata, commands, stack and other game data:

| Allocation per postprocessor | Calculation | Bytes |
| --- | ---: | ---: |
| HDR scene | 320 × 244 × 4 | 312,320 |
| Two blur buffers | 2 × 80 × 64 × 4 | 40,960 |
| Total | | 353,280 = 345 KiB |

The [example main loop](https://github.com/HailToDodongo/tiny3d/blob/main/examples/24_hdr_bloom/src/main.cpp) constructs three postprocessors: **1,059,840 bytes, about 1.01 MiB extra**, in addition to ordinary display/depth storage. The visible bloom image is 80×60. That implementation defaults to four blur steps, disables geometry AA, and resolves a previous rendered frame. Memory, delayed presentation and antialiasing behavior therefore belong in the comparison, as well as appearance.

A full 320×240 resolve reading RGBA32 and writing RGBA16 moves at least 460,800 pixel bytes; at 30 fps that is **13.824 MB/s**, calculated before blur, scene rendering, depth traffic or scanout. This is a traffic floor, not a measured throughput requirement or frame-time prediction. No credible standalone effect time or representative gameplay headroom was established from the reviewed material.

There is evidence beyond an isolated sample: [Pyrite64](https://github.com/HailToDodongo/pyrite64) exposes HDR/bloom in an editor and runtime with scenes, collision and audio. Its maintainers still describe the project as early development. That makes the implementation worth studying as reusable engineering, without treating it as proof that our particular mission will fit.

**Our assessment:** try restrained bloom after recovering baseline frame time. Keep dark stone and shadowed guards below the bloom threshold; concentrate the effect on lamp glass and the brightest highlights. Compare fixed exposure first. Automatic exposure can make darkness ambiguous or visibly pump when a lamp enters the view. Render the HUD after the effect. Our current renderer clamps cached display colors, so adding a postprocess alone cannot recover lost lighting range: the material/color preparation must preserve the intended headroom too.

A smaller alternative worth designing is an authored emissive mask and glow, without the full HDR scene buffer. For example, two unpadded 80×60 8-bit masks contain **9,600 pixel bytes** in total. That is a proposed storage calculation, not an implemented bloom system or its complete memory budget. Occlusion, padding, blur, compositing and command storage still need design and measurement. A few depth-aware lamp halos are an even smaller first comparison.

## Reflections, Fresnel and mipmaps: useful material tools

Tiny3D's [environment-mapping example](https://github.com/HailToDodongo/tiny3d/blob/main/examples/12_uv_gen/main.c) generates texture coordinates from normals and uses supplied reflection images, including blurred variants for rougher material. Its [Fresnel example](https://github.com/HailToDodongo/tiny3d/blob/main/examples/21_fresnel/main.c) uses light calculations to produce an angle-dependent alpha value for the combiner. The cheaper directional approximation has limitations on flat surfaces; a camera-position point-light variant is evaluated per vertex. Some materials require more than one pass.

**Our assessment:** these are strong candidates for brass fittings, loot, lamp glass and restrained wet highlights. A supplied environment image does not automatically reflect a moving guard. Use that approximation where it reads well; reserve actual secondary scene views for a deliberately bounded mirror or water surface. The result can suggest material roughness without reproducing a modern PBR pipeline.

[Mipmaps](https://github.com/HailToDodongo/tiny3d/blob/main/examples/20_mipmaps/main.c) are an original RDP capability, not a recently discovered shader effect. Their value here is reduced distant texture shimmer and more stable movement. Budget the additional texture levels; the example specifically cautions against treating mipmapping as a rendering-speed optimization.

## Palette shading and normal detail: promising, with narrow limits

Pekka Väänänen's [Castello breakdown](https://30fps.net/pages/palette-lighting-tricks-n64/) (May 17, 2025; June 4 update) explains CPU shading of small palettes that jointly represent surface color and normal direction. Updating palette entries changes many texels together. Baked vertex information supplies ambient color and sun visibility. The impressive result has explicit limits: the final demo uses greyscale textures, directional illumination, approximate specular shading and discontinuities between orientation groups. The author also reports a crash-prone PAL ROM. It is a research demonstration, not a general material system ready to adopt.

**Our assessment:** a useful candidate for moonlit brick, carved stone or selected ornaments. It does not solve a guard's lantern illuminating arbitrary nearby surfaces. A first LightEngine experiment should cook one material into palette indices plus normal/color metadata and compare it on flat, angled and curved geometry. Our cooker currently emits RGBA16 textures, so this requires a deliberate CI texture/palette extension. Preserve material color and normal quality in separate diagnostics; a small palette that preserves color well can still damage the lighting response.

## Shadows: choose what the player needs to see

James Lambert's [native shadow demonstration](https://github.com/lambertjamesd/n64graphicsdemo) uses a shadow-volume variant requiring three subject passes and two volume passes. It also demonstrates a separate 64×64, 8-bit projected character shadow. The former is strong evidence that the effect is possible; the latter is a much more focused starting point for a character standing on a receiving surface. Neither establishes a supported count of arbitrary moving shadowed lamps.

**Our assessment:** prioritize one readable guard shadow or a lantern silhouette crossing a doorway over scene-wide shadow volumes. A 64×64 8-bit mask is only **4,096 pixel bytes**, calculated, but drawing the caster, projecting onto receivers and handling occlusion still cost time. Test walls, steps, overlapping receivers and camera crossings: a cheap projected mask must not bleed through a wall or float across stairs. Gameplay detection should use the shared light/occlusion model; the visual shadow approximation must not silently become the AI's ground truth.

## High-resolution textures: a different rendering architecture

Tiny3D's [large-texture example](https://github.com/HailToDodongo/tiny3d/blob/main/examples/22_bigtex/src/main.cpp) performs deferred texture lookup: the RDP produces UV/material information, CPU/custom RSP code fetches texture data from RAM, and a separate shading result is combined later. It demonstrates 256×256 textures rather than simply increasing the size of an ordinary TMEM upload. It requires the Expansion Pak. The source's approximately 60 fps observation concerns a skybox-only view, not the full scene or a busy game.

Its [memory implementation](https://github.com/HailToDodongo/tiny3d/blob/main/examples/22_bigtex/src/utils/memory.cpp) allocates three RGBA16 color, three RGBA32 UV, three RGBA16 shading and one RGBA16 depth surface at 320×240: **1,996,800 raw pixel bytes, about 1.90 MiB**, calculated before textures and special memory-layout reservations. This figure includes its ordinary color/depth surfaces; the HDR figure above is additional storage. They describe different example pipelines and should not be added as if they formed a tested combination.

**Our assessment:** impressive research, but a larger commitment than cooking our StreetLight images into ordinary compact textures. First test palette formats, mipmaps, material grouping and selective detail. If larger textures still provide substantial value at 320×240, compare the deferred renderer on the same moving camera and audio workload before changing our asset contract.

## Evidence from games, not only demonstrations

| Project | What is established | What it does not establish |
| --- | --- | --- |
| [Portal64: Still Alive](https://github.com/mwpenny/portal64-still-alive) | The project reports seventeen completed chambers with functional portals, physics, dialogue, lighting and reflections. | A complete released demake or unrestricted dynamic shadow lighting. Its [point-light documentation](https://github.com/mwpenny/portal64-still-alive/blob/2f1f82e32218db030c6f4a953d02ccd3242c4d50/documentation/levels/level_objects/point_light.md) specifies baked vertex colors, not runtime point lights. |
| [Junkrunner64](https://github.com/lambertjamesd/n64brew2025/releases/tag/v3.2) | A released game-jam project with continuing gameplay, art and stability updates. Its [renderer](https://github.com/lambertjamesd/n64brew2025/blob/420c192a33f7a4ff45e79cc293e5c418a07bbb28/src/overworld/overworld_render.c) combines a scaled, sorted distant layer with depth-tested nearby scenery; [streaming](https://github.com/lambertjamesd/n64brew2025/blob/420c192a33f7a4ff45e79cc293e5c418a07bbb28/src/overworld/overworld_load.c) separates visual and actor tiles. | A measured Skyrim-equivalent world or simulation load; no reliable sustained FPS budget was established here. |
| [Xibalba 64](https://phoboslab.org/log/2026/08/xibalba64-making-of) | The developer's August 4, 2026 account describes a physical release and reports stable 60 fps using libdragon/Tiny3D, batching, indexed textures and visibility optimization. | A capacity proxy for DarkLantern64: its world is explicitly flat and grid-based. It demonstrates the stack's game viability, not our fully 3D architecture or HDR performance. |

This supports a qualified **yes** to game viability. Bounded secondary views, baked fidelity, material tricks and carefully scheduled scenery have game evidence. The most elaborate shading demonstrations still need their own integration tests. A game can benefit from a technique even when using it on only a few objects or in a particular room.

## Emulator enhancements use a different budget

[RT64](https://github.com/rt64/rt64) renders through modern host APIs and supports enhancements such as higher resolution and frame interpolation in native ports. Its current public README still lists path tracing and emulator-plugin integration as work in progress. A ray-traced showcase or experimental branch should not be confused with a generally available plugin, or with an N64 ROM performing those calculations.

[ParaLLEl-RDP's developer explanation](https://www.libretro.com/index.php/parallel-rdp-how-the-upscaled-rendering-works/) describes Vulkan rendering at native and enhanced resolutions while preserving the memory behavior the emulated CPU observes. This is valuable presentation work, but the extra samples and memory belong to the host GPU.

Our inference is straightforward: these approaches could support an enhanced PC edition later. They cannot establish original-hardware fidelity or timing. For a stealth game, a PC renderer must also retain the same gameplay light state; visually adding a lamp while guards continue using the unmodified ROM's exposure model would be misleading.

## What would make an effect acceptable for DarkLantern64?

Use the same courtyard, camera route and gameplay recording for every comparison. Capture both the ordinary framebuffer and final display appearance where possible. Measure baseline versus effect with normal audio, then with voices, footsteps, multiple actors and a door/light change. Our [scale study](scaling-guide.md) currently places the courtyard around 50 ms/frame, already above the proposed 33.33 ms target; that is a reason to fix geometry/query costs before spending on postprocessing.

| Candidate experiment | Accept if | Reject or reduce if |
| --- | --- | --- |
| Small lamp halo, then low-resolution bloom | Light sources feel brighter while cover and silhouettes stay readable | Dark regions lift, image smears, or input latency grows noticeably |
| Moonlit palette material | Relief improves at useful viewing distances with stable motion | Palette bands/facets distract, or material variants multiply excessively |
| Selected reflective material | Metal/glass catches useful highlights with bounded draw cost | Large wet surfaces require repeated expensive scene rendering |
| One projected guard shadow | Position and movement become easier to perceive | Receiver errors reveal the approximation during ordinary play |
| Larger texture or streamed scenery | Detail/scale improves while worst doorway/turn remains smooth | Uploads, decompression or residency overlap cause stalls or audio disruption |

Record extra resident bytes and peak overlap, whole-frame times and worst spikes, RSP occupancy alongside audio, RDP completion/queue stalls, material uploads, and input-to-image delay. A timer around asynchronous command submission does not measure completed effect work. Repeat on original N64 and M64; emulator timing is preliminary evidence.

The intended order is the RSP geometry comparison and spatial queries already proposed; better cooked material/light data; a selected shadow or reflective material; then an HDR/bloom experiment with an explicit quality and latency budget. LightEngine remains our authoring tool. External projects provide techniques and source to evaluate within that pipeline.
