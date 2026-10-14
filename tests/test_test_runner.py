"""Exercise the public runner in private projects, without SDKs or GUIs."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import traceback
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tools import run_tests as runner

ROOT = Path(__file__).resolve().parents[1]


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="standard-runner-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "tools").mkdir()
        (self.root / "tests").mkdir()
        for name in ("run_tests.py", "validation_scenarios.py", "source_snapshot.py", "emulator_lease.py"):
            shutil.copyfile(ROOT / "tools" / name, self.root / "tools" / name)
        self.spec = dict(id="portable-python", kind="python_unittest", required=True, profiles=["portable"],
                         classification="portable", dependencies=[], start_directory="tests", pattern="test_*.py",
                         known_failures=[dict(id="test_cases.Cases.test_known", message_pattern="ValueError: retained fixture")],
                         allowed_skips=[dict(id="test_cases.Cases.test_optional",
                                             message_pattern="Set DL_THIEF_GOLD to verify locally installed source assets")])
        self.manifest = self.root / "tools/acceptance_manifest.json"
        self.save_manifest()

    def save_manifest(self):
        self.manifest.write_text(json.dumps(dict(schema_version=1, checks=[self.spec], builds=[], scenarios=[])), encoding="utf-8")

    def cases(self, methods):
        (self.root / "tests/test_cases.py").write_text("import unittest, os, tempfile, json, uuid, sys, time\nfrom pathlib import Path\nclass Cases(unittest.TestCase):\n" + methods, encoding="utf-8")

    def run_private(self, **kwargs):
        return runner.run_tests(self.root, **kwargs)

    def test_default_manifest_is_the_pipeline_allowlist_not_a_second_copy(self):
        spec, path = runner.portable_spec(ROOT)
        manifest = json.loads(path.read_bytes())
        expected = next(row for row in manifest["checks"] if row["id"] == "portable-python")
        self.assertEqual(spec, expected)
        self.assertEqual(len(spec["known_failures"]), 1)
        self.assertIn("sponza-collision-parapet-north", spec["known_failures"][0]["message_pattern"])

    def test_temp_utf8_and_canonical_import_namespace_with_retained_input_identity(self):
        self.cases("""    def test_environment(self):
        temporary = Path(tempfile.gettempdir())
        self.assertEqual(str(temporary), os.environ['TEMP'])
        self.assertEqual(os.environ['TMP'], os.environ['TEMP'])
        self.assertEqual(os.environ['TMPDIR'], os.environ['TEMP'])
        self.assertTrue(sys.flags.utf8_mode)
        self.assertEqual(sys.flags.optimize, 0)
        self.assertEqual(sys.stderr.encoding.lower(), 'utf-8')
        self.assertNotIn(str(Path.cwd() / 'tools'), sys.path)
        (temporary / 'unicode.txt').write_text('lantern café 🏮', encoding='utf-8')
        observation = Path.cwd() / 'build/observation.json'
        observation.parent.mkdir(exist_ok=True)
        observation.write_text(json.dumps({'temp':str(temporary),'value':(temporary/'unicode.txt').read_text(encoding='utf-8')}), encoding='utf-8')
""")
        with patch.dict(os.environ, {"PYTHONOPTIMIZE": "1"}):
            report = self.run_private()
        self.assertTrue(report["passed"])
        observed = json.loads((self.root / "build/observation.json").read_bytes())
        temporary = Path(observed["temp"])
        self.assertTrue(temporary.is_relative_to(self.root / ".dev/test-runs"))
        self.assertFalse(temporary.exists())
        self.assertEqual(observed["value"], "lantern café 🏮")
        self.assertTrue(report["runner"]["temporary_cleaned"])
        self.assertEqual(report["process_environment"]["python_utf8"], 1)
        self.assertEqual(json.loads(Path(report["report_path"]).read_bytes()), report)
        self.assertEqual(json.loads((self.root / "build/test-results/latest.json").read_bytes()), report)
        self.assertIn("tests/test_cases.py", report["inputs"]["files"])
        self.assertEqual(len(report["inputs"]["source_sha256"]), 64)
        self.assertEqual(len(report["inputs"]["manifest_sha256"]), 64)
        self.assertFalse(report["inputs"]["include_external"])
        self.assertTrue(report["selection"]["complete_declared_suite"])

    def test_exact_known_error_is_retained_but_new_error_replaces_previous_success(self):
        self.cases("    def test_known(self):\n        raise ValueError('retained fixture')\n")
        known = self.run_private()
        self.assertEqual(known["status"], "known_failure")
        self.assertTrue(known["accepted"])
        self.assertFalse(known["passed"])
        self.assertEqual(known["runner"]["exit_code"], 0)
        self.cases("    def test_known(self):\n        raise RuntimeError('new regression')\n")
        failed = self.run_private()
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["accepted"])
        self.assertEqual(failed["runner"]["exit_code"], 1)
        self.assertFalse(failed["failures"][0]["known"])
        self.assertEqual(json.loads((self.root / "build/test-results/latest.json").read_bytes())["run_id"], failed["run_id"])
        self.assertTrue(Path(known["report_path"]).is_file())

    def test_optional_local_source_absence_is_separate_from_undeclared_skip(self):
        self.cases("    def test_optional(self):\n        self.skipTest('Set DL_THIEF_GOLD to verify locally installed source assets')\n")
        report = self.run_private()
        self.assertTrue(report["accepted"])
        self.assertEqual(len(report["unavailable_local_content"]), 1)
        self.assertEqual(report["failures"], [])
        self.assertEqual(report["undeclared_skips"], [])
        self.cases("    def test_optional(self):\n        self.skipTest('Host GCC unavailable')\n")
        report = self.run_private()
        self.assertEqual(report["status"], "incomplete")
        self.assertFalse(report["accepted"])
        self.assertEqual(report["unavailable_local_content"], [])
        self.assertEqual(len(report["undeclared_skips"]), 1)
        self.assertEqual(len(report["unavailable_tools"]), 1)
        self.assertEqual(report["runner"]["exit_code"], 1)

    def test_added_source_during_test_never_certifies_mixed_version(self):
        self.cases("    def test_mutation(self):\n        (Path.cwd()/'tools/added.py').write_text('changed input',encoding='utf-8')\n")
        report = self.run_private()
        self.assertEqual(report["status"], "stale")
        self.assertFalse(report["accepted"])
        self.assertNotIn("tools/added.py", report["inputs"]["files"])
        self.assertTrue(report["runner"]["temporary_cleaned"])

    def test_manifest_mutation_and_zero_discovery_are_not_accepted(self):
        self.cases("    def test_manifest_mutation(self):\n        with (Path.cwd()/'tools/acceptance_manifest.json').open('a',encoding='utf-8') as stream: stream.write(' ')\n")
        self.assertEqual(self.run_private()["status"], "stale")
        report = self.run_private(pattern="nothing_*.py")
        self.assertFalse(report["accepted"])
        self.assertEqual(report["tests_run"], 0)
        self.assertEqual(report["selection"]["pattern"], "nothing_*.py")
        self.assertFalse(report["selection"]["complete_declared_suite"])

    def test_report_cannot_overwrite_manifest_or_test_source(self):
        self.cases("    def test_ok(self):\n        pass\n")
        before = self.manifest.read_bytes()
        for path in (self.manifest, self.root / "tests/test_cases.py", self.root / "tools/new-report.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "overwrite captured input|outside source directories"):
                self.run_private(output=path)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertFalse((self.root / ".dev/test-runs").exists())

    def test_concurrent_runs_own_distinct_temp_and_keep_complete_individual_reports(self):
        self.cases("    def test_ok(self):\n        (Path(tempfile.gettempdir())/'marker').write_text('owned',encoding='utf-8')\n")
        reports, errors = [], []
        def execute():
            try:
                reports.append(self.run_private())
            except BaseException as error:
                errors.append("".join(traceback.format_exception(type(error), error, error.__traceback__)))
        threads = [threading.Thread(target=execute) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(reports), 2)
        self.assertTrue(all(report["accepted"] for report in reports))
        self.assertNotEqual(reports[0]["runner"]["temp_path"], reports[1]["runner"]["temp_path"])
        self.assertNotEqual(reports[0]["report_path"], reports[1]["report_path"])
        for report in reports:
            self.assertEqual(json.loads(Path(report["report_path"]).read_bytes()), report)
            self.assertFalse(Path(report["runner"]["temp_path"]).exists())

    def test_timeout_retains_nonaccepted_result_and_cleans_owned_temp(self):
        self.cases("    def test_slow(self):\n        time.sleep(60)\n")
        report = self.run_private(timeout=.3)
        self.assertEqual(report["status"], "incomplete")
        self.assertFalse(report["accepted"])
        self.assertIn("TimeoutExpired", report["reason"])
        self.assertTrue(report["runner"]["temporary_cleaned"])
        self.assertTrue(Path(report["report_path"]).is_file())

    def test_cleanup_error_does_not_publish_an_accepted_success(self):
        self.cases("    def test_ok(self):\n        pass\n")
        with patch.object(runner.shutil, "rmtree", side_effect=PermissionError("fixture directory remains busy")):
            report = self.run_private()
        self.assertEqual(report["status"], "incomplete")
        self.assertFalse(report["accepted"])
        self.assertFalse(report["runner"]["temporary_cleaned"])
        self.assertIn("Cannot clean owned", report["reason"])
        self.assertFalse(json.loads((self.root / "build/test-results/latest.json").read_bytes())["accepted"])

    def directory_failure(self, target, winerror, failures):
        original = Path.mkdir
        attempts = []
        def mkdir(path, *args, **kwargs):
            if path == target:
                attempts.append(path)
                if len(attempts) <= failures:
                    error = PermissionError("injected Windows directory creation denial")
                    error.winerror = winerror
                    raise error
            return original(path, *args, **kwargs)
        return mkdir, attempts

    def test_report_directory_transient_denial_retries_before_publication(self):
        (self.root / "build").mkdir()
        output = self.root / "build/reports/result.json"
        mkdir, attempts = self.directory_failure(output.parent, 5, 2)
        with patch.object(Path, "mkdir", autospec=True, side_effect=mkdir), \
                patch.object(runner, "os", SimpleNamespace(name="nt", replace=os.replace)), \
                patch.object(runner.time, "sleep") as sleep:
            runner.write_report(output, {"passed": True, "value": "complete UTF-8 café"})
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_args_list, [unittest.mock.call(.01), unittest.mock.call(.01)])
        self.assertEqual(json.loads(output.read_bytes()), {"passed": True, "value": "complete UTF-8 café"})
        self.assertFalse(list(output.parent.glob("*.tmp")))

    def test_shared_temp_storage_creation_transient_denial_recovers(self):
        self.cases("    def test_ok(self):\n        pass\n")
        (self.root / ".dev").mkdir()
        storage = self.root / ".dev/test-runs"
        mkdir, attempts = self.directory_failure(storage, 32, 2)
        with patch.object(Path, "mkdir", autospec=True, side_effect=mkdir), \
                patch.object(runner, "os", SimpleNamespace(name="nt", replace=os.replace, environ=os.environ)):
            report = self.run_private()
        self.assertEqual(len(attempts), 3)
        self.assertTrue(report["accepted"])
        self.assertTrue(report["runner"]["temporary_cleaned"])
        self.assertFalse(list(storage.iterdir()))

    def test_directory_retry_exhaustion_or_other_error_never_writes_success(self):
        (self.root / "build").mkdir()
        for winerror, expected in ((5, 20), (1314, 1)):
            output = self.root / ("build/reports-" + str(winerror)) / "result.json"
            mkdir, attempts = self.directory_failure(output.parent, winerror, 100)
            with self.subTest(winerror=winerror), patch.object(Path, "mkdir", autospec=True, side_effect=mkdir), \
                    patch.object(runner, "os", SimpleNamespace(name="nt", replace=os.replace)), \
                    patch.object(runner.time, "sleep") as sleep, self.assertRaises(PermissionError):
                runner.write_report(output, {"passed": True})
            self.assertEqual(len(attempts), expected)
            self.assertEqual(sleep.call_count, expected-1)
            self.assertFalse(output.exists())
            self.assertFalse(list(self.root.rglob("*.tmp")))

    def test_bootstrap_prepublication_failure_cannot_reuse_old_latest_success(self):
        from tools.verify_bootstrap import read_pattern_result
        latest = self.root / "build/test-results/latest.json"
        latest.parent.mkdir(parents=True)
        previous = dict(status="passed", accepted=True, selection=dict(pattern="old_subset.py"))
        latest.write_text(json.dumps(previous), encoding="utf-8")
        result = read_pattern_result(self.root / "missing-fresh-report.json", "new_subset.py", 1)
        self.assertFalse(result["accepted"])
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("fresh pattern report", result["reason"])
        self.assertIsNone(result["report_path"])
        self.assertEqual(json.loads(latest.read_bytes()), previous)

    def test_bootstrap_requires_matching_pattern_and_consistent_command_exit(self):
        from tools.verify_bootstrap import read_pattern_result
        path = self.root / "fresh.json"
        value = dict(status="passed", accepted=True, selection=dict(pattern="expected.py"))
        path.write_text(json.dumps(value), encoding="utf-8")
        self.assertFalse(read_pattern_result(path, "other.py", 0)["accepted"])
        self.assertFalse(read_pattern_result(path, "expected.py", 1)["accepted"])
        self.assertTrue(read_pattern_result(path, "expected.py", 0)["accepted"])


if __name__ == "__main__":
    unittest.main()
