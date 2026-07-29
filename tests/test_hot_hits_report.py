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
