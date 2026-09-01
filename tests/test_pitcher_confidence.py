from __future__ import annotations

import unittest
from types import SimpleNamespace

from mlb_props.config import PITCHER_STRIKEOUTS
from mlb_props.pitcher_confidence import confidence_label, estimate_pitcher_confidence


def _candidate(
    *,
    projected: float = 6.3,
    line: float = 5.5,
    side: str = "OVER",
    reliability: str = "HIGH",
    flags: list[str] | None = None,
) -> SimpleNamespace:
    shadow = SimpleNamespace(
        opportunity_confidence=reliability,
        starts_available=10,
        outs_volatility_last_5=1.2,
        flags=[],
    )
    return SimpleNamespace(
        prop_type=PITCHER_STRIKEOUTS,
        projected_strikeouts=projected,
        projected_outs=18.0,
        projected_batters_faced=24.0,
        projected_k_rate=projected / 24.0,
        workload_stability=0.70,
        opportunity_shadow=shadow,
        played_last_10=10,
        flags=list(flags or []),
        side=side,
        line=line,
    )


class PitcherConfidenceTests(unittest.TestCase):
    def test_larger_projection_edge_increases_confidence(self) -> None:
        small_edge = estimate_pitcher_confidence(_candidate(projected=6.0))
        large_edge = estimate_pitcher_confidence(_candidate(projected=7.0))

        self.assertGreater(large_edge.win_probability, small_edge.win_probability)

    def test_estimate_is_deterministic(self) -> None:
        candidate = _candidate(projected=6.7, reliability="MEDIUM")

        self.assertEqual(
            estimate_pitcher_confidence(candidate),
            estimate_pitcher_confidence(candidate),
        )

    def test_low_workload_reliability_shrinks_toward_fifty(self) -> None:
        high = estimate_pitcher_confidence(_candidate(reliability="HIGH"))
        low = estimate_pitcher_confidence(_candidate(reliability="LOW"))

        self.assertGreater(high.win_probability, low.win_probability)
        self.assertIn("LOW_WORKLOAD_RELIABILITY", low.flags)

    def test_risk_flags_reduce_confidence_without_changing_projection(self) -> None:
        clean = estimate_pitcher_confidence(_candidate())
        risky = estimate_pitcher_confidence(
            _candidate(flags=["LOW_PITCH", "CONTROL_RISK"])
        )

        self.assertEqual(clean.projected_mean, risky.projected_mean)
        self.assertGreater(clean.win_probability, risky.win_probability)
        self.assertIn("SEVERE_RISK_SHRINKAGE", risky.flags)

    def test_under_probability_uses_the_listed_side(self) -> None:
        estimate = estimate_pitcher_confidence(
            _candidate(projected=4.2, side="UNDER")
        )

        self.assertGreater(estimate.win_probability, 0.50)

    def test_estimate_is_calibrated_and_price_agnostic(self) -> None:
        estimate = estimate_pitcher_confidence(_candidate(projected=9.0))

        self.assertEqual(estimate.calibration_status, "CALIBRATED_V1")
        self.assertFalse(estimate.price_included)
        self.assertLessEqual(estimate.confidence_percentage, 57)
        self.assertEqual(estimate.calibration_shrink, 0.55)
        self.assertIn("CALIBRATED_V1", estimate.flags)
        self.assertIn("PRICE_NOT_INCLUDED", estimate.flags)

    def test_strong_edges_no_longer_reach_the_old_cap(self) -> None:
        strong = estimate_pitcher_confidence(_candidate(projected=9.5))

        self.assertEqual(strong.win_probability, 0.57)
        self.assertLess(strong.confidence_percentage, 60)
        self.assertEqual(strong.label, "SOLID")

    def test_label_boundaries(self) -> None:
        self.assertEqual(confidence_label(60), "STRONG")
        self.assertEqual(confidence_label(57), "SOLID")
        self.assertEqual(confidence_label(54), "CAUTIOUS")
        self.assertEqual(confidence_label(51), "HIGHER RISK")
        self.assertEqual(confidence_label(50), "NO PICK")


if __name__ == "__main__":
    unittest.main()
