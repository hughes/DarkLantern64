import copy
from pathlib import Path
import tempfile
import unittest

from tools import check_project_capabilities as capabilities


class ProjectCapabilitiesTests(unittest.TestCase):
    def setUp(self):
        self.data = capabilities.load_capabilities()
        self.temporary = tempfile.TemporaryDirectory(prefix="project-capabilities-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        paths = {ref["path"] for row in self.data["capabilities"] for ref in row["source_references"]}
        paths.update(ref["path"] for limit in self.data["limits"].values() for ref in limit["source_references"])
        paths.update(doc["path"] for doc in self.data["documentation"])
        for relative in paths:
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((capabilities.ROOT / relative).read_bytes())

    def test_all_maintained_limits_and_documents_match(self):
        self.assertEqual([], capabilities.validate(self.data))
        self.assertEqual([], capabilities.check_documents(self.data))

    def test_source_limit_drift_is_failure_and_write_cannot_certify(self):
        path = self.root / "src/content_limits.h"
        path.write_text(path.read_text().replace("DL_MAX_SCENE_VERTICES 6144", "DL_MAX_SCENE_VERTICES 6145"))
        errors = capabilities.validate(self.data, self.root)
        self.assertTrue(any("scene_vertices" in e and "6145" in e for e in errors), errors)
        self.assertEqual(6144, self.data["limits"]["scene_vertices"]["value"])

    def test_cooker_duplicate_cap_cannot_drift_from_runtime(self):
        path = self.root / "tools/cook_diagnostics.py"
        path.write_text(path.read_text().replace("region_count=32", "region_count=33"))
        errors = capabilities.validate(self.data, self.root)
        self.assertTrue(any("runtime_regions" in e and "33" in e for e in errors), errors)

    def test_handwritten_capability_changes_require_generated_docs_update(self):
        changed = copy.deepcopy(self.data)
        changed["capabilities"][0]["state"] = "limited"
        self.assertTrue(capabilities.check_documents(changed, self.root))
        self.assertEqual([], capabilities.check_documents(changed, self.root, write=True))
        self.assertEqual([], capabilities.check_documents(changed, self.root))

    def test_document_repair_preserves_unmarked_prose(self):
        path = self.root / "README.md"
        path.write_text("Private introduction\n" + path.read_text().replace("Full authoring preview: implemented", "Outdated capability claim"))
        self.assertTrue(capabilities.check_documents(self.data, self.root))
        self.assertEqual([], capabilities.check_documents(self.data, self.root, write=True))
        self.assertTrue(path.read_text().startswith("Private introduction\n"))

    def test_missing_or_duplicate_markers_are_not_silently_inserted(self):
        path = self.root / "README.md"
        path.write_text(path.read_text().replace(capabilities.END, ""))
        before = path.read_bytes()
        self.assertTrue(capabilities.check_documents(self.data, self.root, write=True))
        self.assertEqual(before, path.read_bytes())

    def test_all_preview_surfaces_required_and_states_explicit(self):
        changed = copy.deepcopy(self.data)
        changed["capabilities"] = [row for row in changed["capabilities"] if row["id"] != "streamed-native-play"]
        self.assertTrue(capabilities.validate(changed))
        changed = copy.deepcopy(self.data)
        changed["capabilities"][0]["state"] = "probably"
        self.assertTrue(capabilities.validate(changed))

    def test_missing_symbol_unknown_limit_and_escape_are_rejected(self):
        changed = copy.deepcopy(self.data)
        changed["capabilities"][0]["source_references"][0]["symbol"] = "nonexistent_function"
        changed["capabilities"][1]["limits"].append("nonexistent_limit")
        changed["limits"]["runtime_regions"]["source_references"][0]["path"] = "../outside.h"
        errors = capabilities.validate(changed)
        self.assertTrue(any("nonexistent_function" in e for e in errors))
        self.assertTrue(any("Unknown limit" in e for e in errors))
        self.assertTrue(any("project relative" in e for e in errors))

    def test_numeric_parser_does_not_execute_expressions(self):
        for expression in ("True", "__import__('os').getcwd()", "[32][0]", "1 ** 32"):
            with self.assertRaises(ValueError):
                capabilities._number(expression)

    def test_numeric_pattern_is_bound_to_declared_python_function(self):
        path = self.root / "tools/compile_mission.py"
        path.write_text(path.read_text().replace("1 <= len(regions) <= 256", "1 <= len(regions) <= 255") +
                        "\ndef unrelated():\n    return 1 <= len(regions) <= 256\n")
        errors = capabilities.validate(self.data, self.root)
        self.assertTrue(any("portable_package_regions" in e and "255" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
