from __future__ import annotations

from datetime import date, datetime, time, timezone

from ..cache import JsonCache
from ..models import Game, MatchupContext, PitcherGameLog, PropLine
from ..utils import normalize_name


SAMPLE_GAMES = [
    {
        "game_id": "nyy-bos",
        "home_team": "BOS",
        "away_team": "NYY",
        "probable_home_pitcher": "Tanner Houck",
        "probable_away_pitcher": "Gerrit Cole",
    },
    {
        "game_id": "sea-oak",
        "home_team": "OAK",
        "away_team": "SEA",
        "probable_home_pitcher": "JP Sears",
        "probable_away_pitcher": "Luis Castillo",
    },
    {
        "game_id": "lad-sdp",
        "home_team": "SDP",
        "away_team": "LAD",
        "probable_home_pitcher": "Joe Musgrove",
        "probable_away_pitcher": "Tyler Glasnow",
    },
]

SAMPLE_LINES = [
    {"pitcher": "Gerrit Cole", "team": "NYY", "opponent": "BOS", "hand": "R", "prop_type": "PITCHER_STRIKEOUTS", "line": 6.5, "bookmaker": "samplebook"},
    {"pitcher": "Gerrit Cole", "team": "NYY", "opponent": "BOS", "hand": "R", "prop_type": "PITCHER_OUTS_RECORDED", "line": 17.5, "bookmaker": "samplebook"},
    {"pitcher": "Tanner Houck", "team": "BOS", "opponent": "NYY", "hand": "R", "prop_type": "PITCHER_STRIKEOUTS", "line": 5.5, "bookmaker": "samplebook"},
    {"pitcher": "Tanner Houck", "team": "BOS", "opponent": "NYY", "hand": "R", "prop_type": "PITCHER_OUTS_RECORDED", "line": 17.5, "bookmaker": "samplebook"},
    {"pitcher": "Luis Castillo", "team": "SEA", "opponent": "OAK", "hand": "R", "prop_type": "PITCHER_STRIKEOUTS", "line": 5.5, "bookmaker": "samplebook"},
    {"pitcher": "Luis Castillo", "team": "SEA", "opponent": "OAK", "hand": "R", "prop_type": "PITCHER_OUTS_RECORDED", "line": 18.5, "bookmaker": "samplebook"},
    {"pitcher": "JP Sears", "team": "OAK", "opponent": "SEA", "hand": "L", "prop_type": "PITCHER_STRIKEOUTS", "line": 4.5, "bookmaker": "samplebook"},
    {"pitcher": "JP Sears", "team": "OAK", "opponent": "SEA", "hand": "L", "prop_type": "PITCHER_OUTS_RECORDED", "line": 15.5, "bookmaker": "samplebook"},
    {"pitcher": "Tyler Glasnow", "team": "LAD", "opponent": "SDP", "hand": "R", "prop_type": "PITCHER_STRIKEOUTS", "line": 7.5, "bookmaker": "samplebook"},
    {"pitcher": "Tyler Glasnow", "team": "LAD", "opponent": "SDP", "hand": "R", "prop_type": "PITCHER_OUTS_RECORDED", "line": 17.5, "bookmaker": "samplebook"},
    {"pitcher": "Joe Musgrove", "team": "SDP", "opponent": "LAD", "hand": "R", "prop_type": "PITCHER_STRIKEOUTS", "line": 5.5, "bookmaker": "samplebook"},
    {"pitcher": "Joe Musgrove", "team": "SDP", "opponent": "LAD", "hand": "R", "prop_type": "PITCHER_OUTS_RECORDED", "line": 16.5, "bookmaker": "samplebook"},
]

SAMPLE_MATCHUPS = [
    {"team": "NYY", "opponent": "BOS", "opponent_k_rate_vs_hand": 0.232, "opponent_walk_rate_vs_hand": 0.082, "opponent_woba_vs_hand": 0.321, "opponent_outs_factor": 1.02, "park_run_factor": 1.01, "moneyline": -125},
    {"team": "BOS", "opponent": "NYY", "opponent_k_rate_vs_hand": 0.215, "opponent_walk_rate_vs_hand": 0.087, "opponent_woba_vs_hand": 0.335, "opponent_outs_factor": 0.98, "park_run_factor": 1.01, "moneyline": 110},
    {"team": "SEA", "opponent": "OAK", "opponent_k_rate_vs_hand": 0.248, "opponent_walk_rate_vs_hand": 0.073, "opponent_woba_vs_hand": 0.298, "opponent_outs_factor": 1.08, "park_run_factor": 0.95, "moneyline": -155},
    {"team": "OAK", "opponent": "SEA", "opponent_k_rate_vs_hand": 0.224, "opponent_walk_rate_vs_hand": 0.079, "opponent_woba_vs_hand": 0.311, "opponent_outs_factor": 0.97, "park_run_factor": 0.95, "moneyline": 138},
    {"team": "LAD", "opponent": "SDP", "opponent_k_rate_vs_hand": 0.218, "opponent_walk_rate_vs_hand": 0.080, "opponent_woba_vs_hand": 0.314, "opponent_outs_factor": 1.01, "park_run_factor": 0.99, "moneyline": -118},
    {"team": "SDP", "opponent": "LAD", "opponent_k_rate_vs_hand": 0.201, "opponent_walk_rate_vs_hand": 0.091, "opponent_woba_vs_hand": 0.346, "opponent_outs_factor": 0.94, "park_run_factor": 0.99, "moneyline": 105},
]

SAMPLE_PITCHER_LOGS = {
    "Gerrit Cole": [
        {"team": "NYY", "opponent": "TOR", "hand": "R", "outs_recorded": 18, "strikeouts": 8, "pitches_thrown": 99, "batters_faced": 25, "walks": 1, "hits_allowed": 4, "earned_runs": 2},
        {"team": "NYY", "opponent": "BAL", "hand": "R", "outs_recorded": 18, "strikeouts": 7, "pitches_thrown": 101, "batters_faced": 24, "walks": 2, "hits_allowed": 3, "earned_runs": 1},
        {"team": "NYY", "opponent": "TB", "hand": "R", "outs_recorded": 21, "strikeouts": 9, "pitches_thrown": 105, "batters_faced": 27, "walks": 1, "hits_allowed": 2, "earned_runs": 0},
        {"team": "NYY", "opponent": "DET", "hand": "R", "outs_recorded": 17, "strikeouts": 6, "pitches_thrown": 94, "batters_faced": 25, "walks": 2, "hits_allowed": 5, "earned_runs": 3},
        {"team": "NYY", "opponent": "MIN", "hand": "R", "outs_recorded": 18, "strikeouts": 8, "pitches_thrown": 97, "batters_faced": 26, "walks": 1, "hits_allowed": 4, "earned_runs": 2},
        {"team": "NYY", "opponent": "HOU", "hand": "R", "outs_recorded": 18, "strikeouts": 7, "pitches_thrown": 100, "batters_faced": 25, "walks": 2, "hits_allowed": 4, "earned_runs": 2},
        {"team": "NYY", "opponent": "BOS", "hand": "R", "outs_recorded": 19, "strikeouts": 9, "pitches_thrown": 104, "batters_faced": 27, "walks": 1, "hits_allowed": 5, "earned_runs": 2},
        {"team": "NYY", "opponent": "CLE", "hand": "R", "outs_recorded": 18, "strikeouts": 6, "pitches_thrown": 96, "batters_faced": 26, "walks": 2, "hits_allowed": 4, "earned_runs": 2},
    ],
    "Tanner Houck": [
        {"team": "BOS", "opponent": "SEA", "hand": "R", "outs_recorded": 18, "strikeouts": 7, "pitches_thrown": 101, "batters_faced": 25, "walks": 1, "hits_allowed": 3, "earned_runs": 1},
        {"team": "BOS", "opponent": "LAA", "hand": "R", "outs_recorded": 15, "strikeouts": 4, "pitches_thrown": 86, "batters_faced": 23, "walks": 3, "hits_allowed": 6, "earned_runs": 3},
        {"team": "BOS", "opponent": "OAK", "hand": "R", "outs_recorded": 18, "strikeouts": 6, "pitches_thrown": 95, "batters_faced": 24, "walks": 2, "hits_allowed": 4, "earned_runs": 1},
        {"team": "BOS", "opponent": "TEX", "hand": "R", "outs_recorded": 17, "strikeouts": 5, "pitches_thrown": 91, "batters_faced": 25, "walks": 2, "hits_allowed": 5, "earned_runs": 2},
        {"team": "BOS", "opponent": "KCR", "hand": "R", "outs_recorded": 18, "strikeouts": 5, "pitches_thrown": 98, "batters_faced": 24, "walks": 1, "hits_allowed": 3, "earned_runs": 1},
        {"team": "BOS", "opponent": "CHW", "hand": "R", "outs_recorded": 18, "strikeouts": 6, "pitches_thrown": 96, "batters_faced": 24, "walks": 2, "hits_allowed": 3, "earned_runs": 2},
        {"team": "BOS", "opponent": "TOR", "hand": "R", "outs_recorded": 16, "strikeouts": 4, "pitches_thrown": 89, "batters_faced": 25, "walks": 2, "hits_allowed": 5, "earned_runs": 3},
        {"team": "BOS", "opponent": "NYY", "hand": "R", "outs_recorded": 18, "strikeouts": 7, "pitches_thrown": 102, "batters_faced": 26, "walks": 1, "hits_allowed": 3, "earned_runs": 1},
    ],
    "Luis Castillo": [
        {"team": "SEA", "opponent": "LAA", "hand": "R", "outs_recorded": 18, "strikeouts": 7, "pitches_thrown": 98, "batters_faced": 24, "walks": 1, "hits_allowed": 3, "earned_runs": 1},
        {"team": "SEA", "opponent": "TEX", "hand": "R", "outs_recorded": 18, "strikeouts": 6, "pitches_thrown": 93, "batters_faced": 25, "walks": 2, "hits_allowed": 4, "earned_runs": 2},
        {"team": "SEA", "opponent": "OAK", "hand": "R", "outs_recorded": 20, "strikeouts": 8, "pitches_thrown": 101, "batters_faced": 27, "walks": 1, "hits_allowed": 4, "earned_runs": 1},
        {"team": "SEA", "opponent": "MIL", "hand": "R", "outs_recorded": 18, "strikeouts": 6, "pitches_thrown": 96, "batters_faced": 24, "walks": 2, "hits_allowed": 3, "earned_runs": 1},
        {"team": "SEA", "opponent": "CIN", "hand": "R", "outs_recorded": 19, "strikeouts": 7, "pitches_thrown": 99, "batters_faced": 25, "walks": 1, "hits_allowed": 3, "earned_runs": 1},
        {"team": "SEA", "opponent": "HOU", "hand": "R", "outs_recorded": 18, "strikeouts": 5, "pitches_thrown": 91, "batters_faced": 25, "walks": 3, "hits_allowed": 5, "earned_runs": 3},
        {"team": "SEA", "opponent": "OAK", "hand": "R", "outs_recorded": 18, "strikeouts": 7, "pitches_thrown": 97, "batters_faced": 24, "walks": 1, "hits_allowed": 2, "earned_runs": 0},
        {"team": "SEA", "opponent": "BOS", "hand": "R", "outs_recorded": 17, "strikeouts": 6, "pitches_thrown": 92, "batters_faced": 26, "walks": 2, "hits_allowed": 4, "earned_runs": 2},
    ],
    "JP Sears": [
        {"team": "OAK", "opponent": "SEA", "hand": "L", "outs_recorded": 15, "strikeouts": 4, "pitches_thrown": 85, "batters_faced": 23, "walks": 2, "hits_allowed": 5, "earned_runs": 3},
        {"team": "OAK", "opponent": "CLE", "hand": "L", "outs_recorded": 16, "strikeouts": 5, "pitches_thrown": 91, "batters_faced": 24, "walks": 1, "hits_allowed": 4, "earned_runs": 2},
        {"team": "OAK", "opponent": "DET", "hand": "L", "outs_recorded": 14, "strikeouts": 3, "pitches_thrown": 82, "batters_faced": 22, "walks": 3, "hits_allowed": 5, "earned_runs": 4},
        {"team": "OAK", "opponent": "SEA", "hand": "L", "outs_recorded": 16, "strikeouts": 4, "pitches_thrown": 89, "batters_faced": 24, "walks": 2, "hits_allowed": 4, "earned_runs": 2},
        {"team": "OAK", "opponent": "TBR", "hand": "L", "outs_recorded": 17, "strikeouts": 5, "pitches_thrown": 94, "batters_faced": 25, "walks": 2, "hits_allowed": 4, "earned_runs": 2},
        {"team": "OAK", "opponent": "LAA", "hand": "L", "outs_recorded": 15, "strikeouts": 3, "pitches_thrown": 84, "batters_faced": 23, "walks": 2, "hits_allowed": 5, "earned_runs": 3},
        {"team": "OAK", "opponent": "SEA", "hand": "L", "outs_recorded": 16, "strikeouts": 4, "pitches_thrown": 88, "batters_faced": 24, "walks": 1, "hits_allowed": 4, "earned_runs": 2},
        {"team": "OAK", "opponent": "HOU", "hand": "L", "outs_recorded": 15, "strikeouts": 4, "pitches_thrown": 86, "batters_faced": 24, "walks": 2, "hits_allowed": 6, "earned_runs": 3},
    ],
    "Tyler Glasnow": [
        {"team": "LAD", "opponent": "ARI", "hand": "R", "outs_recorded": 18, "strikeouts": 9, "pitches_thrown": 101, "batters_faced": 24, "walks": 1, "hits_allowed": 2, "earned_runs": 1},
        {"team": "LAD", "opponent": "SFG", "hand": "R", "outs_recorded": 17, "strikeouts": 8, "pitches_thrown": 97, "batters_faced": 24, "walks": 2, "hits_allowed": 3, "earned_runs": 1},
        {"team": "LAD", "opponent": "SDP", "hand": "R", "outs_recorded": 18, "strikeouts": 10, "pitches_thrown": 104, "batters_faced": 25, "walks": 1, "hits_allowed": 3, "earned_runs": 1},
        {"team": "LAD", "opponent": "COL", "hand": "R", "outs_recorded": 16, "strikeouts": 7, "pitches_thrown": 90, "batters_faced": 24, "walks": 3, "hits_allowed": 4, "earned_runs": 3},
        {"team": "LAD", "opponent": "STL", "hand": "R", "outs_recorded": 18, "strikeouts": 9, "pitches_thrown": 99, "batters_faced": 24, "walks": 1, "hits_allowed": 2, "earned_runs": 0},
        {"team": "LAD", "opponent": "CIN", "hand": "R", "outs_recorded": 15, "strikeouts": 6, "pitches_thrown": 87, "batters_faced": 22, "walks": 2, "hits_allowed": 5, "earned_runs": 2},
        {"team": "LAD", "opponent": "SDP", "hand": "R", "outs_recorded": 18, "strikeouts": 8, "pitches_thrown": 98, "batters_faced": 24, "walks": 1, "hits_allowed": 3, "earned_runs": 1},
        {"team": "LAD", "opponent": "CHC", "hand": "R", "outs_recorded": 17, "strikeouts": 8, "pitches_thrown": 95, "batters_faced": 25, "walks": 2, "hits_allowed": 4, "earned_runs": 2},
    ],
    "Joe Musgrove": [
        {"team": "SDP", "opponent": "LAD", "hand": "R", "outs_recorded": 16, "strikeouts": 4, "pitches_thrown": 88, "batters_faced": 24, "walks": 2, "hits_allowed": 5, "earned_runs": 3},
        {"team": "SDP", "opponent": "ATL", "hand": "R", "outs_recorded": 18, "strikeouts": 6, "pitches_thrown": 96, "batters_faced": 25, "walks": 1, "hits_allowed": 4, "earned_runs": 2},
        {"team": "SDP", "opponent": "SFG", "hand": "R", "outs_recorded": 17, "strikeouts": 5, "pitches_thrown": 93, "batters_faced": 24, "walks": 2, "hits_allowed": 4, "earned_runs": 2},
        {"team": "SDP", "opponent": "ARI", "hand": "R", "outs_recorded": 15, "strikeouts": 4, "pitches_thrown": 84, "batters_faced": 23, "walks": 2, "hits_allowed": 6, "earned_runs": 4},
        {"team": "SDP", "opponent": "COL", "hand": "R", "outs_recorded": 18, "strikeouts": 7, "pitches_thrown": 101, "batters_faced": 25, "walks": 1, "hits_allowed": 3, "earned_runs": 1},
        {"team": "SDP", "opponent": "MIL", "hand": "R", "outs_recorded": 18, "strikeouts": 5, "pitches_thrown": 94, "batters_faced": 25, "walks": 2, "hits_allowed": 4, "earned_runs": 2},
        {"team": "SDP", "opponent": "LAD", "hand": "R", "outs_recorded": 16, "strikeouts": 4, "pitches_thrown": 87, "batters_faced": 24, "walks": 2, "hits_allowed": 5, "earned_runs": 3},
        {"team": "SDP", "opponent": "STL", "hand": "R", "outs_recorded": 17, "strikeouts": 6, "pitches_thrown": 95, "batters_faced": 24, "walks": 1, "hits_allowed": 4, "earned_runs": 2},
    ],
}


class SampleSlateSource:
    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache

    def fetch_games(self, screen_date) -> list[Game]:
        cache_key = f"sample_slate_{screen_date.isoformat()}"
        cached = self.cache.get(cache_key)
        if cached:
            return [_deserialize_game(item) for item in cached]

        games = [
            Game(
                game_id=item["game_id"],
                game_date=screen_date,
                game_time=datetime.combine(screen_date, time(hour=18, minute=5), tzinfo=timezone.utc),
                home_team=item["home_team"],
                away_team=item["away_team"],
                probable_home_pitcher=item["probable_home_pitcher"],
                probable_away_pitcher=item["probable_away_pitcher"],
                source="sample",
            )
            for item in SAMPLE_GAMES
        ]
        self.cache.set(cache_key, [_serialize_game(game) for game in games])
        return games


class SamplePitcherPropsSource:
    def __init__(self, shared_cache: JsonCache, lines_cache: JsonCache) -> None:
        self.shared_cache = shared_cache
        self.lines_cache = lines_cache

    def fetch_prop_lines(self, games: list[Game], supported_prop_types: list[str]) -> list[PropLine]:
        event_ids = {game.game_id for game in games}
        cache_key = f"sample_lines_{'_'.join(sorted(event_ids))}"
        cached = self.lines_cache.get(cache_key)
        if cached:
            return [_deserialize_prop_line(item) for item in cached if item["prop_type"] in supported_prop_types]

        now = datetime.now(timezone.utc)
        game_dates = {
            team: game.game_date
            for game in games
            for team in (game.home_team, game.away_team)
        }
        lines = [
            PropLine(
                event_id=f"{item['team']}-{item['opponent']}",
                game_date=game_dates[item["team"]],
                subject_name_raw=item["pitcher"],
                subject_name_norm=normalize_name(item["pitcher"]),
                subject_id=None,
                subject_role="pitcher",
                team=item["team"],
                opponent=item["opponent"],
                hand=item["hand"],
                prop_type=item["prop_type"],
                line=item["line"],
                bookmaker=item["bookmaker"],
                source="sample",
                collected_at=now,
            )
            for item in SAMPLE_LINES
            if item["prop_type"] in supported_prop_types
        ]
        self.lines_cache.set(cache_key, [_serialize_prop_line(line) for line in lines])
        return lines

    def fetch_matchup_contexts(self, screen_date) -> dict[tuple[str, str], MatchupContext]:
        cache_key = f"sample_matchups_v2_{screen_date.isoformat()}"
        cached = self.shared_cache.get(cache_key)
        if cached:
            items = [MatchupContext(**item) for item in cached]
        else:
            items = [MatchupContext(source="sample", **item) for item in SAMPLE_MATCHUPS]
            self.shared_cache.set(cache_key, [_serialize_matchup(item) for item in items])
        return {(item.team, item.opponent): item for item in items}


class SamplePitcherLogsSource:
    def __init__(self, cache: JsonCache) -> None:
        self.cache = cache

    def fetch_logs_for_lines(self, prop_lines: list[PropLine]) -> dict[str, list[PitcherGameLog]]:
        result: dict[str, list[PitcherGameLog]] = {}
        for line in prop_lines:
            key = line.subject_name_norm
            if key in result:
                continue
            result[key] = self.fetch_logs(line.subject_name_raw)
        return result

    def fetch_logs(self, pitcher_name: str) -> list[PitcherGameLog]:
        cache_key = f"sample_logs_{normalize_name(pitcher_name)}"
        cached = self.cache.get(cache_key)
        if cached:
            return [_deserialize_pitcher_log(item) for item in cached]

        raw_logs = SAMPLE_PITCHER_LOGS[pitcher_name]
        logs = []
        for index, item in enumerate(raw_logs):
            logs.append(
                PitcherGameLog(
                    pitcher_name_raw=pitcher_name,
                    pitcher_name_norm=normalize_name(pitcher_name),
                    pitcher_id=None,
                    game_date=datetime(2026, 4, 10 - index, tzinfo=timezone.utc).date(),
                    team=item["team"],
                    opponent=item["opponent"],
                    hand=item["hand"],
                    outs_recorded=item["outs_recorded"],
                    strikeouts=item["strikeouts"],
                    pitches_thrown=item["pitches_thrown"],
                    batters_faced=item["batters_faced"],
                    walks=item["walks"],
                    hits_allowed=item["hits_allowed"],
                    earned_runs=item["earned_runs"],
                    did_start=True,
                    source="sample",
                )
            )
        self.cache.set(cache_key, [_serialize_pitcher_log(log) for log in logs])
        return logs

    def fetch_logs_for_games(self, games: list[Game]) -> dict[str, list[PitcherGameLog]]:
        result: dict[str, list[PitcherGameLog]] = {}
        for game in games:
            for pitcher_name in (game.probable_home_pitcher, game.probable_away_pitcher):
                if not pitcher_name:
                    continue
                key = normalize_name(pitcher_name)
                if key in result:
                    continue
                result[key] = self.fetch_logs(pitcher_name)
        return result


def _serialize_game(game: Game) -> dict:
    return {
        **game.__dict__,
        "game_date": game.game_date.isoformat(),
        "game_time": game.game_time.isoformat(),
    }


def _serialize_prop_line(line: PropLine) -> dict:
    return {
        **line.__dict__,
        "game_date": line.game_date.isoformat(),
        "collected_at": line.collected_at.isoformat(),
    }


def _serialize_matchup(matchup: MatchupContext) -> dict:
    return matchup.__dict__.copy()


def _serialize_pitcher_log(log: PitcherGameLog) -> dict:
    return {
        **log.__dict__,
        "game_date": log.game_date.isoformat(),
    }


def _deserialize_game(item: dict) -> Game:
    return Game(
        **{
            **item,
            "game_date": date.fromisoformat(item["game_date"]),
            "game_time": datetime.fromisoformat(item["game_time"]),
        }
    )


def _deserialize_prop_line(item: dict) -> PropLine:
    return PropLine(
        **{
            **item,
            "subject_id": item.get("subject_id"),
            "game_date": date.fromisoformat(item["game_date"]),
            "collected_at": datetime.fromisoformat(item["collected_at"]),
        }
    )


def _deserialize_pitcher_log(item: dict) -> PitcherGameLog:
    return PitcherGameLog(
        **{
            **item,
            "pitcher_id": item.get("pitcher_id"),
            "game_date": date.fromisoformat(item["game_date"]),
        }
    )
