from __future__ import annotations

import unittest
from dataclasses import fields
from datetime import date
from pathlib import Path
from unittest.mock import patch

import backtest


class HistoryDirectoryTests(unittest.TestCase):
    def test_history_dir_defaults_to_outputs_history(self) -> None:
        self.assertEqual(
            backtest._history_dir_from_args([]),
            backtest.OUTPUTS_DIR / "history",
        )

    def test_history_dir_accepts_separate_argument(self) -> None:
        self.assertEqual(
            backtest._history_dir_from_args(["--history-dir", "/tmp/pitcher-history"]),
            Path("/tmp/pitcher-history"),
        )

    def test_history_dir_accepts_equals_argument(self) -> None:
        self.assertEqual(
            backtest._history_dir_from_args(["--history-dir=/tmp/pitcher-history"]),
            Path("/tmp/pitcher-history"),
        )

    def test_history_dir_requires_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a directory path"):
            backtest._history_dir_from_args(["--history-dir"])


class CandidateTierTests(unittest.TestCase):
    def test_exported_buckets_retain_tier_labels(self) -> None:
        payload = {
            "displayed_candidates": [{"subject_name": "Core Pitcher"}],
            "lean_candidates": [{"subject_name": "Lean Pitcher"}],
            "watch_candidates": [{"subject_name": "Watch Pitcher"}],
        }
        with patch.object(backtest.sys, "argv", ["backtest.py", "--include-watch"]):
            rows = backtest._candidate_rows_from_payload(payload)
        self.assertEqual(
            [row["history_tier"] for row in rows],
            ["core", "lean", "watch"],
        )

    def test_legacy_candidates_are_unclassified(self) -> None:
        rows = backtest._candidate_rows_from_payload(
            {"candidates": [{"subject_name": "Legacy Pitcher"}]}
        )
        self.assertEqual(rows[0]["history_tier"], "unclassified")


class ProjectionEdgeTests(unittest.TestCase):
    def test_projection_edge_bands_cover_policy_boundaries(self) -> None:
        self.assertEqual(backtest._projection_edge_band(0.99), "<1.00")
        self.assertEqual(backtest._projection_edge_band(1.0), "1.00-1.24")
        self.assertEqual(backtest._projection_edge_band(1.25), "1.25-1.49")
        self.assertEqual(backtest._projection_edge_band(1.5), "1.50-1.99")
        self.assertEqual(backtest._projection_edge_band(2.0), "2.0+")


class OpportunityShadowCompatibilityTests(unittest.TestCase):
    def test_legacy_prediction_without_shadow_data_is_supported(self) -> None:
        self.assertEqual(
            backtest._opportunity_shadow_from_prediction(
                {"subject_name": "Legacy Pitcher"}
            ),
            {},
        )

    def test_nested_shadow_profile_is_loaded(self) -> None:
        profile = {
            "shadow_projected_outs": 17.2,
            "opportunity_confidence": "MEDIUM",
        }
        self.assertEqual(
            backtest._opportunity_shadow_from_prediction(
                {"opportunity_shadow": profile}
            ),
            profile,
        )

    def test_shadow_report_compares_current_and_experimental_error(self) -> None:
        values = {field.name: None for field in fields(backtest.ResolvedPrediction)}
        values.update(
            {
                "screen_date": date(2026, 7, 10),
                "tier": "watch",
                "model_version": "test",
                "history_schema_version": 3,
                "pitcher_name": "Test Pitcher",
                "team": "DET",
                "opponent": "KC",
                "prop_type": "PITCHER_STRIKEOUTS",
                "side": "OVER",
                "line": 5.5,
                "score": 2,
                "flags": [],
                "actual": 6.0,
                "actual_outs": 15,
                "actual_pitches": 90,
                "actual_batters_faced": 24,
                "projected_outs": 18.0,
                "projected_batters_faced": 26.0,
                "shadow_pitch_budget": 95.0,
                "shadow_projected_outs": 16.0,
                "shadow_projected_batters_faced": 24.5,
                "opportunity_confidence": "HIGH",
                "opportunity_flags": ["STABLE_ROLE"],
                "outcome": "win",
            }
        )
        row = backtest.ResolvedPrediction(**values)

        rendered = "\n".join(
            backtest._render_shadow_opportunity_diagnostics(
                [row],
                min_flag_samples=1,
            )
        )

        self.assertIn("Outs MAE, current vs shadow: 3.00 vs 1.00 (-2.00)", rendered)
        self.assertIn("BF MAE, current vs shadow: 2.00 vs 0.50 (-1.50)", rendered)
        self.assertIn("HIGH: 1 plays", rendered)
        self.assertIn("STABLE_ROLE: 1 plays", rendered)


class ConfidenceCompatibilityTests(unittest.TestCase):
    def test_legacy_prediction_without_confidence_is_supported(self) -> None:
        self.assertEqual(backtest._confidence_from_prediction({"subject_name": "Legacy"}), {})

    def test_confidence_report_shows_forecast_and_observed_rate(self) -> None:
        rows = []
        for outcome in ("win", "loss"):
            values = {field.name: None for field in fields(backtest.ResolvedPrediction)}
            values.update(
                {
                    "screen_date": date(2026, 8, 1),
                    "pitcher_name": "Test Pitcher",
                    "outcome": outcome,
                    "provisional_win_probability": 0.60,
                    "confidence_percentage": 60,
                }
            )
            rows.append(backtest.ResolvedPrediction(**values))

        rendered = "\n".join(backtest._render_confidence_calibration(rows))

        self.assertIn("Graded estimates: 2", rendered)
        self.assertIn("60%+: 2 plays", rendered)
        self.assertIn("avg forecast 60.0%", rendered)
        self.assertIn("observed 50.0%", rendered)


class RecencyShadowCompatibilityTests(unittest.TestCase):
    def test_legacy_prediction_without_recency_shadow_is_supported(self) -> None:
        self.assertEqual(
            backtest._recency_shadow_from_prediction({"subject_name": "Legacy"}),
            {},
        )

    def test_recency_report_compares_projection_error_and_brier_score(self) -> None:
        values = {field.name: None for field in fields(backtest.ResolvedPrediction)}
        values.update(
            {
                "screen_date": date(2026, 8, 1),
                "pitcher_name": "Test Pitcher",
                "actual": 6.0,
                "actual_batters_faced": 24,
                "projected_batters_faced": 27.0,
                "projected_strikeouts": 8.0,
                "recency_shadow_version": "recency-shadow-v1",
                "recency_shadow_projected_batters_faced": 24.5,
                "recency_shadow_projected_strikeouts": 6.5,
                "outcome": "win",
                "provisional_win_probability": 0.55,
                "recency_shadow_win_probability": 0.60,
            }
        )
        row = backtest.ResolvedPrediction(**values)

        rendered = "\n".join(backtest._render_recency_shadow_diagnostics([row]))

        self.assertIn("Active Ks: bias -2.00, MAE 2.00", rendered)
        self.assertIn("Shadow Ks: bias -0.50, MAE 0.50", rendered)
        self.assertIn("Active BF MAE: 3.00", rendered)
        self.assertIn("Shadow BF MAE: 0.50", rendered)
        self.assertIn("active 0.202, shadow 0.160", rendered)

    def test_l5_audit_separates_outcome_bands(self) -> None:
        values = {field.name: None for field in fields(backtest.ResolvedPrediction)}
        values.update(
            {
                "pitcher_name": "Cold Recent Pitcher",
                "hits_last_5": 1,
                "played_last_5": 5,
                "outcome": "win",
                "actual": 7.0,
                "actual_outs": 18,
                "actual_batters_faced": 25,
                "projected_strikeouts": 6.0,
                "recency_shadow_projected_strikeouts": 6.5,
                "provisional_win_probability": 0.56,
            }
        )

        rendered = "\n".join(
            backtest._render_l5_recency_diagnostics(
                [backtest.ResolvedPrediction(**values)]
            )
        )

        self.assertIn("0-1/5: 1 plays, 100.0% hit", rendered)
        self.assertIn("active K MAE 1.00", rendered)
        self.assertIn("recency-shadow K MAE 0.50", rendered)


class DailyCardSectionTests(unittest.TestCase):
    def _resolved(self, **overrides):
        values = {field.name: None for field in fields(backtest.ResolvedPrediction)}
        values.update(
            {
                "screen_date": date(2026, 9, 1),
                "tier": "daily_card",
                "pitcher_name": "Card Pitcher",
                "team": "CLE",
                "opponent": "DET",
                "prop_type": "PITCHER_STRIKEOUTS",
                "side": "UNDER",
                "line": 5.5,
                "score": 3,
                "actual": 4.0,
                "actual_outs": 15,
                "actual_pitches": 82,
                "actual_batters_faced": 20,
                "projected_strikeouts": 4.8,
                "outcome": "win",
                "edge": -1.5,
            }
        )
        values.update(overrides)
        return backtest.ResolvedPrediction(**values)

    def test_daily_card_rows_from_payload_are_marked(self) -> None:
        rows = backtest._daily_card_rows_from_payload(
            {"daily_card": [{"subject_name": "Card Pitcher", "card_rank": 1}]}
        )

        self.assertEqual(rows[0]["history_tier"], "daily_card")

    def test_section_reports_hit_rate_and_units(self) -> None:
        card_predictions = [{"screen_date": "2026-09-01", "subject_name": "Card Pitcher"}]
        resolved = [
            self._resolved(),
            self._resolved(pitcher_name="Second Card", outcome="loss", actual=7.0, line=5.5),
        ]

        lines = backtest._render_daily_card_section(card_predictions, resolved, [], [])

        text = "\n".join(lines)
        self.assertIn("Daily Card (pre-registered research policy; not Core/Lean/Watch):", text)
        self.assertIn("Card plays graded: 2 (1-1)", text)
        self.assertIn("Card hit rate: 50.0%", text)
        self.assertIn("Card flat units at -110: -0.09", text)
        self.assertIn("Card Pitcher (CLE vs DET) UNDER 5.5 | proj 4.8 | actual 4 | win", text)

    def test_section_skips_when_no_card_predictions(self) -> None:
        self.assertEqual(backtest._render_daily_card_section([], [], [], []), [])

    def test_void_and_unresolved_carry_tier_defaults(self) -> None:
        void_kwargs = dict(
            screen_date=date(2026, 9, 1),
            pitcher_name="X",
            team="A",
            opponent="B",
            prop_type="PITCHER_STRIKEOUTS",
            side="UNDER",
            line=5.5,
        )
        self.assertEqual(
            backtest.VoidPrediction(reason="void_no_start", tier="daily_card", **void_kwargs).tier,
            "daily_card",
        )
        self.assertEqual(
            backtest.UnresolvedPrediction(reason="no_logs_loaded", **void_kwargs).tier,
            "unclassified",
        )
        self.assertEqual(
            backtest.UnresolvedPrediction(reason="no_logs_loaded", tier="daily_card", **void_kwargs).tier,
            "daily_card",
        )


if __name__ == "__main__":
    unittest.main()
