from __future__ import annotations

import unittest
from types import SimpleNamespace

from mlb_props.config import PITCHER_STRIKEOUTS
from mlb_props.models import PriceShadow
from mlb_props.tiers import (
    CORE_EDGE_MAX,
    CORE_MIN_NO_VIG_PROBABILITY,
    core_block_reasons,
    core_eligible,
    side_no_vig_probability,
)


def _item(
    *,
    side: str = "UNDER",
    line: float = 5.5,
    projected: float = 4.2,
    score: int = 8,
    flags: list[str] | None = None,
    price_shadow: PriceShadow | None = None,
    projected_outs: float = 15.0,
    projected_batters_faced: float = 20.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        prop_type=PITCHER_STRIKEOUTS,
        side=side,
        line=line,
        projected_strikeouts=projected,
        projected_outs=projected_outs,
        projected_batters_faced=projected_batters_faced,
        score=score,
        flags=list(flags or []),
        price_shadow=price_shadow,
    )


def _price_shadow(*, over_no_vig: float, under_no_vig: float) -> PriceShadow:
    return PriceShadow(
        version="price-shadow-v1",
        screen_date="2026-09-01",
        bookmaker="test",
        prop_type=PITCHER_STRIKEOUTS,
        line=5.5,
        over_price=-110,
        under_price=-110,
        price_collected_at="2026-09-01T15:35:00+00:00",
        source="test",
        over_implied_probability=0.5,
        under_implied_probability=0.5,
        over_no_vig_probability=over_no_vig,
        under_no_vig_probability=under_no_vig,
    )


class CoreGateRebuildTests(unittest.TestCase):
    def test_unpriced_market_backed_under_qualifies(self) -> None:
        item = _item()

        self.assertEqual(core_block_reasons(item), [])
        self.assertTrue(core_eligible(item, min_score=7))

    def test_over_side_is_blocked(self) -> None:
        item = _item(side="OVER", line=5.5, projected=6.7)

        reasons = core_block_reasons(item)

        self.assertTrue(any("requires UNDER" in reason for reason in reasons))
        self.assertFalse(core_eligible(item, min_score=7))

    def test_under_below_minimum_edge_is_blocked(self) -> None:
        item = _item(line=5.5, projected=4.8)

        reasons = core_block_reasons(item)

        self.assertTrue(any("under edge" in reason for reason in reasons))

    def test_under_edge_above_cap_is_blocked(self) -> None:
        item = _item(line=7.5, projected=5.2)

        reasons = core_block_reasons(item)

        self.assertTrue(any(f"edge 2.30>{CORE_EDGE_MAX:.2f}" in reason for reason in reasons))

    def test_high_opportunity_under_remains_blocked(self) -> None:
        item = _item(flags=["DEPTH_PLUS", "WORKLOAD_PLUS", "QS_PLUS"], projected_outs=18.5)

        reasons = core_block_reasons(item)

        self.assertTrue(any("under workload/depth" in reason for reason in reasons))
        self.assertTrue(any("under outs" in reason for reason in reasons))

    def test_volatile_under_with_small_edge_is_blocked(self) -> None:
        item = _item(line=5.0, projected=4.0, flags=["VOLATILE"])

        reasons = core_block_reasons(item)

        self.assertTrue(any("volatile edge" in reason for reason in reasons))

    def test_priced_side_below_no_vig_threshold_is_blocked(self) -> None:
        item = _item(
            price_shadow=_price_shadow(over_no_vig=0.62, under_no_vig=0.38),
        )

        reasons = core_block_reasons(item)

        self.assertTrue(
            any(f"no-vig 0.38<{CORE_MIN_NO_VIG_PROBABILITY:.2f}" in reason for reason in reasons)
        )
        self.assertFalse(core_eligible(item, min_score=7))

    def test_priced_side_at_or_above_no_vig_threshold_qualifies(self) -> None:
        item = _item(
            price_shadow=_price_shadow(over_no_vig=0.44, under_no_vig=0.56),
        )

        self.assertEqual(core_block_reasons(item), [])
        self.assertTrue(core_eligible(item, min_score=7))

    def test_under_side_reads_under_no_vig_probability(self) -> None:
        shadow = _price_shadow(over_no_vig=0.61, under_no_vig=0.39)

        self.assertEqual(side_no_vig_probability(_item(price_shadow=shadow)), 0.39)
        self.assertEqual(
            side_no_vig_probability(_item(side="OVER", price_shadow=shadow)),
            0.61,
        )
        self.assertIsNone(side_no_vig_probability(_item()))

    def test_starter_board_probe_without_price_shadow_still_gates(self) -> None:
        probe = SimpleNamespace(
            prop_type=PITCHER_STRIKEOUTS,
            side="OVER",
            line=5.5,
            score=8,
            projected_strikeouts=6.7,
            projected_outs=15.0,
            projected_batters_faced=20.0,
            flags=[],
        )

        reasons = core_block_reasons(probe)

        self.assertTrue(any("requires UNDER" in reason for reason in reasons))


if __name__ == "__main__":
    unittest.main()
