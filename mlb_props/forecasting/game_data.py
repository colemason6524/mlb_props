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

# Park run factors keyed by MLB Stats API team ID. The schedule cache does
# not carry team abbreviations, so the old abbreviation-keyed map never
# matched and the park feature was a constant 1.0.
PARK_FACTOR_BY_TEAM_ID = {
    109: 1.01, 144: 1.03, 110: 0.97, 111: 1.02, 112: 1.01, 145: 1.03,
    113: 1.09, 114: 0.98, 115: 1.12, 116: 0.97, 117: 0.99, 118: 1.02,
    108: 1.00, 119: 0.98, 146: 0.95, 158: 1.02, 142: 1.01, 121: 0.98,
    147: 1.03, 133: 0.95, 143: 1.03, 134: 0.98, 135: 0.96, 137: 0.94,
    136: 0.95, 138: 0.99, 139: 0.97, 140: 1.05, 141: 1.02, 120: 1.01,
}
LEAGUE_AVG_BULLPEN_RA9 = 4.30

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
            "park": home_id,
        })
        history[away_id].append({
            "date": d, "gamePk": game["gamePk"],
            "runs_for": away["score"], "runs_against": home["score"],
            "park": home_id,
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


_sp_gamelog_index = None


def sp_gamelog_index() -> dict:
    """pitcher_id -> {game_date: (outs, runs)} from cached gamelogs.

    Uses the split-level `date` field (game.gameDate is absent). Loaded
    once per process; the underlying cache files are managed by
    starter_features.
    """
    global _sp_gamelog_index
    if _sp_gamelog_index is not None:
        return _sp_gamelog_index
    idx: dict = {}
    for path in cache_dir().glob("pitcher_*_2026.json"):
        try:
            pid = int(path.stem.split("_")[1])
        except (IndexError, ValueError):
            continue
        try:
            splits = json.loads(path.read_text())
        except Exception:
            continue
        per_game: dict = {}
        for row in splits:
            stat = row.get("stat", {})
            if not stat.get("gamesStarted"):
                continue
            gd = (row.get("date") or "")[:10]
            if not gd:
                continue
            outs = int(stat.get("outs") or 0)
            runs = int(stat.get("earnedRuns") or 0) + int(stat.get("unearnedRuns") or 0)
            per_game[gd] = (outs, runs)
        idx[pid] = per_game
    _sp_gamelog_index = idx
    return idx


def bullpen_history_map(finals: list[dict]) -> dict[int, list[tuple]]:
    """team_id -> chronological [(date, bullpen_ra9)] for finished games.

    bullpen_ra9 = (team_runs_allowed - starter_runs) / bullpen_ip * 9,
    where the starter's line comes from his gamelog matched on date.
    Capped at 18.0 to contain blowups/opener chaos.
    """
    sp_idx = sp_gamelog_index()
    bp: dict = defaultdict(list)
    for game in finals:
        d = game["gameDate"][:10]
        n_innings = len((game.get("linescore") or {}).get("innings") or []) or 9
        for tkey in ("home", "away"):
            team = game["teams"][tkey]
            tid = team["team"]["id"]
            tra = team.get("score")
            if tra is None:
                continue
            sp_id = (team.get("probablePitcher") or {}).get("id")
            if not sp_id:
                continue
            glog = sp_idx.get(sp_id)
            if not glog or d not in glog:
                continue
            outs, sp_runs = glog[d]
            bp_runs = max(0, tra - sp_runs)
            bp_ip = max(n_innings - outs / 3.0, 0.5)
            bp_ra9 = min(bp_runs / bp_ip * 9.0, 12.0)
            bp[tid].append((d, bp_ra9))
    for rows in bp.values():
        rows.sort(key=lambda r: r[0])
    return bp


def bullpen_ra9_prior(bp_hist: dict, team_id: int, screen: str,
                      window_days: int = 14, min_games: int = 3) -> float:
    """Trailing-window mean bullpen RA/9 strictly before screen."""
    rows = [r for r in bp_hist.get(team_id, []) if r[0] < screen]
    if len(rows) < min_games:
        return LEAGUE_AVG_BULLPEN_RA9
    # last `window_days` of entries (approx: entries are daily-ish)
    window = rows[-window_days:]
    return sum(r[1] for r in window) / len(window)


def adj_defense_prior(history: dict, team_id: int, screen: str,
                      window_games: int = 30, min_games: int = 10) -> float:
    """Trailing park-adjusted runs ALLOWED/game strictly before screen."""
    rows = [r for r in history.get(team_id, []) if r["date"] < screen]
    window = rows[-window_games:]
    if len(window) < min_games:
        return 4.50
    adj = [r["runs_against"] / PARK_FACTOR_BY_TEAM_ID.get(r.get("park"), 1.0)
           for r in window]
    return sum(adj) / len(adj)


def adj_offense_prior(history: dict, team_id: int, screen: str,
                      window_games: int = 30, min_games: int = 10) -> float:
    """Trailing park-adjusted runs/game strictly before screen.

    Each game's runs are deflated by the park they were scored in, so
    Coors-inflated lines don't masquerade as offensive talent.
    """
    rows = [r for r in history.get(team_id, []) if r["date"] < screen]
    window = rows[-window_games:]
    if len(window) < min_games:
        return 4.50
    adj = [r["runs_for"] / PARK_FACTOR_BY_TEAM_ID.get(r.get("park"), 1.0)
           for r in window]
    return sum(adj) / len(adj)


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
        game_date = (row.get("date") or "")[:10]
        if not game_date or game_date >= screen:
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
    """Features for an upcoming game (works pre-game; no score needed).

    v2 feature set (game-run-model-v2):
      - park-adjusted trailing-30g offense replaces raw runs_l7/season
      - opposing starter last-5 (PIT-fixed; the old gameDate lookup leaked)
      - park factor via team ID (old abbreviation lookup never matched)
    Bullpen helpers (bullpen_history_map etc.) are available but not wired
    into v2: the per-game RA/9 construction was too noisy (trades, probable-
    vs-actual mismatches). Future work.
    """
    screen = game["gameDate"][:10]
    home = game["teams"]["home"]
    away = game["teams"]["away"]
    home_id = home["team"]["id"]
    away_id = away["team"]["id"]

    probable_home = home.get("probablePitcher") or {}
    probable_away = away.get("probablePitcher") or {}
    sp_away = starter_features(probable_away.get("id"), screen)
    sp_home = starter_features(probable_home.get("id"), screen)
    park = PARK_FACTOR_BY_TEAM_ID.get(home_id, 1.0)

    def side(
        team_id: int,
        opp_id: int,
        opp_sp: dict | None,
        home_flag: int,
    ) -> TeamGameFeatures:
        st = opp_sp or {"runs": 4.0, "hits": 6.0, "walks": 2.2, "k": 5.5, "deep": 0.55}
        return TeamGameFeatures(
            adj_offense_l30=adj_offense_prior(history, team_id, screen),
            opp_defense_l30=adj_defense_prior(history, opp_id, screen),
            opp_sp_ra_l5=st["runs"],
            opp_sp_hits_l5=st["hits"],
            opp_sp_bb_l5=st["walks"],
            opp_sp_k_l5=st["k"],
            opp_sp_deep_l5=st["deep"],
            park_factor=park,
            home=home_flag,
        )

    return GameFeatures(
        game_pk=str(game["gamePk"]),
        screen_date=screen,
        home_side=side(home_id, away_id, sp_away, 1),
        away_side=side(away_id, home_id, sp_home, 0),
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
