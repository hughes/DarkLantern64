"""Real OS leases and disposable Python processes; never launches Ares."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
import importlib.util

from tools.emulator_lease import EmulatorLease, EmulatorLeaseTimeout, OwnedProcessJob

ROOT = Path(__file__).resolve().parents[1]


def child(command, **kwargs):
    if os.name == "nt":
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        kwargs["startupinfo"] = startup
    return subprocess.Popen([sys.executable, "-u", "-c", command], **kwargs)


def process_alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.restype = ctypes.c_void_p
    api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = api.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return api.WaitForSingleObject(handle, 0) == 0x102
    finally:
        api.CloseHandle(handle)


class EmulatorLeaseTests(unittest.TestCase):
    def setUp(self):
        self.key = "DarkLantern64.Ares.LeaseTest." + uuid.uuid4().hex

    def test_cross_process_cross_directory_owners_serialize(self):
        command = (f"import sys;sys.path.insert(0,{str(ROOT)!r});" +
            "from tools.emulator_lease import EmulatorLease;import time,json;" +
            f"lease=EmulatorLease(key={self.key!r},timeout=5);" +
            "lease.acquire();start=time.monotonic();time.sleep(.2);end=time.monotonic();" +
            "lease.release();print(json.dumps([start,end,lease.status]))")
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as temporary:
            directories = [Path(temporary) / name for name in ("project-a", "project-b")]
            for directory in directories:
                directory.mkdir()
            processes = [child(command, cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for directory in directories]
            try:
                observations = []
                for process in processes:
                    stdout, stderr = process.communicate(timeout=10)
                    self.assertEqual(process.returncode, 0, stderr)
                    observations.append(json.loads(stdout))
                first, second = sorted(observations)
                self.assertGreaterEqual(second[0], first[1])
                self.assertEqual(first[2]["key"], second[2]["key"])
                self.assertTrue(all(row[2]["state"] == "released" for row in observations))
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.wait()

    def test_same_thread_reentrant_attempt_cannot_launch_another_owner(self):
        with EmulatorLease(key=self.key):
            started = time.monotonic()
            with self.assertRaises(EmulatorLeaseTimeout) as failure:
                EmulatorLease(key=self.key, timeout=.06).acquire()
            self.assertLess(time.monotonic()-started, .6)
            self.assertEqual(failure.exception.lease_status["state"], "acquisition_failed")

    def test_alternate_import_form_shares_process_guard(self):
        spec = importlib.util.spec_from_file_location("alternate_emulator_lease", ROOT / "tools/emulator_lease.py")
        alternate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(alternate)
        with EmulatorLease(key=self.key):
            with self.assertRaises(TimeoutError):
                alternate.EmulatorLease(key=self.key, timeout=.03).acquire()

    def test_crashed_owner_releases_os_lease(self):
        command = (f"import sys;sys.path.insert(0,{str(ROOT)!r});" +
            "from tools.emulator_lease import EmulatorLease;import time;" +
            f"lease=EmulatorLease(key={self.key!r});lease.acquire();print('held',flush=True);time.sleep(60)")
        owner = child(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(owner.stdout.readline().strip(), "held")
            with self.assertRaises(EmulatorLeaseTimeout):
                EmulatorLease(key=self.key, timeout=.05).acquire()
            owner.kill(); owner.wait(timeout=5)
            with EmulatorLease(key=self.key, timeout=5) as recovered:
                self.assertEqual(recovered.status["state"], "acquired")
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait()
            owner.stdout.close(); owner.stderr.close()

    @unittest.skipUnless(os.name == "nt", "Windows job crash containment")
    def test_owner_crash_kills_only_job_child_before_next_grant(self):
        command = (f"import sys;sys.path.insert(0,{str(ROOT)!r});" +
            "from tools.emulator_lease import EmulatorLease;import subprocess,time;" +
            f"lease=EmulatorLease(key={self.key!r});lease.acquire();" +
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=lease.creationflags);" +
            "lease.register(p);print(p.pid,flush=True);time.sleep(60)")
        unrelated = child("import time;time.sleep(60)")
        owner = child(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            line = owner.stdout.readline().strip()
            self.assertTrue(line.isdigit(), owner.stderr.read() if owner.poll() is not None else line)
            owned_pid = int(line)
            self.assertTrue(process_alive(owned_pid))
            owner.kill(); owner.wait(timeout=5)
            with EmulatorLease(key=self.key, timeout=5):
                self.assertFalse(process_alive(owned_pid))
                self.assertIsNone(unrelated.poll())
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait()
            owner.stdout.close(); owner.stderr.close()
            unrelated.terminate(); unrelated.wait(timeout=5)

    def test_reporting_failure_cannot_skip_process_cleanup(self):
        def broken_report(state):
            raise OSError("status disk unavailable")
        with EmulatorLease(key=self.key, on_state=broken_report) as lease:
            process = child("import time;time.sleep(60)", creationflags=lease.creationflags)
            lease.register(process)
        self.assertIsNotNone(process.poll())
        self.assertEqual(lease.status["state"], "released")
        self.assertIn("unavailable", lease.status["callback_error"])

    def test_interrupt_during_stopping_still_cleans_owned_process(self):
        def interrupt_stopping(state):
            if state["state"] == "stopping":
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            with EmulatorLease(key=self.key, on_state=interrupt_stopping) as lease:
                process = child("import time;time.sleep(60)", creationflags=lease.creationflags)
                lease.register(process)
        self.assertIsNotNone(process.poll())
        self.assertEqual(lease.status["state"], "released")
        with EmulatorLease(key=self.key, timeout=.1):
            pass

    def test_command_timeout_owns_descendants_and_preserves_unrelated_process(self):
        unrelated = child("import time;time.sleep(60)")
        command = ("import subprocess,sys,time;" +
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);" +
            "print(p.pid,flush=True);time.sleep(60)")
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                with OwnedProcessJob() as job:
                    process = job.spawn([sys.executable,"-u","-c",command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    descendant = int(process.stdout.readline().strip())
                    process.wait(timeout=.03)
            self.assertIsNotNone(process.poll())
            self.assertFalse(process_alive(descendant))
            self.assertIsNone(unrelated.poll())
        finally:
            if 'process' in locals():
                process.stdout.close(); process.stderr.close()
            unrelated.terminate(); unrelated.wait(timeout=5)

    @unittest.skipUnless(os.name == "nt", "Windows nested owned jobs")
    def test_outer_command_job_contains_inner_leased_process_after_owner_crash(self):
        command = (f"import sys;sys.path.insert(0,{str(ROOT)!r});" +
            "from tools.emulator_lease import EmulatorLease;import subprocess,time;" +
            f"lease=EmulatorLease(key={self.key!r});lease.acquire();" +
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=lease.creationflags);" +
            "lease.register(p);print(p.pid,flush=True);time.sleep(60)")
        unrelated = child("import time;time.sleep(60)")
        try:
            with OwnedProcessJob() as job:
                process = job.spawn([sys.executable,"-u","-c",command], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                descendant = int(process.stdout.readline().strip())
                self.assertTrue(process_alive(descendant))
            self.assertFalse(process_alive(descendant))
            with EmulatorLease(key=self.key, timeout=5):
                self.assertIsNone(unrelated.poll())
        finally:
            if 'process' in locals():
                process.stdout.close(); process.stderr.close()
            unrelated.terminate(); unrelated.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
