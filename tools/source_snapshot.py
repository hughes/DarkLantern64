"""Freeze validation inputs at stable build paths and archive their exact bytes."""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

SOURCE_DIRS = ("src", "content", "tools", "tests", "editor", "art", "docs/models")
# The required capability check reads these maintained pages. Keep them in the
# same source identity and Windows read guard as the tools that validate them.
# Generated docs/evidence remains outside the snapshot so publication is safe.
SOURCE_FILES = ("dependencies.json", "build.ps1", ".gitignore", "README.md",
                "docs/workflow.md", "docs/architecture.md", "docs/project-status.md",
                "docs/character-animation.md", "docs/bootstrap.md")
EXCLUDED = {".git", "__pycache__", ".dev", "build", "build_sdk_main", ".cache", "node_modules"}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_inventory(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_paths(root, *, external=True):
    root = Path(root).resolve()
    folders = list(SOURCE_DIRS)
    if external:
        folders += ["external/LightEngine", "external/tiny3d", "external/tiny3d/.git"]
    for name in SOURCE_FILES:
        path = root / name
        if path.is_file():
            yield path
    for name in folders:
        folder = root / name
        if not folder.is_dir():
            continue
        if folder.is_symlink() or getattr(folder.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Validation source directory must be regular: {folder}")
        for directory, children, files in os.walk(folder, followlinks=False):
            children[:] = sorted(child for child in children if child not in EXCLUDED and
                                  not child.startswith("bazel-"))
            for child in children:
                candidate = Path(directory) / child
                if candidate.is_symlink() or getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400:
                    raise ValueError(f"Validation source directory must be regular: {candidate}")
            for name in sorted(files):
                path = Path(directory) / name
                if ".git" in path.relative_to(root).parts and (name.endswith(".lock") or "logs" in path.relative_to(folder).parts):
                    continue
                if name in ("MODULE.bazel.lock", "imgui.ini") or name.endswith((".pyc", ".tmp", ".log")):
                    continue
                # A snapshot cannot certify a symlink's mutable external target.
                if path.is_symlink():
                    raise ValueError(f"Validation source file must be regular: {path}")
                if path.is_file():
                    yield path


def inventory(root, *, external=True):
    root = Path(root).resolve()
    return {path.relative_to(root).as_posix(): sha256(path)
            for path in source_paths(root, external=external)}


def external_inventory(paths):
    """Audit separately consumed compiler/runtime inputs; do not copy the SDK."""
    files = {}
    for value in paths:
        path = Path(value).resolve()
        candidates = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else []
        if not candidates:
            files[str(path)] = "missing"
        for candidate in candidates:
            files[str(candidate)] = sha256(candidate)
    return files


class SourceChanged(RuntimeError):
    pass


class ReadGuard:
    """Windows share-read handles prevent writes/replacement until exit.

    Elsewhere the mandatory inventory audits detect changes and reject the
    entire run. This guard does not change permissions on the user's files.
    """
    def __init__(self):
        self.handles = []

    def lock(self, paths):
        if os.name != "nt":
            return
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                           wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        try:
            for path in paths:
                handle = create(str(Path(path).resolve()), 0x80000000, 1, None, 3, 0x80, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise OSError(ctypes.get_last_error(), f"Cannot freeze input: {path}")
                self.handles.append(handle)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handles:
            from ctypes import wintypes
            close = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
            close.argtypes = [wintypes.HANDLE]
            close.restype = wintypes.BOOL
            for handle in reversed(self.handles):
                close(handle)
            self.handles.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class FrozenInputs:
    def __init__(self, root, archive, *, external=True, dependencies=()):
        self.root = Path(root).resolve()
        self.archive = Path(archive).resolve()
        if self.archive == self.root or not self.archive.is_relative_to(self.root / ".dev"):
            raise ValueError("Validation archive must be a new project-owned .dev directory")
        self.include_external = external
        self.dependencies = tuple(Path(path).resolve() for path in dependencies)
        self.guard = ReadGuard()
        self.files = {}
        self.external_files = {}

    def __enter__(self):
        try:
            self.files = inventory(self.root, external=self.include_external)
            self.external_files = external_inventory(self.dependencies)
            self.guard.lock([*(self.root / name for name in self.files),
                             *(Path(name) for name, value in self.external_files.items() if value != "missing")])
            self.assert_current()
            self.archive.mkdir(parents=True, exist_ok=False)
            for relative in self.files:
                target = self.archive / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.root / relative, target)
            self.assert_current()
            if inventory(self.archive, external=self.include_external) != self.files:
                raise SourceChanged("Archived input bytes differ from declared source snapshot")
            self.identity = {"source_sha256": digest_inventory(self.files),
                             "external_inputs_sha256": digest_inventory(self.external_files),
                             "files": self.files, "external_files": self.external_files,
                             "execution_root": str(self.root), "archive": str(self.archive),
                             "freeze_method": "Windows share-read handles and inventory audits" if os.name == "nt" else "inventory audits before/after each node and publication"}
            (self.archive.parent / "input-manifest.json").write_text(json.dumps(self.identity, indent=2) + "\n", encoding="utf-8")
            return self
        except BaseException:
            self.guard.close()
            raise

    def assert_current(self):
        try:
            current = inventory(self.root, external=self.include_external)
        except (OSError, ValueError) as error:
            raise SourceChanged("Source snapshot is no longer readable or regular: " + str(error)) from error
        if current != self.files:
            changed = sorted(key for key in set(current) | set(self.files) if current.get(key) != self.files.get(key))
            raise SourceChanged("Source snapshot changed: " + ", ".join(changed[:12]))
        if external_inventory(self.dependencies) != self.external_files:
            raise SourceChanged("Compiler, SDK, settings or other external validation input changed")

    def assert_archive(self):
        try:
            retained = inventory(self.archive, external=self.include_external)
        except (OSError, ValueError) as error:
            raise SourceChanged("Retained source archive is no longer readable or regular: " + str(error)) from error
        if retained != self.files:
            raise SourceChanged("Retained source archive changed; evidence is stale")

    def __exit__(self, *_):
        self.guard.close()


@contextlib.contextmanager
def project_run_lock(root, timeout=600):
    """One validation publisher per project; unrelated editors keep running."""
    path = Path(root).resolve() / ".dev/v5/pipeline.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Another validation run owns this project")
                time.sleep(.1)
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
