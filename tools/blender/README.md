# Blender asset-pack exporter

The add-on in `darklantern64_export/` exports selected mesh objects to the same
OBJ, material JSON and PNG inputs used by the LightEngine preview and N64 cooker.
It requires Blender 4.2 or newer. Keep the editable `.blend` file as the source;
the export is a generated asset pack.

## Install and export

Zip the **directory** `darklantern64_export` so that the zip contains
`darklantern64_export/__init__.py` and `darklantern64_export/core.py`. In Blender,
open Preferences → Add-ons → Install from Disk, choose the zip, and enable
**DarkLantern64 Asset Pack**. The repository's `tools/blender_assets.py` helper
can package and run this add-on without installing it into Blender preferences.

1. Model in metres. Blender's Scene → Units → Unit Scale is respected even when
   the unit display is set to None. Put each mesh's origin at its intended game
   pivot, usually its base. Export one mesh object per reusable game model.
2. Give each object a string custom property `dl64_asset_id`, for example
   `loot-goblet`. Give its single material a string custom property
   `dl64_material_id`, for example `mat-loot-atlas`. IDs start with a letter and
   use ASCII letters, numbers, underscores or hyphens. Object IDs may have up to
   59 characters because the mesh ID receives the `mesh-` prefix.
3. Select the desired mesh objects and leave Edit Mode. Choose File → Export →
   **DarkLantern64 Asset Pack (.json)**. Set **Content Root** to this project's
   `content` directory. Export `pack.json` in a child directory such as
   `content/assets/loot/`.
4. Import the exported pack into a level using the project asset helper/editor
   workflow. Existing copies of material definitions remain unchanged. The
   importer rejects a changed definition under an existing material ID; use
   editor material controls or a new material ID for an intentional change.

Each object receives one OBJ and one prefab definition. Shared materials and
textures are exported once per material ID. A material image is saved as
`<material-id>.png`; a model is saved as `<object-id>.obj`. All manifest URIs are
relative to the selected content root. OBJ `vt` coordinates retain Blender's
upward V convention; the existing game cooker performs its own V conversion.

The exporter evaluates mesh modifiers and bakes the object's world rotation and
scale, including its parents' linear transforms. It removes world translation
so models arranged on a Blender presentation table do not inherit those table
positions. The object origin remains the exported pivot. Blender coordinates
`(x, y, z)` become game coordinates `(x, z, -y)`. Mirrored transforms reverse
triangle winding to retain outward faces. Faces are triangulated by Blender;
the exporter preserves UV seams and rejects degenerates.

## Material contract

Use one material slot per object, including evaluated geometry. Join component
meshes and assign their faces to regions of a shared UV atlas for multicolored
objects. Multiple material slots are rejected, even if an extra slot is unused.

The exporter accepts a plain material diffuse color, or one Principled BSDF
connected directly to the active Material Output. Principled Base Color can be
a constant RGBA color or connect directly to the Color output of an Image
Texture. An image uses Flat projection, Repeat extension, and the active UV map
(leave Vector unconnected, or connect Texture Coordinate → UV). Mapping nodes,
named UV overrides and other coordinate sources must be baked before export.

Texture dimensions must each be a power of two from 1 to 64, with
`width × height × 2 <= 4096` bytes. A 32×32 RGBA16 atlas occupies 2 KiB of decoded
pixel data; 64×32 occupies all 4 KiB of TMEM. Source pixels must be in 0..1 and
opaque. The main cooker quantizes the PNG to RGB555 with one alpha bit and
creates the libdragon sprite. The exporter does not perform that second cook.

Constant emission color multiplied by emission strength maps to the runtime
`emissive` RGB field, with each component limited to 0..1. Backface culling maps
to `double_sided`. **Metallic and roughness constants are Blender authoring
settings only**: the runtime uses its existing lighting model and the atlas;
these constants do not create reflections or specular shading in the game.
The exporter rejects linked roughness, metallic, normal, alpha and emission
inputs, as well as transmission, subsurface, coat, sheen, volumes and
displacement. Bake the desired appearance into the small Base Color atlas or
model geometry. Transparent textures/materials are currently rejected.

Models are checked against the cooker's per-mesh limits of 4,096 vertices after
UV seams and 4,096 triangles. These are validity limits, not recommended prop
budgets. Level-wide instance totals, memory and performance remain the main
cooker's responsibility; keep ordinary loot models much smaller.

## Script API

Run this inside Blender, for example from its Python console or `--background`
with a Python script. No Blender GUI context override or OBJ plug-in is needed.

```python
from pathlib import Path
import sys
import bpy

project = Path(r"C:\Users\hughe\dev\DarkLantern64")
sys.path.insert(0, str(project / "tools" / "blender"))
from darklantern64_export import export_pack

manifest = export_pack(
    bpy.context,
    project / "content",
    project / "content" / "assets" / "loot",
    objects=[obj for obj in bpy.context.scene.objects
             if obj.type == "MESH" and obj.get("dl64_asset_id")],
)
```

Omit `objects` to export the selected meshes. The function returns the version-1
manifest written to `pack.json`. It validates all geometry/materials and stages
all generated files before publishing; the manifest is replaced last. Failed
file replacement attempts restore earlier changes in that export. Unchanged
files are left untouched. Re-export updates files owned by that pack; the
exporter rejects pre-existing unrelated files and conflicting asset/material
IDs elsewhere under the content root. Removing an object from the selection
removes its entry from the new manifest but leaves its old source file on disk,
so existing levels are not broken by automatic deletion.

## Painted animated guard

Open `art/guard.blend` and paint the packed image named
`Guard | PAINT THIS painted atlas source`. The guard material uses this image;
its `dl64_texture_export_name` property exports it as `guard-atlas-painted.png`.
Save the Blender file, then run `python tools/guard_assets.py` and cook the level
normally. The shared guard pack specifies the 64×64 CI4 runtime texture. The
original `guard-atlas.png` remains a historical asset.

The separately packed image named `TARGET PREVIEW` is a generated snapshot, not
the painting source. Re-cooking in the editor creates a current target preview;
the source image in Blender deliberately retains its authoring detail.
`tools/blender/paint_guard_uv.py` is the one-time layout migration helper: it
replaces the guard's UVs and should not be rerun over an artist's custom unwrap.
`tools/blender/render_guard_atlas.py` renders source and cooked images in a
private process without saving changes to the scene.
