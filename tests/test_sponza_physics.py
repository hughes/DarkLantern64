import tempfile
from pathlib import Path
import unittest

from tools.verify_sponza_physics import ROOT, verify


class AuthoredSponzaPhysics(unittest.TestCase):
    def test_actual_core_traverses_authored_stairs_and_respects_stacked_floors(self):
        with tempfile.TemporaryDirectory() as directory:
            report = verify(Path(directory), ROOT / "content/sponza_courtyard.json")
        self.assertTrue(report["passed"])
        self.assertEqual(report["traversals"], 36)
        self.assertTrue(report["level_colliders_included"])
        self.assertGreater(report["actor_samples"], 5000)
        self.assertEqual(report["clearance_checks"], 6)
        self.assertEqual(report["los_checks"], 12)
        self.assertTrue(report["mission_ordinary_guards"])
        self.assertGreater(report["mission_seconds"], 60)


if __name__ == "__main__":
    unittest.main()
