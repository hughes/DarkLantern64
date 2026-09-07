"""Set up pinned editor dependencies; --tiny3d explicitly acquires the RSP library."""
from __future__ import annotations

import argparse
import json
import os
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tiny3d", action="store_true",
                        help="Also fetch and build the pinned Tiny3D library locally, without SDK installation")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    args = parser.parse_args()
    try:
        setup()
        if args.tiny3d:
            from build_tiny3d import build_library
            build_library(args.sdk, fetch=True)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Editor setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
