from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from mlb_props.sources.action_network import FANDUEL_SOURCE, parse_action_markets


FIXTURE = Path(__file__).parent / "fixtures" / "action_network_mlb_scoreboard.json"


class ActionNetworkGameLinesTests(unittest.TestCase):
    def test_parses_fanduel_game_markets_with_home_side_semantics(self) -> None:
        payload = json.loads(FIXTURE.read_text())
        markets, diagnostics = parse_action_markets(
            payload,
            date(2026, 9, 7),
            now=datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(len(markets), 1)
        self.assertEqual(next(iter(markets))[0:2], ("STL", "SF"))
        game = next(iter(markets.values()))
        self.assertEqual(game["source"], FANDUEL_SOURCE)
        self.assertEqual(game["moneyline"], {"line": None, "price_a": -134, "price_b": 114})
        self.assertEqual(game["spread"], {"line": -1.5, "price_a": 102, "price_b": -194})
        self.assertEqual(game["total"], {"line": 7.5, "price_a": -124, "price_b": 102})
        self.assertEqual(diagnostics["games_seen"], 3)
        self.assertEqual(diagnostics["started_games"], 1)
        self.assertEqual(diagnostics["wrong_date_games"], 1)
        self.assertEqual(diagnostics["games_with_complete_markets"], 1)

    def test_missing_fanduel_row_does_not_use_consensus(self) -> None:
        payload = json.loads(FIXTURE.read_text())
        payload["games"][0]["odds"] = [payload["games"][0]["odds"][0]]
        markets, diagnostics = parse_action_markets(
            payload,
            date(2026, 9, 7),
            now=datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(markets, {})
        self.assertEqual(diagnostics["games_with_fanduel"], 0)

    def test_fanduel_row_without_usable_prices_is_not_a_match(self) -> None:
        payload = json.loads(FIXTURE.read_text())
        payload["games"][0]["odds"][1] = {"book_id": 69}
        markets, diagnostics = parse_action_markets(
            payload,
            date(2026, 9, 7),
            now=datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(markets, {})
        self.assertEqual(diagnostics["games_with_fanduel"], 1)
        self.assertEqual(diagnostics["games_with_complete_markets"], 0)


if __name__ == "__main__":
    unittest.main()
