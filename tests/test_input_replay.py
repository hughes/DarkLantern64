"""Recorded commands remain bounded when comparing streamed host/ROM play."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.input_replay import load
from tools.build import build_rom


class InputReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "replay.json"
        self.replay = dict(format="darklantern-replay", version=1, tick_rate=60,
                           fingerprint="a" * 64, inputs=[dict(forward=1, use=True)])

    def read(self):
        self.path.write_text(json.dumps(self.replay), encoding="utf-8")
        return load(self.path)

    def test_stream_boundary_is_explicit_and_invalid_modes_reject(self):
        self.assertNotIn("stream_boundary", self.read())
        for mode in ("frame", "settled"):
            self.replay["stream_boundary"] = mode
            self.assertEqual(self.read()["stream_boundary"], mode)
        for mode in ("immediate", None, [], True):
            self.replay["stream_boundary"] = mode
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, "stream replay boundary"):
                self.read()

    def test_invalid_input_is_rejected_even_with_settled_stream_boundary(self):
        self.replay["stream_boundary"] = "settled"
        for row in (dict(forward=1.1), dict(use=1), dict(teleport=True), dict(turn=float("nan"))):
            self.replay["inputs"] = [row]
            with self.subTest(row=row), self.assertRaises(ValueError):
                self.read()

    def test_bundle_replay_requires_direct_selection_before_io_or_cooking(self):
        with patch("tools.build.load_input_replay") as replay, patch("tools.build.prepare_bundle") as cook:
            with self.assertRaisesRegex(ValueError, "direct level launch"):
                build_rom(Path("missing-sdk"), bundle=Path("catalog.json"), input_replay=self.path)
            replay.assert_not_called()
            cook.assert_not_called()


if __name__ == "__main__":
    unittest.main()
