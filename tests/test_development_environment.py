"""Tool discovery is read-only and preserves explicit, broken settings."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import development_environment as doctor


class EnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="environment-doctor-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "dependencies.json").write_text(json.dumps({"lightengine": {
            "path": "external/LightEngine", "commit": "a" * 40}}), encoding="utf-8")

    def file(self, name):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture executable, never launched")
        return path

    def test_explicit_missing_paths_never_choose_installed_alternatives(self):
        alternative = self.file("installed/gcc.exe")
        settings = {key: "missing/" + key for key in
                    ("host_compiler", "n64_sdk", "msys2_bash", "ares", "bazel", "python", "editor", "runner")}
        with patch.object(doctor.shutil, "which", side_effect=lambda name, **kwargs: str(alternative) if name == "gcc" else None):
            result = doctor.inspect_environment(self.root, settings, probe=False)
        for key, raw in settings.items():
            with self.subTest(tool=key):
                row = result["tools"][key]
                self.assertFalse(row["available"])
                self.assertEqual(row["path"], str((self.root / raw).resolve()))
                self.assertEqual(row["source"], "configured")
                self.assertTrue(row["configured"])
                self.assertTrue(row["diagnostics"][0]["action"])
        self.assertFalse(any(result["readiness"].values()))

    def test_environment_overrides_are_authoritative_and_explicit_config_wins(self):
        names = dict(DL64_HOST_CC="absent-gcc.exe", N64_INST="absent-sdk", MSYS2_BASH="absent-bash.exe", ARES_EXE="absent-ares.exe")
        existing = self.file("valid/compiler.exe")
        with patch.dict(os.environ, names, clear=True), patch.object(doctor.shutil, "which", return_value=None):
            result = doctor.inspect_environment(self.root, {"host_compiler": str(existing)}, probe=False)
        self.assertEqual(result["tools"]["host_compiler"]["path"], str(existing))
        self.assertTrue(result["tools"]["host_compiler"]["available"])
        for key, env_key in (("n64_sdk", "N64_INST"), ("msys2_bash", "MSYS2_BASH"), ("ares", "ARES_EXE")):
            row = result["tools"][key]
            self.assertEqual(row["source"], "environment:" + env_key)
            self.assertFalse(row["available"])

    def test_configured_command_on_path_and_relative_path_with_spaces(self):
        compiler = self.file("local tools/host gcc.exe")
        with patch.object(doctor.shutil, "which", side_effect=lambda name, **kwargs: str(compiler) if name == "my-gcc" else None):
            selection = doctor.resolve_host_compiler("my-gcc", root=self.root, environ={"PATH": "fixture"})
            relative = doctor.resolve_host_compiler("local tools/host gcc.exe", root=self.root, environ={"PATH": ""})
        self.assertTrue(selection["available"])
        self.assertEqual(selection["path"], str(compiler))
        self.assertEqual(relative["path"], str(compiler))

    def test_shared_compiler_selection_uses_path_then_actual_supported_fallbacks(self):
        fallback = self.file("supported/gcc.exe")
        path_compiler = self.file("path/gcc.exe")
        with patch.object(doctor, "HOST_GCC_FALLBACKS", (str(self.root / "missing.exe"), str(fallback))), \
                patch.object(doctor.shutil, "which", return_value=None):
            result = doctor.resolve_host_compiler(root=self.root, environ={"PATH": ""})
            self.assertEqual(result["path"], str(fallback))
            self.assertEqual(result["source"], "default")
        with patch.object(doctor, "HOST_GCC_FALLBACKS", (str(fallback),)), \
                patch.object(doctor.shutil, "which", return_value=str(path_compiler)):
            result = doctor.resolve_host_compiler(root=self.root, environ={"PATH": "fixture"})
            self.assertEqual(result["path"], str(path_compiler))
            self.assertEqual(result["source"], "PATH:gcc")

    def test_sdk_requires_compiler_tools_headers_and_libraries_not_just_directory(self):
        sdk = self.root / "sdk"
        sdk.mkdir()
        result = doctor.inspect_environment(self.root, {"n64_sdk": str(sdk)}, probe=False)
        self.assertEqual(result["tools"]["n64_sdk"]["status"], "incomplete")
        self.assertIn("mips64-elf/lib/n64.ld", result["tools"]["n64_sdk"]["missing_files"])
        for name in result["tools"]["n64_sdk"]["required_files"]:
            self.file("sdk/" + name)
        result = doctor.inspect_environment(self.root, {"n64_sdk": str(sdk)}, probe=False)
        self.assertTrue(result["tools"]["n64_sdk"]["complete"])
        self.assertEqual(result["tools"]["n64_compiler"]["path"], str(sdk / "bin/mips64-elf-gcc.exe"))

    def test_probes_bounded_and_do_not_launch_ares_bazel_editor_or_modify_files(self):
        configured = {key: str(self.file("bin/" + key + ".exe")) for key in
                      ("python", "host_compiler", "msys2_bash", "ares", "bazel", "editor", "runner")}
        configured["n64_sdk"] = str(self.root / "missing-sdk")
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        def run(argv, **kwargs):
            self.assertEqual(argv[1:], ["--version"])
            self.assertLessEqual(kwargs["timeout"], 5)
            self.assertIn(argv[0], [configured[key] for key in ("python", "host_compiler", "msys2_bash")])
            return subprocess.CompletedProcess(argv, 0, "fixture version 1\n", "")
        with patch.object(doctor.subprocess, "run", side_effect=run) as probe:
            result = doctor.inspect_environment(self.root, configured)
        self.assertEqual(probe.call_count, 3)
        self.assertEqual(result["tools"]["host_compiler"]["version"], "fixture version 1")
        for key in ("ares", "bazel", "editor", "runner"):
            self.assertEqual(result["tools"][key]["probe"]["status"], "not_run")
            self.assertIsNone(result["tools"][key]["version"])
        self.assertEqual(before, {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob("*") if p.is_file()})

    def test_probe_failure_retains_file_availability_but_not_readiness(self):
        compiler = self.file("bin/gcc.exe")
        with patch.object(doctor.subprocess, "run", side_effect=subprocess.TimeoutExpired("gcc", 4)):
            result = doctor.inspect_environment(self.root, {"host_compiler": str(compiler)})
        row = result["tools"]["host_compiler"]
        self.assertTrue(row["available"])
        self.assertEqual(row["status"], "probe_failed")
        self.assertFalse(result["readiness"]["portable_c"])
        self.assertIn("DL64_HOST_CC", row["diagnostics"][0]["action"])

    def test_complete_sdk_with_failed_n64_compiler_probe_cannot_be_rom_ready(self):
        sdk = self.root / "sdk"
        sdk.mkdir()
        settings = dict(n64_sdk=str(sdk), host_compiler=str(self.file("host/gcc.exe")),
                        msys2_bash=str(self.file("msys/bash.exe")))
        report = doctor.inspect_environment(self.root, settings, probe=False)
        for name in report["tools"]["n64_sdk"]["required_files"]:
            self.file("sdk/" + name)
        (self.root / "external/tiny3d").mkdir(parents=True)
        def run(argv, **kwargs):
            if Path(argv[0]) == sdk / "bin/mips64-elf-gcc.exe":
                return subprocess.CompletedProcess(argv, 1, "", "N64 compiler cannot start")
            return subprocess.CompletedProcess(argv, 0, "fixture version 1\n", "")
        with patch.object(doctor.subprocess, "run", side_effect=run):
            failed = doctor.inspect_environment(self.root, settings)
        self.assertTrue(failed["tools"]["n64_sdk"]["complete"])
        self.assertTrue(failed["tools"]["n64_compiler"]["available"])
        self.assertEqual(failed["tools"]["n64_compiler"]["status"], "probe_failed")
        self.assertTrue(failed["readiness"]["portable_c"])
        self.assertFalse(failed["readiness"]["rom_build"])
        self.assertFalse(failed["readiness"]["rom_build_t3d"])
        present = doctor.inspect_environment(self.root, settings, probe=False)
        self.assertTrue(present["readiness"]["rom_build"])
        self.assertTrue(present["readiness"]["rom_build_t3d"])

    def test_disabled_probes_never_execute_tools(self):
        with patch.object(doctor.subprocess, "run") as run:
            result = doctor.inspect_environment(self.root, {"python": sys.executable}, probe=False)
        run.assert_not_called()
        self.assertTrue(result["tools"]["python"]["available"])
        self.assertEqual(result["tools"]["python"]["probe"]["status"], "disabled")

    def test_unsupported_python_is_present_but_not_ready(self):
        configured = {"python": sys.executable, "n64_sdk": str(self.root / "no-sdk"), "msys2_bash": str(self.root / "no-bash")}
        with patch.object(doctor.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "Python 3.10.14\n", "")):
            result = doctor.inspect_environment(self.root, configured)
        self.assertTrue(result["tools"]["python"]["available"])
        self.assertEqual(result["tools"]["python"]["status"], "unsupported")
        self.assertFalse(result["readiness"]["portable_python"])
        self.assertIn("3.11", result["tools"]["python"]["diagnostics"][0]["action"])

    def test_missing_host_override_fails_build_and_cooker_with_same_action(self):
        from tools import build, cook_lighting
        with patch.dict(os.environ, {"DL64_HOST_CC": str(self.root / "missing-gcc.exe")}), \
                patch.object(build, "compile_level"), patch.object(build, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "Set DL64_HOST_CC") as build_error:
                build.run_portable_tests()
            with self.assertRaisesRegex(RuntimeError, "Set DL64_HOST_CC") as cooker_error:
                cook_lighting.host_compiler()
        run.assert_not_called()
        self.assertEqual(str(build_error.exception), str(cooker_error.exception))

    def test_malformed_dependency_metadata_reports_failure_without_crashing(self):
        for tiny3d in ("invalid row", {"path": ["not a path"]}, {"path": "../outside"}):
            (self.root / "dependencies.json").write_text(json.dumps({"lightengine": {
                "path": "external/LightEngine", "commit": "a" * 40}, "tiny3d": tiny3d}), encoding="utf-8")
            with self.subTest(tiny3d=tiny3d):
                result = doctor.inspect_environment(self.root, probe=False)
                self.assertEqual(result["tools"]["tiny3d"]["status"], "invalid_metadata")
                self.assertTrue(result["tools"]["tiny3d"]["metadata_error"])
                self.assertFalse(result["readiness"]["rom_build_t3d"])


if __name__ == "__main__":
    unittest.main()
