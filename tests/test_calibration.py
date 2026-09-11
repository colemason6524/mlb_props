from __future__ import annotations

import unittest

from mlb_props.calibration import (
    IsotonicCalibrator,
    LogisticModel,
    brier,
    count_probabilities,
    log_loss,
    neg_binomial_pmf,
)


class IsotonicTests(unittest.TestCase):
    def test_monotone_fit_on_noisy_monotone_data(self) -> None:
        pairs = [
            (0.2, 0), (0.25, 0), (0.3, 0), (0.35, 0),
            (0.4, 1), (0.45, 0), (0.5, 1),
            (0.6, 1), (0.7, 1), (0.8, 1), (0.9, 1),
        ]
        cal = IsotonicCalibrator.fit(pairs)
        values = [cal.predict(x) for x in (0.05, 0.3, 0.5, 0.7, 0.95)]
        self.assertEqual(values[0], min(values))
        for a, b in zip(values, values[1:]):
            self.assertGreaterEqual(b, a - 1e-9)
        self.assertGreater(cal.predict(0.9), cal.predict(0.4))

    def test_estimate_stays_within_observed_range(self) -> None:
        cal = IsotonicCalibrator.fit([(0.3, 0), (0.6, 1), (0.7, 1)])
        value = cal.predict(1.5)
        self.assertLessEqual(value, 1.0)

    def test_empty_fit_passes_through(self) -> None:
        cal = IsotonicCalibrator.fit([])
        self.assertEqual(cal.predict(0.42), 0.42)


class ScoreTests(unittest.TestCase):
    def test_brier_perfect_and_useless(self) -> None:
        self.assertEqual(brier([(1.0, 1)]), 0.0)
        self.assertAlmostEqual(brier([(0.5, 1), (0.5, 0)]), 0.25)

    def test_log_loss_symmetry(self) -> None:
        pairs = [(0.9, 1), (0.2, 0)]
        value = log_loss(pairs)
        self.assertLess(value, 0.3)

    def test_wilson_rejects_zero_samples(self) -> None:
        self.assertIsNone(log_loss([]))


class CountDistributionTests(unittest.TestCase):
    def test_half_line_has_no_push(self) -> None:
        over, push, under = count_probabilities(5.5, 6.0, 8.0)
        self.assertAlmostEqual(push, 0.0, places=9)
        self.assertAlmostEqual(over + under, 1.0, places=6)
        self.assertGreater(over, under)  # mean above the line favours over

    def test_integer_line_push(self) -> None:
        over, push, under = count_probabilities(5.0, 6.0, 8.0)
        self.assertGreater(push, 0.0)
        self.assertAlmostEqual(over + push + under, 1.0, places=6)


class FMLTests(unittest.TestCase):
    def test_logistic_separates(self) -> None:
        model = LogisticModel(epochs=800)
        rows = [[1.0, 0.0], [2.0, 0.0], [0.0, 1.0], [0.0, 2.0]]
        targets = [1, 1, 0, 0]
        model.fit(rows, targets)
        self.assertGreater(model.predict_proba([2.0, 0.0]), 0.7)
        self.assertLess(model.predict_proba([0.0, 2.0]), 0.3)


if __name__ == "__main__":
    unittest.main()
