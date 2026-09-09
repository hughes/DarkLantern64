"""Music selection, cache invalidation and failure preservation at the cook boundary."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.cook_music import cook_music


class MusicCookTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "content").mkdir()
        (self.root / "art/music").mkdir(parents=True)
        (self.root / "sdk/bin").mkdir(parents=True)
        (self.root / "sdk/bin/audioconv64.exe").write_bytes(b"converter-one")
        self.source = self.root / "art/music/cue.xm"
        header = bytearray(81)
        header[:17] = b"Extended Module: "
        header[58:60] = b"\x04\x01"
        struct.pack_into("<H", header, 68, 8)
        self.source.write_bytes(header)
        self.manifest = self.root / "content/music.json"
        self.manifest.write_text(json.dumps({"version": 1, "tracks": {"cue": {
            "source": "art/music/cue.xm", "gain": .55}}, "levels": {"sponza": "cue", "other": "cue"}}))
        self.levels = [{"source": str(self.root / "content/sponza.json")},
                       {"source": str(self.root / "content/other.json")}]
        self.calls = 0

    def convert(self, args, **kwargs):
        self.calls += 1
        self.assertNotIn("\\", args[-1])
        source = Path(args[-1])
        source.with_suffix(".xm64").write_bytes(b"XM64" + hashlib.sha256(source.read_bytes()).digest())
        return subprocess.CompletedProcess(args, 0, "converted", "")

    def cook(self, levels=None):
        return cook_music(self.levels if levels is None else levels, self.root / "out",
                          self.root / "sdk", root=self.root)

    def test_shared_cue_caches_and_source_edit_invalidates(self):
        original = self.source.read_bytes()
        with patch("tools.cook_music.subprocess.run", side_effect=self.convert):
            first = self.cook()
            self.assertEqual(len(first["assets"]), 1)
            self.assertEqual(first["cues"][0], first["cues"][1])
            self.cook()
            self.assertEqual(self.calls, 1)
            self.assertEqual(self.source.read_bytes(), original)
            self.source.write_bytes(original[:-1] + b"x")
            changed = self.cook()
            self.assertEqual(self.calls, 2)
            self.assertNotEqual(first["assets"][0]["sha256"], changed["assets"][0]["sha256"])

    def test_corrupt_cache_is_recooked(self):
        with patch("tools.cook_music.subprocess.run", side_effect=self.convert):
            first = self.cook()
            Path(first["assets"][0]["cooked_path"]).write_bytes(b"broken")
            second = self.cook()
            self.assertEqual(self.calls, 2)
            self.assertEqual(first["assets"][0]["sha256"], second["assets"][0]["sha256"])

    def test_failure_preserves_previous_cache_and_source(self):
        with patch("tools.cook_music.subprocess.run", side_effect=self.convert):
            first = self.cook()
        previous = Path(first["assets"][0]["cooked_path"])
        binary = previous.read_bytes()
        self.source.write_bytes(self.source.read_bytes()[:-1] + b"y")
        edited = self.source.read_bytes()
        with patch("tools.cook_music.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "bad export")):
            with self.assertRaisesRegex(ValueError, "conversion failed"):
                self.cook()
        self.assertEqual(previous.read_bytes(), binary)
        self.assertEqual(self.source.read_bytes(), edited)

    def test_unassigned_level_needs_no_converter(self):
        with patch("tools.cook_music.subprocess.run", side_effect=AssertionError("Unexpected converter")):
            result = self.cook([{"source": str(self.root / "content/quiet.json")}])
            self.assertEqual(result["cues"], [None])
            self.assertEqual(result["assets"], [])

    def test_channel_budget_rejected_before_conversion(self):
        data = bytearray(self.source.read_bytes())
        struct.pack_into("<H", data, 68, 9)
        self.source.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "1..8 XM channels"):
            self.cook()


if __name__ == "__main__":
    unittest.main()
