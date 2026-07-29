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


if __name__ == "__main__":
    unittest.main()
