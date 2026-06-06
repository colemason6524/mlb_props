from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from ..cache import JsonCache
from ..config import TEAM_ABBR_TO_MLB_ID
from ..models import MatchupContext, PitcherGameLog, PropLine
from ..utils import fetch_json, safe_float, safe_int


@dataclass
class TeamHitter:
    player_id: int
    name: str
    bat_side: str
    plate_appearances: int
    k_rate: float
    walk_rate: float
    woba_proxy: float
    lineup_score: float = 0.0


@dataclass
class OpponentProfile:
    k_rate_vs_rhp: float
    k_rate_vs_lhp: float
    walk_rate_vs_rhp: float
    walk_rate_vs_lhp: float
    woba_proxy_vs_rhp: float
    woba_proxy_vs_lhp: float
    outs_factor_vs_rhp: float
    outs_factor_vs_lhp: float
    source: str = "mlb_stats_api_roster_proxy"


class MlbOpponentContextSource:
    BASE_URL = "https://statsapi.mlb.com/api/v1"
    RECENT_GAMES_WINDOW = 10
    MAX_RECENT_GAMES = 6
    MIN_PROJECTED_HITTERS = 7

    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache

    def build_matchup_contexts(
        self,
        prop_lines: list[PropLine],
        logs_by_pitcher: dict[str, list[PitcherGameLog]],
        base_contexts: dict[tuple[str, str], MatchupContext],
        season: int,
    ) -> dict[tuple[str, str], MatchupContext]:
        profiles_by_team_hand: dict[tuple[str, str, str], OpponentProfile] = {}
        enriched: dict[tuple[str, str], MatchupContext] = {}

        for line in prop_lines:
            base_context = base_contexts.get((line.team, line.opponent))
            if base_context is None:
                continue

            logs = logs_by_pitcher.get(line.subject_name_norm, [])
            pitcher_hand = next((log.hand for log in logs if log.hand), line.hand or "R")
            profile_key = (line.opponent, pitcher_hand, line.game_date.isoformat())
            if profile_key not in profiles_by_team_hand:
                profiles_by_team_hand[profile_key] = self.fetch_team_profile(
                    team_abbr=line.opponent,
                    season=season,
                    pitcher_hand=pitcher_hand,
                    as_of_date=line.game_date,
                )
            profile = profiles_by_team_hand[profile_key]

            if pitcher_hand == "L":
                opponent_k_rate = profile.k_rate_vs_lhp
                opponent_walk_rate = profile.walk_rate_vs_lhp
                opponent_woba = profile.woba_proxy_vs_lhp
                outs_factor = profile.outs_factor_vs_lhp
            else:
                opponent_k_rate = profile.k_rate_vs_rhp
                opponent_walk_rate = profile.walk_rate_vs_rhp
                opponent_woba = profile.woba_proxy_vs_rhp
                outs_factor = profile.outs_factor_vs_rhp

            blended_outs_factor = round((base_context.opponent_outs_factor + outs_factor) / 2.0, 3)
            enriched[(line.team, line.opponent)] = MatchupContext(
                team=line.team,
                opponent=line.opponent,
                opponent_k_rate_vs_hand=opponent_k_rate,
                opponent_walk_rate_vs_hand=opponent_walk_rate,
                opponent_woba_vs_hand=opponent_woba,
                opponent_outs_factor=blended_outs_factor,
                park_run_factor=base_context.park_run_factor,
                moneyline=base_context.moneyline,
                source=profile.source,
            )

        return enriched or base_contexts

    def fetch_team_profile(
        self,
        team_abbr: str,
        season: int,
        pitcher_hand: str,
        as_of_date: date,
    ) -> OpponentProfile:
        team_id = TEAM_ABBR_TO_MLB_ID.get(team_abbr)
        if team_id is None:
            return self._default_profile()

        projected_cache_key = (
            f"mlb_opponent_profile_projected_{team_abbr}_{season}_{pitcher_hand}_{as_of_date.isoformat()}"
        )
        cached_projected = self.cache.get(projected_cache_key)
        if cached_projected is not None:
            return OpponentProfile(**cached_projected)

        hitters = self._fetch_projected_lineup_hitters(
            team_id=team_id,
            team_abbr=team_abbr,
            season=season,
            as_of_date=as_of_date,
        )
        if len(hitters) >= self.MIN_PROJECTED_HITTERS:
            profile = self._build_profile(hitters, source="mlb_stats_api_projected_lineup")
            self.cache.set(projected_cache_key, profile.__dict__)
            return profile

        roster_profile = self._fetch_roster_profile(team_id=team_id, team_abbr=team_abbr, season=season)
        self.cache.set(projected_cache_key, roster_profile.__dict__)
        return roster_profile

    def _fetch_projected_lineup_hitters(
        self,
        team_id: int,
        team_abbr: str,
        season: int,
        as_of_date: date,
    ) -> list[TeamHitter]:
        recent_game_ids = self._fetch_recent_game_ids(team_id=team_id, as_of_date=as_of_date)
        if not recent_game_ids:
            return []

        hitters_by_id: dict[int, TeamHitter] = {}
        for game_index, game_id in enumerate(recent_game_ids[: self.MAX_RECENT_GAMES]):
            lineup_hitters = self._fetch_game_lineup(team_id=team_id, game_id=game_id)
            recency_multiplier = max(0.55, 1.0 - (0.1 * game_index))
            for slot, hitter in enumerate(lineup_hitters, start=1):
                slot_multiplier = max(0.7, 1.08 - (0.03 * (slot - 1)))
                start_weight = recency_multiplier * slot_multiplier
                existing = hitters_by_id.get(hitter.player_id)
                if existing is None:
                    hitter.lineup_score = start_weight
                    hitters_by_id[hitter.player_id] = hitter
                    continue
                existing.lineup_score += start_weight
                existing.plate_appearances = max(existing.plate_appearances, hitter.plate_appearances)
                if hitter.k_rate > 0:
                    existing.k_rate = hitter.k_rate
                if hitter.walk_rate > 0:
                    existing.walk_rate = hitter.walk_rate
                if hitter.woba_proxy > 0:
                    existing.woba_proxy = hitter.woba_proxy

        projected = sorted(hitters_by_id.values(), key=lambda hitter: hitter.lineup_score, reverse=True)[:9]
        if len(projected) < self.MIN_PROJECTED_HITTERS:
            return []

        # Give the most likely starters more influence than fringe inclusions.
        top_score = projected[0].lineup_score or 1.0
        for hitter in projected:
            hitter.lineup_score = round(max(0.65, hitter.lineup_score / top_score), 3)
        return projected

    def _fetch_recent_game_ids(self, team_id: int, as_of_date: date) -> list[int]:
        start_date = as_of_date - timedelta(days=self.RECENT_GAMES_WINDOW)
        cache_key = f"mlb_recent_games_{team_id}_{as_of_date.isoformat()}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return [safe_int(game_id) for game_id in cached if safe_int(game_id) > 0]

        url = (
            f"{self.BASE_URL}/schedule"
            f"?sportId=1&teamId={team_id}"
            f"&startDate={start_date.isoformat()}&endDate={as_of_date.isoformat()}"
        )
        data = fetch_json(url)
        game_ids: list[int] = []
        for day in data.get("dates", []):
            for game in day.get("games", []):
                if game.get("status", {}).get("codedGameState") != "F":
                    continue
                game_pk = safe_int(game.get("gamePk"))
                if game_pk > 0:
                    game_ids.append(game_pk)
        game_ids = list(reversed(game_ids))[: self.MAX_RECENT_GAMES]
        self.cache.set(cache_key, game_ids)
        return game_ids

    def _fetch_game_lineup(self, team_id: int, game_id: int) -> list[TeamHitter]:
        cache_key = f"mlb_lineup_boxscore_{game_id}_{team_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return [TeamHitter(**item) for item in cached]

        url = f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
        data = fetch_json(url)
        teams = data.get("liveData", {}).get("boxscore", {}).get("teams", {})
        team_box = None
        for side in ("home", "away"):
            side_box = teams.get(side) or {}
            if safe_int(side_box.get("team", {}).get("id")) == team_id:
                team_box = side_box
                break
        if team_box is None:
            return []

        batting_order = team_box.get("battingOrder", []) or []
        players = team_box.get("players", {}) or {}
        lineup: list[TeamHitter] = []
        for player_id in batting_order[:9]:
            player_key = f"ID{player_id}"
            player = players.get(player_key) or {}
            person = player.get("person") or {}
            season_batting = (player.get("seasonStats") or {}).get("batting") or {}
            plate_appearances = max(safe_int(season_batting.get("plateAppearances")), 1)
            obp = safe_float(season_batting.get("obp"))
            slg = safe_float(season_batting.get("slg"))
            lineup.append(
                TeamHitter(
                    player_id=safe_int(person.get("id")),
                    name=person.get("fullName", ""),
                    bat_side=(player.get("batSide") or {}).get("code", "R"),
                    plate_appearances=plate_appearances,
                    k_rate=safe_float(season_batting.get("strikeOuts")) / plate_appearances,
                    walk_rate=safe_float(season_batting.get("baseOnBalls")) / plate_appearances,
                    woba_proxy=(0.7 * obp) + (0.3 * (slg / 2.0)),
                )
            )

        if lineup:
            self.cache.set(cache_key, [hitter.__dict__ for hitter in lineup])
        return lineup

    def _fetch_roster_profile(self, team_id: int, team_abbr: str, season: int) -> OpponentProfile:
        cache_key = f"mlb_opponent_profile_roster_{team_abbr}_{season}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return OpponentProfile(**cached)

        url = (
            f"{self.BASE_URL}/teams/{team_id}/roster"
            f"?rosterType=active"
            f"&hydrate=person(stats(group=[hitting],type=[season],season={season}),batSide)"
        )
        data = fetch_json(url)
        roster = data.get("roster", [])
        hitters: list[TeamHitter] = []
        for entry in roster:
            person = entry.get("person") or {}
            if (person.get("primaryPosition") or {}).get("type") == "Pitcher":
                continue
            stat_splits = []
            for stat_block in person.get("stats", []):
                stat_splits.extend(stat_block.get("splits", []))
            if not stat_splits:
                continue
            stat = stat_splits[0].get("stat", {})
            plate_appearances = max(safe_int(stat.get("plateAppearances")), 1)
            obp = safe_float(stat.get("obp"))
            slg = safe_float(stat.get("slg"))
            hitters.append(
                TeamHitter(
                    player_id=safe_int(person.get("id")),
                    name=person.get("fullName", ""),
                    bat_side=(person.get("batSide") or {}).get("code", "R"),
                    plate_appearances=plate_appearances,
                    k_rate=safe_float(stat.get("strikeOuts")) / plate_appearances,
                    walk_rate=safe_float(stat.get("baseOnBalls")) / plate_appearances,
                    woba_proxy=(0.7 * obp) + (0.3 * (slg / 2.0)),
                    lineup_score=1.0,
                )
            )

        profile = self._build_profile(hitters, source="mlb_stats_api_roster_proxy") if hitters else self._default_profile()
        self.cache.set(cache_key, profile.__dict__)
        return profile

    def _build_profile(self, hitters: list[TeamHitter], source: str) -> OpponentProfile:
        return OpponentProfile(
            k_rate_vs_rhp=self._weighted_rate(hitters, pitcher_hand="R", stat_key="k_rate"),
            k_rate_vs_lhp=self._weighted_rate(hitters, pitcher_hand="L", stat_key="k_rate"),
            walk_rate_vs_rhp=self._weighted_rate(hitters, pitcher_hand="R", stat_key="walk_rate"),
            walk_rate_vs_lhp=self._weighted_rate(hitters, pitcher_hand="L", stat_key="walk_rate"),
            woba_proxy_vs_rhp=self._weighted_rate(hitters, pitcher_hand="R", stat_key="woba_proxy"),
            woba_proxy_vs_lhp=self._weighted_rate(hitters, pitcher_hand="L", stat_key="woba_proxy"),
            outs_factor_vs_rhp=self._outs_factor(self._weighted_rate(hitters, pitcher_hand="R", stat_key="woba_proxy")),
            outs_factor_vs_lhp=self._outs_factor(self._weighted_rate(hitters, pitcher_hand="L", stat_key="woba_proxy")),
            source=source,
        )

    def _weighted_rate(self, hitters: list[TeamHitter], pitcher_hand: str, stat_key: str) -> float:
        weighted_total = 0.0
        total_weight = 0.0
        for hitter in hitters:
            weight = hitter.plate_appearances * hitter.lineup_score * self._platoon_weight(hitter.bat_side, pitcher_hand)
            weighted_total += getattr(hitter, stat_key) * weight
            total_weight += weight
        if total_weight == 0:
            return 0.0
        return round(weighted_total / total_weight, 3)

    def _platoon_weight(self, bat_side: str, pitcher_hand: str) -> float:
        if bat_side == "S":
            return 1.15
        if bat_side == pitcher_hand:
            return 0.75
        return 1.0

    def _outs_factor(self, woba_proxy: float) -> float:
        return round(1.0 + ((0.320 - woba_proxy) * 1.75), 3)

    def _default_profile(self) -> OpponentProfile:
        return OpponentProfile(
            k_rate_vs_rhp=0.22,
            k_rate_vs_lhp=0.22,
            walk_rate_vs_rhp=0.08,
            walk_rate_vs_lhp=0.08,
            woba_proxy_vs_rhp=0.320,
            woba_proxy_vs_lhp=0.320,
            outs_factor_vs_rhp=1.0,
            outs_factor_vs_lhp=1.0,
        )
