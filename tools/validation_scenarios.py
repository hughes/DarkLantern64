"""Manifest validation and evidence adapters for the project acceptance pipeline.

New content uses recorded input and data expectations. The named legacy
adapters retain their existing parsers and budgets; none certify settled input
as wall-time performance. All commands use argv lists, never a shell.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
TOKEN = re.compile(r"\{([a-z_]+|(?:build|prepared):[a-z0-9-]+:[a-z_]+)\}")
CLASSIFICATIONS = {"portable", "editor", "functional", "strict_wall_time", "lifecycle", "capture_only"}
LEGACY = {"menu_idle", "expansion_gate", "menu_lifecycle", "district_functional", "district_strict", "district_capture"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return path


def evidence(path):
    path = Path(path).resolve()
    return dict(path=str(path), sha256=sha256(path), bytes=path.stat().st_size)


def load_manifest(path):
    document = json.loads(Path(path).read_bytes())
    validate_manifest(document)
    return document


def validate_manifest(document):
    require(isinstance(document, dict) and document.get("schema_version") == 1, "Acceptance manifest schema_version must be 1")
    nodes = {}
    for section in ("checks", "builds", "scenarios"):
        require(isinstance(document.get(section), list), f"Manifest requires {section} array")
        for spec in document[section]:
            require(isinstance(spec, dict), "Manifest node must be an object")
            ident = spec.get("id")
            require(isinstance(ident, str) and ID.fullmatch(ident) and ident not in nodes, f"Invalid or duplicate node ID: {ident}")
            require(type(spec.get("required")) is bool, ident + ": required must be explicit")
            require(isinstance(spec.get("profiles"), list) and spec["profiles"] and
                    all(p in ("all", "portable", "host") for p in spec["profiles"]), ident + ": invalid profiles")
            require(isinstance(spec.get("dependencies"), list) and all(isinstance(d, str) for d in spec["dependencies"]), ident + ": dependencies must be an array")
            if section != "builds":
                require(spec.get("classification") in CLASSIFICATIONS, ident + ": missing evidence classification")
            if section == "checks":
                require(spec.get("kind") in ("python_unittest", "command"), ident + ": unknown check kind")
                if spec["kind"] == "command":
                    require(isinstance(spec.get("argv"), list) and spec["argv"] and all(isinstance(a, str) for a in spec["argv"]), ident + ": argv required")
                    require(spec.get("parser", {}).get("kind") in ("exit_code", "json_file", "stdout_json"), ident + ": unknown parser")
                for name in ("known_failures", "allowed_skips"):
                    rows = spec.get(name, [])
                    require(isinstance(rows, list), ident + ": invalid " + name)
                    for row in rows:
                        require(isinstance(row.get("id"), str) and row["id"] and isinstance(row.get("message_pattern"), str) and
                                row["message_pattern"], ident + ": failure/skip allowances need exact ID and message pattern")
                        re.compile(row["message_pattern"])
            elif section == "builds":
                require(spec.get("kind") == "rom" and isinstance(spec.get("kwargs"), dict), ident + ": invalid ROM spec")
            else:
                require(spec.get("kind") in ("generic_replay", "legacy_route"), ident + ": unknown scenario kind")
                if spec["kind"] == "generic_replay":
                    require(spec.get("adapter") in ("native", "rom") and spec["classification"] == "functional",
                            ident + ": fixed-dt replay certifies functional state only")
                    require(spec.get("prepared_replay") or (spec.get("mission") and spec.get("route")), ident + ": missing replay data")
                else:
                    require(spec.get("adapter") in LEGACY and spec.get("build_id"), ident + ": unknown legacy adapter or build")
                    classification = {"district_strict": "strict_wall_time", "district_capture": "capture_only",
                                      "district_functional": "functional"}.get(spec["adapter"], "lifecycle")
                    require(spec["classification"] == classification, ident + ": adapter classification mismatch")
            nodes[ident] = spec
    for ident, spec in nodes.items():
        require(all(d in nodes and d != ident for d in spec["dependencies"]), ident + ": missing/self dependency")
        for key in ("build_id", "prepared_replay"):
            if key in spec:
                require(spec[key] in nodes, ident + ": unknown " + key)
                require(spec[key] in spec["dependencies"], ident + ": " + key + " must be an explicit dependency")
    visiting, complete = set(), set()
    def visit(ident):
        require(ident not in visiting, "Manifest dependency cycle at " + ident)
        if ident in complete:
            return
        visiting.add(ident)
        for dependency in nodes[ident]["dependencies"]:
            visit(dependency)
        visiting.remove(ident)
        complete.add(ident)
    for ident in nodes:
        visit(ident)
    return document


def resolve_argv(context, argv):
    """Substitute only declared tokens; code strings and JSON braces stay literal."""
    def value(match):
        name = match[1]
        if name.startswith("build:") or name.startswith("prepared:"):
            kind, ident, field = name.split(":")
            collection = context["build_artifacts" if kind == "build" else "prepared_scenarios"]
            require(ident in collection and field in collection[ident], "Unresolved token " + name)
            return str(collection[ident][field])
        if name in ("editor", "runner"):
            require(name in context.get("editor_paths", {}), "Missing " + name + " executable")
            return str(context["editor_paths"][name])
        require(name in context and context[name] is not None, "Unresolved token " + name)
        return str(context[name])
    return [TOKEN.sub(value, argument) for argument in argv]


def project_path(context, relative):
    root = Path(context["snapshot_root"]).resolve()
    path = (root / relative).resolve()
    require(path.is_relative_to(root), "Manifest path escapes input root: " + str(relative))
    return path


def build_kwargs(context, spec):
    kwargs = dict(spec["kwargs"])
    for field in ("bundle", "level", "input_replay"):
        if field in kwargs and kwargs[field] is not None:
            kwargs[field] = project_path(context, kwargs[field])
    if spec.get("prepared_replay"):
        prepared = context["prepared_scenarios"][spec["prepared_replay"]]
        verify_prepared(prepared)
        kwargs["input_replay"] = Path(prepared["replay_path"])
    return kwargs


def base_result(spec):
    return dict(id=spec["id"], required=spec["required"], classification=spec["classification"],
                status="incomplete", passed=False, accepted=False, reason=None, evidence=[])


def persist_result(context, spec, result):
    path = Path(context["artifacts_dir"]) / "nodes" / spec["id"] / "result.json"
    result["result_path"] = str(path.resolve())
    write_json(path, result)
    return result


def skipped_result(context, spec, reason):
    result = base_result(spec)
    result.update(status="skipped", reason=reason)
    return persist_result(context, spec, result)


def classify_unittest(tests_run, failures, errors, skips, spec):
    def allowed(row, category):
        # The terminal exception must match. A known error used as the cause
        # of a new exception must not mask that new regression.
        terminal = row["message"].rstrip().splitlines()[-1] if row["message"].strip() else ""
        return any(rule["id"] == row["id"] and re.fullmatch(rule["message_pattern"], terminal)
                   for rule in spec.get(category, []))
    problems = [dict(row, known=allowed(row, "known_failures")) for row in failures + errors]
    skipped = [dict(row, declared=allowed(row, "allowed_skips")) for row in skips]
    unknown = [row for row in problems if not row["known"]]
    unexpected_skips = [row for row in skipped if not row["declared"]]
    status = "failed" if unknown or tests_run == 0 else "incomplete" if unexpected_skips else "known_failure" if problems else "passed"
    return dict(status=status, passed=status == "passed", accepted=status in ("passed", "known_failure"),
                tests_run=tests_run, failures=problems, skipped_tests=skipped,
                tested_passes=max(0, tests_run-len({row.get("testcase", row["id"]) for row in problems})-len(skipped)),
                reason="Unlisted test failure" if unknown else "Undeclared test skip" if unexpected_skips else
                       "No tests discovered" if tests_run == 0 else "Exact declared fixture failure retained" if problems else None)


class StructuredTestResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.failure_rows, self.error_rows, self.skip_rows = [], [], []

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self.failure_rows.append(dict(id=test.id(), message=self._exc_info_to_string(err, test)))

    def addError(self, test, err):
        super().addError(test, err)
        self.error_rows.append(dict(id=test.id(), message=self._exc_info_to_string(err, test)))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self.skip_rows.append(dict(id=test.id(), message=reason))

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            row = dict(id=subtest.id(), testcase=test.id(), message=self._exc_info_to_string(err, test))
            (self.failure_rows if issubclass(err[0], test.failureException) else self.error_rows).append(row)

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self.failure_rows.append(dict(id=test.id(), message=self._exc_info_to_string(err, test)))

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self.failure_rows.append(dict(id=test.id(), message="Unexpected unittest success; manifest expectation needs review"))


def run_unittest(spec, output):
    # Direct script execution adds tools/ to sys.path. Keeping it would load
    # both compile_level and tools.compile_level, giving identical exception
    # definitions distinct identities and bypassing package-qualified mocks.
    # Match the canonical namespace of python -m unittest discover.
    scripts = (ROOT / "tools").resolve()
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != scripts]
    suite = unittest.defaultTestLoader.discover(str(ROOT / spec.get("start_directory", "tests")),
                                              pattern=spec.get("pattern", "test_*.py"))
    result = unittest.TextTestRunner(verbosity=2, resultclass=StructuredTestResult).run(suite)
    report = classify_unittest(result.testsRun, result.failure_rows, result.error_rows, result.skip_rows, spec)
    write_json(output, report)
    return 0 if report["accepted"] else 1


def parse_json_stdout(raw):
    """Tools may print subprocess progress before their final JSON document."""
    decoder = json.JSONDecoder()
    for match in reversed(list(re.finditer(r"(?m)^\s*\{", raw))):
        try:
            document, end = decoder.raw_decode(raw[match.start():].lstrip())
            if not raw[match.start():].lstrip()[end:].strip() and isinstance(document, dict):
                return document
        except ValueError:
            continue
    raise ValueError("Command did not emit a final JSON result")


def command(context, spec, argv, cwd=None):
    try:
        from emulator_lease import OwnedProcessJob
    except ImportError:
        from tools.emulator_lease import OwnedProcessJob
    directory = Path(context["artifacts_dir"]) / "nodes" / spec["id"]
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "command.log"
    timeout = spec.get("timeout_seconds", 1800)
    require(type(timeout) in (int, float) and math.isfinite(timeout) and timeout > 0, "Invalid command timeout")
    started = time.monotonic()
    child_env = dict(os.environ)
    child_env.pop("PYTHONOPTIMIZE", None)
    child_env["PYTHONUTF8"] = "1"
    with log.open("w", encoding="utf-8") as stream, OwnedProcessJob() as job:
        try:
            process = job.spawn([str(a) for a in argv], cwd=cwd or context["snapshot_root"], env=child_env,
                                stdout=stream, stderr=subprocess.STDOUT)
            code, error = process.wait(timeout=timeout), None
        except subprocess.TimeoutExpired:
            code, error = None, "Command timeout; retained output cannot establish acceptance"
        except OSError as problem:
            code, error = None, str(problem)
    return dict(argv=[str(a) for a in argv], exit_code=code, error=error, log=evidence(log),
                elapsed_seconds=time.monotonic()-started)


def run_check(context, spec):
    result = base_result(spec)
    try:
        directory = Path(context["artifacts_dir"]) / "nodes" / spec["id"]
        directory.mkdir(parents=True, exist_ok=True)
        parser = spec.get("parser", {})
        if spec["kind"] == "python_unittest":
            spec_path = write_json(directory / "spec.json", spec)
            parsed_path = directory / "unittest.json"
            argv = [context.get("python", sys.executable), "-m", "tools.validation_scenarios",
                    "unittest", "--spec", str(spec_path), "--output", str(parsed_path)]
        else:
            argv = resolve_argv(context, spec["argv"])
            parsed_path = Path(resolve_argv(context, [parser["path"]])[0]) if parser.get("path") else None
        # Each run requires new evidence: an old success JSON cannot survive a failed command.
        if parsed_path is not None and parsed_path.exists():
            parsed_path.unlink()
        invocation = command(context, spec, argv, project_path(context, spec.get("cwd", ".")))
        result["invocation"] = invocation
        result["evidence"].append(invocation["log"])
        if spec["kind"] == "python_unittest" or parser.get("kind") == "json_file":
            require(parsed_path.is_file(), "Command lacks its newly written JSON evidence")
            parsed = json.loads(parsed_path.read_bytes())
            result["evidence"].append(evidence(parsed_path))
        elif parser.get("kind") == "stdout_json":
            parsed = parse_json_stdout(Path(invocation["log"]["path"]).read_text(encoding="utf-8", errors="replace"))
            if parsed.get("result"):
                saved = Path(parsed["result"]).resolve()
                require(saved.is_relative_to(Path(context["snapshot_root"]).resolve()), "Saved command result lies outside input project")
                parsed = json.loads(saved.read_bytes())
                result["evidence"].append(evidence(saved))
            elif parsed.get("private_root") or parsed.get("root"):
                saved = Path(parsed.get("private_root", parsed.get("root"))) / "result.json"
                require(saved.resolve().is_relative_to(Path(context["snapshot_root"]).resolve()), "Private result lies outside input project")
                require(saved.is_file(), "Missing owned verifier result")
                result["evidence"].append(evidence(saved))
        else:
            parsed = dict(passed=invocation["exit_code"] == 0)
        result["result"] = parsed
        if spec["kind"] == "python_unittest":
            result.update({key: parsed[key] for key in ("status", "passed", "accepted", "reason")})
            if invocation["error"] or invocation["exit_code"] != (0 if parsed["accepted"] else 1):
                result.update(status="incomplete", passed=False, accepted=False,
                              reason=invocation["error"] or "Unittest process did not finish with its declared result")
        else:
            passed = parsed.get("passed") is True and invocation["exit_code"] == 0 and invocation["error"] is None
            result.update(status="passed" if passed else "incomplete" if invocation["exit_code"] is None else "failed",
                          passed=passed, accepted=passed, reason=invocation["error"] or parsed.get("error"))
    except (OSError, ValueError, KeyError, TypeError) as error:
        result.update(status="incomplete" if result.get("invocation", {}).get("exit_code") is None else "failed", reason=str(error))
    return persist_result(context, spec, result)


def prepare_scenario(context, spec):
    """Cook once and bind a route to the exact package, for Native and ROM."""
    if spec.get("prepared_replay"):
        prepared = context["prepared_scenarios"][spec["prepared_replay"]]
        verify_prepared(prepared)
        return prepared
    require(spec.get("mission") and spec.get("route"), "Replay preparation needs mission and route")
    try:
        from compile_mission import prepare_stream_level
        from input_replay import load
        from verify_stream_replay_parity import scenario
    except ImportError:
        from tools.compile_mission import prepare_stream_level
        from tools.input_replay import load
        from tools.verify_stream_replay_parity import scenario
    directory = Path(context["artifacts_dir"]) / "replays" / spec["id"]
    directory.mkdir(parents=True, exist_ok=True)
    route_path = project_path(context, spec["route"])
    route = json.loads(route_path.read_bytes())
    require(isinstance(route.get("expectations"), list) and route["expectations"], "Acceptance replay must declare data expectations")
    prepare_stream_level(project_path(context, spec["mission"]), directory / "c", project_path(context, "content"))
    package_path = directory / "c/generated/play.json"
    package = json.loads(package_path.read_bytes())
    replay_path = write_json(directory / "input.json", scenario(package, route))
    load(replay_path)
    package_file = Path(package["stream"]["package_path"])
    require(sha256(package_file) == package["stream"]["package_sha256"], "Cooked DLM package identity mismatch")
    prepared = dict(directory=str(directory.resolve()), package_path=str(package_path.resolve()), replay_path=str(replay_path.resolve()),
                    native_states_path=str((directory / "native-states.json").resolve()), fingerprint=package["fingerprint"],
                    package_sha256=package["stream"]["package_sha256"], package_json_sha256=sha256(package_path),
                    replay_sha256=sha256(replay_path), route_path=str(route_path), route_sha256=sha256(route_path),
                    mission_path=str(project_path(context, spec["mission"])), mission_sha256=sha256(project_path(context, spec["mission"])))
    write_json(directory / "prepared.json", prepared)
    context.setdefault("prepared_scenarios", {})[spec["id"]] = prepared
    return prepared


def verify_prepared(prepared):
    for path, digest in (("package_path", "package_json_sha256"), ("replay_path", "replay_sha256"),
                         ("route_path", "route_sha256"), ("mission_path", "mission_sha256")):
        require(sha256(prepared[path]) == prepared[digest], "Prepared replay input is stale: " + path)
    package = json.loads(Path(prepared["package_path"]).read_bytes())
    require(package["fingerprint"] == prepared["fingerprint"] and
            sha256(package["stream"]["package_path"]) == prepared["package_sha256"], "Prepared package is stale")


def field_value(row, path):
    value = row
    for key in path.split("."):
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def relation(value, op, expected):
    if op == "eq":
        # ROM replay flags use JSON integers 0/1; Native uses booleans.
        # Match this declared wire representation without truthy coercion.
        if type(value) is bool or type(expected) is bool:
            return (type(value) in (bool, int) and type(expected) in (bool, int) and
                    value in (0, 1) and expected in (0, 1) and value == expected)
        return type(value) is type(expected) and value == expected or type(value) in (int, float) and type(expected) in (int, float) and value == expected
    if op == "ne":
        return not relation(value, "eq", expected)
    if op in ("gt", "gte", "lt", "lte"):
        require(type(value) in (int, float) and type(expected) in (int, float), "Numeric expectation needs numeric values")
        return {"gt": value > expected, "gte": value >= expected, "lt": value < expected, "lte": value <= expected}[op]
    raise ValueError("Unknown expectation relation: " + op)


def evaluate_expectations(states, replay, expectations):
    require(len(states) == len(replay["inputs"]) and states, "Replay trace incomplete")
    require(all(row["tick"] == index+1 for index, row in enumerate(states)), "Replay ticks are discontinuous")
    results = []
    ids = set()
    for rule in expectations:
        ident = rule.get("id")
        require(isinstance(ident, str) and ID.fullmatch(ident) and ident not in ids, "Invalid/duplicate expectation ID")
        ids.add(ident)
        selector = rule.get("select", "all")
        selected = states
        if selector == "final":
            selected = states[-1:]
        elif selector == "first":
            selected = states[:1]
        elif selector == "after_match_until_input":
            match = rule["start_match"]
            first = next((i for i, row in enumerate(states) if relation(field_value(row, match["field"]), match["op"], match["value"])), None)
            require(first is not None, ident + ": start match missing")
            last = next((i for i in range(first+1, len(states)) if replay["inputs"][i].get(rule["until_input"]) is True), None)
            require(last is not None, ident + ": terminal input missing")
            selected = states[first:last]
        else:
            require(selector == "all", ident + ": unknown selection")
        values = [field_value(row, rule["field"]) for row in selected]
        operation = rule["op"]
        if operation == "contains_all":
            passed = all(any(relation(value, "eq", expected) for value in values) for expected in rule["values"])
        elif operation == "min_changes":
            passed = sum(a != b for a, b in zip(values, values[1:])) >= rule["value"]
        elif operation == "constant":
            passed = all(value == values[0] for value in values)
        elif operation == "any_gt":
            passed = any(relation(value, "gt", rule["value"]) for value in values)
        else:
            passed = all(relation(value, operation, rule["value"]) for value in values)
        results.append(dict(id=ident, passed=passed, samples=len(values), select=selector, field=rule["field"], op=operation))
    require(all(row["passed"] for row in results), "Replay expectation failed: " + ", ".join(row["id"] for row in results if not row["passed"]))
    return results


def verify_build_artifact(artifact):
    require(sha256(artifact["rom"]) == artifact["rom_sha256"], "ROM differs from archived build identity")
    metadata = json.loads(Path(artifact["metadata"]).read_bytes())
    require(metadata["signature"] == artifact["build_id"] and metadata["rom_sha256"] == artifact["rom_sha256"], "Archived ROM/build metadata disagree")
    debug_map = json.loads(Path(artifact["map"]).read_bytes())
    require(debug_map["build_id"] == artifact["build_id"] and debug_map["rom_sha256"] == artifact["rom_sha256"], "Archived runtime map identity mismatch")
    return metadata


def exercise_scenario(context, spec, marker):
    try:
        from smoke_ares import exercise, AresRunError
    except ImportError:
        from tools.smoke_ares import exercise, AresRunError
    artifact = context["build_artifacts"][spec["build_id"]]
    metadata = verify_build_artifact(artifact)
    exe = Path(context.get("ares") or os.environ.get("ARES_EXE") or (Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))
    run = None
    try:
        run = exercise(exe, Path(context["settings"]), Path(artifact["rom"]), spec["id"], marker,
                       spec.get("timeout_seconds", 420), artifact["rom_sha256"], spec.get("memory_mib", 8),
                       lease_owner="validation:" + spec["id"], provenance=dict(scenario=spec["id"], build_id=artifact["build_id"],
                       rom_sha256=artifact["rom_sha256"], manifest_sha256=context.get("manifest_sha256")))
    except AresRunError as error:
        run = error.result
    require(run is not None, "Missing emulator evidence")
    log = Path(run["log"])
    if not log.is_absolute():
        log = Path(context["snapshot_root"]) / log
    write_json(log.parent / "verification-build.json", metadata)
    write_json(log.parent / "verification-run.json", run)
    return run, log, metadata


def run_evidence(context, run):
    rows = []
    for key in ("log", "stderr_log", "run_status", "tested_rom", "settings"):
        if run.get(key):
            path = Path(run[key])
            if not path.is_absolute():
                path = Path(context["snapshot_root"]) / path
            require(path.is_file(), "Missing retained emulator evidence: " + key)
            rows.append(dict(evidence(path), role=key))
    return rows


def generic_replay(context, spec, result):
    try:
        from verify_stream_replay_parity import decode_log, functional_state, compare, NUMERIC_TOLERANCE
    except ImportError:
        from tools.verify_stream_replay_parity import decode_log, functional_state, compare, NUMERIC_TOLERANCE
    prepared = prepare_scenario(context, spec) if not spec.get("prepared_replay") and spec["id"] not in context.get("prepared_scenarios", {}) else context["prepared_scenarios"][spec.get("prepared_replay", spec["id"])]
    verify_prepared(prepared)
    replay = json.loads(Path(prepared["replay_path"]).read_bytes())
    route = json.loads(Path(prepared["route_path"]).read_bytes())
    result.update(prepared=prepared, timing_accepted=False, numeric_tolerance=NUMERIC_TOLERANCE,
                  stream_boundary=replay["stream_boundary"], limitations=["Fixed 60Hz input and settled boundaries certify functional state; IO latency, viewport rendering, audio and frame pacing are separate checks."])
    native_path = Path(prepared["native_states_path"])
    if spec["adapter"] == "native":
        invocation = command(context, spec, [context["editor_paths"]["runner"], prepared["package_path"], prepared["replay_path"], "--states"])
        result["invocation"] = invocation
        result["evidence"].append(invocation["log"])
        require(invocation["exit_code"] == 0 and not invocation["error"], invocation["error"] or "Native runner failed")
        raw = Path(invocation["log"]["path"]).read_text(encoding="utf-8")
        document = json.loads(raw)
        write_json(native_path, dict(fingerprint=prepared["fingerprint"], replay_sha256=prepared["replay_sha256"], states=document["states"]))
        result["evidence"].append(evidence(native_path))
        native = document["states"]
        result["expectations"] = evaluate_expectations(native, replay, route["expectations"])
        result.update(native_passed=True, rom_parity_passed=False, ticks=len(native))
    else:
        require(native_path.is_file(), "Native baseline missing for ROM comparison")
        baseline = json.loads(native_path.read_bytes())
        require(baseline["fingerprint"] == prepared["fingerprint"] and baseline["replay_sha256"] == prepared["replay_sha256"], "Native baseline belongs to a different replay")
        native = baseline["states"]
        build = verify_build_artifact(context["build_artifacts"][spec["build_id"]])
        catalog = build.get("level_catalog", {}).get("levels", [])
        require(any(row.get("content", {}).get("mission", {}).get("sha256") == prepared["package_sha256"] for row in catalog), "ROM catalog lacks the prepared replay package")
        run, log, _ = exercise_scenario(context, spec, "DL64 input_replay_done")
        result["run"] = run
        result["build_binding"] = {key: context["build_artifacts"][spec["build_id"]][key] for key in ("build_id", "rom_sha256", "metadata", "map")}
        result["evidence"].extend(run_evidence(context, run))
        require(run["passed"], run.get("error", "Incomplete emulator replay"))
        console = decode_log(log.read_text(errors="replace"), prepared["fingerprint"], replay["stream_boundary"])
        result["expectations"] = evaluate_expectations(console, replay, route["expectations"])
        require(len(console) == len(native), "ROM and Native replay lengths differ")
        for first, second in zip(native, console):
            compare(functional_state(first), functional_state(second), "tick " + str(first["tick"]))
        result.update(native_passed=True, rom_parity_passed=True, ticks=len(native))
    result["evidence"].extend(evidence(path) for path in (prepared["replay_path"], prepared["package_path"], prepared["route_path"]))
    if spec["adapter"] == "rom":
        result["evidence"].append(evidence(native_path))
    result.update(status="passed", passed=True, accepted=True)


def lifecycle_report(context, spec, text, directory):
    # Existing authored-start and exact lifecycle heap assertions, unchanged.
    require(__debug__, "Legacy assertion-based lifecycle parser must run without Python optimization")
    try:
        from smoke_bundle import fields, starts, check_start, heap_stability
        from compile_bundle import read_manifest
        from capture_ares import decode_captures
    except ImportError:
        # smoke_bundle intentionally retains script-local imports.
        sys.path.insert(0, str(Path(context["snapshot_root"]) / "tools"))
        from smoke_bundle import fields, starts, check_start, heap_stability
        from compile_bundle import read_manifest
        from capture_ares import decode_captures
    _, entries = read_manifest(project_path(context, "content/level_bundle.json"))
    data = {row["id"]: json.loads(row["source"].read_bytes()) for row in entries}
    expected = {(ident, preset): [] for ident, level in data.items() for preset in
                ["default"] + [row["id"] for row in level.get("test_starts", [])]}
    for row in starts(text):
        key = row["id"], row["preset"]
        require(key in expected, "Unexpected authored start " + str(key))
        check_start(row, data[key[0]], key[1])
        expected[key].append(int(row["heap"].split("/")[0]))
    steady = {"/".join(key): heap_stability(values, key) for key, values in expected.items()}
    opens = [fields(line) for line in text.splitlines() if line.startswith("DL64 menu_open ")]
    resumes = [fields(line) for line in text.splitlines() if line.startswith("DL64 menu_resume ")]
    require(len(resumes) == len(expected)*3, "Every authored variant must exercise resume")
    for row in resumes:
        require(any(all(first.get(key) == row.get(key) for key in ("id", "preset", "elapsed", "player", "door", "complete", "caught")) for first in opens), "Resume changed paused mission state")
    frame = decode_captures(text, expected_count=1, expected_dimensions=(320, 240))[1]
    from PIL import Image
    image = directory / "level-select.png"
    Image.frombytes("RGB", frame[:2], frame[2]).save(image)
    return dict(passed=True, variants=len(expected), cycles=3, starts=sum(map(len, expected.values())),
                steady_heap_bytes=steady, heap_samples={"/".join(key): value for key, value in expected.items()}, capture=evidence(image))


def legacy_route(context, spec, result):
    adapter = spec["adapter"]
    markers = dict(menu_idle="DL64 profile_end window=1", expansion_gate="boot_failed reason=expansion_pak",
                   menu_lifecycle="DL64 menu_test_complete", district_functional="DL64 bafford_district_test_complete",
                   district_strict="DL64 bafford_district_test_complete", district_capture="DL64 bafford_district_capture_complete")
    run, log, _ = exercise_scenario(context, spec, markers[adapter])
    result.update(run=run, adapter=adapter, legacy=True,
                  build_binding={key: context["build_artifacts"][spec["build_id"]][key] for key in ("build_id", "rom_sha256", "metadata", "map")})
    result["evidence"].extend(run_evidence(context, run))
    result["evidence"].extend(evidence(path) for path in (log.parent / "verification-run.json", log.parent / "verification-build.json"))
    directory = Path(context["artifacts_dir"]) / "nodes" / spec["id"]
    directory.mkdir(parents=True, exist_ok=True)
    if adapter.startswith("district"):
        output = directory / "legacy-report.json"
        tool = "verify_bafford_district_performance.py" if adapter == "district_strict" else "verify_bafford_district.py"
        argv = [context.get("python", sys.executable), str(project_path(context, "tools/" + tool)), "--rom",
                context["build_artifacts"][spec["build_id"]]["rom"], "--log", str(log), "--output", str(output)]
        if adapter == "district_capture":
            argv.append("--capture")
        invocation = command(context, spec, argv)
        result["invocation"] = invocation
        result["evidence"].append(invocation["log"])
        require(output.is_file(), "Legacy parser did not retain a structured result")
        parsed = json.loads(output.read_bytes())
        for capture in parsed.get("captures", []):
            source = Path(capture["image"])
            require(sha256(source) == capture["sha256"], "Capture changed after decoding")
            archived = directory / "captures" / source.name
            archived.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, archived)
            capture["image"] = str(archived.resolve())
            result["evidence"].append(evidence(archived))
        if parsed.get("captures"):
            write_json(output, parsed)
        result["evidence"].append(evidence(output))
        complete = parsed.get("complete", run["passed"])
        passed = parsed.get("passed") is True and run["passed"] and invocation["exit_code"] == 0
        result.update(result=parsed, status="passed" if passed else "incomplete" if not complete else "failed",
                      passed=passed, accepted=passed, reason=parsed.get("error") or run.get("error") or "; ".join(parsed.get("failures", [])) or None,
                      timing_accepted=adapter == "district_strict" and passed)
    else:
        require(run["passed"], run.get("error", "Incomplete lifecycle run"))
        text = log.read_text(errors="replace")
        if adapter == "menu_idle":
            require("mode=menu" in text and "DL64 level_start " not in text, "Ordinary menu initialized gameplay")
            parsed = dict(passed=True, menu_only=True)
        elif adapter == "expansion_gate":
            require("boot_failed reason=expansion_pak" in text and "DL64 level_start " not in text, "4 MiB gate initialized gameplay")
            parsed = dict(passed=True, memory_mib=4, gate_checked=True)
        else:
            parsed = lifecycle_report(context, spec, text, directory)
        result.update(result=parsed, status="passed", passed=True, accepted=True, timing_accepted=False)


def run_scenario(context, spec):
    result = base_result(spec)
    try:
        if spec["kind"] == "generic_replay":
            generic_replay(context, spec, result)
        elif spec["kind"] == "legacy_route":
            legacy_route(context, spec, result)
        else:
            raise ValueError("Unknown scenario kind")
    except (OSError, ValueError, KeyError, TypeError, AssertionError, RuntimeError) as error:
        result.update(status="incomplete" if result.get("run", {}).get("passed") is False else
                      "stale" if "stale" in str(error).lower() or "identity" in str(error).lower() else "failed", reason=str(error))
    return persist_result(context, spec, result)


def main(argv=None):
    sys.path.insert(0, str(ROOT))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("unittest", "prepare", "check", "scenario"))
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    spec = json.loads(args.spec.read_bytes())
    if args.operation == "unittest":
        return run_unittest(spec, args.output)
    require(args.context is not None, "Operation requires context")
    context = json.loads(args.context.read_bytes())
    operation = {"prepare": prepare_scenario, "check": run_check, "scenario": run_scenario}[args.operation]
    try:
        result = operation(context, spec)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        result = dict(id=spec["id"], status="failed", passed=False, accepted=False, reason=str(error))
    write_json(args.output, result)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if args.operation == "prepare" and "replay_path" in result or result.get("accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
