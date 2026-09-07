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
        (self.source / "src/t3d").mkdir(parents=True)
        (self.source / "src/t3d/t3d.c").write_text("pristine source\n")
        self.stage = self.root / "build/tiny3d-source"
        self.library = self.stage / "build_sdk_main/libt3d.a"
        self.patches = self.root / "tools/tiny3d/patches"
        self.bash = self.root / "msys/usr/bin/bash.exe"
        self.bash.parent.mkdir(parents=True)
        self.bash.write_bytes(b"bash")
        self.sdk = self.make_sdk("sdk-a")
        self.sdk_b = self.make_sdk("sdk-b")
        self.rebuilt = []
        self.fail_make = False
        for name, value in (("ROOT", self.root), ("SOURCE", self.source), ("LIBRARY", self.library),
                            ("BUILD_SOURCE", self.stage), ("PATCH_DIRECTORY", self.patches)):
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
            output = builder.REVISION if "rev-parse" in args else ""
            if "ls-tree" in args:
                output = "Makefile\0src/t3d/t3d.c\0"
            return subprocess.CompletedProcess(args, 0, output, "")
        self.assertEqual(args[0], self.bash)
        stale = self.library.parent / "old-object.o"
        rebuilt = not self.library.exists()
        self.rebuilt.append(rebuilt)
        if rebuilt:
            self.assertFalse(stale.exists())
            for name in builder.GENERATED_METADATA:
                self.assertFalse((self.stage / name).exists())
        if self.fail_make:
            raise subprocess.CalledProcessError(2, args, stderr="simulated compiler failure")
        self.library.parent.mkdir(parents=True, exist_ok=True)
        self.library.write_bytes(b"mock-library")
        stale.write_bytes(b"object")
        for name in builder.GENERATED_METADATA:
            path = self.stage / name
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
        self.library.parent.mkdir(parents=True)
        self.library.write_bytes(b"old library")
        (self.library.parent / "old-object.o").write_bytes(b"old object")
        for name in builder.GENERATED_METADATA:
            path = self.stage / name
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
            (self.stage / builder.GENERATED_METADATA[1]).unlink()
            self.build()
        self.assertEqual(self.rebuilt, [True, True, True, True])

    def test_generated_metadata_corruption_with_original_timestamp_rebuilds(self):
        self.build()
        for name in builder.GENERATED_METADATA:
            metadata = self.stage / name
            original = metadata.read_bytes()
            self.mutate_preserving_timestamp(metadata, b"X" + original[1:])
            self.build()
            self.assertEqual(metadata.read_bytes(), original)
        self.build()
        self.assertEqual(self.rebuilt, [True, True, True, False])

    def test_untracked_shadow_header_never_enters_pinned_stage(self):
        shadow = self.source / "src/libdragon.h"
        shadow.write_text("untracked experimental header\n")
        old_metadata = self.source / builder.GENERATED_METADATA[0]
        old_metadata.parent.mkdir(parents=True)
        old_metadata.write_text("old checkout build addresses\n")
        self.build()
        self.assertFalse((self.stage / "src/libdragon.h").exists())
        self.assertEqual((self.stage / "src/t3d/t3d.c").read_text(), "pristine source\n")
        shadow.write_text("different untracked experiment\n")
        self.build()
        self.assertEqual(self.rebuilt, [True, False])
        self.assertFalse((self.stage / "src/libdragon.h").exists())
        self.assertEqual(old_metadata.read_text(), "old checkout build addresses\n")

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
            with self.assertRaisesRegex(RuntimeError, "inside its disposable project build stage"):
                builder._invalidate_library()
        self.assertTrue(self.library.is_file())

    def test_stage_tampering_rebuilds_without_touching_pristine_checkout(self):
        self.build()
        (self.stage / "src/t3d/t3d.c").write_text("edited stage\n")
        self.build()
        self.assertEqual(self.rebuilt, [True, True])
        self.assertEqual((self.stage / "src/t3d/t3d.c").read_text(), "pristine source\n")
        self.assertEqual((self.source / "src/t3d/t3d.c").read_text(), "pristine source\n")
        self.assertFalse((self.source / builder.GENERATED_METADATA[0]).exists())

    def test_patch_change_invalidates_identity_and_cannot_escape_stage(self):
        self.build()
        self.patches.mkdir(parents=True)
        fix = self.patches / "depth.patch"
        fix.write_text("--- a/src/t3d/t3d.c\n+++ b/src/t3d/t3d.c\n")
        first = builder.library_identity(self.sdk, self.bash, {})
        fix.write_text(fix.read_text() + "# changed patch\n")
        second = builder.library_identity(self.sdk, self.bash, {})
        self.assertNotEqual(first, second)
        fix.write_text("--- a/src/../../outside.c\n+++ b/src/../../outside.c\n")
        with self.assertRaisesRegex(RuntimeError, "only change staged src/"):
            self.build()
        self.assertEqual((self.source / "src/t3d/t3d.c").read_text(), "pristine source\n")


if __name__ == "__main__":
    unittest.main()
