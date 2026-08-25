"""ESPN MLB odds page scrape (free HTML, regex-parsed).

Fallback / cross-check for Bovada game lines. Provides run-line spreads and
game totals per game; ESPN's odds board does not expose moneyline prices in
a stable scrapeable form, so this source never supplies a moneyline.

Fail-open by design: parse failures return an empty mapping plus diagnostics
rather than raising, so the shadow collector can proceed on Bovada alone.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from ..cache import JsonCache
from ..utils import fetch_text


ESPN_ALIAS_TO_ABBR = {
    "ari": "AZ",
    "az": "AZ",
    "atl": "ATL",
    "bal": "BAL",
    "bos": "BOS",
    "chc": "CHC",
    "chi": "CHC",
    "cws": "CWS",
    "chw": "CWS",
    "cin": "CIN",
    "cle": "CLE",
    "col": "COL",
    "det": "DET",
    "hou": "HOU",
    "kc": "KC",
    "laa": "LAA",
    "lad": "LAD",
    "mia": "MIA",
    "mil": "MIL",
    "min": "MIN",
    "nym": "NYM",
    "nyy": "NYY",
    "ath": "ATH",
    "oak": "ATH",
    "phi": "PHI",
    "pit": "PIT",
    "sd": "SD",
    "sdp": "SD",
    "sf": "SF",
    "sfg": "SF",
    "sea": "SEA",
    "stl": "STL",
    "tb": "TB",
    "tbr": "TB",
    "tex": "TEX",
    "tor": "TOR",
    "wsh": "WSH",
    "wsn": "WSH",
}


@dataclass
class EspnGameOdds:
    away_team: str
    home_team: str
    away_spread: float | None
    home_spread: float | None
    total: float | None

    def as_dict(self) -> dict:
        return {
            "away_team": self.away_team,
            "home_team": self.home_team,
            "away_spread": self.away_spread,
            "home_spread": self.home_spread,
            "total": self.total,
        }


class EspnMlbOddsSource:
    ODDS_URL = "https://www.espn.com/mlb/odds/_/date/{date_str}"
    CACHE_VERSION = "mlb_v1"

    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache

    def fetch_game_context(self, screen_date: date) -> tuple[dict[tuple[str, str], EspnGameOdds], dict]:
        """Return ({(away_abbr, home_abbr): EspnGameOdds}, diagnostics)."""
        cache_key = f"espn_odds_{self.CACHE_VERSION}_{screen_date.isoformat()}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            contexts = {
                tuple(item["key"]): EspnGameOdds(**item["value"])
                for item in cached
            }
            return contexts, {"mode": "cache", "games_parsed": len(contexts)}

        html = ""
        error: str | None = None
        for user_agent in (None, "curl/8.7.1"):
            headers = {"User-Agent": user_agent} if user_agent else None
            try:
                html = fetch_text(
                    self.ODDS_URL.format(date_str=screen_date.strftime("%Y%m%d")),
                    headers=headers,
                )
            except Exception as exc:  # noqa: BLE001 - fail-open source
                error = f"{type(exc).__name__}: {exc}"
                continue
            if 'window.gokuProps' not in html and "betSixPackTable-item" in html:
                break
        contexts, diagnostics = self._parse_html(html)
        if not contexts and error is None and 'window.gokuProps' in html:
            error = "espn_waf_challenge"
        diagnostics["error"] = error
        serializable = [
            {"key": list(key), "value": value.as_dict()}
            for key, value in contexts.items()
            if value.total is not None or value.away_spread is not None
        ]
        if serializable:
            self.cache.set(cache_key, serializable)
        return contexts, diagnostics

    def _parse_html(self, html: str) -> tuple[dict[tuple[str, str], EspnGameOdds], dict]:
        """Parse the betSixPackTable layout.

        Each ``betSixPackTable-item`` block is one game carrying two team links
        (away then home) and six deep-link anchors in order: away ML, away
        total ("o7" / -118), away run line ("-1.5" / +119), then the home
        equivalents. Prices come from ``data-exit-type-value``; lines come
        from the anchor's first visible text token.
        """
        results: dict[tuple[str, str], EspnGameOdds] = {}
        blocks_seen = 0
        malformed_blocks = 0
        item_chunks = re.split(r'data-testid="betSixPackTable-item-\d+-\d+"', html)
        for chunk in item_chunks[1:]:
            blocks_seen += 1
            team_aliases = re.findall(r"/mlb/team/_/name/([a-z]{2,3})/", chunk)
            # Dedupe while preserving order; team links repeat within a row.
            ordered_teams: list[str] = []
            for alias in team_aliases:
                if alias not in ordered_teams:
                    ordered_teams.append(alias)
            if len(ordered_teams) < 2:
                malformed_blocks += 1
                continue
            away = ESPN_ALIAS_TO_ABBR.get(ordered_teams[0], ordered_teams[0].upper())
            home = ESPN_ALIAS_TO_ABBR.get(ordered_teams[1], ordered_teams[1].upper())

            cells = _parse_anchor_cells(chunk)
            away_ml = _cell_price(cells, "moneyline", 0)
            home_ml = _cell_price(cells, "moneyline", 1) if len(_cells_for(cells, "moneyline")) > 1 else None
            total_cells = _cells_for(cells, "total")
            spread_cells = _cells_for(cells, "pointspread")
            away_total_line = total_cells[0].line if total_cells else None
            away_spread = spread_cells[0].line if spread_cells else None
            home_spread = spread_cells[1].line if len(spread_cells) > 1 else None
            total = away_total_line
            del away_ml, home_ml  # prices collected for diagnostics parity; lines are what we shadow

            results[(away, home)] = EspnGameOdds(
                away_team=away,
                home_team=home,
                away_spread=away_spread,
                home_spread=home_spread,
                total=total,
            )
        diagnostics = {
            "mode": "fresh",
            "blocks_seen": blocks_seen,
            "games_parsed": len(results),
            "with_total": sum(1 for item in results.values() if item.total is not None),
            "with_spreads": sum(
                1
                for item in results.values()
                if item.away_spread is not None and item.home_spread is not None
            ),
            "malformed_blocks": malformed_blocks,
        }
        return results, diagnostics


@dataclass
class _AnchorCell:
    market: str  # "moneyline" | "total" | "pointspread"
    line: float | None
    price: int | None


def _cells_for(cells: list[_AnchorCell], market: str) -> list[_AnchorCell]:
    return [cell for cell in cells if cell.market == market]


def _cell_price(cells: list[_AnchorCell], market: str, index: int) -> int | None:
    matching = _cells_for(cells, market)
    if len(matching) <= index:
        return None
    return matching[index].price


_LINE_TOKEN_PATTERN = re.compile(r"^[+-]?[ou]?\d+(?:\.\d+)?$", re.IGNORECASE)


def _parse_anchor_cells(chunk: str) -> list[_AnchorCell]:
    cells: list[_AnchorCell] = []
    for match in re.finditer(
        r'<a[^>]*data-exit-type-value="([^"]+)".*?</a>', chunk, flags=re.DOTALL
    ):
        raw_value = match.group(1).strip().lower()
        market_key = {"moneyline": "moneyline", "total": "total", "pointspread": "pointspread"}.get(
            raw_value.split(" ", 1)[0]
        )
        if market_key is None:
            continue
        texts = [text.strip() for text in re.findall(r">([^<>]+)<", match.group(0)) if text.strip()]
        line: float | None = None
        price: int | None = None
        for text in texts:
            candidate = text.replace("+", "")
            if price is None and re.fullmatch(r"-?\d{3,4}|100|-?1\d{2}", candidate):
                try:
                    price = int(candidate)
                    continue
                except ValueError:
                    pass
            if line is None and _LINE_TOKEN_PATTERN.fullmatch(text):
                cleaned = text.lower()
                if cleaned[0] in "+-":
                    sign = cleaned[0]
                    cleaned = sign + cleaned[1:].lstrip("ou")
                else:
                    cleaned = cleaned.lstrip("ou")
                try:
                    line = float(cleaned)
                except ValueError:
                    pass
        cells.append(_AnchorCell(market=market_key, line=line, price=price))
    return cells
