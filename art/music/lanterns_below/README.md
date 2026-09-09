# Lanterns Below

An original exploratory DarkLantern64 cue: 48 seconds, 80 BPM, D minor, with
the progression Dm–Dm–B-flat–C–Dm–F–Gm–A. The melody uses chord tones; C-sharp
appears deliberately over the final A-major chord resolving to D minor.
Four 64-row XM patterns form
16 bars. The sound is a deliberately synthetic approximation of low bowed
strings, felt/plucked wire, a watch bell, frame drum, brushed iron, and cold air.
All seven mono samples were synthesized for this study; no external recording
or existing composition was used.

Open **lanterns_below.xm** in OpenMPT or MilkyTracker to inspect and edit the
eight channels, instruments, envelopes, and notes. Sample tuning is embedded in
the XM. Samples are 16-bit at 16,726 Hz, with XM relative-note +12 and the
reported fine tuning; auditioning the standalone sample WAV at its native rate
plays its base pitch. The first two string samples and cold-air texture loop.

| Channel | Part |
| --- | --- |
| 1 | Low viol, six-second harmonic cells |
| 2 | Veiled middle strings |
| 3 | Occasional upper string color |
| 4 | Sparse plucked motif |
| 5 | Quiet, separately voiced motif echo |
| 6 | Widely spaced bell notes |
| 7 | Low frame-drum accents |
| 8 | Air texture alternating with brushed iron |

The echo is part of the score and consumes a real channel. There is no external
reverb plugin or master processing to reproduce. The final cell leaves a breath
before the beginning; this is intentional space, not a missing buffer.

Revision 2 responds to listening feedback that the original melody lacked a
clear key progression: it replaces the E-flat color with a more legible minor-key
sequence and shortens the pluck envelope to keep echoes inside their harmony.
The original XM and score JSON are preserved in `versions/`.

Two editing paths are available:

- **Tracker editing:** edit/save the XM, then run the study command below. It
  cooks the saved XM without regenerating it. This fixture assumes the existing
  48-second arrangement, tempo, channel and instrument counts. Its assertions
  deliberately reject changes that require a more general duration analyzer.
- **Procedural editing:** edit `score.json` for notes, timing, volumes and pans,
  or `tools/music_study/compose.py` for sample design. Run the study with
  `--compose` to regenerate the XM and sample WAVs. This overwrites tracker or
  sample edits, so preserve an alternate version first if you need both.

From the repository root:

```powershell
python tools/music_study/study.py --rom
python tools/music_study/verify_ares.py
```

Requirements: Python with NumPy, MinGW GCC, installed libdragon SDK, and the
inspected libdragon source checkout in the sibling `../libdragon` directory.
Override with `--gcc`, `--sdk`, or `--libdragon`. Verification uses Ares and the
existing project-local 8 MiB settings; `--ares` and `--settings` override those.
Nothing is downloaded. Outputs go to `build/music-study/lanterns-below/`:

- `lanterns_below.wav`: audible 32 kHz stereo **host reference rendering of the
  cooked XM64**. It is not a capture of the RSP or the VADPCM decoder.
- `lanterns_below.xm64` and `lanterns_below.wav64`: console assets.
- `lanterns_below.z64`: isolated audition ROM, automatically playing 50 seconds
  of XM64 and then 50 seconds of the rendered stereo VADPCM alternative.
- Float renders and conversion logs for verification.

The host player uses the pinned fork's libxm sequencer and cooked-file loader,
but its CPU mixer and resident sample storage differ from console playback.
See [the study report](../../../docs/music-study.md) for findings and limits.
