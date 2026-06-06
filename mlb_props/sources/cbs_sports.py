from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from ..cache import JsonCache
from ..config import OUTPUTS_DIR, PITCHER_OUTS_RECORDED
from ..models import Game, PropLine
from ..utils import fetch_text, normalize_name, safe_float
from .mlb_stats_api import build_probable_pitcher_index


class MlbCbsSportsSource:
    PAGE_URL = "https://www.cbssports.com/mlb/picks/prop-bets/"
    CARD_RE = re.compile(r"data-betting-carousel-card-tracking-options='([^']+)'")

    def __init__(self, shared_cache: JsonCache, lines_cache: JsonCache) -> None:
        self.shared_cache = shared_cache
        self.lines_cache = lines_cache
        self._diagnostics: dict[str, int] = {}
        self._issue_snapshots: list[dict] = []

    def fetch_prop_lines(self, games: list[Game], supported_prop_types: list[str]) -> list[PropLine]:
        self._reset_diagnostics(games)
        if PITCHER_OUTS_RECORDED not in supported_prop_types:
            return []

        html = self._fetch_page_html()
        if not html:
            return []

        probable_pitchers = build_probable_pitcher_index(games)
        probable_by_game = self._build_probable_pitchers_by_game(games)
        lines: list[PropLine] = []
        cards_seen = 0
        outs_cards_seen = 0
        for raw in self.CARD_RE.findall(html):
            cards_seen += 1
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                self._diagnostics["card_json_decode_failed"] += 1
                continue
            card = payload.get("cardData", {})
            button = card.get("buttonConfig", {})
            if button.get("marketName") != "Outs Recorded":
                continue
            outs_cards_seen += 1
            player_label = self._extract_player_label(button)
            if not player_label:
                self._diagnostics["player_label_missing"] += 1
                continue
            game_abbrev = (card.get("game") or {}).get("abbrev", "")
            probable = self._resolve_probable_pitcher(
                player_label=player_label,
                game_abbrev=game_abbrev,
                probable_pitchers=probable_pitchers,
                probable_by_game=probable_by_game,
            )
            if probable is None:
                self._diagnostics["probable_pitcher_mismatch"] += 1
                self._record_issue_snapshot(
                    "probable_pitcher_mismatch",
                    {"player_label": player_label, "game_abbrev": game_abbrev},
                )
                continue
            line_value = safe_float(button.get("line"), default=-1.0)
            if line_value < 0:
                self._diagnostics["outs_line_missing"] += 1
                continue
            game = probable["game"]
            lines.append(
                PropLine(
                    event_id=game.game_id,
                    game_date=game.game_date,
                    subject_name_raw=probable["subject_name"],
                    subject_name_norm=normalize_name(probable["subject_name"]),
                    subject_id=probable.get("subject_id"),
                    subject_role="pitcher",
                    team=probable["team"],
                    opponent=probable["opponent"],
                    hand="",
                    prop_type=PITCHER_OUTS_RECORDED,
                    line=line_value,
                    bookmaker="cbs_sports",
                    source="cbs_sports_featured_outs",
                    collected_at=datetime.now(timezone.utc),
                )
            )

        deduped = {(line.subject_name_norm, line.prop_type): line for line in lines}
        self._diagnostics["cards_seen"] = cards_seen
        self._diagnostics["outs_cards_seen"] = outs_cards_seen
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
        path = diagnostics_dir / f"cbs_outs_{screen_date.isoformat()}_{timestamp}.json"
        payload = {
            "screen_date": screen_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "diagnostics": self._diagnostics,
            "issue_snapshots": self._issue_snapshots,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return path

    def _fetch_page_html(self) -> str:
        cache_key = "cbs_mlb_prop_bets_html"
        cached = self.lines_cache.get(cache_key)
        if cached is not None:
            self._diagnostics["page_html_from_cache"] += 1
            return cached
        html = fetch_text(self.PAGE_URL, timeout=30)
        self.lines_cache.set(cache_key, html)
        return html

    def _build_probable_pitchers_by_game(self, games: list[Game]) -> dict[str, list[dict]]:
        items: dict[str, list[dict]] = {}
        for game in games:
            away_home_key = f"MLB_{game.game_date.strftime('%Y%m%d')}_{game.away_team}@{game.home_team}"
            home_away_key = f"MLB_{game.game_date.strftime('%Y%m%d')}_{game.home_team}@{game.away_team}"
            probable_items = [
                {
                    "subject_name": game.probable_home_pitcher,
                    "subject_id": game.probable_home_pitcher_id,
                    "team": game.home_team,
                    "opponent": game.away_team,
                    "game": game,
                },
                {
                    "subject_name": game.probable_away_pitcher,
                    "subject_id": game.probable_away_pitcher_id,
                    "team": game.away_team,
                    "opponent": game.home_team,
                    "game": game,
                },
            ]
            items[away_home_key] = probable_items
            items[home_away_key] = probable_items
        return items

    def _resolve_probable_pitcher(
        self,
        player_label: str,
        game_abbrev: str,
        probable_pitchers: dict[str, dict],
        probable_by_game: dict[str, list[dict]],
    ) -> dict | None:
        normalized = normalize_name(player_label)
        probable = probable_pitchers.get(normalized)
        if probable is not None:
            probable = probable.copy()
            probable["subject_name"] = next(
                (
                    item["subject_name"]
                    for item in probable_by_game.get(game_abbrev, [])
                    if normalize_name(item["subject_name"]) == normalized
                ),
                player_label,
            )
            probable["game"] = next(
                (item["game"] for item in probable_by_game.get(game_abbrev, []) if normalize_name(item["subject_name"]) == normalized),
                None,
            )
            return probable if probable.get("game") is not None else None

        game_candidates = probable_by_game.get(game_abbrev, [])
        if not game_candidates:
            return None
        player_parts = normalized.split()
        if not player_parts:
            return None
        first_token = player_parts[0]
        last_name = player_parts[-1]
        first_initial = first_token[0]
        for item in game_candidates:
            subject_name = item["subject_name"]
            if not subject_name:
                continue
            subject_norm = normalize_name(subject_name)
            subject_parts = subject_norm.split()
            if not subject_parts:
                continue
            if subject_parts[-1] != last_name:
                continue
            if subject_parts[0][0] != first_initial:
                continue
            return item
        return None

    def _extract_player_label(self, button: dict) -> str:
        meta = button.get("meta") or {}
        bets = meta.get("bets") or []
        for bet in bets:
            selection = bet.get("selection") or {}
            label = selection.get("label")
            if isinstance(label, str) and label.strip():
                return label.strip()
        return ""

    def _reset_diagnostics(self, games: list[Game]) -> None:
        self._diagnostics = {
            "games_seen": len(games),
            "page_html_from_cache": 0,
            "cards_seen": 0,
            "outs_cards_seen": 0,
            "card_json_decode_failed": 0,
            "player_label_missing": 0,
            "probable_pitcher_mismatch": 0,
            "outs_line_missing": 0,
            "outs_lines_found": 0,
            "outs_unique_lines_returned": 0,
        }
        self._issue_snapshots = []

    def _record_issue_snapshot(self, reason: str, payload: dict) -> None:
        if len(self._issue_snapshots) >= 10:
            return
        self._issue_snapshots.append({"reason": reason, "payload": payload})
