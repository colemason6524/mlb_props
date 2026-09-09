"""Action Network MLB game lines used as a FanDuel cross-check and fallback."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..cache import JsonCache
from ..utils import fetch_json, normalize_team_abbr


ACTION_MLB_URL = "https://api.actionnetwork.com/web/v1/scoreboard/mlb?period=game&date={date_str}"
FANDUEL_BOOK_ID = 69
FANDUEL_SOURCE = "action_network_fanduel_nj"
EASTERN = ZoneInfo("America/New_York")


def _price(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _line(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _two_way(line: Any, price_a: Any, price_b: Any) -> dict | None:
    parsed_a = _price(price_a)
    parsed_b = _price(price_b)
    if parsed_a is None or parsed_b is None:
        return None
    return {"line": _line(line), "price_a": parsed_a, "price_b": parsed_b}


def parse_action_markets(
    payload: Any,
    screen_date: date,
    now: datetime | None = None,
) -> tuple[dict[tuple[str, str, str], dict], dict]:
    now = now or datetime.now(timezone.utc)
    markets_by_key: dict[tuple[str, str, str], dict] = {}
    diagnostics = {
        "book_id": FANDUEL_BOOK_ID,
        "book_name": "FanDuel NJ",
        "games_seen": 0,
        "pregame_games": 0,
        "games_with_fanduel": 0,
        "games_with_complete_markets": 0,
        "wrong_date_games": 0,
        "started_games": 0,
        "unmatched_teams": [],
    }

    games = payload.get("games", []) if isinstance(payload, dict) else []
    for game in games:
        diagnostics["games_seen"] += 1
        start_time = game.get("start_time")
        try:
            kickoff = datetime.fromisoformat(str(start_time).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        if kickoff <= now:
            diagnostics["started_games"] += 1
            continue
        if kickoff.astimezone(EASTERN).date() != screen_date:
            diagnostics["wrong_date_games"] += 1
            continue
        diagnostics["pregame_games"] += 1

        teams = {
            team.get("id"): normalize_team_abbr(str(team.get("abbr") or ""))
            for team in game.get("teams", [])
            if isinstance(team, dict)
        }
        away = teams.get(game.get("away_team_id"))
        home = teams.get(game.get("home_team_id"))
        if not away or not home:
            diagnostics["unmatched_teams"].append(str(game.get("id") or "unknown"))
            continue

        odds = next(
            (
                item
                for item in game.get("odds", [])
                if isinstance(item, dict) and item.get("book_id") == FANDUEL_BOOK_ID
            ),
            None,
        )
        if odds is None:
            continue
        diagnostics["games_with_fanduel"] += 1

        moneyline = _two_way(None, odds.get("ml_home"), odds.get("ml_away"))
        spread = _two_way(
            odds.get("spread_home"),
            odds.get("spread_home_line"),
            odds.get("spread_away_line"),
        )
        total = _two_way(odds.get("total"), odds.get("over"), odds.get("under"))
        if not any((moneyline, spread, total)):
            continue
        if moneyline and spread and total:
            diagnostics["games_with_complete_markets"] += 1
        start_time_utc = kickoff.astimezone(timezone.utc).isoformat()
        markets_by_key[(away, home, start_time_utc)] = {
            "event_id": str(game.get("id") or ""),
            "away_abbr": away,
            "home_abbr": home,
            "start_time_utc": start_time_utc,
            "moneyline": moneyline,
            "spread": spread,
            "total": total,
            "source": FANDUEL_SOURCE,
            "source_updated_at": odds.get("inserted"),
        }

    return markets_by_key, diagnostics


def fetch_action_markets(
    cache_dir: Path,
    screen_date: date,
) -> tuple[dict[tuple[str, str, str], dict], dict]:
    cache = JsonCache(cache_dir, ttl_hours=15 / 60)
    cache_key = f"action_network_mlb_{screen_date.isoformat()}"
    url = ACTION_MLB_URL.format(date_str=screen_date.strftime("%Y%m%d"))
    mode = "fresh"
    error = None
    try:
        payload = fetch_json(url, headers={"Accept": "application/json"})
        markets, diagnostics = parse_action_markets(payload, screen_date)
        cache.set(cache_key, payload)
    except Exception as exc:  # fail open to the most recent response
        payload = cache.get_allow_stale(cache_key)
        if payload is None:
            return {}, {"mode": "error", "error": f"{type(exc).__name__}: {exc}"}
        mode = "cache"
        error = f"{type(exc).__name__}: {exc}"
        try:
            markets, diagnostics = parse_action_markets(payload, screen_date)
        except Exception as cache_exc:
            return {}, {"mode": "error", "error": f"{type(cache_exc).__name__}: {cache_exc}"}

    diagnostics.update({"mode": mode, "error": error, "source": FANDUEL_SOURCE})
    return markets, diagnostics
