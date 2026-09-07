# Profiling the game in Ares

The ROM records a small breakdown of **emulated elapsed time per frame**. Use it to find expensive systems and compare the same scene before and after a change. These measurements describe Ares's timing model; original N64 and M64 measurements are still required before treating the numbers as hardware performance.

The [first measured baseline](profiling-baseline.md) records the automated route and its capture hashes.

## Record and read a session

1. Close the previous game window, then use the editor's **Play level**, or run **DarkLantern64: Launch game** in VS Code and select a saved level. `python tools/build.py --run` still launches the saved Lantern Store directly from the repository folder.
2. Play through the area you want to measure for several seconds. **Start** on the controller, or **Tab** on the keyboard, toggles the debug display. Profiling records continue with debug off.
3. In a terminal at the repository root, run:

   ```powershell
   python tools/profile_report.py
   ```

The report follows `.dev/ares/latest-session.json` to the latest play session's log, prints average and maximum milliseconds, and writes `build/profile-report.json`. New sessions have separate logs under `.dev/ares/sessions/`. Older setups without a session pointer fall back to `.dev/ares/manual-session.log`. You can read a log while the game runs; unfinished trailing records are excluded. Run the command again to refresh. Use `--output` to preserve a report under a separate filename.

For an archived or automated-replay log:

```powershell
python tools/profile_report.py .dev/ares/runs/YOUR-RUN/smoke-8mb.log --output build/profile-replay.json
```

Run `python tools/smoke_ares.py` for a fresh build, the 4 MiB startup gate and the 8 MiB input-only route. It prints each archived log path and writes `build/ares-smoke.json`. Add `--debug-overlay` to measure the same route with the overlay visible. Each run uses an isolated settings copy with physical input disabled; your play-session mappings are retained. The replay uses a fixed simulation step of 1/60 second, so its gameplay cost is not identical to manual play when frame times differ. `python tools/build.py --run --debug-overlay` starts a manual session with the overlay already visible.

The JSON includes the source path and SHA-256 of the exact log bytes read, complete window/frame counts, raw tick totals, callback count, debug states, and the converted times. Compare captures with the same ROM/content, camera path, emulator settings, and debug state. Debug-on and debug-off frames are identified, but a report containing both aggregates them together; use separate sessions for clean comparisons.

## What the scopes mean

| Scope | Measured work |
| --- | --- |
| `input` | Controller polling and input preparation. |
| `gameplay` | Movement, collision, guard AI, sight, hearing, and interactions. |
| `audio_events` | Preparing and dispatching game sounds, plus related event logging. |
| `audio_mix` | Audio callback work, counted separately even when it interrupts another scope. |
| `cache` | Initial static geometry/lighting cache construction when a level is first rendered. |
| `transforms` | Model/view vertex transformations. |
| `lighting` | Dynamic lighting and static relighting when the door changes. Initial cached lighting is charged to `cache`. |
| `triangles` | Clipping and triangle command submission. |
| `hud` | Normal game interface. |
| `debug_hud` | Optional debug interface and minimap. |
| `render_setup` | Graphics setup and remaining explicitly grouped renderer work. |
| `display_wait` | Waiting to acquire a display buffer. |
| `other` | Frame time outside the named scopes, including instrumentation overhead. |

The CPU's count register advances at half the CPU clock: **46,875,000 timer ticks per second** on this target. The report uses the rate emitted by the ROM, never the host computer's clock. These are elapsed stopwatch measurements, not instruction counts or a CPU-utilization percentage.

**Average ms** is total ticks divided by all recorded frames, including frames where a scope does no work. Aggregation is frame weighted even when windows have different lengths. **Max ms** is the worst observed per-frame total for that scope, not a percentile or the slowest individual function call. Scope maxima may occur in different frames and must not be added together. Startup cache construction and door-triggered transform/lighting updates remain in the data so their spikes are visible.

**% budget** compares the average with the ROM's configured budget: **16.667 ms at 60 fps** by default (`DL_PROFILE_TARGET_FPS`). `--target-fps 30` changes a report's comparison budget without changing the ROM. Older logs retain their original 30 fps budget unless overridden. **% elapsed** compares it with the measured average whole frame, including `display_wait`. Every frame's exclusive scope totals add up to its elapsed time. A large wait is different from expensive gameplay; a scope above 100% of the budget already exceeds the target by itself. New JSON uses `percent_of_frame_budget`; the legacy `percent_of_30fps_budget` field continues to mean exactly 30 fps for older consumers.

Version 2 logs retain each frame's exact elapsed ticks and **work ticks** (elapsed minus exclusive `display_wait`). Reports show nearest-rank p50, p95, p99, maximum, and the count exceeding the chosen budget. Work includes normal audio, logging and internal graphics queue stalls. The fixed 128-frame sample buffer covers the approximately one-second reporting window at the target's display rate. If it overflows, aggregate timings remain readable, but exact distributions are marked unavailable and performance verification fails. Report formatting uses bounded integer conversion and a 512-byte text buffer written through libdragon's existing debug output hook. This preserves every record and sample while avoiding repeated formatted-output parsing and line flushing; its full cost remains included in `other`.

## Practical limits

The instrumentation subtracts the timed audio callback body from the interrupted scope and assigns it to `audio_mix`. Callback marker/call overhead and interrupt dispatch remain outside that measured body. Other interrupt work remains charged to whichever scope it interrupts. Profiling itself adds overhead; the debug display adds further work under `debug_hud`. Exact frame samples occupy 1,024 bytes of fixed storage, in addition to bounded totals, maxima, VI counters and report text. There are no per-frame allocations. Installing the VI observer once at display initialization allocates one libdragon callback-list node. The debug display also retains bounded text buffers; its numeric text updates once per reporting window.

The initial measurement exposed an audio startup bug: installing the callback alone did not start the installed SDK's AI queue. Startup now calls `audio_write_silence()` once to prime it. Automated profiling requires a nonzero callback count; zero is a missing-work indication, not evidence that mixing is free.

RDP commands execute asynchronously. Internal queue stalls remain in the scope submitting commands; the profiler inserts no forced graphics waits. Consequently, `triangles` is CPU-side preparation/submission time including any existing stalls, **not RDP execution time**. `display_wait` is not a complete GPU measurement either. Ares host CPU/GPU usage and wall-clock speed are separate metrics.

Logs use numbered windows with an explicit end marker and all 13 scopes. The report rejects missing/duplicate scopes, inconsistent counts or totals, changed timer rates, and duplicate/skipped windows. Only an unfinished trailing window or line is ignored. An older ROM without profiling records produces a clear error instead of a misleading empty report.

## Verify two visible guards at 60 fps

```powershell
python tools/verify_guard_performance.py --renderer t3d --visibility-report docs/evidence/guard-60fps-visibility.json
```

Use `--rom PATH --manifest PATH` to measure an already built ordinary ROM without rebuilding. Output defaults to `build/guard-60fps/performance-verification.json`; use a separate `--output` for comparisons. The older six-window guard baseline and `build/guard-60fps/baseline-two-guards.json` remain separate. `--log PATH` performs diagnostic analysis of an existing log; it cannot independently certify ROM provenance.

The verifier uses the authored `two-guards` workshop start, ordinary audio, lights, HUD, patrol and sentry. It excludes three complete warmup windows and measures at least 30 seconds. Every work sample must fit **1000/60 ms**, and every observed native VI scan must show a new framebuffer. Both animated guard models must be submitted on every measured frame. Active procedural head-attention overlays are counted separately in the range 0–2; quiet patrol and sentry behavior can legitimately record zero, so this run does not benchmark alerted head tracking. `geometry_mode=0` counts CPU post-clip nondegenerate triangles and separately reports fully framed models. `geometry_mode=1` counts bounds candidates and triangles submitted to the RSP; full-body coverage is unavailable in this mode, not measured as zero. A partially clipped guard still meets the two-visible-guards workload when the image review confirms it is visible. Patrol movement, the stationary sentry, source dependencies, build flags and exact ROM/log hashes are checked. Shorter `--windows` runs are useful diagnostics but cannot pass the 30-second gate.

The VI observer reads the hardware `VI_ORIGIN` register once per VI interrupt. It uses the game's non-interlaced output with at least two buffers; unchanged origin means a repeated scanned image. Libdragon prepends callbacks, so registration after `display_init` observes the previous scan just before the display callback changes origin. That introduces a known one-field observation delay. It does not force synchronization, enqueue another frame, or confuse `display_show` requests with presentation. The observed native NTSC rate may be about 59.94 Hz rather than mathematically 60.000 Hz; the report preserves that rate and requires one fresh image per VI. A callback interval above 1.5 native VI periods also fails verification, preventing a long interrupt blackout from hiding a repeated scan. Ares's host monitor refresh and original-hardware performance remain separate questions.

Frustum and submitted-triangle counters cannot prove that another surface did not occlude a guard. The required visibility review therefore cites separately captured framebuffer images from the same source, camera and renderer; capture runs are excluded from timing. Its JSON format is documented in `visibility_evidence()` in `tools/verify_guard_performance.py`. An explicit reviewer confirms both entity IDs are visible, and image/content hashes preserve that evidence. Missing visual evidence produces a failed check rather than an assumed pass.

## Repeatable scale experiments

Run `python tools/scale_study.py --compare-culling` to measure guard/light CPU workloads and eight render fixtures with model bounds rejection enabled and disabled. For a shorter diagnostic, use `--micro-only`; for just the hidden-room comparison, use `--skip-micro --cases baseline hidden12 --compare-culling`. These developer commands keep the editor's task list limited to Launch editor and Launch game.

Each study builds disposable ROMs, disables physical input in private Ares settings, retains its logs and fixture snapshots, and verifies that the source level remains unchanged. Results are stored in a unique `build/scale-study/` directory; `latest.json` points to the completed report. The regular game now logs `scene_work` counts for total models, bounds survivors and camera-transformed vertices. Bounds survivors are candidates, not necessarily visible pixels.

These experiments measure distinct workloads, not final supported mission counts. See the [mission scale guide](scaling-guide.md) for measurements, excluded work and architectural priorities. Capture ROM timings are unsuitable for this comparison because framebuffer export forces graphics waits.
