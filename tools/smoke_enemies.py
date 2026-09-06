"""Verify independent moving patrols and a stationary sentry in isolated Ares.

Runs the ordinary Enemy Patrol Workshop ROM with physical input disabled.
This is a short behavior smoke test, not a maximum-enemy performance study.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re

from build import ROOT, build_rom
from smoke_ares import exercise


def main():
    source = ROOT / "content/enemy_patrols.json"
    rom = build_rom(Path(os.environ.get("N64_INST", "C:/n64-toolchain")), level=source)
    exe = Path(os.environ.get("ARES_EXE", str(Path(os.environ["LOCALAPPDATA"]) / "ares/ares.exe")))
    settings = ROOT / ".dev/ares/settings-8mb.bml"
    if not settings.exists():
        settings = exe.parent / "settings.bml"
    result = exercise(exe, settings, rom, "enemy-patrols", "DL64 profile_end window=6", 90,
                      hashlib.sha256(rom.read_bytes()).hexdigest())
    log = (ROOT / result["log"]).read_text()
    rows = re.findall(r"DL64 enemy id=([A-Za-z0-9_-]+) index=(\d+) position=([-\d.,]+) "
                      r"state=(\w+) patrol_index=(\d+) sees=(\d+) hears=(\d+) alert=([\d.]+)", log)
    enemies = {}
    for ident, index, position, state, patrol, sees, hears, alert in rows:
        enemies.setdefault(ident, []).append({"index": int(index),
            "position": [float(v) for v in position.split(",")], "state": state,
            "patrol_index": int(patrol), "sees": int(sees), "hears": int(hears), "awareness": float(alert)})
    if set(enemies) != {"store-guard", "workroom-scout", "store-sentry"}:
        raise AssertionError(f"Expected the three example actors, got {set(enemies)}; inspect {result['log']}")
    for ident, samples in enemies.items():
        if len(samples) < 4 or len({s["index"] for s in samples}) != 1:
            raise AssertionError(f"Missing or inconsistent telemetry for {ident}")
        distance = max(math.dist(samples[0]["position"], s["position"]) for s in samples)
        if ident == "store-sentry":
            if distance > .02 or any(s["state"] != "PATROL" for s in samples):
                raise AssertionError("Undisturbed sentry left its authored post")
        elif distance < .5 or len({s["patrol_index"] for s in samples}) < 2:
            raise AssertionError(f"{ident} did not move independently through its authored route")
    if "caught=1" in log or "complete=1" in log or "DL64 restart " in log:
        raise AssertionError("Stationary smoke test unexpectedly ended or reset the mission")
    result["enemy_samples"] = enemies
    result["source_file_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    result["checks"] = ["Both patrol enemies move and advance their own route index",
                        "Undisturbed sentry holds its post", "Three distinct stable enemy indices",
                        "Ordinary ROM runs without capture freezing or gameplay input"]
    result["limitations"] = "Short example-level behavior check; no crowded chase, navigation or hardware performance certification"
    destination = ROOT / "build/scenes/enemy_patrols/runtime-smoke.json"
    destination.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "enemy_samples"}, indent=2))


if __name__ == "__main__":
    main()
