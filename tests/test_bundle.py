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
        (ROOT / "build").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="bundle-test-", dir=ROOT / "build")
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
        report = bundle.union_assets([first, second, third], self.output)["textures"]
        self.assertEqual((len(report["textures"]), report["decoded_bytes"], report["maximum_level_decoded_bytes"]), (2, 4096, 2048))
        report = bundle.union_assets([first], self.output)["textures"]
        self.assertEqual(len(list((self.output / "romfs").rglob("*.sprite"))), 1)
        self.assertEqual((self.root / "third" / third["textures"]["textures"][0]["sprite_path"]).read_bytes(), b"unique")
        bundle.union_assets([], self.output)
        self.assertEqual(list((self.output / "romfs").iterdir()), [])

    def test_texture_hash_collision_rejected_before_replacing_package(self):
        first = self.texture_level("first", b"first")
        second = self.texture_level("second", b"second")
        bundle.union_assets([first], self.output)
        with self.assertRaisesRegex(ValueError, "path collision"):
            bundle.union_assets([first, second], self.output)
        self.assertEqual((self.output / first["textures"]["textures"][0]["sprite_path"]).read_bytes(), b"first")
        second["textures"]["textures"][0]["sprite_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            bundle.union_assets([second], self.output)

    def test_texture_union_counts_shared_palettes_once_and_active_level_totals(self):
        first = self.texture_level("indexed-one", b"palette-sprite")
        second = self.texture_level("indexed-two", b"palette-sprite")
        third = self.texture_level("rgba", b"rgba-sprite", "texture-fedcba9876543210")
        for level in (first, second):
            level["textures"]["textures"][0].update(format="CI4", palette_bytes=32, decoded_total_bytes=2080,
                                                  width=64, height=64)
        report = bundle.union_assets([first, second, third], self.output)["textures"]
        self.assertEqual((report["decoded_bytes"], report["palette_bytes"], report["decoded_total_bytes"]), (4096, 32, 4128))
        self.assertEqual((report["maximum_level_decoded_bytes"], report["maximum_level_palette_bytes"],
                          report["maximum_level_decoded_total_bytes"]), (2048, 32, 2080))

    def test_incompatible_diagnostics_fail_before_cooking(self):
        cases = [{"bundle": ROOT / "content/level_bundle.json", "level": ROOT / "content/first_room.json"},
                 {"bundle": ROOT / "content/level_bundle.json", "capture": True},
                 {"start_preset": "default", "autoplay": True},
                 {"start_level": "first_room"}, {"menu_test": True},
                 {"stream_test_capture": True}, {"stream_test": True},
                 {"bundle": ROOT / "content/level_bundle.json", "stream_test": True, "menu_test": True},
                 {"bundle": ROOT / "content/level_bundle.json", "stream_test": True, "start_level": "streaming-probe"},
                 {"bafford_stream_test_capture": True}, {"bafford_stream_test": True},
                 {"bundle": ROOT / "content/level_bundle.json", "bafford_stream_test": True, "stream_test": True},
                 {"bundle": ROOT / "content/level_bundle.json", "bafford_stream_test": True, "start_level": "bafford-stream"},
                 {"bafford_expanded_test_capture": True}, {"bafford_expanded_test": True},
                 {"bundle": ROOT / "content/level_bundle.json", "bafford_expanded_test": True, "bafford_stream_test": True},
                 {"lighting_bake_verify": True, "disable_lighting_bake": True}]
        with patch.object(build, "prepare_bundle", side_effect=AssertionError("Must reject before cook")):
            for kwargs in cases:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    build.build_rom(self.root / "missing-sdk", **kwargs)

    def lighting_level(self, name, payload, signature="a" * 64):
        cooked = self.root / name
        relative = f"romfs/lighting/lighting-{signature}.bin"
        destination = cooked / relative
        destination.parent.mkdir(parents=True)
        destination.write_bytes(payload)
        return {"cooked_dir": str(cooked), "textures": {"textures": [], "decoded_bytes": 0, "sprite_bytes": 0},
                "content": {"lighting": {"path": relative, "sha256": hashlib.sha256(payload).hexdigest(),
                                          "bytes": len(payload)}}}

    def test_lighting_only_package_deduplicates_and_removes_old_bakes(self):
        first = self.lighting_level("lit-one", b"lighting")
        second = self.lighting_level("lit-two", b"lighting")
        package = bundle.union_assets([first, second], self.output)
        self.assertEqual(package["textures"]["textures"], [])
        self.assertEqual(len(package["rom_assets"]), 1)
        self.assertEqual(package["lighting"]["bytes"], 8)
        # Removing a bake replaces the package exactly, without touching its
        # source cook. The same rule covers switching a night scene to scalar.
        bundle.union_assets([], self.output)
        self.assertEqual(list((self.output / "romfs").iterdir()), [])
        self.assertTrue((Path(first["cooked_dir"]) / first["content"]["lighting"]["path"]).exists())

    def test_lighting_corruption_and_escape_leave_previous_package_intact(self):
        first = self.lighting_level("lit-good", b"accepted")
        second = self.lighting_level("lit-bad", b"different")
        bundle.union_assets([first], self.output)
        published = self.output / first["content"]["lighting"]["path"]
        with self.assertRaisesRegex(ValueError, "path collision"):
            bundle.union_assets([first, second], self.output)
        self.assertEqual(published.read_bytes(), b"accepted")
        second["content"]["lighting"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash or size mismatch"):
            bundle.union_assets([second], self.output)
        second["content"]["lighting"]["path"] = "../outside.bin"
        with self.assertRaisesRegex(ValueError, "Invalid bundle lighting path"):
            bundle.union_assets([second], self.output)
        self.assertEqual(published.read_bytes(), b"accepted")

    def test_lighting_only_level_enables_rom_filesystem(self):
        original_compile = bundle.compile_level
        def with_lighting(*args, **kwargs):
            report = original_compile(*args, **kwargs)
            cooked = Path(args[1])
            relative = "romfs/lighting/lighting-" + "b" * 64 + ".bin"
            path = cooked / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"bake")
            report["lighting"] = {"path": relative, "bytes": 4, "sha256": hashlib.sha256(b"bake").hexdigest()}
            return report
        with patch.object(bundle, "compile_level", side_effect=with_lighting):
            report, _ = self.prepare(menu=True)
        self.assertEqual(report["textures"]["textures"], [])
        self.assertEqual(len(report["rom_assets"]), 1)
        self.assertIn(".has_rom_assets=true", (self.output / "generated/bundle.c").read_text())

    def stream_fixture(self, *, mixed=False):
        source = json.loads((ROOT / "content/streaming_probe.json").read_text())
        manifest = json.loads((ROOT / "content/streaming_probe.mission.json").read_text())
        self.write_level("streaming_probe.json", source)
        self.write_level("streaming_probe.mission.json", manifest)
        # The existing baseline fixture already supplies the legacy guard OBJ.
        self.data["levels"] = ([{"id": "one", "source": "one.json"}] if mixed else []) + [
            {"id": "streaming-probe", "mission": "streaming_probe.mission.json"}]
        return source, manifest

    def test_stream_manifest_selection_and_exclusive_source_contract(self):
        self.stream_fixture(mixed=True)
        _, entries = self.read()
        self.assertEqual(entries[1]["source"], self.content / "streaming_probe.json")
        self.assertEqual(entries[1]["mission"], self.content / "streaming_probe.mission.json")
        self.assertEqual(bundle.selection(entries, "streaming-probe", "east", menu=True), (1, 1, False))
        original = copy.deepcopy(self.data)
        for entry in ({"id": "bad", "source": "one.json", "mission": "streaming_probe.mission.json"},
                      {"id": "bad", "mission": "../streaming_probe.mission.json"},
                      {"id": "bad", "mission": "one.json"},
                      {"id": "bad", "mission": "missing.mission.json"}):
            self.data = dict(original, levels=[entry])
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.read()

    def test_stream_manifest_source_confinement_and_study_gate(self):
        source, manifest = self.stream_fixture()
        self.write_level("streaming_probe.mission.json", dict(manifest, source="../outside.json"))
        with self.assertRaisesRegex(ValueError, "inside content"):
            self.read()
        self.write_level("streaming_probe.mission.json", manifest)
        source["authoring_profile"] = "external-scale-study"
        self.write_level("streaming_probe.json", source)
        with self.assertRaisesRegex(ValueError, "authoring-only source study"):
            self.read()

    def test_stream_bundle_packages_binary_and_reports_bootstrap_residency(self):
        self.stream_fixture(mixed=True)
        report, sources = self.prepare(menu=True, start_level="streaming-probe", start_preset="east")
        streamed = report["levels"][1]["content"]
        self.assertEqual(streamed["title"], "Streaming Rooms")
        self.assertEqual(report["initial_level"], 1)
        self.assertEqual(report["initial_start"], 1)
        self.assertEqual(report["resident_bootstrap_geometry_bytes"], 1800)
        self.assertEqual(report["resident_geometry_bytes"], report["levels"][0]["content"]["compiled_geometry_bytes"] + 1800)
        self.assertEqual(report["stream_rom_bytes"], streamed["mission"]["bytes"])
        self.assertEqual(report["music"]["cues"], [None, None])
        self.assertEqual([asset["kind"] for asset in report["rom_assets"]], ["mission"])
        package = self.output / streamed["mission"]["path"]
        self.assertEqual(package.read_bytes()[:4], b"DLM1")
        self.assertEqual(hashlib.sha256(package.read_bytes()).hexdigest(), streamed["mission"]["sha256"])
        header = (Path(report["levels"][1]["cooked_dir"]) / "generated/demo_level.h").read_text()
        self.assertNotIn('"west-floor"', header)
        self.assertNotIn("static const DlCollider", header)
        self.assertIn(".stream=&dl_stream_definition", header)
        self.assertIn(".has_rom_assets=true", (self.output / "generated/bundle.c").read_text())
        self.assertEqual(len(sources), 3)

    def test_stream_partition_fingerprint_and_exact_asset_union(self):
        _, manifest = self.stream_fixture()
        first, sources = self.prepare(menu=True)
        first_content = first["levels"][0]["content"]
        old_path = first_content["mission"]["path"]
        signature = build.fingerprint(sources, {}, content_hashes=[first_content["source_sha256"]])
        manifest["regions"][0]["bounds"][1][1] = 4.1
        self.write_level("streaming_probe.mission.json", manifest)
        second, sources = self.prepare(menu=True)
        second_content = second["levels"][0]["content"]
        self.assertNotEqual(first_content["source_sha256"], second_content["source_sha256"])
        self.assertNotEqual(signature, build.fingerprint(sources, {}, content_hashes=[second_content["source_sha256"]]))
        self.assertFalse((self.output / old_path).exists())
        self.assertTrue((Path(first["levels"][0]["cooked_dir"]) / old_path).exists())
        self.assertEqual(len(list((self.output / "romfs/missions").iterdir())), 1)

    def test_stream_binary_corruption_and_escape_preserve_published_union(self):
        self.stream_fixture()
        report, _ = self.prepare(menu=True)
        streamed = report["levels"][0]
        mission = streamed["content"]["mission"]
        published = self.output / mission["path"]
        payload = published.read_bytes()
        cooked_path = Path(streamed["cooked_dir"]) / mission["path"]
        cooked_path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
        with self.assertRaisesRegex(ValueError, "mission hash or size mismatch"):
            bundle.union_assets([streamed], self.output)
        self.assertEqual(published.read_bytes(), payload)
        cooked_path.write_bytes(payload)
        mission["path"] = "../outside.dlm"
        with self.assertRaisesRegex(ValueError, "Invalid bundle mission path"):
            bundle.union_assets([streamed], self.output)
        self.assertEqual(published.read_bytes(), payload)

    def test_failed_directory_publish_restores_previous_asset_union(self):
        original = self.texture_level("accepted-assets", b"accepted")
        replacement = self.texture_level("replacement-assets", b"replacement")
        bundle.union_assets([original], self.output)
        published = self.output / "romfs"
        previous = {path.relative_to(published): path.read_bytes() for path in published.rglob("*") if path.is_file()}
        actual_replace = bundle.os.replace
        def fail_new_publication(source, destination):
            if Path(source).name == "romfs" and Path(source).parent.name.startswith("package-"):
                raise OSError("injected directory publication failure")
            return actual_replace(source, destination)
        with patch.object(bundle.os, "replace", side_effect=fail_new_publication), self.assertRaisesRegex(OSError, "injected directory publication"):
            bundle.union_assets([replacement], self.output)
        self.assertEqual(previous, {path.relative_to(published): path.read_bytes() for path in published.rglob("*") if path.is_file()})
        self.assertFalse(list(self.output.glob("package-*-previous")))

    @unittest.skipUnless(os.name == "nt", "Windows directory sharing retries")
    def test_windows_sharing_retry_is_bounded_filtered_and_retains_previous_package(self):
        original = self.texture_level("original-sharing", b"original")
        replacement = self.texture_level("replacement-sharing", b"replacement")
        bundle.union_assets([original], self.output)
        published = self.output / "romfs"
        actual_replace = bundle.os.replace
        calls = []
        def sharing_error(code):
            error = PermissionError("injected Windows directory lock")
            error.winerror = code
            return error
        def transient(source, destination):
            if Path(source).name == "romfs" and Path(source).parent.name.startswith("package-"):
                calls.append(source)
                if len(calls) <= 2:
                    raise sharing_error(5)
            return actual_replace(source, destination)
        with patch.object(bundle.os, "replace", side_effect=transient), patch.object(bundle.time, "sleep") as sleep:
            bundle.union_assets([replacement], self.output)
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual((published / "textures/texture-0123456789abcdef.sprite").read_bytes(), b"replacement")
        calls.clear()
        def permanent(source, destination):
            if Path(source).name == "romfs" and Path(source).parent.name.startswith("package-"):
                calls.append(source)
                raise sharing_error(5)
            return actual_replace(source, destination)
        with patch.object(bundle.os, "replace", side_effect=permanent), patch.object(bundle.time, "sleep") as sleep, self.assertRaises(PermissionError):
            bundle.union_assets([original], self.output)
        self.assertEqual(len(calls), 8)
        self.assertEqual(sleep.call_count, 7)
        self.assertEqual((published / "textures/texture-0123456789abcdef.sprite").read_bytes(), b"replacement")
        with patch.object(bundle.os, "replace", side_effect=sharing_error(87)) as replace, patch.object(bundle.time, "sleep") as sleep, self.assertRaises(PermissionError):
            bundle.replace_directory("source", "destination")
        self.assertEqual(replace.call_count, 1)
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
