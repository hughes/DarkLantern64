# Thief: Bafford conversion study

The first campaign mission, **Lord Bafford's Manor**, is converted from the local
Thief Gold installation. It is `miss2.mis`; `miss1.mis` is the training mission.
This is a proof of geometry and authoring scale, with a separate small runtime
probe. The full mission is not a playable N64 port yet.

The [full-mission runtime plan](thief-bafford-runtime-plan.md) describes the
memory, streaming, visibility, collision and gameplay work needed to run the
whole mission, with a proposed implementation sequence and acceptance checks.

Open **`art/thief-bafford.blend`** for the complete editable architecture.
**`art/thief-bafford-entry.blend`** contains the connected starting street probe.
Both are ordinary checkouts using our DarkLantern add-on, with original packed
terrain textures, UVs, metre coordinates and editable box proxies. Architecture
is grouped by material and spatial region. Each mesh face has `thief_cell_id`
and `thief_polygon_id` attributes. Object and material `dl64_source` properties
keep original IDs and surface sound tags. Blender Text datablocks retain the
conversion report, source objects, original cell vertices, portal openings,
compiled light locations and the complete BSP tree.

| Data | Original mission | Adapted full source study |
| --- | ---: | ---: |
| Portal cells | 4,991 | 4,984 cells with retained visible surfaces |
| Solid fan triangles | 75,266 | 51,268 architecture triangles |
| Architecture objects | BSP surfaces | 1,400 material / region meshes |
| Terrain materials | 137 used ordinary textures | 137, with original texture pixels |
| Compiled lights | 365 | 365 original emitter locations |
| Armed guards | 19 | 19 using our guard model |
| Ordinary loot | 23 / 1,429 value | 23 / 1,429 value using our loot models |
| Bafford's scepter | Scripted object 74 | One objective using our scepter model |
| Box proxies | Original cell boundaries | 16,605 exact rectangular face proxies |

The triangle reduction removes straight-edge subdivision points left by the
original portal compiler, preserving the surface shape. It also omits 570 sky
boundary faces, 330 water portal surfaces, and two submillimeter source slivers.
The adaptation chooses **0.3048 metres per source unit**, with Thief XYZ mapped
to game `(X, Z, -Y)`. Winding is checked against the inward cell plane; UVs use
the inverse of Thief's potentially skewed texture basis and the original image
dimensions. Source yaw becomes game yaw `90 + source yaw`.

The full scene is saved as `content/thief_bafford.json` with the explicit
`external-scale-study` authoring profile. Our importer and editor preview accept
its larger host budgets. Native Play, ROM compilation and bundle membership
reject that profile. Normal runtime limits remain 6,144 vertices, 4,096
triangles, 128 model instances, 256 entities / boxes, 16 lights and 16 guards.
The full study has 123,074 instanced vertices and 64,457 triangles including
repeated guard and loot visuals. Seamless N64 traversal needs spatial streaming
and visibility work; increasing resident array limits would not establish that.

The entry probe uses the unchanged runtime format: 37 connected cells, 475
triangles, 47 architecture meshes, 155 box proxies and 16 nearby light emitters.
Its source neighborhood has no imported guard or loot placements. The probe is
a geometry / texture / lighting sanity check, rather than a full stealth mission.
Some edges open into omitted cells, and surfaces without box proxies remain
nonblocking. Use it for a limited walk around the starting street.

The menu uses the expanded streamed variant as `bafford-expanded`; see the
[street expansion](thief-bafford-expanded.md) for generation and current
verification, and the earlier [streaming conversion](thief-bafford-streaming.md)
for the initial entry package. The original editable entry remains available
for comparison and standalone builds.
Build the main ROM with `python tools/build.py --bundle content/level_bundle.json --run`
and choose **Bafford Streets Expanded** in the level selector.
The full source study remains excluded from runtime bundles. On a fresh checkout,
run the local Thief importer first: the generated level, textures and models
remain ignored by Git and must be present before building this bundle.
The earlier [menu verification record](evidence/thief-bafford-menu.json) confirmed all
six entries launch, restart and resume in Ares; Bafford's heap is stable across
all six fresh-launch/restart samples.

## Adaptations and omitted behavior

Water/swimming, ladders, floor trapdoors, object doors, decorative props, source
mission scripts, servants and spiders are omitted. Their source data stays in
the Blender provenance text. Ordinary loot in containers is placed at the
container position, with the original child position retained. Starting
inventory loot is excluded. The scepter is represented as a DarkLantern
objective; original difficulty goals and mission scripting are not reproduced.

Collision boxes are produced only for axis-aligned rectangular faces. Bounds
are not filled across sloped or nonrectangular architecture: 5,553 sloped faces
and 3,922 nonrectangular / very thin faces need richer collision or manual
authoring. The full study's proxy collision is therefore incomplete. Guard and
player body origins are projected onto containing horizontal floors to produce
game feet positions. The original body positions remain in provenance. Guards
use sentry behavior; patrol nodes, explicit links and inferred source assignments
are retained for subsequent navigation work.

Terrain material tags preserve stone, wood, tile, glass, carpet, metal, earth
and vegetation categories. The runtime currently emits one footstep sound;
the conversion does not yet make these tags affect playback or AI hearing.

Compiled WR light locations include 106 more emitters than the source object
property list. The converter preserves all 365 compiled records, maps 84
animated lights by exact table indices, and annotates 174 one-to-one static
property matches as approximations. Light brightness / 125 maps to our
intensity; unbounded source lights use an 8 m radius. Spot cones and animated
cycles remain metadata. Our lighting is an approximation of the original game.

## Reproduce and edit

Generated game assets and Blender files are ignored by Git; no copied
commercial assets are shipped with the importer. The source installation is
read only. Generation refuses to overwrite existing `.blend` sources.

```powershell
python tools/import_thief.py --install "C:\Program Files (x86)\Steam\steamapps\common\thief_gold"
```

For interrupted generation, `--finish-prepared` resumes the Blender/export/import
stages from `build/thief-bafford/report.json`, preserving existing source files.
`--prepare-only` produces adapted documents and runtime cook before Blender.

Edit architecture in Blender, use **DarkLantern → Export level changes**, then
**Import Blender changes** in DarkLantern. Gameplay markers are references:
author light, guard and loot changes in DarkLantern. The same operations work
from the command line:

```powershell
python tools/blender_scene.py export --source art/thief-bafford.blend
python tools/blender_scene.py import --exchange build/blender/thief_bafford/SCENE_ID/scene.json
python tools/project_levels.py cook --level thief_bafford.json
python tools/build.py --level content/thief_bafford_entry.json --run
```

Installable add-on: `build/darklantern64_export.zip`. Detailed generated evidence
lives in `build/thief-bafford/report.json`; binary format sources and layouts
are documented in [the worldrep research](research/thief-worldrep.md).

The overview image is a temporary ceiling cutaway rendered from original
materials; its lighting is a visualization approximation. The entry ROM
capture uses the actual RDP framebuffer. Neither render modifies the `.blend`.

Verified locally on 2026-10-12: both Blender checkouts exported and imported
through the existing bridge; the full host preview cooked successfully; the
project editor rebuilt with its study Play guard; and the Tiny3D entry capture
booted in Ares with 8 MiB and exported the expected framebuffer. The ordinary
playable entry ROM is `build/DarkLantern64-thief_bafford_entry-t3d.z64`. These
checks establish the conversion and entry probe, not full mission traversal or
original hardware performance.

```powershell
$env:DL_THIEF_GOLD = "C:\Program Files (x86)\Steam\steamapps\common\thief_gold"
python -m unittest tests.test_thief_dark tests.test_thief_objects tests.test_thief_geometry tests.test_thief_conversion
```
