"""Leak checks compare the same allocation lifecycle across bundle cycles."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from smoke_bundle import heap_stability


class BundleHeapTests(unittest.TestCase):
    def test_identical_usage_and_one_warmup(self):
        self.assertEqual(heap_stability([900,950,1000,1000,1000,1000], 'entry'),
                         {'launch':1000,'restart':1000})

    def test_exactly_stable_phase_offset(self):
        self.assertEqual(heap_stability([900,1008,1000,1008,1000,1008], 'scene'),
                         {'launch':1000,'restart':1008})

    def test_launch_growth_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, r'\(launch\)'):
            heap_stability([900,1008,1000,1008,1008,1008], 'scene')

    def test_restart_growth_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, r'\(restart\)'):
            heap_stability([900,1008,1000,1008,1000,1016], 'scene')

    def test_missing_cycle_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'three launch/restart pairs'):
            heap_stability([1000,1000,1000,1000], 'scene')


if __name__ == '__main__':
    unittest.main()
