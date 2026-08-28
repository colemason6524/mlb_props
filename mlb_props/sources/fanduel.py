from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from ..cache import JsonCache
from ..config import OUTPUTS_DIR, PARK_RUN_FACTORS, PITCHER_STRIKEOUTS, TEAM_ABBR_TO_FANDUEL_SLUG
from ..models import Game, MatchupContext, PropLine
from ..utils import fetch_text, normalize_name
from .mlb_stats_api import build_probable_pitcher_index


class MlbFanDuelSource:
    BASE_URL = "https://sportsbook.fanduel.com/teams/mlb"

    def __init__(self, shared_cache: JsonCache, lines_cache: JsonCache) -> None:
        self.shared_cache = shared_cache
        self.lines_cache = lines_cache
        self._matchup_contexts: dict[tuple[str, str], MatchupContext] = {}
        self._diagnostics: dict[str, int] = {}
        self._issue_snapshots: list[dict] = []

    def fetch_prop_lines(self, games: list[Game], supported_prop_types: list[str]) -> list[PropLine]:
        probable_pitchers = build_probable_pitcher_index(games)
        self._reset_diagnostics(games)
        if PITCHER_STRIKEOUTS not in supported_prop_types:
            self._matchup_contexts = self._build_default_matchup_contexts(games)
            return []

        lines: list[PropLine] = []
        matchup_contexts: dict[tuple[str, str], MatchupContext] = {}
        for game in games:
            for team, opponent in ((game.home_team, game.away_team), (game.away_team, game.home_team)):
                self._diagnostics["team_pages_attempted"] += 1
                page_data, from_cache = self._fetch_team_page_data(team, game)
                if not page_data:
                    self._diagnostics["team_pages_missing"] += 1
                    continue
                self._diagnostics["team_pages_loaded"] += 1
                context = self._extract_matchup_context(page_data, game, team, opponent)
                if context is not None:
                    matchup_contexts[(team, opponent)] = context
                    self._diagnostics["matchup_contexts_found"] += 1
                line, miss_reason, miss_payload = self._extract_strikeout_line(
                    page_data,
                    game,
                    probable_pitchers,
                    team,
                    opponent,
                )
                if line is None and from_cache and miss_reason in {"strikeout_market_missing", "strikeout_line_missing"}:
                    self._diagnostics["fresh_fetch_retries"] += 1
                    refreshed_page_data, _ = self._fetch_team_page_data(team, game, bypass_cache=True)
                    if refreshed_page_data:
                        page_data = refreshed_page_data
                        context = self._extract_matchup_context(page_data, game, team, opponent)
                        if context is not None:
                            matchup_contexts[(team, opponent)] = context
                        line, miss_reason, miss_payload = self._extract_strikeout_line(
                            page_data,
                            game,
                            probable_pitchers,
                            team,
                            opponent,
                            diagnostics_prefix="refreshed_",
                        )
                if line is not None:
                    lines.append(line)
                    self._diagnostics["strikeout_lines_found"] += 1
                elif miss_reason:
                    self._diagnostics[miss_reason] += 1
                    self._record_issue_snapshot(team, game, miss_reason, miss_payload)

        deduped: dict[tuple[str, str], PropLine] = {}
        for line in lines:
            if (line.subject_name_norm, line.prop_type) in deduped:
                self._diagnostics["duplicate_lines_dropped"] += 1
            deduped[(line.subject_name_norm, line.prop_type)] = line
        self._diagnostics["unique_lines_returned"] = len(deduped)
        self._matchup_contexts = matchup_contexts or self._build_default_matchup_contexts(games)
        return sorted(deduped.values(), key=lambda item: (item.team, item.subject_name_raw, item.prop_type))

    def fetch_matchup_contexts(self, _screen_date: date) -> dict[tuple[str, str], MatchupContext]:
        return self._matchup_contexts

    def diagnostics(self) -> dict[str, int]:
        return dict(self._diagnostics)

    def export_diagnostics_snapshot(self, screen_date: date) -> Path | None:
        if not self._issue_snapshots:
            return None
        diagnostics_dir = OUTPUTS_DIR / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = diagnostics_dir / f"fanduel_scrape_{screen_date.isoformat()}_{timestamp}.json"
        payload = {
            "screen_date": screen_date.isoformat(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "diagnostics": self._diagnostics,
            "issue_snapshots": self._issue_snapshots,
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True))
        return path

    def _fetch_team_page_data(self, team: str, game: Game, bypass_cache: bool = False) -> tuple[dict | None, bool]:
        slug = TEAM_ABBR_TO_FANDUEL_SLUG.get(team)
        if not slug:
            self._diagnostics["unknown_team_slug"] += 1
            return None, False
        cache_key = f"fanduel_mlb_team_{slug}"
        cached = None if bypass_cache else self.lines_cache.get(cache_key)
        if cached is not None:
            self._diagnostics["team_pages_from_cache"] += 1
            return cached, True

        try:
            html = fetch_text(f"{self.BASE_URL}/{slug}/odds")
        except RuntimeError:
            self._diagnostics["team_pages_fetch_error"] = (
                self._diagnostics.get("team_pages_fetch_error", 0) + 1
            )
            return None, False
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html)
        if not match:
            return None, False
        payload = json.loads(match.group(1))
        page_data = payload.get("props", {}).get("pageProps", {})
        page_data["__raw_html"] = html
        self.lines_cache.set(cache_key, page_data)
        if bypass_cache:
            self._diagnostics["fresh_fetch_successes"] += 1
        return page_data, False

    def _extract_strikeout_line(
        self,
        page_data: dict,
        game: Game,
        probable_pitchers: dict[str, dict],
        team: str,
        opponent: str,
        diagnostics_prefix: str = "",
    ) -> tuple[PropLine | None, str | None, dict]:
        strikeouts = (((page_data.get("team") or {}).get("teamLeader") or {}).get("strikeouts") or {})
        leader = strikeouts.get("leader") or {}
        prop_bet = strikeouts.get("leaderPropBet") or {}
        player_name = leader.get("name", "")
        player_props = prop_bet.get("playerPropsForGame") or []
        if not player_name or not player_props:
            fallback_line, fallback_payload = self._extract_probable_pitcher_strikeout_line_fallback(
                page_data=page_data,
                game=game,
                team=team,
                opponent=opponent,
            )
            if fallback_line is not None:
                self._diagnostics[f"{diagnostics_prefix}fallback_probable_pitcher_line_found"] += 1
                return fallback_line, None, fallback_payload
            return (
                None,
                f"{diagnostics_prefix}strikeout_market_missing",
                {
                    "leader_name": player_name,
                    "team_leader_keys": sorted((((page_data.get("team") or {}).get("teamLeader")) or {}).keys()),
                    "page_data_excerpt": page_data.get("team"),
                },
            )
        probable = probable_pitchers.get(normalize_name(player_name))
        if probable is None or probable["team"] != team or probable["opponent"] != opponent:
            fallback_line, fallback_payload = self._extract_probable_pitcher_strikeout_line_fallback(
                page_data=page_data,
                game=game,
                team=team,
                opponent=opponent,
            )
            if fallback_line is not None:
                self._diagnostics[f"{diagnostics_prefix}fallback_probable_pitcher_line_found"] += 1
                return fallback_line, None, fallback_payload
            return (
                None,
                f"{diagnostics_prefix}probable_pitcher_mismatch",
                {
                    "leader_name": player_name,
                    "leader_name_norm": normalize_name(player_name),
                    "expected_team": team,
                    "expected_opponent": opponent,
                    "matched_probable": probable,
                },
            )
        first_prop = player_props[0]
        line_value = first_prop.get("playerLine")
        if line_value is None:
            fallback_line, fallback_payload = self._extract_probable_pitcher_strikeout_line_fallback(
                page_data=page_data,
                game=game,
                team=team,
                opponent=opponent,
            )
            if fallback_line is not None:
                self._diagnostics[f"{diagnostics_prefix}fallback_probable_pitcher_line_found"] += 1
                return fallback_line, None, fallback_payload
            return (
                None,
                f"{diagnostics_prefix}strikeout_line_missing",
                {
                    "leader_name": player_name,
                    "player_props": player_props[:3],
                },
            )
        return (
            PropLine(
                event_id=game.game_id,
                game_date=game.game_date,
                subject_name_raw=player_name,
                subject_name_norm=normalize_name(player_name),
                subject_id=probable.get("subject_id"),
                subject_role="pitcher",
                team=team,
                opponent=opponent,
                hand="",
                prop_type=PITCHER_STRIKEOUTS,
                line=float(line_value),
                bookmaker="fanduel",
                source="fanduel_scrape",
                collected_at=datetime.now(timezone.utc),
            ),
            None,
            {},
        )

    def _extract_probable_pitcher_strikeout_line_fallback(
        self,
        page_data: dict,
        game: Game,
        team: str,
        opponent: str,
    ) -> tuple[PropLine | None, dict]:
        probable_name, probable_id = self._probable_pitcher_for_team(game, team)
        if not probable_name:
            return None, {"reason": "probable_pitcher_name_missing"}

        probable_name_norm = normalize_name(probable_name)
        line_value = self._search_page_data_for_probable_pitcher_line(page_data, probable_name_norm)
        if line_value is None:
            raw_html = page_data.get("__raw_html", "")
            return None, {
                "reason": "fallback_line_missing",
                "probable_name": probable_name,
                "probable_name_norm": probable_name_norm,
                "probable_name_present_in_raw_html": bool(raw_html and probable_name in raw_html),
            }

        return (
            PropLine(
                event_id=game.game_id,
                game_date=game.game_date,
                subject_name_raw=probable_name,
                subject_name_norm=probable_name_norm,
                subject_id=probable_id,
                subject_role="pitcher",
                team=team,
                opponent=opponent,
                hand="",
                prop_type=PITCHER_STRIKEOUTS,
                line=float(line_value),
                bookmaker="fanduel",
                source="fanduel_scrape_fallback",
                collected_at=datetime.now(timezone.utc),
            ),
            {
                "reason": "fallback_probable_pitcher_line_found",
                "probable_name": probable_name,
                "line_value": float(line_value),
            },
        )

    def _probable_pitcher_for_team(self, game: Game, team: str) -> tuple[str, int | None]:
        if game.home_team == team:
            return game.probable_home_pitcher, game.probable_home_pitcher_id
        if game.away_team == team:
            return game.probable_away_pitcher, game.probable_away_pitcher_id
        return "", None

    def _search_page_data_for_probable_pitcher_line(self, page_data: dict, probable_name_norm: str) -> float | None:
        best_line: float | None = None

        def walk(node):
            nonlocal best_line
            if best_line is not None:
                return
            if isinstance(node, dict):
                text_blob = json.dumps(node, sort_keys=True)
                if probable_name_norm in normalize_name(text_blob) and "strikeout" in text_blob.lower():
                    candidate = node.get("playerLine") or node.get("line")
                    if self._is_plausible_strikeout_line(candidate):
                        best_line = float(candidate)
                        return
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(page_data)
        return best_line

    def _is_plausible_strikeout_line(self, value: object) -> bool:
        if not isinstance(value, (int, float)):
            return False
        numeric = float(value)
        if numeric <= 0 or numeric > 15:
            return False
        fractional = numeric - int(numeric)
        return fractional in {0.0, 0.5}

    def _extract_matchup_context(
        self,
        page_data: dict,
        game: Game,
        team: str,
        opponent: str,
    ) -> MatchupContext | None:
        next_games = ((((page_data.get("team") or {}).get("prevNextGame") or {}).get("nextGames")) or [])
        if not next_games:
            return None
        next_game = next_games[0]
        odds = next_game.get("odds") or {}
        home = odds.get("home") or {}
        away = odds.get("away") or {}

        team_moneyline = 0
        for side in (home, away):
            if side.get("teamCode") != team:
                continue
            team_moneyline = self._extract_moneyline(side)
            break

        park_factor = PARK_RUN_FACTORS.get(game.home_team, 1.0)
        return MatchupContext(
            team=team,
            opponent=opponent,
            opponent_k_rate_vs_hand=0.22,
            opponent_walk_rate_vs_hand=0.08,
            opponent_woba_vs_hand=0.315,
            opponent_outs_factor=1.0 + (0.04 if team_moneyline <= -140 else -0.03 if team_moneyline >= 130 else 0.0),
            park_run_factor=park_factor,
            moneyline=team_moneyline,
            source="fanduel_scrape",
        )

    def _extract_moneyline(self, side: dict) -> int:
        try:
            return int((((side.get("oddsDetails") or {}).get("moneyLine") or {}).get("odd")) or 0)
        except (TypeError, ValueError):
            return 0

    def _build_default_matchup_contexts(self, games: list[Game]) -> dict[tuple[str, str], MatchupContext]:
        contexts: dict[tuple[str, str], MatchupContext] = {}
        for game in games:
            for team, opponent in ((game.home_team, game.away_team), (game.away_team, game.home_team)):
                contexts[(team, opponent)] = MatchupContext(
                    team=team,
                    opponent=opponent,
                    opponent_k_rate_vs_hand=0.22,
                    opponent_walk_rate_vs_hand=0.08,
                    opponent_woba_vs_hand=0.315,
                    opponent_outs_factor=1.0,
                    park_run_factor=PARK_RUN_FACTORS.get(game.home_team, 1.0),
                    moneyline=0,
                    source="fanduel_scrape",
                )
        return contexts

    def _reset_diagnostics(self, games: list[Game]) -> None:
        self._diagnostics = {
            "games_seen": len(games),
            "expected_team_pages": len(games) * 2,
            "team_pages_attempted": 0,
            "team_pages_loaded": 0,
            "team_pages_from_cache": 0,
            "team_pages_missing": 0,
            "next_data_missing": 0,
            "unknown_team_slug": 0,
            "matchup_contexts_found": 0,
            "strikeout_market_missing": 0,
            "probable_pitcher_mismatch": 0,
            "strikeout_line_missing": 0,
            "fallback_probable_pitcher_line_found": 0,
            "refreshed_strikeout_market_missing": 0,
            "refreshed_probable_pitcher_mismatch": 0,
            "refreshed_strikeout_line_missing": 0,
            "refreshed_fallback_probable_pitcher_line_found": 0,
            "strikeout_lines_found": 0,
            "duplicate_lines_dropped": 0,
            "unique_lines_returned": 0,
            "fresh_fetch_retries": 0,
            "fresh_fetch_successes": 0,
        }
        self._issue_snapshots = []

    def _record_issue_snapshot(self, team: str, game: Game, reason: str, payload: dict) -> None:
        if len(self._issue_snapshots) >= 12:
            return
        self._issue_snapshots.append(
            {
                "team": team,
                "game_id": game.game_id,
                "game_date": game.game_date.isoformat(),
                "opponent_home": game.home_team,
                "opponent_away": game.away_team,
                "reason": reason,
                "payload": payload,
            }
        )
