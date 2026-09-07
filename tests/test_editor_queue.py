import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from editorctl import command_queue


class EditorQueueTests(unittest.TestCase):
    def test_project_queue_is_stable_and_isolated_sessions_keep_existing_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            expected = root / ".dev/editor"
            self.assertEqual(command_queue(root), expected / "project")
            self.assertEqual(command_queue(root, "content/first_room.json"), expected)
            self.assertEqual(command_queue(root, root / "content/first_room.json"), expected)

    def test_scenes_are_isolated_and_paths_resolve_against_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            courtyard = root / ".dev/editor/scenes/moonlit_courtyard"
            self.assertEqual(command_queue(root, "content/moonlit_courtyard.json"), courtyard)
            self.assertEqual(command_queue(root, root / "content/moonlit_courtyard.json"), courtyard)
            self.assertEqual(command_queue(root, "content/../content/moonlit_courtyard.json"), courtyard)
            self.assertNotEqual(command_queue(root, "content/second_yard.json"), courtyard)
            self.assertFalse(courtyard.exists())

    def test_invalid_levels_cannot_escape_or_alias_another_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for source in ("../content/outside.json", "content/nested/yard.json",
                           "content/yard.txt", "content/yard.json.json", "content/yard space.json",
                           "content/夜.json", "content/.json"):
                with self.subTest(source=source), self.assertRaises(ValueError):
                    command_queue(root, source)
            self.assertFalse((root / ".dev").exists())


if __name__ == "__main__":
    unittest.main()
