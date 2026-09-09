# Lanterns Below: first music experiment

An original 48-second atmospheric cue now exercises the composition, XM64 cook,
host preview, rendered WAV64 alternative, and standalone N64 build. It uses
eight channels and seven synthesized mono instruments, with no downloaded
samples. The composition is intentionally spare: a low string foundation,
plucked fragments with a separately voiced echo, occasional bells, and soft
percussion/air textures. Source, sample inventory, and editing instructions are
in [art/music/lanterns_below](../art/music/lanterns_below/README.md).

This is an exploratory fixture. Its XM64 cue is now assigned to Sponza in the
game ROM, with a completed-PCM queue feeding the existing synthesized effects
callback. It does not yet play inside native editor Play. The small host renderer
is a useful first component for editor audition. See [Music](music.md) for the
implemented build command, runtime policy, and in-level verification.

The current composition is **revision 2**, revised after listening feedback:
Dm–Dm–B-flat–C–Dm–F–Gm–A, with a recurring chord-tone melody and a shortened
pluck release to keep echoes from crossing harmonic changes. The original's
E-flat colors were too ambiguous for the requested sense of a recognizable key.
Original XM/score sources remain in the asset's `versions/` directory.

## Measured conversion and host playback

| Item | Result |
| --- | ---: |
| Arrangement | 80 BPM, speed 6, 4 × 64-row patterns, 48 seconds |
| Tracker channels / instruments | 8 / 7 |
| Editable XM | 306,729 bytes |
| Cooked XM64 | **297,194 bytes (290.23 KiB)** |
| Converter RAM estimate | **22 KiB**, integer-rounded; not the whole audio heap |
| Converter sample buffers | **14,896 bytes**, across eight channels |
| Rendered stereo WAV | 6,144,044 bytes, including WAV header |
| Stereo VADPCM WAV64 | **1,728,352 bytes (1.65 MiB)** |
| Standalone audition ROM | 2,228,224 bytes, containing both alternatives |

For this cue, the recorded alternative costs about **5.82 times** the XM64 ROM
space but uses two music mixer channels instead of eight. This result is specific
to this sample palette and arrangement; it is not a general compression ratio.

The host test loads both the original XM and cooked XM64 with the inspected
libdragon/libxm sources and renders 96 seconds plus one sample frame. Results:

- Source and cooked float PCM match exactly in this host implementation.
- The second loop matches the first exactly; the boundary jump is zero.
- Rendering in blocks of 256 versus 997 frames gives identical PCM.
- No non-finite or clipped samples; peak is 0.24117, about −12.35 dBFS; RMS is
  approximately −25.74 dBFS. This leaves mix headroom; there is no hidden preview
  normalization or added reverb.

The preview WAV contains one 48-second loop rendered from **cooked XM64**.
These checks establish repeatability in the host reference renderer. They do
not establish bit-exact RSP playback: CPU sample arithmetic, gain/pan behavior,
and volume smoothing still need comparison with the console mixer.

The machine-readable [host report](evidence/music-study.json) records actual
asset hashes, converter identity, host core hashes, compiler identity, checks,
and ROM build identity. Rebuilding regenerates this evidence for the current
source. The tools are a fixed study harness, not the general importer proposed
in [Music](music.md).

## What the exercise has taught us

1. **Cooked XM64 host audition is practical.** The fork's host-compatible loader
   and sequence renderer compiled with MinGW and rendered our cooked asset. A
   desktop device and editor controls are still needed; console-compatible CPU
   mixing remains separate work.
2. **Instrument changes reset sample pan.** Each authored note now includes an
   explicit pan effect so new notes keep the intended stereo placement.
3. **Echoes consume voices.** This cue deliberately spends its fifth channel on
   a quiet delayed motif. A desktop effect plugin would obscure that cost.
4. **Pitch and timing determine channel buffers.** The converter requests
   `[440, 1304, 1920, 2536, 2536, 3000, 896, 2264]` bytes. Eight equal generic
   windows would poorly describe this particular arrangement.
5. **Windows paths need normalization.** Passing backslash input paths to this
   `audioconv64` makes it construct an invalid destination filename. The harness
   supplies forward slashes. SDK symbol tooling also requires `N64_INST`.
6. **Reproducible authoring needs clear ownership.** The saved XM is cooked by
   default. Regeneration from score JSON is explicit, so a cook cannot silently
   overwrite a composer's tracker edits.
7. **Channel reuse inherits gain.** The first emulator run completed both paths,
   but the WAV64 phase peaked at only 873 versus 7,825 for XM64. Its channel still
   had the last XM note's volume. Explicitly setting the stereo waveform's gain
   after starting it corrects the handoff. Backend changes need deliberate
   channel-state initialization; comparing codecs without matching gain would
   give a misleading result. The [first-run evidence](evidence/music-study-ares-v1.json)
   is preserved separately from the revised test.

## Standalone console experiment

[player.c](../tools/music_study/player.c) initializes 32 kHz stereo output with
three buffers and eight mixer channels. It loads the XM64 from DragonFS and
services libdragon's mixer from the foreground. After 50 seconds it requests
stop, services the deferred stop, closes the player, then streams the stereo
VADPCM alternative for 50 seconds. Both phases cross the 48-second loop boundary.

The player records output rate, block length, nonzero PCM peaks, and sampled
heap allocation. It synchronizes RSP work before inspecting PCM; this diagnostic
barrier is not a proposed production scheduling policy. `verify_ares.py` runs a
hidden, disposable Ares instance with isolated settings and checks that both
phases generate nonzero PCM and reach completion. It closes only its own process.

Console/emulator results are recorded separately in
[the Ares report](evidence/music-study-ares.json) when verification succeeds.
Even successful completion is not an underrun test or an acoustic listening
judgment. This ROM has no Tiny3D scene, competing game effects, loading stress,
or original N64/M64 validation.

**Revision 2 passed in Ares:** both 50-second phases completed with nonzero PCM
and the deferred XM stop/close completed. Actual output was 31,995 Hz in
1,272-frame blocks. The sampled heap baseline after console/audio/mixer setup
was 770,072 bytes; XM peaked at 795,312 bytes (**25,240 bytes / 24.65 KiB above
that baseline**). After closing XM, usage was 770,504 bytes. The subsequent WAV64
phase peaked at 786,880 bytes and ended at 786,512 bytes, illustrating retained
allocation after playback. These are sampled whole-heap values from one ordered
test, not isolated cold-start backend budgets or complete allocation traces.

With explicit gain initialization, the XM and WAV64 phase peaks were 8,371 and
7,916 respectively in signed 16-bit PCM units. The host reference peak differs
from the RSP result; output rates and mixer paths also differ. Exact audio parity
remains a separate comparison requiring aligned PCM captures, not just peaks.

## Next use of this fixture

Connect the cooked-file host renderer to a desktop audio device and a minimal
editor audition panel. Keep the saved XM and reference WAV for comparison.
Then extend the first Sponza console integration into a shared music command
interface for native Play, while retaining gameplay effects and measuring
graphics/audio contention across more encounters. The [music plan](music.md)
describes those integration and acceptance requirements.
