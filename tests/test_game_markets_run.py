from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from mlb_props.models import Game
from mlb_props.sources.action_network import FANDUEL_SOURCE
from run_game_markets import (
    _run,
    build_snapshot,
    format_action_diagnostics,
    format_bovada_diagnostics,
    market_entry_for_game,
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
            with (
                patch("run_game_markets.load_settings", return_value=_settings(tmp_path)),
                patch("run_game_markets.CACHE_DIR", cache_dir),
                patch("run_game_markets.OUTPUTS_DIR", outputs_dir),
                patch("run_game_markets.fetch_game_markets", return_value=({}, leftovers)),
                patch(
                    "run_game_markets.fetch_action_markets",
                    return_value=({}, {"mode": "fresh", "games_seen": 1}),
                ),
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

    def test_format_action_diagnostics_includes_mode(self) -> None:
        text = format_action_diagnostics(
            {"mode": "fresh", "games_seen": 13, "pregame_games": 10, "games_with_fanduel": 10}
        )
        self.assertIn("mode=fresh", text)
        self.assertIn("games=13", text)

    def test_action_fanduel_is_labeled_fallback_when_bovada_is_empty(self) -> None:
        action_entry = {
            "source": FANDUEL_SOURCE,
            "start_time_utc": "2026-08-28T23:05:00+00:00",
            "moneyline": {"line": None, "price_a": -125, "price_b": 105},
            "spread": {"line": -1.5, "price_a": 155, "price_b": -180},
            "total": {"line": 8.5, "price_a": -110, "price_b": -110},
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_slate = MagicMock()
            fake_slate.fetch_games.return_value = [_slate_game()]
            with (
                patch("run_game_markets.load_settings", return_value=_settings(tmp_path)),
                patch("run_game_markets.CACHE_DIR", tmp_path / "cache"),
                patch("run_game_markets.OUTPUTS_DIR", tmp_path / "outputs"),
                patch("run_game_markets.fetch_game_markets", return_value=({}, {})),
                patch(
                    "run_game_markets.fetch_action_markets",
                    return_value=(
                        {("CIN", "CHC", action_entry["start_time_utc"]): action_entry},
                        {"mode": "fresh"},
                    ),
                ),
                patch(
                    "mlb_props.sources.mlb_stats_api.MlbStatsApiSlateSource",
                    return_value=fake_slate,
                ),
            ):
                exit_code, _ = _run()

            history_file = next((tmp_path / "outputs" / "history").glob("game_markets_*.json"))
            payload = json.loads(history_file.read_text())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["history_schema_version"], 2)
        self.assertEqual(payload["coverage"]["action_fanduel_fallback"], 1)
        self.assertEqual(payload["games"][0]["source"], FANDUEL_SOURCE)
        self.assertIsNone(payload["games"][0]["cross_check"]["source"])

    def test_bovada_remains_primary_and_action_is_cross_check(self) -> None:
        bovada = {
            "source": "bovada",
            "start_time_utc": "2026-08-28T23:05:00+00:00",
            "moneyline": {"line": None, "price_a": -120, "price_b": 100},
            "spread": {"line": -1.5, "price_a": 150, "price_b": -175},
            "total": {"line": 8.0, "price_a": -105, "price_b": -115},
        }
        action = {
            "source": FANDUEL_SOURCE,
            "source_updated_at": "2026-08-28T20:00:00+00:00",
            "moneyline": {"line": None, "price_a": -125, "price_b": 105},
            "spread": {"line": -1.5, "price_a": 155, "price_b": -180},
            "total": {"line": 8.5, "price_a": -110, "price_b": -110},
        }
        snapshot = build_snapshot(_slate_game(), bovada, action)
        # Bovada primary remains for moneyline/spread; whole total swaps to FanDuel half.
        self.assertEqual(snapshot.source, "bovada")
        self.assertEqual(snapshot.total.line, 8.5)
        self.assertEqual(snapshot.total.price_a, -110)
        self.assertEqual(snapshot.total_source, FANDUEL_SOURCE)
        self.assertEqual(snapshot.total_selection_reason, "fanduel_nonwhole_total_preferred")
        # Bovada whole total preserved as cross-check for auditability.
        self.assertEqual(snapshot.cross_check_source, FANDUEL_SOURCE)
        self.assertEqual(snapshot.cross_check_total.line, 8.0)
        self.assertEqual(snapshot.cross_check_updated_at, "2026-08-28T20:00:00+00:00")
        # Moneyline/spread stay bovada
        self.assertEqual(snapshot.moneyline.price_a, -120)
        self.assertEqual(snapshot.spread.price_a, 150)

    def test_whole_bovada_kept_when_fanduel_also_whole(self) -> None:
        bovada = {
            "source": "bovada",
            "start_time_utc": "2026-08-28T23:05:00+00:00",
            "total": {"line": 8.0, "price_a": -105, "price_b": -115},
        }
        action = {
            "source": FANDUEL_SOURCE,
            "total": {"line": 8.0, "price_a": -110, "price_b": -110},
        }
        snapshot = build_snapshot(_slate_game(), bovada, action)
        self.assertEqual(snapshot.total.line, 8.0)
        self.assertEqual(snapshot.total_source, "bovada")
        self.assertEqual(snapshot.total_selection_reason, "bovada_total_selected")
        self.assertEqual(snapshot.cross_check_total.line, 8.0)

    def test_half_bovada_not_swapped_even_if_fanduel_differs(self) -> None:
        bovada = {
            "source": "bovada",
            "start_time_utc": "2026-08-28T23:05:00+00:00",
            "total": {"line": 8.5, "price_a": -105, "price_b": -115},
        }
        action = {
            "source": FANDUEL_SOURCE,
            "total": {"line": 7.5, "price_a": -110, "price_b": -110},
        }
        snapshot = build_snapshot(_slate_game(), bovada, action)
        self.assertEqual(snapshot.total.line, 8.5)
        self.assertEqual(snapshot.total_source, "bovada")
        self.assertEqual(snapshot.total_selection_reason, "bovada_total_selected")

    def test_whole_bovada_kept_when_fanduel_incomplete(self) -> None:
        bovada = {
            "source": "bovada",
            "start_time_utc": "2026-08-28T23:05:00+00:00",
            "total": {"line": 8.0, "price_a": -105, "price_b": -115},
        }
        action = {
            "source": FANDUEL_SOURCE,
            "total": {"line": 8.5, "price_a": -110, "price_b": None},
        }
        snapshot = build_snapshot(_slate_game(), bovada, action)
        self.assertEqual(snapshot.total.line, 8.0)
        self.assertEqual(snapshot.total_source, "bovada")
        self.assertEqual(snapshot.total_selection_reason, "bovada_total_selected")

    def test_fanduel_fallback_total_when_bovada_missing(self) -> None:
        bovada = {
            "source": "bovada",
            "start_time_utc": "2026-08-28T23:05:00+00:00",
            "moneyline": {"line": None, "price_a": -120, "price_b": 100},
        }
        action = {
            "source": FANDUEL_SOURCE,
            "total": {"line": 8.5, "price_a": -110, "price_b": -110},
        }
        snapshot = build_snapshot(_slate_game(), bovada, action)
        # Bovada has no total, but selection logic promotes complete FanDuel half-total.
        self.assertEqual(snapshot.total.line, 8.5)
        self.assertEqual(snapshot.total_source, FANDUEL_SOURCE)
        self.assertIn(snapshot.total_selection_reason, ("fanduel_fallback_total", "fanduel_nonwhole_total_preferred"))

    def test_total_selection_never_mixes_line_and_prices(self) -> None:
        from mlb_props.game_markets import TwoWayPrice, select_total_market

        bovada = TwoWayPrice(line=8.0, price_a=-105, price_b=-115)
        fanduel = TwoWayPrice(line=8.5, price_a=-100, price_b=-122)
        selected, src, _, reason = select_total_market(bovada, "bovada", None, fanduel, FANDUEL_SOURCE, None)
        self.assertEqual(reason, "fanduel_nonwhole_total_preferred")
        self.assertEqual(selected.line, 8.5)
        self.assertEqual(selected.price_a, -100)
        self.assertEqual(selected.price_b, -122)
        # Ensure not mixed: fanduel line with bovada prices would be 8.5 with -105/-115.
        self.assertNotEqual((selected.price_a, selected.price_b), (bovada.price_a, bovada.price_b))

    def test_doubleheader_market_is_matched_by_nearest_start_time(self) -> None:
        game = _slate_game()
        early = {"start_time_utc": "2026-08-28T17:05:00+00:00", "source": "bovada"}
        late = {"start_time_utc": "2026-08-28T23:04:00+00:00", "source": "bovada"}
        markets = {
            ("CIN", "CHC", early["start_time_utc"]): early,
            ("CIN", "CHC", late["start_time_utc"]): late,
        }
        self.assertIs(market_entry_for_game(markets, game), late)



if __name__ == "__main__":
    unittest.main()
