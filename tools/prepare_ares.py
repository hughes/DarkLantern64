#!/usr/bin/env python3
"""Prepare isolated Windows Ares settings for the DarkLantern64 demo.

Bindings use Ares RawInput key indices, not Windows virtual-key codes. A local
Ares checkout can verify them. The installed settings file is read only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys


PROJECT = Path(__file__).resolve().parents[1]
GAMEPAD = "Nintendo64/Input/Controller.Port.1/Gamepad"
MAPPINGS = {
    "Up": ("Up", "look up"),
    "Down": ("Down", "look down"),
    "Left": ("Left", "look left"),
    "Right": ("Right", "look right"),
    "L-Up": ("Up", "look up"),
    "L-Down": ("Down", "look down"),
    "L-Left": ("Left", "look left"),
    "L-Right": ("Right", "look right"),
    "C-Left": ("A", "strafe left"),
    "C-Right": ("D", "strafe right"),
    "C-Up": ("W", "forward"),
    "C-Down": ("S", "backward"),
    "L": ("Spacebar", "jump"),
    "Z": ("Z", "crouch"),
    "A": ("E", "use"),
    "B": ("N", "noise"),
    "R": ("R", "restart scenario"),
    "Start": ("Tab", "toggle debug display"),
}

# Ares serializes each stick direction both as an input and as an axis-pair
# half. The pair is loaded last; keep the aliases identical. Y Lo is negative
# in the desktop input layer, then the N64 controller negates it to stick-up.
ANALOG_ALIASES = {"L-Up": "Y-Axis/Lo", "L-Down": "Y-Axis/Hi",
                  "L-Left": "X-Axis/Lo", "L-Right": "X-Axis/Hi"}

# Verified from ares 7b51c8ab719e403a150aa700e0933d9e93a06851:
# ruby/input/keyboard/rawinput.cpp and desktop-ui/input/input.cpp.
# Keeping the verified subset here avoids requiring emulator source to play.
VERIFIED_INDICES = {"W": 57, "S": 53, "Left": 88, "Right": 89, "A": 35,
                    "D": 38, "Up": 86, "Down": 87, "Spacebar": 92, "Z": 60,
                    "E": 39, "N": 48, "R": 52, "Tab": 90}


def read_binding_indices(source: Path) -> tuple[dict[str, int], dict[str, str]]:
    """Check the serialization/device contract and read its actual key indices."""
    evidence_paths = (
        "ruby/input/keyboard/rawinput.cpp",
        "ruby/input/sdl.cpp",
        "nall/nall/hid.hpp",
        "desktop-ui/input/input.cpp",
        "desktop-ui/settings/settings.cpp",
        "desktop-ui/input/input.hpp",
        "desktop-ui/emulator/nintendo-64.cpp",
        "ares/n64/controller/gamepad/gamepad.cpp",
    )
    sources = {name: (source / name).read_text(encoding="utf-8") for name in evidence_paths}
    keyboard = sources[evidence_paths[0]]
    hid = sources[evidence_paths[2]]
    binding = sources[evidence_paths[3]]
    settings = sources[evidence_paths[4]]
    required = (
        (keyboard, "kb.hid->setPathID(0)"),
        (keyboard, "kb.hid->setVendorID(HID::Keyboard::GenericVendorID)"),
        (keyboard, "kb.hid->setProductID(HID::Keyboard::GenericProductID)"),
        (keyboard, "for(auto& key : keys) kb.hid->buttons().append(key.name)"),
        (sources[evidence_paths[1]], "InputKeyboardRawInput keyboard"),
        (binding, 'binding.deviceID = token[0].natural()'),
        (binding, 'binding.groupID = token[1].natural()'),
        (binding, 'binding.inputID = token[2].natural()'),
        (settings, 'nall::split(value, ";")'),
        (sources[evidence_paths[5]], 'BindingLimit = 3'),
        (binding, 'digitalPressed |= value != 0'),
        (binding, 's32 direction = (hi.pressed ? 1 : 0) - (lo.pressed ? 1 : 0)'),
        (sources[evidence_paths[6]], 'device.analog ("X-Axis",  virtualPorts[id].pad.lstick_left, virtualPorts[id].pad.lstick_right)'),
        (sources[evidence_paths[6]], 'device.analog ("Y-Axis",  virtualPorts[id].pad.lstick_up,   virtualPorts[id].pad.lstick_down)'),
        (sources[evidence_paths[7]], 'data.byte(0) = s8(-ay)'),
    )
    if any(fragment not in text for text, fragment in required):
        raise ValueError("Ares input contract changed; review its binding implementation before generating settings")
    keyboard_type = re.search(r"struct Keyboard : Device \{(.*?)\n\};", hid, re.S)
    if not keyboard_type or not re.search(
        r"GenericVendorID\s*=\s*0x0000,\s*GenericProductID\s*=\s*0x0001",
        keyboard_type.group(1),
    ) or not re.search(r"enum GroupID\s*:\s*u32\s*\{\s*Button\s*\}", keyboard_type.group(1)):
        raise ValueError("Ares keyboard device/group identity is no longer the verified 0x1/0")
    # Anchor to executable push_back lines so commented-out Pause/NumLock do not shift IDs.
    names = re.findall(r'^\s*keys\.push_back\(\{[^\n]*?"([^"]+)"', keyboard, re.M)
    if not names or len(names) != len(set(names)):
        raise ValueError("Cannot determine a unique Ares RawInput keyboard index table")
    indices = {name: names.index(name) for name, _ in MAPPINGS.values()}
    hashes = {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in sources.items()}
    return indices, hashes


def node_indices(lines: list[str]) -> dict[tuple[str, ...], int]:
    """Index the simple indentation-based BML settings while preserving its text."""
    stack: list[tuple[int, str]] = []
    found: dict[tuple[str, ...], int] = {}
    for index, line in enumerate(lines):
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("//") or stripped.startswith("#"):
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise ValueError("Tab-indented BML settings are not supported")
        indentation = len(line) - len(stripped)
        name = stripped.split(":", 1)[0].strip()
        while stack and stack[-1][0] >= indentation:
            stack.pop()
        path = tuple(parent for _, parent in stack) + (name,)
        if path in found:
            raise ValueError(f"Ambiguous duplicate BML path: {'/'.join(path)}")
        found[path] = index
        stack.append((indentation, name))
    return found


def set_value(lines: list[str], path: str, value: str | None) -> None:
    parts = tuple(path.split("/"))
    found = node_indices(lines)
    if parts in found:
        index = found[parts]
        indent = lines[index][: len(lines[index]) - len(lines[index].lstrip(" "))]
        lines[index] = indent + parts[-1] + (f": {value}" if value is not None else "")
        return
    if len(parts) == 1:
        lines.append(parts[0] + (f": {value}" if value is not None else ""))
        return
    parent = parts[:-1]
    if parent not in found:
        set_value(lines, "/".join(parent), None)
        found = node_indices(lines)
    index = found[parent]
    parent_indent = len(lines[index]) - len(lines[index].lstrip(" "))
    insertion = index + 1
    while insertion < len(lines):
        line = lines[insertion]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= parent_indent:
            break
        insertion += 1
    lines.insert(insertion, " " * (parent_indent + 2) + parts[-1] +
                 (f": {value}" if value is not None else ""))


def keyboard_assignment(lines: list[str], paths: list[str], index: int) -> str:
    """Replace old keyboard keys while retaining physical-controller slots."""
    found = node_indices(lines)
    retained = []
    for path in paths:
        line_index = found.get(tuple(path.split("/")))
        if line_index is None:
            continue
        value = lines[line_index].partition(":")[2].strip()
        for binding in value.split(";"):
            if not binding or binding.split("/", 1)[0].lower() == "0x1":
                continue
            if binding not in retained:
                retained.append(binding)
    if len(retained) > 2:
        raise ValueError(f"No free Ares binding slot for keyboard at {paths[0]}; existing controller bindings were preserved")
    bindings = retained + [f"0x1/0/{index}"]
    return ";".join(bindings + [""] * (3 - len(bindings)))


def prepare(memory: int, source_settings: Path, ares_source: Path | None = None) -> dict:
    if memory not in (4, 8):
        raise ValueError("Ares memory must be 4 or 8 MiB")
    source_settings = source_settings.expanduser().resolve()
    output_dir = (PROJECT / ".dev" / "ares").resolve()
    if not output_dir.is_relative_to(PROJECT):
        raise ValueError("Project .dev/ares resolves outside the project directory")
    output = output_dir / f"settings-{memory}mb.bml"
    if source_settings == output:
        raise ValueError("Source and generated settings must be different files")
    raw = source_settings.read_bytes()
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    if ares_source is not None:
        indices, evidence = read_binding_indices(ares_source.resolve())
    else:
        indices = VERIFIED_INDICES
        evidence = {"verified_ares_commit": "7b51c8ab719e403a150aa700e0933d9e93a06851"}
    found = node_indices(lines)
    if tuple(GAMEPAD.split("/")) not in found:
        raise ValueError("Source settings do not contain Nintendo 64 controller port 1 Gamepad settings")

    replacements = {
        "Nintendo64/ExpansionPak": "true" if memory == 8 else "false",
        "General/HomebrewMode": "true",  # Installed older Ares release.
        "Developer/HomebrewMode": "true",  # Current sibling source checkout.
        "Input/Driver": "SDL",  # Uses the verified RawInput keyboard on Windows.
        "Input/Defocus": "Allow",
        "Input/DigitalToAnalog": "Immediate",
        "Paths/Screenshots": (output_dir / "screenshots").as_posix() + "/",
        "Paths/Debugging": (output_dir / "logs").as_posix() + "/",
        "Paths/Saves": (output_dir / "saves").as_posix() + "/",
    }
    controls = []
    for button, (key, action) in MAPPINGS.items():
        paths = [f"{GAMEPAD}/{button}"]
        if button in ANALOG_ALIASES:
            paths.append(f"{GAMEPAD}/{ANALOG_ALIASES[button]}")
        assignment = keyboard_assignment(lines, paths, indices[key])
        for path in paths:
            replacements[path] = assignment
        controls.append({"key": key, "n64_button": button, "action": action,
                         "binding": assignment, "settings_paths": paths})
    for path, value in replacements.items():
        set_value(lines, path, value)
    result = (newline.join(lines) + newline).encode("utf-8")

    output_dir.mkdir(parents=True, exist_ok=True)
    for directory in ("screenshots", "logs", "saves"):
        (output_dir / directory).mkdir(exist_ok=True)
    temporary = output.with_suffix(".bml.tmp")
    temporary.write_bytes(result)
    temporary.replace(output)
    if source_settings.read_bytes() != raw:
        raise RuntimeError("Source settings changed concurrently during preparation; regenerate the copy")
    return {
        "settings_file": str(output),
        "memory_mib": memory,
        "source_settings": str(source_settings),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "settings_sha256": hashlib.sha256(result).hexdigest(),
        "changed_keys": list(replacements),
        "keyboard_controls": controls,
        "binding_source_sha256": evidence,
    }


def prepare_settings(memory: int = 8) -> Path:
    source = Path(os.environ["LOCALAPPDATA"]) / "ares" / "settings.bml"
    result = prepare(memory, source)
    return Path(result["settings_file"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory", required=True, type=int, choices=(4, 8), help="Emulated RDRAM in MiB")
    parser.add_argument("--source-settings", type=Path, help="Installed settings.bml (default: LOCALAPPDATA/ares)")
    parser.add_argument("--ares-source", type=Path, help="Optional Ares source checkout to reverify binding indices")
    parser.add_argument("--json", action="store_true", help="Print the output path, hashes, and mappings as JSON")
    args = parser.parse_args()
    source = args.source_settings
    if source is None:
        local_appdata = os.environ.get("LOCALAPPDATA")
        if not local_appdata:
            parser.error("LOCALAPPDATA is unset; pass --source-settings explicitly")
        source = Path(local_appdata) / "ares" / "settings.bml"
    try:
        result = prepare(args.memory, source, args.ares_source)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"prepare_ares: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if args.json else result["settings_file"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
