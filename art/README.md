# Editable art sources

`loot.blend` contains five original Blender meshes for DarkLantern64: coins, a goblet, a cut garnet, a drawstring purse, and a scepter. Their shared 32×32 atlas was generated inside Blender; no downloaded artwork is used.

The active **DarkLantern64 Loot** scene contains an **EXPORT** collection with the five tagged mesh objects. Its separate **PRESENTATION** collection contains the studio floor, camera, and lights. Those presentation objects are excluded from game exports.

Save edits here, then run `python tools/blender_assets.py` from the repository root. The saved source exports into `content/assets/loot/`. The command does not overwrite the source or edit level placements.

`tools/blender/create_loot.py` is the reproducible starting-point generator. Running `--make-demo` overwrites the selected source and exports, so use ordinary export for artist-modified models. Blender backup files (`.blend1`, etc.) are ignored by Git.

See [the creator workflow](../docs/blender-assets.md) for installation, materials, placement, budgets, and launching the game.
