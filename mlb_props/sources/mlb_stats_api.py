from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import quote

from ..cache import JsonCache
from ..config import ODDS_API_TEAM_ABBR, TEAM_ABBR_TO_MLB_ID
from ..models import BatterGameLog, BatterPitcherHistory, Game, PitcherGameLog, PropLine
from ..utils import fetch_json, normalize_name, normalize_team_abbr, parse_iso_datetime, safe_int


@dataclass(frozen=True)
class ProjectedBatter:
    player_id: int
    name: str
    team: str
    opponent: str
    position: str
    batting_order: int | None = None


class MlbStatsApiSource:
    BASE_URL = "https://statsapi.mlb.com/api/v1"

    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache

    def _get_json(self, cache_key: str, url: str) -> dict:
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        data = fetch_json(url)
        self.cache.set(cache_key, data)
        return data


class MlbStatsApiSlateSource(MlbStatsApiSource):
    def fetch_games(self, screen_date: date) -> list[Game]:
        hydrate = quote("probablePitcher(note),team", safe="(),")
        url = f"{self.BASE_URL}/schedule?sportId=1&date={screen_date.isoformat()}&hydrate={hydrate}"
        data = self._get_json(f"mlb_schedule_{screen_date.isoformat()}", url)
        games: list[Game] = []
        for day in data.get("dates", []):
            for game in day.get("games", []):
                teams = game.get("teams", {})
                home = teams.get("home", {})
                away = teams.get("away", {})
                home_team = home.get("team", {}).get("abbreviation", "")
                away_team = away.get("team", {}).get("abbreviation", "")
                if not home_team or not away_team:
                    continue
                home_pitcher = home.get("probablePitcher") or {}
                away_pitcher = away.get("probablePitcher") or {}
                games.append(
                    Game(
                        game_id=str(game.get("gamePk", "")),
                        game_date=screen_date,
                        game_time=parse_iso_datetime(game["gameDate"]),
                        home_team=normalize_team_abbr(home_team),
                        away_team=normalize_team_abbr(away_team),
                        probable_home_pitcher=home_pitcher.get("fullName", ""),
                        probable_away_pitcher=away_pitcher.get("fullName", ""),
                        probable_home_pitcher_id=home_pitcher.get("id"),
                        probable_away_pitcher_id=away_pitcher.get("id"),
                        source="mlb_stats_api",
                    )
                )
        return games


class MlbStatsApiPitcherLogsSource(MlbStatsApiSource):
    def fetch_logs_for_lines(self, prop_lines: list[PropLine], season: int) -> dict[str, list[PitcherGameLog]]:
        logs_by_pitcher: dict[str, list[PitcherGameLog]] = {}
        for line in prop_lines:
            key = normalize_name(line.subject_name_raw)
            if key in logs_by_pitcher:
                continue
            logs_by_pitcher[key] = self.fetch_logs(
                pitcher_id=line.subject_id,
                pitcher_name=line.subject_name_raw,
                team_hint=line.team,
                season=season,
            )
        return logs_by_pitcher

    def fetch_logs(
        self,
        pitcher_id: int | None,
        pitcher_name: str,
        team_hint: str,
        season: int,
    ) -> list[PitcherGameLog]:
        if pitcher_id is None:
            return []

        hydrate = quote(f"stats(group=[pitching],type=[gameLog],season={season})", safe="=[](),")
        url = f"{self.BASE_URL}/people?personIds={pitcher_id}&hydrate={hydrate}"
        cache_key = f"mlb_pitcher_gamelog_{pitcher_id}_{season}"
        data = self._get_json(cache_key, url)
        people = data.get("people", [])
        if not people:
            return []

        person = people[0]
        raw_name = person.get("fullName", pitcher_name)
        splits = []
        for stat_block in person.get("stats", []):
            splits.extend(stat_block.get("splits", []))

        results: list[PitcherGameLog] = []
        for split in splits:
            stat = split.get("stat", {})
            game = split.get("game", {})
            game_date_raw = split.get("date") or game.get("gameDate")
            if not game_date_raw:
                continue
            results.append(
                PitcherGameLog(
                    pitcher_name_raw=raw_name,
                    pitcher_name_norm=normalize_name(raw_name),
                    pitcher_id=pitcher_id,
                    game_date=parse_iso_datetime(f"{game_date_raw}T00:00:00Z").date() if len(game_date_raw) == 10 else parse_iso_datetime(game_date_raw).date(),
                    team=normalize_team_abbr(
                        (split.get("team") or {}).get("abbreviation")
                        or ODDS_API_TEAM_ABBR.get((split.get("team") or {}).get("name", ""), team_hint)
                        or team_hint
                    ),
                    opponent=normalize_team_abbr(
                        (split.get("opponent") or {}).get("abbreviation")
                        or ODDS_API_TEAM_ABBR.get((split.get("opponent") or {}).get("name", ""), "")
                    ),
                    hand=(person.get("pitchHand") or {}).get("code", ""),
                    outs_recorded=safe_int(stat.get("outs")),
                    strikeouts=safe_int(stat.get("strikeOuts")),
                    pitches_thrown=safe_int(stat.get("numberOfPitches")),
                    batters_faced=safe_int(stat.get("battersFaced")),
                    walks=safe_int(stat.get("baseOnBalls")),
                    hits_allowed=safe_int(stat.get("hits")),
                    earned_runs=safe_int(stat.get("earnedRuns")),
                    did_start=safe_int(stat.get("gamesStarted"), 1) > 0,
                    source="mlb_stats_api",
                )
            )

        return sorted(results, key=lambda item: item.game_date, reverse=True)

    def fetch_logs_for_games(self, games: list[Game], season: int) -> dict[str, list[PitcherGameLog]]:
        logs_by_pitcher: dict[str, list[PitcherGameLog]] = {}
        for game in games:
            for pitcher_id, pitcher_name, team_hint in (
                (game.probable_home_pitcher_id, game.probable_home_pitcher, game.home_team),
                (game.probable_away_pitcher_id, game.probable_away_pitcher, game.away_team),
            ):
                if not pitcher_name:
                    continue
                key = normalize_name(pitcher_name)
                if key in logs_by_pitcher:
                    continue
                logs_by_pitcher[key] = self.fetch_logs(
                    pitcher_id=pitcher_id,
                    pitcher_name=pitcher_name,
                    team_hint=team_hint,
                    season=season,
                )
        return logs_by_pitcher


class MlbStatsApiBatterLogsSource(MlbStatsApiSource):
    MAX_BATTERS_PER_TEAM = 11
    RECENT_LINEUP_DAYS = 10
    RECENT_LINEUP_GAMES = 4

    def fetch_projected_batters_for_games(self, games: list[Game]) -> list[ProjectedBatter]:
        projected: list[ProjectedBatter] = []
        seen: set[tuple[int, str]] = set()
        for game in games:
            for team, opponent in ((game.home_team, game.away_team), (game.away_team, game.home_team)):
                team_hitters = self._fetch_recent_lineup_batters(
                    team=team,
                    opponent=opponent,
                    as_of_date=game.game_date,
                )
                if len(team_hitters) < 7:
                    team_hitters = self._fetch_active_roster_batters(team=team, opponent=opponent)
                for hitter in team_hitters[: self.MAX_BATTERS_PER_TEAM]:
                    key = (hitter.player_id, team)
                    if hitter.player_id <= 0 or key in seen:
                        continue
                    seen.add(key)
                    projected.append(hitter)
        return projected

    def fetch_logs_for_batters(
        self,
        batters: list[ProjectedBatter],
        season: int,
    ) -> dict[str, list[BatterGameLog]]:
        logs_by_batter: dict[str, list[BatterGameLog]] = {}
        for batter in batters:
            key = normalize_name(batter.name)
            if key in logs_by_batter:
                continue
            logs_by_batter[key] = self.fetch_logs(
                batter_id=batter.player_id,
                batter_name=batter.name,
                team_hint=batter.team,
                season=season,
                position_hint=batter.position,
                batting_order_hint=batter.batting_order,
            )
        return logs_by_batter

    def fetch_logs(
        self,
        batter_id: int | None,
        batter_name: str,
        team_hint: str,
        season: int,
        position_hint: str = "",
        batting_order_hint: int | None = None,
    ) -> list[BatterGameLog]:
        if batter_id is None:
            return []

        hydrate = quote(f"stats(group=[hitting],type=[gameLog],season={season})", safe="=[](),")
        url = f"{self.BASE_URL}/people?personIds={batter_id}&hydrate={hydrate}"
        data = self._get_json(f"mlb_batter_gamelog_{batter_id}_{season}", url)
        people = data.get("people", [])
        if not people:
            return []

        person = people[0]
        raw_name = person.get("fullName", batter_name)
        bat_side = (person.get("batSide") or {}).get("code", "")
        position = (person.get("primaryPosition") or {}).get("abbreviation", position_hint)
        splits = []
        for stat_block in person.get("stats", []):
            splits.extend(stat_block.get("splits", []))

        results: list[BatterGameLog] = []
        for split in splits:
            stat = split.get("stat", {})
            game = split.get("game", {})
            game_date_raw = split.get("date") or game.get("gameDate")
            if not game_date_raw:
                continue
            at_bats = safe_int(stat.get("atBats"))
            plate_appearances = safe_int(stat.get("plateAppearances"), at_bats + safe_int(stat.get("baseOnBalls")))
            if plate_appearances <= 0:
                continue
            results.append(
                BatterGameLog(
                    batter_name_raw=raw_name,
                    batter_name_norm=normalize_name(raw_name),
                    batter_id=batter_id,
                    game_date=parse_iso_datetime(f"{game_date_raw}T00:00:00Z").date() if len(game_date_raw) == 10 else parse_iso_datetime(game_date_raw).date(),
                    team=normalize_team_abbr(
                        (split.get("team") or {}).get("abbreviation")
                        or ODDS_API_TEAM_ABBR.get((split.get("team") or {}).get("name", ""), team_hint)
                        or team_hint
                    ),
                    opponent=normalize_team_abbr(
                        (split.get("opponent") or {}).get("abbreviation")
                        or ODDS_API_TEAM_ABBR.get((split.get("opponent") or {}).get("name", ""), "")
                    ),
                    bat_side=bat_side,
                    position=position,
                    batting_order=batting_order_hint,
                    at_bats=at_bats,
                    hits=safe_int(stat.get("hits")),
                    doubles=safe_int(stat.get("doubles")),
                    triples=safe_int(stat.get("triples")),
                    home_runs=safe_int(stat.get("homeRuns")),
                    walks=safe_int(stat.get("baseOnBalls")),
                    strikeouts=safe_int(stat.get("strikeOuts")),
                    plate_appearances=plate_appearances,
                    source="mlb_stats_api",
                )
            )

        return sorted(results, key=lambda item: item.game_date, reverse=True)

    def fetch_batter_pitcher_history(
        self,
        batter_id: int | None,
        pitcher_id: int | None,
    ) -> BatterPitcherHistory | None:
        if batter_id is None or pitcher_id is None:
            return None

        cache_key = f"mlb_bvp_{batter_id}_{pitcher_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return BatterPitcherHistory(**cached) if cached else None

        url = f"{self.BASE_URL}/people/{batter_id}/stats?stats=vsPlayer&group=hitting&opposingPlayerId={pitcher_id}"
        try:
            data = self._get_json(cache_key, url)
        except RuntimeError:
            self.cache.set(cache_key, {})
            return None

        splits = []
        for stat_block in data.get("stats", []):
            splits.extend(stat_block.get("splits", []))
        if not splits:
            self.cache.set(cache_key, {})
            return None
        stat = splits[0].get("stat", {})
        history = BatterPitcherHistory(
            batter_id=batter_id,
            pitcher_id=pitcher_id,
            at_bats=safe_int(stat.get("atBats")),
            hits=safe_int(stat.get("hits")),
            extra_base_hits=safe_int(stat.get("doubles")) + safe_int(stat.get("triples")) + safe_int(stat.get("homeRuns")),
            walks=safe_int(stat.get("baseOnBalls")),
            strikeouts=safe_int(stat.get("strikeOuts")),
            source="mlb_stats_api_vs_player",
        )
        self.cache.set(cache_key, history.__dict__)
        return history

    def _fetch_recent_lineup_batters(
        self,
        team: str,
        opponent: str,
        as_of_date: date,
    ) -> list[ProjectedBatter]:
        team_id = TEAM_ABBR_TO_MLB_ID.get(team)
        if team_id is None:
            return []

        cache_key = f"mlb_hot_hits_projected_lineup_{team}_{as_of_date.isoformat()}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return [ProjectedBatter(**{**item, "opponent": opponent}) for item in cached]

        game_ids = self._fetch_recent_game_ids(team_id=team_id, as_of_date=as_of_date)
        weighted: dict[int, tuple[float, ProjectedBatter]] = {}
        for game_index, game_id in enumerate(game_ids[: self.RECENT_LINEUP_GAMES]):
            for slot, hitter in enumerate(self._fetch_game_lineup(team_id=team_id, team=team, opponent=opponent, game_id=game_id), start=1):
                weight = max(0.5, 1.0 - (game_index * 0.12)) * max(0.72, 1.04 - (slot * 0.025))
                existing = weighted.get(hitter.player_id)
                if existing is None:
                    weighted[hitter.player_id] = (weight, hitter)
                else:
                    weighted[hitter.player_id] = (existing[0] + weight, existing[1])

        hitters = [item[1] for item in sorted(weighted.values(), key=lambda pair: pair[0], reverse=True)]
        if hitters:
            self.cache.set(cache_key, [{**hitter.__dict__, "opponent": ""} for hitter in hitters])
        return hitters

    def _fetch_recent_game_ids(self, team_id: int, as_of_date: date) -> list[int]:
        start_date = as_of_date - timedelta(days=self.RECENT_LINEUP_DAYS)
        cache_key = f"mlb_hot_hits_recent_games_{team_id}_{as_of_date.isoformat()}"
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
        game_ids = list(reversed(game_ids))[: self.RECENT_LINEUP_GAMES]
        self.cache.set(cache_key, game_ids)
        return game_ids

    def _fetch_game_lineup(
        self,
        team_id: int,
        team: str,
        opponent: str,
        game_id: int,
    ) -> list[ProjectedBatter]:
        cache_key = f"mlb_hot_hits_lineup_{game_id}_{team_id}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return [ProjectedBatter(**{**item, "opponent": opponent}) for item in cached]

        url = f"https://statsapi.mlb.com/api/v1.1/game/{game_id}/feed/live"
        data = self._get_json(cache_key, url)
        teams = data.get("liveData", {}).get("boxscore", {}).get("teams", {})
        team_box = None
        for side in ("home", "away"):
            side_box = teams.get(side) or {}
            if safe_int(side_box.get("team", {}).get("id")) == team_id:
                team_box = side_box
                break
        if team_box is None:
            return []

        players = team_box.get("players", {}) or {}
        lineup: list[ProjectedBatter] = []
        for slot, player_id in enumerate((team_box.get("battingOrder") or [])[:9], start=1):
            player = players.get(f"ID{player_id}") or {}
            person = player.get("person") or {}
            position = (player.get("position") or {}).get("abbreviation", "")
            if not person:
                continue
            lineup.append(
                ProjectedBatter(
                    player_id=safe_int(person.get("id")),
                    name=person.get("fullName", ""),
                    team=team,
                    opponent=opponent,
                    position=position,
                    batting_order=slot,
                )
            )
        if lineup:
            self.cache.set(cache_key, [{**hitter.__dict__, "opponent": ""} for hitter in lineup])
        return lineup

    def _fetch_active_roster_batters(self, team: str, opponent: str) -> list[ProjectedBatter]:
        team_id = TEAM_ABBR_TO_MLB_ID.get(team)
        if team_id is None:
            return []

        cache_key = f"mlb_hot_hits_active_roster_{team}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return [ProjectedBatter(**{**item, "opponent": opponent}) for item in cached]

        url = f"{self.BASE_URL}/teams/{team_id}/roster?rosterType=active"
        data = self._get_json(cache_key, url)
        hitters: list[ProjectedBatter] = []
        for entry in data.get("roster", []):
            person = entry.get("person") or {}
            position = entry.get("position") or {}
            if position.get("type") == "Pitcher":
                continue
            hitters.append(
                ProjectedBatter(
                    player_id=safe_int(person.get("id")),
                    name=person.get("fullName", ""),
                    team=team,
                    opponent=opponent,
                    position=position.get("abbreviation", ""),
                    batting_order=None,
                )
            )
        self.cache.set(cache_key, [{**hitter.__dict__, "opponent": ""} for hitter in hitters])
        return hitters

def build_probable_pitcher_index(games: list[Game]) -> dict[str, dict]:
    probable_pitchers: dict[str, dict] = {}
    for game in games:
        for name, pitcher_id, team, opponent in (
            (game.probable_home_pitcher, game.probable_home_pitcher_id, game.home_team, game.away_team),
            (game.probable_away_pitcher, game.probable_away_pitcher_id, game.away_team, game.home_team),
        ):
            if not name:
                continue
            probable_pitchers[normalize_name(name)] = {
                "subject_id": pitcher_id,
                "team": team,
                "opponent": opponent,
            }
    return probable_pitchers
