"""The conservative source recipe captured by both resident and stream cooks."""
import hashlib
from pathlib import Path


def recipe_sources(root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    paths = sorted((root / "tools").glob("*.py"))
    paths += sorted(p for p in (root / "src").rglob("*") if p.suffix in (".c", ".h", ".inc", ".def"))
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
