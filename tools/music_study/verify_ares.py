"""Exercise the isolated music ROM with project-local Ares settings."""
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("ARES_EXE", str(Path(os.environ["LOCALAPPDATA"]) / "ares/ares.exe"))))
    parser.add_argument("--settings", type=Path, default=ROOT / ".dev/ares/settings-8mb.bml")
    args = parser.parse_args()
    evidence = json.loads((ROOT / "docs/evidence/music-study.json").read_text())
    rom = ROOT / evidence["rom"]["path"]
    result = exercise(args.ares, args.settings, rom, "music-study", "DL64 music_study_done",
                      seconds=160, expected_sha256=evidence["rom"]["sha256"], memory=8)
    log = (ROOT / result["log"]).read_text(errors="replace")
    records = [line for line in log.splitlines() if "DL64 music_" in line]
    phases = re.findall(r"music_phase kind=(\w+) frames=(\d+) blocks=(\d+) peak=(\d+) heap_peak=(\d+)", log)
    if [r[0] for r in phases] != ["xm64", "wav64"] or any(int(r[3]) <= 0 for r in phases):
        raise RuntimeError("Both backends must produce nonzero PCM and reach completion")
    result.update(rom_source_sha256=evidence["rom"]["player_source_sha256"],
                  emulator_sha256=hashlib.sha256(args.ares.read_bytes()).hexdigest(),
                  records=records, phases=[dict(kind=r[0], frames=int(r[1]), blocks=int(r[2]),
                                               peak=int(r[3]), sampled_heap_peak=int(r[4])) for r in phases],
                  limitations="Standalone emulator execution only. No game load, underrun instrumentation, PCM parity capture, or original-hardware validation.")
    (ROOT / "docs/evidence/music-study-ares.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
