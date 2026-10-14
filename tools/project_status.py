"""Read-only project provenance, capabilities, sessions and environment status.

Never instantiates ProjectLevels: even its catalog can cook lighting or populate
caches. Artifact freshness and historical test outcome are independent fields.
"""
from __future__ import annotations

import sys
sys.dont_write_bytecode = True

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def canonical_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def artifact(kind, path=None, *, freshness="unavailable", outcome="unavailable", reasons=(), **fields):
    status = ("stale" if freshness == "stale" else "unavailable" if freshness == "unavailable"
              else "incomplete" if freshness == "unknown" or outcome == "incomplete"
              else "failing" if outcome in ("failing", "known_failure") else "passing"
              if outcome == "passing" else "current")
    return dict(kind=kind, path=str(path) if path else None, status=status,
                freshness=freshness, outcome=outcome, reasons=list(reasons), source_bindings={}, **fields)


class Inspector:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.hashes = {}
        self._inventory = {}
        self._checked_directories = set()
        self._recipe_sources = None
        self.observed_stats = {}

    @staticmethod
    def _stamp(info):
        return info.st_mtime_ns, info.st_size, info.st_ino

    def audit(self):
        changed = []
        for path, stamp in self.observed_stats.items():
            # Session markers are asynchronous observations, not frozen cook
            # inputs. Their process tokens are checked separately.
            if path.name in ("state.json", "session.json", "run-status.json") and path.is_relative_to(self.root / ".dev"):
                continue
            try:
                if self._stamp(path.stat()) != stamp:
                    changed.append(str(path))
            except OSError:
                changed.append(str(path))
        if self._inventory:
            try:
                from .source_snapshot import source_paths
            except ImportError:
                from source_snapshot import source_paths
            for external, captured in self._inventory.items():
                try:
                    names = {p.relative_to(self.root).as_posix() for p in source_paths(self.root, external=external)}
                    changed.extend(str(self.root / name) for name in sorted(names ^ set(captured)))
                except (OSError, ValueError) as error:
                    changed.append("Source inventory no longer readable: " + str(error))
        if self._recipe_sources is not None:
            names = {p.relative_to(self.root).as_posix() for p in (self.root / "tools").glob("*.py")}
            names.update(p.relative_to(self.root).as_posix() for p in (self.root / "src").rglob("*") if p.suffix in (".c", ".h", ".inc", ".def"))
            changed.extend(str(self.root / name) for name in sorted(names ^ set(self._recipe_sources)))
        return changed

    def path(self, value, base=None):
        path = Path(value)
        return path if path.is_absolute() else (base or self.root) / path

    def local(self, path):
        path = Path(os.path.abspath(self.path(path)))
        if not path.is_relative_to(self.root):
            raise ValueError("Path is outside this project: " + str(path))
        for item in (path, *path.parents):
            if item == self.root.parent:
                break
            if item in self._checked_directories:
                break
            try:
                info = item.lstat()
            except FileNotFoundError:
                continue
            if item.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Project status does not follow linked paths: " + str(item))
            if item.is_dir():
                self._checked_directories.add(item)
        return path

    def read(self, path):
        path = Path(path)
        before = path.stat()
        raw = path.read_bytes()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise OSError("File changed during status collection: " + str(path))
        self.hashes[Path(os.path.abspath(path))] = hashlib.sha256(raw).hexdigest()
        self.observed_stats.setdefault(Path(os.path.abspath(path)), self._stamp(after))
        return raw

    def json(self, path):
        return json.loads(self.read(self.local(path)))

    def sha(self, path):
        path = Path(os.path.abspath(path))
        if path not in self.hashes:
            before = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            after = path.stat()
            if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                raise OSError("File changed during status collection: " + str(path))
            self.hashes[path] = digest.hexdigest()
            self.observed_stats.setdefault(path, self._stamp(after))
        return self.hashes[path]

    def verify(self, path, expected, reasons, label):
        if not expected:
            reasons.append(label + ": recorded hash unavailable")
            return "unknown"
        try:
            actual = self.sha(path)
        except (OSError, ValueError) as error:
            reasons.append(label + ": unavailable (" + str(error) + ")")
            return "unknown"
        if actual != expected:
            reasons.append(label + ": bytes changed")
            return "stale"
        return "current"

    def inventory(self, external=True):
        if external not in self._inventory:
            try:
                from .source_snapshot import source_paths
            except ImportError:
                from source_snapshot import source_paths
            self._inventory[external] = {p.relative_to(self.root).as_posix(): self.sha(p)
                                        for p in source_paths(self.root, external=external)}
        return self._inventory[external]

    def recipe_sources(self):
        if self._recipe_sources is None:
            paths = sorted((self.root / "tools").glob("*.py"))
            paths += sorted(p for p in (self.root / "src").rglob("*") if p.suffix in (".c", ".h", ".inc", ".def"))
            self._recipe_sources = {p.relative_to(self.root).as_posix(): self.sha(p) for p in paths}
        return self._recipe_sources


def combine(values):
    return "stale" if "stale" in values else "unknown" if "unknown" in values or not values else "current"


def checkout(root):
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=10,
                                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        if result.returncode:
            raise ValueError(result.stderr.strip())
        return result.stdout.strip()
    try:
        branch = git("symbolic-ref", "--quiet", "--short", "HEAD")
    except (OSError, subprocess.SubprocessError, ValueError):
        branch = None
    try:
        return dict(branch=branch or "detached", commit=git("rev-parse", "HEAD"),
                    dirty=bool(git("status", "--porcelain", "--untracked-files=normal")), status="current", reason=None)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return dict(branch=None, commit=None, dirty=None, status="unavailable", reason=str(error))


def source_bindings(inspector, report, scene, partition):
    """Check saved JSON and every dependency declared by the cooker."""
    reasons, states = [], []
    mission = (report.get("mission") or {}).get("report") or {}
    identity = (report.get("renderer_diagnostics") or {}).get("identity") or (mission.get("renderer_diagnostics") or {}).get("identity") or {}
    raw_hash = identity.get("source_file_sha256") or report.get("source_file_sha256") or mission.get("source_file_sha256")
    semantic_hash = identity.get("renderer_source_sha256")
    if raw_hash:
        basis = identity.get("source_file_sha256_basis") or report.get("source_file_sha256_basis") or mission.get("source_file_sha256_basis", "saved_bytes")
        if basis == "canonical_json":
            states.append("current" if canonical_sha256(scene["document"]) == raw_hash else "stale")
            if states[-1] == "stale":
                reasons.append("Scene: canonical document changed")
        elif basis == "saved_bytes":
            states.append(inspector.verify(scene["path"], raw_hash, reasons, "Scene"))
        else:
            states.append("unknown"); reasons.append("Scene: unknown hash basis")
    elif semantic_hash:
        try:
            from .cook_diagnostics import source_identity
        except ImportError:
            from cook_diagnostics import source_identity
        states.append("current" if source_identity(scene["document"]) == semantic_hash else "stale")
        if states[-1] == "stale":
            reasons.append("Scene: canonical document changed")
    else:
        states.append("unknown")
        reasons.append("Cook has no saved scene identity")
    streaming = report.get("streaming", {})
    manifest_hash = report.get("mission_manifest_sha256") or streaming.get("mission_manifest_sha256")
    if partition:
        if manifest_hash:
            states.append(inspector.verify(partition, manifest_hash, reasons, "Partition"))
        elif report.get("canonical_partition_sha256"):
            states.append("current" if canonical_sha256(inspector.json(partition)) == report["canonical_partition_sha256"] else "stale")
            if states[-1] == "stale":
                reasons.append("Partition: canonical document changed")
        else:
            states.append("unknown"); reasons.append("Partition: recorded hash unavailable")
    if report.get("recipe_sources") is not None:
        states.append("current" if report["recipe_sources"] == inspector.recipe_sources() else "stale")
        if states[-1] == "stale":
            reasons.append("Cook source recipe changed")
    else:
        states.append("unknown"); reasons.append("Cook source recipe unavailable; recook for provenance")
    dependencies = report.get("dependencies", []) + report.get("content_dependencies", [])
    for row in dependencies:
        uri = row.get("uri") or row.get("path")
        if not uri:
            continue
        target = inspector.path(uri[5:]) if uri.startswith("code:") else inspector.path(uri, inspector.root / "content")
        try:
            target = inspector.local(target)
            states.append(inspector.verify(target, row.get("sha256"), reasons, "Dependency " + uri))
        except ValueError as error:
            states.append("unknown"); reasons.append(str(error))
    recipe = (report.get("lighting") or {}).get("recipe", {})
    for name, digest in recipe.get("sources", {}).items():
        states.append(inspector.verify(inspector.path(name), digest, reasons, "Lighting input " + name))
    compiler = recipe.get("compiler", {})
    if compiler.get("path"):
        states.append(inspector.verify(inspector.path(compiler["path"]), compiler.get("sha256"), reasons, "Lighting compiler"))
    return states, reasons


def cook_status(inspector, path, scene, partition):
    try:
        report = inspector.json(path)
        states, reasons = source_bindings(inspector, report, scene, partition)
        output = path.parent.parent
        mission = report.get("mission", {})
        mission_report_path = path.with_name("mission_report.json")
        if mission_report_path.is_file():
            mission_report = inspector.json(mission_report_path)
            more, why = source_bindings(inspector, mission_report, scene, partition)
            states.extend(more); reasons.extend(why)
            mission = {**mission, "sha256": mission_report.get("package_sha256"),
                       "path": "romfs/missions/mission-" + str(mission_report.get("package_sha256")) + ".dlm"}
        if mission.get("path"):
            states.append(inspector.verify(inspector.local(output / mission["path"]), mission.get("sha256"), reasons, "Stream package"))
        preview = output / "editor-assets/levels/first_room.json"
        if not preview.is_file() and not report.get("experimental_stream"):
            reasons.append("Authoring preview descriptor is unavailable")
            states.append("unknown")
        # Ordinary source_sha256 is the raw source followed by URI/NUL/asset
        # bytes. This also catches unrecorded source identity in legacy reports.
        if not partition and report.get("source_sha256") and "dependencies" in report:
            digest = hashlib.sha256(inspector.read(scene["path"]))
            for dep in report["dependencies"]:
                uri = dep["uri"]
                target = inspector.path(uri[5:]) if uri.startswith("code:") else inspector.path(uri, inspector.root / "content")
                digest.update(uri.encode()); digest.update(b"\0"); digest.update(inspector.read(inspector.local(target)))
            if digest.hexdigest() != report["source_sha256"]:
                states.append("stale"); reasons.append("Cooked scene/dependency digest changed")
            elif "Cook has no saved scene identity" in reasons:
                states[0] = "current"; reasons.remove("Cook has no saved scene identity")
        bridge = report.get("bridge_diagnostics") or (report.get("streaming") or {}).get("bridge_diagnostics") or {}
        outcome = "failing" if bridge.get("valid") is False else "passing"
        if outcome == "failing":
            reasons.extend("Target bridge: " + str(r.get("message", r)) for r in bridge.get("failures", []))
        row = artifact("cook", path, freshness=combine(states), outcome=outcome, reasons=reasons,
                       scope="target-cook" if report.get("experimental_stream") else "authoring-cook",
                       authoring_preview_outcome="passing" if preview.is_file() else "unavailable",
                       package_sha256=mission.get("sha256"), playable=report.get("playable"),
                       native_play_supported=report.get("native_play_supported", report.get("playable")))
        row["source_bindings"] = {"scene": scene["file"], "source_sha256": scene["source_sha256"],
                                  "partition": str(partition) if partition else None,
                                  "cooked_source_sha256": report.get("source_sha256")}
        return row
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        row = artifact("cook", path, freshness="unknown", outcome="incomplete", reasons=[str(error)])
        row["source_bindings"] = {"scene": scene["file"], "source_sha256": scene["source_sha256"]}
        return row


def rom_status(inspector, path, report):
    reasons, states = [], []
    # Cheap recipe checks first. Once an input differs, the build is already
    # stale: rehashing gigabytes of historical ROMFS cannot make it current.
    inputs = sorted(report.get("input_hashes", []), key=lambda r: 0 if str(r.get("path", "")).replace("\\", "/").startswith((inspector.root / "tools").as_posix() + "/") else 1)
    for row in inputs:
        states.append(inspector.verify(inspector.path(row["path"]), row.get("sha256"), reasons, "Build input " + row["path"]))
        if states[-1] == "stale":
            reasons.append("Historical build: remaining inputs and artifact bytes were not rehashed")
            break
    if not report.get("input_hashes"):
        states.append("unknown"); reasons.append("Build input inventory unavailable")
    rom = inspector.local(inspector.path(report["rom"]))
    historical = "stale" in states
    def output_check(target, digest, label):
        if historical:
            if not target.is_file():
                reasons.append(label + ": local artifact unavailable")
                return "unknown"
            return "current"
        return inspector.verify(target, digest, reasons, label)
    states.append(output_check(rom, report.get("rom_sha256"), "ROM"))
    if not historical:
        for value, digest in report.get("artifact_hashes", {}).items():
            states.append(output_check(inspector.local(inspector.path(value)), digest, "Build artifact " + value))
    for key in ("runtime_debug_map", "input_manifest"):
        if report.get(key):
            states.append(output_check(inspector.local(inspector.path(report[key])), report.get(key + "_sha256"), key))
    row = artifact("rom", path, freshness=combine(states), outcome="passing", reasons=reasons,
                   rom=str(rom), rom_sha256=report.get("rom_sha256"), build_id=report.get("signature"))
    row["source_bindings"] = {"levels": [{"scene": value.get("source"), "partition": value.get("mission_manifest"),
                               "cooked_source_sha256": value.get("content", {}).get("source_sha256")}
                              for value in report.get("level_catalog", {}).get("levels", [])]}
    return row


def validation_status(inspector, path, report):
    reasons, states, known, missing, failures = [], [], [], [], []
    inputs = report.get("inputs", {})
    if inputs.get("files"):
        current = inspector.inventory(inputs.get("include_external", "runner" not in report))
        states.append("current" if inputs["files"] == current else "stale")
        if inputs.get("source_sha256") and canonical_sha256(inputs["files"]) != inputs["source_sha256"]:
            states.append("unknown"); reasons.append("Source inventory does not match its recorded identity")
        if states[-1] == "stale":
            changed = sorted(k for k in set(inputs["files"]) | set(current) if inputs["files"].get(k) != current.get(k))
            reasons.append("Source snapshot changed: " + ", ".join(changed[:12]))
    else:
        states.append("unknown"); reasons.append("Source snapshot unavailable; historical outcome only")
    manifest_hash = report.get("manifest_sha256") or inputs.get("manifest_sha256")
    manifest_path = inspector.path(report.get("manifest") or inputs.get("manifest_path") or "tools/acceptance_manifest.json")
    if manifest_hash:
        states.append(inspector.verify(manifest_path, manifest_hash, reasons, "Acceptance manifest"))
    if combine(states) == "current":
        for name, digest in inputs.get("external_files", {}).items():
            states.append(inspector.verify(Path(name), digest, reasons, "External input " + name))
    outcomes = []
    nodes = report.get("results", [])
    if not nodes and "tests_run" in report:
        nodes = [dict(id="python", result=report, status=report.get("status"),
                      accepted=report.get("accepted"), passed=report.get("passed"), required=True)]
    try:
        from .validation_scenarios import classify_unittest
    except ImportError:
        from validation_scenarios import classify_unittest
    try:
        manifest = json.loads(inspector.read(manifest_path))
        spec = next(s for s in manifest.get("checks", []) if s.get("kind") == "python_unittest")
    except (OSError, ValueError, StopIteration, AttributeError, TypeError):
        spec = {}
    for node in nodes:
        if node.get("required") is False:
            continue
        result = node.get("result", {})
        if "tests_run" in result:
            classified = classify_unittest(result["tests_run"], result.get("failures", []), [],
                                           result.get("skipped_tests", []), spec)
            outcomes.append({"passed": "passing", "known_failure": "known_failure", "failed": "failing"}.get(classified["status"], "incomplete"))
            known.extend({"report": str(path), "id": r["id"], "message": r["message"].strip().splitlines()[-1]}
                         for r in classified.get("failures", []) if r.get("known"))
            for problem in classified.get("failures", []):
                terminal = problem["message"].strip().splitlines()[-1] if problem["message"].strip() else ""
                failures.append(dict(report=str(path), id=problem["id"], message=terminal, known=problem["known"]))
                reasons.append("Test " + problem["id"] + ": " + terminal)
            missing.extend({"report": str(path), **r} for r in classified.get("skipped_tests", []))
            if node.get("status") in ("failed", "incomplete", "stale", "queued", "running"):
                outcomes[-1] = "failing" if node["status"] == "failed" else "incomplete"
                reasons.append("Node " + str(node.get("id")) + " recorded " + node["status"] + ": " + str(node.get("reason")))
            elif node.get("accepted") is False or node.get("status") == "passed" and node.get("passed") is False:
                outcomes[-1] = "incomplete"; reasons.append("Node acceptance disagrees with test outcome")
        else:
            outcomes.append("passing" if node.get("status") == "passed" and node.get("passed") is True
                            else "failing" if node.get("status") == "failed" else "incomplete")
        for evidence in node.get("evidence", []):
            target = inspector.local(inspector.path(evidence["path"]))
            if combine(states) == "current":
                states.append(inspector.verify(target, evidence.get("sha256"), reasons, "Evidence " + evidence["path"]))
            elif not target.is_file():
                reasons.append("Evidence unavailable: " + str(target))
                states.append("unknown")
    outcome = "failing" if "failing" in outcomes else "incomplete" if not outcomes or "incomplete" in outcomes else "known_failure" if "known_failure" in outcomes else "passing"
    if report.get("status") == "stale":
        states.append("stale"); reasons.append("Run recorded changed inputs: " + str(report.get("reason", report.get("error", ""))))
    elif report.get("status") in ("queued", "running", "incomplete") and outcome != "failing":
        outcome = "incomplete"; reasons.append("Run did not complete: " + str(report.get("reason", report.get("error", ""))))
    row = artifact("validation", path, freshness=combine(states), outcome=outcome, reasons=reasons,
                   profile=report.get("profile"), known_fixture_failures=known, unavailable_test_data=missing,
                   test_failures=failures,
                   selection=report.get("selection"), scope=report.get("scope"),
                   full_acceptance=report.get("full_acceptance", False))
    row["source_bindings"] = {"source_sha256": inputs.get("source_sha256"), "manifest_sha256": manifest_hash,
                              "manifest": str(manifest_path)}
    return row


def process_inventory():
    """Observe processes only; never attach, wait, terminate or change settings."""
    if os.name != "nt":
        rows = []
        for entry in Path("/proc").glob("[0-9]*"):
            try:
                rows.append(dict(pid=int(entry.name), executable=str((entry / "exe").resolve()),
                                 command_line=(entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace"), started_at_utc=None))
            except OSError:
                pass
        return rows
    cmd = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(ares|darklantern64_project_editor)(\\.exe)?$' } | "
           "ForEach-Object { @{pid=$_.ProcessId;executable=$_.ExecutablePath;command_line=$_.CommandLine;"
           "started_at_utc=$_.CreationDate.ToUniversalTime().ToString('o')} } | ConvertTo-Json -Compress")
    completed = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    if completed.returncode:
        raise OSError(completed.stderr.strip())
    value = json.loads(completed.stdout) if completed.stdout.strip() else []
    return value if isinstance(value, list) else [value]


def _timestamp(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def sessions_status(inspector, processes=None):
    rows, claimed = [], set()
    try:
        observed = process_inventory() if processes is None else processes
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        observed = []
        rows.append(dict(kind="process_inventory", pid=None, alive=None, owned=False, identity_status="unknown", reason=str(error)))
    by_pid = {p["pid"]: p for p in observed}
    paths = list((inspector.root / ".dev/editor").glob("*/session.json"))
    paths += list((inspector.root / ".dev/ares/runs").glob("*/run-status.json"))
    for path in sorted(paths):
        try:
            data = inspector.json(path)
            kind = "editor" if data.get("project_editor") else "ares"
            if kind == "editor" and path.with_name("state.json").is_file():
                state = inspector.json(path.with_name("state.json"))
                if state.get("pid") == data.get("pid"):
                    data.update(state)
            pid = data.get("pid") if kind == "editor" else data.get("process_pid")
            if not pid:
                continue
            process = by_pid.get(pid)
            owned, reason = False, "Saved session process is no longer running"
            if process:
                token = data.get("process_started_at_utc")
                actual = _timestamp(process.get("started_at_utc"))
                expected = _timestamp(token)
                if data.get("process_creation_filetime"):
                    expected = data["process_creation_filetime"] / 10000000 - 11644473600
                name = Path(process.get("executable") or "").name.lower()
                correct_name = "project_editor" in name if kind == "editor" else name in ("ares", "ares.exe")
                command = process.get("command_line") or ""
                marker = str(inspector.path(data.get("tested_rom", ""))) if kind == "ares" else str(inspector.root)
                same_command = marker.replace("\\", "/").lower() in command.replace("\\", "/").lower()
                # A PID alone does not establish ownership, including PID reuse.
                owned = bool(correct_name and same_command and expected is not None and actual is not None and abs(actual - expected) <= .001)
                reason = "Owned process and creation identity match" if owned else "Process is running; ownership or creation identity is unknown/mismatched"
                claimed.add(pid)
            provenance = data.get("provenance", {})
            rows.append(dict(kind=kind, pid=pid, alive=process is not None, owned=owned,
                             identity_status="known" if owned else "unknown", session_path=str(path),
                             scene=data.get("source") or provenance.get("scene"), build=provenance.get("build_id"),
                             package_sha256=data.get("package_sha256"), rom_sha256=data.get("rom_sha256"),
                             source_sha256=data.get("source_saved_sha256", data.get("source_sha256")),
                             partition_sha256=data.get("partition_saved_sha256"),
                             cooked_source_sha256=data.get("cooked_report_source_sha256"),
                             native_play=data.get("native_play"), stream_preview=data.get("stream_preview"), dirty=data.get("dirty"),
                             status=data.get("status"), reason=reason))
        except (OSError, ValueError, TypeError, AttributeError) as error:
            rows.append(dict(kind="session", pid=None, alive=None, owned=False, identity_status="unknown", session_path=str(path), reason=str(error)))
    for process in observed:
        if process["pid"] in claimed:
            continue
        name = Path(process.get("executable") or "").name.lower()
        if "ares" in name or "project_editor" in name:
            rows.append(dict(kind="ares" if "ares" in name else "editor", pid=process["pid"], alive=True, owned=False,
                             identity_status="unknown", scene=None, build=None, package_sha256=None, session_path=None,
                             reason="Unrelated or unregistered process; preserved", executable=process.get("executable")))
    return rows


def latest(rows, kind):
    candidates = [r for r in rows if r["kind"] == kind]
    current = [r for r in candidates if r["freshness"] == "current"]
    def modified(row):
        try:
            return Path(row["path"]).stat().st_mtime_ns
        except OSError:
            return 0
    chosen = max(current or candidates, key=modified) if candidates else artifact(kind, reasons=["No matching local artifact is available"])
    return {**chosen, "matching_current": bool(current)}


def collect_status(root=ROOT, draft=None, include_doctor=True, *, processes=None):
    inspector = Inspector(root)
    root = inspector.root
    scenes, reports, partitions, by_file = [], [], {}, {}
    for path in sorted((root / "content").glob("*.mission.json")):
        try:
            value = inspector.json(path)
            partitions.setdefault(value["source"], []).append(path)
        except (OSError, ValueError, KeyError, TypeError) as error:
            reports.append(artifact("partition", path, freshness="unknown", outcome="incomplete", reasons=[str(error)]))
    for path in sorted((root / "content").glob("*.json")):
        if path.name.endswith(".mission.json") or path.name == "level_bundle.json":
            continue
        try:
            document = inspector.json(path)
            if not isinstance(document, dict) or "entities" not in document:
                continue
            paired = partitions.get(path.name, [])
            partition = paired[0] if len(paired) == 1 else None
            part = dict(file=str(partition.relative_to(root)) if partition else None,
                        sha256=inspector.sha(partition) if partition else None,
                        status="current" if partition else "incomplete" if len(paired) > 1 else "unavailable",
                        reason="Multiple partitions name this scene" if len(paired) > 1 else None if partition else "No saved partition")
            scene = dict(file=path.relative_to(root).as_posix(), title=document.get("title", path.stem),
                         source_sha256=inspector.sha(path), canonical_sha256=canonical_sha256(document),
                         partition=part, draft=dict(dirty=False, partition_dirty=False),
                         authoring_profile=document.get("authoring_profile"), path=path, document=document)
            by_file[scene["file"]] = scene; scenes.append(scene)
            output = root / "build" if path.stem == "first_room" else root / "build/scenes" / path.stem
            cook_path = output / "generated/level_report.json"
            if cook_path.is_file():
                row = cook_status(inspector, cook_path, scene, partition)
                if len(paired) > 1:
                    row.update(freshness="unknown", outcome="incomplete", status="incomplete")
                    row["reasons"].append("Multiple saved partitions name this scene; no unambiguous cook match")
                reports.append(row)
        except (OSError, ValueError, TypeError) as error:
            reports.append(artifact("scene", path, freshness="unknown", outcome="incomplete", reasons=[str(error)]))
    for path in sorted((root / "build").glob("*/build.json")):
        try:
            data = inspector.json(path)
            rom_row = rom_status(inspector, path, data)
            reports.append(rom_row)
            for value in data.get("level_catalog", {}).get("levels", []):
                source = inspector.local(inspector.path(value["source"]))
                scene = by_file.get(source.relative_to(root).as_posix())
                cooked = inspector.local(inspector.path(value["cooked_dir"])) / "generated/level_report.json"
                if scene and cooked.is_file() and rom_row["freshness"] == "current":
                    paired = scene["partition"]["file"]
                    reports.append(cook_status(inspector, cooked, scene, root / paired if paired else None))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            reports.append(artifact("rom", path, freshness="unknown", outcome="incomplete", reasons=[str(error)]))
    validation_paths = set((root / ".dev/v5").glob("*/summary.json"))
    validation_paths.update((root / "build").glob("*validation*summary.json"))
    validation_paths.update((root / "build/test-results").glob("*.json"))
    for path in sorted(validation_paths):
        try:
            reports.append(validation_status(inspector, path, inspector.json(path)))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            reports.append(artifact("validation", path, freshness="unknown", outcome="incomplete", reasons=[str(error)]))
    for path in sorted((root / "docs/evidence").glob("*.json")):
        try:
            evidence = inspector.json(path)
            # Maintained exports can bind individual scene/scenario hashes but
            # lack the complete checkout/SDK inventory of acceptance summaries.
            # Preserve their historical outcome without promoting it to current.
            outcome = "passing" if evidence.get("passed") is True else "failing" if evidence.get("passed") is False else "incomplete"
            reports.append(artifact("evidence", path, freshness="unknown", outcome=outcome,
                                    reasons=["Historical export; complete current input inventory unavailable"],
                                    scope=evidence.get("scope")))
        except (OSError, ValueError, TypeError, AttributeError) as error:
            reports.append(artifact("evidence", path, freshness="unknown", outcome="incomplete", reasons=[str(error)]))
    draft_status = dict(scene_file=None, dirty=None, partition_dirty=None, reason="Draft unavailable outside a registered editor snapshot")
    if draft:
        name = str(draft.get("scene_file", "")).replace("\\", "/")
        if name.startswith(str(root).replace("\\", "/") + "/"):
            name = name[len(str(root)) + 1:]
        scene = by_file.get(name)
        if scene and isinstance(draft.get("scene"), dict):
            draft_hash = canonical_sha256(draft["scene"])
            part_file = draft.get("partition_file")
            part_hash, saved_part_hash = None, None
            if isinstance(draft.get("partition"), dict):
                part_hash = canonical_sha256(draft["partition"])
                if part_file:
                    try:
                        saved_part_hash = canonical_sha256(inspector.json(inspector.local(inspector.path(part_file))))
                    except (OSError, ValueError):
                        pass
            draft_status = dict(scene_file=name, source_sha256=draft_hash, saved_source_sha256=scene["canonical_sha256"],
                                dirty=draft_hash != scene["canonical_sha256"], partition_file=part_file,
                                partition_sha256=part_hash, saved_partition_sha256=saved_part_hash,
                                partition_dirty=part_hash != saved_part_hash if part_hash else False)
            scene["draft"] = {key: draft_status[key] for key in ("dirty", "partition_dirty")}
    for scene in scenes:
        cooks = [r for r in reports if r.get("source_bindings", {}).get("scene") == scene["file"]]
        roms = [r for r in reports if any(str(inspector.path(v.get("scene") or "")).replace("\\", "/") == scene["path"].as_posix()
                                         for v in r.get("source_bindings", {}).get("levels", []))]
        scene["cook"] = latest(cooks, "cook"); scene["rom"] = latest(roms, "rom")
        del scene["path"]; del scene["document"]
    try:
        capabilities = inspector.json(root / "tools/project_capabilities.json")
        try:
            from .check_project_capabilities import validate
        except ImportError:
            from check_project_capabilities import validate
        errors = validate(capabilities, root)
        capabilities.update(status="current" if not errors else "stale", errors=errors)
    except (OSError, ValueError, AttributeError, TypeError) as error:
        capabilities = dict(status="unavailable", reason=str(error), capabilities=[], limits={})
    environment = {"status": "unavailable", "reason": "Doctor omitted by request"}
    if include_doctor:
        try:
            from .development_environment import inspect_environment
        except ImportError:
            from development_environment import inspect_environment
        environment = inspect_environment(root)
    sessions = sessions_status(inspector, processes)
    try:
        manifest = inspector.json(root / "tools/acceptance_manifest.json")
        declared_known = [r for check in manifest.get("checks", []) for r in check.get("known_failures", [])]
    except (OSError, ValueError, AttributeError, TypeError):
        declared_known = []
    changed = inspector.audit()
    if changed:
        for row in reports:
            if row["freshness"] == "current":
                row.update(freshness="unknown", status="incomplete")
                row["reasons"].append("Inputs changed during collection; refresh for a consistent snapshot")
        for scene in scenes:
            for kind in ("cook", "rom"):
                if scene[kind]["freshness"] == "current":
                    scene[kind].update(freshness="unknown", status="incomplete", matching_current=False)
        draft_status.update(dirty=None, partition_dirty=None, reason="Inputs changed during status collection")
    return dict(schema_version=1, generated_at_utc=datetime.now(timezone.utc).isoformat(), project_root=str(root),
                collection=dict(consistent=not changed, changed_inputs=changed),
                checkout=checkout(root), draft=draft_status, scenes=scenes, reports=reports,
                sessions=sessions, capabilities=capabilities, environment=environment,
                summary=dict(counts=dict(Counter(r["status"] for r in reports)),
                             declared_known_fixture_failures=declared_known,
                             new_test_failures=[k for r in reports for k in r.get("test_failures", []) if not k["known"]],
                             known_fixture_failures=[k for r in reports for k in r.get("known_fixture_failures", [])],
                             unavailable_test_data=[k for r in reports for k in r.get("unavailable_test_data", [])]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--draft-file", type=Path)
    parser.add_argument("--no-doctor", action="store_true")
    args = parser.parse_args(argv)
    draft = json.loads(args.draft_file.read_text(encoding="utf-8")) if args.draft_file else None
    print(json.dumps(collect_status(args.project, draft, not args.no_doctor), indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
