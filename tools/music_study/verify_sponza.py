"""Verify the current Sponza music ROM through a full loop under gameplay rendering."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from smoke_ares import exercise
from profile_report import make_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reuse-latest", action="store_true", help="Re-analyze a completed run of this exact ROM")
    args = parser.parse_args()
    name = "DarkLantern64-sponza_courtyard-t3d"
    build = json.loads((ROOT / "build" / name / "build.json").read_text())
    rom = ROOT / "build" / (name + ".z64")
    ares = Path(os.environ.get("ARES_EXE", str(Path(os.environ["LOCALAPPDATA"]) / "ares/ares.exe")))
    if args.reuse_latest:
        matches = []
        for log_path in (ROOT / ".dev/ares/runs").glob("*/sponza-music.log"):
            snapshot = log_path.parent / rom.name
            if (snapshot.is_file() and hashlib.sha256(snapshot.read_bytes()).hexdigest() == build["rom_sha256"]
                    and "DL64 music_loop_ok" in log_path.read_text(errors="replace")):
                matches.append(log_path)
        if not matches:
            raise RuntimeError("No completed run matches the current ROM")
        log_path = max(matches, key=lambda p: p.stat().st_mtime_ns)
        result = {"name": "sponza-music", "passed": True, "reprocessed": True,
                  "log": str(log_path.relative_to(ROOT)), "rom_sha256": build["rom_sha256"],
                  "tested_rom": str((log_path.parent / rom.name).relative_to(ROOT)), "rom_bytes": rom.stat().st_size}
    else:
        result = exercise(ares, ROOT / ".dev/ares/settings-8mb.bml", rom, "sponza-music",
                          "DL64 music_loop_ok", seconds=90, expected_sha256=build["rom_sha256"], memory=8)
    log = (ROOT / result["log"]).read_text(errors="replace")
    music = re.findall(r"DL64 music cue=(\S+) consumed_frames=(\d+) generated_blocks=(\d+) queued=(\d+) underruns=(\d+) peak=(\d+)", log)
    if not music or any(int(row[4]) for row in music) or int(music[-1][5]) <= 0:
        raise RuntimeError("Music did not stay supplied with nonzero PCM through its full loop")
    if "DL64 level_start id=sponza_courtyard" not in log or 'world=3d renderer=rsp-tiny3d' not in log:
        raise RuntimeError("Expected Sponza with Tiny3D gameplay rendering")
    profile = make_report((ROOT / result["log"]).read_bytes(), ROOT / result["log"])
    result.update(music_records=[line for line in log.splitlines() if line.startswith("DL64 music")],
                  last_music=dict(cue=music[-1][0], consumed_frames=int(music[-1][1]),
                                  generated_blocks=int(music[-1][2]), queued=int(music[-1][3]),
                                  underruns=int(music[-1][4]), peak=int(music[-1][5])),
                  profiler=profile, emulator_sha256=hashlib.sha256(ares.read_bytes()).hexdigest(),
                  limitations="Stationary Sponza start with game simulation, guards and Tiny3D running in Ares. Not original hardware or exhaustive camera/loading stress. Underruns count the software music queue, not every possible AI/device fault.")
    (ROOT / "docs/evidence/sponza-music.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k not in ("profiler", "music_records")}, indent=2))


if __name__ == "__main__":
    main()
