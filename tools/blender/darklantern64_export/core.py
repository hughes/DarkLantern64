"""Blender-independent validation for the DarkLantern64 asset-pack exporter."""
from __future__ import annotations

import json
import math
from pathlib import Path
import re


class ExportError(ValueError):
    """An authoring feature cannot be represented by the game asset format."""


def require(condition, message):
    if not condition:
        raise ExportError(message)


def identifier(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value),
            f"{label}: use 1-64 ASCII letters, digits, underscores or hyphens, starting with a letter")
    return value


def bounded(values, count, label, minimum=0, maximum=1):
    values = list(values)
    require(len(values) == count and all(isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(v) and minimum <= v <= maximum for v in values),
            f"{label}: expected {count} finite values between {minimum} and {maximum}")
    return values


def texture_size(width, height):
    for value in (width, height):
        require(type(value) is int and 1 <= value <= 64 and value & (value - 1) == 0,
                "Texture dimensions must each be a power of two from 1 to 64")
    require(width * height * 2 <= 4096, "RGBA16 texture exceeds the N64's 4 KiB TMEM limit")


def relative_uri(path, content_root):
    root, path = Path(content_root).resolve(), Path(path).resolve()
    require(path.is_relative_to(root) and path != root, "Export destination must be inside the content root")
    return path.relative_to(root).as_posix()


def check_unique(records, label):
    seen = set()
    for record in records:
        require(isinstance(record, dict), f"{label}: expected objects")
        ident = identifier(record.get("id"), label + " ID")
        require(ident.casefold() not in seen, f"Duplicate {label} ID: {ident}")
        seen.add(ident.casefold())


def validate_publication(manifest, content_root, output_dir):
    """Permit re-export of owned files; reject collisions with other source assets.

    Existing level copies of an old pack's material are allowed. They remain
    unchanged until the creator performs an intentional material migration;
    importing a conflicting definition does not silently overwrite it.
    """
    root, output = Path(content_root).resolve(), Path(output_dir).resolve()
    relative_uri(output / "pack.json", root)
    for category in ("assets", "materials", "prefabs"):
        check_unique(manifest[category], category)
    pack_path = output / "pack.json"
    previous = None
    if pack_path.exists():
        require(pack_path.is_file(), f"Destination is not a file: {pack_path}")
        try:
            previous = json.loads(pack_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ExportError(f"Cannot read existing pack {pack_path}: {error}") from error
        require(isinstance(previous, dict) and previous.get("version") == 1 and all(isinstance(previous.get(k), list)
                for k in ("assets", "materials", "prefabs")), "Existing pack.json is not a version-1 asset pack")
        for category in ("assets", "materials", "prefabs"):
            check_unique(previous[category], "existing " + category)
        for item in previous["assets"]:
            require(isinstance(item.get("uri"), str), "Existing pack asset is missing its URI")
        for item in previous["materials"]:
            require("texture" not in item or (isinstance(item["texture"], dict) and
                    isinstance(item["texture"].get("uri"), str)), "Existing pack material has an invalid texture URI")

    previous_assets = {item["id"].casefold(): item for item in (previous or {}).get("assets", [])}
    previous_materials = {item["id"].casefold(): item for item in (previous or {}).get("materials", [])}
    old_uris = {item["uri"] for item in previous_assets.values()}
    old_uris.update(item["texture"]["uri"] for item in previous_materials.values() if "texture" in item)
    new_uris = {item["uri"] for item in manifest["assets"]}
    new_uris.update(item["texture"]["uri"] for item in manifest["materials"] if "texture" in item)
    for uri in new_uris:
        destination = (root / uri).resolve()
        require(destination.is_relative_to(output), "All exported asset files must stay inside the pack directory")
        require(not destination.exists() or uri in old_uris,
                f"Refusing to overwrite an existing file not owned by this pack: {uri}")
    for asset in manifest["assets"]:
        old = previous_assets.get(asset["id"].casefold())
        require(old is None or old == asset, f"Existing asset ID changed its spelling or path: {asset['id']}")

    new_assets = {item["id"].casefold(): item for item in manifest["assets"]}
    new_materials = {item["id"].casefold(): item for item in manifest["materials"]}
    for document_path in root.rglob("*.json"):
        if document_path.resolve() == pack_path:
            continue
        # Only documents declaring assets/materials participate in the ID namespace.
        try:
            document = json.loads(document_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(document, dict):
            continue
        for item in document.get("assets", []) if isinstance(document.get("assets"), list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            incoming = new_assets.get(item["id"].casefold())
            require(incoming is None or item == incoming,
                    f"Asset ID {item['id']} already belongs to different content in {document_path.name}")
        for item in document.get("materials", []) if isinstance(document.get("materials"), list) else []:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                continue
            incoming = new_materials.get(item["id"].casefold())
            old = previous_materials.get(item["id"].casefold())
            require(incoming is None or item == incoming or (old is not None and item == old),
                    f"Material ID {item['id']} already belongs to different content in {document_path.name}")
    return previous
