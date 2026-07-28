from __future__ import annotations

import unittest
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


if __name__ == "__main__":
    unittest.main()
