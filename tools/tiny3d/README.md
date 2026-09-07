# Local Tiny3D dependency and RSP proof

`python tools/build_tiny3d.py --fetch --proof` acquires the pinned source and builds
the library plus a small diagnostic ROM. After the first fetch, use
`python tools/build_tiny3d.py --proof`. The build never installs into the SDK.
Omit `--proof` when only the library is needed. MSYS2 bash defaults to
`C:/msys64/usr/bin/bash.exe`; use `--bash` or `MSYS2_BASH` to select another.

The pin is maintained in `dependencies.json`. For a complete editor setup,
`python tools/setup_editor.py --tiny3d` also explicitly fetches and builds this
dependency. The standalone `build_tiny3d.py --fetch` command does not require a
LightEngine checkout. Neither setup command installs Tiny3D into the SDK.

The game build exposes a developer selector:
`python tools/build.py --level content/animation_workshop.json --renderer t3d`.
Tiny3D is the default; `--renderer cpu` retains the original output names. Tiny3D appends
`-t3d` to its ROM and build directory and records the renderer, dependency pin,
and library hash in `build.json`, so measurements can identify the exact backend.
Normal game builds run the dependency's incremental local make without fetching.
The helper copies the pristine checkout to `build/tiny3d-source` and applies the
reviewed patches in `tools/tiny3d/patches` there. Neither the checkout nor the
installed SDK is changed. The local cache fingerprints patches, SDK/toolchain
contents, build flags and staged source contents. A changed identity rebuilds
the disposable stage, including objects and generated RSP metadata.

The reviewed pin is Tiny3D
[`ec557373e986b5e041cc102a7ff787eb07921937`](https://github.com/HailToDodongo/tiny3d/tree/ec557373e986b5e041cc102a7ff787eb07921937)
(2025-01-09). This is an SDK compatibility pin, not a claim that it is the latest
or fastest release. It builds and runs against the existing `C:/n64-toolchain`
SDK, with no libdragon source or installed-header changes. The newer Tiny3D
`73d822f3af7c85cc1b93eaa4546504ba0a4ea156` needs preview interfaces absent from this
SDK: vector/matrix types and `rspq_profile.h`. Its RSP assembly did compile.
A future move to current Tiny3D should stage a pinned libdragon preview SDK
locally and revalidate the whole game, including audio and profiling.

Integration inputs:

- Include directory: `build/tiny3d-source/src`.
- Link input, before `-ldragon`: `build/tiny3d-source/build_sdk_main/libt3d.a`.
- Build callable: `build_tiny3d.build_library(sdk)` returns that library path.
- Dependency report: `build/tiny3d-dependency.json` includes revision, patches and library hash.

The perspective patch refines the RSP's reciprocal approximation and backports
the upstream viewport-scale precision correction. It preserves the 70-entry
vertex cache and current game lighting/fog. To fit the RSP instruction memory,
this build excludes Tiny3D's native point-light handler and explicitly rejects
`t3d_light_set_point`, including release builds. DarkLantern64's CPU light probes
and dynamic vertex colors remain supported. See the patch notes and depth study
beside this guide for the math, reproduction and measured scope.

The upstream Makefile produces an ELF relocatable object named `libt3d.a`,
rather than an `ar` archive. Pass it directly to the linker. The underscore in
`build_sdk_main` matters: this SDK's RSP binary-symbol rewrite handles slashes
and periods but does not replace hyphens in a build-directory name.

The pin uses 70 transformed-vertex slots, confirmed by `TRI_BUFFER_COUNT` in
both generated RSP assembly files and `MAX_VERTEX_COUNT` in the official
importer. The older `t3d.h` comment saying 64 is stale. Vertex input consists of
pairs (`T3DVertPacked`, 32 bytes per pair): signed 16-bit positions, packed
5/6/5 normals, RGBA8 color, and signed 10.5 texture pixel coordinates. Loads
must have even counts; the game packer also uses even starts. A scale in the
model matrix can retain the game's metre coordinates while packing sub-metre
vertex precision into integers.

The shortest integration path keeps our character sampler and cooked geometry.
Pack shared mesh batches once; sample the 20 bone matrices on the CPU; submit
each bone's matrix and its vertex subset, preserving prior subsets until all
triangles in the batch have been emitted. Mixed-bone triangles reference those
retained slots directly. Tiny3D performs transform, projection, clipping,
lighting and RDP triangle setup on the RSP. Texture state remains RDPQ.

This pin requires explicit `t3d_tri_sync()` after triangle batches before
overwriting vertex slots or submitting RDPQ work. Matrix/vertex buffers must
remain valid until RSP completion; use buffered submission storage or completion
fences before reusing them. The proof deliberately waits and is not a throughput
benchmark. Allocate the Z surface with `surface_alloc(FMT_RGBA16, width, height)`;
the installed SDK does not provide the newer `display_get_zbuf()` convenience.

`tools/tiny3d/proof.c` draws two triangles using two separately loaded matrices.
At frame 0 they form a static square; at frame 30 the second pair of vertices has
rotated and the triangles still connect to the unchanged first pair. It exports
raw RGBA5551 framebuffer captures and verifies that each visible case contains
more than 500 colored pixels. Frames 60 and 90 additionally draw the static
CPU-front triangle with `CULL_BACK` and `CULL_FRONT`: the first must remain
visible and the second must produce zero colored pixels. This confirms that
the backend's front-color pass should use `CULL_BACK`. Tiny3D internally swaps
the last two vertex arguments before entering its triangle kernel, which must
be included when reasoning about its assembly culling convention.
The existing isolated `smoke_ares.exercise()` harness can
wait for `DL64 tiny3d_proof complete`, then `capture_ares.decode_captures()` can
decode four captures. The validated run produced 10,000 and 9,859 colored pixels
for the geometry cases, 10,000 with `CULL_BACK`, and zero with `CULL_FRONT`;
the exported images were visually inspected. Output paths are
`build/tiny3d-proof/{tiny3d-proof.z64,winding.json,static.png,connected.png,cull-back.png,cull-front.png}`.

This proves SDK/microcode compatibility and connected matrix groups in Ares.
It does not measure workshop performance or establish real-console frame rates.

Primary implementation references:

- [Pinned matrix/load/triangle API](https://github.com/HailToDodongo/tiny3d/blob/ec557373e986b5e041cc102a7ff787eb07921937/src/t3d/t3d.h).
- [Pinned RSP capacity and transform code](https://github.com/HailToDodongo/tiny3d/blob/ec557373e986b5e041cc102a7ff787eb07921937/src/t3d/rsp/rsp_tiny3d.S).
- [Official local-library integration](https://github.com/HailToDodongo/tiny3d/blob/ec557373e986b5e041cc102a7ff787eb07921937/t3d.mk).
