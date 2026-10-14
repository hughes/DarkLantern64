"""One automation emulator per host, independent of project/ROM directories.

Windows uses an OS mutex and a named kill-on-close job. The child is created
suspended, assigned to that job, and only then resumed. Interactive processes
are outside this job. POSIX uses a host-wide flock; its crash containment is
limited to the lease itself, rather than the Windows job guarantee.
"""
from __future__ import annotations

import ctypes
import builtins
import hashlib
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import signal

LEASE_KEY = "DarkLantern64.Ares.Automation.v1"
# CLI imports and package imports can load this file under different module
# names. Windows mutexes are recursive within one thread, so the Python guard
# must be process-wide across both import forms as well as across threads.
_guards, _guards_lock = vars(builtins).setdefault("__darklantern64_emulator_lease_registry_v1", ({}, threading.Lock()))


class EmulatorLeaseTimeout(TimeoutError):
    def __init__(self, status):
        self.lease_status = status
        super().__init__("Timed out waiting for the host emulator lease: " + status["key"])


def _guard(key):
    with _guards_lock:
        return _guards.setdefault(key, threading.Lock())


class _Windows:
    def __init__(self, key, *, with_mutex=True):
        from ctypes import wintypes as w
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.w = w
        for name, result, args in (
            ("CreateMutexW", w.HANDLE, [w.LPVOID, w.BOOL, w.LPCWSTR]),
            ("WaitForSingleObject", w.DWORD, [w.HANDLE, w.DWORD]),
            ("ReleaseMutex", w.BOOL, [w.HANDLE]), ("CloseHandle", w.BOOL, [w.HANDLE]),
            ("CreateJobObjectW", w.HANDLE, [w.LPVOID, w.LPCWSTR]),
            ("SetInformationJobObject", w.BOOL, [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]),
            ("QueryInformationJobObject", w.BOOL, [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID]),
            ("AssignProcessToJobObject", w.BOOL, [w.HANDLE, w.HANDLE]),
            ("TerminateJobObject", w.BOOL, [w.HANDLE, w.UINT]),
            ("OpenProcess", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
            ("IsProcessInJob", w.BOOL, [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]),
            ("CreateToolhelp32Snapshot", w.HANDLE, [w.DWORD, w.DWORD]),
            ("Thread32First", w.BOOL, [w.HANDLE, w.LPVOID]),
            ("Thread32Next", w.BOOL, [w.HANDLE, w.LPVOID]),
            ("OpenThread", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
            ("ResumeThread", w.DWORD, [w.HANDLE]),
        ):
            function = getattr(self.api, name)
            function.restype, function.argtypes = result, args
        self.mutex = self.api.CreateMutexW(None, False, "Global\\" + key) if with_mutex else None
        if with_mutex and not self.mutex:
            raise ctypes.WinError(ctypes.get_last_error())
        self.job, self.acquired = None, False
        self.job_name = "Global\\" + key + ".OwnedJob" if with_mutex else None

    def wait(self, milliseconds):
        result = self.api.WaitForSingleObject(self.mutex, milliseconds)
        if result in (0, 0x80):
            self.acquired = True
            return True, result == 0x80
        if result == 0x102:
            return False, False
        raise ctypes.WinError(ctypes.get_last_error())

    def prepare_job(self, deadline):
        w = self.w
        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", w.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                ("active_limit", w.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD), ("scheduling", w.DWORD)]
        class Limits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [("times", ctypes.c_longlong * 4), ("faults", w.DWORD),
                ("total", w.DWORD), ("active", w.DWORD), ("terminated", w.DWORD)]
        if self.job is None:
            self.job = self.api.CreateJobObjectW(None, self.job_name)
            if not self.job:
                raise ctypes.WinError(ctypes.get_last_error())
        limits = Limits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())
        # A dead previous owner can release its mutex before process teardown
        # completes. Recover only members of this automation's protected job.
        killed = False
        while True:
            accounting = Accounting()
            if not self.api.QueryInformationJobObject(self.job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not accounting.active:
                return killed
            if not killed:
                if not self.api.TerminateJobObject(self.job, 1):
                    raise ctypes.WinError(ctypes.get_last_error())
                killed = True
            if time.monotonic() >= deadline:
                raise TimeoutError("Prior owned emulator job did not drain")
            time.sleep(.01)

    def register(self, process):
        if not self.api.AssignProcessToJobObject(self.job, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def capture_members(self, deadline):
        """Retain exact job-member handles before termination removes their IDs.

        Nested-job accounting can reach zero before terminated process objects
        signal. A retained handle also avoids confusing teardown with PID reuse.
        """
        capacity, handles = 16, []
        try:
            while True:
                class ProcessIds(ctypes.Structure):
                    _fields_ = [("assigned", self.w.DWORD), ("count", self.w.DWORD),
                                ("pids", ctypes.c_size_t * capacity)]
                value = ProcessIds()
                ok = self.api.QueryInformationJobObject(self.job, 3, ctypes.byref(value), ctypes.sizeof(value), None)
                if ok and value.count >= value.assigned:
                    break
                error = ctypes.get_last_error()
                if not ok and error != 234:  # ERROR_MORE_DATA
                    raise ctypes.WinError(error)
                if time.monotonic() >= deadline:
                    raise TimeoutError("Owned job process inventory did not stabilize")
                capacity = max(capacity * 2, value.assigned) if not ok else max(capacity, value.assigned)
            for pid in value.pids[:value.count]:
                # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION. No access to
                # terminate an arbitrary PID is requested or used here.
                handle = self.api.OpenProcess(0x100000 | 0x1000, False, pid)
                if not handle:
                    error = ctypes.get_last_error()
                    if error == 87:  # ERROR_INVALID_PARAMETER: process exited.
                        continue
                    raise ctypes.WinError(error)
                try:
                    member = self.w.BOOL()
                    if not self.api.IsProcessInJob(handle, self.job, ctypes.byref(member)):
                        raise ctypes.WinError(ctypes.get_last_error())
                    if member.value:
                        handles.append((pid, handle))
                        handle = None
                finally:
                    if handle:
                        self.api.CloseHandle(handle)
            return handles
        except BaseException:
            self.close_members(handles)
            raise

    def wait_members(self, handles, deadline):
        for pid, handle in handles:
            remaining = max(0, int((deadline-time.monotonic()) * 1000))
            result = self.api.WaitForSingleObject(handle, remaining)
            if result == 0x102:
                raise TimeoutError("Owned job process did not finish teardown: " + str(pid))
            if result != 0:
                raise ctypes.WinError(ctypes.get_last_error())

    def close_members(self, handles):
        for _, handle in handles:
            self.api.CloseHandle(handle)

    def resume(self, process):
        w = self.w
        class Thread(ctypes.Structure):
            _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("thread", w.DWORD),
                ("process", w.DWORD), ("base", w.LONG), ("delta", w.LONG), ("flags", w.DWORD)]
        snapshot = self.api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            item, threads = Thread(), []
            item.size = ctypes.sizeof(item)
            valid = self.api.Thread32First(snapshot, ctypes.byref(item))
            while valid:
                if item.process == process.pid:
                    threads.append(item.thread)
                valid = self.api.Thread32Next(snapshot, ctypes.byref(item))
            if len(threads) != 1:
                raise RuntimeError("Suspended owned emulator must have exactly one primary thread")
            thread = self.api.OpenThread(2, False, threads[0])  # THREAD_SUSPEND_RESUME
            if not thread:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                previous = self.api.ResumeThread(thread)
                if previous == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
                if previous != 1:
                    raise RuntimeError("Owned primary thread must resume from exactly one suspension")
            finally:
                self.api.CloseHandle(thread)
        finally:
            self.api.CloseHandle(snapshot)

    def close(self):
        if self.job:
            self.api.CloseHandle(self.job)
            self.job = None
        if self.acquired:
            if not self.api.ReleaseMutex(self.mutex):
                raise ctypes.WinError(ctypes.get_last_error())
            self.acquired = False
        if self.mutex:
            self.api.CloseHandle(self.mutex)
            self.mutex = None


class _Posix:
    def __init__(self, key):
        import fcntl
        self.fcntl = fcntl
        name = "darklantern64-emulator-" + hashlib.sha256(key.encode()).hexdigest()[:24] + ".lock"
        self.file = (Path("/tmp") / name).open("a+b")

    def wait(self, milliseconds):
        try:
            self.fcntl.flock(self.file.fileno(), self.fcntl.LOCK_EX | self.fcntl.LOCK_NB)
            return True, False
        except BlockingIOError:
            time.sleep(milliseconds / 1000)
            return False, False

    def prepare_job(self, deadline):
        return False

    def close(self):
        if self.file:
            self.file.close()
            self.file = None


class EmulatorLease:
    def __init__(self, *, owner=None, provenance=None, timeout=600, on_state=None, key=LEASE_KEY):
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("Lease wait timeout must be finite and nonnegative")
        self.timeout, self.on_state = timeout, on_state
        self.status = dict(key=key, owner=owner or f"pid-{os.getpid()}", owner_pid=os.getpid(),
            state="new", provenance=provenance or {}, backend="windows_mutex_job" if os.name == "nt" else "posix_flock",
            abandoned_recovered=False, prior_job_drained=False, queue_seconds=0, process_pid=None)
        self.local_guard, self.backend, self.process = _guard(key), None, None
        self.local_acquired, self.acquired = False, False

    def _state(self, state):
        self.status["state"] = state
        if self.on_state:
            try:
                self.on_state(dict(self.status))
            except Exception as error:
                # Telemetry failures must never bypass owned-process cleanup.
                self.status["callback_error"] = str(error)

    def acquire(self):
        if self.acquired:
            raise RuntimeError("Emulator lease already acquired")
        started = time.monotonic()
        deadline = started + self.timeout
        self._state("queued")
        try:
            while not self.local_guard.acquire(timeout=min(.05, max(0, deadline - time.monotonic()))):
                if time.monotonic() >= deadline:
                    raise EmulatorLeaseTimeout(dict(self.status))
            self.local_acquired = True
            self.backend = _Windows(self.status["key"]) if os.name == "nt" else _Posix(self.status["key"])
            while True:
                acquired, abandoned = self.backend.wait(min(50, max(0, int((deadline-time.monotonic())*1000))))
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    raise EmulatorLeaseTimeout(dict(self.status))
            self.acquired = True
            self.status["abandoned_recovered"] = abandoned
            self.status["prior_job_drained"] = self.backend.prepare_job(deadline)
            self.status["queue_seconds"] = round(time.monotonic()-started, 6)
            self._state("acquired")
            return self
        except BaseException as error:
            self.status["queue_seconds"] = round(time.monotonic()-started, 6)
            self.status["state"] = "acquisition_failed"
            self._close()
            self._state("acquisition_failed")
            if isinstance(error, EmulatorLeaseTimeout):
                error.lease_status = dict(self.status)
            raise

    @property
    def creationflags(self):
        return 4 if os.name == "nt" else 0  # CREATE_SUSPENDED

    def register(self, process):
        if not self.acquired or self.process is not None:
            raise RuntimeError("Register one owned process after acquiring its lease")
        self.process = process
        self.status["process_pid"] = process.pid
        if os.name == "nt":
            self.backend.register(process)
        self._state("registered")
        if os.name == "nt":
            self.backend.resume(process)
        self._state("running")

    def stop_owned(self):
        if self.process is None:
            return
        interrupted = None
        try:
            self._state("stopping")
        except BaseException as error:
            interrupted = error
        if self.process.poll() is None:
            self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.status["process_returncode"] = self.process.returncode
        try:
            self._state("stopped")
        except BaseException as error:
            if interrupted is None:
                interrupted = error
        if interrupted is not None:
            raise interrupted

    def _close(self):
        if self.backend:
            self.backend.close()
            self.backend = None
        self.acquired = False
        if self.local_acquired:
            self.local_guard.release()
            self.local_acquired = False

    def release(self):
        if self.process is not None and self.process.poll() is None:
            raise RuntimeError("Cannot release lease while its owned emulator is alive")
        if os.name == "nt" and self.backend is not None:
            self.backend.prepare_job(time.monotonic()+5)
        self._close()
        self._state("released")

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exception):
        try:
            self.stop_owned()
        finally:
            # Refuse to authorize the next runnable emulator if cleanup failed.
            if self.process is None or self.process.poll() is not None:
                self.release()


class OwnedProcessJob:
    """Contain one command and all descendants, without taking the Ares lease.

    Use for verifier commands that may spawn editors or an inner leased Ares.
    Windows job nesting is supported on Windows 8 and newer. The context must
    outlive communicate()/wait(); timeout cleanup stays within this ownership.
    """
    def __init__(self):
        self.backend, self.process = None, None

    def __enter__(self):
        if os.name == "nt":
            self.backend = _Windows("", with_mutex=False)
            try:
                self.backend.prepare_job(time.monotonic()+5)
            except BaseException:
                self.backend.close()
                self.backend = None
                raise
        return self

    def spawn(self, arguments, **kwargs):
        if self.process is not None:
            raise RuntimeError("Owned process job can launch only one command")
        if os.name == "nt":
            if self.backend is None:
                raise RuntimeError("Enter owned process job before spawning")
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | 4
            if "startupinfo" not in kwargs:
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = subprocess.SW_HIDE
                kwargs["startupinfo"] = startup
        else:
            kwargs["start_new_session"] = True
        self.process = subprocess.Popen(arguments, **kwargs)
        try:
            if self.backend:
                self.backend.register(self.process)
                self.backend.resume(self.process)
        except BaseException:
            self.close()
            raise
        return self.process

    def close(self):
        members, backend = [], self.backend
        try:
            # Capture before stopping the leader: closing its inner leased job
            # may already remove descendants from the outer job's PID list.
            if backend:
                members = backend.capture_members(time.monotonic()+5)
            if self.process is not None:
                if os.name == "nt":
                    if self.process.poll() is None:
                        self.process.terminate()
                else:
                    try:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        self.process.kill()
                    else:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    self.process.wait(timeout=5)
                if os.name != "nt":
                    # The leader can exit while a descendant ignores SIGTERM.
                    # Finish the owned group even when wait() already returned.
                    try:
                        os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            if self.backend:
                self.backend.prepare_job(time.monotonic()+5)
                self.backend.wait_members(members, time.monotonic()+5)
        finally:
            try:
                if backend:
                    backend.close_members(members)
            finally:
                if self.backend:
                    self.backend.close()
                    self.backend = None

    def __exit__(self, *exception):
        self.close()
