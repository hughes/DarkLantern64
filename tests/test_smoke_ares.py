"""Exercise failure retention and process ownership with a fake Python emulator."""
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import smoke_ares
from test_emulator_lease import child
from tools.emulator_lease import EmulatorLease

ROOT = Path(__file__).resolve().parents[1]


class OwnedExerciseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="owned-ares-test-", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = self.root / "input.bml"
        self.settings.write_text("Nintendo64\n  ExpansionPak: true\n  Input\n    Port1: active-controller\nHotkeys\n  FastForward: assigned\n")
        self.settings_before = self.settings.read_bytes()
        self.rom = self.root / "fake.z64"
        self.rom.write_bytes(b"retained fake ROM snapshot")
        self.sha = hashlib.sha256(self.rom.read_bytes()).hexdigest()
        self.processes = []

    def launch(self, command):
        original = subprocess.Popen
        def fake_popen(arguments, **kwargs):
            self.assertEqual(arguments[1], "--settings-file")
            process = original([sys.executable, "-u", "-c", command], **kwargs)
            self.processes.append(process)
            return process
        return patch.object(smoke_ares.subprocess, "Popen", fake_popen)

    def check_retained(self, result):
        status = json.loads((self.root / result["run_status"]).read_bytes())
        self.assertEqual(status, result)
        self.assertEqual((self.root / result["tested_rom"]).read_bytes(), self.rom.read_bytes())
        self.assertEqual(result["rom_sha256"], self.sha)
        self.assertTrue((self.root / result["log"]).is_file())
        self.assertTrue((self.root / result["stderr_log"]).is_file())
        self.assertEqual(self.settings.read_bytes(), self.settings_before)
        self.assertTrue(result["cleanup_complete"])

    def test_success_holds_lease_through_owned_cleanup_and_preserves_unrelated_process(self):
        unrelated = child("import time;time.sleep(60)")
        try:
            with patch.object(smoke_ares, "ROOT", self.root), self.launch(
                "import time;print('DL64 boot_ok world=3d',flush=True);"
                "print('ms=20.5 cpu_ms=6.25 heap=1200/8000',flush=True);"
                "print('EXPECTED_DONE',flush=True);time.sleep(60)"):
                result = smoke_ares.exercise(sys.executable, self.settings, self.rom, "fake-success", "EXPECTED_DONE", 3,
                    self.sha, lease_owner="unit-run", provenance={"scenario": "synthetic"})
            self.check_retained(result)
            self.assertTrue(result["passed"])
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["lease"]["state"], "released")
            states = [row["state"] for row in result["lease_events"]]
            self.assertEqual(states, ["queued", "acquired", "registered", "running", "stopping", "stopped", "released"])
            self.assertEqual(result["peak_sampled_heap_bytes"], 1200)
            self.assertEqual(result["max_reported_cpu_ms"], 6.25)
            self.assertIsNone(unrelated.poll())
            self.assertTrue(all(process.poll() is not None for process in self.processes))
        finally:
            unrelated.terminate(); unrelated.wait(timeout=5)

    def test_timeout_keeps_rom_partial_metrics_stderr_and_structured_failure(self):
        with patch.object(smoke_ares, "ROOT", self.root), self.launch(
            "import sys,time;print('ms=31 cpu_ms=9 heap=2400/8000',flush=True);"
            "print('partial diagnostic',file=sys.stderr,flush=True);time.sleep(60)"):
            with self.assertRaises(smoke_ares.AresRunError) as failure:
                smoke_ares.exercise(sys.executable, self.settings, self.rom, "fake-timeout", "NO_SUCH_EVENT", .25, self.sha)
        result = failure.exception.result
        self.check_retained(result)
        self.assertFalse(result["passed"])
        self.assertIn("timed out", result["error"])
        self.assertEqual(result["failure_kind"], "emulator_timeout")
        self.assertEqual(result["peak_sampled_heap_bytes"], 2400)
        self.assertIn("partial diagnostic", (self.root / result["stderr_log"]).read_text())
        self.assertTrue(all(process.poll() is not None for process in self.processes))

    def test_assertion_and_early_exit_keep_failure_and_cleanup(self):
        for name, command, message in (("fake-assert", "print('ASSERTION FAILED: synthetic',flush=True)", "assertion"),
                                       ("fake-exit", "print('partial startup',flush=True)", "exited")):
            with self.subTest(name=name), patch.object(smoke_ares, "ROOT", self.root), self.launch(command):
                with self.assertRaises(smoke_ares.AresRunError) as failure:
                    smoke_ares.exercise(sys.executable, self.settings, self.rom, name, "NO_SUCH_EVENT", 2, self.sha)
            self.check_retained(failure.exception.result)
            self.assertIn(message, failure.exception.result["error"])

    def test_bad_verified_hash_retains_snapshot_without_starting_process(self):
        with patch.object(smoke_ares, "ROOT", self.root), patch.object(smoke_ares.subprocess, "Popen", side_effect=AssertionError("must not launch")):
            with self.assertRaises(smoke_ares.AresRunError) as failure:
                smoke_ares.exercise(sys.executable, self.settings, self.rom, "fake-stale", "DONE", 1, "0"*64)
        self.check_retained(failure.exception.result)
        self.assertIn("changed after", failure.exception.result["error"])

    def test_wait_timeout_reports_queue_and_never_starts_process(self):
        with EmulatorLease(), patch.object(smoke_ares, "ROOT", self.root), patch.object(smoke_ares.subprocess, "Popen", side_effect=AssertionError("must not launch")):
            with self.assertRaises(smoke_ares.AresRunError) as failure:
                smoke_ares.exercise(sys.executable, self.settings, self.rom, "fake-queued", "DONE", 1,
                                    self.sha, lease_wait_seconds=.04)
        self.check_retained(failure.exception.result)
        self.assertEqual(failure.exception.result["failure_kind"], "lease_timeout")
        self.assertEqual(failure.exception.result["lease_events"][0]["state"], "queued")
        self.assertEqual(failure.exception.result["lease"]["state"], "acquisition_failed")

    def test_process_launch_failure_retains_evidence_and_releases_lease(self):
        with patch.object(smoke_ares, "ROOT", self.root), patch.object(smoke_ares.subprocess, "Popen", side_effect=OSError("fake launch error")):
            with self.assertRaises(smoke_ares.AresRunError) as failure:
                smoke_ares.exercise(sys.executable, self.settings, self.rom, "fake-launch", "DONE", 1, self.sha)
        self.check_retained(failure.exception.result)
        self.assertEqual(failure.exception.result["lease"]["state"], "released")
        self.assertEqual(failure.exception.result["failure_kind"], "OSError")

    @unittest.skipUnless(shutil.which("node") and (ROOT / ".dev/tla-precheck/node_modules/tla-precheck").is_dir(), "Local pinned TLA interpreter unavailable")
    def test_observed_os_lifecycle_executes_through_dsl_interpreter(self):
        with patch.object(smoke_ares, "ROOT", self.root), self.launch("import time;print('DONE',flush=True);time.sleep(60)"):
            result = smoke_ares.exercise(sys.executable, self.settings, self.rom, "fake-model-trace", "DONE", 2, self.sha)
        completed = subprocess.run([shutil.which("node"), str(ROOT / "tools/check_emulator_lease.mjs"),
            "--trace", str(self.root / result["run_status"]), "--trace-only"], capture_output=True, text=True, check=True)
        checked = json.loads(completed.stdout)
        self.assertEqual(checked["trace_interpreter_steps"], 8)
        self.assertFalse(checked["python_os_equivalence_proven"])


if __name__ == "__main__":
    unittest.main()
