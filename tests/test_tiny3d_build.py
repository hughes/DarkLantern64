"""An SDK switch must rebuild both Tiny3D objects and generated RSP metadata."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_tiny3d as builder


class Tiny3DBuildCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "external/tiny3d"
        (self.source / ".git").mkdir(parents=True)
        (self.source / "Makefile").write_text("all: lib\n")
        self.library = self.source / "build_sdk_main/libt3d.a"
        self.bash = self.root / "msys/usr/bin/bash.exe"
        self.bash.parent.mkdir(parents=True)
        self.bash.write_bytes(b"bash")
        self.sdk = self.make_sdk("sdk-a")
        self.sdk_b = self.make_sdk("sdk-b")
        self.rebuilt = []
        self.fail_make = False
        for name, value in (("ROOT", self.root), ("SOURCE", self.source), ("LIBRARY", self.library)):
            context = patch.object(builder, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(builder, "run", self.fake_run)
        context.start()
        self.addCleanup(context.stop)
        context = patch.dict(os.environ, {}, clear=True)
        context.start()
        self.addCleanup(context.stop)

    def make_sdk(self, name):
        sdk = self.root / name
        for relative, content in (("include/n64.mk", b"flags"),
                                  ("mips64-elf/include/rspq.inc", b"rsp-v1"),
                                  ("mips64-elf/lib/rsp.ld", b"link"),
                                  ("bin/mips64-elf-gcc.exe", b"gcc"),
                                  ("libexec/gcc/mips64-elf/1/cc1.exe", b"compiler-v1")):
            path = sdk / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return sdk

    def fake_run(self, args, **kwargs):
        if args[0] == "git":
            return subprocess.CompletedProcess(args, 0, builder.REVISION if "rev-parse" in args else "", "")
        self.assertEqual(args[0], self.bash)
        stale = self.library.parent / "old-object.o"
        rebuilt = not self.library.exists()
        self.rebuilt.append(rebuilt)
        if rebuilt:
            self.assertFalse(stale.exists())
            for name in builder.GENERATED_METADATA:
                self.assertFalse((self.source / name).exists())
        if self.fail_make:
            raise subprocess.CalledProcessError(2, args, stderr="simulated compiler failure")
        self.library.parent.mkdir(parents=True, exist_ok=True)
        self.library.write_bytes(b"mock-library")
        stale.write_bytes(b"object")
        for name in builder.GENERATED_METADATA:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("generated from selected SDK")
        return subprocess.CompletedProcess(args, 0, "", "")

    def build(self, sdk=None):
        return builder.build_library(sdk or self.sdk, bash=self.bash)

    def mutate_preserving_timestamp(self, path, content):
        before = path.stat()
        self.assertEqual(len(content), before.st_size)
        path.write_bytes(content)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    def test_unchanged_sdk_stays_incremental_and_switch_rebuilds_all(self):
        self.build()
        self.build()
        self.build(self.sdk_b)
        self.build(self.sdk_b)
        self.assertEqual(self.rebuilt, [True, False, True, False])
        report = json.loads((self.root / "build/tiny3d-dependency.json").read_text())
        self.assertEqual(report["build_identity"]["sdk"], str(self.sdk_b))
        self.assertFalse(report["dependency_cache_rebuilt"])

    def test_old_unstamped_cache_is_discarded_including_metadata(self):
        self.library.parent.mkdir()
        self.library.write_bytes(b"old library")
        (self.library.parent / "old-object.o").write_bytes(b"old object")
        for name in builder.GENERATED_METADATA:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("old metadata")
        self.build()
        self.assertEqual(self.rebuilt, [True])

    def test_replaced_internal_compiler_and_rsp_include_are_hashed(self):
        self.build()
        self.mutate_preserving_timestamp(self.sdk / "libexec/gcc/mips64-elf/1/cc1.exe", b"compiler-v2")
        self.build()
        self.mutate_preserving_timestamp(self.sdk / "mips64-elf/include/rspq.inc", b"rsp-v2")
        self.build()
        self.assertEqual(self.rebuilt, [True, True, True])

    def test_flags_library_corruption_and_missing_metadata_invalidate(self):
        self.build()
        with patch.dict(os.environ, {"CFLAGS": "-O1"}):
            self.build()
            self.library.write_bytes(b"tampered")
            self.build()
            (self.source / builder.GENERATED_METADATA[1]).unlink()
            self.build()
        self.assertEqual(self.rebuilt, [True, True, True, True])

    def test_failed_rebuild_does_not_certify_partial_cache(self):
        self.build()
        self.fail_make = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.build(self.sdk_b)
        self.assertFalse((self.library.parent / "dl64-build-identity.json").exists())
        self.fail_make = False
        self.build(self.sdk_b)
        self.assertEqual(self.rebuilt, [True, True, True])

    def test_output_path_guard_checks_all_targets_before_deletion(self):
        self.build()
        with patch.object(builder, "GENERATED_METADATA", ("../../outside.h",)):
            with self.assertRaisesRegex(RuntimeError, "inside its project checkout"):
                builder._invalidate_library()
        self.assertTrue(self.library.is_file())


if __name__ == "__main__":
    unittest.main()
