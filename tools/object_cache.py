"""Content-checked C object caching using GCC's actual dependency output.

Dependency discovery is repeated even on hits: adding a header earlier in an
include search path must not silently reuse an object built with another file.
Successful compilations independently emit -MD dependencies and must agree
with the discovered, immutable input set. System/SDK headers are included.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

SCHEMA_VERSION = 1
ENVIRONMENT_KEYS = ("CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "COMPILER_PATH",
                    "GCC_EXEC_PREFIX", "LIBRARY_PATH", "SOURCE_DATE_EPOCH")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex[:8] + ".tmp")
    temporary.write_bytes(json.dumps(value, indent=2).encode() + b"\n")
    os.replace(temporary, path)


def parse_dependencies(text, cwd):
    """Parse a single -M/-MD make rule, including escaped spaces and drives."""
    text = re.sub(r"\\\r?\n", "", text)
    boundary = re.search(r":(?=\s|$)", text)
    if not boundary:
        raise ValueError("GCC dependency file contains no make rule")
    text = text[boundary.end():]
    names, token, index = [], [], 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in " \\#:\t":
            token.append(text[index + 1]); index += 2
        elif char.isspace():
            if token:
                names.append("".join(token)); token = []
            index += 1
        else:
            if char == "$" and index + 1 < len(text) and text[index + 1] == "$":
                token.append("$"); index += 2
            else:
                token.append(char); index += 1
    if token:
        names.append("".join(token))
    if not names:
        raise ValueError("GCC dependency file is empty")
    result = []
    for name in names:
        path = Path(name)
        if not path.is_absolute():
            path = Path(cwd) / path
        path = path.resolve()
        if path not in result:
            result.append(path)
    return sorted(result, key=lambda path: path.as_posix())


def snapshot(paths):
    result = []
    for path in sorted({Path(path).resolve() for path in paths}, key=lambda path: path.as_posix()):
        before = path.stat()
        checksum = digest(path)
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise RuntimeError("Input changed while hashing: " + str(path))
        result.append(dict(path=path.as_posix(), sha256=checksum, bytes=after.st_size,
                           mtime_ns=after.st_mtime_ns))
    return result


def hashes(rows):
    return {row["path"]: row["sha256"] for row in rows}


def assert_unchanged(rows):
    for row in rows:
        path = Path(row["path"])
        if not path.is_file():
            raise RuntimeError("Build input disappeared: " + str(path))
        stat = path.stat()
        if stat.st_size != row["bytes"] or stat.st_mtime_ns != row["mtime_ns"] or digest(path) != row["sha256"]:
            raise RuntimeError("Build input changed: " + str(path))


def compiler_identity(compiler, *, env=None, cwd=None):
    compiler = Path(compiler).resolve()
    environment = dict(os.environ if env is None else env)
    files = [compiler]

    def query(flag):
        return subprocess.run([str(compiler), flag], cwd=cwd, env=environment, check=True,
                              capture_output=True, text=True).stdout.strip()

    properties = {flag: query(flag) for flag in ("--version", "-dumpmachine", "-dumpspecs",
                                               "-print-prog-name=cc1", "-print-prog-name=as",
                                               "-print-file-name=specs")}
    for flag in ("-print-prog-name=cc1", "-print-prog-name=as", "-print-file-name=specs"):
        value = properties[flag]
        path = Path(value)
        if not path.is_file():
            found = shutil.which(value, path=environment.get("PATH"))
            path = Path(found) if found else path
        if path.is_file():
            files.append(path.resolve())
    for parent in {path.parent for path in files}:
        files.extend(parent.glob("*.dll"))
    rows = snapshot(files)
    # GCC's built-in specs can be large; bind their complete bytes, keep a
    # compact report rather than repeating them for every translation unit.
    return dict(schema_version=SCHEMA_VERSION, executable=compiler.as_posix(),
                files=[{key: row[key] for key in ("path", "sha256", "bytes")} for row in rows],
                version=properties["--version"], target=properties["-dumpmachine"],
                specs_sha256=hashlib.sha256(properties["-dumpspecs"].encode()).hexdigest(),
                environment={key: environment.get(key) for key in ENVIRONMENT_KEYS})


class CompileFailure(RuntimeError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


class ObjectCache:
    def __init__(self, directory, compiler, identity, *, cwd, env=None):
        self.directory = Path(directory)
        self.compiler = Path(compiler).resolve()
        self.identity = identity
        self.cwd = Path(cwd).resolve()
        self.env = env
        self.reports = []

    def _attempt(self, source, flags):
        attempt = self.directory / "attempts" / uuid.uuid4().hex[:12]
        attempt.mkdir(parents=True)
        return dict(source=Path(source).resolve().as_posix(), flags=list(map(str, flags)),
                    compiler_identity=self.identity, attempt=str(attempt), status="discovering", reasons=[])

    def _run(self, command, report, phase):
        command = [str(arg) for arg in command]
        report[phase + "_command"] = command
        result = subprocess.run(command, cwd=self.cwd, env=self.env, capture_output=True, text=True)
        log = Path(report["attempt"]) / (phase + ".log")
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        report[phase + "_log"] = str(log)
        report[phase + "_returncode"] = result.returncode
        if result.returncode:
            report.update(status="failed", reasons=[phase + "_failed"])
            write_json(Path(report["attempt"]) / "report.json", report)
            raise CompileFailure(phase + " failed for " + report["source"] + "; log: " + str(log), report)

    def discover(self, source, flags):
        report = self._attempt(source, flags)
        depfile = Path(report["attempt"]) / "discover.d"
        self._run([self.compiler, *flags, "-M", "-MF", depfile, "-MT", "dl64_object", source], report, "discover")
        paths = parse_dependencies(depfile.read_text(), self.cwd)
        source = Path(source).resolve()
        if source not in paths:
            raise CompileFailure("GCC dependencies omitted source " + str(source), report)
        report["dependency_hashes"] = snapshot(paths)
        return report

    def compile(self, source, output, flags, *, discovery=None, clean=False):
        report = discovery or self.discover(source, flags)
        output = Path(output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        report["object"] = str(output)
        action = dict(source=report["source"], flags=report["flags"], compiler_identity=self.identity)
        key = hashlib.sha256(canonical(action)).hexdigest()
        manifest = self.directory / "entries" / (key + ".json")
        current = hashes(report["dependency_hashes"])
        reasons, previous = [], None
        if clean:
            reasons.append("clean_build_requested")
        else:
            try:
                previous = json.loads(manifest.read_bytes())
                if previous.get("schema_version") != SCHEMA_VERSION or any(previous.get(field) != action[field] for field in action):
                    previous = None
            except (OSError, ValueError):
                previous = None
            if not previous:
                reasons.append("no_valid_cache_entry")
                try:
                    last = json.loads((self.directory / "index" / (hashlib.sha256(report["source"].encode()).hexdigest() + ".json")).read_bytes())
                    if last.get("flags") != action["flags"]:
                        reasons.append("flags_changed")
                    if last.get("compiler_identity") != action["compiler_identity"]:
                        reasons.append("compiler_or_toolchain_changed")
                except (OSError, ValueError):
                    pass
            if previous:
                old = hashes(previous.get("dependency_hashes", []))
                for path in sorted(set(old) | set(current)):
                    if old.get(path) != current.get(path):
                        reasons.append(("dependency_added:" if path not in old else
                                        "dependency_removed:" if path not in current else "dependency_changed:") + path)
                blob = self.directory / "blobs" / (previous["object_sha256"] + ".o")
                if not blob.is_file() or digest(blob) != previous["object_sha256"]:
                    reasons.append("cached_object_missing_or_changed")
                if not reasons:
                    assert_unchanged(report["dependency_hashes"])
                    if not output.is_file() or digest(output) != previous["object_sha256"]:
                        temporary = output.with_name(output.name + "." + uuid.uuid4().hex[:8] + ".tmp")
                        shutil.copyfile(blob, temporary); os.replace(temporary, output)
                        report["restored_object"] = True
                    report.update(status="reused", reasons=["actual_dependencies_and_compiler_flags_unchanged"],
                                  object_sha256=previous["object_sha256"], cache_entry=str(manifest))
                    write_json(Path(report["attempt"]) / "report.json", report)
                    self.reports.append(report)
                    return report
        report["reasons"] = reasons
        attempt = Path(report["attempt"])
        object_temp, depfile = attempt / "compiled.o", attempt / "compiled.d"
        assert_unchanged(report["dependency_hashes"])
        try:
            self._run([self.compiler, *flags, "-MD", "-MF", depfile, "-MT", "dl64_object",
                       "-c", source, "-o", object_temp], report, "compile")
            actual = parse_dependencies(depfile.read_text(), self.cwd)
            if {path.as_posix() for path in actual} != set(current):
                raise RuntimeError("Dependencies changed between discovery and compilation")
            assert_unchanged(report["dependency_hashes"])
            checksum = digest(object_temp)
            if not clean:
                blob = self.directory / "blobs" / (checksum + ".o")
                blob.parent.mkdir(parents=True, exist_ok=True)
                if not blob.is_file() or digest(blob) != checksum:
                    temporary = blob.with_name(blob.name + "." + uuid.uuid4().hex[:8] + ".tmp")
                    shutil.copyfile(object_temp, temporary); os.replace(temporary, blob)
                write_json(manifest, dict(schema_version=SCHEMA_VERSION, **action,
                           dependency_hashes=report["dependency_hashes"], object_sha256=checksum))
                write_json(self.directory / "index" / (hashlib.sha256(report["source"].encode()).hexdigest() + ".json"), action)
            temporary = output.with_name(output.name + "." + uuid.uuid4().hex[:8] + ".tmp")
            shutil.copyfile(object_temp, temporary); os.replace(temporary, output)
            report.update(status="rebuilt", object_sha256=checksum, cache_entry=str(manifest) if not clean else None)
            write_json(attempt / "report.json", report)
            self.reports.append(report)
            return report
        except Exception as error:
            report.update(status="failed", reasons=[*reasons, "compile_failed_or_inputs_changed"], error=str(error))
            write_json(attempt / "report.json", report)
            self.reports.append(report)
            if isinstance(error, CompileFailure):
                raise
            raise CompileFailure(str(error), report) from error
