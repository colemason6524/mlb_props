from __future__ import annotations

import unittest

from mlb_props.price_shadow import (
    american_to_implied_probability,
    no_vig_probabilities,
    raw_implied_probabilities,
)


class AmericanOddsTests(unittest.TestCase):
    def test_positive_american_odds(self) -> None:
        self.assertAlmostEqual(american_to_implied_probability(150), 0.4, places=4)

    def test_negative_american_odds(self) -> None:
        self.assertAlmostEqual(american_to_implied_probability(-150), 0.6, places=4)

    def test_missing_price(self) -> None:
        self.assertIsNone(american_to_implied_probability(None))

    def test_zero_price_is_rejected(self) -> None:
        self.assertIsNone(american_to_implied_probability(0))

    def test_garbage_price_is_rejected(self) -> None:
        self.assertIsNone(american_to_implied_probability("not-a-price"))


class RawImpliedTests(unittest.TestCase):
    def test_raw_returns_vig_inclusive_probabilities(self) -> None:
        over, under = raw_implied_probabilities(-110, -110)
        self.assertAlmostEqual(over, 0.5238, places=3)
        self.assertAlmostEqual(under, 0.5238, places=3)


class NoVigTests(unittest.TestCase):
    def test_even_juice_de_vigs_to_half(self) -> None:
        over, under = no_vig_probabilities(-110, -110)
        self.assertAlmostEqual(over, 0.5, places=4)
        self.assertAlmostEqual(under, 0.5, places=4)

    def test_skewed_prices_de_vig_preserves_ordering(self) -> None:
        over, under = no_vig_probabilities(-140, 110)
        self.assertGreater(over, under)
        self.assertAlmostEqual(over + under, 1.0, places=6)

    def test_missing_side_returns_none(self) -> None:
        over, under = no_vig_probabilities(-110, None)
        self.assertIsNone(over)
        self.assertIsNone(under)


if __name__ == "__main__":
    unittest.main()
