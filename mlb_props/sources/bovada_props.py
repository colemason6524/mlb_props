"""Bovada MLB batter hit-prop prices via the event-scoped coupon API.

The game-lines coupon (bovada_mlb.fetch_mlb_payload) supplies today's event
slugs. For each slate game this module fetches that event's own coupon, which
carries the full display groups including "Batter Props", and extracts the
single-sided YES American prices for:

- "Player to record a Hit"   (the one-hit market)
- "Player to record 2+ Hits" (the alt line)

The YES side is priced alone (Bovada does not list NO), so implied
probabilities are vig-inclusive and conservative: they can overstate the
market's fair probability but never understate it. Fail-open per event: one
dead event never aborts the run. Observation-only; prices never gate scoring,
tiers, Discord selection, or exports.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .bovada_mlb import (
    HTTP_TIMEOUT_SECONDS,
    HTTP_USER_AGENT,
    fetch_mlb_payload,
)
from ..utils import normalize_name

EASTERN = ZoneInfo("America/New_York")

EVENT_COUPON_URL = (
    "https://www.bovada.lv/services/sports/event/coupon/events/A/description"
    "/baseball/mlb/{slug}"
)
BETWEEN_EVENT_SLEEP_SECONDS = 0.75

HIT_MARKET_KEY = "Player to record a Hit"
HITS_2PLUS_MARKET_KEY = "Player to record 2+ Hits"

_PLAYER_OUTCOME_PATTERN = re.compile(r"^(?P<name>.+?)\s*\((?P<team>[A-Z]{2,3})\)\s*$")


@dataclass(frozen=True)
class HitPropQuote:
    player_name: str
    player_name_norm: str
    team_abbr: str
    hit_yes_price: int
    hits_2plus_yes_price: int | None
    event_slug: str
    event_start_time_utc: str | None
    collected_at_utc: str
    flags: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch_text(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": HTTP_USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", "replace")


def _fetch_event_payload(slug: str) -> dict:
    """Fetch one event coupon with one bounded retry on transient failures."""
    url = EVENT_COUPON_URL.format(slug=slug)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            text = _fetch_text(url)
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                raise ValueError(f"unexpected event payload type {type(parsed).__name__}")
            return parsed
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            if isinstance(exc, HTTPError) and exc.code != 429 and exc.code < 500:
                break
            if attempt == 0:
                time.sleep(1.0)
    raise RuntimeError(f"event coupon fetch failed for {slug}: {last_error}")


def _iter_display_groups(node: Any):
    if isinstance(node, dict):
        groups = node.get("displayGroups")
        if isinstance(groups, list):
            yield from groups
        for value in node.values():
            yield from _iter_display_groups(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_display_groups(value)


def _is_open(market: dict) -> bool:
    return str(market.get("status", "O")).upper() in ("O", "OPEN", "")


def _is_game_period(market: dict) -> bool:
    period = market.get("period") or {}
    description = str(period.get("description", "")).lower()
    return description in ("game", "match", "") or bool(period.get("main"))


def _parse_american(value: object) -> int | None:
    text = str(value).strip().upper()
    if text in ("EVEN", "EV"):
        return 100
    try:
        return int(text.replace("+", ""))
    except (TypeError, ValueError):
        return None


def american_to_implied_probability(price: int | None) -> float | None:
    if price is None or price == 0:
        return None
    if price > 0:
        return 100.0 / (price + 100.0)
    return abs(price) / (abs(price) + 100.0)


def _parse_player_outcome(description: str) -> tuple[str, str] | None:
    match = _PLAYER_OUTCOME_PATTERN.match((description or "").strip())
    if match is None:
        return None
    name = match.group("name").strip()
    team = match.group("team")
    if not name or len(name) < 3:
        return None
    return name, team


def parse_hit_prop_quotes(payload: dict) -> tuple[dict[tuple[str, str], HitPropQuote], dict[str, int]]:
    """Extract per-player hit quotes from one event coupon payload.

    Returns (quotes keyed by (team_abbr, normalized player name), diagnostics).
    """
    diagnostics = {
        "batter_props_group_found": 0,
        "hit_markets_seen": 0,
        "hits2_markets_seen": 0,
        "outcomes_parsed": 0,
        "outcomes_unparsed": 0,
        "duplicate_player_quotes": 0,
    }
    quotes: dict[tuple[str, str], HitPropQuote] = {}

    batter_groups = [
        group
        for group in _iter_display_groups(payload)
        if str(group.get("description", "")).strip().lower() == "batter props"
    ]
    if not batter_groups:
        return quotes, diagnostics
    diagnostics["batter_props_group_found"] = 1

    hit_prices: dict[tuple[str, str], int] = {}
    hits2_prices: dict[tuple[str, str], int] = {}

    for group in batter_groups:
        for market in group.get("markets") or []:
            if not (_is_open(market) and _is_game_period(market)):
                continue
            description = str(market.get("description", "")).strip()
            key = None
            if description == HIT_MARKET_KEY:
                key = hit_prices
                diagnostics["hit_markets_seen"] += 1
            elif description == HITS_2PLUS_MARKET_KEY:
                key = hits2_prices
                diagnostics["hits2_markets_seen"] += 1
            if key is None:
                continue
            for outcome in market.get("outcomes") or []:
                parsed = _parse_player_outcome(str(outcome.get("description", "")))
                if parsed is None:
                    diagnostics["outcomes_unparsed"] += 1
                    continue
                name, team = parsed
                price = _parse_american((outcome.get("price") or {}).get("american"))
                if price is None:
                    diagnostics["outcomes_unparsed"] += 1
                    continue
                player_key = (team, normalize_name(name))
                diagnostics["outcomes_parsed"] += 1
                if player_key in key:
                    diagnostics["duplicate_player_quotes"] += 1
                    continue
                key[player_key] = price

    for (team, player_norm), hit_price in hit_prices.items():
        quotes[(team, player_norm)] = HitPropQuote(
            player_name=player_norm,
            player_name_norm=player_norm,
            team_abbr=team,
            hit_yes_price=hit_price,
            hits_2plus_yes_price=hits2_prices.get((team, player_norm)),
            event_slug="",
            event_start_time_utc=None,
            collected_at_utc=_now_iso(),
        )
    return quotes, diagnostics


def _slate_event_slugs(
    payload: Any,
    screen_date: date,
) -> tuple[dict[tuple[str, str], tuple[str, str | None]], dict[str, int]]:
    """Map (away_abbr, home_abbr) -> (event slug, start iso) for the screen date.

    Uses the game-lines coupon. Doubleheader days can list two events for the
    same matchup; the earliest scheduled event on the screen date wins and the
    collision is counted in diagnostics.
    """
    from .bovada_mlb import _split_teams, normalize_team_name

    diagnostics = {
        "coupon_events_seen": 0,
        "coupon_events_unparsed": 0,
        "coupon_events_other_date": 0,
        "coupon_doubleheader_collisions": 0,
        "slate_events_matched": 0,
    }
    events_by_key: dict[tuple[str, str], tuple[str, str | None]] = {}
    now_utc = datetime.now(timezone.utc)
    for group in payload or []:
        for event in group.get("events", []) or []:
            diagnostics["coupon_events_seen"] += 1
            description = str(event.get("description", ""))
            teams = _split_teams(description)
            if teams is None:
                diagnostics["coupon_events_unparsed"] += 1
                continue
            away = normalize_team_name(teams[0])
            home = normalize_team_name(teams[1])
            if away is None or home is None:
                diagnostics["coupon_events_unparsed"] += 1
                continue
            start_ms = event.get("startTime")
            if not start_ms:
                continue
            start_utc = datetime.fromtimestamp(start_ms / 1000.0, tz=timezone.utc)
            if start_utc.astimezone(EASTERN).date() != screen_date:
                diagnostics["coupon_events_other_date"] += 1
                continue
            slug = str(event.get("link") or "").rsplit("/", 1)[-1]
            if not slug:
                continue
            key = (away, home)
            existing = events_by_key.get(key)
            is_future = start_utc > now_utc
            if existing is not None:
                existing_start_iso = existing[1]
                existing_start = (
                    datetime.fromisoformat(existing_start_iso)
                    if existing_start_iso
                    else None
                )
                existing_future = bool(existing_start and existing_start > now_utc)
                if existing_future or not is_future:
                    diagnostics["coupon_doubleheader_collisions"] += 1
                    continue
                # existing event already started while this one is upcoming:
                # the upcoming event carries the tradeable props, so replace.
            events_by_key[key] = (slug, start_utc.isoformat())
    diagnostics["slate_events_matched"] = len(events_by_key)
    return events_by_key, diagnostics


def fetch_hit_prop_quotes(
    cache_dir,
    games: list,
    screen_date: date,
) -> tuple[dict[tuple[str, str], HitPropQuote], dict[str, Any]]:
    """Fetch hit-prop quotes for every slate game with a Bovada event today.

    Returns (quotes keyed by (team_abbr, normalized player name), diagnostics).
    Fails open per event: fetch failures are counted, never raised.
    """
    coupon = fetch_mlb_payload(cache_dir, refresh=True)
    events_by_key, event_diagnostics = _slate_event_slugs(coupon.payload, screen_date)

    diagnostics: dict[str, Any] = {
        **event_diagnostics,
        "slate_games": len(games),
        "events_requested": 0,
        "events_fetched": 0,
        "events_failed": 0,
        "events_without_batter_props": 0,
        "failed_events": [],
    }

    quotes: dict[tuple[str, str], HitPropQuote] = {}
    for game in games:
        key = _slate_key_for_game(game)
        if key is None:
            continue
        matched = events_by_key.get(key)
        if matched is None:
            continue
        slug, start_iso = matched
        diagnostics["events_requested"] += 1
        try:
            payload = _fetch_event_payload(slug)
        except RuntimeError as exc:
            diagnostics["events_failed"] += 1
            diagnostics["failed_events"].append({"slug": slug, "error": str(exc)[:160]})
            time.sleep(BETWEEN_EVENT_SLEEP_SECONDS)
            continue
        diagnostics["events_fetched"] += 1
        event_quotes, parse_diag = parse_hit_prop_quotes(payload)
        for diag_key, value in parse_diag.items():
            diagnostics[f"parse_{diag_key}"] = diagnostics.get(f"parse_{diag_key}", 0) + value
        if not event_quotes:
            diagnostics["events_without_batter_props"] += 1
        for quote_key, quote in event_quotes.items():
            quotes[quote_key] = HitPropQuote(
                player_name=quote.player_name_norm,
                player_name_norm=quote.player_name_norm,
                team_abbr=quote.team_abbr,
                hit_yes_price=quote.hit_yes_price,
                hits_2plus_yes_price=quote.hits_2plus_yes_price,
                event_slug=slug,
                event_start_time_utc=start_iso,
                collected_at_utc=_now_iso(),
            )
        time.sleep(BETWEEN_EVENT_SLEEP_SECONDS)
    diagnostics["quotes_found"] = len(quotes)
    return quotes, diagnostics


def _slate_key_for_game(game) -> tuple[str, str] | None:
    away = str(getattr(game, "away_team", "") or "")
    home = str(getattr(game, "home_team", "") or "")
    if not away or not home:
        return None
    return (away, home)
