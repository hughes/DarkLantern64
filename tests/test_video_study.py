import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from video_study import snapshot


class VideoStudyProvenanceTests(unittest.TestCase):
    def fixture(self, root):
        base = root / "baseline"
        base.mkdir()
        (base / "snapshot.json").write_text(json.dumps({"base_rom_sha256": "rom-a", "files": {}, "sdk_identity": {}}))
        (base / "build.json").write_text(json.dumps({"signature": "signature-a", "sdk": "unused-sdk"}))
        verification = root / "verification.json"
        verification.write_text(json.dumps({"passed": True, "manifest":
            {"rom_sha256": "rom-a", "signature": "signature-a"}}))
        return base, verification

    def test_reused_output_rejects_different_verified_rom(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, verification = self.fixture(root)
            value = json.loads(verification.read_text())
            value["manifest"]["rom_sha256"] = "rom-b"
            verification.write_text(json.dumps(value))
            with patch("video_study.sdk_identity") as sdk:
                with self.assertRaisesRegex(ValueError, "new output directory"):
                    snapshot(root, verification)
                sdk.assert_not_called()

    def test_reused_output_rejects_sdk_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, verification = self.fixture(root)
            with patch("video_study.sdk_identity", return_value={"header.h": "new"}):
                with self.assertRaisesRegex(ValueError, "SDK inputs changed"):
                    snapshot(root, verification)

    def test_frozen_input_change_rejected_before_sdk_or_build(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base, verification = self.fixture(root)
            (base / "render.o").write_bytes(b"altered object")
            saved = json.loads((base / "snapshot.json").read_text())
            saved["files"] = {"render.o": "old hash"}
            (base / "snapshot.json").write_text(json.dumps(saved))
            with patch("video_study.sdk_identity") as sdk:
                with self.assertRaisesRegex(ValueError, "Frozen baseline changed"):
                    snapshot(root, verification)
                sdk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
