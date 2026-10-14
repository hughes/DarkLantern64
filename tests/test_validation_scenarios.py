"""Fail-closed manifest/evidence contracts and data-driven replay expectations."""
import copy
import json
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import validation_scenarios as validation

ROOT = Path(__file__).resolve().parents[1]


def manifest():
    return validation.load_manifest(ROOT / "tools/acceptance_manifest.json")


class ManifestTests(unittest.TestCase):
    def test_required_categories_and_separate_variants_declared(self):
        data = manifest()
        self.assertTrue(all(row["required"] for row in data["checks"]))
        by_id = {row["id"]: row for row in data["scenarios"]}
        self.assertEqual(by_id["probe-rom-parity"]["classification"], "functional")
        self.assertEqual(by_id["district-strict"]["classification"], "strict_wall_time")
        self.assertEqual(by_id["district-views"]["classification"], "capture_only")
        self.assertNotEqual(by_id["district-strict"]["build_id"], by_id["district-views"]["build_id"])
        self.assertFalse(by_id["district-diagnostics-counterfactual"]["required"])
        self.assertFalse(data["historical_context"]["disabled_diagnostics_counterfactual_strict_accepted"])

    def test_cycle_missing_dependency_and_duplicate_are_rejected(self):
        for mutation in ("cycle", "missing", "duplicate"):
            data = manifest()
            if mutation == "cycle":
                data["checks"][0]["dependencies"] = ["portable-c"]
                data["checks"][1]["dependencies"] = ["portable-python"]
            elif mutation == "missing":
                data["checks"][0]["dependencies"] = ["nonexistent"]
            else:
                data["checks"][1]["id"] = data["checks"][0]["id"]
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validation.validate_manifest(data)

    def test_fixed_dt_cannot_be_relabelled_strict(self):
        data = manifest()
        data["scenarios"][0]["classification"] = "strict_wall_time"
        with self.assertRaisesRegex(ValueError, "functional state only"):
            validation.validate_manifest(data)

    def test_failure_allowance_requires_id_and_message(self):
        data = manifest()
        python_check = next(row for row in data["checks"] if row["id"] == "portable-python")
        del python_check["known_failures"][0]["message_pattern"]
        with self.assertRaisesRegex(ValueError, "exact ID and message"):
            validation.validate_manifest(data)

    def test_tokens_preserve_literal_code_braces_and_dont_evaluate(self):
        context = dict(snapshot_root=ROOT, python=sys.executable,
                       build_artifacts={"sample": {"rom": "C:/safe/a.z64"}}, prepared_scenarios={})
        args = validation.resolve_argv(context, ["{python}", "-c", "print({'value': 1})", "{build:sample:rom}", "$(sensitive)"])
        self.assertEqual(args[2], "print({'value': 1})")
        self.assertEqual(args[3:], ["C:/safe/a.z64", "$(sensitive)"])
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            validation.resolve_argv(context, ["{build:missing:rom}"])

    def test_project_path_cannot_escape_frozen_root(self):
        with self.assertRaisesRegex(ValueError, "escapes"):
            validation.project_path(dict(snapshot_root=ROOT), "../outside.py")


class StructuredTests(unittest.TestCase):
    def setUp(self):
        self.spec = next(row for row in manifest()["checks"] if row["id"] == "portable-python")
        self.known = self.spec["known_failures"][0]

    def test_exact_known_fixture_is_retained_without_pure_pass(self):
        row = dict(id=self.known["id"], message="Traceback...\n" + self.known["message_pattern"] + "\n")
        result = validation.classify_unittest(5, [], [row], [], self.spec)
        self.assertEqual(result["status"], "known_failure")
        self.assertTrue(result["accepted"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["tested_passes"], 4)
        self.assertTrue(result["failures"][0]["known"])

    def test_same_id_new_exception_and_chained_cause_fail(self):
        for message in ("RuntimeError: new regression", self.known["message_pattern"] + "\nChained...\nRuntimeError: new regression"):
            result = validation.classify_unittest(1, [], [dict(id=self.known["id"], message=message)], [], self.spec)
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["accepted"])

    def test_same_exception_new_id_fails(self):
        result = validation.classify_unittest(1, [], [dict(id="other.test", message=self.known["message_pattern"])], [], self.spec)
        self.assertEqual(result["status"], "failed")

    def test_undeclared_skips_and_zero_tests_never_pass(self):
        result = validation.classify_unittest(1, [], [], [dict(id=self.known["id"], message="Compiler unavailable")], self.spec)
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["accepted"])
        self.assertFalse(validation.classify_unittest(0, [], [], [], self.spec)["accepted"])

    def test_declared_optional_asset_skip_is_visible(self):
        rule = self.spec["allowed_skips"][0]
        result = validation.classify_unittest(2, [], [], [dict(id=rule["id"], message=rule["message_pattern"])], self.spec)
        self.assertTrue(result["accepted"])
        self.assertEqual(result["tested_passes"], 1)
        self.assertTrue(result["skipped_tests"][0]["declared"])
        result = validation.classify_unittest(2, [], [], [dict(id=rule["id"], message="Portable C compiler unavailable")], self.spec)
        self.assertFalse(result["accepted"])

    def test_subtest_failures_are_recorded_and_not_hidden(self):
        class Cases(unittest.TestCase):
            def runTest(self):
                for value in (1,2):
                    with self.subTest(value=value):
                        self.assertEqual(value, 0)
        result = unittest.TextTestRunner(stream=io.StringIO(), resultclass=validation.StructuredTestResult).run(unittest.TestSuite([Cases()]))
        self.assertEqual(len(result.failure_rows), 2)
        structured = validation.classify_unittest(result.testsRun, result.failure_rows, result.error_rows, result.skip_rows, self.spec)
        self.assertFalse(structured["accepted"])
        self.assertEqual(structured["tested_passes"], 0)

    def test_windows_symlink_capability_allowance_is_precise(self):
        rule = next(row for row in self.spec["allowed_skips"] if "symlink_sources" in row["id"])
        reason = r"Symlink creation unavailable: [WinError 1314] A required privilege is not held by the client: 'C:\\project\\build\\project-level-test-example-outside.json' -> 'C:\\project\\build\\project-level-test-example\\content\\linked.json'"
        self.assertTrue(validation.classify_unittest(1, [], [], [dict(id=rule["id"], message=reason)], self.spec)["accepted"])
        for ident, message in ((rule["id"], reason.replace("1314", "5")),
                               ("other.Class.test", reason),
                               (rule["id"], reason.replace("example\\\\content", "different\\\\content")),
                               (rule["id"], "Symlink creation unavailable: unknown failure")):
            with self.subTest(ident=ident, message=message):
                self.assertFalse(validation.classify_unittest(1, [], [], [dict(id=ident, message=message)], self.spec)["accepted"])

    def test_script_discovery_removes_shadowing_tools_directory(self):
        original = list(sys.path)
        self.addCleanup(lambda: setattr(sys, "path", original))
        sys.path.insert(0, str(ROOT / "tools"))
        def discover(start, pattern):
            self.assertNotIn((ROOT / "tools").resolve(), [Path(entry or ".").resolve() for entry in sys.path])
            self.assertEqual(Path(start), ROOT / "tests")
            return unittest.TestSuite([unittest.FunctionTestCase(lambda: None)])
        with tempfile.TemporaryDirectory() as temp, patch.object(unittest.defaultTestLoader, "discover", side_effect=discover):
            from tools.run_tests import execute_suite
            self.assertTrue(execute_suite(ROOT, dict(start_directory="tests", pattern="test_*.py"), stream=io.StringIO())["accepted"])

    def test_pipeline_unittest_delegates_standard_runner_without_changing_spec(self):
        from tools import run_tests
        with patch.object(run_tests, "run_tests", return_value={"accepted": True}) as run:
            self.assertEqual(validation.run_unittest(self.spec, Path("unittest.json")), 0)
        self.assertEqual(run.call_args.args, (ROOT,))
        self.assertEqual(run.call_args.kwargs["spec"], self.spec)
        self.assertEqual(run.call_args.kwargs["output"], Path("unittest.json"))


class ReplayExpectationsTests(unittest.TestCase):
    def test_rom_integer_flags_match_native_booleans_without_coercion(self):
        for value, expected in ((0, False), (1, True), (False, 0), (True, 1)):
            with self.subTest(value=value, expected=expected):
                self.assertTrue(validation.relation(value, "eq", expected))
        for value, expected in ((2, True), (-1, False), (0.0, False), (1.0, True),
                                ("0", False), (None, False), (False, 1), (True, 0)):
            with self.subTest(value=value, expected=expected):
                self.assertFalse(validation.relation(value, "eq", expected))
        with self.assertRaisesRegex(ValueError, "Numeric"):
            validation.relation(True, "gt", 0)

    def test_identical_flags_in_wire_forms_satisfy_the_same_route_rules(self):
        replay = dict(inputs=[{},{}])
        rules = [dict(id="not-caught", field="caught", op="eq", value=False),
                 dict(id="door-closed", field="door_open", select="final", op="eq", value=False)]
        native = [dict(tick=i+1, caught=False, door_open=False) for i in range(2)]
        rom = [dict(tick=i+1, caught=0, door_open=0) for i in range(2)]
        self.assertEqual(validation.evaluate_expectations(native, replay, rules),
                         validation.evaluate_expectations(rom, replay, rules))

    def test_fixture_inputs_unchanged_and_expectations_generic(self):
        route = json.loads((ROOT / "tests/fixtures/stream_probe_route.json").read_bytes())
        self.assertEqual(sum(row["ticks"] for row in route["segments"]), 1070)
        self.assertEqual(len(route["expectations"]), 8)
        self.assertEqual(route["stream_boundary"], "settled")

    def test_persistence_span_stops_at_restart_input(self):
        states = [dict(tick=i+1, loot=value, stream=dict(mask=mask)) for i, (value, mask) in enumerate(((0,1),(25,2),(25,1),(25,2),(0,1),(0,1)))]
        replay = dict(inputs=[{}, {}, {}, {}, {"restart": True}, {}])
        span = dict(select="after_match_until_input", start_match=dict(field="loot", op="gt", value=0), until_input="restart")
        rules = [dict(span, id="persists", field="loot", op="constant"),
                 dict(span, id="two-views", field="stream.mask", op="contains_all", values=[1,2]),
                 dict(id="reset", select="final", field="loot", op="eq", value=0)]
        results = validation.evaluate_expectations(states, replay, rules)
        self.assertEqual(results[0]["samples"], 3)
        states[2]["loot"] = 0
        with self.assertRaisesRegex(ValueError, "persists"):
            validation.evaluate_expectations(states, replay, rules)

    def test_incomplete_ticks_missing_match_and_bad_operator_fail(self):
        states = [dict(tick=1, value=0)]
        replay = dict(inputs=[{}])
        with self.assertRaisesRegex(ValueError, "incomplete"):
            validation.evaluate_expectations(states, dict(inputs=[{},{}]), [])
        with self.assertRaisesRegex(ValueError, "start match missing"):
            validation.evaluate_expectations(states, replay, [dict(id="span", select="after_match_until_input", start_match=dict(field="value",op="gt",value=0), until_input="restart", field="value",op="constant")])
        with self.assertRaisesRegex(ValueError, "Unknown"):
            validation.evaluate_expectations(states, replay, [dict(id="invalid",field="value",op="execute",value=0)])


class EvidenceTests(unittest.TestCase):
    def test_old_success_is_removed_when_command_does_not_write_result(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            stale = directory / "old.json"
            stale.write_text('{"passed":true}')
            context = dict(snapshot_root=ROOT, artifacts_dir=directory, python=sys.executable)
            spec = dict(id="missing-evidence", kind="command", required=True, classification="functional",
                        argv=["{python}", "-c", "print('no result')"], parser=dict(kind="json_file", path=str(stale)))
            result = validation.run_check(context, spec)
            self.assertFalse(result["accepted"])
            self.assertEqual(result["status"], "failed")
            self.assertFalse(stale.exists())
            self.assertTrue(Path(result["result_path"]).is_file())

    def test_exit_zero_with_failed_json_is_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            context = dict(snapshot_root=ROOT, artifacts_dir=Path(temp), python=sys.executable)
            spec = dict(id="false-result", kind="command", required=True, classification="functional",
                        argv=["{python}", "-c", "print('{\"passed\":false,\"error\":\"bad\"}')"], parser=dict(kind="stdout_json"))
            result = validation.run_check(context, spec)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "bad")
            self.assertEqual(len(result["evidence"][0]["sha256"]), 64)

    def test_skipped_required_node_is_explicitly_unaccepted(self):
        with tempfile.TemporaryDirectory() as temp:
            context = dict(artifacts_dir=Path(temp))
            result = validation.skipped_result(context, dict(id="not-run", required=True, classification="functional"), "--skip-rom")
            self.assertEqual(result["status"], "skipped")
            self.assertFalse(result["accepted"])
            self.assertEqual(result["reason"], "--skip-rom")

    def test_stale_archived_rom_is_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as temp:
            rom = Path(temp) / "game.z64"
            rom.write_bytes(b"new")
            with self.assertRaisesRegex(ValueError, "archived build identity"):
                validation.verify_build_artifact(dict(rom=str(rom), rom_sha256="0"*64))

    def test_final_stdout_json_with_progress_is_not_inner_object(self):
        value = validation.parse_json_stdout('Preparing...\n{\n "passed":true,\n "nested":{"value":1}\n}\n')
        self.assertTrue(value["passed"])
        self.assertEqual(value["nested"], dict(value=1))


if __name__ == "__main__":
    unittest.main()
