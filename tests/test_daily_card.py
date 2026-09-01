from __future__ import annotations

import unittest

from mlb_props.config import PITCHER_OUTS_RECORDED
from mlb_props.daily_card import (
    DAILY_CARD_LIMIT,
    build_daily_card,
    daily_card_payload,
    daily_card_policy_payload,
    daily_card_side_edge,
    qualifies_for_daily_card,
)
from mlb_props.models import Candidate, OpportunityShadow, PitcherConfidenceEstimate
from mlb_props.version import PITCHER_DAILY_CARD_POLICY_VERSION


def _shadow(confidence: str = "HIGH") -> OpportunityShadow:
    return OpportunityShadow(
        version="opportunity-shadow-v1",
        screen_date="2026-09-01",
        starts_available=10,
        recent_start_dates=["2026-08-28"],
        pitch_counts_last_3=[96, 95, 94],
        outs_last_3=[18, 17, 17],
        batters_faced_last_3=[24, 23, 23],
        pitches_per_bf_last_3=[4.0, 4.1, 4.1],
        days_since_last_start=4,
        avg_pitch_count_last_3=95.0,
        max_pitch_count_last_5=100,
        pitch_count_trend="STABLE",
        pitch_count_trend_delta=1.0,
        outs_trend="STABLE",
        outs_trend_delta=0.5,
        pitch_count_volatility_last_5=2.0,
        outs_volatility_last_5=1.0,
        short_starts_last_5=0,
        shadow_pitch_budget=97.0,
        shadow_projected_batters_faced=24.0,
        shadow_projected_outs=18.0,
        opportunity_confidence=confidence,
        flags=[],
    )


def _candidate(
    name: str = "Card Pitcher",
    *,
    side: str = "UNDER",
    line: float = 5.5,
    projected: float = 4.6,
    prop_type: str = "PITCHER_STRIKEOUTS",
    score: int = 2,
    confidence: int | None = 54,
) -> Candidate:
    candidate = Candidate(
        subject_name=name,
        subject_id=1,
        subject_role="pitcher",
        team="DET",
        opponent="KC",
        hand="R",
        prop_type=prop_type,
        side=side,
        line=line,
        bookmaker="test",
        hits_last_5=2,
        played_last_5=5,
        hits_last_10=4,
        played_last_10=10,
        avg_last_5=4.4,
        avg_last_10=4.6,
        median_last_5=4.0,
        median_last_10=4.5,
        season_avg=4.8,
        delta_avg_last_5=0.7,
        delta_avg_last_10=0.6,
        avg_pitch_count_last_5=95.0,
        avg_pitch_count_last_10=94.0,
        avg_outs_last_5=17.0,
        avg_outs_last_10=16.5,
        avg_k_rate_last_5=0.24,
        avg_walk_rate_last_5=0.07,
        avg_earned_runs_last_5=2.4,
        quality_starts_last_10=4,
        short_starts_last_10=1,
        workload_stability=0.6,
        matchup_rating=0.1,
        projected_outs=16.0,
        projected_batters_faced=21.0,
        projected_k_rate=0.22,
        projected_strikeouts=projected,
        score=score,
        flags=["UNDER"],
        opportunity_shadow=_shadow(),
    )
    if confidence is not None:
        candidate.confidence_estimate = PitcherConfidenceEstimate(
            version="pitcher-confidence-calibrated-v2",
            calibration_status="CALIBRATED_V1",
            win_probability=confidence / 100,
            confidence_percentage=confidence,
            label="CAUTIOUS",
            projected_mean=projected,
            projected_standard_deviation=2.5,
            raw_win_probability=0.62,
            reliability_weight=0.6,
            price_included=False,
            calibration_shrink=0.55,
            flags=["CALIBRATED_V1", "PRICE_NOT_INCLUDED"],
        )
    return candidate


class DailyCardGateTests(unittest.TestCase):
    def test_under_small_edge_low_line_qualifies(self) -> None:
        self.assertTrue(qualifies_for_daily_card(_candidate(line=5.5, projected=4.6)))
        self.assertTrue(qualifies_for_daily_card(_candidate(line=5.5, projected=4.5)))

    def test_boundary_line_and_edge_are_inclusive(self) -> None:
        self.assertTrue(qualifies_for_daily_card(_candidate(line=5.5, projected=6.5)))
        self.assertFalse(qualifies_for_daily_card(_candidate(line=6.5, projected=5.5)))
        self.assertFalse(qualifies_for_daily_card(_candidate(line=5.5, projected=4.4)))

    def test_over_side_is_excluded(self) -> None:
        self.assertFalse(qualifies_for_daily_card(_candidate(side="OVER", projected=6.4)))

    def test_outs_prop_is_excluded(self) -> None:
        self.assertFalse(
            qualifies_for_daily_card(
                _candidate(prop_type=PITCHER_OUTS_RECORDED, projected=15.0)
            )
        )

    def test_side_edge_is_line_minus_projection(self) -> None:
        self.assertAlmostEqual(daily_card_side_edge(_candidate(line=5.5, projected=4.6)), 0.9)


class DailyCardSelectionTests(unittest.TestCase):
    def test_card_is_capped_and_ranked_by_confidence(self) -> None:
        candidates = [
            _candidate("Low Conf", confidence=51),
            _candidate("High Conf", confidence=57),
            _candidate("Mid Conf", confidence=54),
            _candidate("Top Conf", confidence=56),
            _candidate("Also Mid", confidence=54),
            _candidate("Excluded", line=6.5, projected=5.6),
        ]

        card = build_daily_card(candidates)

        self.assertEqual(DAILY_CARD_LIMIT, 4)
        self.assertEqual(len(card), 4)
        self.assertEqual(
            [play.candidate.subject_name for play in card],
            ["High Conf", "Top Conf", "Mid Conf", "Also Mid"],
        )
        self.assertEqual([play.card_rank for play in card], [1, 2, 3, 4])

    def test_confidence_ties_break_by_side_edge_then_signal(self) -> None:
        candidates = [
            _candidate("Smaller Edge", projected=4.9, score=5),
            _candidate("Larger Edge", projected=4.5, score=2),
        ]

        card = build_daily_card(candidates)

        self.assertEqual(card[0].candidate.subject_name, "Larger Edge")

    def test_empty_board_produces_empty_card(self) -> None:
        self.assertEqual(build_daily_card([_candidate(side="OVER")]), [])

    def test_selection_is_deterministic(self) -> None:
        candidates = [_candidate(f"P{index}", confidence=50 + index) for index in range(9)]

        first = build_daily_card(candidates)
        second = build_daily_card(list(reversed(candidates)))

        self.assertEqual(
            [play.candidate.subject_name for play in first],
            [play.candidate.subject_name for play in second],
        )
        self.assertEqual(
            [play.candidate.subject_name for play in first],
            [f"P{index}" for index in range(8, 4, -1)],
        )


class DailyCardPayloadTests(unittest.TestCase):
    def test_payload_records_rank_and_tier_marker(self) -> None:
        card = build_daily_card([_candidate("Card One", confidence=55)])

        payload = daily_card_payload(card)

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["card_rank"], 1)
        self.assertEqual(payload[0]["history_tier"], "daily_card")
        self.assertEqual(payload[0]["subject_name"], "Card One")
        self.assertEqual(payload[0]["side"], "UNDER")

    def test_policy_payload_describes_frozen_gates(self) -> None:
        policy = daily_card_policy_payload()

        self.assertEqual(policy["version"], PITCHER_DAILY_CARD_POLICY_VERSION)
        self.assertEqual(policy["version"], "daily-unders-card-v1")
        self.assertEqual(policy["side"], "UNDER")
        self.assertEqual(policy["max_line"], 5.5)
        self.assertEqual(policy["max_abs_edge"], 1.0)
        self.assertEqual(policy["limit"], 4)
        self.assertIsNone(policy["market_support_gate"])


if __name__ == "__main__":
    unittest.main()
