# Kaze's SM64 optimization work: lessons for DarkLantern64

Checked **2026-09-05** through public source, repository history, developer video metadata and our existing compiled ROM. **Cache behavior, data movement and repeated work should be explicit targets in our next performance pass.** Our current prototype measurements describe software we can improve, not a ceiling on the N64's capabilities.

## Which work is available?

Kaze's [2022 refactor overview](https://www.youtube.com/watch?v=t_rzYnXEQlE) and [2024 optimization discussion](https://www.youtube.com/watch?v=Ca1hHC2EctY) are useful starting points. His public [optimization-removal repository](https://github.com/KazeEmanuar/SM64-but-some-optimizations-are-removed) explicitly identifies itself as an incomplete demonstration. It is not established here as the complete or latest version of his engine. The linked demonstration patch is committed by robertkirkman; HackerSM64 also credits contributions by Wiseguy and others. Keep those attributions distinct.

This review inspected the code below. Video titles, descriptions and chapter metadata were accessible; full transcripts were not. Consequently, headline speed multipliers and detailed claims from third-party video summaries are not used as evidence.

## Concrete implementation lessons

| Public implementation | What it does | Application here |
| --- | --- | --- |
| [Fused transform construction/multiplication](https://github.com/KazeEmanuar/HackerSM64/commit/39f92391f800190091d581ff8caac98760655758) | A commit explicitly crediting Kaze computes transform × parent directly instead of building and then multiplying an intermediate matrix. | Reduce intermediate writes and repeated reads in a measured geometry path; preserve transform order and aliasing contracts. |
| [Shared vector routines](https://github.com/KazeEmanuar/HackerSM64/commit/50941e0559d484d3a1de76430ceab019a2fb8416) | Another Kaze-credited change replaces expanded vector macros with helper routines, including in raycast code. | Test whether smaller collision/query code offsets function-call overhead. |
| [Shared graph-node helpers](https://github.com/KazeEmanuar/HackerSM64/blob/be0d717982c0984c477cade66724190d785c1be6/src/game/rendering_graph_node.c#L428) | Common matrix-stack and display-list tail work is factored into `inc_mat_stack` and `append_dl_and_return`. | Inspect repeated hot code and compiler inlining; benchmark compact helpers against duplicated paths. |
| [Looped matrix multiplication](https://github.com/KazeEmanuar/SM64-but-some-optimizations-are-removed/blob/b46813647ec403a3542fb590b8383583eed57a7f/src/engine/math_util.c#L487) | The demonstration replaces expanded matrix expressions and temporary copying with compact loops. | Smaller code can be worth loop overhead; measure generated MIPS code, rather than assuming unrolling helps. |
| [Computed collision bounds](https://github.com/KazeEmanuar/HackerSM64/blob/be0d717982c0984c477cade66724190d785c1be6/src/engine/surface_load.c#L619) | Finds a scaled object's maximum squared vertex distance, then computes a padded radius and caches it. | Our model bounds already follow the broad principle. Extend conservative rejection to collision/light candidates so expensive tests only see relevant data. |
| [One-time collision setup](https://github.com/KazeEmanuar/SM64-but-some-optimizations-are-removed/blob/b46813647ec403a3542fb590b8383583eed57a7f/data/behavior_data.c#L2657) | The submarine collision load moves outside its repeated behavior loop. | Give static data an explicit lifetime. Our box proxies are already compiled, but their rotation terms are still recalculated during queries. |

These are techniques to evaluate, not a patch to transplant. The demonstration also removes LOD selection and other behavior; removing a matrix temporary changes the behavior when input and output alias. Its [diff](https://github.com/KazeEmanuar/SM64-but-some-optimizations-are-removed/commit/b46813647ec403a3542fb590b8383583eed57a7f) does not establish that removing culling, animation handling or LOD is generally correct. Test both the cost of a rejection mechanism and the work it saves. Our own bounds-culling study already shows a small baseline regression alongside a useful saving with many hidden models.

The CPU has **16 KiB instruction cache and 8 KiB data cache**. A shorter instruction sequence, a smaller executable, and a faster frame are different measurements. Likewise, precomputing a value trades arithmetic for storage and access; it needs an appropriate lifetime and layout. [Nintendo CPU documentation](https://ultra64.ca/files/documentation/online-manuals/man-v5-1/pro-man/pro03/03-05.htm)

Kaze's [sine-function comparison](https://www.youtube.com/watch?v=xFKFoGiGlXQ) explicitly covers lookup tables, paired sine/cosine and polynomial alternatives. That supplies a useful experiment list, but the available metadata does not establish a numerical winner for our compiler, angle range or accuracy requirements.

## What the local audit found

The [recorded ELF audit](kaze-local-audit.json) identifies the exact ROM, compiler and ELF hashes. This was read-only inspection, not a new timing run or a cache-miss measurement.

| Current observation | Proposed experiment |
| --- | --- |
| `dl_line_of_sight` contains actual calls to `sinf`, `cosf`, `fminf` and `fmaxf`; these were not all optimized away. | Cook/cache static collider rotation terms; reduce candidate boxes first. Compare validated internal interval helpers where their semantics permit it. |
| Player visibility and guard updates run inside the approximately 120 Hz motion loop. | Separate perception from motion and reuse unchanged light-state results; preserve immediate sound/door events and chase responsiveness. |
| `dl_render_frame` occupies 9,084 code bytes; `dl_game_update` 5,096. | Inspect hot blocks and inlined helpers before trying selective outlining or compiler options. |
| Reserved world-vertex and camera-vertex arrays are 49,152 bytes each; the face cache is 131,072 bytes. | Compare batch-local geometry processing and smaller hot records during the RSP backend experiment. |

Function sizes include cold branches and inlined code; their sum does not establish simultaneous cache residency. Array sizes describe reserved capacity, not bytes touched every frame. The renderer only visits authored entries. These observations identify candidates for measurement, not proven causes or predicted gains.

Keep the finite-input checks and collision tolerances that protect gameplay. A global fast-math switch or approximate trig replacement can change NaN handling, camera motion and contact behavior. Prefer an exact removal of repeated work first; give any approximation a measured error bound and adversarial movement tests.

## Measure the processor that is waiting

The related [F3DEX3 project](https://github.com/HackerN64/F3DEX3) is authored by **Sauraen**, with Kaze credited for suggestions and testing. Its larger vertex batches, repeated texture-load suppression and optional planar occluder are useful references for reducing transfers and redundant work. They are not automatically compatible with our libdragon/Tiny3D path.

Its [profiling counters](https://hackern64.github.io/F3DEX3/counters.html) distinguish DMA stalls, RDP FIFO stalls, lit vertices, input/output triangles and overlay loads. That is the direction to extend our current CPU-phase profiler when evaluating an RSP backend. Its [performance analysis](https://hackern64.github.io/F3DEX3/performance.html) also documents a tradeoff where compressed triangle commands save traffic but add RSP work. A local improvement to one resource can lose overall.

Run controlled A/B builds with the same content, gameplay, audio and camera route. Record code/data size, completed frame time, RSP work and queue stalls; keep rendering and interaction regression checks. Cache and memory-layout gains require original N64 and M64 confirmation. Ares remains useful for iteration, but elapsed emulator timings alone do not validate every hardware memory optimization.

The resulting priorities are: **local spatial queries and reusable static transforms; perception scheduling; an RSP geometry comparison with material batching; then measured code-layout and math changes.** Retain the [scale study](scaling-guide.md) as the baseline and the [modern graphics study](modern-n64-graphics.md) as effects to budget after these improvements.
