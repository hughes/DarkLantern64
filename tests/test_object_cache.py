"""Exercise actual GCC dependencies, object equivalence and failed publication."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from tools import object_cache
from tools.build import build_tag_name, fingerprint, sdk_link_inputs

ROOT = Path(__file__).resolve().parents[1]


class DependencyParsing(unittest.TestCase):
    def test_continuations_and_make_escaped_names(self):
        paths = object_cache.parse_dependencies("dl64_object: source.c dir/a\\ b.h \\\n dir/hash\\#name.h dir/dollar$$name.h\n", ROOT)
        self.assertEqual({path.name for path in paths}, {"source.c", "a b.h", "hash#name.h", "dollar$name.h"})

    def test_empty_and_invalid_rules_reject(self):
        for text in ("", "source.c header.h", "object:"):
            with self.assertRaises(ValueError):
                object_cache.parse_dependencies(text, ROOT)

    def test_build_tag_cannot_escape_output_directory(self):
        self.assertEqual(build_tag_name("p-normal"), "DarkLantern64-p-normal")
        for tag in ("", ".", "../x", "a/b", "a\\b", "a b", "c:drive", "unicode-\u00e9", "x" * 25, None):
            with self.assertRaises(ValueError):
                build_tag_name(tag)


class ActualObjectCaching(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fallback = next((path for path in ("C:/msys64/ucrt64/bin/gcc.exe", "C:/ProgramData/mingw64/mingw64/bin/gcc.exe") if Path(path).is_file()), None)
        cls.compiler = Path(shutil.which("gcc") or fallback or "gcc").resolve()
        if not cls.compiler.is_file():
            raise unittest.SkipTest("Host GCC unavailable")
        cls.env = dict(os.environ, PATH=str(cls.compiler.parent) + os.pathsep + os.environ.get("PATH", ""))
        cls.identity = object_cache.compiler_identity(cls.compiler, env=cls.env, cwd=ROOT)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="object-cache-", dir=ROOT / "build")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "sdk").mkdir()
        (self.root / "sdk/sdk_value.h").write_text("#define SDK_VALUE 3\n")
        (self.root / "a.h").write_text("#define A_VALUE 5\n")
        (self.root / "asset.inc").write_text("static const int asset_value=7;\n")
        (self.root / "a.c").write_text('#include "a.h"\n#include "asset.inc"\n#include <sdk_value.h>\n#include <stdint.h>\nint a(void){return A_VALUE+asset_value+SDK_VALUE;}\n')
        (self.root / "b.c").write_text("int b(void){return 11;}\n")
        self.flags = ["-std=c17", "-O2", "-g", "-Wall", "-Wextra", "-Werror", "-I" + str(self.root), "-isystem", str(self.root / "sdk")]
        self.cache = object_cache.ObjectCache(self.root / "cache", self.compiler, self.identity, cwd=self.root, env=self.env)

    def compile(self, name="a", *, flags=None, clean=False, cache=None):
        return (cache or self.cache).compile(self.root / (name + ".c"), self.root / (name + ".o"),
                                             self.flags if flags is None else flags, clean=clean)

    def test_warm_cache_and_clean_objects_are_byte_identical(self):
        first = self.compile()
        self.assertEqual(first["status"], "rebuilt")
        self.assertEqual(self.compile()["status"], "reused")
        stored = {path: path.read_bytes() for path in (self.root / "cache/entries").glob("*.json")}
        clean = self.compile(clean=True)
        self.assertEqual(clean["object_sha256"], first["object_sha256"])
        self.assertEqual(clean["reasons"], ["clean_build_requested"])
        self.assertEqual(stored, {path: path.read_bytes() for path in stored})
        self.assertEqual(self.compile()["status"], "reused")

    def test_isolated_c_change_only_rebuilds_that_source(self):
        self.compile("a"); self.compile("b")
        (self.root / "a.c").write_text((self.root / "a.c").read_text().replace("asset_value+", "asset_value+1+"))
        self.assertEqual(self.compile("a")["status"], "rebuilt")
        self.assertEqual(self.compile("b")["status"], "reused")

    def test_implicit_link_archive_invalidates_recipe_without_rebuilding_c(self):
        target = self.root / "fake-sdk/mips64-elf/lib"
        runtime = self.root / "fake-sdk/lib/gcc/target/version"
        target.mkdir(parents=True); runtime.mkdir(parents=True)
        (target / "libc.a").write_bytes(b"target C archive")
        (runtime / "libgcc.a").write_bytes(b"compiler support archive")
        (runtime / "crtbegin.o").write_bytes(b"startup object")
        inputs = sdk_link_inputs(self.root / "fake-sdk")
        self.assertEqual({p.name for p in inputs}, {"libc.a", "libgcc.a", "crtbegin.o"})
        before = fingerprint(inputs, {"link": "fixture"})
        self.compile()
        (runtime / "libgcc.a").write_bytes(b"changed compiler support archive")
        self.assertNotEqual(fingerprint(inputs, {"link": "fixture"}), before)
        self.assertEqual(self.compile()["status"], "reused")

    def test_identity_binding_change_does_not_rebuild_consumers(self):
        header = self.root / "identity.h"
        header.write_text("extern const char fixture_build_id[];\n#define BUILD_ID fixture_build_id\n")
        (self.root / "a.c").write_text("const char *identity(void){return BUILD_ID;}\n")
        (self.root / "binding.c").write_text('const char fixture_build_id[]="first";\n')
        flags = [*self.flags, "-include", str(header)]
        self.compile("a", flags=flags); self.compile("binding")
        (self.root / "binding.c").write_text('const char fixture_build_id[]="second";\n')
        self.assertEqual(self.compile("binding")["status"], "rebuilt")
        self.assertEqual(self.compile("a", flags=flags)["status"], "reused")

    def test_header_generated_asset_and_sdk_change_actual_dependents(self):
        first = self.compile("a"); self.compile("b")
        dependencies = {Path(row["path"]).name for row in first["dependency_hashes"]}
        self.assertTrue({"a.c", "a.h", "asset.inc", "sdk_value.h", "stdint.h"} <= dependencies)
        for name, text in (("a.h", "#define A_VALUE 17\n"), ("asset.inc", "static const int asset_value=23;\n"),
                           ("sdk/sdk_value.h", "#define SDK_VALUE 29\n")):
            with self.subTest(dependency=name):
                (self.root / name).write_text(text)
                result = self.compile("a")
                self.assertEqual(result["status"], "rebuilt")
                self.assertTrue(any(reason.startswith("dependency_changed:") for reason in result["reasons"]))
                self.assertEqual(self.compile("b")["status"], "reused")

    def test_new_shadow_header_invalidates_existing_dependency_set(self):
        early, late = self.root / "early", self.root / "late"
        early.mkdir(); late.mkdir()
        (late / "chosen.h").write_text("#define CHOSEN 3\n")
        (self.root / "a.c").write_text("#include <chosen.h>\nint a(void){return CHOSEN;}\n")
        flags = [*self.flags, "-I" + str(early), "-I" + str(late)]
        self.compile(flags=flags)
        (early / "chosen.h").write_text("#define CHOSEN 19\n")
        result = self.compile(flags=flags)
        self.assertEqual(result["status"], "rebuilt")
        self.assertTrue(any(reason.startswith("dependency_added:") for reason in result["reasons"]))
        self.assertTrue(any(reason.startswith("dependency_removed:") for reason in result["reasons"]))

    def test_optimization_and_compiler_identity_change_invalidate(self):
        self.compile()
        changed = self.compile(flags=[*self.flags, "-O3"])
        self.assertIn("flags_changed", changed["reasons"])
        identity = copy.deepcopy(self.identity)
        identity["specs_sha256"] = "f" * 64
        cache = object_cache.ObjectCache(self.root / "cache", self.compiler, identity, cwd=self.root, env=self.env)
        self.assertIn("compiler_or_toolchain_changed", self.compile(cache=cache)["reasons"])

    def test_missing_output_restores_but_corrupt_cached_blob_recompiles(self):
        first = self.compile()
        (self.root / "a.o").unlink()
        restored = self.compile()
        self.assertEqual(restored["status"], "reused")
        self.assertTrue(restored["restored_object"])
        (self.root / "cache/blobs" / (first["object_sha256"] + ".o")).write_bytes(b"corrupt")
        result = self.compile()
        self.assertEqual(result["status"], "rebuilt")
        self.assertIn("cached_object_missing_or_changed", result["reasons"])

    def test_failed_compile_preserves_old_cache_and_retains_errors(self):
        first = self.compile()
        entry = Path(first["cache_entry"]).read_bytes()
        (self.root / "a.c").write_text("int a(void){this is invalid C;}\n")
        with self.assertRaises(object_cache.CompileFailure) as raised:
            self.compile()
        self.assertEqual(object_cache.digest(self.root / "a.o"), first["object_sha256"])
        self.assertEqual(Path(first["cache_entry"]).read_bytes(), entry)
        report = raised.exception.report
        self.assertEqual(report["status"], "failed")
        self.assertTrue(Path(report["compile_log"]).read_text())
        self.assertEqual(json.loads((Path(report["attempt"]) / "report.json").read_text())["status"], "failed")

    def test_input_change_between_discovery_and_compile_aborts_publication(self):
        first = self.compile()
        discovery = self.cache.discover(self.root / "a.c", self.flags)
        (self.root / "a.h").write_text("#define A_VALUE 99\n")
        with self.assertRaises(RuntimeError):
            self.cache.compile(self.root / "a.c", self.root / "a.o", self.flags, discovery=discovery)
        self.assertEqual(object_cache.digest(self.root / "a.o"), first["object_sha256"])

    def test_linked_clean_and_cached_outputs_are_equivalent(self):
        self.compile("a"); self.compile("b")
        (self.root / "main.c").write_text("int a(void);int b(void);int main(void){return a()+b()!=26;}\n")
        self.compile("main")
        link_flags = ["-Wl,--no-insert-timestamp"] if os.name == "nt" else []
        executable = self.root / ("fixture.exe" if os.name == "nt" else "fixture")
        command = [str(self.compiler), *link_flags, *[str(self.root / (name + ".o")) for name in ("a", "b", "main")], "-o", str(executable)]
        subprocess.run(command, check=True, cwd=self.root, env=self.env, capture_output=True)
        initial = executable.read_bytes()
        for name in ("a", "b", "main"):
            self.compile(name, clean=True)
        subprocess.run(command, check=True, cwd=self.root, env=self.env, capture_output=True)
        self.assertEqual(executable.read_bytes(), initial)
        subprocess.run([str(executable)], check=True, env=self.env)


if __name__ == "__main__":
    unittest.main()
