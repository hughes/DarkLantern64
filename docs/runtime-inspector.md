# Runtime inspector

Build with `--runtime-trace` to capture the runtime's streaming lifecycle,
resource ownership and input/query diagnostics. Import the emulator log with
`tools/runtime_inspector.py` to produce a self-contained HTML inspector and a
JSON report. The HTML works offline and has launch selection, a snapshot
timeline, an authored X/Z region and gate map, slot progress/fences, correlated
load/candidate jobs, stage costs and searchable events. Selecting an event
shows its exact fields and moves to the preceding state snapshot.

Tracing is opt-in. Its bounded buffer and flushing add overhead; the ordinary
build does not collect these records. The end marker reports measured recorder
and flush ticks and buffer storage. Those measurements describe the tracing
implementation; frame acceptance still comes from the independent performance
verifier described in [Bafford transition performance](bafford-stream-performance.md).
Region-local query provenance uses optional metadata arrays in both scene banks,
totalling 6,400 bytes in traced builds. Compact keys follow the hot bank fields.
Ordinary ROMs retain their original bank
layout; host fixtures enable provenance with `DL_RUNTIME_DIAGNOSTICS=1`.
The trace ring adds its separately reported allocation. Comparing ordinary and
traced builds measures the complete optional diagnostic path: provenance,
recording, probes and output.

## Capture and inspect

Streaming Rooms is a small authored example that does not require the imported
Bafford mission. These commands build the diagnostic ROM, complete its input
route in Ares and import the captured log:

```powershell
python tools/build.py --bundle content/level_bundle.json --stream-test --runtime-trace
python tools/verify_streaming_probe.py --rom build/DarkLantern64-trace-bundle-level_bundle-menu-stream-test-t3d.z64 --output build/runtime-streaming-probe.json
$capture = Get-Content build/runtime-streaming-probe.json -Raw | ConvertFrom-Json
python tools/runtime_inspector.py $capture.log --map build/DarkLantern64-trace-bundle-level_bundle-menu-stream-test-t3d/runtime_debug_map.json --rom build/DarkLantern64-trace-bundle-level_bundle-menu-stream-test-t3d.z64 --output build/runtime-inspector.html --require-complete
```

Open `build/runtime-inspector.html` in a browser. The adjacent
`build/runtime-inspector.json` contains the same capture, identity map, jobs,
cost totals and completeness findings. `--json` chooses a different JSON path.
For any other ROM, use the `runtime_debug_map` path printed by its build or
stored in its `build.json`; maps are tied to that exact build and package.

An ordinary interactive run can be imported before it ends. The report stays
explicitly **incomplete** until every launch has an `rt_end` record. Mission
shutdown/restart and the completed diagnostic routes finish their trace. A
forced emulator termination may leave an unfinished record or session. By
default the importer still writes a useful partial report; `--require-complete`
writes the findings and returns a nonzero exit code. A stale map, malformed
record or unsupported protocol rejects import.

The importer compares the trace's build fingerprint and mission package
SHA-256 with the sidecar before resolving runtime IDs. `--rom` additionally
compares the actual ROM's SHA-256. Region, entity, object-index, gate and
region-local collider mappings come from the cook. Imported object/source
metadata remains attached to the mapped entities and collider records, so a
blocking collider can be traced back to its authored source. Keep the ROM,
sidecar and log together when archiving evidence. Source IDs are stable within
the mapped package; they are not a compatibility promise across changed cooks.

## Reading a transition

The state snapshot distinguishes four sets: authored **wanted** demand,
**physical** resident chunks, the **published** view used by gameplay and
rendering, and the unpublished **candidate**. A completed read or resident
chunk alone does not admit a crossing. CPU assembly, complete collision-query
index construction and GPU preparation must finish before publication.

Each region job uses `(generation << 32) | (slot + 1)`; candidate IDs use a
separate high-bit sequence. Slot recycling therefore does not associate a new
load with a cancelled earlier load. The timeline records demand, requests,
read progress/completion, validated residency, CPU and GPU readiness,
publication, cancellation, retirement and ownership release, with extended
session-relative tick timestamps and reasons. A changed approach can cancel
an unpublished candidate while the old published scene remains usable.

Request-to-publication latency is the elapsed span from the request to the
actual publication. It includes waits and work distributed across frames.
The service tables separately report active service elapsed ticks, CPU ticks
excluding audio callbacks, callback ticks, commit cost and initial-admission
cost. They do not equate a sum of CPU slices with latency. Publication and
startup attribution are parts of their containing service samples, so they
must not be added again to the elapsed total. Per-job costs include only
samples associated with that job; launch stage totals also include unassigned
read/idle samples. Repeated same-job/stage service samples are aggregated before
transport: `samples` gives their count and `max_cpu` retains the measured
individual CPU maximum. The accumulated elapsed/CPU cost of an aggregate is
not an individual frame maximum. An unfinished job in a complete capture means it was still
pending when recording ended, and a cancelled job has no successful
request-to-publication latency.

Service aggregates cover up to 60 frames. The transport drains at most one
record per gameplay frame; startup and shutdown drain the complete queue.
Recorded timestamps identify observation time independently of log transport.

Each slot shows bytes read versus chunk bytes, CPU pins, and its last RSP/RDP
serials compared with captured completion serials. A retiring slot may remain
owned while these fences or borrows drain. The inspector reports that actual
ownership rather than assuming retirement immediately releases storage.

## Motion and interaction diagnostics

Motion records show the nearest relevant authored gate, its source and target
regions, linked door, requested displacement, direction, required working set,
missing physical/published masks and readiness reason (`read`, `cpu`, `gpu`,
`publication`, or `ready`). The captured `waiting` value and actual horizontal
or jump suppression are separate fields. Parallel or away-from-gate input can
therefore be inspected while a gate is waiting without implying that such
input was blocked. The displacement is an intent probe using simulation input
and turn rules; it is not a substitute for the actual collision result.
Unchanged normal motion is sampled every 30 render frames; readiness, demand
and requested movement changes trigger another sample. Every actual suppressed
movement/jump tick is recorded. The lightweight ROM intent probe avoids
repeating the simulation's predicted-position collision query.

Interaction records distinguish ordinary line of sight from the interaction's
effective query, which ignores its own door leaf and linked loading barrier
where gameplay already does so. `probe=raw` retains the ordinary blocker when
the effective interaction is clear; `probe=effective` identifies the effective
blocker when it is occluded. This makes an own-door observation visible without
mistaking it for an interaction failure. The record also captures eligibility,
range, input suppression, query-cache readiness, source entity/region/gate,
authored collider ordinal and associated interactive object.

`source_record` is one-based in the ROM trace, with zero reserved for synthetic
barriers; the sidecar's region-local collider `record` is zero-based. The
importer resolves this conversion and checks the referenced entity. Loading
gate barriers and region barriers remain identified as synthetic sources.
Diagnostic helpers observe existing gameplay/query results and do not change
LOS, collision, door, loot, guard, residency or publication semantics.

## Small host doorway fixture and integrity checks

`src/runtime_diagnostic_fixture.h` supplies `DlRuntimeDoorwayFixture`, a compact
two-region package with a door, target loot, an authored gate and a loading
barrier. It runs with the host compiler and needs neither Ares nor commercial
mission assets. Its public helpers expose independent load and publication:

```c
static DlRuntimeDoorwayFixture fixture;
dl_runtime_doorway_init(&fixture);
dl_runtime_doorway_load(&fixture, 1);
dl_runtime_doorway_publish(&fixture); /* physical=1, published=1 */
dl_runtime_doorway_load(&fixture, 2); /* physical=3, published=1 */
/* Inspect motion/LOS here: the candidate's geometry is still unpublished. */
dl_runtime_doorway_publish(&fixture); /* physical=3, published=3 */
```

`tests/test_runtime_diagnostics.c` exercises unavailable, physically resident
but unpublished, CPU-ready, GPU-ready and published states; toward/parallel/
away input; own-door and loading-barrier LOS; scalar/cache agreement; and
observer non-mutation. The bounded trace tests cover actual manager requests,
partial reads, cancellation, recycling and retirement fences. Run all host
checks with `python tools/build.py --test`, or just the importer checks with
`python -m unittest discover -s tests -p test_runtime_inspector.py`.

The importer tests also cover literal ROM serialization, request/candidate
correlation, reversal and cancellation, restarts, stale identities and slot
generations, missing records/slot rows, queue overflow, a truncated final line,
missing end markers, collider provenance, exact 64-bit job IDs in HTML and safe
display of authored names. Incomplete captures show explicit findings; they
never acquire a successful completeness label by dropping their missing tail.

## District performance validation

Use the trace flag on the existing continuous 41-checkpoint route and run the
same strict verifier:

```powershell
python tools/build.py --bundle content/level_bundle.json --bafford-district-performance-test --runtime-trace
python tools/verify_bafford_district_performance.py --rom build/DarkLantern64-trace-bundle-level_bundle-menu-bafford-district-test-performance-t3d.z64 --output build/runtime-district-performance.json
```

Import the log and matching map as above. Compare the opt-in trace build with
an ordinary build using the same route and budgets. Retain the independent
frame/service maxima, simulation-time conservation, zero movement and
interaction waits, current-view coverage and audio continuity results. A
complete trace proves the recorded diagnostic stream is complete; it does not
by itself prove the route passed or that original N64 hardware has been tested.

Verified October 13, 2026 (Pacific), using the same final mission package in both
variants. Both tours pass all 41 checkpoints with zero dropped simulation time,
movement suppression, loading interaction waits and music underruns.

| Measured maximum | Ordinary ROM | Traced ROM | Limit |
| --- | ---: | ---: | ---: |
| Gameplay frame | 30.352 ms | 32.046 ms | 33.333 ms |
| Raw frame interval | 31.911 ms | 32.827 ms | 33.333 ms |
| Service CPU excluding audio | 7.537 ms | 7.864 ms | 8.000 ms |

The accepted capture contains 2,392 records across two complete launch sessions,
with zero lost events. Recorder/probe/transport elapsed overhead totals 1.288
seconds across those sessions, including startup/end drains and interrupts.
The fixed 256-record ring occupies 45,128 bytes; optional collider provenance
adds 6,400 bytes. The observed average gameplay-frame difference is 0.171 ms;
display waits and route pacing mean this is not an isolated CPU benchmark.
[Saved acceptance evidence](evidence/runtime-inspector-performance.json) records
the exact build, ROM, package and log identities and the measurement boundaries.
