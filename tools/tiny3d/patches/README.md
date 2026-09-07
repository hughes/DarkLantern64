# Perspective precision patch

`0001-perspective-precision.patch` applies to Tiny3D commit
`ec557373e986b5e041cc102a7ff787eb07921937`. The build helper applies it to a
disposable source copy; the checkout and installed libdragon SDK stay pristine.

The patch combines the viewport precision changes from upstream commit
[`5392d6ed26b7d52da7b11d4c85cc68cdc8d364ce`](https://github.com/HailToDodongo/tiny3d/commit/5392d6ed26b7d52da7b11d4c85cc68cdc8d364ce)
with one refined reciprocal step in ordinary and clipped vertex projection.
The viewport change uses the quantized W normalization consistently, retains
more precision in screen scale, and clamps its depth scale. Its unrelated
metrics instrumentation is excluded.

The RSP reciprocal instruction approximates `x = 0.5 / W`. A direct Newton
update, `x * (2 - 2*W*x)`, removes most lookup error, but computing its residual
in fixed 16.16 loses useful precision. This patch evaluates the equivalent:

```text
e = (128 * W) * x - 64
x = x - round((x * e) / 64)
```

Both products use fixed 16.16 arithmetic. Adding `32/65536` before the signed
six-bit shift rounds the correction. Scaling the residual retains seven more
fractional bits. The cached homogeneous W supplies the original operand;
only the inverse-W lanes change. The 70-vertex cache, matrices, topology,
RGB, shade alpha, texture coordinates and clipping format are unchanged.
Corrected inverse W also improves projected X/Y and texture interpolation.

The main and clipping assembly additions contain 27 and 23 instructions,
respectively. The main kernel handles two vertices per iteration. Their final
instruction sizes are 3,848 and 2,840 bytes, within 4 KiB IMEM; DMEM stays 4 KiB.
To make room, this specialized build removes Tiny3D's native point-light
routine. `t3d_light_set_point` calls `debug_assert_func_f` unconditionally,
including release builds with `NDEBUG`; bypassing that setter reaches an RSP
`break`. DarkLantern64's existing CPU light probes and shading remain active.
Native ambient/directional lighting and shade-alpha fog remain available.

The `.S` changes were scheduled manually against the pinned register layout.
Corresponding RSPL source records the mathematical operation and unsupported
point-light path; **the RSPL transpiler was not rerun**. Re-transpilation requires
another register/IMEM audit and the depth fixture before accepting its output.
The tested generated assembly, rather than an unverified regeneration, is the
build input.

The fixture retains the courtyard's actual wall/window triangles and 47.5 mm
separation. It tests 60 cameras, both draw orders, and near planes of 12, 24 and
40 cm. At 12 cm, the pinned library lost window pixels in 66/120 cases. The
combined patch loses none in all 120 cases, with the same result at 24/40 cm.
This is rendering correctness evidence, not a frame-rate measurement. The
successful combined fixture library SHA-256 is
`df33d2e57efdd0476d6a533d225cc1c1576f8645121d39a8620696d9f109281f`.

An independent fixed-point rounding-bound sweep covered 20,480 combinations:
both W signs, magnitudes 0.001–2, and reciprocal seed error targets up to 0.195%.
All results satisfy the Newton error plus residual/product/output rounding
bound. On 36 captured RSP vertices, the first Newton assembly matched the host
integer model exactly, to the last inverse-W bit. The refined model reduced
the maximum relative reciprocal error from 0.144% to 0.000611% in that capture.
These sampled error figures do not establish a continuous bound on rendered
pixel depth over arbitrary geometry.
