"""Run OS/process lease refinements without launching Ares or building a ROM."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def verify():
    sys.path.insert(0, str(ROOT / "tests"))
    sys.path.insert(0, str(ROOT))
    import test_emulator_lease
    import test_smoke_ares
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(module)
                               for module in (test_emulator_lease, test_smoke_ares))
    output = io.StringIO()
    started = time.perf_counter()
    result = unittest.TextTestRunner(stream=output, verbosity=2).run(suite)
    paths = ("tools/emulator_lease.py", "tools/smoke_ares.py", "tests/test_emulator_lease.py", "tests/test_smoke_ares.py",
             "docs/models/emulator-lease.machine.ts", "tools/check_emulator_lease.mjs")
    return dict(schema_version=1, passed=result.wasSuccessful(), status="complete" if result.wasSuccessful() else "failed",
        platform=platform.platform(), python=sys.version, tests_run=result.testsRun,
        elapsed_seconds=round(time.perf_counter()-started, 3), skipped=[dict(test=str(test), reason=reason) for test, reason in result.skipped],
        failures=[dict(test=str(test), traceback=trace) for test, trace in result.failures + result.errors],
        test_output=output.getvalue(), source_sha256={path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in paths},
        boundary="Real OS lease, real disposable Python processes, observed lifecycle through DSL interpreter when available; no Ares, ROM, GPU-performance or Python/OS formal-equivalence claim.",
        windows_crash_job_tested=os.name == "nt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "build/emulator-lease-proof/os-refinement.json")
    args = parser.parse_args()
    report = verify()
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in ("passed", "tests_run", "elapsed_seconds", "skipped", "boundary")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
