# Level loading and static lighting

Sponza renderer preparation fell from **3695.68 ms to 427.54 ms**, an **8.64×
speedup**. The broader reset-to-ready timer measures **430.87 ms** with baking.
The baked static colors match all **133,056 RGBA bytes** produced by the
original N64 calculation, across both door states.

The [before timing record](evidence/level-loading-before.json) and
[complete loading verification](evidence/level-loading.json) preserve the
measurement scopes, ROM identities, stage totals and fallback checks.

| Supplied level | Renderer preparation | Full reset to ready |
| --- | ---: | ---: |
| The Lantern Store | 368.48 ms | 370.89 ms |
| Moonlit Delivery Yard | 299.80 ms | 302.78 ms |
| Enemy Patrol Workshop | 439.44 ms | 441.90 ms |
| Guard Animation Workshop | 166.49 ms | 168.80 ms |
| Sponza After Hours | 427.54 ms | 430.87 ms |

Moonlit Delivery Yard's explicit runtime-lighting fallback takes 1861.11 ms,
versus 299.80 ms with its bake. Its 78,336 static RGBA bytes also match exactly.
The other three supplied levels use legacy scalar lighting and are unaffected
by the night-lighting bake.

The main loading cost was ray-traced static vertex lighting. Every level start
recomputed the environment's lighting for every static triangle corner, once
with the door closed and again with it open. Precomputing both states avoids a
pause during gameplay when the player opens a door, but doing it on the N64
made the level selector slow.

The ordinary Sponza courtyard build measured the following renderer startup
stages in Ares, using the emulated CPU timer with interrupts included:

| Stage before baking | Milliseconds |
| --- | ---: |
| Lighting, current door state | 1696.82 |
| Lighting, alternate door state | 1693.57 |
| RSP geometry setup | 222.76 |
| Geometry and cache allocation | 68.72 |
| Textures | 6.77 |
| HUD | 4.62 |
| Retirement, door restoration and diagnostic records | 2.43 |
| **Total renderer preparation** | **3695.68** |

Static lighting accounts for **91.7%** of this measured delay. The texture set
is small enough that texture streaming would have little effect on startup.
This is renderer preparation time, not editor compilation time, emulator ROM
opening time, or a stopwatch measurement from menu input to the first image.

The first ordinary baked run spent 52.13 ms reading, checking and expanding
the bake, 64.16 ms on remaining lighting/face preparation, and 227.18 ms on RSP
setup. RSP geometry preparation is now the largest stage; static light rays
no longer dominate startup.

The older `scene_prepared` diagnostic ends before RSP geometry and HUD setup.
It remains available for comparison with earlier reports; use `scene_load`
for the complete renderer preparation stages. Mixing those two scopes would
overstate the improvement.

## Content workflow

**Save + Cook**, **Play level**, **Play game menu**, and the existing VS Code
game task generate static night lighting automatically. The calculation uses
the compiled mesh positions and packed normals, placed model transforms,
materials, collision proxies, lights and environment settings. Both door
states are baked. There is no extra task or manual export step.

The bake runs in a small native host program compiled with GCC. The supported
Windows setup already uses MinGW/MSYS2 GCC for the renderer toolchain and
portable gameplay tests. `DL64_HOST_CC` can select a particular host GCC;
otherwise the cooker checks PATH and the established Windows installation
paths. Cooked lighting is cached against the generated
content, relevant lighting code and host compiler inputs. A changed recipe
regenerates it; an unchanged recipe reuses the checked output.

On the development host, a private Sponza cook took **3.60 seconds cold** and
**0.32 seconds on an unchanged repeat**. The lighting step accounted for
**0.68 seconds** and **0.06 seconds**, respectively. The first lighting step
includes compiling the host worker. These are host wall-clock measurements,
separate from the emulated N64 startup timer. See the [cook timing evidence](evidence/lighting-cook-timing.json).

The desktop viewport still uses LightEngine's lighting. Check final N64
lighting through **Play level**, as before.

## Runtime and memory

Each night level references a compact file in DragonFS. At startup the game
streams its colors into the existing two-state lighting cache. Inactive
levels' lighting files stay in ROM. The file carries an identity, bounded
model descriptors and a checksum; invalid data falls back to calculating
lighting on the console.

Sponza's file is **51,292 bytes**: 49,896 bytes of RGB and 1,396 bytes of
identity/layout data. The active level keeps its existing 187,296-byte
two-state cache; the streaming decoder uses a 512-byte read buffer. The bake
does not add another full resident copy of the level's lighting.

Single-sided meshes store RGB for the front only; the back reuses the same
color. Double-sided meshes retain separate colors for both sides. Guards,
rotating objectives and animated meshes keep their runtime lighting. Fog and
loot pulsing remain runtime presentation effects. Baking does not change AI
visibility or sound occlusion.

This is an optimization of the existing static-lighting design. Moving a
light or arbitrary occluder during gameplay still needs an explicit lighting
update strategy. The existing global door state is represented by two baked
variants; many independent doors should not be implemented by multiplying
full-level variants for every combination.

## Verification tools

`--verify-lighting-bake` builds a separate diagnostic ROM that compares every
static cached RGBA byte against the original on-console lighting calculation,
including both door states. `--disable-lighting-bake` builds a separate ROM
that forces the original calculation for timing comparisons. Neither flag is
used by ordinary game launches.

Fixed N64 framebuffer captures check the visible result. Bundle switching and
restart checks cover repeated loading and memory use. Ordinary load timings
must be measured separately from the diagnostic that recomputes lighting.

The completed run passes all five supplied levels, both night-level color
comparisons, an untextured night-level ROM, a missing bake descriptor and a
deliberately corrupted bake checksum. The latter two cases complete startup
through runtime fallback. All eight Sponza views and both guard views remain
[byte-identical](evidence/level-loading-visual-parity.json). The
[two-guard 60 fps check](evidence/guard-60fps.json) and
[102-transition bundle check](evidence/sponza-bundle.json) also pass.
