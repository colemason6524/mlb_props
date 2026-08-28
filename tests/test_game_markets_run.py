from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mlb_props.models import Game
from run_game_markets import (
    _run,
    format_bovada_diagnostics,
    format_espn_diagnostics,
)


def _settings(tmp: Path, export_history: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        cache_ttl_hours=24,
        lines_cache_ttl_minutes=15,
        screen_date=date(2026, 8, 28),
        export_history=export_history,
        run_note="unit test empty coverage",
    )


def _slate_game() -> Game:
    return Game(
        game_id="1",
        game_date=date(2026, 8, 28),
        game_time=datetime(2026, 8, 28, 23, 5, tzinfo=timezone.utc),
        home_team="CHC",
        away_team="CIN",
        probable_home_pitcher="Home",
        probable_away_pitcher="Away",
        source="test",
        probable_home_pitcher_id=10,
        probable_away_pitcher_id=11,
    )


class EmptyCoverageRunTests(unittest.TestCase):
    def test_empty_snapshots_still_export_history_and_print_diagnostics(self) -> None:
        leftovers = {
            "coupon_fetch": {"mode": "fresh", "error": None, "attempts": 1},
            "events_seen": 5,
            "games_parsed": 5,
            "games_matched_to_slate": 0,
            "stale_games_filtered": 5,
            "wrong_date_games_filtered": 0,
            "empty_markets_filtered": 0,
            "stale_games": ["HOU @ NYY", "KC @ TOR"],
            "wrong_date_games": [],
            "unmatched_teams": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cache_dir = tmp_path / "cache"
            outputs_dir = tmp_path / "outputs"
            fake_slate = MagicMock()
            fake_slate.fetch_games.return_value = [_slate_game()]
            fake_espn = MagicMock()
            fake_espn.fetch_game_context.return_value = (
                {},
                {"mode": "fresh", "games_parsed": 13, "with_total": 13, "with_spreads": 13},
            )
            with (
                patch("run_game_markets.load_settings", return_value=_settings(tmp_path)),
                patch("run_game_markets.CACHE_DIR", cache_dir),
                patch("run_game_markets.OUTPUTS_DIR", outputs_dir),
                patch("run_game_markets.fetch_game_markets", return_value=({}, leftovers)),
                patch("run_game_markets.EspnMlbOddsSource", return_value=fake_espn),
                patch(
                    "mlb_props.sources.mlb_stats_api.MlbStatsApiSlateSource",
                    return_value=fake_slate,
                ),
            ):
                exit_code, message = _run()

            history_files = list((outputs_dir / "history").glob("game_markets_*.json"))
            self.assertEqual(exit_code, 0)
            self.assertIn("no game market snapshots collected", message)
            self.assertEqual(len(history_files), 1)
            payload = json.loads(history_files[0].read_text())
            self.assertEqual(payload["coverage"]["matched_with_lines"], 0)
            self.assertEqual(payload["coverage"]["slate_games"], 1)
            self.assertEqual(payload["games"], [])
            self.assertEqual(payload["unmatched_slate_game_names"], ["CIN @ CHC"])
            self.assertEqual(payload["source_diagnostics"]["bovada"]["stale_games_filtered"], 5)

    def test_format_bovada_diagnostics_includes_stale_labels(self) -> None:
        text = format_bovada_diagnostics(
            {
                "coupon_fetch": {"mode": "fresh", "error": None},
                "events_seen": 5,
                "games_parsed": 5,
                "games_matched_to_slate": 0,
                "stale_games_filtered": 5,
                "wrong_date_games_filtered": 0,
                "empty_markets_filtered": 0,
                "stale_games": ["HOU @ NYY"],
                "unmatched_teams": [],
            }
        )
        self.assertIn("stale=5", text)
        self.assertIn("current-day=0", text)
        self.assertIn("HOU @ NYY", text)

    def test_format_espn_diagnostics_includes_mode(self) -> None:
        text = format_espn_diagnostics({"mode": "fresh", "games_parsed": 13, "with_total": 13, "with_spreads": 13})
        self.assertIn("mode=fresh", text)
        self.assertIn("parsed=13", text)



if __name__ == "__main__":
    unittest.main()
