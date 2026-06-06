from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from ..cache import JsonCache
from ..config import OUTPUTS_DIR, PITCHER_OUTS_RECORDED
from ..models import Game, PropLine
from ..utils import fetch_json, fetch_text, normalize_name, safe_float
from .mlb_stats_api import build_probable_pitcher_index


class MlbDraftKingsSource:
    PAGE_URL = "https://sportsbook.draftkings.com/leagues/7/mlb?category=pitcher-props&subcategory=outs-recorded"
    SPORTS_CONTENT_HOST_RE = re.compile(r'"sportsContentBff":"(https:\\/\\/[^"]+\\/api\\/sportscontent\\/([^\\/"]+)\\/)"')
    OUTS_PARAMS_RE = re.compile(
        r'"title":"Outs Recorded O/U".*?"parameters":\{"sportId":"7","leagueId":"(?P<league>\d+)","categoryId":"(?P<category>\d+)","subcategoryId":"(?P<subcategory>\d+)","marketTypeId":"(?P<market>\d+)"\}',
        re.S,
    )

    def __init__(self, shared_cache: JsonCache, lines_cache: JsonCache) -> None:
        self.shared_cache = shared_cache
        self.lines_cache = lines_cache
        self._diagnostics: dict[str, int] = {}
        self._issue_snapshots: list[dict] = []

    def fetch_prop_lines(self, games: list[Game], supported_prop_types: list[str]) -> list[PropLine]:
        self._reset_diagnostics(games)
        if PITCHER_OUTS_RECORDED not in supported_prop_types:
            return []

        probable_pitchers = build_probable_pitcher_index(games)
        payload = self._fetch_outs_payload()
        if payload is None:
            return []

        lines: list[PropLine] = []
        for market in self._iter_player_prop_markets(payload):
            player_name = self._extract_player_name(market)
            if not player_name:
                continue
            probable = probable_pitchers.get(normalize_name(player_name))
            if probable is None:
                continue
            line_value = self._extract_line_value(market)
            if line_value is None:
                continue
            game = next(
                (
                    item
                    for item in games
                    if (
                        (item.home_team == probable["team"] and item.away_team == probable["opponent"])
                        or (item.away_team == probable["team"] and item.home_team == probable["opponent"])
                    )
                ),
                None,
            )
            if game is None:
                continue
            lines.append(
                PropLine(
                    event_id=game.game_id,
                    game_date=game.game_date,
                    subject_name_raw=player_name,
                    subject_name_norm=normalize_name(player_name),
                    subject_id=probable.get("subject_id"),
                    subject_role="pitcher",
                    team=probable["team"],
                    opponent=probable["opponent"],
                    hand="",
                    prop_type=PITCHER_OUTS_RECORDED,
                    line=line_value,
                    bookmaker="draftkings",
                    source="draftkings_scrape_experimental",
                    collected_at=datetime.now(timezone.utc),
                )
            )
        deduped = {(line.subject_name_norm, line.prop_type): line for line in lines}
        self._diagnostics["outs_lines_found"] = len(lines)
        self._diagnostics["outs_unique_lines_returned"] = len(deduped)
        return sorted(deduped.values(), key=lambda item: (item.team, item.subject_name_raw))

    def diagnostics(self) -> dict[str, int]:
        return dict(self._diagnostics)

    def export_diagnostics_snapshot(self, screen_date: date) -> Path | None:
        if not self._issue_snapshots:
            return None
        diagnostics_dir = OUTPUTS_DIR / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = diagnostics_dir / f"draftkings_outs_{screen_date.isoformat()}_{timestamp}.json"
        payload = {
            "screen_date": screen_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "diagnostics": self._diagnostics,
            "issue_snapshots": self._issue_snapshots,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return path

    def _fetch_outs_payload(self) -> dict | list | None:
        page_cache_key = "draftkings_outs_page_html"
        html = self.lines_cache.get(page_cache_key)
        if html is None:
            html = fetch_text(self.PAGE_URL, timeout=30)
            self.lines_cache.set(page_cache_key, html)
        else:
            self._diagnostics["page_html_from_cache"] += 1

        host_match = self.SPORTS_CONTENT_HOST_RE.search(html)
        params_match = self.OUTS_PARAMS_RE.search(html)
        if host_match is None or params_match is None:
            self._diagnostics["page_config_missing"] += 1
            self._record_issue_snapshot("page_config_missing", {"html_excerpt": html[:4000]})
            return None

        api_base = host_match.group(1).replace("\\/", "/")
        payload_url = (
            f"{api_base}v1/leagues/{params_match.group('league')}"
            f"/categories/{params_match.group('category')}"
            f"/subcategories/{params_match.group('subcategory')}"
        )
        payload_cache_key = f"draftkings_outs_payload_{params_match.group('league')}_{params_match.group('category')}_{params_match.group('subcategory')}"
        cached = self.lines_cache.get(payload_cache_key)
        if cached is not None:
            self._diagnostics["payload_from_cache"] += 1
            return cached

        try:
            payload = fetch_json(
                payload_url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://sportsbook.draftkings.com",
                    "Referer": self.PAGE_URL,
                },
                timeout=20,
            )
        except Exception as exc:
            self._diagnostics["payload_fetch_failed"] += 1
            self._record_issue_snapshot("payload_fetch_failed", {"payload_url": payload_url, "error": str(exc)})
            return None

        self.lines_cache.set(payload_cache_key, payload)
        self._diagnostics["payload_fetch_success"] += 1
        return payload

    def _iter_player_prop_markets(self, payload: dict | list) -> list[dict]:
        markets: list[dict] = []

        def walk(node):
            if isinstance(node, dict):
                title = str(node.get("label") or node.get("title") or node.get("name") or "")
                if "outs recorded" in title.lower():
                    markets.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        self._diagnostics["outs_markets_seen"] = len(markets)
        if not markets:
            self._record_issue_snapshot(
                "outs_markets_missing",
                {"payload_excerpt": json.dumps(payload)[:4000]},
            )
        return markets

    def _extract_player_name(self, market: dict) -> str:
        for key in ("participant", "playerName", "description", "label", "name", "title"):
            value = market.get(key)
            if isinstance(value, str) and value and "outs recorded" not in value.lower():
                if " over " in value.lower() or " under " in value.lower():
                    return value.split(" Over")[0].split(" Under")[0].strip()
                return value.strip()
        outcomes = market.get("outcomes")
        if isinstance(outcomes, list):
            for outcome in outcomes:
                for key in ("participant", "playerName", "description", "label"):
                    value = outcome.get(key)
                    if isinstance(value, str) and value:
                        return value.split(" Over")[0].split(" Under")[0].strip()
        return ""

    def _extract_line_value(self, market: dict) -> float | None:
        direct = safe_float(market.get("line"), default=-1.0)
        if direct >= 0:
            return direct
        direct = safe_float(market.get("points"), default=-1.0)
        if direct >= 0:
            return direct
        outcomes = market.get("outcomes")
        if isinstance(outcomes, list):
            points = [safe_float(item.get("line"), default=-1.0) for item in outcomes]
            points = [point for point in points if point >= 0]
            if points:
                return points[0]
            points = [safe_float(item.get("points"), default=-1.0) for item in outcomes]
            points = [point for point in points if point >= 0]
            if points:
                return points[0]
        return None

    def _reset_diagnostics(self, games: list[Game]) -> None:
        self._diagnostics = {
            "games_seen": len(games),
            "page_html_from_cache": 0,
            "payload_from_cache": 0,
            "page_config_missing": 0,
            "payload_fetch_failed": 0,
            "payload_fetch_success": 0,
            "outs_markets_seen": 0,
            "outs_lines_found": 0,
            "outs_unique_lines_returned": 0,
        }
        self._issue_snapshots = []

    def _record_issue_snapshot(self, reason: str, payload: dict) -> None:
        if len(self._issue_snapshots) >= 10:
            return
        self._issue_snapshots.append({"reason": reason, "payload": payload})
