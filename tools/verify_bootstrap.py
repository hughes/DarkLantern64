"""Exercise discovery and standard tests in a copy without ignored local state.

Copies tracked files and non-ignored pending source changes, never external/,
build/, .dev/, .git/ or Bazel outputs. It does not install, build an SDK, open a
GUI, or claim that unavailable editor/ROM prerequisites passed verification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]


def read_pattern_result(path, pattern, exit_code):
    """A failed command must never borrow another subset's successful report."""
    path = Path(path)
    try:
        if not path.is_file():
            raise ValueError("Test command did not produce its fresh pattern report")
        value = json.loads(path.read_bytes())
        if not isinstance(value, dict) or not isinstance(value.get("selection"), dict):
            raise ValueError("Test command report has no valid selection")
        if value["selection"].get("pattern") != pattern:
            raise ValueError("Test command report belongs to a different pattern")
        if type(value.get("accepted")) is not bool or exit_code != (0 if value["accepted"] else 1):
            raise ValueError("Test command exit disagrees with its fresh report")
        if value["accepted"] and value.get("status") not in ("passed", "known_failure"):
            raise ValueError("Test command acceptance contradicts its status")
        return value
    except (OSError, ValueError, TypeError) as error:
        return dict(status="incomplete", passed=False, accepted=False, reason=str(error), tests_run=None,
                    selection=dict(pattern=pattern), inputs={}, runner=dict(temporary_cleaned=None),
                    report_path=str(path) if path.is_file() else None)


def verify(root=ROOT, output=None):
    root = Path(root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from tools.run_tests import write_report
    from tools.emulator_lease import OwnedProcessJob
    listing = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    destination = root / ".dev/bootstrap" / uuid.uuid4().hex
    checkout = destination / "checkout"
    files = {}
    for name in sorted(set(value.decode("utf-8") for value in listing.stdout.split(b"\0") if value)):
        relative = Path(name)
        if any(part in (".git", ".dev", "build", "external", "__pycache__") or part.startswith("bazel-") for part in relative.parts):
            continue
        source = root / relative
        if not source.is_file():
            continue
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError("Bootstrap inputs must be regular project files: " + name)
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        target.write_bytes(raw)
        files[name] = hashlib.sha256(raw).hexdigest()
    missing = {key: str(checkout / "unavailable" / key) for key in
               ("host_compiler", "n64_sdk", "msys2_bash", "bazel", "ares", "editor", "runner", "vulkan_sdk", "host_cxx")}
    missing["python"] = sys.executable
    configuration = destination / "missing-tools.json"
    write_report(configuration, missing)
    command_environment = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    doctor_run = subprocess.run([sys.executable, "-X", "utf8", "tools/development_environment.py", "--root", str(checkout),
                                 "--configured", str(configuration), "--no-probe"], cwd=checkout, env=command_environment,
                                capture_output=True, text=True, encoding="utf-8", check=True, timeout=30)
    doctor = json.loads(doctor_run.stdout)
    results = []
    for pattern in ("test_development_environment.py", "test_test_runner.py", "test_validation_scenarios.py",
                    "test_project_status.py", "test_project_capabilities.py"):
        log = destination / (pattern + ".log")
        report_path = destination / (pattern + ".result.json")
        report_path.unlink(missing_ok=True)
        with log.open("w", encoding="utf-8") as stream, OwnedProcessJob() as owner:
            process = owner.spawn([sys.executable, "-X", "utf8", "tools/run_tests.py", "--pattern", pattern,
                                   "--output", str(report_path)],
                                  cwd=checkout, env=command_environment, stdout=stream, stderr=subprocess.STDOUT)
            exit_code = process.wait(timeout=180)
        result = read_pattern_result(report_path, pattern, exit_code)
        result["bootstrap_log"] = str(log)
        result["bootstrap_exit_code"] = exit_code
        results.append(result)
    passed = all(not doctor["tools"][key]["available"] for key in missing if key != "python") and all(row["accepted"] for row in results)
    result = dict(schema_version=1, passed=passed, status="passed" if passed else "failed",
                  private_root=str(destination), checkout=str(checkout), copied_inputs=files,
                  copied_source_sha256=hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                  excluded_local_state=[".git", ".dev", "build", "external", "bazel outputs", "ignored authored/imported data"],
                  environment=doctor,
                  tests=[dict(pattern=row["selection"]["pattern"], status=row["status"], tests_run=row.get("tests_run"),
                              accepted=row.get("accepted", False), reason=row.get("reason"), exit_code=row["bootstrap_exit_code"],
                              temporary_cleaned=row.get("runner", {}).get("temporary_cleaned"), inputs=row.get("inputs", {}),
                              report_path=row.get("report_path"), log_path=row["bootstrap_log"],
                              report_sha256=hashlib.sha256(Path(row["report_path"]).read_bytes()).hexdigest()
                              if row.get("report_path") and Path(row["report_path"]).is_file() else None) for row in results],
                  host_dependencies=dict(python=sys.executable, pillow=doctor["tools"]["pillow"]["version"]),
                  scope="Clean-source bootstrap subset: discovery, runner, manifest classifier, status currentness/stale fixtures and capability docs. Python/Pillow reuse the installed host. Full Python, C, editor, SDK/ROM, emulator and hardware validation are not certified.")
    write_report(destination / "result.json", result)
    if output:
        write_report(output, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = verify(args.root, args.output)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
