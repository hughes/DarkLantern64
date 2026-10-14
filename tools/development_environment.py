"""Read-only development tool discovery; this command never installs or builds.

Explicit settings are authoritative even when their paths are missing. Version
probes are bounded commands for Python, GCC and bash only; GUI applications and
download-capable wrappers are inspected as files, without launching them.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HOST_GCC_FALLBACKS = ("C:/ProgramData/mingw64/mingw64/bin/gcc.exe", "C:/msys64/ucrt64/bin/gcc.exe")
SDK_TOOLS = ("mips64-elf-gcc", "mips64-elf-g++", "n64sym", "mips64-elf-strip",
             "n64elfcompress", "n64tool", "mips64-elf-size", "mkdfs")
SDK_LIBRARIES = ("libdragon.a", "libdragonsys.a", "n64.ld")


def _selection(root, configured, environ, key, env_key=None, commands=(), defaults=(), directory=False):
    explicit = key in configured or env_key is not None and env_key in environ
    if key in configured:
        raw, source = configured[key], "configured"
    elif env_key is not None and env_key in environ:
        raw, source = environ[env_key], "environment:" + env_key
    else:
        raw, source = None, "not_found"
        for command in commands:
            found = shutil.which(command, path=environ.get("PATH", ""))
            if found:
                raw, source = found, "PATH:" + command
                break
        if raw is None:
            for default in defaults:
                candidate = Path(default)
                if not candidate.is_absolute():
                    candidate = root / candidate
                exists = candidate.is_dir() if directory else candidate.is_file()
                if exists:
                    raw, source = str(candidate), "default"
                    break
    path = None
    if raw is not None and str(raw).strip():
        # A configured command name may resolve on PATH. A missing configured
        # path must never be replaced with an unrelated installed executable.
        raw = str(raw)
        found = None if directory else shutil.which(raw, path=environ.get("PATH", ""))
        candidate = Path(found or raw)
        path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    available = path is not None and (path.is_dir() if directory else path.is_file())
    return dict(path=str(path) if path is not None else None, source=source, configured=explicit,
                requested=str(raw) if raw is not None else None, available=available,
                status="available" if available else "missing", version=None,
                probe=dict(status="not_requested"), diagnostics=[])


def resolve_host_compiler(configured=None, *, root=None, environ=None):
    """Return the shared GCC selection record, preserving a missing override."""
    root = Path(root or ROOT).resolve()
    settings = {} if configured is None else {"host_compiler": configured}
    return _selection(root, settings, os.environ if environ is None else environ,
                      "host_compiler", "DL64_HOST_CC", ("gcc",), HOST_GCC_FALLBACKS)


def require_host_compiler():
    record = resolve_host_compiler()
    if not record["available"]:
        requested = record["requested"] or "gcc on PATH or the supported MinGW/MSYS2 locations"
        raise RuntimeError("Host GCC unavailable: " + requested + ". Set DL64_HOST_CC to an existing GCC executable.")
    return Path(record["path"])


def _probe(record, enabled, allowed):
    if not record["available"]:
        record["probe"] = dict(status="unavailable")
    elif not enabled:
        record["probe"] = dict(status="disabled")
    elif not allowed:
        record["probe"] = dict(status="not_run", reason="File discovery only; launching this tool may open a GUI or acquire dependencies.")
    else:
        env = dict(os.environ, PATH=str(Path(record["path"]).parent) + os.pathsep + os.environ.get("PATH", ""),
                   PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
        try:
            result = subprocess.run([record["path"], "--version"], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=4, env=env, check=False)
            lines = (result.stdout or result.stderr).strip().splitlines()
            if result.returncode != 0 or not lines:
                raise RuntimeError("Version probe exited " + str(result.returncode) + ": " + result.stderr[:300])
            record["version"] = lines[0][:500]
            record["probe"] = dict(status="passed", exit_code=result.returncode)
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
            record["status"] = "probe_failed"
            record["probe"] = dict(status="failed", error=str(error))


def inspect_environment(root, configured=None, probe=True):
    """Resolve tools and prerequisites without changing the project or host.

    ``configured`` accepts python, host_compiler, n64_sdk, msys2_bash, bazel,
    ares, editor, runner, git, host_cxx, vulkan_sdk and lightengine keys. Relative
    configured paths resolve from root. Availability proves file presence;
    readiness is a prerequisite check, never successful build certification.
    """
    root, settings, env = Path(root).resolve(), dict(configured or {}), os.environ
    tools = {}
    tools["python"] = _selection(root, settings, env, "python", defaults=(sys.executable,))
    tools["python"]["minimum_version"] = "3.11"
    if tools["python"]["path"] == str(Path(sys.executable).resolve()):
        tools["python"].update(version="Python " + ".".join(map(str, sys.version_info[:3])), version_source="running_interpreter")
    tools["host_compiler"] = _selection(root, settings, env, "host_compiler", "DL64_HOST_CC", ("gcc",), HOST_GCC_FALLBACKS)
    tools["n64_sdk"] = _selection(root, settings, env, "n64_sdk", "N64_INST", defaults=("C:/n64-toolchain",), directory=True)
    sdk = tools["n64_sdk"]
    # SDK names match build.py's Windows SDK contract, including DFS tooling.
    required = ["bin/" + name + ".exe" for name in SDK_TOOLS]
    required += ["mips64-elf/lib/" + name for name in SDK_LIBRARIES]
    required += ["mips64-elf/include/libdragon.h"]
    sdk["required_files"] = required
    sdk["missing_files"] = [name for name in required if not sdk["path"] or not (Path(sdk["path"]) / name).is_file()]
    sdk["complete"] = sdk["available"] and not sdk["missing_files"]
    if sdk["available"] and not sdk["complete"]:
        sdk["status"] = "incomplete"
    gcc = Path(sdk["path"]) / "bin/mips64-elf-gcc.exe" if sdk["path"] else None
    tools["n64_compiler"] = _selection(root, {"n64_compiler": gcc}, env, "n64_compiler")
    tools["n64_compiler"]["source"] = "n64_sdk"
    tools["msys2_bash"] = _selection(root, settings, env, "msys2_bash", "MSYS2_BASH", defaults=("C:/msys64/usr/bin/bash.exe",))
    tools["bazel"] = _selection(root, settings, env, "bazel", "BAZEL", ("bazelisk", "bazel"))
    ares_defaults = (str(Path(env["LOCALAPPDATA"]) / "ares/ares.exe"),) if env.get("LOCALAPPDATA") else ()
    tools["ares"] = _selection(root, settings, env, "ares", "ARES_EXE", defaults=ares_defaults)
    tools["git"] = _selection(root, settings, env, "git", commands=("git",))
    # Bazel can discover MSVC outside a developer shell. Inspect actual known
    # installation directories too, without invoking an installer or vcvars.
    msvc = []
    if os.name == "nt":
        for base in (env.get("ProgramFiles", "C:/Program Files"), env.get("ProgramFiles(x86)", "C:/Program Files (x86)")):
            msvc.extend(sorted((Path(base) / "Microsoft Visual Studio").glob("*/*/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe"), reverse=True))
    tools["host_cxx"] = _selection(root, settings, env, "host_cxx", "CXX", ("cl",) if os.name == "nt" else ("g++", "clang++"), defaults=msvc)
    tools["vulkan_sdk"] = _selection(root, settings, env, "vulkan_sdk", "VULKAN_SDK", directory=True)
    tools["editor"] = _selection(root, settings, env, "editor", defaults=("editor/bazel-bin/darklantern64_project_editor.exe",))
    tools["runner"] = _selection(root, settings, env, "runner", defaults=("editor/bazel-bin/play_runner.exe",))
    dependency_file = root / "dependencies.json"
    dependency_error = None
    try:
        dependency_document = json.loads(dependency_file.read_bytes())
        dependency = dependency_document["lightengine"]
        dependency_path = (root / dependency["path"]).resolve()
        if not dependency_path.is_relative_to(root):
            raise ValueError("LightEngine dependency path escapes project root")
    except (OSError, ValueError, KeyError, TypeError) as error:
        dependency_document, dependency, dependency_path, dependency_error = {}, {}, root / "external/LightEngine", str(error)
    tools["lightengine"] = _selection(root, settings, env, "lightengine", defaults=(str(dependency_path),), directory=True)
    tools["lightengine"]["expected_commit"] = dependency.get("commit")
    tools["lightengine"]["identity_checked"] = False
    if dependency_error:
        tools["lightengine"]["metadata_error"] = dependency_error
        tools["lightengine"]["status"] = "invalid_metadata"
    tiny3d = dependency_document.get("tiny3d", {})
    tiny3d_error = None
    if not isinstance(tiny3d, dict):
        tiny3d, tiny3d_error = {}, "Tiny3D dependency metadata must be an object"
    tiny3d_path = tiny3d.get("path", "external/tiny3d")
    if not isinstance(tiny3d_path, str):
        tiny3d_path, tiny3d_error = "external/tiny3d", "Tiny3D dependency path must be a string"
    if not (root / tiny3d_path).resolve().is_relative_to(root):
        tiny3d_path, tiny3d_error = "external/tiny3d", "Tiny3D dependency path escapes project root"
    tools["tiny3d"] = _selection(root, settings, env, "tiny3d", defaults=(tiny3d_path,), directory=True)
    tools["tiny3d"]["expected_commit"] = tiny3d.get("commit")
    tools["tiny3d"]["identity_checked"] = False
    if tiny3d_error:
        tools["tiny3d"].update(status="invalid_metadata", metadata_error=tiny3d_error)
    tools["tiny3d_library"] = _selection(root, settings, env, "tiny3d_library", defaults=("build/tiny3d-source/build_sdk_main/libt3d.a",))
    tools["pillow"] = dict(path=None, source="python_package", configured=False, available=False,
                            status="missing", version=None, probe=dict(status="not_run"), diagnostics=[])
    if tools["python"]["path"] != str(Path(sys.executable).resolve()):
        tools["pillow"].update(status="unverified", inspection_interpreter=sys.executable,
                               note="Package discovery belongs to the running interpreter; Pillow in a different configured Python is unknown.")
    elif importlib.util.find_spec("PIL") is not None:
        tools["pillow"].update(available=True, status="available", path=importlib.util.find_spec("PIL").origin)
        try:
            tools["pillow"]["version"] = importlib.metadata.version("Pillow")
        except importlib.metadata.PackageNotFoundError:
            pass
    actions = {
        "python": "Install Python 3.11+ or configure python to its executable.",
        "host_compiler": "Install MinGW/MSYS2 GCC, add gcc to PATH, or set DL64_HOST_CC to its executable.",
        "n64_sdk": "Install the project libdragon SDK and set N64_INST to its root; inspect missing_files.",
        "n64_compiler": "Restore bin/mips64-elf-gcc.exe in the configured N64_INST SDK.",
        "msys2_bash": "Install MSYS2 bash or set MSYS2_BASH to its executable (needed for Tiny3D preparation).",
        "bazel": "Install Bazelisk/Bazel and add it to PATH, or configure bazel to its executable.",
        "ares": "Install Ares and set ARES_EXE; open it once to create its settings before emulator verification.",
        "git": "Install Git and add git to PATH to fetch/check the pinned editor dependency.",
        "host_cxx": "Use a Visual Studio C++ developer environment on Windows (cl on PATH), or a C++23 compiler on Linux.",
        "vulkan_sdk": "Install Vulkan SDK and set VULKAN_SDK to its root for editor builds.",
        "editor": "Prepare editor prerequisites, then run python tools/build.py --editor.",
        "runner": "Prepare editor prerequisites, then build //:play_runner with Bazel in editor/.",
        "lightengine": "Run python tools/setup_editor.py to explicitly fetch the pinned LightEngine checkout.",
        "tiny3d": "Run python tools/build_tiny3d.py --fetch to explicitly acquire and prepare the pinned renderer dependency.",
        "tiny3d_library": "Run python tools/build_tiny3d.py after SDK, MSYS2 and the pinned Tiny3D checkout are ready.",
        "pillow": "Install Pillow in the selected Python environment: python -m pip install Pillow.",
    }
    diagnostics = []
    for key, record in tools.items():
        if key not in ("pillow", "n64_sdk", "vulkan_sdk", "lightengine", "tiny3d"):
            _probe(record, probe, key in ("python", "host_compiler", "n64_compiler", "msys2_bash"))
        if key == "python" and record["version"]:
            version = re.search(r"Python\s+(\d+)\.(\d+)", record["version"])
            if version and tuple(map(int, version.groups())) < (3, 11):
                record["status"] = "unsupported"
        if record["status"] != "available":
            message = key + " is " + record["status"]
            if record.get("configured"):
                message += "; explicit setting preserved: " + str(record.get("requested"))
            row = dict(tool=key, code=key + "_" + record["status"], message=message, action=actions[key])
            record["diagnostics"].append(row)
            diagnostics.append(row)
    usable = lambda key: tools[key]["status"] == "available"
    ready = dict(portable_python=usable("python") and usable("pillow"), portable_c=usable("host_compiler"),
                 editor_build=all(usable(key) for key in ("python", "bazel", "git", "host_cxx", "vulkan_sdk", "lightengine")),
                 native_play=usable("editor") and usable("runner"),
                 rom_build=sdk["complete"] and all(usable(key) for key in ("host_compiler", "n64_compiler")),
                 rom_build_t3d=sdk["complete"] and all(usable(key) for key in ("host_compiler", "n64_compiler", "tiny3d", "msys2_bash")),
                 emulator=usable("ares"))
    return dict(schema_version=1, root=str(root), tools=tools, readiness=ready, diagnostics=diagnostics,
                probe_enabled=bool(probe), scope="Read-only prerequisite discovery; file presence and version probes do not certify builds or runtime behavior.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--configured", type=Path, help="JSON object of explicit tool locations")
    parser.add_argument("--no-probe", action="store_true")
    args = parser.parse_args(argv)
    configured = json.loads(args.configured.read_bytes()) if args.configured else None
    print(json.dumps(inspect_environment(args.root, configured, not args.no_probe), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
