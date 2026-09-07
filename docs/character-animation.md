# Artist-authored character animation

Status: **architecture with an implemented CPU reference prototype**, updated September 6, 2026. The [guard animation study](guard-animation-prototype.md) now proves Blender export, connected one-influence geometry, simple compression, shared assets, editor playback and N64 integration. Five clips occupy 8,820 key bytes, but the measured two-guard workshop runs at roughly 10 fps; RSP acceleration and production performance remain future work. LightEngine's dynamic preview support is published in [PR #17](https://github.com/hughes/LightEngine/pull/17), pending review. Unless explicitly identified as measured, the numbers below are experiment budgets, not verified hardware limits. This extends the [architecture](architecture.md), [Blender workflow](blender-assets.md), [audio design](audio-design.md), and [scale study](scaling-guide.md).

## Recommended direction

Author and rig characters in Blender, export a small deformation skeleton and named clips, and cook those into shared character assets. LightEngine should preview the actual cooked motion and let designers assign characters and behavior sets. The game should keep small independent animation state per actor, while sharing meshes, skeleton definitions and clip data.

Start with a low-bone humanoid, in-place locomotion, one bone influencing each vertex, short crossfades, foot-contact events, and named attachment sockets. Evaluate an RSP geometry backend alongside the first animated guard. Tiny3D is the strongest existing candidate: its current feature set includes skeletal animation, animation blending and compressed ROM streaming. Its skinning limit is **one bone per vertex**, with up to three bones represented by a triangle; this is distinct from blending two animation clips. It requires libdragon preview and currently documents a Fast64-dependent material import path. Pin and test the SDK, library and conversion tools together before adoption. [Tiny3D](https://github.com/HailToDodongo/tiny3d)

The N64's RSP is programmable vector hardware, suitable for transforming and lighting vertices. The RDP rasterizes the resulting triangles; it does not evaluate a Blender rig. Bone sampling, hierarchy traversal and animation decisions remain software work. Graphics and RSP-based audio share processing capacity, so test animation with the intended audio workload. Our [hardware guide](n64-hardware-guide.md) and [audio guide](audio-design.md) explain these divisions.

## What exists today

| Current seam | Implemented prototype and remaining work |
| --- | --- |
| Static Blender prop export remains available. | `tools/guard_assets.py` exports the saved guard's bind mesh, rig and Actions. A general character-export UI remains future work. |
| Level/pack assets accept `type: "character"`. | Version-1 character JSON defines stable bone, clip and socket identities with one influence per vertex. Production glTF/GLB interchange remains proposed. |
| `tools/character_assets.py` and the level/bundle compiler cook characters. | Quantized keys, constant/rest-channel removal, conservative bounds, events, error reports and shared bundle data are implemented. Variable-rate compression is not. |
| LightEngine provides fixed-topology mesh updates. | The project editor runs the shared C sampler and CPU skinning for connected meshes, with rigid-part previews as a fallback. Desktop lighting remains separate. |
| `src/animation.c` samples/blends poses; `src/render.c` deforms geometry and normals on the CPU. | Idle/walk gameplay blending and bounded head attention work. RSP transforms, animation LOD and production-speed rendering remain future work. |
| `src/game.c` retains collision and movement authority. | Actual-distance gait and foot-contact playback are integrated; the broader animation controller and shared audio/AI event queue remain proposed. |

The present renderer reserves caches for up to **128 models, 4,096 instanced vertices and 4,096 instanced triangles**. Its limits count every placed instance, even if the source mesh is shared. Eight 500-vertex guards would consume almost the entire vertex capacity before scenery. The 16-enemy authoring limit does not establish capacity for 16 animated guards.

Bundled levels retain their static-world geometry with separate compilation per level; texture payloads have deduplication. Character geometry, skeletons, clips and sockets now use one generated C definition per source hash across the bundle. Actors share these immutable assets while retaining independent motion state and per-instance renderer caches.

## Artist and designer workflow

1. **Create a character in Blender.** Artists can use control rigs, inverse kinematics and constraints for authoring. Export bakes their evaluated result onto a separate small deformation skeleton; control bones and Blender constraint solvers stay on the workstation.
2. **Author named clips.** Begin with idle, walk, run, turn, notice/listen, investigate, attack and fall. Use explicit clip ranges, loop flags and semantic markers such as left-foot contact. A clip's stable identity must survive renaming its display label.
3. **Export a character pack.** The guard prototype uses an explicit versioned JSON bake. For a general interchange path, evaluate glTF/GLB for skin, joint hierarchy and transform tracks, plus a manifest for clip roles, event markers, sockets, root-motion policy and target settings. glTF specifies skin attributes, inverse bind matrices, transform animation and joint attachments, while our footstep/AI semantics need project metadata. [glTF 2.0 specification](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#skins)
4. **Cook and inspect the target result.** Reduce and quantize tracks, apply the target influence policy, validate materials, and calculate motion bounds. Show source-versus-cooked deformation and compression error before publishing. Extra weights must produce a visible diagnostic or explicit approved reduction, rather than quietly disappearing.
5. **Use the character in LightEngine.** Designers choose an enemy type and compatible character/animation set, place actors, and author patrols as today. Character assets own motion; level files reference them. Reimport preserves actor placement and behavior overrides.
6. **Play the level or a named test start.** The existing launch workflow remains the entry point. Animation inspection and stress fixtures belong inside the editor and developer tools, without adding another daily VS Code task.

Adopting Tiny3D would require an offline bridge from our materials to its supported target representation, or a deliberately supported Fast64 workflow. A generic GLB export is not proof that the current Tiny3D importer accepts every material or exporter version. Keep the source asset contract independent from the selected N64 converter.

## Decisions to establish early

| Decision | Recommended first policy | Reason and escape path |
| --- | --- | --- |
| Rig identity and coordinates | One versioned humanoid deformation rig; meters, +Y up, character forward +Z; a distinct motion root and pelvis. | Share clips and attachments. Convert Blender coordinates once, including bind matrices, normals, sockets and motion curves. Test an asymmetric pose to catch mirrors. |
| Rig complexity | Experiment with roughly 20–24 deformation bones; no animated scale or individual finger rig initially. | Spend joints on readable shoulders, spine, head, hands and feet. Other body plans can have separate rig families. |
| Skinning | One influence per vertex first. | Fits Tiny3D's current path. Author joint loops and clothing around this restriction; consider selective two-weight deformation only after an actual visual/performance comparison. |
| Locomotion ownership | Collision and gameplay move the actor; walking/running clips are in place. | Advance gait from actual traveled distance and authored stride length, so a blocked guard does not keep marching. Keep acceleration and turning consistent with available clips. |
| Root motion for actions | Preserve source motion curves as optional metadata, but defer runtime application. | Later lunges, climbs and takedowns must request collision-resolved movement with explicit interruption rules. Never let a render callback move an actor through a wall. |
| Clip transitions | A small explicit controller, two-source crossfades, synchronized locomotion phase. | Predictable cost and no requirement for a full general-purpose animation graph. Define priorities and whether notice/attack/fall can interrupt each other. |
| Reuse | Shared immutable character assets; independent clocks, blending and pose buffers per actor. | Several guards can share a walk without walking in lockstep. Retarget different proportions offline, with foot-contact validation. |
| Physics and perception | Keep simple gameplay collision and a deliberate eye/aim model. | A decorative head turn should not silently alter the AI's field of view. Selected interactions can request socket or bone queries explicitly. |

For the first art experiment, try a close guard around 300–500 triangles and a cheaper variant around 150–250. These are comparison targets, not acceptance limits. Report the **cooked** vertex count after UV, material, normal and joint boundaries split vertices. Materials, overdraw, attachments, lights and clipping can matter as much as the triangle count.

One-influence meshes can be continuous: vertices on either side of a joint follow different bones, and triangles connect them. The guard demonstrates this with 48 elbow/knee triangles; its [study](guard-animation-prototype.md#connected-joints-and-the-n64-vertex-cache) records the Kaze, Nintendo and Fast64 references. Elbows, shoulders and hips still deform differently from smoothly weighted desktop characters. Inspect crouching, reaching and turning as well as idle poses. The Tiny3D author's asset documentation describes selecting the strongest weight when several are supplied; our current exporter rejects extra weights instead of reducing them silently. [Author's skinning notes](https://hailtododongo.github.io/pyrite64/docs/manual/assets/model3d.html#skinning)

Rigid articulated parts are an especially cheap fit for armor and mechanical enemies. A few morph targets or vertex-animated regions may later help unusual creatures or expressions, but full per-frame vertex animation should not be the default guard format. Begin faces with a jaw/head bone and small eye or mouth texture changes if needed.

## Memory and data layout

Separate four costs in every report:

- **Shared resident assets:** geometry, textures, skeleton/rest pose, hot clips and event tables, counted once per unique bundle asset.
- **Per-actor state:** logical controller, playback cursors, sample caches, pose buffers, attachment state, render matrices and any independently allocated stream decoder/history storage.
- **Temporary work:** decode buffers, crossfade poses, visible-actor processing arenas, alignment and asynchronous graphics buffers.
- **ROM-only data:** optional cold clips and their containers and seek indices while not resident; report their additional RAM cost separately when loaded or streamed.

An illustrative raw-data comparison shows why skeletal clips are attractive. Assume a two-second clip sampled at 15 Hz with 31 samples including both endpoints:

| Payload assumption | Bytes |
| --- | ---: |
| 24 bones × 31 samples × 8-byte quantized quaternion | 5,952 |
| One animated root translation, 31 × 6 bytes | 186 |
| Skeletal motion subtotal | **6,138**, about **6.0 KiB** |
| 500 vertices × 31 samples × 6-byte position | **93,000**, about **90.8 KiB** |

This is arithmetic for a proposed simple format, **not Tiny3D's file layout or a measured asset**. The comparison excludes base meshes, rest translations, any additional animated translations, normals, headers, events, alignment and decoder state. Eight such skeletal clips would have about 48 KiB of those motion payloads, shared by all compatible actors; the raw vertex positions alone would be about 727 KiB. Sparse keys and constant-track removal can improve skeletal storage further.

Likewise, 24 affine 3×4 matrices at four bytes per element occupy 1,152 bytes for one palette. A backend using 64-byte matrices needs 1,536 bytes. These are only palette sizes: local poses, blend scratch, hierarchy transforms and multiple in-flight graphics buffers add to the per-actor allocation. Never advertise the palette subtotal as the full animated-guard cost.

Tiny3D provides a concrete example: its skeleton allocation includes local bone state and a configurable number of fixed-point matrix buffers. With 24 bones and three 64-byte matrix buffers, render matrices alone occupy **4.5 KiB per guard**. Blending poses and playback state add to that. Use target `sizeof` values and actual allocator instrumentation in the editor's estimate. [Skeleton allocation](https://github.com/HailToDodongo/tiny3d/blob/main/src/t3d/t3dskeleton.c), [matrix representation](https://github.com/HailToDodongo/tiny3d/blob/main/src/t3d/t3dmath.h)

**Streaming is not automatically the smallest RAM option.** Tiny3D creates a file stream per animation instance and channel state when it attaches to a pose. Compressed libdragon asset streams also allocate decoder/history storage. Several guards playing the same short clip independently can therefore cost more than one shared resident clip plus small cursors. Compare both strategies; prefer shared resident idle/walk/run data as an architectural goal and measure streaming for longer or rarely used clips. This may require a residency/sampling adapter beyond Tiny3D's stock animation API. [Animation allocation and sampling](https://github.com/HailToDodongo/tiny3d/blob/main/src/t3d/t3danim.c), [libdragon stream allocation](https://github.com/DragonMinded/libdragon/blob/preview/src/asset.c)

The current Tiny3D API does not support reverse playback, and moving backward in time rewinds the stream. Seeking and consuming keys can affect randomized starting phases, reactivation, editor scrubbing and restored test states. Specify these operations in our interface and measure their cost; a forward-only streaming implementation must not define what the editor can preview. [Playback API](https://github.com/HailToDodongo/tiny3d/blob/main/src/t3d/t3danim.h)

Prefer rotation tracks and fixed rest translations where possible. Drop constant channels, quantize using explicit ranges, and reduce keys using errors measured at affected vertices and important endpoints such as feet and held objects. A small shoulder-angle error can produce a large hand displacement. Modern animation-compression work demonstrates the value of measuring deformation error, rather than treating every local rotation equally. We can apply that principle in our offline cooker without adopting a large desktop runtime library. [Animation Compression Library author's error-metric discussion](https://nfrechette.github.io/2023/02/26/dominance_based_error_estimation/)

Sample density and playback rate are separate decisions: storing 15 samples per second does not require visibly updating the character only 15 times per second. Interpolate between keys at the selected pose-update rate. Start with straightforward quantized quaternion interpolation, handling antipodal signs correctly; choose more complex compression only when its measured memory savings justify decode cost.

The package contract must define endianness, alignment, offsets, format/skeleton versions, quaternion convention, time units and loop endpoints. Preserve the current static format and introduce a versioned animated asset type. Runtime loading must reject incompatible rigs and malformed ranges before allocation.

## CPU, RSP and update scheduling

The preferred division is CPU controller/clip sampling/bone pose work, RSP vertex transforms and suitable lighting, and RDP rasterization. Tiny3D follows that broad division: CPU sampling and skeleton code produce matrices for rendering. Keep a portable CPU reference for correctness and target preview. Evaluate Tiny3D before growing a bespoke CPU skinning and per-triangle submission path into the permanent renderer. [Animation implementation](https://github.com/HailToDodongo/tiny3d/blob/main/src/t3d/t3danim.c), [skeletal animation example](https://github.com/HailToDodongo/tiny3d/blob/main/examples/08_animation/main.c)

Separate static-world caches from a bounded character render path. Cook meshes into draw batches compatible with the target's matrix and vertex handling. Keep matrix/vertex buffers alive until asynchronous graphics work has consumed them; reuse through a fenced ring or equivalent bounded ownership scheme. Bone count, visible vertices, draw batches and RSP occupancy all need measurement.

Use independent schedules for logical animation and visual work:

- Advance logical clip phase, gait and semantic events with simulation time, even for culled actors.
- Evaluate a full pose and deformation when it contributes to the current view. Nearby relevant actors receive the best quality; distant actors can use fewer pose updates and lower-detail meshes, with hysteresis to avoid flicker between policies.
- Evaluate essential socket/bone chains when gameplay or an audible foot contact needs a position, even if the mesh is not drawn.
- Share bounded cached base-pose samples for compatible clip/phase combinations where useful; retain independent event clocks and per-actor overlays. Avoid forcing every guard into the same phase just to share a pose.

Reduced visual update rates are an optimization to measure, not permission to reduce AI hearing or skip gameplay events. Modern animation systems likewise separate sampling and blending from higher-level state decisions. Their organization is useful inspiration; their SIMD assumptions and generality do not imply an N64-ready runtime. [ozz-animation runtime design](https://guillaumeblanc.github.io/ozz-animation/documentation/animation_runtime/)

## Footsteps, voices and gameplay events

This integration is essential for DarkLantern64. Animated guards now advance authored walk contacts from actual traveled distance, including while culled, and `src/main.c` sends them through the existing audible guard-step path. Static models retain the distance-threshold fallback. These sounds still do not traverse the player-sound AI-hearing path; terrain-specific contact positions and a shared event queue remain future work. The game's single latest event is insufficient for simultaneous actor stimuli.

Introduce a bounded semantic event queue. A foot-contact event carries an actor ID, simulation timestamp, left/right foot, contact location and movement intensity. Resolve the surface material there, then feed the same event into player audio and AI acoustics. Listener rules can ignore self-generated steps or recognize routine friendly movement; audibility does not automatically mean alarm. Choosing or dropping a playback voice must not erase the corresponding gameplay stimulus. Define priority and overflow behavior explicitly; instrument missed deadlines or overflow.

Advance locomotion markers from a shared gait phase across walk/run blends. Do not fire both clips' footsteps during a crossfade. Handle marker intervals, loops, large time steps, interruption and replay deterministically. Editor scrubbing should be silent by default; explicit audition can play cues without mutating gameplay. Reset/restart must reset event cursors predictably.

Artists specify semantic contacts and gestures, not particular audio file paths. Character sound sets, terrain and acoustics decide whether a step is leather on wood, metal on stone, or muted across a closed door. Voice playback can drive a cheap jaw envelope later; detailed facial blend shapes are a separate budget choice.

## Lighting, attachments and visibility

Store sockets such as `hand_r`, `hand_l`, `head` and `belt` in the character definition. A weapon, lantern or purse uses the socket transform plus an authored offset, while keeping its own gameplay identity where needed. A swinging lantern can attach an actual dynamic light, but its illumination and shadow-query costs must be budgeted separately from the animation.

Transform normals consistently with the pose. The prototype now deforms character normals and combines them with the existing environmental probes and directional lighting response. Its normal cache costs 11,964 bytes per 997-vertex guard, and lighting remains expensive. Preserve cheap environmental visibility probes when replacing the CPU geometry path; avoid per-vertex world shadow traces for every animated guard.

Cook conservative bounds that cover full motion, attachments, transitions and allowed procedural offsets. Rest-pose bounds are insufficient. Sampled motion bounds need conservative padding or stronger bounds construction for interpolation between samples; verify extended weapons, falls and raised arms at frustum edges. A culled guard must still have valid logical state and sound events.

## Modern techniques worth bringing back

| Technique | Proposed use |
| --- | --- |
| Offline rig baking and retargeting | Rich Blender authoring becomes a small fixed runtime rig; reuse motion across a compatible character family. |
| Error-bounded compression | Spend precision on feet, hands, silhouettes and held-object sockets instead of uniform precision everywhere. |
| Phase-synchronized blending | Move smoothly from patrol to pursuit with fewer foot slides and duplicate step sounds. |
| Small masked or additive layers | Later, turn the head toward a sound or raise a lantern while the legs keep walking. Limit active layers. |
| Animation and mesh LOD | Spend pose/deformation work where it is visible while preserving the logical timeline. |
| Limited procedural adjustment | After the baseline, compare head tracking, turn lean and two-bone foot placement on the closest important guard. Include ground-query cost. |

Full ragdolls, simulated cloth, large facial rigs, unrestricted multi-weight skinning and large motion-matching databases should wait. We can achieve readable, expressive motion with authored clips and a few controlled adjustments before paying those memory and processing costs.

These libraries are not limited to emulator-only experiments. Cathode Quest 64 has a published N64brew game-jam source release based on the author's libdragon/Tiny3D engine. That is evidence of game integration, not a frame-rate promise for our guards. Tiny3D's animation example remains the simpler source to inspect for the animation mechanics. [Cathode Quest 64 source](https://github.com/N64brew-Game-Jam-2025/Cathode-Quest-64), [engine author](https://github.com/HailToDodongo/pyrite64)

## First implementation milestone

The initial source/export/cook/preview/runtime milestone is implemented and measured in the [guard animation study](guard-animation-prototype.md). Remaining work below focuses on backend comparison, a broader controller and scale. Idle/walk blending, manual transition preview and head attention are available; attachment rendering, skeleton/socket overlays, foot IK and the shared audio/AI event system are not yet implemented.

Use one asymmetric, artist-editable humanoid with idle, walk, run, notice and turn clips, a hand socket, and two foot-contact markers. Include a planted-foot loop and a wide-reaching pose specifically to expose sliding, deformation and bounds errors.

First establish export, cooking, target preview and one animated guard following its existing patrol. Then compare the CPU reference against an RSP path in the same room, with the same camera, mesh, lighting and ordinary audio. Include the existing material conversion and SDK compatibility work in this spike. Do not make full smooth skinning a prerequisite for evaluating the geometry backend.

Next run 1, 2, 4 and 8 actor cases with visible and occluded groups, synchronized transitions, a moving attachment and audio-heavy moments. These are attempted workloads, not promised supported counts. Report simulation, sampling, blending, hierarchy, transforms/skinning, lighting, submission, queue waits, RSP activity and audio underruns separately. Record shared resident assets, per-actor allocations and transition high-water memory; the earlier mission-scale guard studies exclude skeletal animation, and the new prototype provides only a short two-guard runtime measurement.

The editor's first animation panel should provide clip selection, play/pause/scrub, transition preview, skeleton/socket display, contacts, source-versus-cooked comparison, and a target cost report. Its command interface should expose the same deterministic clip/time/transition controls for automated comparison captures. Ares proves runtime integration; original N64 and M64 establish final performance and presentation.

Generic LightEngine character-preview changes belong on an engine branch and PR. DarkLantern64 owns character roles, guard state mapping, contact semantics, target budgets and compiled assets. This keeps the art workflow useful while allowing the renderer and compression implementation to evolve.
