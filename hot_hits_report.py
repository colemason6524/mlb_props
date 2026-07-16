from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import time
import unicodedata
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from mlb_props.config import OUTPUTS_DIR


@dataclass
class GradedHotHit:
    date: str
    rank: int
    batter_name: str
    team: str
    score: int
    tier: str
    batting_order: int | None
    avg_last_5: float
    avg_last_10: float
    season_avg: float
    hit_games_last_5: int
    matchup_rating: float
    pitcher_hits_allowed_rate_last_5: float
    pitcher_k_rate_last_5: float
    batter_vs_pitcher_ab: int | None
    batter_vs_pitcher_avg: float | None
    discord_sim: bool
    result: str
    hits: int | None
    at_bats: int | None
    plate_appearances: int | None
    game_state: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grade exported hot-hit history files and summarize model patterns."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--zip", type=Path, help="Zip containing hot_hits_*.json exports.")
    source.add_argument("--history-dir", type=Path, default=OUTPUTS_DIR / "history")
    parser.add_argument("--since", help="First screen date to include, YYYY-MM-DD.")
    parser.add_argument("--through", help="Last screen date to include, YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, default=6, help="Simulated Discord card size.")
    parser.add_argument("--output", type=Path, help="Optional path to write the report text.")
    parser.add_argument("--json-output", type=Path, help="Optional path to write graded rows as JSON.")
    parser.add_argument(
        "--include-pending",
        action="store_true",
        help="Print pending games in the report instead of only excluding them from rates.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    history_files = _resolve_history_files(args)
    if not history_files:
        print("No hot_hits_*.json history files found.")
        return 1

    rows = grade_history_files(history_files, limit=args.limit)
    rows = [
        row
        for row in rows
        if (not args.since or row.date >= args.since)
        and (not args.through or row.date <= args.through)
    ]
    if not rows:
        print("No graded rows remain after date filtering.")
        return 1

    report = render_report(rows, include_pending=args.include_pending)
    print(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps([row.__dict__ for row in rows], indent=2, sort_keys=True))
    return 0


def _resolve_history_files(args: argparse.Namespace) -> list[Path]:
    if args.zip:
        temp_dir = Path(tempfile.mkdtemp(prefix="hot_hits_report_"))
        with zipfile.ZipFile(args.zip) as archive:
            archive.extractall(temp_dir)
        return sorted(temp_dir.rglob("hot_hits_*.json"))
    return sorted(args.history_dir.glob("hot_hits_*.json"))


def grade_history_files(paths: Iterable[Path], limit: int = 6) -> list[GradedHotHit]:
    api = MlbStatsClient()
    rows: list[dict] = []
    for path in paths:
        payload = json.loads(path.read_text())
        screen_date = payload.get("screen_date")
        candidates = payload.get("candidates", [])
        if not screen_date or not isinstance(candidates, list):
            continue
        for rank, candidate in enumerate(candidates, start=1):
            row = dict(candidate)
            row["date"] = screen_date
            row["rank"] = rank
            row["score"] = current_hot_hit_score(row)
            row.update(api.grade_batter(candidate, screen_date))
            row["tier"] = hot_hit_tier(row)
            rows.append(row)

    discord_keys: set[tuple[str, int, str]] = set()
    for screen_date in sorted({row["date"] for row in rows}):
        day_rows = [row for row in rows if row["date"] == screen_date]
        for row in simulated_discord_card(day_rows, limit=limit):
            discord_keys.add((row["date"], row["rank"], row.get("batter_name", "")))

    graded: list[GradedHotHit] = []
    for row in rows:
        graded.append(
            GradedHotHit(
                date=row["date"],
                rank=int(row["rank"]),
                batter_name=row.get("batter_name", ""),
                team=row.get("team", ""),
                score=int(row.get("score", 0) or 0),
                tier=row["tier"],
                batting_order=row.get("batting_order"),
                avg_last_5=float(row.get("avg_last_5", 0.0) or 0.0),
                avg_last_10=float(row.get("avg_last_10", 0.0) or 0.0),
                season_avg=float(row.get("season_avg", 0.0) or 0.0),
                hit_games_last_5=int(row.get("hit_games_last_5", 0) or 0),
                matchup_rating=float(row.get("matchup_rating", 0.0) or 0.0),
                pitcher_hits_allowed_rate_last_5=float(
                    row.get("pitcher_hits_allowed_rate_last_5", 0.0) or 0.0
                ),
                pitcher_k_rate_last_5=float(row.get("pitcher_k_rate_last_5", 0.0) or 0.0),
                batter_vs_pitcher_ab=row.get("batter_vs_pitcher_ab"),
                batter_vs_pitcher_avg=row.get("batter_vs_pitcher_avg"),
                discord_sim=(row["date"], row["rank"], row.get("batter_name", "")) in discord_keys,
                result=row.get("result", "UNKNOWN"),
                hits=row.get("hits"),
                at_bats=row.get("at_bats"),
                plate_appearances=row.get("plate_appearances"),
                game_state=row.get("game_state"),
            )
        )
    return graded


def current_hot_hit_score(row: dict) -> int:
    score = 0
    avg_last_5 = float(row.get("avg_last_5", 0.0) or 0.0)
    avg_last_10 = float(row.get("avg_last_10", 0.0) or 0.0)
    season_avg = float(row.get("season_avg", 0.0) or 0.0)
    hit_games_last_5 = int(row.get("hit_games_last_5", 0) or 0)
    hit_games_last_10 = int(row.get("hit_games_last_10", 0) or 0)
    batting_order = row.get("batting_order")
    matchup_rating = float(row.get("matchup_rating", 0.0) or 0.0)
    pitcher_hrate = float(row.get("pitcher_hits_allowed_rate_last_5", 0.0) or 0.0)
    pitcher_krate = float(row.get("pitcher_k_rate_last_5", 0.0) or 0.0)
    pitcher_wrate = float(row.get("pitcher_walk_rate_last_5", 0.0) or 0.0)
    pitcher_has_data = bool(
        row.get("pitcher_hits_allowed_rate_last_5")
        or row.get("pitcher_k_rate_last_5")
        or row.get("pitcher_walk_rate_last_5")
    )
    bvp_ab = int(row.get("batter_vs_pitcher_ab", 0) or 0)
    bvp_avg = row.get("batter_vs_pitcher_avg")
    bvp_avg = float(bvp_avg) if bvp_avg is not None else None

    if hit_games_last_5 >= 5:
        score += 2
    elif hit_games_last_5 >= 4:
        score += 1

    if hit_games_last_10 >= 8:
        score += 2
    elif hit_games_last_10 >= 7:
        score += 1

    score += 3 if avg_last_5 >= 0.400 else 2

    if avg_last_10 >= 0.320:
        score += 1
    if season_avg >= 0.285:
        score += 2
    elif season_avg >= 0.260:
        score += 1

    if avg_last_5 - season_avg >= 0.080:
        score += 1
    elif avg_last_5 < season_avg:
        score -= 1

    if batting_order is not None and batting_order <= 4:
        score += 1
    elif batting_order is not None and 5 <= batting_order <= 7:
        if matchup_rating < 0.0 and pitcher_hrate < 0.260:
            score -= 1
    elif batting_order is not None and batting_order >= 8:
        score -= 2

    if matchup_rating >= 0.20:
        score += 3
    elif matchup_rating <= -0.20:
        score -= 2

    if pitcher_hrate >= 0.280:
        score += 2
    elif pitcher_hrate >= 0.260:
        score += 1
    elif pitcher_hrate <= 0.220 and pitcher_has_data:
        score -= 1

    if pitcher_krate <= 0.200 and pitcher_has_data:
        score += 1
    elif pitcher_krate >= 0.285:
        score -= 1

    if pitcher_wrate >= 0.095:
        score -= 1

    if bvp_ab >= 8 and (bvp_avg or 0.0) >= 0.300:
        score += 1
    elif bvp_ab >= 8 and (bvp_avg or 0.0) <= 0.180:
        score -= 1

    return score


def hot_hit_tier(row: dict) -> str:
    batting_order = row.get("batting_order") or 99
    matchup_rating = float(row.get("matchup_rating", 0.0) or 0.0)
    pitcher_hrate = float(row.get("pitcher_hits_allowed_rate_last_5", 0.0) or 0.0)
    avg_last_5 = float(row.get("avg_last_5", 0.0) or 0.0)
    hit_games_last_5 = int(row.get("hit_games_last_5", 0) or 0)
    score = int(row.get("score", 0) or 0)

    low_order = batting_order >= 8
    value_order = 5 <= batting_order <= 7
    matchup_floor = matchup_rating > -0.20
    strong_contact_spot = pitcher_hrate >= 0.280 or matchup_rating >= 0.20
    hot_hand = avg_last_5 >= 0.400 or hit_games_last_5 >= 5
    support_count = hot_hit_support_count(row)

    if score >= 14 and batting_order <= 4 and matchup_floor and support_count >= 2:
        return "Core"
    if score >= 12 and hot_hand and matchup_floor and not low_order and support_count >= 2:
        return "Value"
    if score >= 11 and value_order and hot_hand and strong_contact_spot and support_count >= 2:
        return "Value"
    return "Thin"


def hot_hit_discord_eligible(row: dict, min_score: int = 10) -> bool:
    batting_order = row.get("batting_order") or 99
    score = int(row.get("score", 0) or 0)
    support_count = hot_hit_support_count(row)
    if score >= 14 and support_count >= 1 and batting_order <= 7:
        return True
    if score >= max(min_score, 12) and support_count >= 2 and batting_order <= 7:
        return True
    return False


def hot_hit_support_count(row: dict) -> int:
    batting_order = row.get("batting_order")
    matchup_rating = float(row.get("matchup_rating", 0.0) or 0.0)
    pitcher_hrate = float(row.get("pitcher_hits_allowed_rate_last_5", 0.0) or 0.0)
    avg_last_5 = float(row.get("avg_last_5", 0.0) or 0.0)
    season_avg = float(row.get("season_avg", 0.0) or 0.0)
    return sum(
        [
            bool(batting_order is not None and batting_order <= 4),
            matchup_rating >= 0.20,
            pitcher_hrate >= 0.260,
            season_avg >= 0.280,
            avg_last_5 >= 0.380,
        ]
    )


def hot_hit_discord_sort_key(row: dict) -> tuple[int, int, int, float, float, float, int]:
    batting_order = row.get("batting_order") or 99
    return (
        hot_hit_support_count(row),
        int(row.get("score", 0) or 0),
        1 if batting_order <= 4 else 0,
        float(row.get("matchup_rating", 0.0) or 0.0),
        float(row.get("pitcher_hits_allowed_rate_last_5", 0.0) or 0.0),
        float(row.get("season_avg", 0.0) or 0.0),
        -batting_order,
    )


def simulated_discord_card(rows: list[dict], limit: int = 6) -> list[dict]:
    eligible = [row for row in rows if hot_hit_discord_eligible(row)]
    eligible = sorted(eligible, key=hot_hit_discord_sort_key, reverse=True)
    shown: list[dict] = []
    for tier_name in ("Core", "Value", "Thin"):
        tier_rows = [row for row in eligible if hot_hit_tier(row) == tier_name]
        take = max(0, limit - len(shown))
        shown.extend(tier_rows[:take])
    return shown[:limit]


class MlbStatsClient:
    def __init__(self) -> None:
        self._teams = self._load_teams()
        self._schedule_cache: dict[str, list[dict]] = {}
        self._boxscore_cache: dict[int, dict] = {}

    def grade_batter(self, candidate: dict, screen_date: str) -> dict:
        game, side = self._find_game(candidate.get("team", ""), screen_date)
        if game is None or side is None:
            return _grade_result("NO_GAME")
        state = (game.get("status") or {}).get("detailedState") or (game.get("status") or {}).get(
            "abstractGameState"
        )
        if not self._is_final(game):
            return _grade_result("PENDING", game_state=state)

        boxscore = self._boxscore(int(game["gamePk"]))
        players = boxscore["teams"][side].get("players", {})
        player = self._find_player(players, candidate)
        if player is None:
            return _grade_result("NO_PLAYER", game_state=state)

        batting = player.get("stats", {}).get("batting", {})
        hits = int(batting.get("hits", 0) or 0)
        at_bats = int(batting.get("atBats", 0) or 0)
        plate_appearances = int(batting.get("plateAppearances", 0) or 0)
        if hits >= 1:
            result = "HIT"
        elif plate_appearances == 0 and at_bats == 0:
            result = "DNP"
        else:
            result = "MISS"
        return _grade_result(
            result,
            hits=hits,
            at_bats=at_bats,
            plate_appearances=plate_appearances,
            game_state=state,
        )

    def _load_teams(self) -> dict[str, int]:
        data = self._get_json("https://statsapi.mlb.com/api/v1/teams?sportId=1")
        teams = {team.get("abbreviation"): team.get("id") for team in data.get("teams", [])}
        if "OAK" in teams:
            teams.setdefault("ATH", teams["OAK"])
        return teams

    def _find_game(self, team: str, screen_date: str) -> tuple[dict | None, str | None]:
        team_id = self._teams.get(team)
        if team_id is None:
            return None, None
        for game in self._schedule(screen_date):
            away_id = game["teams"]["away"]["team"]["id"]
            home_id = game["teams"]["home"]["team"]["id"]
            if team_id == away_id:
                return game, "away"
            if team_id == home_id:
                return game, "home"
        return None, None

    def _schedule(self, screen_date: str) -> list[dict]:
        if screen_date not in self._schedule_cache:
            data = self._get_json(
                f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={screen_date}"
            )
            self._schedule_cache[screen_date] = [
                game for date_item in data.get("dates", []) for game in date_item.get("games", [])
            ]
        return self._schedule_cache[screen_date]

    def _boxscore(self, game_pk: int) -> dict:
        if game_pk not in self._boxscore_cache:
            self._boxscore_cache[game_pk] = self._get_json(
                f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
            )
        return self._boxscore_cache[game_pk]

    def _find_player(self, players: dict, candidate: dict) -> dict | None:
        batter_id = candidate.get("batter_id")
        if batter_id is not None:
            player = players.get(f"ID{batter_id}")
            if player is not None:
                return player
        target = _normalize_name(candidate.get("batter_name", ""))
        for player in players.values():
            if _normalize_name(player.get("person", {}).get("fullName", "")) == target:
                return player
        for player in players.values():
            full_name = _normalize_name(player.get("person", {}).get("fullName", ""))
            if target and (target in full_name or full_name in target):
                return player
        return None

    @staticmethod
    def _is_final(game: dict) -> bool:
        status = game.get("status") or {}
        return status.get("abstractGameState") == "Final" or status.get("detailedState") in {
            "Final",
            "Game Over",
        }

    @staticmethod
    def _get_json(url: str, tries: int = 4) -> dict:
        last_error: Exception | None = None
        for attempt in range(tries):
            try:
                request = Request(url, headers={"User-Agent": "mlb-props-hot-hits-report/1.0"})
                with urlopen(request, timeout=30) as response:
                    return json.loads(response.read().decode())
            except (HTTPError, URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                time.sleep(0.5 * (attempt + 1))
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Unable to fetch {url}")


def _grade_result(
    result: str,
    *,
    hits: int | None = None,
    at_bats: int | None = None,
    plate_appearances: int | None = None,
    game_state: str | None = None,
) -> dict:
    return {
        "result": result,
        "hits": hits,
        "at_bats": at_bats,
        "plate_appearances": plate_appearances,
        "game_state": game_state,
    }


def _normalize_name(value: str) -> str:
    normalized = "".join(
        char for char in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(char)
    )
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def render_report(rows: list[GradedHotHit], include_pending: bool = False) -> str:
    final_rows = [row for row in rows if row.result != "PENDING"]
    discord_rows = [row for row in final_rows if row.discord_sim]
    lines = [
        "MLB Hot Hits Backtest Report",
        f"Dates: {min(row.date for row in rows)} through {max(row.date for row in rows)}",
        "",
        "Overall",
        _summary_line("Full pool", final_rows),
        _summary_line("Simulated Discord", discord_rows),
        "",
        "By Day",
    ]
    for screen_date in sorted({row.date for row in rows}):
        day_rows = [row for row in rows if row.date == screen_date and row.discord_sim and row.result != "PENDING"]
        lines.append(_summary_line(screen_date, day_rows))

    lines.extend(["", "Tiers"])
    for tier_name in ("Core", "Value", "Thin"):
        lines.append(_summary_line(tier_name, [row for row in discord_rows if row.tier == tier_name]))

    lines.extend(["", "Score Bands"])
    score_bands: list[tuple[str, Callable[[GradedHotHit], bool]]] = [
        ("score >= 18", lambda row: row.score >= 18),
        ("score >= 16", lambda row: row.score >= 16),
        ("score >= 14", lambda row: row.score >= 14),
        ("score 12-13", lambda row: 12 <= row.score <= 13),
        ("score 10-11", lambda row: 10 <= row.score <= 11),
        ("score < 10", lambda row: row.score < 10),
    ]
    for label, predicate in score_bands:
        lines.append(_summary_line(label, [row for row in final_rows if predicate(row)]))

    lines.extend(["", "Lineup And PA"])
    buckets: list[tuple[str, Callable[[GradedHotHit], bool]]] = [
        ("BO <= 4", lambda row: (row.batting_order or 99) <= 4),
        ("BO 5-7", lambda row: 5 <= (row.batting_order or 99) <= 7),
        ("BO >= 8", lambda row: (row.batting_order or 0) >= 8),
        ("0 PA / DNP", lambda row: row.result == "DNP"),
        ("1-2 PA", lambda row: row.plate_appearances is not None and 1 <= row.plate_appearances <= 2),
        ("3+ PA", lambda row: row.plate_appearances is not None and row.plate_appearances >= 3),
    ]
    for label, predicate in buckets:
        lines.append(_summary_line(label, [row for row in final_rows if predicate(row)]))

    lines.extend(["", "Signals"])
    signals: list[tuple[str, Callable[[GradedHotHit], bool]]] = [
        ("Match >= +0.20", lambda row: row.matchup_rating >= 0.20),
        ("Match < 0", lambda row: row.matchup_rating < 0),
        ("Pitcher HRate >= .280", lambda row: row.pitcher_hits_allowed_rate_last_5 >= 0.280),
        ("Pitcher HRate < .220", lambda row: 0 < row.pitcher_hits_allowed_rate_last_5 < 0.220),
        ("L5 AVG >= .450", lambda row: row.avg_last_5 >= 0.450),
        ("Hit games 5/5", lambda row: row.hit_games_last_5 == 5),
        ("Season AVG >= .300", lambda row: row.season_avg >= 0.300),
        ("BvP >= 8 AB", lambda row: (row.batter_vs_pitcher_ab or 0) >= 8),
    ]
    for label, predicate in signals:
        lines.append(_summary_line(label, [row for row in discord_rows if predicate(row)]))

    lines.extend(["", "Day Shape"])
    lines.extend(_day_shape_lines(rows))

    lines.extend(["", "Displayed Misses"])
    displayed_misses = [
        row for row in discord_rows if row.result in {"MISS", "DNP", "NO_PLAYER", "NO_GAME"}
    ]
    lines.extend(_row_lines(displayed_misses, limit=20))

    lines.extend(["", "Missed Hits Outside Discord"])
    missed_hits = [
        row
        for row in final_rows
        if not row.discord_sim and row.result == "HIT" and (row.rank <= 12 or row.score >= 12)
    ]
    missed_hits.sort(key=lambda row: (row.date, -row.score, row.rank))
    lines.extend(_row_lines(missed_hits, limit=25))

    pending = [row for row in rows if row.result == "PENDING"]
    if include_pending and pending:
        lines.extend(["", "Pending"])
        lines.extend(_row_lines(pending, limit=25))

    return "\n".join(lines)


def _summary_line(label: str, rows: list[GradedHotHit]) -> str:
    graded = [row for row in rows if row.result in {"HIT", "MISS"}]
    hits = sum(row.result == "HIT" for row in graded)
    misses = sum(row.result == "MISS" for row in graded)
    dnp = sum(row.result == "DNP" for row in rows)
    unknown = len(rows) - len(graded) - dnp
    rate = hits / len(graded) if graded else 0.0
    nonpending_rate = hits / len(rows) if rows else 0.0
    return (
        f"{label}: {hits}/{len(graded)} ({rate:.1%})"
        f" | miss {misses} | DNP {dnp} | other {unknown} | incl all {nonpending_rate:.1%}"
    )


def _day_shape_lines(rows: list[GradedHotHit]) -> list[str]:
    lines: list[str] = []
    for screen_date in sorted({row.date for row in rows}):
        card = [row for row in rows if row.date == screen_date and row.discord_sim and row.result != "PENDING"]
        if not card:
            continue
        hits = sum(row.result == "HIT" for row in card if row.result in {"HIT", "MISS"})
        graded = sum(row.result in {"HIT", "MISS"} for row in card)
        rate = hits / graded if graded else 0.0
        lines.append(
            f"{screen_date}: {hits}/{graded} ({rate:.1%})"
            f" | shape {_card_shape(card)}"
            f" | avg score {_avg(row.score for row in card):.1f}"
            f" | avg match {_avg(row.matchup_rating for row in card):+.2f}"
            f" | avg HRate {_avg(row.pitcher_hits_allowed_rate_last_5 for row in card):.3f}"
        )
    return lines


def _card_shape(rows: list[GradedHotHit]) -> str:
    core_count = sum(row.tier == "Core" for row in rows)
    value_count = sum(row.tier == "Value" for row in rows)
    thin_count = sum(row.tier == "Thin" for row in rows)
    match_count = sum(row.matchup_rating >= 0.20 for row in rows)
    hot_count = sum(row.avg_last_5 >= 0.450 for row in rows)
    if thin_count >= 3:
        return "Thin-heavy"
    if value_count >= core_count:
        return "Value-balanced"
    if core_count >= 6:
        return "Core-heavy"
    if match_count >= 4:
        return "Matchup-driven"
    if hot_count >= 4:
        return "Hot-form driven"
    return "Balanced"


def _row_lines(rows: list[GradedHotHit], limit: int) -> list[str]:
    if not rows:
        return ["None"]
    lines = []
    for row in rows[:limit]:
        lines.append(
            f"{row.date} {row.tier} rank{row.rank} score={row.score} "
            f"{row.batter_name} ({row.team}) BO={row.batting_order or '-'} "
            f"L5={row.avg_last_5:.3f} Szn={row.season_avg:.3f} "
            f"Match={row.matchup_rating:+.2f} HRate={row.pitcher_hits_allowed_rate_last_5:.3f} "
            f"{row.result} H={row.hits} AB={row.at_bats} PA={row.plate_appearances}"
        )
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more")
    return lines


def _avg(values: Iterable[float | int | None]) -> float:
    nums = [float(value) for value in values if value is not None]
    return sum(nums) / len(nums) if nums else 0.0


if __name__ == "__main__":
    raise SystemExit(main())
