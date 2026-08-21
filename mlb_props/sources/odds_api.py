from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from urllib.parse import urlencode

from ..cache import JsonCache
from ..config import MARKET_TO_PROP, ODDS_API_MARKETS, ODDS_API_TEAM_ABBR, PARK_RUN_FACTORS, Settings
from ..models import Game, MatchupContext, PropLine
from ..utils import fetch_json, fetch_json_cached, normalize_name, normalize_team_abbr, parse_iso_datetime, safe_float
from .mlb_stats_api import build_probable_pitcher_index


class MlbOddsApiSource:
    BASE_URL = "https://api.the-odds-api.com/v4/sports/baseball_mlb"

    def __init__(self, settings: Settings, shared_cache: JsonCache, lines_cache: JsonCache) -> None:
        self.settings = settings
        self.shared_cache = shared_cache
        self.lines_cache = lines_cache
        self._matchup_contexts: dict[tuple[str, str], MatchupContext] = {}

    def _get_json(self, cache_key: str, url: str) -> dict | list:
        cache = self.lines_cache if "odds_event_" in cache_key else self.shared_cache
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        data = fetch_json_cached(cache, cache_key, url)
        return data

    def fetch_event_ids(self, screen_date: date) -> list[dict]:
        params = urlencode(
            {
                "apiKey": self.settings.odds_api_key,
                "dateFormat": "iso",
            }
        )
        url = f"{self.BASE_URL}/events?{params}"
        data = self._get_json(f"mlb_odds_events_{screen_date.isoformat()}", url)
        events = []
        for item in data:
            commence_time = parse_iso_datetime(item["commence_time"])
            if commence_time.date() != screen_date:
                continue
            events.append(item)
        return events

    def fetch_prop_lines(self, games: list[Game], supported_prop_types: list[str]) -> list[PropLine]:
        if not self.settings.odds_api_key:
            raise RuntimeError("ODDS_API_KEY is required for live odds.")

        games_by_key = {
            tuple(sorted((game.home_team, game.away_team))): game
            for game in games
        }
        probable_pitchers = build_probable_pitcher_index(games)
        events = self.fetch_event_ids(games[0].game_date if games else date.today())

        markets = ["h2h", *[ODDS_API_MARKETS[prop] for prop in supported_prop_types]]
        params = urlencode(
            {
                "apiKey": self.settings.odds_api_key,
                "regions": self.settings.odds_api_regions,
                "markets": ",".join(markets),
                "dateFormat": "iso",
                "oddsFormat": "american",
                "bookmakers": ",".join(self.settings.odds_api_bookmakers),
            }
        )

        props_by_key: dict[tuple[str, str], list[PropLine]] = defaultdict(list)
        matchup_contexts: dict[tuple[str, str], MatchupContext] = {}
        for event in events:
            home_team = ODDS_API_TEAM_ABBR.get(event.get("home_team", ""), event.get("home_team", ""))
            away_team = ODDS_API_TEAM_ABBR.get(event.get("away_team", ""), event.get("away_team", ""))
            home_team = normalize_team_abbr(home_team)
            away_team = normalize_team_abbr(away_team)
            game = games_by_key.get(tuple(sorted((home_team, away_team))))
            if game is None:
                continue

            event_id = event["id"]
            payload = self._get_json(f"odds_event_{event_id}_{game.game_date.isoformat()}", f"{self.BASE_URL}/events/{event_id}/odds?{params}")
            self._collect_matchup_contexts(payload, game, matchup_contexts)
            for line in self._parse_event_odds(payload, game, probable_pitchers):
                props_by_key[(line.subject_name_norm, line.prop_type)].append(line)

        self._matchup_contexts = matchup_contexts

        chosen_props: list[PropLine] = []
        preferred = {name: index for index, name in enumerate(self.settings.odds_api_bookmakers)}
        for _, candidates in props_by_key.items():
            candidates = sorted(candidates, key=lambda item: (preferred.get(item.bookmaker, 999), item.bookmaker, item.line))
            if candidates:
                chosen_props.append(candidates[0])
        return chosen_props

    def fetch_matchup_contexts(self, _screen_date: date) -> dict[tuple[str, str], MatchupContext]:
        return self._matchup_contexts

    def _parse_event_odds(self, payload: dict, game: Game, probable_pitchers: dict[str, dict]) -> list[PropLine]:
        results: list[PropLine] = []
        for bookmaker in payload.get("bookmakers", []):
            bookmaker_key = bookmaker.get("key", "")
            for market in bookmaker.get("markets", []):
                prop_type = MARKET_TO_PROP.get(market.get("key", ""))
                if not prop_type:
                    continue
                for outcome in market.get("outcomes", []):
                    if outcome.get("name") != "Over":
                        continue
                    pitcher_name = outcome.get("description") or outcome.get("participant") or ""
                    if not pitcher_name:
                        continue
                    probable = probable_pitchers.get(normalize_name(pitcher_name))
                    if probable is None:
                        continue
                    over_price = _safe_price(outcome.get("price"))
                    under_price = _opposite_price(market.get("outcomes", []), "Under")
                    results.append(
                        PropLine(
                            event_id=str(payload.get("id", game.game_id)),
                            game_date=game.game_date,
                            subject_name_raw=pitcher_name,
                            subject_name_norm=normalize_name(pitcher_name),
                            subject_id=probable.get("subject_id"),
                            subject_role="pitcher",
                            team=probable["team"],
                            opponent=probable["opponent"],
                            hand="",
                            prop_type=prop_type,
                            line=safe_float(outcome.get("point")),
                            bookmaker=bookmaker_key,
                            source="the_odds_api",
                            collected_at=datetime.now(timezone.utc),
                            over_price=over_price,
                            under_price=under_price,
                            price_collected_at=datetime.now(timezone.utc).isoformat(),
                        )
                    )
        return results

    def _collect_matchup_contexts(
        self,
        payload: dict,
        game: Game,
        matchup_contexts: dict[tuple[str, str], MatchupContext],
    ) -> None:
        moneylines = self._extract_moneylines(payload)
        home_moneyline = moneylines.get(game.home_team, 0)
        away_moneyline = moneylines.get(game.away_team, 0)
        park_factor = PARK_RUN_FACTORS.get(game.home_team, 1.0)

        for team, opponent, moneyline in (
            (game.home_team, game.away_team, home_moneyline),
            (game.away_team, game.home_team, away_moneyline),
        ):
            matchup_contexts[(team, opponent)] = MatchupContext(
                team=team,
                opponent=opponent,
                opponent_k_rate_vs_hand=0.22,
                opponent_walk_rate_vs_hand=0.08,
                opponent_woba_vs_hand=0.315,
                opponent_outs_factor=1.0 + (0.04 if moneyline <= -140 else -0.03 if moneyline >= 130 else 0.0),
                park_run_factor=park_factor,
                moneyline=moneyline,
                source="the_odds_api",
            )

    def _extract_moneylines(self, payload: dict) -> dict[str, int]:
        moneylines: dict[str, int] = {}
        home_team = ODDS_API_TEAM_ABBR.get(payload.get("home_team", ""), payload.get("home_team", ""))
        away_team = ODDS_API_TEAM_ABBR.get(payload.get("away_team", ""), payload.get("away_team", ""))
        home_team = normalize_team_abbr(home_team)
        away_team = normalize_team_abbr(away_team)
        for bookmaker in payload.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    team_name = normalize_team_abbr(ODDS_API_TEAM_ABBR.get(outcome.get("name", ""), outcome.get("name", "")))
                    if team_name in {home_team, away_team} and outcome.get("price") is not None:
                        moneylines[team_name] = int(outcome["price"])
                if moneylines:
                    return moneylines
        return moneylines


def _safe_price(value) -> int | None:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return None
    return numeric if numeric != 0 else None


def _opposite_price(outcomes: list[dict], name: str) -> int | None:
    for outcome in outcomes:
        if outcome.get("name") == name:
            return _safe_price(outcome.get("price"))
    return None
