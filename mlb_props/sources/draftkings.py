from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from ..cache import JsonCache
from ..config import OUTPUTS_DIR, PITCHER_OUTS_RECORDED, PITCHER_STRIKEOUTS
from ..models import Game, PropLine
from ..utils import fetch_json, fetch_text, normalize_name, safe_float
from .mlb_stats_api import build_probable_pitcher_index


class MlbDraftKingsSource:
    PAGE_URL = "https://sportsbook.draftkings.com/leagues/7/mlb?category=pitcher-props&subcategory=outs-recorded"
    SPORTS_CONTENT_HOST_RE = re.compile(
        r'"sportsContentBff":"(?P<url>https:(?:\\/\\/|//)[^"]+/api/sportscontent/(?P<site>[^\\/"]+)(?:\\/|/))"'
    )
    MARKET_CONFIGS = {
        PITCHER_STRIKEOUTS: {
            "seo_id": "strikeouts",
            "title": "Strikeouts",
            "diagnostic_prefix": "strikeout",
            "referer_subcategory": "strikeouts",
        },
        PITCHER_OUTS_RECORDED: {
            "seo_id": "outs",
            "title": "Outs",
            "diagnostic_prefix": "outs",
            "referer_subcategory": "outs-recorded",
        },
    }

    def __init__(self, shared_cache: JsonCache, lines_cache: JsonCache) -> None:
        self.shared_cache = shared_cache
        self.lines_cache = lines_cache
        self._diagnostics: dict[str, int] = {}
        self._issue_snapshots: list[dict] = []

    def fetch_prop_lines(self, games: list[Game], supported_prop_types: list[str]) -> list[PropLine]:
        self._reset_diagnostics(games)
        supported_dk_props = [prop for prop in self.MARKET_CONFIGS if prop in supported_prop_types]
        if not supported_dk_props:
            return []

        probable_pitchers = build_probable_pitcher_index(games)
        html = self._fetch_page_html()
        if html is None:
            return []

        lines: list[PropLine] = []
        for prop_type in supported_dk_props:
            payload = self._fetch_market_payload(html, prop_type)
            if payload is None:
                continue
            prefix = self.MARKET_CONFIGS[prop_type]["diagnostic_prefix"]
            for market in self._iter_player_prop_markets(payload, prop_type):
                player_name = self._extract_player_name(market, prop_type)
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
                        prop_type=prop_type,
                        line=line_value,
                        bookmaker="draftkings",
                        source="draftkings_scrape_experimental",
                        collected_at=datetime.now(timezone.utc),
                    )
                )
            self._diagnostics[f"{prefix}_lines_found"] = len([line for line in lines if line.prop_type == prop_type])
        deduped = {(line.subject_name_norm, line.prop_type): line for line in lines}
        self._diagnostics["unique_lines_returned"] = len(deduped)
        self._diagnostics["strikeout_unique_lines_returned"] = len(
            [line for line in deduped.values() if line.prop_type == PITCHER_STRIKEOUTS]
        )
        self._diagnostics["outs_unique_lines_returned"] = len(
            [line for line in deduped.values() if line.prop_type == PITCHER_OUTS_RECORDED]
        )
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

    def _fetch_page_html(self) -> str | None:
        page_cache_key = "draftkings_pitcher_props_page_html"
        html = self.lines_cache.get(page_cache_key)
        if html is None:
            try:
                html = fetch_text(self.PAGE_URL, timeout=30)
            except Exception as exc:
                self._diagnostics["page_fetch_failed"] += 1
                self._record_issue_snapshot("page_fetch_failed", {"page_url": self.PAGE_URL, "error": str(exc)})
                return None
            self.lines_cache.set(page_cache_key, html)
        else:
            self._diagnostics["page_html_from_cache"] += 1
        return html

    def _fetch_market_payload(self, html: str, prop_type: str) -> dict | list | None:
        host_match = self.SPORTS_CONTENT_HOST_RE.search(html)
        params_match = self._find_market_params(html, prop_type)
        prefix = self.MARKET_CONFIGS[prop_type]["diagnostic_prefix"]
        if host_match is None or params_match is None:
            self._diagnostics[f"{prefix}_page_config_missing"] += 1
            self._record_issue_snapshot(
                f"{prefix}_page_config_missing",
                {
                    "host_found": host_match is not None,
                    "params_found": params_match is not None,
                    "html_excerpt": html[:4000],
                },
            )
            return None

        params = self._parse_market_params(params_match.group(1))
        api_base = host_match.group("url").replace("\\/", "/")
        payload_url = (
            f"{api_base}v1/leagues/{params['leagueId']}"
            f"/categories/{params['categoryId']}"
            f"/subcategories/{params['subcategoryId']}"
        )
        payload_cache_key = (
            f"draftkings_{prefix}_payload_"
            f"{params['leagueId']}_{params['categoryId']}_{params['subcategoryId']}"
        )
        cached = self.lines_cache.get(payload_cache_key)
        if cached is not None:
            self._diagnostics[f"{prefix}_payload_from_cache"] += 1
            return cached

        try:
            payload = fetch_json(
                payload_url,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Origin": "https://sportsbook.draftkings.com",
                    "Referer": (
                        "https://sportsbook.draftkings.com/leagues/7/mlb"
                        f"?category=pitcher-props&subcategory={self.MARKET_CONFIGS[prop_type]['referer_subcategory']}"
                    ),
                },
                timeout=20,
            )
        except Exception as exc:
            self._diagnostics[f"{prefix}_payload_fetch_failed"] += 1
            self._record_issue_snapshot(f"{prefix}_payload_fetch_failed", {"payload_url": payload_url, "error": str(exc)})
            return None

        self.lines_cache.set(payload_cache_key, payload)
        self._diagnostics[f"{prefix}_payload_fetch_success"] += 1
        return payload

    def _find_market_params(self, html: str, prop_type: str) -> re.Match | None:
        config = self.MARKET_CONFIGS[prop_type]
        return re.search(
            (
                rf'"seoId":"{re.escape(config["seo_id"])}","title":"{re.escape(config["title"])}"'
                r'.*?"parameters":\{([^}]+)\}'
            ),
            html,
            re.S,
        )

    def _parse_market_params(self, raw_params: str) -> dict[str, str]:
        return dict(re.findall(r'"([^"]+)":"([^"]+)"', raw_params))

    def _iter_player_prop_markets(self, payload: dict | list, prop_type: str) -> list[dict]:
        markets: list[dict] = []
        config = self.MARKET_CONFIGS[prop_type]
        prefix = config["diagnostic_prefix"]
        title_needle = str(config["title"]).lower()
        seo_needle = str(config["seo_id"]).lower()

        def walk(node):
            if isinstance(node, dict):
                title = str(node.get("label") or node.get("title") or node.get("name") or "")
                seo_id = str(node.get("seoId") or node.get("seoName") or "")
                if title_needle in title.lower() or seo_needle == seo_id.lower():
                    markets.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(payload)
        self._diagnostics[f"{prefix}_markets_seen"] = len(markets)
        if not markets:
            self._record_issue_snapshot(
                f"{prefix}_markets_missing",
                {"payload_excerpt": json.dumps(payload)[:4000]},
            )
        return markets

    def _extract_player_name(self, market: dict, prop_type: str) -> str:
        title_needle = str(self.MARKET_CONFIGS[prop_type]["title"]).lower()
        for key in ("participant", "playerName", "description", "label", "name", "title"):
            value = market.get(key)
            if isinstance(value, str) and value and title_needle not in value.lower():
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
            "page_fetch_failed": 0,
            "strikeout_payload_from_cache": 0,
            "strikeout_page_config_missing": 0,
            "strikeout_payload_fetch_failed": 0,
            "strikeout_payload_fetch_success": 0,
            "strikeout_markets_seen": 0,
            "strikeout_lines_found": 0,
            "strikeout_unique_lines_returned": 0,
            "outs_payload_from_cache": 0,
            "outs_page_config_missing": 0,
            "outs_payload_fetch_failed": 0,
            "outs_payload_fetch_success": 0,
            "outs_markets_seen": 0,
            "outs_lines_found": 0,
            "outs_unique_lines_returned": 0,
            "unique_lines_returned": 0,
        }
        self._issue_snapshots = []

    def _record_issue_snapshot(self, reason: str, payload: dict) -> None:
        if len(self._issue_snapshots) >= 10:
            return
        self._issue_snapshots.append({"reason": reason, "payload": payload})
