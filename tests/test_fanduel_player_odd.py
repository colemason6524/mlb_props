from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from mlb_props.cache import JsonCache
from mlb_props.config import PITCHER_STRIKEOUTS
from mlb_props.models import Game
from mlb_props.sources.fanduel import (
    MlbFanDuelSource,
    _over_under_prices_from_player_props,
    _parse_player_odd,
)
from mlb_props.utils import normalize_name


# July 1 2026 FanDuel team-page PlayerOdd pairs (same marketId, consecutive
# selectionIds). HTML player-strikeout-over-odd-button is index 0; under is 1.
JULY_AZ_GALLEN_PROPS = [
    {
        "__typename": "PlayerOdd",
        "betUrl": (
            "https://account.sportsbook.fanduel.com/sportsbook/addToBetslip"
            "?marketId=42.589244080&selectionId=25482164"
        ),
        "playerLine": 3.5,
        "playerOdd": "-113",
    },
    {
        "__typename": "PlayerOdd",
        "betUrl": (
            "https://account.sportsbook.fanduel.com/sportsbook/addToBetslip"
            "?marketId=42.589244080&selectionId=25482165"
        ),
        "playerLine": 3.5,
        "playerOdd": "-113",
    },
]

JULY_CIN_ABBOTT_PROPS = [
    {
        "__typename": "PlayerOdd",
        "betUrl": (
            "https://account.sportsbook.fanduel.com/sportsbook/addToBetslip"
            "?marketId=42.589224175&selectionId=56806640"
        ),
        "playerLine": 4.5,
        "playerOdd": "+102",
    },
    {
        "__typename": "PlayerOdd",
        "betUrl": (
            "https://account.sportsbook.fanduel.com/sportsbook/addToBetslip"
            "?marketId=42.589224175&selectionId=56806645"
        ),
        "playerLine": 4.5,
        "playerOdd": "-136",
    },
]

# Frozen July 1 2026 FanDuel AZ team-page capture. The live production cache
# file is refreshed by scheduled runs, so this test reads a fixture instead of
# .cache/lines to stay hermetic.
JULY_AZ_CACHE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "fanduel_july_az_team_page.json"
)


def _source() -> MlbFanDuelSource:
    cache = JsonCache(Path(tempfile.mkdtemp()), ttl_hours=24)
    return MlbFanDuelSource(shared_cache=cache, lines_cache=cache)


def _game(home: str, away: str, home_pitcher: str, away_pitcher: str = "Visitor Arm") -> Game:
    return Game(
        game_id="g-july",
        game_date=date(2026, 7, 1),
        game_time=datetime(2026, 7, 1, 19, 40),
        home_team=home,
        away_team=away,
        probable_home_pitcher=home_pitcher,
        probable_away_pitcher=away_pitcher,
        source="test",
        probable_home_pitcher_id=1,
    )


def _page(leader_name: str, props: list[dict]) -> dict:
    return {
        "team": {
            "teamLeader": {
                "strikeouts": {
                    "leader": {"name": leader_name},
                    "leaderPropBet": {
                        "propName": f"{leader_name} - Strikeouts",
                        "playerPropsForGame": props,
                    },
                }
            }
        }
    }


class ParsePlayerOddTests(unittest.TestCase):
    def test_string_american_odds(self) -> None:
        self.assertEqual(_parse_player_odd("-113"), -113)
        self.assertEqual(_parse_player_odd("+102"), 102)
        self.assertEqual(_parse_player_odd(" -136 "), -136)

    def test_rejects_missing_and_zero(self) -> None:
        self.assertIsNone(_parse_player_odd(None))
        self.assertIsNone(_parse_player_odd(""))
        self.assertIsNone(_parse_player_odd("0"))
        self.assertIsNone(_parse_player_odd("nope"))


class OverUnderIndexTests(unittest.TestCase):
    def test_july_az_even_juice_maps_index0_over_index1_under(self) -> None:
        over_price, under_price = _over_under_prices_from_player_props(JULY_AZ_GALLEN_PROPS)
        self.assertEqual(over_price, -113)
        self.assertEqual(under_price, -113)

    def test_july_cin_asymmetric_prices_confirm_over_then_under(self) -> None:
        # HTML: O 4.5 +102, U 4.5 -136. SelectionIds 56806640 then 56806645.
        over_price, under_price = _over_under_prices_from_player_props(JULY_CIN_ABBOTT_PROPS)
        self.assertEqual(over_price, 102)
        self.assertEqual(under_price, -136)

    def test_single_sided_fills_over_only(self) -> None:
        over_price, under_price = _over_under_prices_from_player_props(JULY_AZ_GALLEN_PROPS[:1])
        self.assertEqual(over_price, -113)
        self.assertIsNone(under_price)


class ExtractStrikeoutLinePriceTests(unittest.TestCase):
    def test_july_fixture_fills_over_and_under_price(self) -> None:
        source = _source()
        game = _game("AZ", "SF", "Zac Gallen")
        probable = {
            normalize_name("Zac Gallen"): {"subject_id": 1, "team": "AZ", "opponent": "SF"}
        }
        line, miss_reason, _ = source._extract_strikeout_line(
            _page("Zac Gallen", JULY_AZ_GALLEN_PROPS),
            game,
            probable,
            "AZ",
            "SF",
        )
        self.assertIsNone(miss_reason)
        self.assertIsNotNone(line)
        self.assertEqual(line.line, 3.5)
        self.assertEqual(line.over_price, -113)
        self.assertEqual(line.under_price, -113)
        self.assertEqual(line.prop_type, PITCHER_STRIKEOUTS)
        self.assertEqual(line.bookmaker, "fanduel")
        self.assertIsNotNone(line.price_collected_at)

    def test_july_cin_fixture_asymmetric_over_under(self) -> None:
        source = _source()
        game = _game("CIN", "NYY", "Andrew Abbott")
        probable = {
            normalize_name("Andrew Abbott"): {"subject_id": 2, "team": "CIN", "opponent": "NYY"}
        }
        line, miss_reason, _ = source._extract_strikeout_line(
            _page("Andrew Abbott", JULY_CIN_ABBOTT_PROPS),
            game,
            probable,
            "CIN",
            "NYY",
        )
        self.assertIsNone(miss_reason)
        self.assertIsNotNone(line)
        self.assertEqual(line.line, 4.5)
        self.assertEqual(line.over_price, 102)
        self.assertEqual(line.under_price, -136)

    def test_missing_player_odd_still_returns_line(self) -> None:
        source = _source()
        game = _game("AZ", "SF", "Zac Gallen")
        probable = {
            normalize_name("Zac Gallen"): {"subject_id": 1, "team": "AZ", "opponent": "SF"}
        }
        props = [{"playerLine": 3.5}]
        line, miss_reason, _ = source._extract_strikeout_line(
            _page("Zac Gallen", props),
            game,
            probable,
            "AZ",
            "SF",
        )
        self.assertIsNone(miss_reason)
        self.assertIsNotNone(line)
        self.assertEqual(line.line, 3.5)
        self.assertIsNone(line.over_price)
        self.assertIsNone(line.under_price)
        self.assertIsNone(line.price_collected_at)


class JulyCacheDryParseTests(unittest.TestCase):
    @unittest.skipUnless(JULY_AZ_CACHE.exists(), "July FanDuel AZ cache not present")
    def test_july_az_cache_fills_prices(self) -> None:
        payload = json.loads(JULY_AZ_CACHE.read_text())
        page_data = payload.get("data") if isinstance(payload, dict) else payload
        source = _source()
        game = _game("AZ", "SF", "Zac Gallen", "Trevor McDonald")
        probable = {
            normalize_name("Zac Gallen"): {"subject_id": 1, "team": "AZ", "opponent": "SF"}
        }
        line, miss_reason, _ = source._extract_strikeout_line(
            page_data,
            game,
            probable,
            "AZ",
            "SF",
        )
        self.assertIsNone(miss_reason)
        self.assertIsNotNone(line)
        self.assertEqual(line.line, 3.5)
        self.assertEqual(line.over_price, -113)
        self.assertEqual(line.under_price, -113)
        self.assertEqual(line.subject_name_raw, "Zac Gallen")


if __name__ == "__main__":
    unittest.main()
