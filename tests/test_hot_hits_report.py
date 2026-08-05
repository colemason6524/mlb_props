from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hot_hits_report import (
    DELIVERED_CARD_POLICY,
    _latest_history_payloads,
    _parlay_summary_line,
    grade_history_files,
    parse_args,
    simulated_discord_card,
)
from mlb_props.hot_hits_policy import (
    CORE_FIRST_POLICY_VERSION,
    HOT_HITS_POLICY_VERSION,
)


class HotHitsReportArgumentTests(unittest.TestCase):
    def test_live_core_first_policy_is_the_default(self) -> None:
        with patch("sys.argv", ["hot_hits_report.py"]):
            args = parse_args()

        self.assertEqual(args.card_policy, CORE_FIRST_POLICY_VERSION)
        self.assertEqual(args.limit, 4)
        self.assertEqual(args.value_limit, 2)

    def test_core_first_policy_accepts_parlay_limits(self) -> None:
        with patch(
            "sys.argv",
            [
                "hot_hits_report.py",
                "--card-policy",
                CORE_FIRST_POLICY_VERSION,
                "--limit",
                "4",
                "--value-limit",
                "2",
            ],
        ):
            args = parse_args()

        self.assertEqual(args.card_policy, CORE_FIRST_POLICY_VERSION)
        self.assertEqual(args.limit, 4)
        self.assertEqual(args.value_limit, 2)


class HotHitsHistoryDiscoveryTests(unittest.TestCase):
    def test_latest_export_per_date_wins_across_nested_transfer_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "transferred"
            nested.mkdir()
            older = {
                "screen_date": "2026-07-20",
                "generated_at": "2026-07-20T15:00:00+00:00",
                "candidates": [{"batter_name": "Older"}],
            }
            newer = {
                "screen_date": "2026-07-20",
                "generated_at": "2026-07-20T16:00:00+00:00",
                "candidates": [{"batter_name": "Newer"}],
            }
            next_day = {
                "screen_date": "2026-07-21",
                "generated_at": "2026-07-21T15:00:00+00:00",
                "candidates": [{"batter_name": "Next Day"}],
            }
            older_path = root / "hot_hits_older.json"
            newer_path = nested / "hot_hits_newer.json"
            next_path = nested / "hot_hits_next.json"
            older_path.write_text(json.dumps(older))
            newer_path.write_text(json.dumps(newer))
            next_path.write_text(json.dumps(next_day))

            payloads = _latest_history_payloads(
                [older_path, newer_path, next_path]
            )

        self.assertEqual(
            [payload["candidates"][0]["batter_name"] for payload in payloads],
            ["Newer", "Next Day"],
        )


class DeliveredHotHitsPolicyTests(unittest.TestCase):
    def test_delivered_policy_uses_only_recorded_successful_delivery_rows(self) -> None:
        rows = [
            {"batter_name": "Core", "_delivered_rank": 1},
            {"batter_name": "Value", "_delivered_rank": 2},
            {"batter_name": "Not Sent", "_delivered_rank": None},
        ]

        selected = simulated_discord_card(
            rows,
            card_policy=DELIVERED_CARD_POLICY,
        )

        self.assertEqual(
            [row["batter_name"] for row in selected],
            ["Core", "Value"],
        )

    def test_history_grading_uses_only_successfully_delivered_snapshot(self) -> None:
        payload = {
            "screen_date": "2026-07-28",
            "generated_at": "2026-07-28T16:00:00+00:00",
            "candidates": [
                {"batter_id": 1, "batter_name": "Delivered", "team": "DET", "score": 14},
                {"batter_id": 2, "batter_name": "Not Delivered", "team": "CLE", "score": 14},
            ],
            "discord_delivery": {
                "status": "sent",
                "core": [
                    {
                        "rank": 1,
                        "batter_id": 1,
                        "batter_name": "Delivered",
                        "team": "DET",
                    }
                ],
                "optional_value": [],
                "thin": [],
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hot_hits_delivery.json"
            path.write_text(json.dumps(payload))
            with patch(
                "hot_hits_report.MlbStatsClient._load_teams",
                return_value={},
            ), patch(
                "hot_hits_report.MlbStatsClient.grade_batter",
                return_value={
                    "result": "HIT",
                    "hits": 1,
                    "at_bats": 4,
                    "plate_appearances": 4,
                    "game_state": "Final",
                },
            ):
                rows = grade_history_files(
                    [path],
                    card_policy=DELIVERED_CARD_POLICY,
                )

        delivered = next(row for row in rows if row.batter_name == "Delivered")
        not_delivered = next(row for row in rows if row.batter_name == "Not Delivered")
        self.assertTrue(delivered.discord_sim)
        self.assertEqual(delivered.discord_role, "Core")
        self.assertFalse(not_delivered.discord_sim)


class ConfidenceResearchHistoryTests(unittest.TestCase):
    @staticmethod
    def candidate(
        batter_id: int,
        batter_name: str,
        *,
        current_display_qualified: bool,
    ) -> dict:
        return {
            "batter_id": batter_id,
            "batter_name": batter_name,
            "team": "DET",
            "score": 18,
            "batting_order": 1,
            "avg_last_5": 0.400,
            "avg_last_10": 0.330,
            "season_avg": 0.290,
            "hit_games_last_5": 4,
            "hit_games_last_10": 8,
            "matchup_rating": 0.25,
            "pitcher_hits_allowed_rate_last_5": 0.280,
            "pitcher_hits_allowed_rate_season": 0.250,
            "pitcher_k_rate_last_5": 0.190,
            "pitcher_walk_rate_last_5": 0.070,
            "current_gate_qualified": current_display_qualified,
            "current_display_qualified": current_display_qualified,
            "gate_failures": (
                []
                if current_display_qualified
                else ["L5_HIT_GAMES_BELOW_CURRENT_GATE"]
            ),
            "confidence_estimate": {
                "version": "hot-hits-confidence-provisional-v1",
                "hit_probability": 0.754,
                "confidence_percentage": 75,
                "label": "SOLID",
                "reliability_weight": 0.85,
            },
        }

    def test_research_only_rows_cannot_enter_simulated_production_card(self) -> None:
        production = self.candidate(
            1,
            "Production Hitter",
            current_display_qualified=True,
        )
        research_only = self.candidate(
            2,
            "Research Hitter",
            current_display_qualified=False,
        )

        selected = simulated_discord_card([research_only, production])

        self.assertEqual(
            [row["batter_name"] for row in selected],
            ["Production Hitter"],
        )

    def test_grading_uses_new_research_pool_but_keeps_production_boundary(self) -> None:
        production = self.candidate(
            1,
            "Production Hitter",
            current_display_qualified=True,
        )
        research_only = self.candidate(
            2,
            "Research Hitter",
            current_display_qualified=False,
        )
        payload = {
            "screen_date": "2026-08-04",
            "generated_at": "2026-08-04T16:00:00+00:00",
            "candidates": [production],
            "confidence_research_pool": [production, research_only],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "hot_hits_confidence.json"
            path.write_text(json.dumps(payload))
            with patch(
                "hot_hits_report.MlbStatsClient._load_teams",
                return_value={},
            ), patch(
                "hot_hits_report.MlbStatsClient.grade_batter",
                return_value={
                    "result": "HIT",
                    "hits": 1,
                    "at_bats": 4,
                    "plate_appearances": 4,
                    "game_state": "Final",
                },
            ):
                rows = grade_history_files([path])

        self.assertEqual(len(rows), 2)
        production_row = next(
            row for row in rows if row.batter_name == "Production Hitter"
        )
        research_row = next(
            row for row in rows if row.batter_name == "Research Hitter"
        )
        self.assertTrue(production_row.discord_sim)
        self.assertTrue(production_row.current_display_qualified)
        self.assertFalse(research_row.discord_sim)
        self.assertFalse(research_row.current_display_qualified)
        self.assertEqual(research_row.tier, "Research")
        self.assertEqual(research_row.confidence_probability, 0.754)
        self.assertEqual(research_row.confidence_percentage, 75)


class HotHitsParlaySummaryTests(unittest.TestCase):
    @staticmethod
    def row(result: str) -> SimpleNamespace:
        return SimpleNamespace(result=result)

    def test_parlay_summary_treats_dnp_as_void_and_any_played_miss_as_loss(self) -> None:
        cards = [
            [self.row("HIT"), self.row("HIT")],
            [self.row("HIT"), self.row("DNP")],
            [self.row("HIT"), self.row("MISS")],
            [self.row("DNP")],
            [self.row("PENDING")],
        ]

        line = _parlay_summary_line("Example", cards)

        self.assertIn("Example: 2/3 cards (66.7%)", line)
        self.assertIn("loss 1", line)
        self.assertIn("DNP legs 2", line)
        self.assertIn("no action 1", line)
        self.assertIn("pending 1", line)
        self.assertIn("opportunities 5", line)


if __name__ == "__main__":
    unittest.main()
