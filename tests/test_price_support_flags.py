from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from mlb_props.screener import _build_price_shadow


def _line(line: float = 5.5, over: int = -130, under: int = 105) -> SimpleNamespace:
    return SimpleNamespace(
        prop_type="PITCHER_STRIKEOUTS",
        game_date=date(2026, 9, 9),
        bookmaker="fanduel",
        line=line,
        over_price=over,
        under_price=under,
        price_collected_at="t",
        source="unit_test",
        event_id="1",
        team="DET",
        opponent="NYY",
        subject_name_raw="Test Pitcher",
        collected_at=None,
    )


def _candidate(side: str, win_probability: float) -> SimpleNamespace:
    return SimpleNamespace(
        side=side,
        confidence_estimate=SimpleNamespace(win_probability=win_probability),
    )


class PriceSupportFlagTests(unittest.TestCase):
    """Side win probability is already side-oriented, so an UNDER candidate
    must be compared against the UNDER no-vig probability directly."""

    def test_under_side_uses_under_probability_not_the_complement(self) -> None:
        # -130/105 de-vigs to over 0.5366 / under 0.4634. An UNDER candidate
        # with side probability 0.55: under_prob 0.4634 <= 0.55 → SUPPORTS.
        # The old code compared against 1-0.55=0.45 and flagged AGAINST.
        shadow = _build_price_shadow(_line(over=-130, under=105), _candidate("UNDER", 0.55))
        self.assertIn("PRICE_SUPPORTS_SIDE", shadow.flags)

    def test_under_against_when_market_prob_exceeds_side_probability(self) -> None:
        shadow = _build_price_shadow(_line(over=-130, under=105), _candidate("UNDER", 0.45))
        self.assertIn("PRICE_AGAINST_SIDE", shadow.flags)

    def test_over_supported_when_over_prob_below_side_probability(self) -> None:
        shadow = _build_price_shadow(_line(over=-130, under=105), _candidate("OVER", 0.55))
        self.assertIn("PRICE_SUPPORTS_SIDE", shadow.flags)

    def test_over_against_when_market_prob_exceeds_side_probability(self) -> None:
        shadow = _build_price_shadow(_line(over=-130, under=105), _candidate("OVER", 0.45))
        self.assertIn("PRICE_AGAINST_SIDE", shadow.flags)


if __name__ == "__main__":
    unittest.main()
