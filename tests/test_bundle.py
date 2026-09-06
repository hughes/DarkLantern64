"""Bundle boundaries, independent generated data, and exact texture packaging."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import compile_bundle as bundle
from tools import build
from level_fixtures import gameplay_baseline, copy_level_dependencies

ROOT = Path(__file__).resolve().parents[1]


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.content = self.root / "content"
        self.content.mkdir()
        self.manifest = self.content / "catalog.json"
        self.data = {"version": 1, "title": "Test bundle", "levels": [
            {"id": "one", "source": "one.json"}, {"id": "two", "source": "two.json"}]}
        # Texture packaging has dedicated cases below; descriptor/build tests
        # deliberately use an untextured room and require no installed SDK.
        self.level = gameplay_baseline()
        copy_level_dependencies(self.level, self.content)
        for i, entry in enumerate(self.data["levels"]):
            level = copy.deepcopy(self.level)
            level["title"] = "One" if i == 0 else "Two"
            self.write_level(entry["source"], level)
        self.output = self.root / "output"

    def write_level(self, name, data):
        (self.content / name).write_text(json.dumps(data), encoding="utf-8")

    def read(self):
        self.manifest.write_text(json.dumps(self.data), encoding="utf-8")
        return bundle.read_manifest(self.manifest, self.content)

    def test_manifest_validates_catalog_and_case_exact_choices(self):
        data, entries = self.read()
        self.assertEqual(data["title"], "Test bundle")
        self.assertEqual(bundle.selection(entries, menu=True), (0, -1, True))
        self.assertEqual(bundle.selection(entries, "two", menu=True), (1, -1, False))
        self.assertEqual(bundle.selection(entries, "one", "default", menu=True), (0, -1, False))
        for kwargs in [{"start_level": "Two"}, {"start_level": "unknown"},
                       {"start_preset": "missing"}, {"start_preset": "default", "menu": True},
                       {"start_preset": "../outside"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                bundle.selection(entries, **kwargs)

    def test_manifest_rejects_empty_duplicates_and_escape(self):
        original = copy.deepcopy(self.data)
        cases = [dict(original, version=True), dict(original, levels=[]),
                 dict(original, levels=original["levels"] * 5), dict(original, typo=1),
                 dict(original, levels=[original["levels"][0]] * 2),
                 dict(original, levels=[original["levels"][0], {"id": "other", "source": "one.json"}])]
        for source in ("../one.json", "sub/one.json", "sub\\one.json", "C:/one.json", "missing.json", "catalog.json"):
            cases.append(dict(original, levels=[{"id": "one", "source": source}]))
        for data in cases:
            with self.subTest(data=data):
                self.data = data
                with self.assertRaises(ValueError):
                    self.read()

    def prepare(self, **kwargs):
        data, entries = self.read()
        return bundle.prepare_bundle(entries, data["title"], self.output, self.root / "unused-sdk", **kwargs)

    def test_case_aliases_cannot_share_a_windows_cook_directory(self):
        self.data["levels"][0]["id"] = "GuardRoom"
        self.data["levels"][1]["id"] = "guardroom"
        with self.assertRaisesRegex(ValueError, "case-insensitive"):
            self.read()
        # The low-level API enforces the same invariant without a manifest.
        entries = [{"id": "GuardRoom", "source": self.content / "one.json"},
                   {"id": "guardroom", "source": self.content / "two.json"}]
        with self.assertRaisesRegex(ValueError, "case-insensitive"):
            bundle.prepare_bundle(entries, "Test", self.output, self.root / "unused-sdk")
        self.data["levels"][1]["id"] = "CON"
        report, _ = self.prepare()
        self.assertEqual(Path(report["levels"][0]["cooked_dir"]).name, "0-GuardRoom")
        self.assertEqual(Path(report["levels"][1]["cooked_dir"]).name, "1-CON")
        _, entries = self.read()
        with self.assertRaisesRegex(ValueError, "directories must be distinct"):
            bundle.prepare_bundle(entries, "Test", self.output, self.root / "unused-sdk",
                                  cooked_dirs=[self.output / "Cook", self.output / "cook"])

    def test_generated_units_isolate_same_header_names_and_stale_includes(self):
        report, sources = self.prepare(menu=True)
        self.assertEqual(report["resident_geometry_bytes"], 2 * report["levels"][0]["content"]["compiled_geometry_bytes"])
        self.assertEqual(len(sources), 3)
        compiler = shutil.which("gcc") or next((str(path) for path in [
            Path("C:/ProgramData/mingw64/mingw64/bin/gcc.exe"), Path("C:/msys64/ucrt64/bin/gcc.exe")]
            if path.is_file()), None)
        if not compiler:
            self.skipTest("Host GCC unavailable for generated translation-unit check")
        # A tempting stale include exists first on the global search path.
        stale = self.root / "stale"
        stale.mkdir()
        (stale / "demo_level.h").write_text('#error "wrong level header selected"\n')
        probe = self.root / "probe.c"
        probe.write_text('#include "bundle.h"\n#include <assert.h>\n#include <string.h>\n'
                         'int main(void) { assert(dl_bundle.level_count==2); assert(dl_bundle.start_in_menu); '
                         'assert(strcmp(dl_bundle.levels[0].load()->title,"One")==0); '
                         'assert(strcmp(dl_bundle.levels[1].load()->title,"Two")==0); '
                         'assert(dl_bundle.levels[0].load()!=dl_bundle.levels[1].load()); return 0; }\n')
        executable = self.root / "probe.exe"
        env = dict(os.environ, PATH=str(Path(compiler).parent) + os.pathsep + os.environ.get("PATH", ""))
        subprocess.run([compiler, "-std=c17", "-Wall", "-Wextra", "-Werror", "-I" + str(stale),
                        "-I" + str(ROOT / "src"), "-I" + str(self.output / "generated"),
                        *map(str, sources), str(probe), "-o", str(executable)], check=True, capture_output=True, text=True, env=env)
        subprocess.run([executable], check=True, capture_output=True, env=env)

    def test_selection_and_removed_levels_change_build_inputs(self):
        _, sources = self.prepare(menu=True)
        menu_signature = build.fingerprint(sources, {})
        _, sources = self.prepare(menu=True, start_level="two")
        self.assertNotEqual(menu_signature, build.fingerprint(sources, {}))
        start_signature = build.fingerprint(sources, {})
        report, sources = self.prepare(menu=True, start_level="one")
        self.assertNotEqual(start_signature, build.fingerprint(sources, {}))
        self.assertEqual(report["initial_start"], -1)
        self.data["levels"] = self.data["levels"][:1]
        report, sources = self.prepare(menu=True)
        self.assertEqual(len(sources), 2)
        self.assertEqual(len(report["levels"]), 1)
        self.assertNotIn("dl_get_level_1", (self.output / "generated/bundle.c").read_text())

    def test_named_preset_order_reaches_descriptor_and_fingerprint(self):
        level = copy.deepcopy(self.level)
        spawn = next(e for e in level["entities"] if e["kind"] == "spawn")
        level["test_starts"] = [{"id": "watch", "label": "Watch", "position": spawn["transform"]["position"],
                                 "yaw": 15, "pitch": -10, "door_open": True, "crouched": True}]
        self.write_level("two.json", level)
        report, sources = self.prepare(menu=True, start_level="two", start_preset="watch")
        self.assertEqual((report["initial_level"], report["initial_start"], report["start_in_menu"]), (1, 0, False))
        preset_signature = build.fingerprint(sources + [Path(entry["cooked_dir"]) / "generated/demo_level.h" for entry in report["levels"]], {})
        level["test_starts"][0]["yaw"] = 40
        self.write_level("two.json", level)
        report, sources = self.prepare(menu=True, start_level="two", start_preset="watch")
        self.assertNotEqual(preset_signature, build.fingerprint(sources + [Path(entry["cooked_dir"]) / "generated/demo_level.h" for entry in report["levels"]], {}))

    def texture_level(self, name, payload, ident="texture-0123456789abcdef"):
        cooked = self.root / name
        path = f"romfs/textures/{ident}.sprite"
        destination = cooked / path
        destination.parent.mkdir(parents=True)
        destination.write_bytes(payload)
        record = {"id": ident, "sprite_path": path, "sprite_sha256": hashlib.sha256(payload).hexdigest(),
                  "sprite_bytes": len(payload), "decoded_bytes": 2048, "width": 32, "height": 32}
        return {"cooked_dir": str(cooked), "textures": {"textures": [record], "decoded_bytes": 2048, "sprite_bytes": len(payload)}}

    def test_union_deduplicates_and_prunes_stale_package_files(self):
        first = self.texture_level("first", b"shared")
        second = self.texture_level("second", b"shared")
        third = self.texture_level("third", b"unique", "texture-fedcba9876543210")
        report = bundle.union_textures([first, second, third], self.output)
        self.assertEqual((len(report["textures"]), report["decoded_bytes"], report["maximum_level_decoded_bytes"]), (2, 4096, 2048))
        report = bundle.union_textures([first], self.output)
        self.assertEqual(len(list((self.output / "romfs").rglob("*.sprite"))), 1)
        self.assertEqual((self.root / "third" / third["textures"]["textures"][0]["sprite_path"]).read_bytes(), b"unique")
        bundle.union_textures([], self.output)
        self.assertEqual(list((self.output / "romfs").iterdir()), [])

    def test_texture_hash_collision_rejected_before_replacing_package(self):
        first = self.texture_level("first", b"first")
        second = self.texture_level("second", b"second")
        bundle.union_textures([first], self.output)
        with self.assertRaisesRegex(ValueError, "path collision"):
            bundle.union_textures([first, second], self.output)
        self.assertEqual((self.output / first["textures"]["textures"][0]["sprite_path"]).read_bytes(), b"first")
        second["textures"]["textures"][0]["sprite_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            bundle.union_textures([second], self.output)

    def test_incompatible_diagnostics_fail_before_cooking(self):
        cases = [{"bundle": ROOT / "content/level_bundle.json", "level": ROOT / "content/first_room.json"},
                 {"bundle": ROOT / "content/level_bundle.json", "capture": True},
                 {"start_preset": "default", "autoplay": True},
                 {"start_level": "first_room"}, {"menu_test": True}]
        with patch.object(build, "prepare_bundle", side_effect=AssertionError("Must reject before cook")):
            for kwargs in cases:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    build.build_rom(self.root / "missing-sdk", **kwargs)


if __name__ == "__main__":
    unittest.main()
