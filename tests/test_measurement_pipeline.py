"""Tests for energy sample timing and modeled-energy integration."""

import tempfile
import unittest

from algorithm.measurement_utils import elapsed_sample_interval
from algorithm.component_energy_model import ACTIVE, ComponentEnergyModel


class ElapsedSampleIntervalTests(unittest.TestCase):
    def test_first_sample_uses_nominal_interval(self):
        self.assertEqual(elapsed_sample_interval(None, 105.0, 5.0), 5.0)

    def test_later_samples_use_actual_start_to_start_interval(self):
        self.assertAlmostEqual(elapsed_sample_interval(100.0, 105.7, 5.0), 5.7)

    def test_non_increasing_cycle_times_are_rejected(self):
        with self.assertRaises(ValueError):
            elapsed_sample_interval(10.0, 10.0, 5.0)
        with self.assertRaises(ValueError):
            elapsed_sample_interval(11.0, 10.0, 5.0)

    def test_invalid_nominal_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            elapsed_sample_interval(None, 10.0, 0.0)


class EnergyIntegrationTests(unittest.TestCase):
    def test_energy_uses_supplied_elapsed_duration_and_is_labeled_as_modeled(self):
        with tempfile.TemporaryDirectory() as directory:
            model = ComponentEnergyModel(data_dir=directory)
            model.switch_states = {"s1": ACTIVE, "s2": ACTIVE}
            model.link_states = {"s1-s2": ACTIVE}

            result = model.record_cycle(
                cycle=1,
                duration_seconds=2.5,
                phase="energy",
                session_id="test-session",
            )

        self.assertEqual(result["duration_seconds"], 2.5)
        self.assertAlmostEqual(result["interval_energy_wh"], 650.0 * 2.5 / 3600.0)
        self.assertFalse(result["is_physical_measurement"])


if __name__ == "__main__":
    unittest.main()
