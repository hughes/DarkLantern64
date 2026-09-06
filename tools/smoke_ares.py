"""Exercise the actual ROM's 4 MiB error path and 8 MiB mission replay in Ares."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def verify_build(rom, *, autoplay, source_sha256):
    metadata = ROOT / "build" / rom.stem / "build.json"
    report = json.loads(metadata.read_text(encoding="utf-8"))
    if report.get("rom_sha256") != hashlib.sha256(rom.read_bytes()).hexdigest():
        raise ValueError(f"ROM differs from its build report: {rom}. Rebuild before testing.")
    if report.get("autoplay") is not autoplay or report.get("source_sha256") != source_sha256:
        raise ValueError(f"ROM uses stale content or the wrong replay mode: {rom}. Rebuild before testing.")
    return report["rom_sha256"]


def isolate_settings(settings, directory, memory):
    """Never rewrite the active user's profile or listen to their controller."""
    from prepare_ares import node_indices, set_value
    raw = settings.read_bytes()
    lines = raw.decode("utf-8-sig").splitlines()
    for path, index in node_indices(lines).items():
        if path[:2] == ("Nintendo64", "Input") or path[0] == "Hotkeys":
            if ":" in lines[index]:
                lines[index] = lines[index].partition(":")[0] + ": ;;"
    replacements = {
        "Nintendo64/ExpansionPak": "true" if memory == 8 else "false",
        "General/HomebrewMode": "true", "Developer/HomebrewMode": "true",
        "Input/Defocus": "Allow", "General/Fast": "false",
        "DebugServer/Enabled": "false", "Developer/DebugServer/Enabled": "false",
    }
    for kind in ("Saves", "Debugging", "Screenshots"):
        target = directory / kind.lower()
        target.mkdir(exist_ok=True)
        replacements[f"Paths/{kind}"] = target.as_posix() + "/"
    for path, value in replacements.items():
        set_value(lines, path, value)
    output = directory / "settings.bml"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output, hashlib.sha256(raw).hexdigest()


def exercise(exe, settings, rom, name, success, seconds, expected_sha256=None, memory=8):
    run_directory = ROOT / ".dev/ares/runs" / uuid.uuid4().hex
    run_directory.mkdir(parents=True)
    run_settings, settings_source_sha256 = isolate_settings(settings, run_directory, memory)
    snapshot = run_directory / rom.name
    shutil.copyfile(rom, snapshot)
    tested_sha256 = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if expected_sha256 is not None and tested_sha256 != expected_sha256:
        raise ValueError("ROM changed after build verification; rerun the smoke test")
    log = run_directory / (name + ".log")
    error_log = log.with_suffix(".stderr.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log.open("w") as output, error_log.open("w") as errors:
        startup = None
        if os.name == "nt":
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen([str(exe), "--settings-file", str(run_settings), "--no-file-prompt", str(snapshot)],
                                   cwd=ROOT, stdout=output, stderr=errors, startupinfo=startup)
        try:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                text = log.read_text(errors="replace")
                if "RSP CRASH |" in text:
                    raise RuntimeError(f"{name}: ROM reported an RSP graphics failure; inspect {log}")
                if success in text:
                    boot = next((s for s in text.splitlines() if "DL64 boot_ok " in s), None)
                    if name == "smoke-8mb" and (boot is None or "world=3d" not in boot):
                        raise RuntimeError("Replay completion lacks the full-3D boot marker; inspect the ROM and log")
                    profile = None
                    if name == "smoke-8mb":
                        from profile_report import make_report, write_report as write_profile_report
                        profile = make_report(log.read_bytes(), log)
                        if profile["audio_calls"] <= 0:
                            raise RuntimeError("Replay completed without running the audio callback")
                        write_profile_report(run_directory / "profile.json", profile)
                    frames = re.findall(r"ms=([\d.]+) cpu_ms=([\d.]+) heap=(\d+)/(\d+)", text)
                    return {"name": name, "passed": True, "log": str(log.relative_to(ROOT)),
                            "tested_rom": str(snapshot.relative_to(ROOT)),
                            "rom_sha256": tested_sha256,
                            "rom_bytes": snapshot.stat().st_size,
                            "settings": str(run_settings.relative_to(ROOT)),
                            "settings_source_sha256": settings_source_sha256,
                            "profile_report": str((run_directory / "profile.json").relative_to(ROOT)) if profile else None,
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                            "peak_sampled_heap_bytes": max((int(f[2]) for f in frames), default=None),
                            "max_reported_cpu_ms": max((float(f[1]) for f in frames), default=None),
                            "boot": boot,
                            "terminal_event": next((s for s in text.splitlines() if success in s), None)}
                if process.poll() is not None:
                    raise RuntimeError(f"{name}: Ares exited before expected event; inspect {log}")
                time.sleep(0.25)
            raise RuntimeError(f"{name}: timed out waiting for {success!r}; inspect {log}")
        finally:
            # End only this disposable emulator instance; no other Ares process is touched.
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def write_report(report):
    output = ROOT / "build/ares-smoke.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ares", type=Path, default=Path(os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))))
    parser.add_argument("--timeout", type=float, default=100)
    parser.add_argument("--debug-overlay", action="store_true", help="Profile replay with the debug HUD enabled")
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    args = parser.parse_args()
    report = {"platform": "Ares emulator", "status": "running", "passed": False,
              "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": [],
              "debug_overlay": args.debug_overlay,
              "hardware_validation": "Original N64 and M64 pending"}
    try:
        write_report(report)
        if not math.isfinite(args.timeout) or not 0 < args.timeout <= 600:
            raise ValueError("Timeout must be greater than zero and at most 600 seconds")
        from build import build_rom
        from compile_level import compile_level
        if not args.ares.is_file():
            raise RuntimeError("Ares is not installed at the configured path")
        # Use the normal cached build, including C sources, headers, toolchain,
        # build script and content in its fingerprint, before testing any ROM.
        roms = {memory: build_rom(args.sdk.resolve(), autoplay=memory == 8,
                                 debug_overlay=args.debug_overlay and memory == 8) for memory in (4, 8)}
        source = compile_level()
        report["source_sha256"] = source["source_sha256"]
        for memory in (4, 8):
            settings = ROOT / ".dev/ares/settings-8mb.bml"
            if not settings.is_file():
                settings = args.ares.parent / "settings.bml"
            rom = roms[memory]
            expected_sha256 = verify_build(rom, autoplay=memory == 8, source_sha256=source["source_sha256"])
            expected = "boot_failed reason=expansion_pak" if memory == 4 else "DL64 profile_replay_done complete=1 caught=0"
            result = exercise(args.ares, settings, rom, f"smoke-{memory}mb", expected, args.timeout, expected_sha256, memory)
            report["results"].append(result)
            write_report(report)
            print(json.dumps(result), flush=True)
        report.update(status="complete", passed=True)
        write_report(report)
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        report.update(status="failed", passed=False, error=str(error))
        try:
            write_report(report)
        except OSError as report_error:
            print(f"Could not record smoke failure: {report_error}", file=sys.stderr)
        print(f"Smoke test failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
