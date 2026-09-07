"""Validate a Blender asset pack and merge its reusable definitions into a level.

Paths in packs are relative to content/, including OBJ and texture paths. Linked
asset_packs are resolved for cooking without flattening the authored level.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}\Z")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def content_path(uri, asset_root, suffixes):
    require(isinstance(uri, str) and bool(uri), "Asset URI must be a nonempty string")
    require("\\" not in uri and ":" not in uri, "Use a content-relative URI with forward slashes")
    relative = Path(uri)
    require(not relative.is_absolute() and ".." not in relative.parts,
            "Asset URI must stay inside content/")
    root = Path(asset_root).resolve()
    path = (root / relative).resolve()
    require(path.is_relative_to(root), "Asset URI resolves outside content/")
    require(path.suffix.lower() in suffixes and path.is_file(), f"Missing or unsupported asset: {uri}")
    return path


def valid_id(value, label):
    require(isinstance(value, str) and ID.fullmatch(value), f"{label}: invalid stable ID")
    return value


def vector(value, label, length=3, low=-1024, high=1024):
    require(isinstance(value, list) and len(value) == length, f"{label}: expected {length} components")
    require(all(type(v) in (int, float) and math.isfinite(v) and low <= v <= high for v in value),
            f"{label}: components must be finite and in {low}..{high}")
    return value


def fields(item, required, optional, label):
    require(isinstance(item, dict), f"{label}: expected an object")
    require(set(required) <= set(item), f"{label}: missing required fields")
    require(not (set(item) - set(required) - set(optional)), f"{label}: unknown fields")


def load_pack(uri, asset_root=ROOT / "content"):
    path = content_path(uri, asset_root, {".json"})
    require(path.stat().st_size <= 262144, "Asset pack manifest exceeds 256 KiB")
    pack = json.loads(path.read_text(encoding="utf-8"))
    fields(pack, {"version", "assets", "materials", "prefabs"}, set(), "Asset pack")
    require(type(pack["version"]) is int and pack["version"] == 1, "Asset pack version must be 1")
    seen = set()
    for group, limit in (("assets", 64), ("materials", 64), ("prefabs", 128)):
        # Prefab names live in the editor catalog, separately from canonical
        # mesh/material/entity IDs. A prop may legitimately use its prefab name.
        if group == "prefabs":
            seen = set()
        values = pack[group]
        require(isinstance(values, list) and 1 <= len(values) <= limit,
                f"Asset pack requires 1..{limit} {group}")
        for item in values:
            require(isinstance(item, dict), f"{group}: expected an object")
            ident = valid_id(item.get("id"), group)
            require(ident not in seen, f"Duplicate asset pack ID: {ident}")
            seen.add(ident)
    for item in pack["assets"]:
        fields(item, {"id", "uri"}, {"type"}, item["id"])
        require(item.get("type", "mesh") in ("mesh", "character"), "Unsupported asset type")
        character = item.get("type") == "character"
        path = content_path(item["uri"], asset_root, {".json"} if character else {".obj"})
        require(path.stat().st_size <= (32 if character else 8) * 1024 * 1024, f"{item['id']}: asset source too large")
        if character:
            try:
                from character_assets import load_character
            except ModuleNotFoundError:
                from tools.character_assets import load_character
            load_character(path)
    for item in pack["materials"]:
        fields(item, {"id", "color"}, {"texture", "emissive", "double_sided"}, item["id"])
        vector(item["color"], "Material RGBA", 4, 0, 1)
        if "emissive" in item:
            vector(item["emissive"], "Emission RGB", low=0, high=1)
        if "double_sided" in item:
            require(type(item["double_sided"]) is bool, "double_sided must be boolean")
        if "texture" in item:
            texture = item["texture"]
            fields(texture, {"uri", "width", "height", "format"}, set(), "Texture")
            require(texture["format"] == "RGBA16", "Texture format must be RGBA16")
            for axis in ("width", "height"):
                n = texture[axis]
                require(type(n) is int and 1 <= n <= 64 and not n & (n - 1),
                        "Texture dimensions must be powers of two from 1 to 64")
            require(texture["width"] * texture["height"] * 2 <= 4096,
                    "Texture exceeds the 4 KiB TMEM limit")
            content_path(texture["uri"], asset_root, {".png", ".jpg", ".jpeg"})
    models = {item["id"] for item in pack["assets"]}
    materials = {item["id"] for item in pack["materials"]}
    for item in pack["prefabs"]:
        fields(item, {"id", "model", "material", "scale"}, {"enemy_type"}, item["id"])
        require(isinstance(item["model"], str) and item["model"] in models,
                f"{item['id']}: unknown pack model")
        require(isinstance(item["material"], str) and item["material"] in materials,
                f"{item['id']}: unknown pack material")
        vector(item["scale"], "Prefab scale", low=.001, high=1024)
        if "enemy_type" in item:
            valid_id(item["enemy_type"], "Prefab enemy_type")
    return pack


def resolve_asset_packs(document, asset_root=ROOT / "content"):
    """Return an expanded copy, catalog and manifest dependencies; never edit input.

    Linked definitions have one owner. A local or second-pack duplicate is an
    error even when equal, so an edit cannot accidentally shadow shared art.
    Packs cannot link further packs, keeping resolution finite and predictable.
    """
    require(isinstance(document, dict), "Level must be an object")
    uris = document.get("asset_packs", [])
    require(isinstance(uris, list) and len(uris) <= 16, "asset_packs: expected at most 16 pack URIs")
    result = copy.deepcopy(document)
    origins, dependencies, prefabs, used, prefab_ids = {}, [], [], set(), set()
    local_definitions = set()
    for group in ("assets", "materials", "entities"):
        values = result.get(group)
        require(isinstance(values, list), f"Level {group} must be an array")
        for item in values:
            require(isinstance(item, dict), f"Level {group}: expected an object")
            ident = valid_id(item.get("id"), f"Level {group}")
            require(ident not in used, f"{ident}: duplicate ID")
            used.add(ident)
            if group != "entities":
                local_definitions.add(ident)
    seen_paths = set()
    for uri in uris:
        path = content_path(uri, asset_root, {".json"})
        require(path not in seen_paths, f"Duplicate linked asset pack: {uri}")
        seen_paths.add(path)
        pack = load_pack(uri, asset_root)
        dependencies.append((uri, path))
        for group in ("assets", "materials"):
            for item in pack[group]:
                ident = item["id"]
                require(ident not in used, f"Linked definition {ident} from {uri} conflicts with a local or linked ID; remove the duplicate definition")
                used.add(ident)
                origins[ident] = uri
                result[group].append(copy.deepcopy(item))
            require(len(result[group]) <= 64, f"Resolved level exceeds 64 {group}")
        for item in pack["prefabs"]:
            ident = item["id"]
            require(ident not in prefab_ids, f"Duplicate linked prefab: {ident}")
            require(ident not in local_definitions, f"Linked prefab {ident} conflicts with a local definition ID")
            # Catalog IDs may match entities, but must not overwrite an asset's
            # different ownership in the shared origins map.
            require(ident not in origins or origins[ident] == uri, f"Conflicting linked prefab origin: {ident}")
            prefab_ids.add(ident)
            origins[ident] = uri
            prefabs.append(copy.deepcopy(item))
        require(len(prefabs) <= 128, "Resolved prefab catalog exceeds 128 entries")
    catalog = {"assets": copy.deepcopy(result["assets"]),
               "materials": copy.deepcopy(result["materials"]),
               "prefabs": prefabs, "origins": origins}
    return result, catalog, dependencies


def merge_pack(document, uri, asset_root=ROOT / "content", prefabs=None):
    """Return (new_document, new_prefab_catalog), preserving inputs on failure.

    Identical definitions are idempotent. A conflicting ID always fails; imports
    never silently replace hand-edited material or model definitions.
    """
    pack = load_pack(uri, asset_root)
    result, catalog = copy.deepcopy(document), copy.deepcopy(prefabs or [])
    require(result.get("version") == 2, "Asset packs require a version-2 level")
    resolved, _, _ = resolve_asset_packs(result, asset_root)
    existing = {}
    for group in ("assets", "materials", "entities"):
        for item in resolved[group]:
            ident = valid_id(item.get("id"), f"Level {group}")
            require(ident not in existing, f"Duplicate level ID: {ident}")
            existing[ident] = (group, item)
    for group in ("assets", "materials"):
        for item in pack[group]:
            ident = item["id"]
            if ident in existing:
                require(existing[ident] == (group, item), f"Conflicting definition: {ident}")
            else:
                result[group].append(copy.deepcopy(item))
                existing[ident] = (group, item)
        require(len(result[group]) <= 64, f"Level exceeds 64 {group}")
    require(isinstance(catalog, list), "Prefab catalog must be an array")
    by_id = {item["id"]: item for item in catalog}
    require(len(by_id) == len(catalog), "Duplicate prefab catalog ID")
    for item in pack["prefabs"]:
        ident = item["id"]
        if ident in by_id:
            require(by_id[ident] == item, f"Conflicting prefab definition: {ident}")
        else:
            catalog.append(copy.deepcopy(item))
            by_id[ident] = item
    require(len(catalog) <= 128, "Prefab catalog exceeds 128 entries")
    return result, catalog


def place_prop(document, prefabs, prefab, ident, position, rotation=None, scale=None, *, loot_highlight=False):
    """Return a new level with one decorative, non-colliding model instance."""
    template = next((item for item in prefabs if item["id"] == prefab), None)
    require(template is not None, f"Unknown prefab: {prefab}; import its pack first")
    require(type(loot_highlight) is bool, "loot_highlight must be a boolean")
    valid_id(ident, "Prop")
    used = {item["id"] for group in ("assets", "materials", "entities") for item in document[group]}
    require(ident not in used, f"Already used object ID: {ident}")
    require(len(document["entities"]) < 256, "The level limit is 256 objects")
    require(sum("model" in e for e in document["entities"]) < 128, "The level limit is 128 model instances")
    entity = {"id": ident, "kind": "static", "model": template["model"], "material": template["material"],
              "loot_highlight": loot_highlight,
              "transform": {"position": vector(position, "Position"),
                            "rotation": vector(rotation if rotation is not None else [0, 0, 0], "Rotation", low=-3600, high=3600),
                            "scale": vector(scale if scale is not None else template["scale"], "Scale", low=.001, high=1024)}}
    result = copy.deepcopy(document)
    result["entities"].append(copy.deepcopy(entity))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", type=Path, required=True)
    parser.add_argument("--pack", required=True, help="Pack URI relative to content/")
    parser.add_argument("--asset-root", type=Path, default=ROOT / "content")
    parser.add_argument("--catalog", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="Staged JSON result; canonical level is not overwritten")
    args = parser.parse_args()
    try:
        document = json.loads(args.level.read_text(encoding="utf-8"))
        catalog = json.loads(args.catalog.read_text(encoding="utf-8")) if args.catalog else []
        document, catalog = merge_pack(document, args.pack, args.asset_root, catalog)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        stage = args.output.with_suffix(args.output.suffix + ".tmp")
        stage.write_text(json.dumps({"document": document, "prefabs": catalog}, indent=2) + "\n", encoding="utf-8")
        stage.replace(args.output)
    except (ValueError, OSError, TypeError, KeyError) as error:
        parser.exit(1, f"Asset pack import failed: {error}\n")


if __name__ == "__main__":
    main()
