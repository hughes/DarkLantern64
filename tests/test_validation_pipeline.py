"""Acceptance publication tests with injected nodes, no SDK/GUI/emulator work."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import patch

from tools import validate_project as pipeline
from tools import validation_scenarios as scenarios
from tools.source_snapshot import sha256

ROOT = Path(__file__).resolve().parents[1]


def check(ident, *, required=True, profiles=None, dependencies=(), classification="portable"):
    return dict(id=ident, kind="command", required=required,
        profiles=profiles or ["portable", "host", "all"], dependencies=list(dependencies),
        classification=classification, argv=["fake-never-executed"], parser=dict(kind="exit_code"))


class ValidationPipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="validation-pipeline-test-", dir=ROOT / "build")
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / "project"
        for name in ("src", "content", "tools", "editor", "tests", "art"):
            (self.root / name).mkdir(parents=True)
        (self.root / "src/main.c").write_text("int main(void){return 0;}\n")
        (self.root / "content/scene.json").write_text('{"version":2}\n')
        (self.root / "dependencies.json").write_text("{}\n")
        self.dependency = self.base / "fake-compiler-input.bin"
        self.dependency.write_bytes(b"immutable fake compiler input")
        self.manifest_path = self.root / "tools/acceptance.json"
        self.manifest = dict(schema_version=1, checks=[check("portable")], builds=[], scenarios=[])
        self.calls, self.contexts = [], []
        self.save_manifest()

    def save_manifest(self, path=None):
        path = path or self.manifest_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.manifest, indent=2) + "\n")
        return path

    def passed(self, context, section, spec):
        self.calls.append(spec["id"])
        self.contexts.append(copy.deepcopy(context))
        node = dict(spec, classification=spec.get("classification", "build"))
        result = scenarios.base_result(node)
        directory = Path(context["artifacts_dir"]) / "nodes" / spec["id"]
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / "fake-evidence.txt"
        log.write_text("Fresh injected evidence for " + spec["id"] + "\n")
        result.update(status="passed", passed=True, accepted=True, evidence=[scenarios.evidence(log)])
        return result

    def run_fake(self, *, execute=None, **kwargs):
        # Missing fake dependency paths are inventoried, never executed. The
        # injected node runner is the only execution boundary exercised here.
        with patch.object(pipeline, "input_dependencies", return_value=[self.dependency]):
            return pipeline.run_pipeline(kwargs.pop("manifest_path", self.manifest_path), root=self.root,
                sdk=self.base / "fake-sdk", ares=self.base / "fake-ares.exe", settings=self.base / "fake-settings.bml",
                execute=execute or self.passed, **kwargs)

    def assert_persisted_bindings(self, summary):
        self.assertEqual(json.loads(Path(summary["summary_path"]).read_bytes()), summary)
        for result in summary["results"]:
            persisted = json.loads(Path(result["result_path"]).read_bytes())
            self.assertEqual(persisted, result)
            self.assertEqual(persisted["pipeline_source_sha256"], summary["inputs"]["source_sha256"])
            self.assertEqual(persisted["manifest_sha256"], summary["manifest_sha256"])

    def test_portable_scope_cannot_certify_required_full_graph(self):
        self.manifest["checks"].append(check("target", profiles=["all"], dependencies=["portable"], classification="strict_wall_time"))
        self.save_manifest()
        summary = self.run_fake(profile="portable")
        self.assertEqual(self.calls, ["portable"])
        self.assertTrue(summary["selected_scope_accepted"])
        self.assertFalse(summary["full_acceptance"])
        self.assertEqual(summary["status"], "scope_passed")
        self.assertEqual(summary["skips"], ["target"])
        self.assert_persisted_bindings(summary)

    def test_optional_failure_is_reported_without_overriding_required_acceptance(self):
        self.manifest["checks"].append(check("comparison", required=False, dependencies=["portable"]))
        self.save_manifest()
        unselected = self.run_fake(profile="all")
        self.assertEqual(unselected["skips"], ["comparison"])
        self.assertTrue(unselected["full_acceptance"])
        self.calls.clear()
        def optional_failure(context, section, spec):
            result = self.passed(context, section, spec)
            if spec["id"] == "comparison":
                result.update(status="failed", passed=False, accepted=False, reason="Optional counterfactual budget fails")
            return result
        selected = self.run_fake(profile="all", include_optional=True, execute=optional_failure)
        self.assertEqual(self.calls, ["portable", "comparison"])
        self.assertTrue(selected["full_acceptance"])
        self.assertEqual(selected["new_failures"], ["comparison"])
        self.assert_persisted_bindings(selected)

    def test_exact_known_fixture_failure_allows_dependency_and_is_never_hidden(self):
        known = self.manifest["checks"][0]
        known.update(kind="python_unittest", known_failures=[dict(id="fixture.exact", message_pattern="ValueError: known collision fixture")])
        self.manifest["checks"].append(check("dependent", dependencies=["portable"]))
        self.save_manifest()
        def classify(context, section, spec):
            result = self.passed(context, section, spec)
            if spec["id"] == "portable":
                result.update(scenarios.classify_unittest(5, [], [dict(id="fixture.exact", message="trace\nValueError: known collision fixture")], [], spec))
            return result
        summary = self.run_fake(execute=classify)
        self.assertTrue(summary["full_acceptance"])
        self.assertEqual(self.calls, ["portable", "dependent"])
        self.assertEqual(summary["status"], "accepted_with_known_fixture_failures")
        self.assertEqual(summary["known_fixture_failures"], ["portable"])
        self.assertFalse(summary["results"][0]["passed"])
        self.assertTrue(summary["results"][0]["failures"][0]["known"])
        self.assert_persisted_bindings(summary)

    def test_new_failure_blocks_dependent_and_full_acceptance(self):
        self.manifest["checks"].append(check("dependent", dependencies=["portable"]))
        self.save_manifest()
        def failed(context, section, spec):
            result = self.passed(context, section, spec)
            result.update(status="failed", passed=False, accepted=False, reason="New regression")
            return result
        summary = self.run_fake(execute=failed)
        self.assertEqual(self.calls, ["portable"])
        self.assertFalse(summary["full_acceptance"])
        self.assertFalse(summary["selected_scope_accepted"])
        self.assertEqual(summary["new_failures"], ["portable"])
        self.assertEqual(summary["skips"], ["dependent"])
        self.assertIn("Dependency", summary["results"][1]["reason"])
        self.assert_persisted_bindings(summary)

    def test_graph_order_crosses_declaration_sections_before_running_dependent(self):
        self.manifest["checks"] = [check("last", dependencies=["route"]), check("foundation")]
        self.manifest["builds"] = [dict(id="rom-build", kind="rom", required=True, profiles=["all"], dependencies=["foundation"], kwargs={})]
        self.manifest["scenarios"] = [dict(id="route", kind="legacy_route", adapter="district_functional", build_id="rom-build",
            required=True, profiles=["all"], dependencies=["rom-build"], classification="functional")]
        self.save_manifest()
        summary = self.run_fake()
        self.assertEqual(self.calls, ["foundation", "rom-build", "route", "last"])
        self.assertTrue(summary["full_acceptance"])
        self.assert_persisted_bindings(summary)

    def test_every_node_is_rebound_to_frozen_source_and_manifest_before_persistence(self):
        def forged_binding(context, section, spec):
            result = self.passed(context, section, spec)
            result.update(pipeline_source_sha256="wrong-input-version", manifest_sha256="wrong-declaration")
            return result
        summary = self.run_fake(execute=forged_binding)
        self.assertTrue(summary["full_acceptance"])
        self.assertEqual(summary["manifest_sha256"], sha256(self.manifest_path))
        self.assertEqual(sha256(summary["manifest_artifact"]["path"]), summary["manifest_sha256"])
        self.assert_persisted_bindings(summary)

    def test_added_source_invalidates_earlier_passes_and_stops_mixed_version_run(self):
        self.manifest["checks"] += [check("mutator", dependencies=["portable"]), check("must-not-run", dependencies=["mutator"])]
        self.save_manifest()
        def mutate(context, section, spec):
            result = self.passed(context, section, spec)
            if spec["id"] == "mutator":
                (self.root / "src/new_version.h").write_text("#define VERSION 2\n")
            return result
        summary = self.run_fake(execute=mutate)
        self.assertEqual(self.calls, ["portable", "mutator"])
        self.assertEqual(summary["status"], "stale")
        self.assertFalse(summary["full_acceptance"])
        self.assertFalse(summary["selected_scope_accepted"])
        self.assertIn("new_version.h", summary["error"])
        self.assertTrue(all(row["observed_status"] == "passed" and row["status"] == "stale" and not row["accepted"] for row in summary["results"]))
        self.assertNotIn("src/new_version.h", summary["inputs"]["files"])
        self.assert_persisted_bindings(summary)

    def test_tampered_retained_source_archive_cannot_be_certified(self):
        def tamper(context, section, spec):
            result = self.passed(context, section, spec)
            archive = Path(context["artifacts_dir"]).parent / "inputs/src/main.c"
            archive.write_text("different archived version\n")
            return result
        summary = self.run_fake(execute=tamper)
        self.assertEqual(summary["status"], "stale")
        self.assertIn("archive changed", summary["error"])
        self.assertFalse(summary["full_acceptance"])
        self.assert_persisted_bindings(summary)

    def test_tampered_node_artifact_invalidates_all_successes(self):
        self.manifest["checks"].append(check("tamper", dependencies=["portable"]))
        self.save_manifest()
        first_path = None
        def tamper(context, section, spec):
            nonlocal first_path
            result = self.passed(context, section, spec)
            if first_path is None:
                first_path = Path(result["evidence"][0]["path"])
            else:
                first_path.write_text("Replaced previous success evidence\n")
            return result
        summary = self.run_fake(execute=tamper)
        self.assertEqual(summary["status"], "stale")
        self.assertIn("evidence changed", summary["error"])
        self.assertTrue(all(not row["accepted"] for row in summary["results"]))
        self.assert_persisted_bindings(summary)

    def test_previous_green_output_replaced_before_execution_and_on_failure(self):
        output = self.root / "build/summary.json"
        accepted = self.run_fake(output=output)
        self.assertTrue(accepted["full_acceptance"])
        def failure(context, section, spec):
            current = json.loads(output.read_bytes())
            self.assertEqual(current["status"], "running")
            self.assertFalse(current["full_acceptance"])
            self.assertNotEqual(current["run_id"], accepted["run_id"])
            raise RuntimeError("Injected execution failure")
        failed = self.run_fake(output=output, execute=failure)
        self.assertFalse(failed["full_acceptance"])
        self.assertFalse(failed["selected_scope_accepted"])
        self.assertEqual(json.loads(output.read_bytes()), failed)
        self.assertIn("Injected execution failure", failed["results"][0]["reason"])
        self.assert_persisted_bindings(failed)

    def test_summary_cannot_overwrite_captured_source_or_its_own_manifest(self):
        protected = [self.root / name for name in ("src/main.c", "content/scene.json", "tools/acceptance.json",
            "editor/author.cpp", "tests/test_case.py", "art/model.obj", "dependencies.json")]
        for path in protected:
            if not path.exists():
                path.write_text("Original protected input\n")
        before = {path: path.read_bytes() for path in protected}
        for path in protected:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.run_fake(output=path)
        saved = self.save_manifest(self.root / ".dev/acceptance.json")
        saved_bytes = saved.read_bytes()
        with self.assertRaises(ValueError):
            self.run_fake(manifest_path=saved, output=saved)
        self.assertEqual(saved.read_bytes(), saved_bytes)
        self.assertEqual({path: path.read_bytes() for path in protected}, before)
        self.assertEqual(self.calls, [])

    def test_external_summary_is_allowed_without_altering_project_inputs(self):
        output = self.base / "outside-project-summary.json"
        summary = self.run_fake(output=output)
        self.assertTrue(summary["full_acceptance"])
        self.assertEqual(json.loads(output.read_bytes()), summary)
        self.assertNotIn(output.name, summary["inputs"]["files"])

    def test_empty_required_graph_does_not_establish_acceptance(self):
        empty = dict(schema_version=1, checks=[], builds=[], scenarios=[])
        summary = pipeline.summarize(empty, [], "all")
        self.assertFalse(summary["full_acceptance"])
        self.assertFalse(summary["selected_scope_accepted"])
        self.assertEqual(summary["status"], "failed_or_incomplete")
        optional = dict(schema_version=1, checks=[check("optional", required=False)], builds=[], scenarios=[])
        summary = pipeline.summarize(optional, [dict(id="optional", status="passed", passed=True, accepted=True)], "all")
        self.assertFalse(summary["full_acceptance"])
        self.assertFalse(summary["selected_scope_accepted"])

    def test_contradictory_accepted_failure_never_counts_as_acceptance(self):
        for status in ("failed", "incomplete", "skipped", "stale"):
            with self.subTest(status=status):
                summary = pipeline.summarize(self.manifest,
                    [dict(id="portable", status=status, accepted=True, passed=False)], "all")
                self.assertFalse(summary["full_acceptance"])
                self.assertFalse(summary["selected_scope_accepted"])

    def test_malformed_accepted_failure_blocks_dependent_execution(self):
        self.manifest["checks"].append(check("must-not-run", dependencies=["portable"]))
        self.save_manifest()
        def malformed(context, section, spec):
            result = self.passed(context, section, spec)
            result.update(status="failed", passed=False, accepted=True, reason="Malformed contradictory result")
            return result
        summary = self.run_fake(execute=malformed)
        self.assertEqual(self.calls, ["portable"])
        self.assertFalse(summary["full_acceptance"])
        self.assertFalse(summary["selected_scope_accepted"])
        self.assertEqual(summary["new_failures"], ["portable"])
        self.assertEqual(summary["skips"], ["must-not-run"])
        self.assertIn("Dependency", summary["results"][1]["reason"])
        self.assert_persisted_bindings(summary)

    def test_progress_never_certifies_before_final_archive_audit(self):
        observed = []
        original = pipeline.FrozenInputs.assert_archive
        output = self.root / "build/summary.json"
        def final_audit(frozen):
            private = json.loads((frozen.archive.parent / "summary.json").read_bytes())
            shared = json.loads(output.read_bytes())
            self.assertTrue(all(pipeline.accepted_result(row) for row in private["results"]))
            for current in (private, shared):
                self.assertEqual(current["status"], "running")
                self.assertFalse(current["full_acceptance"])
                self.assertFalse(current["selected_scope_accepted"])
            observed.append(private)
            # This final audit really rejects changed retained data. An interim
            # candidate must never have advertised certification beforehand.
            (frozen.archive / "src/main.c").write_text("tampered after node success\n")
            return original(frozen)
        with patch.object(pipeline.FrozenInputs, "assert_archive", final_audit):
            summary = self.run_fake(output=output)
        self.assertEqual(len(observed), 1)
        self.assertEqual(summary["status"], "stale")
        self.assertFalse(summary["full_acceptance"])
        self.assert_persisted_bindings(summary)

    def test_shared_initial_and_failure_publications_hold_project_lease(self):
        output = self.root / "build/summary.json"
        real_write, real_lock = pipeline.write_json, pipeline.project_run_lock
        publications = []
        def write(path, value):
            if Path(path).resolve() == output.resolve():
                with self.assertRaises(TimeoutError):
                    with real_lock(self.root, timeout=.001):
                        self.fail("A shared publication escaped the project lease")
                publications.append(value["status"])
            return real_write(path, value)
        def mutate(context, section, spec):
            result = self.passed(context, section, spec)
            (self.root / "src/changed_during_run.h").write_text("#define CHANGED 1\n")
            return result
        with patch.object(pipeline, "write_json", write):
            summary = self.run_fake(output=output, execute=mutate)
        self.assertEqual(publications, ["running", "stale"])
        self.assertEqual(summary["status"], "stale")
        with real_lock(self.root, timeout=.001):
            pass

    def test_concurrent_json_writers_use_distinct_atomic_staging_files(self):
        output = self.root / "build/atomic-summary.json"
        barrier = threading.Barrier(2)
        seen, gate_lock = set(), threading.Lock()
        real_replace = pipeline.os.replace
        stages, failures = [], []
        def replace(stage, target):
            stages.append(str(stage))
            with gate_lock:
                ident = threading.get_ident()
                first = ident not in seen
                seen.add(ident)
            if first:
                barrier.wait(timeout=5)
            return real_replace(stage, target)
        def writer(ident):
            try:
                pipeline.write_json(output, dict(run_id=ident, payload="complete record " + ident))
            except BaseException as error:
                failures.append(error)
        with patch.object(pipeline.os, "replace", replace):
            threads = [threading.Thread(target=writer, args=(ident,)) for ident in ("first", "second")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(len(set(stages)), 2)
        saved = json.loads(output.read_bytes())
        self.assertIn(saved["run_id"], ("first", "second"))
        self.assertEqual(saved["payload"], "complete record " + saved["run_id"])
        self.assertFalse(any(Path(stage).exists() for stage in stages))

    def test_transient_windows_sharing_failures_retry_same_staged_bytes_then_commit(self):
        output = self.root / "build/retry-summary.json"
        prior = dict(run_id="prior", payload="previous complete record")
        value = dict(run_id="current", payload="new complete record")
        pipeline.write_json(output, prior)
        real_replace = pipeline.os.replace
        attempts, observed_previous = [], []
        def replace(stage, target):
            stage = Path(stage)
            attempts.append((stage, stage.read_bytes()))
            observed_previous.append(json.loads(Path(target).read_bytes()))
            if len(attempts) <= 2:
                error = PermissionError(13, "Injected transient Windows sharing violation")
                error.winerror = 32
                raise error
            return real_replace(stage, target)
        # A narrow OS shim exercises Windows retry semantics on any test host
        # without altering os.name globally or platform path construction.
        with patch.object(pipeline, "os", SimpleNamespace(name="nt", replace=replace)), patch.object(pipeline.time, "sleep") as sleep:
            pipeline.write_json(output, value)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len({path for path, _ in attempts}), 1)
        self.assertTrue(all(json.loads(raw) == value for _, raw in attempts))
        self.assertEqual(observed_previous, [prior, prior, prior])
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(json.loads(output.read_bytes()), value)
        self.assertFalse(attempts[0][0].exists())

    def test_persistent_windows_replace_error_exhausts_retries_without_partial_commit_or_staging_leak(self):
        output = self.root / "build/persistent-summary.json"
        prior = dict(run_id="prior", payload="last complete record")
        value = dict(run_id="current", payload="never committed")
        pipeline.write_json(output, prior)
        for code, expected_attempts in ((32, 20), (1314, 1)):
            with self.subTest(winerror=code):
                attempts = []
                def replace(stage, target):
                    stage = Path(stage)
                    attempts.append((stage, stage.read_bytes()))
                    error = PermissionError(13, "Injected persistent Windows replace failure")
                    error.winerror = code
                    raise error
                with patch.object(pipeline, "os", SimpleNamespace(name="nt", replace=replace)), patch.object(pipeline.time, "sleep") as sleep:
                    with self.assertRaisesRegex(PermissionError, "persistent Windows replace failure"):
                        pipeline.write_json(output, value)
                self.assertEqual(len(attempts), expected_attempts)
                self.assertEqual(sleep.call_count, expected_attempts - 1)
                self.assertEqual(len({path for path, _ in attempts}), 1)
                self.assertTrue(all(json.loads(raw) == value for _, raw in attempts))
                self.assertEqual(json.loads(output.read_bytes()), prior)
                self.assertFalse(attempts[0][0].exists())
                self.assertEqual(list(output.parent.glob(output.name + ".*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
