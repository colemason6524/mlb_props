from __future__ import annotations

import unittest
from datetime import date, timedelta

from mlb_props.models import PitcherGameLog
from mlb_props.recency_shadow import build_recency_projection_shadow
from mlb_props.version import PITCHER_RECENCY_SHADOW_VERSION


def _log(
    game_date: date,
    *,
    strikeouts: int,
    batters_faced: int = 20,
    outs_recorded: int = 15,
) -> PitcherGameLog:
    return PitcherGameLog(
        pitcher_name_raw="Test Pitcher",
        pitcher_name_norm="test pitcher",
        game_date=game_date,
        team="DET",
        opponent="KC",
        hand="R",
        outs_recorded=outs_recorded,
        strikeouts=strikeouts,
        pitches_thrown=90,
        batters_faced=batters_faced,
        walks=1,
        hits_allowed=4,
        earned_runs=2,
        did_start=True,
        source="test",
        pitcher_id=1,
    )


class RecencyProjectionShadowTests(unittest.TestCase):
    def test_uses_aggregate_rates_instead_of_mean_of_game_rates(self) -> None:
        screen_date = date(2026, 8, 4)
        logs = [
            _log(screen_date - timedelta(days=5), strikeouts=1, batters_faced=10),
            _log(screen_date - timedelta(days=10), strikeouts=9, batters_faced=30),
        ]

        shadow = build_recency_projection_shadow(logs, screen_date, None, 18.0)

        self.assertEqual(shadow.version, PITCHER_RECENCY_SHADOW_VERSION)
        self.assertAlmostEqual(shadow.season_k_rate, 0.25, places=4)
        self.assertAlmostEqual(shadow.k_rate_last_10, 0.25, places=4)
        self.assertAlmostEqual(shadow.k_rate_last_5, 0.25, places=4)
        self.assertAlmostEqual(shadow.shadow_projected_k_rate, 0.25, places=3)

    def test_excludes_same_day_and_future_logs(self) -> None:
        screen_date = date(2026, 8, 4)
        prior_logs = [
            _log(screen_date - timedelta(days=index + 1), strikeouts=value)
            for index, value in enumerate((2, 4, 6, 8, 10))
        ]
        logs = prior_logs + [
            _log(screen_date, strikeouts=20),
            _log(screen_date + timedelta(days=1), strikeouts=20),
        ]

        shadow = build_recency_projection_shadow(logs, screen_date, None, 18.0)

        self.assertEqual(shadow.starts_available, 5)
        self.assertAlmostEqual(shadow.season_k_rate, 0.30, places=4)
        self.assertAlmostEqual(shadow.shadow_projected_batters_faced, 24.0, places=1)
        self.assertAlmostEqual(shadow.shadow_projected_strikeouts, 7.2, places=1)
        self.assertIn("RESEARCH_ONLY", shadow.flags)


if __name__ == "__main__":
    unittest.main()
