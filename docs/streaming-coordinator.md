# Shared streaming coordinator

Native Play and the ROM execute `src/stream_coordinator.c`. It owns demand,
queued doorway use, bounded scene preparation, publication and retirement.
`world_stream.c` owns DLM1/DLR1 validation and slot lifetimes; `stream_scene.c`
builds working geometry, exact floors, collision, unavailable-gate barriers and
visual membership. No libdragon code enters these portable modules.

Assembly advances in batches of at most 16 records between clock checks while
honoring the caller's record cap. This keeps platform clock callbacks from
consuming the preparation slice. Integer ROM trace rows use a bounded text
writer that preserves the v1 protocol and full-width job/clock values; it
avoids general-purpose formatting in the frame loop.

The desktop adapter in `editor/stream_play.h` reads the cooked package file and
verifies its SHA256 before opening its directory. The ROM adapter in
`src/stream_rom.c` supplies bounded DMA reads, timing budgets, renderer
preparation, and actual RSP/RDP completion. Native preparation emits a bootstrap
`play.json` binding those same package bytes and authored IDs. It contains global
gameplay definitions and shared dynamic visuals. Static architecture and
collision are decoded only from the checked region package.

## Ownership and publication

The caller allocates the immutable mission directory, bootstrap definitions,
disjoint slot buffers, coordinator and two scene banks. The directory and
bootstrap stay alive until coordinator and renderer shutdown. A region handle
contains a slot and its nonwrapping generation. Requests never reuse a buffer
with CPU pins or outstanding graphics submissions.

A candidate snapshots physically resident handles and current guard positions
at a foreground boundary. Incremental assembly borrows each selected generation
until success, failure or cancellation. The inactive bank copies geometry and
collision; it retains no streamed-buffer pointers after assembly completes.
Preparation leaves the published bank drawable. Renderer preparation receives
the candidate's monotonic job token; a canceled job cannot authorize a later
candidate's renderer result. The platform adapter must check that token.

Publication requires completed assembly and renderer preparation, a live job
generation, a prepared query cache, and guard visual membership matching the
current game. A changed demand cancels an obsolete candidate. A guard crossing
a residency boundary during preparation cancels and rebuilds that candidate.
Guard membership changes also refresh the scene when the region mask stays the
same. Modeled guards remain inactive until their visual rows publish with the
same bank; actors without a visual row continue to use region membership.

Cancellation releases only unpublished CPU borrows and staged renderer work.
The published scene and graphics ownership survive it. Retiring slots reject
new borrows and wait for existing CPU pins plus independent RSP and RDP
completion serials before becoming free. Completion reports are trusted
platform inputs; an adapter must report actual device completion. A DMA read
must not retain its destination after returning. The ROM adapter keeps its
asynchronous staging buffer separate and acknowledges abandonment safely.

Snapshots distinguish wanted, physical, candidate and published masks. An
available slot does not by itself remove a gate barrier; working collision
changes transactionally when the candidate publishes. Policy history retains
thin swept-gate crossings and deliberate reversal. A closed doorway interaction
can queue its target working set, then deliver one use edge after that target
publishes. Reversing or leaving the doorway discards the obsolete interaction.

Doors, collected objects, objectives, loot and actor state belong to `DlGame`,
not region buffers or banks. Retirement/reload changes `game.level` and active
membership without reinitializing those values. Restart uses the adapter's
stop/graphics-shutdown/initialize/start lifecycle and selected test start;
mission state and old movement history reset together.

## Verification

`python tools/build.py --test` includes `tests/test_stream_coordinator.c`.
Its independent three-region package and fake platform exercise 7-byte partial
reads, delayed reads, IO and checksum failures, incremental CPU preparation,
delayed and obsolete renderer results, cancellation pins and generations,
independent RSP/RDP retirement, reversed approaches, branches, thin swept gates,
queued door use, guard membership at preparation/publication boundaries,
persistent values across reload and complete restart.

The two finite TLA PreCheck references have different boundaries:

```powershell
node tools/check_region_lifetime.mjs --c --proof
node tools/check_stream_coordinator.mjs --proof
```

The coordinator reference explores 2,304 states and 11,520 edges. TLC and the
TypeScript interpreter reach the same graph. The checker additionally checks
128 enabled publications and 3,840 cancellation/reset transitions for snapshot
and active-bank preservation. The region-lifetime reference separately checks
CPU and independent graphics ownership, with an exhaustive one-slot C trace
projection. See [model notes](models/README.md) and unmodified certificates in
`docs/evidence/` for domains and limitations. These finite references do not
prove the handwritten C equivalent, platform callbacks, liveness, decoding or
frame budgets.

The fixed route in `tests/fixtures/stream_probe_route.json` expands to 1,070
normalized inputs. It opens and closes the shared door, crosses in both
directions, collects loot, retires and reloads both rooms, and restarts. Run
the native and actual MIPS/Ares comparison with:

```powershell
python tools/verify_stream_replay_parity.py --evidence docs/evidence/stream-replay-parity.json
```

The tool binds replay and ROM-log provenance to the cooked fingerprint and
package SHA256. It compares every declared functional tick: player
position/orientation/stance, visibility, door/mission flags, loot, guard
position/state/awareness/patrol and sensing, object state, simulation events,
current/demand/published masks, active guards and queued interactions. Discrete
decisions compare exactly; floating values have an absolute tolerance of 0.002
for host/MIPS libm and seven-decimal diagnostic output. Missing ticks, missing
fields, crashes, incorrect content fingerprints and missing completion markers
fail the check.

The replay sets `stream_boundary: "settled"`. Both adapters complete stream
service and renderer preparation before consuming each input. This isolates
functional decisions from IO/frame pacing. Slot placement, loading counters,
graphics serials, publication revisions, rendering and audio are excluded;
the synthetic suite tests stalls and fences separately. Native-only route
validation never claims ROM parity. The report includes separate host process
wall timing covering package/checksum startup, streaming, simulation and state
serialization; it makes no Native viewport or N64 timing claim. Historical
results do not accept later runtime changes.

Console acceptance remains separate:

```powershell
python tools/verify_streaming_probe.py
python tools/verify_bafford_district_performance.py
python tools/smoke_bundle.py
```

The probe tests ordinary menu selection, streamed traversal and lifetime. The
performance command evaluates actual N64 stage budgets and is strict by
default; `--baseline` opts into relaxed baseline collection. Menu/restart smoke
checks resource shutdown and reinitialization. Host parity and finite proofs
cannot substitute for those checks or original hardware validation.
