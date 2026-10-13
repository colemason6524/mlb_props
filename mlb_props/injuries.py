"""MLB injury impact engine.

Turns the ESPN injury feed into model inputs BEFORE the board is built:

* SP scratch / change: when the ESPN probable pitcher disagrees with the
  MLB Stats API slate (or the listed SP sits on the OUT injury list), the
  slate's ``probablePitcher`` is overridden so the game engine re-projects
  with the actual starter's last-5 stats instead of the scratched arm's.
  When no replacement is known the starter is blanked and the engine's
  league-average fallback applies -- strictly better than the wrong SP.
* Key hitter OUT: debits the team's park-adjusted trailing-30g offense
  (``adj_offense_l30``) by the hitter's marginal runs above a replacement
  bat, mirroring the WNBA ``max(0, season_ppg - 6.0)`` debit shape.
* Bullpen arms: noted, never debited (minor impact by design).

Free data only: ESPN core API (injuries + scoreboard probables) and the
MLB Stats API (people search + season hitting stats).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from .cache import JsonCache
from .sources.espn_injuries import EspnInjurySource
from .utils import fetch_json, normalize_name, normalize_team_abbr

# Injury statuses that mean the player is unavailable today. Day-To-Day
# is deliberately excluded: DTD hitters routinely play, and Cole's rule
# is that only confirmed OUTs move the model.
OUT_STATUSES = {
    "out",
    "il",
    "10-day-il",
    "15-day-il",
    "60-day-il",
    "7-day-il",
    "injured list",
}

# A "key hitter" is a top-3/4-calibre bat: OPS at/above this with a real
# sample of plate appearances. Playoff-team 4th bats live ~.780-.820.
KEY_HITTER_OPS = 0.780
KEY_HITTER_MIN_PA = 200

# Offense debit: marginal runs/game of the hitter above a replacement bat.
# WNBA shape: max(0, production - replacement_level). A team-level +0.080
# OPS is worth ~+0.7 runs/game over ~38 PA, i.e. ~0.23 runs per
# (OPS point x PA); a full-time bat sees ~4.3 PA/game.
REPLACEMENT_OPS = 0.650
EXPECTED_PA_PER_GAME = 4.3
RUNS_PER_OPS_POINT_PER_PA = 0.23
MAX_OFFENSE_DEBIT_RUNS = 0.60

ESPN_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard"
    "?dates={yyyymmdd}"
)
MLBAM_SEARCH_URL = "https://statsapi.mlb.com/api/v1/people/search?names={name}"
MLBAM_SEASON_HITTING_URL = (
    "https://statsapi.mlb.com/api/v1/people/{mlbam_id}/stats"
    "?stats=season&group=hitting&season={season}"
)

_MISSING_NAMES = {"", "tbd", "to be announced"}

# ESPN pitcher position abbreviations. Relievers (RP/CP) are noted but
# never debited; only the probable-starter path moves the model.
PITCHER_POSITIONS = {"P", "SP", "RP", "CP"}


def _is_pitcher(position: str | None) -> bool:
    return (position or "").strip().upper() in PITCHER_POSITIONS


def offense_debit_runs(ops: float | None, pa: int | None) -> float:
    """Runs/game debit for a key hitter who is OUT.

    ``max(0, OPS - replacement)`` scaled to a full-time bat's plate
    appearances, capped so one absence can never move a team total by
    more than a clear single-bat effect.
    """
    if ops is None or pa is None:
        return 0.0
    if pa < KEY_HITTER_MIN_PA:
        return 0.0
    raw = max(0.0, ops - REPLACEMENT_OPS) * EXPECTED_PA_PER_GAME * RUNS_PER_OPS_POINT_PER_PA
    return round(min(raw, MAX_OFFENSE_DEBIT_RUNS), 3)


def is_key_hitter(ops: float | None, pa: int | None) -> bool:
    return (
        ops is not None
        and pa is not None
        and pa >= KEY_HITTER_MIN_PA
        and ops >= KEY_HITTER_OPS
    )


def resolve_mlbam_id(player_name: str, cache: JsonCache) -> int | None:
    """Best-effort MLBAM person id for a display name, cached for 7 days."""
    norm = normalize_name(player_name)
    cache_key = f"mlbam_id_{norm}"
    cached = cache.get(cache_key)
    if cached:
        try:
            return int(cached)
        except (TypeError, ValueError):
            pass
    try:
        payload = fetch_json(MLBAM_SEARCH_URL.format(name=player_name.replace(" ", "%20")))
    except Exception:
        return None
    people = payload.get("people") or []
    best: int | None = None
    for person in people:
        if normalize_name(person.get("fullName", "")) == norm:
            best = person.get("id")
            break
    if best is None and people:
        best = people[0].get("id")
    if best is not None:
        cache.set(cache_key, int(best))
    return best


def season_hitting(mlbam_id: int, season: int, cache: JsonCache) -> tuple[float | None, int | None]:
    """(OPS, plateAppearances) for a season, cached ~12h (lineups change daily)."""
    cache_key = f"season_hitting_{mlbam_id}_{season}"
    cached = cache.get(cache_key)
    if cached:
        return cached.get("ops"), cached.get("pa")
    try:
        payload = fetch_json(MLBAM_SEASON_HITTING_URL.format(mlbam_id=mlbam_id, season=season))
    except Exception:
        return None, None
    ops: float | None = None
    pa: int | None = None
    for block in payload.get("stats", []):
        for split in block.get("splits", []):
            stat = split.get("stat") or {}
            try:
                ops = float(stat["ops"]) if stat.get("ops") not in (None, "") else None
            except (TypeError, ValueError):
                ops = None
            try:
                pa = int(stat["plateAppearances"]) if stat.get("plateAppearances") not in (None, "") else None
            except (TypeError, ValueError):
                pa = None
    cache.set(cache_key, {"ops": ops, "pa": pa})
    return ops, pa


def fetch_espn_probables(screen: str) -> dict[tuple[str, str], dict[str, str]]:
    """ESPN probable starters per game, keyed by (away_abbr, home_abbr).

    Values are {"away": name, "home": name}; a side is "" when ESPN names
    no probable. Never raises: on failure returns {} and the slate stands.
    """
    yyyymmdd = screen.replace("-", "")
    try:
        payload = fetch_json(ESPN_SCOREBOARD_URL.format(yyyymmdd=yyyymmdd))
    except Exception:
        return {}
    probables: dict[tuple[str, str], dict[str, str]] = {}
    for event in payload.get("events", []):
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competitors = competitions[0].get("competitors") or []
        sides: dict[str, str] = {}
        abbrs: dict[str, str] = {}
        for comp in competitors:
            side = comp.get("homeAway")
            abbrs[side] = normalize_team_abbr((comp.get("team") or {}).get("abbreviation", ""))
            names = [
                (p.get("athlete") or {}).get("displayName", "")
                for p in comp.get("probables") or []
            ]
            names = [n for n in names if n]
            sides[side] = names[0] if names else ""
        if abbrs.get("away") and abbrs.get("home"):
            probables[(abbrs["away"], abbrs["home"])] = {
                "away": sides.get("away", ""),
                "home": sides.get("home", ""),
            }
    return probables


def _slate_probable(game: dict, side: str) -> tuple[str, int | None]:
    info = (game.get("teams", {}).get(side, {}).get("probablePitcher")) or {}
    name = (info.get("fullName") or "").strip()
    pid = info.get("id")
    try:
        pid = int(pid) if pid is not None else None
    except (TypeError, ValueError):
        pid = None
    return name, pid


# MLBAM team id -> repo-canonical abbreviation. The schedule hydrate used
# for the slate does not carry abbreviations, so ids are the reliable key.
TEAM_ABBR_BY_ID = {
    108: "LAA", 109: "AZ", 110: "BAL", 111: "BOS", 112: "CHC", 113: "CIN",
    114: "CLE", 115: "COL", 116: "DET", 117: "HOU", 118: "KC", 119: "LAD",
    120: "WSH", 121: "NYM", 133: "ATH", 134: "PIT", 135: "SD", 136: "SEA",
    137: "SF", 138: "STL", 139: "TB", 140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}


def _team_abbr(game: dict, side: str) -> str:
    team = (game.get("teams", {}).get(side, {}).get("team", {})) or {}
    abbr = team.get("abbreviation") or TEAM_ABBR_BY_ID.get(team.get("id"), "")
    return normalize_team_abbr(abbr)


def build_adjustments(
    screen: str,
    slate_games: list[dict],
    id_cache: JsonCache,
    stats_cache: JsonCache,
    injury_source: EspnInjurySource,
    *,
    season: int | None = None,
) -> dict[str, Any]:
    """Build the per-game injury adjustments artifact for a screen date.

    Never raises for ESPN/MLBAM hiccups: per-team failures degrade that
    team's entry (recorded in ``errors``) while the rest of the slate
    still gets adjustments.
    """
    season = season or date.fromisoformat(screen).year
    espn_probables = fetch_espn_probables(screen)
    try:
        fetch_json(ESPN_SCOREBOARD_URL.format(yyyymmdd=screen.replace("-", "")))
        espn_ok = True
    except Exception:
        espn_ok = False

    games: dict[str, Any] = {}
    injuries_out: list[dict[str, Any]] = []
    errors: list[str] = []
    team_injuries: dict[str, list] = {}

    for game in slate_games:
        pk = str(game.get("gamePk"))
        state = (game.get("status") or {}).get("abstractGameState", "")
        away = _team_abbr(game, "away")
        home = _team_abbr(game, "home")
        if not away or not home:
            continue

        for team in (away, home):
            if team in team_injuries:
                continue
            try:
                team_injuries[team] = injury_source.fetch_team_injuries(
                    team, date.fromisoformat(screen), force_refresh=True
                )
            except Exception as exc:
                errors.append(f"{team}: injury fetch failed ({type(exc).__name__})")
                team_injuries[team] = []

        out_norms = {
            inj.player_name_norm
            for team in (away, home)
            for inj in team_injuries.get(team, [])
            if inj.status.strip().lower() in OUT_STATUSES
        }

        entry: dict[str, Any] = {
            "away_team": away,
            "home_team": home,
            "game_state": state,
            "notes": [],
        }
        scratched: list[str] = []

        for side, team in (("away", away), ("home", home)):
            slate_name, slate_id = _slate_probable(game, side)
            espn_name = (espn_probables.get((away, home)) or {}).get(side, "")
            slate_norm = normalize_name(slate_name)
            espn_norm = normalize_name(espn_name)
            entry[f"slate_probable_{side}"] = {"name": slate_name, "mlbam_id": slate_id}
            entry[f"espn_probable_{side}"] = espn_name

            slate_missing = slate_norm in _MISSING_NAMES
            espn_missing = espn_norm in _MISSING_NAMES
            scratched_by_feed = (
                bool(slate_norm)
                and slate_norm not in _MISSING_NAMES
                and slate_norm in out_norms
            )
            espn_disagrees = (
                not slate_missing and not espn_missing and espn_norm != slate_norm
            )

            override: dict[str, Any] | None = None
            if state in ("Final", "Live"):
                status = "game_started"
            elif scratched_by_feed or espn_disagrees:
                scratched.append(slate_norm)
                if not espn_missing and espn_norm != slate_norm:
                    mlbam_id = resolve_mlbam_id(espn_name, id_cache)
                    override = {"name": espn_name, "mlbam_id": mlbam_id}
                    status = "overridden"
                    entry["notes"].append(
                        f"SP change ({team}): {slate_name or '?'} -> {espn_name}"
                        + ("" if mlbam_id else " [unresolved id: league-avg SP features]")
                    )
                else:
                    # Scratched with no named replacement: blank the starter
                    # so the engine falls back to league-average SP features
                    # instead of projecting with the wrong arm's stats.
                    override = {"name": "", "mlbam_id": None}
                    status = "scratched_unknown_replacement"
                    entry["notes"].append(
                        f"SP scratched ({team}): {slate_name}; replacement unknown"
                    )
            elif slate_missing and not espn_missing:
                mlbam_id = resolve_mlbam_id(espn_name, id_cache)
                override = {"name": espn_name, "mlbam_id": mlbam_id}
                status = "filled_from_espn"
                entry["notes"].append(f"SP filled from ESPN ({team}): {espn_name}")
            else:
                status = "confirmed"
            entry[f"sp_override_{side}"] = override
            entry[f"sp_status_{side}"] = status

        # Key-hitter debits: one pass per team (the debit applies to the
        # team's batting side in this game).
        seen: set[str] = set()
        for team in (away, home):
            debit = 0.0
            for inj in team_injuries.get(team, []):
                if inj.status.strip().lower() not in OUT_STATUSES:
                    continue
                key = (team, inj.player_name_norm)
                if key not in seen:
                    seen.add(key)
                    injuries_out.append(inj.__dict__)
                if _is_pitcher(inj.position):
                    # Pitchers: the SP case is handled above; relievers are
                    # tracked but never debited or displayed (noise, not signal).
                    if inj.player_name_norm not in scratched:
                        inj.impact_level = "minor"
                    else:
                        inj.impact_level = "sp_scratch"
                    continue
                mlbam_id = resolve_mlbam_id(inj.player_name, id_cache)
                ops, pa = (
                    season_hitting(mlbam_id, season, stats_cache) if mlbam_id else (None, None)
                )
                inj.season_ops = ops
                inj.season_pa = pa
                if is_key_hitter(ops, pa):
                    inj.impact_level = "key_hitter"
                    inj.offense_debit_runs = offense_debit_runs(ops, pa)
                    debit += inj.offense_debit_runs
                    entry["notes"].append(
                        f"Key hitter OUT ({team}): {inj.player_name} "
                        f"(OPS {ops:.3f}) -{inj.offense_debit_runs:.2f} runs"
                    )
                else:
                    inj.impact_level = "minor"
            side = "away" if team == away else "home"
            entry[f"{side}_offense_debit"] = round(debit, 3)

        entry["scratched_sps"] = scratched
        games[pk] = entry

    status = "ok"
    if errors or not espn_ok:
        status = "degraded"
    return {
        "screen_date": screen,
        "generated_at": date.today().isoformat(),
        "status": status,
        "espn_reachable": espn_ok,
        "games": games,
        "injuries": injuries_out,
        "errors": errors,
    }


def apply_sp_overrides(slate_games: list[dict], adjustments: dict[str, Any]) -> int:
    """Apply SP overrides onto slate game dicts in place.

    Returns the number of games whose probable pitcher was changed.
    """
    games = (adjustments or {}).get("games", {})
    changed = 0
    for game in slate_games:
        entry = games.get(str(game.get("gamePk")))
        if not entry:
            continue
        for side in ("away", "home"):
            override = entry.get(f"sp_override_{side}")
            if not override:
                continue
            name = override.get("name") or ""
            mlbam_id = override.get("mlbam_id")
            info: dict[str, Any] = {}
            if name:
                info["fullName"] = name
            if mlbam_id:
                info["id"] = mlbam_id
            game["teams"][side]["probablePitcher"] = info
            changed += 1
    return changed


def offense_debits(adjustments: dict[str, Any], game_pk: str) -> tuple[float, float]:
    """(away_debit, home_debit) runs for a game; (0, 0) when unknown."""
    entry = ((adjustments or {}).get("games", {}) or {}).get(str(game_pk), {})
    try:
        return (
            float(entry.get("away_offense_debit") or 0.0),
            float(entry.get("home_offense_debit") or 0.0),
        )
    except (TypeError, ValueError):
        return 0.0, 0.0


def scratched_sp_norms(adjustments: dict[str, Any]) -> set[str]:
    """Normalized names of scratched starters (pitcher props must exclude)."""
    names: set[str] = set()
    for entry in ((adjustments or {}).get("games", {}) or {}).values():
        for norm in entry.get("scratched_sps") or []:
            if norm:
                names.add(norm)
    return names


def injury_notes_for_game(adjustments: dict[str, Any], game_pk: str) -> list[str]:
    entry = ((adjustments or {}).get("games", {}) or {}).get(str(game_pk), {})
    return list(entry.get("notes") or [])
