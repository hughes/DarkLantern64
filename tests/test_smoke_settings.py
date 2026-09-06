import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from smoke_ares import isolate_settings
from prepare_ares import node_indices


class SmokeSettingsTests(unittest.TestCase):
    def test_isolation_preserves_source_and_disables_external_inputs(self):
        raw = ("Nintendo64\n  ExpansionPak: true\n  Input\n    Controller.Port.1\n"
               "      Gamepad\n        A: my-switch-button\n        X-Axis\n          Lo: my-stick\n"
               "Hotkeys\n  FastForward: my-key\nVideo\n  Driver: Vulkan\n")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "user.bml"
            source.write_text(raw, encoding="utf-8")
            run = Path(directory) / "run"
            run.mkdir()
            result, digest = isolate_settings(source, run, 4)
            self.assertEqual(source.read_text(), raw)
            self.assertEqual(len(digest), 64)
            lines = result.read_text().splitlines()
            nodes = node_indices(lines)
            def value(path):
                return lines[nodes[tuple(path.split('/'))]].partition(':')[2].strip()
            self.assertEqual(value('Nintendo64/ExpansionPak'), 'false')
            self.assertEqual(value('Nintendo64/Input/Controller.Port.1/Gamepad/A'), ';;')
            self.assertEqual(value('Nintendo64/Input/Controller.Port.1/Gamepad/X-Axis/Lo'), ';;')
            self.assertEqual(value('Hotkeys/FastForward'), ';;')
            self.assertEqual(value('Video/Driver'), 'Vulkan')
            self.assertEqual(value('Developer/HomebrewMode'), 'true')
            self.assertEqual(value('Paths/Saves'), (run / 'saves').as_posix() + '/')


if __name__ == '__main__':
    unittest.main()
