"""Run the declared acceptance graph against one frozen, source-bound input set."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.source_snapshot import FrozenInputs, SourceChanged, project_run_lock, sha256
from tools import validation_scenarios as scenarios

STARTUP_HASHES = {str(path): sha256(path) for path in
                 (Path(__file__).resolve(), Path(scenarios.__file__).resolve(),
                  Path(sys.modules[FrozenInputs.__module__].__file__).resolve())}
ENVIRONMENT_KEYS = {"PATH", "DL64_HOST_CC", "MSYS2_BASH", "ARES_EXE", "CFLAGS", "CPPFLAGS", "CXXFLAGS",
                    "ASFLAGS", "RSPASFLAGS", "LDFLAGS", "D", "CCACHE", "MAKEFLAGS", "MFLAGS",
                    "GCC_EXEC_PREFIX", "COMPILER_PATH", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH",
                    "LIBRARY_PATH", "SOURCE_DATE_EPOCH"}


def execution_environment():
    return {key: value for key, value in os.environ.items() if key in ENVIRONMENT_KEYS or key.startswith("N64_")}


def assert_execution_inputs(environment):
    if execution_environment() != environment:
        raise SourceChanged("Compiler or validation execution environment changed")
    if any(sha256(path) != checksum for path, checksum in STARTUP_HASHES.items()):
        raise SourceChanged("Pipeline implementation changed after its interpreter loaded")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        stage.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + "\n", encoding="utf-8")
        # Windows can briefly deny a replace while another replace/reader is
        # releasing the destination. Each writer still owns its staged bytes.
        for attempt in range(20):
            try:
                os.replace(stage, path)
                break
            except PermissionError as error:
                if os.name != "nt" or getattr(error, "winerror", None) not in (5, 32, 33) or attempt == 19:
                    raise
                time.sleep(.01)
    finally:
        stage.unlink(missing_ok=True)
    return str(path.resolve())


def ordered_nodes(manifest):
    nodes = {spec["id"]: (section, spec) for section in ("checks", "builds", "scenarios") for spec in manifest[section]}
    result, visited = [], set()
    def visit(ident):
        if ident in visited:
            return
        section, spec = nodes[ident]
        for dependency in spec["dependencies"]:
            visit(dependency)
        visited.add(ident)
        result.append((section, spec))
    for ident in nodes:
        visit(ident)
    return result


def accepted_result(row):
    return row.get("accepted") is True and ((row.get("status") == "passed" and row.get("passed") is True) or
                                            (row.get("status") == "known_failure" and row.get("passed") is False))


def summarize(manifest, results, profile, *, stale=False):
    nodes = [spec for section in ("checks", "builds", "scenarios") for spec in manifest[section]]
    required = {row["id"] for row in nodes if row["required"]}
    by_id = {row["id"]: row for row in results}
    def accepted(ident):
        return accepted_result(by_id.get(ident, {}))
    full = not stale and bool(required) and all(accepted(ident) for ident in required)
    selected = {row["id"] for row in nodes if profile in row["profiles"] and row["required"]}
    selected_ok = not stale and bool(selected) and all(accepted(ident) for ident in selected)
    groups = {key: [row["id"] for row in results if row["status"] == status] for key, status in
              (("new_failures", "failed"), ("known_fixture_failures", "known_failure"),
               ("skips", "skipped"), ("incomplete", "incomplete"), ("stale_evidence", "stale"))}
    return dict(full_acceptance=full, selected_scope_accepted=selected_ok,
                status="stale" if stale else "accepted_with_known_fixture_failures" if full and groups["known_fixture_failures"] else
                "accepted" if full else "scope_passed" if selected_ok else "failed_or_incomplete",
                **groups)


def input_dependencies(sdk, ares, settings, bazel, *, rom):
    paths = [Path(sys.executable)]
    node = shutil.which("node")
    if node:
        paths.append(Path(node))
    proof_modules = ROOT / ".dev/tla-precheck/node_modules"
    if proof_modules.is_dir():
        paths.append(proof_modules)
    compiler = Path(shutil.which("gcc") or "C:/msys64/ucrt64/bin/gcc.exe")
    override = os.environ.get("DL64_HOST_CC")
    baker = Path((shutil.which(override) or override) if override else compiler)
    for host_compiler in {compiler.resolve(), baker.resolve()}:
        if host_compiler.is_file():
            prefix = host_compiler.parent.parent
            paths += [host_compiler.parent, prefix / "include", prefix / "lib", prefix / "x86_64-w64-mingw32"]
    if bazel:
        paths.append(Path(bazel))
    if rom:
        sdk = Path(sdk)
        paths += [sdk / name for name in ("bin", "include", "mips64-elf/include", "mips64-elf/lib", "lib/gcc", "libexec/gcc")]
        prefix = Path(os.environ.get("N64_GCCPREFIX", str(sdk)))
        target = os.environ.get("N64_TARGET", "mips64-elf")
        paths += [prefix / "bin", prefix / "lib/gcc", prefix / "libexec/gcc", sdk / target / "include", sdk / target / "lib"]
        bash = Path(os.environ.get("MSYS2_BASH", "C:/msys64/usr/bin/bash.exe"))
        paths += [bash, bash.parent / "make.exe", bash.parent / "cygpath.exe", *bash.parent.glob("*.dll")]
        paths += [Path(ares), Path(settings)]
    return paths


def build_tag(ident):
    """Stable short names also keep generated SDK paths below MAX_PATH."""
    return "p-" + ident if len(ident) <= 22 else "p-" + ident[:13] + "-" + hashlib.sha256(ident.encode()).hexdigest()[:8]


def archive_build(context, spec, metadata_path):
    metadata_path = Path(metadata_path).resolve()
    metadata = json.loads(metadata_path.read_bytes())
    root = Path(context["snapshot_root"])
    rom = (root / metadata["rom"]).resolve()
    mapped = (root / metadata["runtime_debug_map"]).resolve()
    if sha256(rom) != metadata["rom_sha256"] or sha256(mapped) != metadata["runtime_debug_map_sha256"]:
        raise SourceChanged("Build artifacts differ from their declared identity")
    directory = Path(context["artifacts_dir"]) / "builds" / spec["id"]
    directory.mkdir(parents=True, exist_ok=False)
    saved_rom, saved_meta, saved_map = directory / rom.name, directory / "build.json", directory / "runtime_debug_map.json"
    for source, target in ((rom, saved_rom), (metadata_path, saved_meta), (mapped, saved_map)):
        shutil.copyfile(source, target)
    bindings = []
    for level in metadata["level_catalog"]["levels"]:
        content = level["content"]
        source = Path(level["source"]).resolve()
        if not source.is_relative_to(root):
            raise SourceChanged("Compiled source is outside frozen project")
        mission = content.get("mission")
        row = dict(level=level["id"], source=str(source), source_file_sha256=sha256(source),
                   compiled_source_sha256=content["source_sha256"])
        if mission:
            partition = Path(level["mission_manifest"]).resolve()
            if not partition.is_relative_to(root) or sha256(partition) != content["mission_manifest_sha256"]:
                raise SourceChanged("Compiled partition differs from frozen source")
            package = mission["report"]
            if row["source_file_sha256"] != package["source_file_sha256"]:
                raise SourceChanged("Compiled mission source differs from frozen source")
            row.update(partition=str(partition), partition_sha256=content["mission_manifest_sha256"],
                       package_sha256=package["package_sha256"])
        bindings.append(row)
    artifact = dict(rom=str(saved_rom), metadata=str(saved_meta), map=str(saved_map),
                    build_id=metadata["signature"], rom_sha256=metadata["rom_sha256"],
                    metadata_sha256=sha256(saved_meta), map_sha256=sha256(saved_map),
                    original_metadata=str(metadata_path), source_bindings=bindings,
                    pipeline_source_sha256=context["source_sha256"])
    context["build_artifacts"][spec["id"]] = artifact
    return artifact


def run_build(context, spec):
    node = dict(spec, classification="build")
    result = scenarios.base_result(node)
    directory = Path(context["artifacts_dir"]) / "nodes" / spec["id"]
    directory.mkdir(parents=True, exist_ok=True)
    kwargs = scenarios.build_kwargs(context, spec)
    kwargs["build_tag"] = build_tag(spec["id"])
    invocation_file = directory / "build-request.json"
    write_json(invocation_file, dict(sdk=str(context["sdk"]), kwargs=kwargs))
    # The child imports frozen project code. No shell or dynamically evaluated manifest code.
    invocation = scenarios.command(context, node,
        [context["python"], str(ROOT / "tools/validate_project.py"), "--build-request", str(invocation_file)])
    result["invocation"] = invocation
    result["evidence"].append(invocation["log"])
    if invocation["exit_code"] != 0 or invocation["error"]:
        result.update(status="incomplete" if invocation["exit_code"] is None else "failed", reason=invocation["error"] or "ROM build failed; attempt logs retained")
    else:
        parsed = scenarios.parse_json_stdout(Path(invocation["log"]["path"]).read_text(encoding="utf-8", errors="replace"))
        artifact = archive_build(context, spec, parsed["metadata"])
        result.update(status="passed", passed=True, accepted=True, artifact=artifact)
        result["evidence"] += [scenarios.evidence(artifact[key]) for key in ("rom", "metadata", "map")]
    return scenarios.persist_result(context, node, result)


def bind_host(context, guard):
    directory = Path(context["artifacts_dir"]) / "host"
    directory.mkdir(parents=True, exist_ok=True)
    bindings = {}
    for key, path in context["editor_paths"].items():
        path = Path(path).resolve()
        guard.lock([path])
        target = directory / path.name
        shutil.copyfile(path, target)
        if sha256(path) != sha256(target):
            raise SourceChanged("Host executable changed while archiving")
        guard.lock([target])
        bindings[key] = dict(original=str(path), executable=str(target), sha256=sha256(target),
                             pipeline_source_sha256=context["source_sha256"])
        context["editor_paths"][key] = str(target)
    context["host_artifacts"] = bindings


def run_pipeline(manifest_path, *, root=ROOT, profile="all", output=None, sdk=Path("C:/n64-toolchain"),
                 settings=None, ares=None, include_optional=False, host_audio_none=False,
                 skip_editor=False, skip_rom=False, execute=None):
    root, manifest_path = Path(root).resolve(), Path(manifest_path).resolve()
    if not manifest_path.is_relative_to(root):
        raise ValueError("Validation manifest must be inside its declared input project")
    manifest_bytes = manifest_path.read_bytes()
    manifest = scenarios.validate_manifest(json.loads(manifest_bytes))
    run = root / ".dev/v5" / uuid.uuid4().hex[:12]
    artifacts = run / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=False)
    output = Path(output or root / "build/validation-summary.json").resolve()
    if output == manifest_path or (output.is_relative_to(root) and not (output.is_relative_to(root / "build") or output.is_relative_to(root / ".dev"))):
        raise ValueError("Summary output inside the project must be under build or .dev, outside validation inputs")
    sdk = Path(sdk).resolve()
    settings = Path(settings or root / ".dev/ares/settings-8mb.bml").resolve()
    ares = Path(ares or os.environ.get("ARES_EXE", str(Path(os.environ.get("LOCALAPPDATA", "")) / "ares/ares.exe"))).resolve()
    bazel = shutil.which("bazelisk") or shutil.which("bazel")
    context = dict(snapshot_root=str(root), artifacts_dir=str(artifacts), sdk=str(sdk), settings=str(settings),
                   ares=str(ares), python=sys.executable, bazel=bazel, prepared_scenarios={}, build_artifacts={},
                   editor_paths={"editor": str(root / "editor/bazel-bin/darklantern64_project_editor.exe"),
                                 "runner": str(root / "editor/bazel-bin/play_runner.exe")},
                   manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest())
    summary = dict(schema_version=1, run_id=run.name, manifest=str(manifest_path), manifest_sha256=context["manifest_sha256"],
                   profile=profile, results=[], full_acceptance=False, selected_scope_accepted=False, status="queued",
                   limitations=["Ares target timing is emulated; original N64 hardware is not certified.",
                                "Functional settled replay, strict wall-time acceptance and framebuffer captures are separate evidence classes."])
    summary_path = run / "summary.json"
    summary["summary_path"] = str(summary_path)
    write_json(summary_path, summary)
    environment = {key: os.environ.get(key) for key in ("TEMP", "TMP", "PYTHONPYCACHEPREFIX", "PYTHONOPTIMIZE", "GIT_OPTIONAL_LOCKS")}
    frozen_environment = execution_environment()
    run_lock = project_run_lock(root)
    owns_lock = False
    try:
        run_lock.__enter__()
        owns_lock = True
        summary["status"] = "running"
        write_json(summary_path, summary)
        # A queued run cannot overwrite the active publisher's output.
        write_json(output, summary)
        assert_execution_inputs(frozen_environment)
        if host_audio_none and profile == "all" and not skip_rom:
            from tools.prepare_ares import set_value
            raw = settings.read_bytes()
            lines = raw.decode("utf-8-sig").splitlines()
            set_value(lines, "Audio/Driver", "None")
            own_profile = artifacts / "settings-source.bml"
            own_profile.write_text("\n".join(lines) + "\n", encoding="utf-8")
            summary["host_audio"] = dict(output="disabled", original_settings_sha256=hashlib.sha256(raw).hexdigest(),
                                        owned_settings_sha256=sha256(own_profile), scope="Owned emulator host output; guest audio still measured")
            context["settings"] = str(own_profile)
        dependencies = input_dependencies(sdk, ares, context["settings"], bazel, rom=profile == "all" and not skip_rom)
        dependencies.append(manifest_path)
        with FrozenInputs(root, run / "inputs", dependencies=dependencies) as frozen:
            if sha256(manifest_path) != context["manifest_sha256"]:
                raise SourceChanged("Acceptance declaration changed while freezing inputs")
            saved_manifest = artifacts / "acceptance-manifest.json"
            shutil.copyfile(manifest_path, saved_manifest)
            summary["manifest_artifact"] = scenarios.evidence(saved_manifest)
            summary["inputs"] = frozen.identity
            summary["inputs"]["execution_environment"] = frozen_environment
            summary["inputs"]["pipeline_implementation_sha256"] = STARTUP_HASHES
            context["source_sha256"] = frozen.identity["source_sha256"]
            write_json(summary_path, summary)
            temp = artifacts / "temp"
            temp.mkdir()
            os.environ.update(TEMP=str(temp), TMP=str(temp), PYTHONPYCACHEPREFIX=str(artifacts / "pycache"))
            os.environ["GIT_OPTIONAL_LOCKS"] = "0"
            os.environ.pop("PYTHONOPTIMIZE", None)
            results = {}
            for section, spec in ordered_nodes(manifest):
                assert_execution_inputs(frozen_environment)
                frozen.assert_current()
                reason = None
                if profile not in spec["profiles"]:
                    reason = "Not selected by declared profile; does not establish full acceptance"
                elif not spec["required"] and not include_optional:
                    reason = "Optional comparison not selected"
                elif skip_rom and (section == "builds" or spec.get("build_id")):
                    reason = "ROM evidence explicitly skipped"
                elif skip_editor and (spec["classification"] == "editor" if "classification" in spec else False):
                    reason = "Editor evidence explicitly skipped"
                elif any(not accepted_result(results.get(dep, {})) for dep in spec["dependencies"]):
                    reason = "Dependency did not produce accepted evidence"
                node = dict(spec, classification=spec.get("classification", "build"))
                if reason:
                    result = scenarios.skipped_result(context, node, reason)
                else:
                    print(f"[validation] {spec['id']}", flush=True)
                    try:
                        result = execute(context, section, spec) if execute else run_build(context, spec) if section == "builds" else scenarios.run_check(context, spec) if section == "checks" else scenarios.run_scenario(context, spec)
                        if spec["id"] == "host-build" and accepted_result(result):
                            bind_host(context, frozen.guard)
                    except (OSError, ValueError, RuntimeError, KeyError) as error:
                        if isinstance(error, SourceChanged):
                            raise
                        result = scenarios.base_result(node)
                        result.update(status="failed", reason=str(error), traceback=traceback.format_exc())
                        scenarios.persist_result(context, node, result)
                result["pipeline_source_sha256"] = context["source_sha256"]
                result["manifest_sha256"] = context["manifest_sha256"]
                scenarios.persist_result(context, node, result)
                results[spec["id"]] = result
                summary["results"].append(result)
                assert_execution_inputs(frozen_environment)
                frozen.assert_current()
                summary.update(summarize(manifest, summary["results"], profile))
                summary.update(status="running", full_acceptance=False, selected_scope_accepted=False)
                write_json(summary_path, summary)
                write_json(run / "context.json", context)
            frozen.assert_archive()
            assert_execution_inputs(frozen_environment)
            frozen.assert_current()
            # Hash every linked artifact again before certifying the run.
            for result in summary["results"]:
                for item in result.get("evidence", []):
                    if item.get("sha256") and sha256(item["path"]) != item["sha256"]:
                        raise SourceChanged("Node evidence changed before publication: " + item["path"])
            if sha256(saved_manifest) != context["manifest_sha256"]:
                raise SourceChanged("Retained acceptance declaration changed before publication")
            summary.update(summarize(manifest, summary["results"], profile))
            summary["build_artifacts"] = context["build_artifacts"]
            summary["host_artifacts"] = context.get("host_artifacts", {})
            for binding in summary["host_artifacts"].values():
                if sha256(binding["executable"]) != binding["sha256"]:
                    raise SourceChanged("Retained host executable changed before publication")
            write_json(summary_path, summary)
            write_json(output, summary)
    except (Exception, KeyboardInterrupt) as error:
        stale = isinstance(error, SourceChanged)
        summary.update(error=str(error), status="stale" if stale else "incomplete", full_acceptance=False, selected_scope_accepted=False)
        if stale:
            for result in summary["results"]:
                result["observed_status"] = result["status"]
                result.update(status="stale", passed=False, accepted=False, reason="Input or artifact changed; cannot certify this result")
                scenarios.persist_result(context, result, result)
            summary.update(summarize(manifest, summary["results"], profile, stale=True))
        write_json(summary_path, summary)
        if owns_lock:
            write_json(output, summary)
    finally:
        if owns_lock:
            run_lock.__exit__(None, None, None)
        for key, value in environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "tools/acceptance_manifest.json")
    parser.add_argument("--profile", choices=("portable", "host", "all"), default="all")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sdk", type=Path, default=Path(os.environ.get("N64_INST", "C:/n64-toolchain")))
    parser.add_argument("--ares", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--host-audio-none", action="store_true", help="Disable unavailable host audio output in an owned settings copy")
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--skip-editor", action="store_true")
    parser.add_argument("--skip-rom", action="store_true")
    parser.add_argument("--build-request", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.build_request:
        request = json.loads(args.build_request.read_bytes())
        from tools.build import build_rom
        kwargs = request["kwargs"]
        for key in ("bundle", "level", "input_replay"):
            if kwargs.get(key):
                kwargs[key] = Path(kwargs[key])
        rom = build_rom(Path(request["sdk"]), **kwargs)
        print(json.dumps(dict(rom=str(rom), metadata=str(ROOT / "build" / rom.stem / "build.json"))))
        return 0
    result = run_pipeline(args.manifest, profile=args.profile, output=args.output, sdk=args.sdk,
                          settings=args.settings, ares=args.ares, include_optional=args.include_optional,
                          host_audio_none=args.host_audio_none, skip_editor=args.skip_editor, skip_rom=args.skip_rom)
    print(json.dumps({key: result.get(key) for key in ("status", "full_acceptance", "selected_scope_accepted", "summary_path", "error")}, indent=2))
    return 0 if result.get("selected_scope_accepted") else 1


if __name__ == "__main__":
    raise SystemExit(main())
