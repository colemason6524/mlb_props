from __future__ import annotations

import unittest
from dataclasses import asdict
from datetime import date, timedelta

from mlb_props.models import PitcherGameLog
from mlb_props.opportunity import build_opportunity_shadow
from mlb_props.version import PITCHER_OPPORTUNITY_SHADOW_VERSION


def _log(
    game_date: date,
    pitches: int,
    outs: int,
    batters_faced: int,
    *,
    did_start: bool = True,
) -> PitcherGameLog:
    return PitcherGameLog(
        pitcher_name_raw="Test Pitcher",
        pitcher_name_norm="test pitcher",
        game_date=game_date,
        team="DET",
        opponent="KC",
        hand="R",
        outs_recorded=outs,
        strikeouts=6,
        pitches_thrown=pitches,
        batters_faced=batters_faced,
        walks=2,
        hits_allowed=5,
        earned_runs=2,
        did_start=did_start,
        source="test",
        pitcher_id=1,
    )


class OpportunityShadowTests(unittest.TestCase):
    def test_uses_only_pregame_starts_and_retains_role_warning(self) -> None:
        screen_date = date(2026, 7, 10)
        logs = [
            _log(screen_date, 120, 24, 30),
            _log(date(2026, 7, 5), 96, 18, 24),
            _log(date(2026, 7, 4), 20, 3, 5, did_start=False),
            _log(date(2026, 6, 30), 90, 17, 23),
            _log(date(2026, 6, 25), 84, 15, 22),
        ]

        shadow = build_opportunity_shadow(logs, screen_date)

        self.assertEqual(
            shadow.recent_start_dates,
            ["2026-07-05", "2026-06-30", "2026-06-25"],
        )
        self.assertEqual(shadow.pitch_counts_last_3, [96, 90, 84])
        self.assertEqual(shadow.days_since_last_start, 5)
        self.assertEqual(shadow.shadow_pitch_budget, 91.8)
        self.assertIn("MIXED_RECENT_ROLE", shadow.flags)
        self.assertEqual(shadow.opportunity_confidence, "LOW")

    def test_stable_five_start_workload_is_high_confidence(self) -> None:
        screen_date = date(2026, 7, 10)
        logs = [
            _log(
                screen_date - timedelta(days=5 * (index + 1)),
                pitches=96 - index,
                outs=18,
                batters_faced=24,
            )
            for index in range(5)
        ]

        shadow = build_opportunity_shadow(logs, screen_date)

        self.assertEqual(shadow.starts_available, 5)
        self.assertEqual(shadow.pitch_count_trend, "STABLE")
        self.assertEqual(shadow.outs_trend, "STABLE")
        self.assertEqual(shadow.opportunity_confidence, "HIGH")
        self.assertEqual(shadow.flags, [])
        self.assertIsNotNone(shadow.shadow_projected_batters_faced)
        self.assertIsNotNone(shadow.shadow_projected_outs)

    def test_no_pregame_starts_returns_auditable_low_confidence_profile(self) -> None:
        screen_date = date(2026, 7, 10)
        shadow = build_opportunity_shadow(
            [_log(screen_date, 95, 18, 24)],
            screen_date,
        )

        self.assertEqual(shadow.starts_available, 0)
        self.assertEqual(shadow.opportunity_confidence, "LOW")
        self.assertEqual(shadow.flags, ["NO_START_DATA"])
        self.assertIsNone(shadow.shadow_projected_outs)

    def test_profile_serializes_for_history_export(self) -> None:
        screen_date = date(2026, 7, 10)
        shadow = build_opportunity_shadow(
            [_log(date(2026, 7, 5), 95, 18, 24)],
            screen_date,
        )

        payload = asdict(shadow)
        self.assertEqual(payload["version"], PITCHER_OPPORTUNITY_SHADOW_VERSION)
        self.assertEqual(payload["screen_date"], "2026-07-10")
        self.assertIn("shadow_projected_outs", payload)


if __name__ == "__main__":
    unittest.main()
