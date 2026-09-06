# First subsystem timing capture

Captured in Ares on 2026-09-05 after fixing audio startup. This is a measured emulator baseline, not original N64 or M64 performance acceptance. See [profiling](profiling.md) for collection and interpretation, including interrupt and graphics-queue limitations.

The input-only Lantern Store route completed the switch, door, stairs and relic objective without capture: 1,183 measured frames, 37 complete reporting windows, 918 audio callbacks, debug overlay off. The replay advances simulation by a fixed 1/60 second per frame; manual play uses elapsed time and can perform more simulation substeps in a slower frame. Startup and door-change spikes are included.

| Scope | Mean ms/frame | Worst observed ms/frame | Mean share of 33.333 ms budget |
| --- | ---: | ---: | ---: |
| Whole frame, including waiting | 31.004 | 132.064 | 93.01% |
| Triangle clipping/submission | 15.877 | 28.915 | 47.63% |
| Display-buffer waiting | 7.651 | 16.213 | 22.95% |
| Lighting | 2.628 | 88.259 | 7.88% |
| Model/view transforms | 1.615 | 1.711 | 4.85% |
| Normal HUD | 1.584 | 2.105 | 4.75% |
| Gameplay | 0.746 | 1.088 | 2.24% |
| Audio synthesis callback | 0.454 | 8.075 | 1.36% |
| Other/bookkeeping | 0.168 | 2.434 | 0.50% |
| Render setup | 0.102 | 0.157 | 0.31% |
| Initial scene cache | 0.088 | 103.996 | 0.26% |
| Input/replay preparation | 0.083 | 0.425 | 0.25% |
| Audio events | 0.008 | 0.484 | 0.02% |
| Debug HUD | 0 | 0 | 0% |

Per-scope maxima occur in different frames and must not be added together. The whole-frame percentage includes waiting; it is not CPU utilization. The broad triangle scope includes CPU clipping, projection, command generation and internal queue stalls, so this capture does not identify which of those suboperations dominates.

The next targeted optimization should investigate that triangle scope and the large static-relighting spike when the door changes. Initial cache construction costs about 104 ms once; door-driven relighting reaches about 88 ms in one frame. These observations support precomputed lighting contributions or bounded updates as future work, rather than assuming the average lighting time is sufficient. Richer textures should be evaluated against these costs using the [texture pipeline proposal](texture-pipeline.md).

## Capture provenance

- Scene source SHA-256: `d8e73eb67179c5fb649361277e7fae1d938bc362bf57545dae5ad0b8f1a2ef4e`.
- Tested replay ROM SHA-256: `1bd8759bdc70c36582a1e873aac32f800b9077d70dc1161832a60ef73fab5d53`.
- Raw log SHA-256: `1ce25b8140c8b0331986840e3260c0f5d6de00bfab893e2402374eb1c355460d`.
- Source settings SHA-256: `4b013206c69c12be9e0fdbd91b389e6c40b87816bb40835c6daadb713b18ce80`.
- Local immutable run: `.dev/ares/runs/5d7fae1868974d4b911fff203f2d86ee/`.
- Local report: `build/profile-baseline.json`; smoke evidence: `build/ares-smoke-profile-baseline.json`.

These hashes identify the actual capture, including its symbols, rather than whichever ROM was most recently built. Generated logs/ROMs are ignored by Git. Reproduce with `python tools/smoke_ares.py`, then run `tools/profile_report.py` on its printed 8 MiB log path. The 4 MiB startup gate also passed.

## Debug display cost

A separate replay of the same route with the debug display enabled completed 1,183 frames and 1,130 audio callbacks. After batching text into columns and caching formatted numbers, the debug HUD averaged **7.248 ms/frame**, down from **11.181 ms/frame** in the first implementation. The complete frame averaged 38.145 ms with the batched overlay visible. Keep the overlay hidden for representative gameplay captures; counters continue recording. These runs include different elapsed audio scheduling and graphics waits, so the isolated `debug_hud` measurement is more useful than attributing the entire frame-time difference to text.

The final overlay run is `.dev/ares/runs/24f244f005994031b421a377b57d2a67/`, replay ROM SHA-256 `b9f62e3d028a1ca365fc5cfb5d958dccb080cc1a0032aa55c16ac5dedd6717f7`. Its validated report is `build/profile-debug-overlay.json` and its smoke evidence is `build/ares-smoke-profile-debug.json`. The renderer's batched text path completed the entire route; manual visual inspection of the overlay remains pending.
