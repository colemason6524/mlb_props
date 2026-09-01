"""Bovada MLB game lines (free JSON coupon API, no key or browser).

Same endpoint family proven in nfl_props / tennis_props / golf_props. The
coupon carries moneyline, point spread (run line), and game total in one
fetch with both-side American prices. Fail-open: on fetch failure the last
good cached payload is reused so a transient outage produces a stale-but-
diagnosed run instead of a silent empty one. Bovada also answers HTTP 200
with `{}` when nothing is listed; that is a fresh empty coupon, not a fetch
failure, and an object wrapping the usual group list is unwrapped.

Observation-only: these lines feed game-market shadow collection and are
never used for EV or staking claims.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

BOVADA_BASE = "https://www.bovada.lv/services/sports/event/coupon/events/A/description"
BOVADA_MLB_URL = f"{BOVADA_BASE}/baseball/mlb?marketFilterId=def&preMatchOnly=true"

HTTP_TIMEOUT_SECONDS = 30
HTTP_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
)

MLB_TEAM_NAME_TO_ABBR = {
    "arizona diamondbacks": "AZ",
    "atlanta braves": "ATL",
    "baltimore orioles": "BAL",
    "boston red sox": "BOS",
    "chicago cubs": "CHC",
    "cincinnati reds": "CIN",
    "cleveland guardians": "CLE",
    "colorado rockies": "COL",
    "chicago white sox": "CWS",
    "detroit tigers": "DET",
    "houston astros": "HOU",
    "kansas city royals": "KC",
    "los angeles angels": "LAA",
    "los angeles dodgers": "LAD",
    "miami marlins": "MIA",
    "milwaukee brewers": "MIL",
    "minnesota twins": "MIN",
    "new york mets": "NYM",
    "new york yankees": "NYY",
    "athletics": "ATH",
    "oakland athletics": "ATH",
    "philadelphia phillies": "PHI",
    "pittsburgh pirates": "PIT",
    "san diego padres": "SD",
    "san francisco giants": "SF",
    "seattle mariners": "SEA",
    "st. louis cardinals": "STL",
    "st louis cardinals": "STL",
    "tampa bay rays": "TB",
    "texas rangers": "TEX",
    "toronto blue jays": "TOR",
    "washington nationals": "WSH",
}


@dataclass
class FetchResult:
    payload: Any
    mode: str  # "fresh" | "cache"
    attempts: int
    http_status: int | None
    response_bytes: int
    fetched_at_utc: str
    cache_age_seconds: float
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "attempts": self.attempts,
            "http_status": self.http_status,
            "response_bytes": self.response_bytes,
            "fetched_at_utc": self.fetched_at_utc,
            "cache_age_seconds": round(self.cache_age_seconds, 1),
            "error": self.error,
        }


def normalize_team_name(name: str) -> str | None:
    return MLB_TEAM_NAME_TO_ABBR.get((name or "").strip().lower())


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cache_age(cache_path: Path) -> float:
    try:
        return max(0.0, time.time() - cache_path.stat().st_mtime)
    except OSError:
        return 0.0


def _load_cache(cache_path: Path) -> list | None:
    if not cache_path.exists():
        return None
    try:
        cached = json.loads(cache_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return cached if isinstance(cached, list) else None


def _fetch_once(url: str) -> tuple[str, int]:
    request = Request(
        url,
        headers={
            "User-Agent": HTTP_USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        text = response.read().decode("utf-8", "replace")
        status = int(getattr(response, "status", 200) or 200)
        return text, status


def _find_coupon_list(obj: dict) -> list | None:
    """Locate the usual list of league groups inside a wrapper object."""
    for value in obj.values():
        if isinstance(value, list) and all(isinstance(item, dict) and "events" in item for item in value):
            return value
        if isinstance(value, dict):
            nested = _find_coupon_list(value)
            if nested is not None:
                return nested
    return None


def _normalize_coupon(parsed: Any) -> list:
    """Return the coupon as the usual list of league groups.

    Bovada sometimes answers HTTP 200 with an object instead of the list:
    `{}` means no events are listed (a genuinely empty coupon), and an
    object wrapping the usual list is unwrapped. Any other shape stays an
    unexpected payload so the fail-open path can still fire.
    """
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        if not parsed:
            return []
        coupon = _find_coupon_list(parsed)
        if coupon is not None:
            return coupon
        raise ValueError("unexpected payload: object without a coupon events list")
    raise ValueError(f"unexpected payload type {type(parsed).__name__}")


def fetch_mlb_payload(cache_dir: Path, refresh: bool = True) -> FetchResult:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / "bovada_mlb.json"
    if not refresh:
        cached = _load_cache(cache_path)
        if cached is None:
            raise RuntimeError(f"Invalid cached Bovada payload: {cache_path.name}")
        return FetchResult(cached, "cache", 0, None, cache_path.stat().st_size, _now_iso(), _cache_age(cache_path))

    last_error: Exception | None = None
    attempts = 0
    for attempt in range(2):
        attempts = attempt + 1
        try:
            text, status = _fetch_once(BOVADA_MLB_URL)
            parsed = json.loads(text)
            coupon = _normalize_coupon(parsed)
            cache_path.write_text(text if coupon is parsed else json.dumps(coupon))
            return FetchResult(coupon, "fresh", attempts, status, len(text.encode("utf-8")), _now_iso(), 0.0)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if isinstance(exc, HTTPError) and exc.code != 429 and exc.code < 500:
                break
            if attempt == 0:
                time.sleep(1.0)

    cached = _load_cache(cache_path)
    if cached is not None:
        return FetchResult(
            cached,
            "cache",
            attempts,
            None,
            cache_path.stat().st_size,
            _now_iso(),
            _cache_age(cache_path),
            str(last_error),
        )
    raise RuntimeError(f"Bovada MLB fetch failed: {last_error}") from last_error


def _split_teams(description: str) -> tuple[str, str] | None:
    """Bovada US-sport descriptions are 'Away Team @ Home Team'."""
    for separator in (" @ ", " vs ", " VS "):
        if separator in description:
            away, home = description.split(separator, 1)
            return away.strip(), home.strip()
    return None


def _parse_american(value: object) -> int | None:
    text = str(value).strip().upper()
    if text in ("EVEN", "EV"):
        return 100
    try:
        return int(text.replace("+", ""))
    except ValueError:
        return None


def _price(outcome: dict) -> tuple[int | None, float | None]:
    price = outcome.get("price") or {}
    american = _parse_american(price.get("american"))
    handicap = price.get("handicap")
    line = None
    if handicap not in (None, ""):
        try:
            line = float(handicap)
        except (TypeError, ValueError):
            line = None
    return american, line


def _is_open(market: dict) -> bool:
    return str(market.get("status", "O")).upper() in ("O", "OPEN", "")


def _is_game_period(market: dict) -> bool:
    period = market.get("period") or {}
    description = str(period.get("description", "")).lower()
    return description in ("game", "match", "") or bool(period.get("main"))


@dataclass
class BovadaGame:
    event_id: str
    away_abbr: str
    home_abbr: str
    away_name: str
    home_name: str
    start_time_utc: str | None
    markets: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "away_abbr": self.away_abbr,
            "home_abbr": self.home_abbr,
            "away_name": self.away_name,
            "home_name": self.home_name,
            "start_time_utc": self.start_time_utc,
        }


def parse_games(payload: Any) -> tuple[list[BovadaGame], dict[str, Any]]:
    from ..game_markets import TwoWayPrice

    games: list[BovadaGame] = []
    diagnostics: dict[str, Any] = {
        "groups_seen": 0,
        "events_seen": 0,
        "unparsed_descriptions": [],
        "unmatched_teams": [],
        "markets_without_prices": [],
    }
    for group in payload or []:
        diagnostics["groups_seen"] += 1
        for event in group.get("events", []) or []:
            diagnostics["events_seen"] += 1
            description = str(event.get("description", ""))
            teams = _split_teams(description)
            if teams is None:
                diagnostics["unparsed_descriptions"].append(description)
                continue
            away_name, home_name = teams
            away = normalize_team_name(away_name)
            home = normalize_team_name(home_name)
            if away is None or home is None:
                diagnostics["unmatched_teams"].append(description)
                continue
            start_ms = event.get("startTime")
            start_iso = None
            if start_ms:
                start_iso = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc).isoformat()
            game = BovadaGame(
                event_id=str(event.get("id") or event.get("link") or description),
                away_abbr=away,
                home_abbr=home,
                away_name=away_name,
                home_name=home_name,
                start_time_utc=start_iso,
            )
            markets: dict[str, Any] = {}
            for display_group in event.get("displayGroups", []) or []:
                for market in display_group.get("markets", []) or []:
                    if not (_is_open(market) and _is_game_period(market)):
                        continue
                    market_description = str(market.get("description", "")).strip().lower()
                    storage_key = {
                        "moneyline": "moneyline",
                        "point spread": "spread",
                        "runline": "spread",
                        "run line": "spread",
                        "total": "total",
                    }.get(market_description)
                    if storage_key is None:
                        continue
                    if storage_key in markets:
                        continue
                    outcomes = market.get("outcomes") or []
                    by_description = {
                        str(outcome.get("description", "")).strip().lower(): outcome
                        for outcome in outcomes
                    }
                    if storage_key == "total":
                        side_a, side_b = by_description.get("over"), by_description.get("under")
                    else:
                        side_a, side_b = by_description.get(game.home_name.lower()), by_description.get(game.away_name.lower())
                    if side_a is None or side_b is None:
                        continue
                    try:
                        price_a, line_a = _price(side_a)
                        price_b, _ = _price(side_b)
                    except (KeyError, TypeError):
                        continue
                    if price_a is None or price_b is None:
                        diagnostics["markets_without_prices"].append(market_description)
                        continue
                    if storage_key == "spread" and line_a is None:
                        continue
                    markets[storage_key] = TwoWayPrice(
                        line=line_a,
                        price_a=price_a,
                        price_b=price_b,
                    )
            game.markets = markets
            games.append(game)
    return games, diagnostics


def fetch_game_markets(
    cache_dir: Path,
    screen_date: date,
    refresh: bool = True,
) -> tuple[dict[tuple[str, str], Any], dict[str, Any]]:
    """Fetch Bovada MLB lines keyed by (away_abbr, home_abbr).

    Returns (markets_by_key, diagnostics). Values are dicts shaped like
    GameMarketSnapshot inputs: moneyline/spread/total TwoWayPrice payloads.
    """
    coupon = fetch_mlb_payload(cache_dir, refresh=refresh)
    games, parse_diagnostics = parse_games(coupon.payload)

    now = datetime.now(timezone.utc)
    markets_by_key: dict[tuple[str, str], Any] = {}
    stale_filtered: list[str] = []
    wrong_date_filtered: list[str] = []
    empty_markets_filtered: list[str] = []
    for game in games:
        label = f"{game.away_abbr} @ {game.home_abbr}"
        kickoff = None
        if game.start_time_utc:
            try:
                kickoff = datetime.fromisoformat(game.start_time_utc.replace("Z", "+00:00"))
            except ValueError:
                kickoff = None
        if kickoff is None or kickoff <= now:
            stale_filtered.append(label)
            continue
        local_date = kickoff.astimezone(EASTERN).date() if kickoff else None
        if local_date is not None and local_date != screen_date:
            wrong_date_filtered.append(label)
            continue
        markets = game.markets or {}
        if not markets:
            empty_markets_filtered.append(label)
            continue
        serialized = {name: market.as_dict() for name, market in markets.items()}
        markets_by_key[(game.away_abbr, game.home_abbr)] = {
            **game.as_dict(),
            "moneyline": serialized.get("moneyline"),
            "spread": serialized.get("spread"),
            "total": serialized.get("total"),
        }

    diagnostics = {
        **parse_diagnostics,
        "games_parsed": len(games),
        "games_matched_to_slate": len(markets_by_key),
        "with_moneyline": sum(1 for item in markets_by_key.values() if item.get("moneyline")),
        "with_spread": sum(1 for item in markets_by_key.values() if item.get("spread")),
        "with_total": sum(1 for item in markets_by_key.values() if item.get("total")),
        "stale_games_filtered": len(stale_filtered),
        "wrong_date_games_filtered": len(wrong_date_filtered),
        "empty_markets_filtered": len(empty_markets_filtered),
        "stale_games": stale_filtered,
        "wrong_date_games": wrong_date_filtered,
        "empty_market_games": empty_markets_filtered,
        "coupon_fetch": coupon.as_dict(),
    }
    return markets_by_key, diagnostics
