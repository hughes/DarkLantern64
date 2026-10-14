"""Observe a process creation token without attaching to or changing it."""
import ctypes
import os


def creation_filetime(pid):
    if os.name != "nt":
        return None
    from ctypes import wintypes as w
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    api.OpenProcess.restype = w.HANDLE
    api.GetProcessTimes.argtypes = [w.HANDLE, ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME),
                                   ctypes.POINTER(w.FILETIME), ctypes.POINTER(w.FILETIME)]
    api.GetProcessTimes.restype = w.BOOL
    api.CloseHandle.argtypes = [w.HANDLE]
    api.CloseHandle.restype = w.BOOL
    handle = api.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        created, ended, kernel, user = (w.FILETIME() for _ in range(4))
        if not api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended),
                                   ctypes.byref(kernel), ctypes.byref(user)):
            return None
        return created.dwLowDateTime | (created.dwHighDateTime << 32)
    finally:
        api.CloseHandle(handle)
