"""Shared game-data access for the game-run engine.

MLB Stats API schedule (hydrate probablePitcher+linescore), cached
locally. `build_features_pregame` works for upcoming games (no scores);
`build_row` adds the outcome for graded finals. All rolling team/starter
state uses games strictly BEFORE the screen date.
"""
from __future__ import annotations

import json
import time
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .game_runs import GameFeatures, TeamGameFeatures

UA = "mlb-props-game-gradepass/1"
GAME_CACHE_DIRNAME = ".cache/games"
STARTER_CACHE_SECONDS = 6 * 60 * 60
SEASON_START = date(2026, 4, 1)

PARK_RUN_FACTOR = {
    "AZ": 1.01, "ATL": 1.03, "BAL": 0.97, "BOS": 1.02, "CHC": 1.01, "CWS": 1.03,
    "CIN": 1.09, "CLE": 0.98, "COL": 1.12, "DET": 0.97, "HOU": 0.99, "KC": 1.02,
    "LAA": 1.00, "LAD": 0.98, "MIA": 0.95, "MIL": 1.02, "MIN": 1.01, "NYM": 0.98,
    "NYY": 1.03, "ATH": 0.95, "PHI": 1.03, "PIT": 0.98, "SD": 0.96, "SF": 0.94,
    "SEA": 0.95, "STL": 0.99, "TB": 0.97, "TEX": 1.05, "TOR": 1.02, "WSH": 1.01,
}

_repo_root: Path | None = None


def _root() -> Path:
    global _repo_root
    if _repo_root is None:
        _repo_root = Path(__file__).resolve().parent.parent.parent
    return _repo_root


def cache_dir() -> Path:
    path = _root() / GAME_CACHE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def cache_schedule_chunks(start: date | None = None, end: date | None = None) -> list[dict]:
    start = start or SEASON_START
    end = end or date.today()
    all_games: list[dict] = []
    chunk_start = start
    while chunk_start <= end:
        nominal_end = chunk_start + timedelta(days=13)
        chunk_end = min(nominal_end, end)
        closed_chunk = nominal_end <= end and nominal_end < date.today()
        cache_suffix = nominal_end.isoformat() if closed_chunk else "open"
        cache_path = cache_dir() / f"games_{chunk_start.isoformat()}_{cache_suffix}.json"
        if cache_path.exists() and closed_chunk:
            payload = json.loads(cache_path.read_text())
        else:
            url = (
                "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
                f"&startDate={chunk_start.isoformat()}&endDate={chunk_end.isoformat()}"
                "&hydrate=probablePitcher,linescore"
            )
            payload = fetch_json(url)
            cache_path.write_text(json.dumps(payload))
        for day in payload.get("dates", []):
            for game in day.get("games", []):
                all_games.append(game)
        chunk_start += timedelta(days=14)
    return all_games


def fetch_slate(screen: str) -> list[dict]:
    """Schedule (with probable pitchers) for one screen date."""
    cache_path = cache_dir() / f"slate_{screen}.json"
    # Today's probable pitchers and game statuses can change throughout the day.
    if cache_path.exists() and date.fromisoformat(screen) < date.today():
        payload = json.loads(cache_path.read_text())
    else:
        url = (
            "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
            f"&startDate={screen}&endDate={screen}"
            "&hydrate=probablePitcher,linescore"
        )
        payload = fetch_json(url)
        cache_path.write_text(json.dumps(payload))
    games: list[dict] = []
    for day in payload.get("dates", []):
        games.extend(day.get("games", []))
    return games


def team_history_map(games: list[dict]) -> dict[int, list[dict]]:
    """team_id -> chronological list of final games with runs for/against."""
    history: dict[int, list[dict]] = defaultdict(list)
    for game in games:
        home = game["teams"]["home"]
        away = game["teams"]["away"]
        if home.get("score") is None or away.get("score") is None:
            continue
        d = game["gameDate"][:10]
        home_id = home["team"]["id"]
        away_id = away["team"]["id"]
        history[home_id].append({
            "date": d, "gamePk": game["gamePk"],
            "runs_for": home["score"], "runs_against": away["score"],
        })
        history[away_id].append({
            "date": d, "gamePk": game["gamePk"],
            "runs_for": away["score"], "runs_against": home["score"],
        })
    for rows in history.values():
        rows.sort(key=lambda r: r["date"])
    return history


def team_feature_pair(rows: list[dict], screen: str) -> tuple[tuple[float, float], tuple[float, float]]:
    prior = [row for row in rows if row["date"] < screen]
    if not prior:
        return (4.5, 4.5), (4.5, 4.5)
    last7 = prior[-7:]
    runs_l7 = sum(row["runs_for"] for row in last7) / len(last7)
    runs_season = sum(row["runs_for"] for row in prior) / len(prior)
    allowed_l7 = sum(row["runs_against"] for row in last7) / len(last7)
    allowed_season = sum(row["runs_against"] for row in prior) / len(prior)
    return (runs_l7, runs_season), (allowed_l7, allowed_season)


def starter_features(pitcher_id: int | None, screen: str) -> dict | None:
    if not pitcher_id:
        return None
    cache_path = cache_dir() / f"pitcher_{pitcher_id}_2026.json"
    cache_fresh = cache_path.exists() and time.time() - cache_path.stat().st_mtime < STARTER_CACHE_SECONDS
    if cache_fresh:
        splits = json.loads(cache_path.read_text())
    else:
        try:
            url = (
                f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats"
                "?stats=gameLog&season=2026&group=pitching"
            )
            full = fetch_json(url)
            splits = []
            for stat_block in full.get("stats", []):
                splits.extend(stat_block.get("splits", []))
            cache_path.write_text(json.dumps(splits))
        except Exception:
            return None
    starts = []
    for row in splits:
        stat = row.get("stat", {})
        if not stat.get("gamesStarted"):
            continue
        game_date = (row.get("game", {}) or {}).get("gameDate", "")[:10]
        if game_date >= screen:
            continue  # strict PIT
        outs = int(stat.get("outs") or 0)
        runs = int(stat.get("earnedRuns") or 0) + int(stat.get("unearnedRuns") or 0)
        starts.append({
            "game_date": game_date,
            "runs": runs,
            "hits": int(stat.get("hits") or 0),
            "walks": int(stat.get("baseOnBalls") or 0),
            "k": int(stat.get("strikeOuts") or 0),
            "deep": 1 if outs >= 18 else 0,
        })
    if not starts:
        return None
    last5 = sorted(starts, key=lambda s: s["game_date"])[-5:]
    n = len(last5)
    return {
        "n": n,
        "runs": sum(row["runs"] for row in last5) / n,
        "hits": sum(row["hits"] for row in last5) / n,
        "walks": sum(row["walks"] for row in last5) / n,
        "k": sum(row["k"] for row in last5) / n,
        "deep": sum(row["deep"] for row in last5) / n,
    }


def build_features_pregame(
    game: dict,
    day_map: dict[str, list[dict]],
    history: dict[int, list[dict]],
) -> GameFeatures | None:
    """Features for an upcoming game (works pre-game; no score needed)."""
    screen = game["gameDate"][:10]
    home = game["teams"]["home"]
    away = game["teams"]["away"]
    home_id = home["team"]["id"]
    away_id = away["team"]["id"]

    offense_h, allowed_h = team_feature_pair(history.get(home_id, []), screen)
    offense_a, allowed_a = team_feature_pair(history.get(away_id, []), screen)

    doubleheader = 0
    for other in day_map.get(screen, []):
        if other["gamePk"] == game["gamePk"]:
            continue
        ids = (other["teams"]["home"]["team"]["id"], other["teams"]["away"]["team"]["id"])
        if home_id in ids or away_id in ids:
            doubleheader = 1
            break

    try:
        game_hour = int(game["gameDate"][11:13])
    except (KeyError, ValueError, IndexError):
        game_hour = 19
    night = 1 if (game_hour >= 17 or game_hour < 5) else 0

    probable_home = home.get("probablePitcher") or {}
    probable_away = away.get("probablePitcher") or {}
    sp_away = starter_features(probable_away.get("id"), screen)
    sp_home = starter_features(probable_home.get("id"), screen)
    abbr = home["team"].get("abbreviation", "")
    park = PARK_RUN_FACTOR.get(abbr, 1.0)

    def side(
        offense: tuple[float, float],
        allowed: tuple[float, float],
        opp_sp: dict | None,
        home_flag: int,
    ) -> TeamGameFeatures:
        st = opp_sp or {"runs": 4.0, "hits": 6.0, "walks": 2.2, "k": 5.5, "deep": 0.55}
        return TeamGameFeatures(
            runs_per_game_l7=offense[0],
            runs_per_game_season=offense[1],
            runs_allowed_per_game_l7=allowed[0],
            opp_starter_runs_allowed_per_start_l5=st["runs"],
            opp_starter_hits_allowed_per_start=st["hits"],
            opp_starter_walks_per_start=st["walks"],
            opp_starter_k_per_start=st["k"],
            opp_starter_deep_start_rate=st["deep"],
            opp_bullpen_pitches_last3=None,
            park_run_factor=park,
            home=home_flag,
            doubleheader=doubleheader,
            night_game=night,
        )

    return GameFeatures(
        game_pk=str(game["gamePk"]),
        screen_date=screen,
        home_side=side(offense_h, allowed_h, sp_away, 1),
        away_side=side(offense_a, allowed_a, sp_home, 0),
    )


def build_row(
    game: dict,
    day_map: dict[str, list[dict]],
    history: dict[int, list[dict]],
) -> tuple[GameFeatures | None, int | None, tuple[float, float] | None]:
    """Features + outcome for a final game (None-triple when unscored)."""
    home = game["teams"]["home"]
    away = game["teams"]["away"]
    if home.get("score") is None or away.get("score") is None:
        return None, None, None
    features = build_features_pregame(game, day_map, history)
    if features is None:
        return None, None, None
    home_runs = int(home["score"])
    away_runs = int(away["score"])
    return features, (1 if home_runs > away_runs else 0), (float(home_runs), float(away_runs))
