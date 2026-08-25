from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from mlb_props.sources.bovada_mlb import (
    BovadaGame,
    normalize_team_name,
    parse_games,
)


def _market(description: str, outcomes: list[dict]) -> dict:
    return {
        "description": description,
        "status": "O",
        "period": {"description": "game", "main": True},
        "outcomes": outcomes,
    }


def _outcome(name: str, american: int | str, handicap: float | None = None) -> dict:
    price: dict = {"american": american, "decimal": 1.9}
    if handicap is not None:
        price["handicap"] = handicap
    return {"description": name, "price": price}


def _event() -> dict:
    return {
        "id": 18001,
        "link": "/baseball/mlb/new-york-yankees-boston-red-sox-20260825",
        "description": "New York Yankees @ Boston Red Sox",
        "startTime": int(datetime(2026, 8, 25, 23, 5, tzinfo=timezone.utc).timestamp() * 1000),
        "displayGroups": [
            {
                "markets": [
                    _market(
                        "Moneyline",
                        [_outcome("Boston Red Sox", -150), _outcome("New York Yankees", 130)],
                    ),
                    _market(
                        "Point Spread",
                        [
                            _outcome("Boston Red Sox", -125, handicap=-1.5),
                            _outcome("New York Yankees", 105, handicap=1.5),
                        ],
                    ),
                    _market(
                        "Total",
                        [_outcome("Over", -110, handicap=8.5), _outcome("Under", -110, handicap=8.5)],
                    ),
                ]
            }
        ],
    }


class NormalizeTeamNameTests(unittest.TestCase):
    def test_full_names_map_to_abbrs(self) -> None:
        self.assertEqual(normalize_team_name("New York Yankees"), "NYY")
        self.assertEqual(normalize_team_name("Chicago White Sox"), "CWS")
        self.assertEqual(normalize_team_name("St. Louis Cardinals"), "STL")
        self.assertEqual(normalize_team_name("Athletics"), "ATH")

    def test_unknown_team_returns_none(self) -> None:
        self.assertIsNone(normalize_team_name("Mars Martians"))


class ParseGamesTests(unittest.TestCase):
    def test_parses_all_three_markets(self) -> None:
        games, diagnostics = parse_games([{"events": [_event()]}])
        self.assertEqual(diagnostics["events_seen"], 1)
        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertIsInstance(game, BovadaGame)
        self.assertEqual((game.away_abbr, game.home_abbr), ("NYY", "BOS"))
        self.assertEqual(game.markets["moneyline"].price_a, -150)
        self.assertEqual(game.markets["moneyline"].price_b, 130)
        self.assertIsNone(game.markets["moneyline"].line)
        self.assertEqual(game.markets["spread"].line, -1.5)
        self.assertEqual(game.markets["total"].line, 8.5)

    def test_unmatched_teams_are_diagnosed(self) -> None:
        payload = [{"events": [{"description": "Mars Martians @ Moon Men"}]}]
        games, diagnostics = parse_games(payload)
        self.assertEqual(games, [])
        self.assertEqual(len(diagnostics["unmatched_teams"]), 1)

    def test_market_without_both_sides_is_skipped(self) -> None:
        event = _event()
        event["displayGroups"][0]["markets"][0]["outcomes"] = [_outcome("Boston Red Sox", -150)]
        games, _ = parse_games([{"events": [event]}])
        game = games[0]
        self.assertNotIn("moneyline", game.markets)

    def test_even_price_parses_to_100(self) -> None:
        event = _event()
        event["displayGroups"][0]["markets"] = [
            _market("Moneyline", [_outcome("Boston Red Sox", "EVEN"), _outcome("New York Yankees", -120)])
        ]
        games, _ = parse_games([{"events": [event]}])
        self.assertEqual(games[0].markets["moneyline"].price_a, 100)

    def test_spread_without_handicap_is_skipped(self) -> None:
        event = _event()
        event["displayGroups"][0]["markets"][1]["outcomes"] = [
            _outcome("Boston Red Sox", -125),
            _outcome("New York Yankees", 105),
        ]
        games, _ = parse_games([{"events": [event]}])
        self.assertNotIn("spread", games[0].markets)


if __name__ == "__main__":
    unittest.main()
