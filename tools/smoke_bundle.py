"""Verify menu, repeated level switching and direct test starts in isolated Ares."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

from build import ROOT, build_rom
from compile_bundle import read_manifest
from smoke_ares import exercise
from capture_ares import decode_captures


def fields(line):
    return dict(re.findall(r"([a-z_]+)=([^ ]+)", line))


def starts(log):
    return [fields(line) for line in log.splitlines() if line.startswith("DL64 level_start ")]


def check_start(row, data, preset):
    spawn = next(e for e in data["entities"] if e["kind"] == "spawn")["transform"]
    state = next((s for s in data.get("test_starts", []) if s["id"] == preset), None)
    position = state["position"] if state else spawn["position"]
    assert all(abs(a - b) < .011 for a, b in zip(map(float, row["player"].split(",")), position))
    yaw = math.radians(state["yaw"] if state else spawn["rotation"][1])
    error = (float(row["yaw"]) - yaw + math.pi) % (2 * math.pi) - math.pi
    assert abs(error) < .0011
    assert abs(float(row["pitch"]) - math.radians(state["pitch"] if state else 0)) < .0011
    assert row["door"] == str(int(state.get("door_open", False) if state else False))
    assert row["crouched"] == str(int(state.get("crouched", False) if state else False))
    assert row["complete"] == row["caught"] == "0" and float(row["elapsed"]) == 0
    assert int(row["enemies"]) == sum(e["kind"] == "guard" for e in data["entities"])


def main():
    sdk = Path(os.environ.get("N64_INST", "C:/n64-toolchain"))
    exe = Path(os.environ.get("ARES_EXE", str(Path(os.environ["LOCALAPPDATA"]) / "ares/ares.exe")))
    settings = ROOT / ".dev/ares/settings-8mb.bml"
    if not settings.is_file():
        settings = exe.parent / "settings.bml"
    manifest = ROOT / "content/level_bundle.json"
    _, entries = read_manifest(manifest)
    data = {entry["id"]: json.loads(entry["source"].read_text()) for entry in entries}
    expected = {(ident, preset): [] for ident, level in data.items()
                for preset in ["default"] + [s["id"] for s in level.get("test_starts", [])]}
    report = {"passed": False, "platform": "Ares", "runs": [], "checks": []}
    destination = ROOT / "build/bundle-smoke.json"

    def run(rom, name, marker, memory=8):
        result = exercise(exe, settings, rom, name, marker, 180,
                          hashlib.sha256(rom.read_bytes()).hexdigest(), memory)
        report["runs"].append(result)
        return result, (ROOT / result["log"]).read_text()

    try:
        # Ordinary menu must draw frames without initializing a gameplay level.
        menu = build_rom(sdk, bundle=manifest)
        _, idle = run(menu, "bundle-idle", "DL64 profile_end window=1")
        assert "mode=menu" in idle and not starts(idle)
        run(menu, "bundle-4mb", "boot_failed reason=expansion_pak", memory=4)
        report["checks"].append("Ordinary menu renders without starting a level; 4 MiB boot gate works")

        test = build_rom(sdk, bundle=manifest, menu_test=True)
        result, log = run(test, "bundle-switching", "DL64 menu_test_complete")
        for row in starts(log):
            key = row["id"], row["preset"]
            assert key in expected, f"Unexpected start {key}"
            check_start(row, data[key[0]], key[1])
            expected[key].append(int(row["heap"].split("/")[0]))
        for key, heaps in expected.items():
            assert len(heaps) == 6, f"Expected three launch/restart pairs for {key}, got {len(heaps)}"
            assert max(heaps[2:]) == min(heaps[2:]), f"Heap did not stabilize for {key}: {heaps}"
        opens = [fields(line) for line in log.splitlines() if line.startswith("DL64 menu_open ")]
        resumes = [fields(line) for line in log.splitlines() if line.startswith("DL64 menu_resume ")]
        assert len(resumes) == len(expected) * 3, "Every variant must exercise resume"
        for resume in resumes:
            assert any(all(opened.get(key) == resume.get(key) for key in
                           ("id", "preset", "elapsed", "player", "door", "complete", "caught"))
                       for opened in opens), "Resume changed paused state"
        report["heap_samples"] = {f"{key[0]}/{key[1]}": values for key, values in expected.items()}
        report["checks"] += ["Every bundled default/test start launches and restarts three times",
                             "Position, view, stance, door and clean mission state match authored starts",
                             "Repeated textured/untextured switches reach a stable per-start heap",
                             "Menu resume preserves paused simulation time"]
        frame = decode_captures(log, expected_count=1, expected_dimensions=(320, 240))[1]
        from PIL import Image
        image_path = ROOT / Path(result["log"]).parent / "level-select.png"
        Image.frombytes("RGB", frame[:2], frame[2]).save(image_path)
        report["menu_image"] = str(image_path)

        direct = build_rom(sdk, bundle=manifest, start_level="enemy-workshop", start_preset="store-door-open")
        _, log = run(direct, "bundle-direct", "DL64 frame n=")
        row = starts(log)[0]
        assert row["id"] == "enemy-workshop" and row["preset"] == "store-door-open" and "mode=direct" in log
        check_start(row, data["enemy-workshop"], "store-door-open")
        direct = build_rom(sdk, level=ROOT / "content/enemy_patrols.json", start_preset="scout-observation")
        _, log = run(direct, "single-direct-preset", "DL64 frame n=")
        row = starts(log)[0]
        assert row["id"] == "enemy_patrols" and row["preset"] == "scout-observation" and "mode=direct" in log
        check_start(row, data["enemy-workshop"], "scout-observation")
        report["checks"].append("Both bundle and standalone ROMs bypass the menu into a named start")
        report["passed"] = True
    finally:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
