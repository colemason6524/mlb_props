from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from mlb_props.pitcher_grading import (
    american_breakeven_rate,
    american_payout,
    priced_card_units,
)
from mlb_props.version import PITCHER_HISTORY_SCHEMA_VERSION


def _card_row(outcome: str, under_price: int | None) -> SimpleNamespace:
    price_shadow = None if under_price is None else {"under_price": under_price}
    return SimpleNamespace(outcome=outcome, price_shadow=price_shadow)


class AmericanConversionsTest(unittest.TestCase):
    def test_payouts(self) -> None:
        self.assertAlmostEqual(american_payout(-150), 100 / 150, places=6)
        self.assertAlmostEqual(american_payout(110), 1.10, places=6)
        self.assertAlmostEqual(american_payout(100), 1.0, places=6)
        self.assertIsNone(american_payout(0))
        self.assertIsNone(american_payout(None))

    def test_breakeven_rates(self) -> None:
        self.assertAlmostEqual(american_breakeven_rate(-150), 0.60, places=6)
        self.assertAlmostEqual(american_breakeven_rate(110), 100 / 210, places=6)


class PricedCardUnitsTest(unittest.TestCase):
    def test_units_at_collected_prices(self) -> None:
        rows = [
            _card_row("win", -150),
            _card_row("loss", -150),
            _card_row("win", 110),
            _card_row("push", -140),
            _card_row("win", None),
        ]
        summary = priced_card_units(rows)
        self.assertEqual(summary["priced_n"], 3)
        self.assertEqual(summary["priced_wins"], 2)
        expected = (100 / 150) - 1.0 + 1.10
        self.assertAlmostEqual(summary["priced_units"], expected, places=6)
        self.assertEqual(summary["unpriced_graded_rows"], 1)
        # push rows are not graded and never counted
        self.assertAlmostEqual(
            summary["priced_avg_breakeven_rate"],
            (0.60 + 0.60 + 100 / 210) / 3,
            places=6,
        )

    def test_no_priced_rows(self) -> None:
        summary = priced_card_units([_card_row("win", None), _card_row("loss", None)])
        self.assertEqual(summary["priced_n"], 0)
        self.assertEqual(summary["priced_units"], 0.0)
        self.assertIsNone(summary["priced_avg_breakeven_rate"])


class DailyCardSummaryPricedKeysTest(unittest.TestCase):
    def test_summary_includes_priced_fields(self) -> None:
        from mlb_props.pitcher_grading import daily_card_summary

        card_row = SimpleNamespace(
            outcome="win",
            price_shadow={"under_price": -150},
            file_name="pitcher_props_test.json",
        )
        baseline_row = SimpleNamespace(
            side="UNDER",
            outcome="loss",
            file_name="pitcher_props_test.json",
        )
        history = SimpleNamespace(
            daily_card=[card_row],
            candidates=[baseline_row],
            daily_card_policy_version="daily-unders-card-v1",
        )
        summary = daily_card_summary(history)
        self.assertIn("priced_units", summary)
        self.assertEqual(summary["priced_n"], 1)
        self.assertAlmostEqual(summary["priced_units"], 100 / 150, places=6)
        self.assertEqual(summary["graded"], 1)
        self.assertEqual(PITCHER_HISTORY_SCHEMA_VERSION, 8)


if __name__ == "__main__":
    unittest.main()
