# Shared guard resources

Guard appearance belongs to a shared asset resource. A level places instances of that resource and supplies their mission behavior.

| Shared across levels | Authored per guard instance |
| --- | --- |
| Mesh, skeleton, animation clips and atlas | Position, rotation and scale |
| Material and visual prefab defaults | Enemy type and behavior selection |
| Enemy type's code defaults | Patrol waypoint references and route order |
| Resource IDs and source file paths | Explicit speed, sight and hearing overrides |

The previous levels already shared `content/models/guard.obj`, and their gameplay types came from `src/enemy_types.def`. However, each level independently declared which model/material IDs represented its guard. Only the animation workshop had been updated to the animated asset. The editor then copied the first existing guard when adding another, perpetuating those old references.

## One visual definition

[The shared guard pack](../content/assets/guard/pack.json) defines the character asset, material and visual defaults. It references the same [character source](../content/assets/guard/guard.character.json) and [atlas](../content/assets/guard/guard-atlas.png) used by the animation workshop. Watchman and scout currently share this appearance while retaining their distinct gameplay types.

Each supplied level links the pack through:

```json
"asset_packs": ["assets/guard/pack.json"]
```

The pack and its asset URIs are relative to the content directory. Entities retain stable `model` and `material` IDs; the cooker resolves their definitions from the linked resource. Saving a level retains the link and instance data instead of copying shared definitions into the level file. Conflicting resource IDs are errors, so importing a pack cannot silently overwrite a hand-authored definition.

The four supplied levels use this resource. Migration preserves guard IDs, positions, rotations, scales, patrols and tuning. The animated mesh is about 1.845 m tall versus the earlier 1.6 m placeholder; both use a feet-at-ground origin, so visual height changes without moving the authored actor or changing gameplay collision.

## Editor workflow

Choose **New enemy type** and use **Add enemy** to create an instance from its shared visual default. Duplicating a guard preserves its resource references and independent instance data. A level does not need an existing guard to supply the model.

Linked material controls identify their shared source. Edit the pack or source artwork to change every linked level; use **Save + Cook** or reopen a level to refresh resolved resources and animation previews. The canonical level file retains its resource links throughout this process. The Blender authoring/export workflow remains unchanged.

After updating the editor executable, reopen the editor and level to load the new resource support. Existing sessions are left open to preserve unsaved work; source conflict checks prevent them from silently overwriting a migrated file.

## Runtime and budget

The bundle compiler already shares character geometry and compressed clip data across levels. Resource sharing therefore does not require a separate copy of the guard asset for every level. Each live guard still owns its AI state, animation pose, lighting and buffered RSP submission data; sharing a definition does not make additional guards free.

After the sleeve correction, the supplied scenes contain up to 4,701 instanced attribute vertices. The scene limit is 6,144 vertices; an individual mesh remains limited to 4,096. Separating these limits keeps per-model lighting/fog scratch unchanged and adds only 24 KiB to the default renderer's world-position cache. The CPU reference path also retains camera coordinates and clip codes, adding 50 KiB there. The triangle limit remains 4,096. These are allocation limits, not guarantees about frame rate or supported crowd size.

## Verification

The [migration and editor verification record](evidence/shared-guard-resources.json) confirms unchanged entity placements/routes/tuning, one bundled character definition with 8,820 bytes of clip keys, and shared material edits refreshing across levels without copying their definitions. A private editor exercised creation in an empty level, type changes, duplication, save/reopen and the existing loot import workflow. The mission and all 66 level/menu transitions passed, with stable heap use on repeated starts. This record identifies the exact asset revision tested; later artwork changes require their own checks.
