# Isolated video-quality study

`video_study.py` derives diagnostic ROMs from a frozen, verified two-guard
Tiny3D build. It changes no canonical game sources, editor options, VS Code
tasks or installed SDK files. Run it only after freezing the verified build's
sources. The existing baseline snapshot is reused only when the requested
verification, every frozen file and the SDK identity still match.

```powershell
python tools/video_study.py 320-aa --run
python tools/video_study.py 640-none --run --windows 10
python tools/video_study.py 640-aa --field-pacing 2 --run --windows 30
```

The default verification is `build/guard-60fps/final-verification.json` and the
default output is `build/video-study`. Use `--verification` and a fresh `--output`
under `build/` for another verified baseline. A reused output intentionally
fails if it refers to a different build. No content is recooked: the snapshot
preserves generated character objects, gameplay objects, textures/DFS and the
Tiny3D library. Three staged translation units are recompiled, and each variant
records its changes, input hashes and resulting ROM hash.

The 640 mode doubles both world-viewport dimensions and focal length, preserving
the camera's field of view. Color and depth surfaces are 640×480. HUD coordinates
and fonts retain the 320 layout; this is a known visual limitation of the study,
not a finished high-resolution game mode. Gamma/dithering, geometry, texture
assets, probes, live guard behavior and audio remain those of the baseline.

The SDK's 640×480 mode is interlaced. The observer maps actual VI origins to
registered framebuffer bases, accounting for the one-row field offset. Unknown
origins, a wrong video mode or missed native video intervals invalidate a run.
It records source-buffer changes and the number of sources that survive both
field offsets separately. A source change alone is not a complete 480-line image.

Merely starting rendering every two fields did not ensure coherent pairs:
variable RDP completion could produce alternating one-/three-field holds.
`--field-pacing 2` additionally queues RDP-completed surfaces through the public
`rdpq_detach_cb` API and publishes one from a bounded three-entry ring on a fixed
alternating VI phase, before libdragon's registered display handler. A framebuffer
remains owned until the normal display subsystem releases it. Waiting is charged
to `display_wait` and remains in raw elapsed samples. The report's
`coherent_30fps_gate` requires no incomplete sources and a two-field cadence;
the ordinary work-budget check is reported separately.

For post-VI image review, open an already built variant separately:

```powershell
python tools/video_study.py 640-aa --field-pacing 2 --visual
```

That visible disposable Ares session uses isolated settings/controller mappings,
closes after 45 seconds, and is never timing evidence. A `finish` file in its
printed run directory ends it early after a screenshot. Raw framebuffer dumps
do not contain the VI's final anti-aliasing output. The portable origin helper
has a standalone C test; provenance failures are covered by the normal Python
test discovery.

These are Ares experiments against the recorded baseline, not original N64/M64
validation or a promise about later scenes/assets. The measured baseline and
results are summarized in `docs/evidence/video-quality-study.json`.
