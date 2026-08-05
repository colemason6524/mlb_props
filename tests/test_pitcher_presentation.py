from __future__ import annotations

import unittest

from mlb_props.models import Candidate, OpportunityShadow
from mlb_props.output import render_candidates, render_pitcher_props_discord_embeds
from mlb_props.pitcher_presentation import (
    build_pitcher_presentations,
    display_rankings_payload,
)


def _shadow(confidence: str) -> OpportunityShadow:
    return OpportunityShadow(
        version="opportunity-shadow-v1",
        screen_date="2026-08-02",
        starts_available=10,
        recent_start_dates=["2026-07-28", "2026-07-22", "2026-07-16"],
        pitch_counts_last_3=[98, 96, 95],
        outs_last_3=[18, 18, 17],
        batters_faced_last_3=[24, 25, 23],
        pitches_per_bf_last_3=[4.083, 3.84, 4.13],
        days_since_last_start=5,
        avg_pitch_count_last_3=96.3,
        max_pitch_count_last_5=100,
        pitch_count_trend="STABLE",
        pitch_count_trend_delta=2.5,
        outs_trend="STABLE",
        outs_trend_delta=0.5,
        pitch_count_volatility_last_5=3.0,
        outs_volatility_last_5=1.0,
        short_starts_last_5=0,
        shadow_pitch_budget=97.0,
        shadow_projected_batters_faced=24.5,
        shadow_projected_outs=18.0,
        opportunity_confidence=confidence,
        flags=[] if confidence == "HIGH" else ["RECENT_SHORT_START"],
    )


def _candidate(
    name: str,
    *,
    side: str = "OVER",
    line: float = 5.5,
    projected: float = 6.8,
    score: int = 4,
    reliability: str = "HIGH",
) -> Candidate:
    return Candidate(
        subject_name=name,
        subject_id=1,
        subject_role="pitcher",
        team="DET",
        opponent="KC",
        hand="R",
        prop_type="PITCHER_STRIKEOUTS",
        side=side,
        line=line,
        bookmaker="test",
        hits_last_5=4,
        played_last_5=5,
        hits_last_10=6,
        played_last_10=10,
        avg_last_5=6.4,
        avg_last_10=6.0,
        median_last_5=6.0,
        median_last_10=6.0,
        season_avg=5.8,
        delta_avg_last_5=1.2,
        delta_avg_last_10=1.0,
        avg_pitch_count_last_5=96.0,
        avg_pitch_count_last_10=94.0,
        avg_outs_last_5=18.0,
        avg_outs_last_10=17.5,
        avg_k_rate_last_5=0.28,
        avg_walk_rate_last_5=0.06,
        avg_earned_runs_last_5=2.0,
        quality_starts_last_10=6,
        short_starts_last_10=0,
        workload_stability=0.8,
        matchup_rating=0.2,
        projected_outs=18.5,
        projected_batters_faced=24.5,
        projected_k_rate=0.28,
        projected_strikeouts=projected,
        score=score,
        flags=["EDGE_PLUS", "K_EFF"],
        opportunity_shadow=_shadow(reliability),
    )


class PitcherPresentationTests(unittest.TestCase):
    def test_no_core_preserves_tiers_and_marks_only_top_three_best_available(self) -> None:
        candidates = [
            _candidate("Lean One", score=5),
            _candidate("Watch Two", score=3),
            _candidate("Watch Three", score=2),
            _candidate("Watch Four", score=1),
            _candidate("Below Floor", score=-1),
        ]

        rows = build_pitcher_presentations(
            candidates,
            min_score=7,
            lean_min_score=4,
            watch_min_score=0,
        )

        self.assertEqual([row.slate_rank for row in rows], [1, 2, 3, 4])
        self.assertEqual([row.recommendation_tier for row in rows], ["lean", "watch", "watch", "watch"])
        self.assertEqual(
            [row.display_role for row in rows],
            ["best_available", "best_available", "best_available", "watch"],
        )
        self.assertNotIn("Below Floor", [row.candidate.subject_name for row in rows])

        payload = display_rankings_payload(rows)
        self.assertEqual(payload[0]["recommendation_tier"], "lean")
        self.assertEqual(payload[0]["display_role"], "best_available")
        self.assertEqual(payload[0]["signal_balance"], 5)
        self.assertEqual(payload[0]["confidence_model_version"], "pitcher-confidence-provisional-v1")
        self.assertFalse(payload[0]["price_included"])

    def test_console_uses_side_aligned_edge_and_explains_signal_semantics(self) -> None:
        lean = _candidate("Lean One", score=4)
        under = _candidate(
            "Under Watch",
            side="UNDER",
            line=5.5,
            projected=4.1,
            score=2,
            reliability="MEDIUM",
        )

        rendered = render_candidates(
            [under, lean],
            min_score=7,
            lean_min_score=4,
            watch_min_score=0,
        )

        self.assertIn("No Core plays cleared the absolute standard", rendered)
        self.assertIn("Best Available (Lean/Watch; not Core)", rendered)
        self.assertIn("Rank", rendered)
        self.assertIn("Signal", rendered)
        self.assertIn("Work Rel", rendered)
        self.assertIn("+1.40", rendered)
        self.assertIn("price-agnostic estimated chance", rendered)
        self.assertIn("not yet calibrated", rendered)
        self.assertLess(rendered.index("Under Watch"), rendered.index("Lean One"))

    def test_discord_ranks_best_available_without_promoting_it_to_core(self) -> None:
        lean = _candidate("Lean One", score=4)
        watch = _candidate("Watch Two", score=2, reliability="MEDIUM")

        embed = render_pitcher_props_discord_embeds(
            [watch, lean],
            screen_date="2026-08-02",
            games_count=15,
            prop_line_count=27,
            coverage_status="ok",
            min_score=7,
            lean_min_score=4,
            watch_min_score=0,
        )[0]

        self.assertEqual(embed["fields"][0]["name"], "Core Standard")
        self.assertIn("not promoted to Core", embed["fields"][0]["value"])
        self.assertIn("% | Best Available (Lean) |", embed["fields"][1]["name"])
        self.assertIn("% | Best Available (Watch) |", embed["fields"][2]["name"])
        self.assertIn("Side edge `+1.3`", embed["fields"][1]["value"])
        self.assertIn("Workload `HIGH`", embed["fields"][1]["value"])
        self.assertIn("provisional and excludes sportsbook price", embed["footer"]["text"])
        self.assertNotIn("Signal balance", embed["fields"][1]["value"])
        self.assertNotIn("Score", str(embed))

    def test_core_remains_absolute_and_ranked_first(self) -> None:
        core = _candidate("Core One", projected=7.0, score=8)
        lean = _candidate("Lean Two", score=4)

        rows = build_pitcher_presentations(
            [lean, core],
            min_score=7,
            lean_min_score=4,
            watch_min_score=0,
        )

        self.assertEqual(rows[0].recommendation_tier, "core")
        self.assertEqual(rows[0].display_role, "core")
        self.assertNotIn("best_available", [row.display_role for row in rows])


if __name__ == "__main__":
    unittest.main()
