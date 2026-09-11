from __future__ import annotations

import unittest

from hot_hits_report import GradedHotHit, hit_leg_priced_units


def _leg(
    *,
    discord_rank: int = 1,
    price: int | None = -150,
    result: str = "HIT",
) -> GradedHotHit:
    return GradedHotHit(
        date="2026-09-09",
        rank=1,
        discord_rank=discord_rank,
        discord_role="Core",
        batter_name="Player",
        team="DET",
        original_score=12,
        score=12,
        tier="Core",
        batting_order=1,
        avg_last_5=0.400,
        avg_last_10=0.350,
        season_avg=0.280,
        hit_games_last_5=4,
        hit_games_last_10=8,
        matchup_rating=0.1,
        pitcher_hits_allowed_rate_last_5=0.260,
        pitcher_k_rate_last_5=0.220,
        pitcher_walk_rate_last_5=0.080,
        batter_vs_pitcher_ab=None,
        batter_vs_pitcher_avg=None,
        discord_sim=True,
        result=result,
        hits=1 if result == "HIT" else 0,
        at_bats=4,
        plate_appearances=4,
        game_state="final",
        current_gate_qualified=True,
        current_display_qualified=True,
        gate_failures=[],
        confidence_probability=0.75,
        confidence_percentage=75,
        confidence_label="Solid",
        confidence_reliability=0.85,
        confidence_model_version="hot-hits-confidence-provisional-v1",
        price_hit_yes=price,
        price_hits_2plus_yes=None,
    )


class HitLegPricedUnitsTest(unittest.TestCase):
    def test_mixed_outcomes_at_collected_prices(self) -> None:
        rows = [
            _leg(discord_rank=1, price=-210, result="HIT"),
            _leg(discord_rank=2, price=-210, result="MISS"),
            _leg(discord_rank=3, price=140, result="HIT"),
            _leg(discord_rank=4, price=None, result="HIT"),
            _leg(discord_rank=None, price=-210, result="MISS"),
        ]
        summary = hit_leg_priced_units(rows)
        self.assertEqual(summary["legs"], 3)
        self.assertEqual(summary["wins"], 2)
        expected = (100 / 210) - 1.0 + (140 / 100.0)
        self.assertAlmostEqual(summary["units"], expected, places=6)
        self.assertEqual(summary["unpriced_delivered_legs"], 1)

    def test_no_priced_legs(self) -> None:
        summary = hit_leg_priced_units([_leg(price=None, result="MISS")])
        self.assertEqual(summary["legs"], 0)
        self.assertEqual(summary["units"], 0.0)
        self.assertEqual(summary["unpriced_delivered_legs"], 1)

    def test_pending_results_excluded(self) -> None:
        rows = [_leg(price=-150, result="pending"), _leg(price=-150, result="DNP")]
        summary = hit_leg_priced_units(rows)
        self.assertEqual(summary["legs"], 0)


if __name__ == "__main__":
    unittest.main()
