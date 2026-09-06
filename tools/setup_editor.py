"""Check out the pinned LightEngine revision without changing existing work."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def setup():
    dependency = json.loads((ROOT / "dependencies.json").read_text())["lightengine"]
    checkout = ROOT / dependency["path"]
    if not checkout.resolve().is_relative_to(ROOT):
        raise ValueError("Dependency path must stay inside the project")
    if not checkout.exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--no-checkout", dependency["repository"], str(checkout)], check=True)
        subprocess.run(["git", "checkout", "--detach", dependency["commit"]], cwd=checkout, check=True)
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, check=True, text=True, capture_output=True)
    if result.stdout.strip() != dependency["commit"]:
        raise ValueError(f"{checkout} is at {result.stdout.strip()}, expected {dependency['commit']}. "
                         "Existing work was left untouched; review the checkout before updating it.")
    print(f"LightEngine ready: {checkout} ({result.stdout.strip()})")
    return checkout


if __name__ == "__main__":
    try:
        setup()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Editor setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
