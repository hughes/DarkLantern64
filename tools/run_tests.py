"""Run Python tests with manifest allowances, UTF-8 and an owned temporary tree.

The default discovers the portable-python check from acceptance_manifest.json.
A declared fixture failure is retained as known_failure; unrelated failures,
undeclared skips and changed inputs always return a nonzero exit status.
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
import tempfile
import time
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


def _modules():
    # Direct invocation must use the same package namespace as -m unittest.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from tools import validation_scenarios, source_snapshot
    return validation_scenarios, source_snapshot


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def retry_windows_io(operation):
    # Windows may deny both CreateDirectory and atomic replacement briefly
    # during concurrent writers. Never reinterpret exhaustion as test success.
    for attempt in range(20):
        try:
            return operation()
        except OSError as error:
            if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 19:
                raise
            time.sleep(.01)


def write_report(path, report):
    path = Path(path)
    retry_windows_io(lambda: path.parent.mkdir(parents=True, exist_ok=True))
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
        staged = Path(stream.name)
        stream.write((json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf-8"))
    try:
        retry_windows_io(lambda: os.replace(staged, path))
    finally:
        staged.unlink(missing_ok=True)


def portable_spec(root, manifest=None):
    validation, _ = _modules()
    path = Path(manifest or Path(root) / "tools/acceptance_manifest.json").resolve()
    data = validation.load_manifest(path)
    matches = [row for row in data["checks"] if row["id"] == "portable-python" and row["kind"] == "python_unittest"]
    if len(matches) != 1:
        raise ValueError("Acceptance manifest needs exactly one portable-python unittest check")
    return dict(matches[0]), path


def execute_suite(root, spec, stream=None):
    """Execute only; classification remains authoritative in validation_scenarios."""
    validation, _ = _modules()
    root = Path(root).resolve()
    scripts = root / "tools"
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != scripts]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    start = (root / spec.get("start_directory", "tests")).resolve()
    if not start.is_relative_to(root):
        raise ValueError("Test discovery directory escapes project root")
    suite = unittest.defaultTestLoader.discover(str(start), pattern=spec.get("pattern", "test_*.py"))
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=validation.StructuredTestResult).run(suite)
    report = validation.classify_unittest(result.testsRun, result.failure_rows, result.error_rows, result.skip_rows, spec)
    # These categories describe observed skips. They cannot add an allowance.
    local = [row for row in report["skipped_tests"] if row["declared"] and
             ("source assets" in row["message"] or "example is not installed" in row["message"])]
    capability = [row for row in report["skipped_tests"] if row["declared"] and row not in local]
    report["unavailable_local_content"] = local
    tool_skips = [row for row in report["skipped_tests"] if any(word in row["message"].lower() for word in
                  ("compiler", "gcc", "sdk", "interpreter", "mksprite"))]
    report["unavailable_os_capabilities"] = [row for row in capability if row not in tool_skips]
    report["unavailable_tools"] = tool_skips
    report["undeclared_skips"] = [row for row in report["skipped_tests"] if not row["declared"]]
    return report


def _worker(root, spec, output):
    try:
        report = execute_suite(root, spec)
    except BaseException as error:
        report = dict(status="incomplete" if isinstance(error, KeyboardInterrupt) else "failed",
                      passed=False, accepted=False, reason=type(error).__name__ + ": " + str(error))
    report["process_environment"] = dict(temp=tempfile.gettempdir(), python_utf8=sys.flags.utf8_mode,
                                         io_encoding=sys.stderr.encoding, pycache_prefix=sys.pycache_prefix)
    write_report(output, report)
    return 0 if report["accepted"] else 1


def run_tests(root=ROOT, *, spec=None, manifest=None, output=None, pattern=None, timeout=1800):
    """Run a child process and retain an input-bound report after TEMP cleanup."""
    _, snapshot = _modules()
    from tools.emulator_lease import OwnedProcessJob
    root = Path(root).resolve()
    if spec is None:
        spec, manifest_path = portable_spec(root, manifest)
    else:
        spec = dict(spec)
        manifest_path = Path(manifest).resolve() if manifest is not None else None
    if pattern is not None:
        spec["pattern"] = pattern
    complete_selection = False
    if manifest_path is not None:
        declared, _ = portable_spec(root, manifest_path)
        complete_selection = spec == declared
    manifest_raw = manifest_path.read_bytes() if manifest_path is not None else None
    before = snapshot.inventory(root, external=False)
    identity = dict(source_sha256=snapshot.digest_inventory(before), files=before,
                    include_external=False,
                    manifest_path=str(manifest_path) if manifest_path else None,
                    manifest_sha256=_digest(manifest_raw) if manifest_raw is not None else None,
                    test_spec_sha256=_digest(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()))
    run_id = uuid.uuid4().hex
    retained = root / "build/test-results" / (run_id + ".json")
    destination = Path(output).resolve() if output is not None else root / "build/test-results/latest.json"
    for path in (retained, destination):
        if manifest_path is not None and path == manifest_path or path.is_relative_to(root) and path.relative_to(root).as_posix() in before:
            raise ValueError("Test report would overwrite captured input: " + str(path))
        if path.is_relative_to(root) and any(path.is_relative_to(root / folder) for folder in snapshot.SOURCE_DIRS):
            raise ValueError("Test report must stay outside source directories: " + str(path))
    storage = root / ".dev/test-runs"
    retry_windows_io(lambda: storage.mkdir(parents=True, exist_ok=True))
    work = Path(tempfile.mkdtemp(prefix="python-", dir=storage)).resolve()
    temporary = work / "temp"
    temporary.mkdir()
    spec_path, worker_output = work / "spec.json", work / "worker-result.json"
    write_report(spec_path, spec)
    env = dict(os.environ, TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONPYCACHEPREFIX=str(work / "pycache"))
    env.pop("PYTHONOPTIMIZE", None)
    started = time.perf_counter()
    report = dict(status="incomplete", passed=False, accepted=False, reason="Test process did not produce a result")
    exit_code = None
    try:
        with OwnedProcessJob() as owner:
            process = owner.spawn([sys.executable, "-X", "utf8", "-m", "tools.run_tests", "--worker",
                                   "--root", str(root), "--spec", str(spec_path), "--output", str(worker_output)],
                                  cwd=root, env=env)
            exit_code = process.wait(timeout=timeout)
        if worker_output.is_file():
            report = json.loads(worker_output.read_bytes())
        if exit_code != (0 if report.get("accepted") else 1):
            report.update(status="incomplete", passed=False, accepted=False,
                          reason="Test process exit disagrees with its structured result")
    except (OSError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        report.update(status="incomplete", passed=False, accepted=False, reason=type(error).__name__ + ": " + str(error))
    finally:
        # Remove only the uniquely allocated directory, after descendants stop.
        if not work.is_relative_to(storage.resolve()):
            raise RuntimeError("Owned test temporary tree escaped its storage directory")
        try:
            shutil.rmtree(work)
        except OSError as error:
            report.update(status="incomplete", passed=False, accepted=False,
                          reason="Cannot clean owned test temporary tree: " + str(error))
    try:
        after = snapshot.inventory(root, external=False)
        if after != before or manifest_path is not None and manifest_path.read_bytes() != manifest_raw:
            report.update(status="stale", passed=False, accepted=False, reason="Test inputs changed during the run")
    except (OSError, ValueError) as error:
        report.update(status="stale", passed=False, accepted=False, reason="Cannot recheck test inputs: " + str(error))
    report.update(schema_version=1, run_id=run_id, root=str(root), inputs=identity,
                  selection=dict(id=spec.get("id"), start_directory=spec.get("start_directory", "tests"),
                                 pattern=spec.get("pattern", "test_*.py"), complete_declared_suite=complete_selection),
                  runner=dict(elapsed_seconds=time.perf_counter()-started, exit_code=exit_code,
                              temp_path=str(temporary), temporary_cleaned=not work.exists(),
                              utf8_requested=True, owned_process_tree=True),
                  report_path=str(retained.resolve()), published_path=str(destination.resolve()),
                  scope="Python unittest evidence only; does not certify editor, ROM or hardware checks.")
    write_report(retained, report)
    write_report(destination, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--pattern", help="Override discovery pattern; report retains this subset selection")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--spec", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if args.worker:
        if args.spec is None or args.output is None:
            parser.error("Worker requires --spec and --output")
        return _worker(args.root, json.loads(args.spec.read_bytes()), args.output)
    report = run_tests(args.root, spec=json.loads(args.spec.read_bytes()) if args.spec else None,
                       manifest=args.manifest, output=args.output, pattern=args.pattern)
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0 if report["accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
