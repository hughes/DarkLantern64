"""Export saved Blender props, open their source, or package the optional add-on."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CONTENT = ROOT / "content"
ADDON = ROOT / "tools/blender/darklantern64_export"


def output_directory(value):
    """Constrain exports to a named asset-pack folder, never canonical levels."""
    output = Path(value).resolve()
    assets = (CONTENT / "assets").resolve()
    if not output.is_relative_to(assets) or output == assets:
        raise ValueError("Export output must be a named folder below content/assets")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for part in output.relative_to(assets).parts:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", part) or part.upper() in reserved:
            raise ValueError("Asset-pack folder names need ASCII letters, digits, '-' or '_', and cannot be Windows device names")
    return output


def source_file(value, *, create=False):
    source = Path(value).resolve()
    if source.suffix.lower() != ".blend":
        raise ValueError("Source must be a .blend file")
    if create:
        art = (ROOT / "art").resolve()
        if not source.is_relative_to(art):
            raise ValueError("Demo generation saves source files below this project's art folder")
    elif not source.is_file():
        raise ValueError(f"Blender source not found: {source}. Run --make-demo to create the example")
    return source


def find_blender(explicit=None):
    """Honor explicit configuration before searching common installations."""
    configured = explicit or os.environ.get("BLENDER_EXE")
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"Blender executable not found: {path}")
        return path
    on_path = shutil.which("blender") or shutil.which("blender.exe")
    if on_path:
        return Path(on_path).resolve()
    candidates = []
    for base in (os.environ.get("ProgramFiles", "C:/Program Files"),
                 os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")):
        candidates.append(Path(base) / "Steam/steamapps/common/Blender/blender.exe")
        foundation = Path(base) / "Blender Foundation"
        if foundation.is_dir():
            candidates.extend(sorted(foundation.glob("Blender */blender.exe"), reverse=True))
    candidates += [Path("/Applications/Blender.app/Contents/MacOS/Blender"),
                   Path("/usr/bin/blender"), Path("/usr/local/bin/blender")]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise ValueError("Blender not found. Pass --blender /path/to/blender or set BLENDER_EXE")


def package_addon():
    """Create an installable ZIP without altering Blender's preferences."""
    if not (ADDON / "__init__.py").is_file():
        raise ValueError(f"Exporter add-on is missing: {ADDON}")
    destination = ROOT / "build/darklantern64_export.zip"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(ADDON.rglob("*")):
            if not source.is_file() or source.suffix not in (".py", ".md", ".json", ".png") or "__pycache__" in source.parts:
                continue
            if not source.resolve().is_relative_to(ADDON.resolve()):
                raise ValueError("Exporter package contains a file outside its directory")
            entry = zipfile.ZipInfo("darklantern64_export/" + source.relative_to(ADDON).as_posix(), (2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, source.read_bytes())
    os.replace(temporary, destination)
    print(f"Add-on package: {destination}")
    return destination


def export_worker(output):
    """Executed by Blender through this fixed file, never a generated expression."""
    import bpy

    sys.path.insert(0, str(ADDON.parent))
    from darklantern64_export import export_pack

    objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH" and obj.get("dl64_asset_id")]
    if not objects:
        raise ValueError("The active scene contains no mesh objects with dl64_asset_id")
    export_pack(bpy.context, CONTENT, output, objects=objects)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "art/loot.blend", help="Saved .blend source (default: art/loot.blend)")
    parser.add_argument("--output", type=Path, default=CONTENT / "assets/loot", help="Named destination below content/assets")
    parser.add_argument("--blender", type=Path, help="Blender executable; otherwise use BLENDER_EXE or discover it")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--open", action="store_true", help="Open the source in interactive Blender")
    actions.add_argument("--make-demo", action="store_true", help="Recreate the example source and export (overwrites the chosen .blend)")
    actions.add_argument("--package-addon", action="store_true", help="Write build/darklantern64_export.zip for manual installation")
    actions.add_argument("--export-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.package_addon:
            package_addon()
            return 0
        output = output_directory(args.output)
        if args.export_worker:
            export_worker(output)
            return 0
        source = source_file(args.source, create=args.make_demo)
        blender = find_blender(args.blender)
        if args.open:
            # This action explicitly opens the artist's visible interactive tool.
            subprocess.Popen([str(blender), str(source)], cwd=ROOT)
            print(f"Opened Blender source: {source}")
            return 0
        command = [str(blender), "--factory-startup", "--background", "--disable-autoexec", "--python-exit-code", "1"]
        if args.make_demo:
            script = ROOT / "tools/blender/create_loot.py"
            if not script.is_file():
                raise ValueError(f"Demo generator is missing: {script}")
            command += ["--python", str(script), "--", "--source", str(source), "--output", str(output)]
        else:
            command += [str(source), "--python", str(Path(__file__).resolve()), "--", "--export-worker", "--output", str(output)]
        print("[run] " + subprocess.list2cmdline(command), flush=True)
        subprocess.run(command, cwd=ROOT, check=True)
        manifest = output / "pack.json"
        pack = json.loads(manifest.read_text(encoding="utf-8"))
        print(json.dumps({"source": str(source), "asset_pack": str(manifest),
                          "assets": len(pack["assets"]), "materials": len(pack["materials"]),
                          "prefabs": [prefab["id"] for prefab in pack["prefabs"]]}, indent=2))
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, KeyError) as error:
        print(f"Blender asset operation failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # Blender retains its own launch arguments before the conventional '--'.
    arguments = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None
    raise SystemExit(main(arguments))
