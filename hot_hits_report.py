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
from mlb_props.hot_hits_policy import (
    CORE_FIRST_POLICY_VERSION,
    HOT_HITS_POLICY_VERSION,
    HotHitPolicySnapshot,
    HotHitScoringInput,
    hot_hit_discord_eligible as policy_hot_hit_discord_eligible,
    hot_hit_discord_sort_key as policy_hot_hit_discord_sort_key,
    hot_hit_support_count as policy_hot_hit_support_count,
    hot_hit_tier as policy_hot_hit_tier,
    score_hot_hit_candidate,
    select_core_first_hot_hits_card,
    select_current_hot_hits_card,
)

DELIVERED_CARD_POLICY = "delivered"


@dataclass
class GradedHotHit:
    date: str
    rank: int
    discord_rank: int | None
    discord_role: str | None
    batter_name: str
    team: str
    original_score: int
    score: int
    tier: str
    batting_order: int | None
    avg_last_5: float
    avg_last_10: float
    season_avg: float
    hit_games_last_5: int
    hit_games_last_10: int
    matchup_rating: float
    pitcher_hits_allowed_rate_last_5: float
    pitcher_k_rate_last_5: float
    pitcher_walk_rate_last_5: float
    batter_vs_pitcher_ab: int | None
    batter_vs_pitcher_avg: float | None
    discord_sim: bool
    result: str
    hits: int | None
    at_bats: int | None
    plate_appearances: int | None
    game_state: str | None
    current_gate_qualified: bool
    current_display_qualified: bool
    gate_failures: list[str]
    confidence_probability: float | None
    confidence_percentage: int | None
    confidence_label: str | None
    confidence_reliability: float | None
    confidence_model_version: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Grade exported hot-hit history files and summarize model patterns."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--zip", type=Path, help="Zip containing hot_hits_*.json exports.")
    source.add_argument("--history-dir", type=Path, default=OUTPUTS_DIR / "history")
    parser.add_argument("--since", help="First screen date to include, YYYY-MM-DD.")
    parser.add_argument("--through", help="Last screen date to include, YYYY-MM-DD.")
    parser.add_argument("--limit", type=int, default=4, help="Core limit or legacy card size.")
    parser.add_argument(
        "--card-policy",
        choices=[
            CORE_FIRST_POLICY_VERSION,
            HOT_HITS_POLICY_VERSION,
            DELIVERED_CARD_POLICY,
        ],
        default=CORE_FIRST_POLICY_VERSION,
        help=(
            "Discord selection policy to grade. 'delivered' uses exact successfully sent "
            "selection metadata available in newer exports."
        ),
    )
    parser.add_argument(
        "--value-limit",
        type=int,
        default=2,
        help="Maximum risky Value fallbacks for the Core-first policy.",
    )
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

    rows = grade_history_files(
        history_files,
        limit=args.limit,
        card_policy=args.card_policy,
        value_limit=args.value_limit,
        since=args.since,
        through=args.through,
    )
    rows = [
        row
        for row in rows
        if (not args.since or row.date >= args.since)
        and (not args.through or row.date <= args.through)
    ]
    if not rows:
        print("No graded rows remain after date filtering.")
        return 1

    report = render_report(
        rows,
        include_pending=args.include_pending,
        card_policy=args.card_policy,
    )
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
    return sorted(args.history_dir.rglob("hot_hits_*.json"))


def _latest_history_payloads(paths: Iterable[Path]) -> list[dict]:
    latest_by_date: dict[str, tuple[tuple[str, str], dict]] = {}
    for path in paths:
        payload = json.loads(path.read_text())
        screen_date = payload.get("screen_date")
        candidates = payload.get("candidates", [])
        if not screen_date or not isinstance(candidates, list):
            continue
        recency_key = (str(payload.get("generated_at", "")), str(path))
        existing = latest_by_date.get(screen_date)
        if existing is None or recency_key > existing[0]:
            latest_by_date[screen_date] = (recency_key, payload)
    return [latest_by_date[screen_date][1] for screen_date in sorted(latest_by_date)]


def grade_history_files(
    paths: Iterable[Path],
    limit: int = 4,
    *,
    card_policy: str = CORE_FIRST_POLICY_VERSION,
    value_limit: int = 2,
    since: str | None = None,
    through: str | None = None,
) -> list[GradedHotHit]:
    api = MlbStatsClient()
    rows: list[dict] = []
    for payload in _latest_history_payloads(paths):
        screen_date = payload["screen_date"]
        if (since and screen_date < since) or (through and screen_date > through):
            continue
        production_candidates = payload["candidates"]
        production_keys = {
            _candidate_delivery_key(candidate) for candidate in production_candidates
        }
        research_candidates = payload.get("confidence_research_pool")
        candidates = (
            research_candidates
            if isinstance(research_candidates, list) and research_candidates
            else production_candidates
        )
        delivery = payload.get("discord_delivery") or {}
        delivered_items: dict[tuple[str, str], tuple[int, str]] = {}
        if delivery.get("status") == "sent":
            for role, delivery_key in (
                ("Core", "core"),
                ("Value", "optional_value"),
                ("Thin", "thin"),
            ):
                for delivered in delivery.get(delivery_key, []):
                    delivered_key_value = _candidate_delivery_key(delivered)
                    delivered_items[delivered_key_value] = (
                        int(delivered.get("rank", 0) or 0),
                        role,
                    )
        for rank, candidate in enumerate(candidates, start=1):
            row = dict(candidate)
            row["date"] = screen_date
            row["rank"] = rank
            row["original_score"] = int(row.get("score", 0) or 0)
            row["score"] = current_hot_hit_score(row)
            row.update(api.grade_batter(candidate, screen_date))
            row["current_display_qualified"] = bool(
                row.get(
                    "current_display_qualified",
                    _candidate_delivery_key(row) in production_keys,
                )
            )
            row["current_gate_qualified"] = bool(
                row.get("current_gate_qualified", row["current_display_qualified"])
            )
            row["tier"] = (
                hot_hit_tier(row)
                if row["current_display_qualified"]
                else "Research"
            )
            delivered_rank_role = delivered_items.get(_candidate_delivery_key(row))
            row["_delivered_rank"] = (
                delivered_rank_role[0] if delivered_rank_role else None
            )
            row["_delivered_role"] = (
                delivered_rank_role[1] if delivered_rank_role else None
            )
            rows.append(row)

    discord_ranks: dict[tuple[str, int, str], int] = {}
    discord_roles: dict[tuple[str, int, str], str] = {}
    for screen_date in sorted({row["date"] for row in rows}):
        day_rows = [row for row in rows if row["date"] == screen_date]
        selected = simulated_discord_card(
            day_rows,
            limit=limit,
            card_policy=card_policy,
            value_limit=value_limit,
        )
        for discord_rank, row in enumerate(selected, start=1):
            key = (row["date"], row["rank"], row.get("batter_name", ""))
            discord_ranks[key] = discord_rank
            discord_roles[key] = (
                row.get("_delivered_role")
                or row.get("tier")
                or "Thin"
            )

    graded: list[GradedHotHit] = []
    for row in rows:
        key = (row["date"], row["rank"], row.get("batter_name", ""))
        confidence = row.get("confidence_estimate") or {}
        graded.append(
            GradedHotHit(
                date=row["date"],
                rank=int(row["rank"]),
                discord_rank=discord_ranks.get(key),
                discord_role=discord_roles.get(key),
                batter_name=row.get("batter_name", ""),
                team=row.get("team", ""),
                original_score=int(row.get("original_score", 0) or 0),
                score=int(row.get("score", 0) or 0),
                tier=row["tier"],
                batting_order=row.get("batting_order"),
                avg_last_5=float(row.get("avg_last_5", 0.0) or 0.0),
                avg_last_10=float(row.get("avg_last_10", 0.0) or 0.0),
                season_avg=float(row.get("season_avg", 0.0) or 0.0),
                hit_games_last_5=int(row.get("hit_games_last_5", 0) or 0),
                hit_games_last_10=int(row.get("hit_games_last_10", 0) or 0),
                matchup_rating=float(row.get("matchup_rating", 0.0) or 0.0),
                pitcher_hits_allowed_rate_last_5=float(
                    row.get("pitcher_hits_allowed_rate_last_5", 0.0) or 0.0
                ),
                pitcher_k_rate_last_5=float(row.get("pitcher_k_rate_last_5", 0.0) or 0.0),
                pitcher_walk_rate_last_5=float(
                    row.get("pitcher_walk_rate_last_5", 0.0) or 0.0
                ),
                batter_vs_pitcher_ab=row.get("batter_vs_pitcher_ab"),
                batter_vs_pitcher_avg=row.get("batter_vs_pitcher_avg"),
                discord_sim=key in discord_ranks,
                result=row.get("result", "UNKNOWN"),
                hits=row.get("hits"),
                at_bats=row.get("at_bats"),
                plate_appearances=row.get("plate_appearances"),
                game_state=row.get("game_state"),
                current_gate_qualified=bool(row.get("current_gate_qualified", True)),
                current_display_qualified=bool(
                    row.get("current_display_qualified", True)
                ),
                gate_failures=list(row.get("gate_failures") or []),
                confidence_probability=(
                    float(confidence.get("hit_probability"))
                    if confidence.get("hit_probability") is not None
                    else None
                ),
                confidence_percentage=(
                    int(confidence.get("confidence_percentage"))
                    if confidence.get("confidence_percentage") is not None
                    else None
                ),
                confidence_label=confidence.get("label"),
                confidence_reliability=(
                    float(confidence.get("reliability_weight"))
                    if confidence.get("reliability_weight") is not None
                    else None
                ),
                confidence_model_version=confidence.get("version"),
            )
        )
    return graded


def current_hot_hit_score(row: dict) -> int:
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

    result = score_hot_hit_candidate(
        HotHitScoringInput(
            avg_last_5=avg_last_5,
            avg_last_10=avg_last_10,
            season_avg=season_avg,
            hit_games_last_5=hit_games_last_5,
            hit_games_last_10=hit_games_last_10,
            batting_order=batting_order,
            matchup_rating=matchup_rating,
            pitcher_hits_allowed_rate_last_5=pitcher_hrate,
            pitcher_k_rate_last_5=pitcher_krate,
            pitcher_walk_rate_last_5=pitcher_wrate,
            pitcher_has_data=pitcher_has_data,
            batter_vs_pitcher_ab=bvp_ab,
            batter_vs_pitcher_avg=bvp_avg,
            batter_vs_pitcher_available=bvp_ab > 0 or bvp_avg is not None,
        )
    )
    return result.score


def hot_hit_tier(row: dict) -> str:
    return policy_hot_hit_tier(HotHitPolicySnapshot.from_mapping(row))


def hot_hit_discord_eligible(row: dict, min_score: int = 10) -> bool:
    return policy_hot_hit_discord_eligible(
        HotHitPolicySnapshot.from_mapping(row),
        min_score=min_score,
    )


def hot_hit_support_count(row: dict) -> int:
    return policy_hot_hit_support_count(HotHitPolicySnapshot.from_mapping(row))


def hot_hit_discord_sort_key(row: dict) -> tuple[int, int, int, float, float, float, int]:
    return policy_hot_hit_discord_sort_key(HotHitPolicySnapshot.from_mapping(row))


def simulated_discord_card(
    rows: list[dict],
    limit: int = 4,
    *,
    card_policy: str = CORE_FIRST_POLICY_VERSION,
    value_limit: int = 2,
) -> list[dict]:
    if card_policy == DELIVERED_CARD_POLICY:
        return sorted(
            [row for row in rows if row.get("_delivered_rank") is not None],
            key=lambda row: int(row["_delivered_rank"]),
        )
    production_rows = [
        row for row in rows if row.get("current_display_qualified", True)
    ]
    snapshots = [
        (HotHitPolicySnapshot.from_mapping(row), row) for row in production_rows
    ]
    rows_by_snapshot_id = {id(snapshot): row for snapshot, row in snapshots}
    policy_candidates = [snapshot for snapshot, _ in snapshots]
    if card_policy == CORE_FIRST_POLICY_VERSION:
        selection = select_core_first_hot_hits_card(
            policy_candidates,
            core_limit=limit,
            value_limit=value_limit,
            min_score=10,
        )
    else:
        selection = select_current_hot_hits_card(
            policy_candidates,
            limit=limit,
            min_score=10,
        )
    return [rows_by_snapshot_id[id(snapshot)] for snapshot in selection.shown]


def _candidate_delivery_key(candidate: dict) -> tuple[str, str]:
    batter_id = candidate.get("batter_id")
    if batter_id not in {None, ""}:
        return ("id", str(batter_id))
    return (
        "name",
        f"{candidate.get('batter_name', '')}|{candidate.get('team', '')}".casefold(),
    )


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


def render_report(
    rows: list[GradedHotHit],
    include_pending: bool = False,
    card_policy: str = CORE_FIRST_POLICY_VERSION,
) -> str:
    final_rows = [row for row in rows if row.result != "PENDING"]
    production_rows = [row for row in final_rows if row.current_display_qualified]
    discord_rows = [row for row in final_rows if row.discord_sim]
    pool_label = (
        "Confidence research pool"
        if any(row.confidence_percentage is not None for row in final_rows)
        else "Full pool"
    )
    lines = [
        "MLB Hot Hits Backtest Report",
        f"Dates: {min(row.date for row in rows)} through {max(row.date for row in rows)}",
        f"Card policy: {card_policy}",
        "",
        "Overall",
        _summary_line(pool_label, final_rows),
        _summary_line("Current production pool", production_rows),
        _summary_line("Simulated Discord", discord_rows),
        "",
        "By Day",
    ]
    for screen_date in sorted({row.date for row in rows}):
        day_rows = [row for row in rows if row.date == screen_date and row.discord_sim and row.result != "PENDING"]
        lines.append(_summary_line(screen_date, day_rows))

    lines.extend(["", "Tiers"])
    for tier_name in ("Core", "Value", "Thin"):
        lines.append(
            _summary_line(
                tier_name,
                [row for row in discord_rows if _selected_role(row) == tier_name],
            )
        )

    lines.extend(["", "Parlay Outcomes"])
    lines.extend(_parlay_outcome_lines(rows))

    confidence_rows = [
        row for row in final_rows if row.confidence_percentage is not None
    ]
    if confidence_rows:
        lines.extend(["", "Confidence Research"])
        lines.append(_confidence_summary_line("All estimates", confidence_rows))
        for label in ("STRONG", "SOLID", "CAUTIOUS", "HIGHER RISK", "NO PICK"):
            lines.append(
                _confidence_summary_line(
                    label.title(),
                    [row for row in confidence_rows if row.confidence_label == label],
                )
            )
        lines.append(
            _summary_line(
                "Current gate qualified",
                [row for row in confidence_rows if row.current_gate_qualified],
            )
        )
        lines.append(
            _summary_line(
                "Current gate excluded",
                [row for row in confidence_rows if not row.current_gate_qualified],
            )
        )
        lines.extend(["", "Confidence-Ranked Parlay Outcomes"])
        lines.extend(_confidence_parlay_outcome_lines(rows))

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
        lines.append(
            _summary_line(label, [row for row in production_rows if predicate(row)])
        )

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
        if row.current_display_qualified
        and not row.discord_sim
        and row.result == "HIT"
        and (row.rank <= 12 or row.score >= 12)
    ]
    missed_hits.sort(key=lambda row: (row.date, -row.score, row.rank))
    lines.extend(_row_lines(missed_hits, limit=25))

    excluded_confidence_hits = sorted(
        [
            row
            for row in final_rows
            if not row.current_gate_qualified
            and row.confidence_percentage is not None
            and row.result == "HIT"
        ],
        key=lambda row: (row.date, -(row.confidence_percentage or 0)),
    )
    if confidence_rows:
        lines.extend(["", "Confidence Hits Excluded By Current Gate"])
        lines.extend(_row_lines(excluded_confidence_hits, limit=25))

    pending = [row for row in rows if row.result == "PENDING"]
    if include_pending and pending:
        lines.extend(["", "Pending"])
        lines.extend(_row_lines(pending, limit=25))

    return "\n".join(lines)


def _selected_cards(rows: list[GradedHotHit]) -> list[list[GradedHotHit]]:
    cards: list[list[GradedHotHit]] = []
    for screen_date in sorted({row.date for row in rows}):
        card = sorted(
            [
                row
                for row in rows
                if row.date == screen_date and row.discord_sim
            ],
            key=lambda row: row.discord_rank or 999,
        )
        if card:
            cards.append(card)
    return cards


def _parlay_outcome_lines(rows: list[GradedHotHit]) -> list[str]:
    cards = _selected_cards(rows)
    lines = [
        "Void-adjusted: DNP legs are removed; any played miss loses; pending/unknown cards are excluded.",
    ]
    for leg_count in range(1, 5):
        top_n = [card[:leg_count] for card in cards if len(card) >= leg_count]
        lines.append(_parlay_summary_line(f"Top {leg_count}", top_n))

    core_only = [
        [row for row in card if _selected_role(row) == "Core"]
        for card in cards
        if any(_selected_role(row) == "Core" for row in card)
    ]
    core_plus_one_value = []
    core_plus_two_values = []
    value_only = []
    for card in cards:
        core = [row for row in card if _selected_role(row) == "Core"]
        value = [row for row in card if _selected_role(row) == "Value"]
        if core and value:
            core_plus_one_value.append(core + value[:1])
            core_plus_two_values.append(core + value[:2])
        elif not core and value:
            value_only.append(value)

    lines.append(_parlay_summary_line("All available Core", core_only))
    lines.append(_parlay_summary_line("Core + first Value", core_plus_one_value))
    lines.append(_parlay_summary_line("Core + up to two Value", core_plus_two_values))
    lines.append(_parlay_summary_line("Value-only fallback", value_only))
    lines.append(_parlay_summary_line("Full simulated card", cards))
    return lines


def _confidence_parlay_outcome_lines(rows: list[GradedHotHit]) -> list[str]:
    cards: list[list[GradedHotHit]] = []
    for screen_date in sorted({row.date for row in rows}):
        ranked = sorted(
            [
                row
                for row in rows
                if row.date == screen_date and row.confidence_percentage is not None
            ],
            key=lambda row: (
                row.confidence_percentage or 0,
                row.confidence_reliability or 0.0,
                -(row.batting_order or 99),
            ),
            reverse=True,
        )
        if ranked:
            cards.append(ranked)
    lines = [
        "Shadow only: ranked from the broader research pool; not the delivered card.",
    ]
    for leg_count in range(1, 5):
        top_n = [card[:leg_count] for card in cards if len(card) >= leg_count]
        lines.append(_parlay_summary_line(f"Confidence Top {leg_count}", top_n))
    return lines


def _parlay_summary_line(label: str, cards: list[list[GradedHotHit]]) -> str:
    wins = 0
    losses = 0
    no_action = 0
    pending = 0
    other = 0
    dnp_legs = 0
    total_legs = 0

    for card in cards:
        total_legs += len(card)
        dnp_legs += sum(row.result == "DNP" for row in card)
        if any(row.result == "PENDING" for row in card):
            pending += 1
            continue
        if any(row.result in {"NO_PLAYER", "NO_GAME", "UNKNOWN"} for row in card):
            other += 1
            continue
        played = [row for row in card if row.result in {"HIT", "MISS"}]
        if not played:
            no_action += 1
        elif any(row.result == "MISS" for row in played):
            losses += 1
        else:
            wins += 1

    graded = wins + losses
    rate = wins / graded if graded else 0.0
    average_legs = total_legs / len(cards) if cards else 0.0
    return (
        f"{label}: {wins}/{graded} cards ({rate:.1%})"
        f" | loss {losses} | DNP legs {dnp_legs}"
        f" | no action {no_action} | other {other} | pending {pending}"
        f" | opportunities {len(cards)} | avg shown {average_legs:.1f}"
    )


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


def _confidence_summary_line(label: str, rows: list[GradedHotHit]) -> str:
    graded = [
        row
        for row in rows
        if row.result in {"HIT", "MISS"} and row.confidence_probability is not None
    ]
    hits = sum(row.result == "HIT" for row in graded)
    hit_rate = hits / len(graded) if graded else 0.0
    mean_forecast = (
        sum(row.confidence_probability or 0.0 for row in graded) / len(graded)
        if graded
        else 0.0
    )
    brier = (
        sum(
            ((row.confidence_probability or 0.0) - (1.0 if row.result == "HIT" else 0.0))
            ** 2
            for row in graded
        )
        / len(graded)
        if graded
        else 0.0
    )
    dnp = sum(row.result == "DNP" for row in rows)
    other = len(rows) - len(graded) - dnp
    return (
        f"{label}: {hits}/{len(graded)} ({hit_rate:.1%})"
        f" | avg forecast {mean_forecast:.1%} | Brier {brier:.3f}"
        f" | DNP {dnp} | other {other}"
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
    core_count = sum(_selected_role(row) == "Core" for row in rows)
    value_count = sum(_selected_role(row) == "Value" for row in rows)
    thin_count = sum(_selected_role(row) == "Thin" for row in rows)
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


def _selected_role(row: GradedHotHit) -> str:
    return row.discord_role or row.tier


def _row_lines(rows: list[GradedHotHit], limit: int) -> list[str]:
    if not rows:
        return ["None"]
    lines = []
    for row in rows[:limit]:
        confidence = (
            f" Conf={row.confidence_percentage}%/{(row.confidence_label or '-').title()}"
            if row.confidence_percentage is not None
            else ""
        )
        gate = (
            "Gate=Pass"
            if row.current_gate_qualified
            else "Gate=" + ",".join(row.gate_failures)
        )
        lines.append(
            f"{row.date} {_selected_role(row)} rank{row.rank} score={row.score} "
            f"{row.batter_name} ({row.team}) BO={row.batting_order or '-'} "
            f"L5={row.avg_last_5:.3f} Szn={row.season_avg:.3f} "
            f"Match={row.matchup_rating:+.2f} HRate={row.pitcher_hits_allowed_rate_last_5:.3f} "
            f"{gate}{confidence} "
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
