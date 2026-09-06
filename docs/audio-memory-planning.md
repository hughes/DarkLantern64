# Audio memory planning in the editor

The first estimator is a planning tool for named listening scenarios and area transitions. It makes assumptions visible before audio assets and runtime allocation policies are established. It does not measure the N64 heap, predict processing cost, or certify that a level fits in memory.

Audio shares the game's **8 MiB total RDRAM** with code, graphics, simulation, and other systems. ROM storage and RDRAM allocation are separate ledgers. The example manifest uses an **illustrative 256 KiB audio envelope**, equal to 3.125% of 8 MiB. This is an editable planning value, not an accepted allocation for the game. The rest of RDRAM is not automatically free.

## Open and use the planner

Build and launch the desktop editor with `./build.ps1 -Editor -Run`. The **Audio Memory** panel is part of the project's default layout. Choose a scenario, inspect its RAM breakdown and channel requirements, and expand the unknown costs before treating positive headroom as available memory. **Apply + Recalculate** validates and saves changes to the planning manifest; **Discard draft** abandons unapplied controls. This does not change the N64 audio configuration.

The canonical inputs are [content/audio_budget.json](../content/audio_budget.json). The editor build generates `build/generated/audio_memory_report.json`; the panel can recalculate it without rebuilding the ROM. The estimator also runs independently:

```powershell
python tools/estimate_audio.py
python tools/estimate_audio.py --manifest content/audio_budget.json --output build/generated/audio_memory_report.json --stdout
```

The report records its schema/model version, canonical JSON input hash, SDK reference, units, formulas, and declared assumptions. Whitespace changes to the manifest do not change its canonical input hash. Invalid inputs produce an error and preserve the last valid report. A valid report that exceeds the illustrative envelope is still generated: a planning warning is not a build failure.

The editor's local command interface exposes the same model:

```powershell
python tools/editorctl.py get_audio_memory
python tools/editorctl.py set_audio_config --args-file audio-patch.json
python tools/editorctl.py recalculate_audio
```

For example, `audio-patch.json` can contain the following. The budget is in bytes; this edit sets a deliberately small envelope to inspect warnings, so restore the original after the experiment.

```json
{
  "scenario_id": "door-to-pursuit",
  "patch": {"budget": {"ram_bytes": 65536}}
}
```

The first UI edits the envelope, output rate/frame count/buffer count, retained pool sizes and channel capacity, and runtime/allocator/unknown/reserve allowances. The envelope can be disabled; the corresponding API patch is `{"budget": null}` and the panel reports that no envelope is assigned. Author asset metadata, individual voice windows, effects, contexts, and transitions in the manifest, then recalculate. The schema is strict: references must resolve, independent IDs must be unique, and format/policy values must be supported. See the [editor guide](../editor/README.md) for the complete command contract.

## Account for allocation lifetimes

| Cost | What determines it | When it remains allocated |
| --- | --- | --- |
| Encoded ROM data | Cooked file size, or a clearly labeled codec/payload estimate. | Not automatically resident in RDRAM. |
| Resident sample banks | Unique loaded assets and their selected in-memory representation. | Until the bank is explicitly unloaded. |
| Decoded playback buffers | Source format, configured playback capability, buffering policy, and independent playback instances. | The SDK can retain channel buffers after playback stops. |
| Decoder working state | Codec, channel count, implementation, and simultaneous decoder instances. | According to decoder/pool ownership, not just audibility. |
| Output buffers | Actual output rate, stereo sample size, block duration/alignment, and allocated buffer count. | Normally throughout audio-system lifetime. |
| Effects and acoustic state | Delay lines, active room responses, source filters, propagation data, and event queues. | Including tails and overlapping room responses. |
| Shared runtime and allocation overhead | Mixer state, code/static data, stacks, file state, alignment, allocator bookkeeping, and shared SDK systems. | Must be charged once to an explicit owner in the whole-game ledger. |
| Reserve | An explicit planning allowance. | Kept available for uncertainty and peaks; it is not permission to omit unknown costs. |

An asset played by several sources can share its resident bank while still requiring separate decoded windows and state for independently advancing playheads. Stereo sources consume two mixer-channel slots. Paired clear/muffled dialogue uses two independent mono playbacks while the pair remains synchronized, even when one is inaudible.

The planning model includes a persistent decoded/decoder pool allowance. A scenario that uses less than that allowance does not pretend the unused pool has been freed. A scenario requiring more reports the shortfall and includes the larger required allocation in its estimate. This is a declared provisioning model; actual SDK allocation behavior still needs runtime verification.

## Calculation rules

All report memory values are bytes; the panel uses KiB (1,024 bytes). Sample rates count sample frames per second. One stereo frame contains two samples.

| Component | Model version 1 calculation |
| --- | --- |
| PCM source payload | Sample frames × channels × bits per sample ÷ 8. |
| VADPCM source payload | Pad source frames to 32, then 9 bytes per 16 samples per channel. Headers/codebooks are a separate declared container cost. |
| Opus source payload | Explicit byte count; no guessed compression ratio. Decoder workspace and block size must also be declared. |
| Output allocation | Round each `(buffer_frames × 4 + padding_bytes)` request up to 16 bytes, then multiply by allocated buffer count. |
| Playback window | Round maximum playback rate × window duration up to frames; add decoded lookahead padding in frames; round to a whole decoder block; convert to bytes and align to 16. |
| Decoder pool | Greater of declared retained capacity and the scenario's summed independent decoder requirements. |
| Decoded pool | Greater of declared retained capacity and the scenario's summed independent playback windows. |
| Effect | Aligned delay line plus separately aligned declared state and scratch. |

Decoded resident PCM still needs a playback window in the checked mixer. A stereo window holds both channels once and consumes two mixer slots. A shared asset-state allowance is charged once per selected asset, while independent per-playback state belongs in the voice's decoder workspace allowance.

Output rate and buffer frames are **independent controls**. The example's 1,280 frames at 32 kHz represent 40 ms per buffer. At 48 kHz the same allocation represents about 26.7 ms; preserving 40 ms requires 1,920 frames and more RAM. Changing output rate alone also does not change explicitly declared voice playback caps or effect rates. These are proposed configurations to evaluate, not commands sent to the running console.

## Context and transition estimates

A context describes a planned set of resident assets, playback instances, and effects, such as a furnished room with two speaking guards. A transition combines the referenced contexts and any explicitly declared extra loading cost. Shared asset and voice IDs are deduplicated; different voices using the same asset remain distinct.

This models simultaneous residency deliberately. Taking the larger of two room totals would miss a doorway transition where both banks, voices, and effect tails remain live. Conversely, adding both totals blindly would count shared output buffers, shared banks, and persistent pools twice.

```text
estimated audio peak = output allocation
                     + unique resident assets
                     + retained/required playback and decoder capacity
                     + simultaneous effects
                     + declared runtime and transition allowances
                     + reserve
```

The tool's scenarios are authored planning examples. They are not automatically inferred from the current 3D scene or its camera position. As audio banks, acoustic cells, and voice policies become real content, their manifests should supply these inputs instead of duplicating them by hand.

The initial example produces the following totals, **including its manual allowances and reserve**. Both individual doorway/pursuit contexts sit below the illustrative envelope; their overlap exceeds it.

| Authored example | Estimated RAM | Difference from 262,144-byte envelope |
| --- | ---: | ---: |
| Quiet corridor | 232,784 bytes | 29,360 bytes conditional headroom |
| Doorway crossfade | 249,232 bytes | 12,912 bytes conditional headroom |
| Pursuit | 219,984 bytes | 42,160 bytes conditional headroom |
| Corridor → doorway overlap | 272,432 bytes | 10,288 bytes over |
| Doorway → pursuit overlap | 285,232 bytes | 23,088 bytes over |

These values describe model version 1.0.0 and canonical manifest hash `c7eaf821358c86bf508101f6f902bc3b833407c3d797a20b04290de5a3053405`. The catalogue separately estimates 1,277,224 bytes of ROM payload plus 1,272 bytes of declared container overhead. None of these numbers describes existing recordings or measured runtime allocations. Editing the assumptions replaces the example result.

## Estimates must state what they know

Every report should identify its input manifest and model version, show the assumptions used for each component, and distinguish estimates from measured sizes. A missing codec workspace or runtime allowance is **unknown**, not zero. A positive difference between an envelope and the known subtotal is only conditional headroom.

The early tool reports warnings and shortfalls. It does not block ROM builds with an unvalidated hardware-memory claim. Later, a build gate can reject a proven budget violation once cooked sizes and measured allocation policies supply complete inputs.

Keep the evidence separate:

- **Calculated:** PCM bytes, aligned output-buffer payload, and an explicitly supported compressed-payload formula.
- **Declared:** residency, concurrency, pool sizes, decoder/effect allowances, and reserve.
- **Unknown:** costs omitted or not yet bounded by the chosen model.
- **Measured later:** actual cooked file sizes and peak allocations on the selected SDK/runtime.

## Use the tool to make decisions

Compare a quiet room, active dialogue, an exterior space, and their transition. Then change one assumption at a time: resident versus streamed assets, mono versus stereo, output rate, retained channel capacity, or duplicated muffled variants. Watch both ROM data and peak RDRAM rather than choosing whichever number looks smaller.

A lower sample rate can reduce assets or configured buffers, but it does not necessarily shrink an already allocated SDK channel pool. A shorter compressed recording reduces ROM bytes; it does not automatically reduce the decoder's peak working memory. More buffering trades memory and latency for resilience against late reads or processing.

The estimator should be followed by allocation instrumentation: tag banks, buffers, decoder state, and effects; record current and high-water usage; exercise transitions and immediate backtracking; compare actual peaks with the planning report. Measure audio underruns, event latency, and CPU/RSP/PI load alongside memory. A memory estimate alone cannot establish that streaming or an effect is affordable in time.

## Verification checkpoint

Fourteen automated estimator tests cover codec/window arithmetic, retained pools during silence, transitions and deduplication, stereo channel consumption, unknown costs, schema failures, deterministic reports, and preserving source/report files on failure. The desktop editor builds successfully. Live file-interface checks verified scenario reports, changing and disabling the envelope, independent rate/frame behavior, failed edits preserving saved files and scenario selection, external edits surviving a stale Apply, and restoring the original manifest. The Audio Memory panel is docked with a positive visible area in the project layout, and saved tab selections survive restart. Runtime allocation measurements and listening tests are still required.

See [Audio design](audio-design.md) for the playback/hearing architecture and [N64 hardware guide](n64-hardware-guide.md) for whole-system constraints.
