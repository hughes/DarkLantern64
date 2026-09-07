# Courtyard depth correctness fixture

After `python tools/build_tiny3d.py --fetch`,
`python tools/depth_study.py --run` builds and runs a private diagnostic ROM.
It does not recook the game, change its clipping planes, edit the dependency
checkout, install an SDK, or change the user's Ares settings. Output lives under
`build/depth-study/pinned`. Each owned emulator process closes in `finally`.

The fixture reads the actual west warehouse and west window transforms from
`content/moonlit_courtyard.json`, and retains the positive local Z faces and
triangulation from their source OBJs: sixteen wall triangles and two window
triangles. The surfaces are 47.5 mm apart. Their material, texture, fog, AA, and
unrelated geometry are omitted so pure green window pixels and red wall pixels
can be compared unambiguously. The source gap is checked; changing the art
requires an explicit review of the fixture rather than silently changing its
label. Source hashes are recorded.

Sixty cameras cover distances of 3, 6, 10, and 18 metres, horizontal incidence
angles of −60°, −30°, 0°, 30°, and 60°, and three small position jitters. The
camera targets the window centre from eye height 1.55 m. Each renderer first
draws the window alone to obtain its own expected pixel mask, then draws the
wall and window in both orders. A lost pixel is an expected green pixel that
the complete scene incorrectly hides. Each near-plane setting therefore has
120 cases. The normal 12 cm near plane is tested alongside **diagnostic-only**
24 cm and 40 cm settings. Rendering and pixel reads wait for RSP and RDP
completion; these timings are not performance evidence.

The CPU branch follows the game's float view transform, six-plane polygon
clipping, perspective depth mapping, and public `rdpq_triangle` call. Tiny3D
uses the game's packed vertex scale of 1024 units/metre and modelview scale of
64 units/metre. Its matrix setup and vertex/triangle submission match the
production path. Both use the normal 320×240 buffer and 320×165 world viewport.

Additional isolated variants:

```powershell
# Viewport-only upstream 5392 precision backport, staged in copied source.
python tools/depth_study.py --precision --run

# Read transformed vertices through public RSP DMA at failing camera 33.
python tools/depth_study.py --probe --run

# Deliberately discard fractional vertex Z on CPU to isolate that error.
python tools/depth_study.py --cpu-quantized-z --run

# An explicitly supplied local candidate library and matching headers.
python tools/depth_study.py --library PATH/libt3d.a --include PATH/src --label candidate --probe --run
```

`--measure-existing` uses the already built ROM and its recorded hash; combine
it with the same variant selectors. It never rebuilds that ROM. Results include
all 720 observations, grouped counts, ROM/library/source hashes, the isolated
run log, and six raw pre-VI images. Images use the worst loss case per Tiny3D
near-plane group; zero-loss references use camera 33 for comparison with the original
worst Tiny3D case. Their `depth_worst` records identify the exact camera/order.

The pinned implementation reproduced the regression: CPU had no losses in any
case; Tiny3D at 12 cm lost 2,799 pixels across 66 of 120 cases. Increasing the near
plane reduced but did not remove failures. The upstream viewport precision
backport retained 66 failing cases. Forcing integer vertex Z in the CPU branch
still produced no losses in this fixture.

The RSP cache probe found reciprocal errors up to 0.144% at the failing camera.
For example, one window vertex had expected depth 32412.826 but cached depth
32435. Reconstructing the same depth mapping from the cached clipZ/W using an
exact reciprocal reduced the 36 sampled vertices' errors to approximately
−0.171…+0.691 depth units. This identifies the approximate reciprocal as a
dominant cause in these measurements; it does not imply fractional vertex Z
can never become a limit in other geometry. The final candidate combines the
refined reciprocal with the viewport correction and passes all 120 cases at
the unchanged 12 cm near plane. See [the full result](../../docs/depth-precision.md).

Use `--probe-view 51` to select another cache observation and zero-loss reference
camera. `--single-view` renders only that camera for a separate image comparison;
it does not replace the full 120-case correctness gate. The ordinary baseline
builds the pristine pinned source in its own `tiny3d/build_sdk_study` directory,
even if the production dependency helper points at a patched library. It copies
only tracked source inputs and regenerates RSP metadata, so no old build inside
`external/tiny3d` is required. Repeated diagnostic builds replace only their
private library stage; the checkout and production library remain unchanged.

Primary source context: [pinned vertex transform](https://github.com/HailToDodongo/tiny3d/blob/ec557373e986b5e041cc102a7ff787eb07921937/src/t3d/rsp/rsp_tiny3d.rspl),
[pinned clipping transform](https://github.com/HailToDodongo/tiny3d/blob/ec557373e986b5e041cc102a7ff787eb07921937/src/t3d/rsp/clipping.rspl),
[upstream viewport precision change](https://github.com/HailToDodongo/tiny3d/commit/5392d6ed26b7d52da7b11d4c85cc68cdc8d364ce),
and [libdragon CPU triangle setup](https://github.com/DragonMinded/libdragon/blob/trunk/src/rdpq/rdpq_tri.c).
