"""Small gameplay fixtures independent of the demo's growing art collection."""
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def gameplay_baseline():
    """Keep room/actor topology but opt out of optional presentation features.

    Tests that exercise an optional feature add it explicitly. This avoids an
    unrelated prop, texture or debug start silently changing their workload.
    Full source/dependency tests should load the authored document directly.
    """
    level = json.loads((ROOT / "content/first_room.json").read_text())
    imported = {asset["id"] for asset in level["assets"] if asset["uri"].startswith("assets/")}
    level["entities"] = [entity for entity in level["entities"] if entity.get("model") not in imported]
    models = {entity["model"] for entity in level["entities"] if "model" in entity}
    materials = {entity["material"] for entity in level["entities"] if "material" in entity}
    level["assets"] = [asset for asset in level["assets"] if asset["id"] in models]
    level["materials"] = [{"id": material["id"], "color": material["color"]}
                          for material in level["materials"] if material["id"] in materials]
    for key in ("views", "test_starts", "environment"):
        level.pop(key, None)
    return level


def copy_level_dependencies(level, destination, source_root=ROOT / "content"):
    """Copy exactly the declared OBJ and texture sources, including pack paths."""
    source_root, destination = Path(source_root).resolve(), Path(destination).resolve()
    uris = {asset["uri"] for asset in level["assets"]}
    uris.update(material["texture"]["uri"] for material in level["materials"] if "texture" in material)
    for uri in uris:
        source, target = (source_root / uri).resolve(), (destination / uri).resolve()
        if Path(uri).is_absolute() or not source.is_relative_to(source_root) or not target.is_relative_to(destination):
            raise ValueError("Fixture dependency escapes its content root")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
