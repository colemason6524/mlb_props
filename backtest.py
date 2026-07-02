from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean

from mlb_props.cache import JsonCache
from mlb_props.config import OUTPUTS_DIR, PITCHER_STRIKEOUTS
from mlb_props.models import PitcherGameLog
from mlb_props.sources.mlb_stats_api import (
    MlbStatsApiPitcherLogsSource,
    MlbStatsApiSlateSource,
    build_probable_pitcher_index,
)
from mlb_props.utils import normalize_name, normalize_team_abbr


@dataclass
class ResolvedPrediction:
    screen_date: date
    pitcher_name: str
    team: str
    opponent: str
    prop_type: str
    side: str
    line: float
    score: int
    edge_signal: float | None
    flags: list[str]
    actual: float
    actual_outs: int
    actual_pitches: int
    actual_batters_faced: int
    actual_walks: int
    actual_hits_allowed: int
    actual_earned_runs: int
    projected_outs: float | None
    projected_batters_faced: float | None
    projected_k_rate: float | None
    projected_strikeouts: float | None
    avg_pitch_count_last_5: float | None
    avg_outs_last_5: float | None
    avg_k_rate_last_5: float | None
    avg_walk_rate_last_5: float | None
    outcome: str
    edge: float
    projection_edge: float | None
    resolution_method: str
    miss_type: str | None


@dataclass
class VoidPrediction:
    screen_date: date
    pitcher_name: str
    team: str
    opponent: str
    prop_type: str
    side: str
    line: float
    reason: str


@dataclass
class UnresolvedPrediction:
    screen_date: date
    pitcher_name: str
    team: str
    opponent: str
    prop_type: str
    side: str
    line: float
    reason: str


@dataclass
class ResolutionReport:
    resolved: list[ResolvedPrediction]
    voided: list[VoidPrediction]
    unresolved: list[UnresolvedPrediction]
    pending: int


@dataclass
class LoadedHistory:
    predictions: list[dict]
    included_dates: list[str]
    selected_mode: str | None
    selected_path: str | None
    selected_displayed_count: int = 0
    selected_lean_count: int = 0
    selected_watch_count: int = 0
    selected_candidate_count: int = 0


def _history_files() -> list[Path]:
    history_dir = OUTPUTS_DIR / "history"
    return sorted(history_dir.glob("pitcher_props_*.json"))


def all_history_mode_enabled() -> bool:
    return "--all-history" in sys.argv


def core_only_enabled() -> bool:
    return "--core-only" in sys.argv


def leans_only_scope_enabled() -> bool:
    return "--include-leans" in sys.argv and "--include-watch" not in sys.argv


def include_leans_enabled() -> bool:
    return not core_only_enabled()


def include_watch_enabled() -> bool:
    return not core_only_enabled() and not leans_only_scope_enabled()


def prediction_scope_label() -> str:
    if core_only_enabled():
        return "Core Plays only"
    if include_watch_enabled():
        return "Core Plays + Leans + Watchlist"
    return "Core Plays + Leans"


def _candidate_rows_from_payload(payload: dict) -> list[dict]:
    displayed = payload.get("displayed_candidates")
    leans = payload.get("lean_candidates")
    watch = payload.get("watch_candidates")
    if isinstance(displayed, list):
        rows = list(displayed)
        if include_leans_enabled() or include_watch_enabled():
            if isinstance(leans, list):
                rows.extend(leans)
        if include_watch_enabled():
            if isinstance(watch, list):
                rows.extend(watch)
        return rows
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        return candidates
    result = payload.get("result") or {}
    return result.get("candidates", []) if isinstance(result, dict) else []


def _starter_id_map(payload: dict) -> dict[tuple[str, str], int | None]:
    starter_board = payload.get("starter_board", [])
    mapping: dict[tuple[str, str], int | None] = {}
    if not isinstance(starter_board, list):
        return mapping
    for item in starter_board:
        if not isinstance(item, dict):
            continue
        pitcher_name = item.get("pitcher_name")
        team = item.get("team")
        if not pitcher_name or not team:
            continue
        mapping[(normalize_team_abbr(team), normalize_name(pitcher_name))] = item.get("pitcher_id")
    return mapping


def _payload_mode_rank(mode: str | None) -> int:
    if mode == "live":
        return 2
    if mode == "sample":
        return 1
    return 0


def _load_latest_predictions() -> LoadedHistory:
    payloads: list[tuple[datetime, str, str | None, list[dict], str]] = []
    latest_counts_by_path: dict[str, tuple[int, int, int, int]] = {}
    for path in _history_files():
        payload = json.loads(path.read_text())
        screen_date = payload.get("screen_date")
        if not screen_date:
            continue
        exported_at_raw = payload.get("exported_at")
        exported_at = datetime.fromisoformat(exported_at_raw) if exported_at_raw else datetime.fromtimestamp(path.stat().st_mtime)
        data_mode = ((payload.get("settings") or {}).get("data_mode"))
        starter_ids = _starter_id_map(payload)
        candidates: list[dict] = []
        for candidate in _candidate_rows_from_payload(payload):
            if candidate.get("prop_type") != PITCHER_STRIKEOUTS:
                continue
            team = normalize_team_abbr(candidate.get("team", ""))
            pitcher_key = normalize_name(candidate.get("subject_name", ""))
            subject_id = candidate.get("subject_id")
            if subject_id is None:
                subject_id = starter_ids.get((team, pitcher_key))
            candidates.append(candidate | {"subject_id": subject_id})
        displayed_candidates = payload.get("displayed_candidates")
        lean_candidates = payload.get("lean_candidates")
        watch_candidates = payload.get("watch_candidates")
        all_candidates = payload.get("candidates")
        latest_counts_by_path[str(path)] = (
            len(displayed_candidates) if isinstance(displayed_candidates, list) else 0,
            len(lean_candidates) if isinstance(lean_candidates, list) else 0,
            len(watch_candidates) if isinstance(watch_candidates, list) else 0,
            len(all_candidates) if isinstance(all_candidates, list) else 0,
        )
        payloads.append((exported_at, screen_date, data_mode, candidates, str(path)))

    if not payloads:
        return LoadedHistory(
            predictions=[],
            included_dates=[],
            selected_mode=None,
            selected_path=None,
        )

    if all_history_mode_enabled():
        latest_by_date: dict[str, tuple[datetime, str, str | None, list[dict], str]] = {}
        for exported_at, screen_date, data_mode, candidates, payload_path in payloads:
            previous = latest_by_date.get(screen_date)
            if previous is None or (_payload_mode_rank(data_mode), exported_at) > (_payload_mode_rank(previous[2]), previous[0]):
                latest_by_date[screen_date] = (exported_at, screen_date, data_mode, candidates, payload_path)
        selected_payloads = list(latest_by_date.values())
        selected_dates = sorted(latest_by_date)
        selected_mode = "mixed"
        selected_path = None
    else:
        latest_completed_screen_date = max(
            (
                screen_date
                for _, screen_date, _, _, _ in payloads
                if date.fromisoformat(screen_date) < date.today()
            ),
            default=max(screen_date for _, screen_date, _, _, _ in payloads),
        )
        selected_payloads = [item for item in payloads if item[1] == latest_completed_screen_date]
        selected_payloads = [max(selected_payloads, key=lambda item: (_payload_mode_rank(item[2]), item[0]))]
        selected_dates = [latest_completed_screen_date]
        selected_mode = selected_payloads[0][2]
        selected_path = selected_payloads[0][4]

    loaded_predictions: list[dict] = []
    for _, screen_date, _, candidates, _ in selected_payloads:
        loaded_predictions.extend(candidate | {"screen_date": screen_date} for candidate in candidates)
    selected_displayed_count = 0
    selected_lean_count = 0
    selected_watch_count = 0
    selected_candidate_count = 0
    if selected_path is not None:
        selected_displayed_count, selected_lean_count, selected_watch_count, selected_candidate_count = latest_counts_by_path.get(
            selected_path,
            (0, 0, 0, 0),
        )
    return LoadedHistory(
        predictions=loaded_predictions,
        included_dates=selected_dates,
        selected_mode=selected_mode,
        selected_path=selected_path,
        selected_displayed_count=selected_displayed_count,
        selected_lean_count=selected_lean_count,
        selected_watch_count=selected_watch_count,
        selected_candidate_count=selected_candidate_count,
    )


def _fill_missing_subject_ids(predictions: list[dict], cache: JsonCache) -> None:
    predictions_by_date: dict[str, list[dict]] = defaultdict(list)
    for prediction in predictions:
        if prediction.get("subject_id") is None:
            predictions_by_date[prediction["screen_date"]].append(prediction)
    if not predictions_by_date:
        return

    slate_source = MlbStatsApiSlateSource(cache)
    probable_index_by_date: dict[str, dict[str, dict]] = {}
    for screen_date_str in predictions_by_date:
        try:
            games = slate_source.fetch_games(date.fromisoformat(screen_date_str))
        except Exception:
            games = []
        probable_index_by_date[screen_date_str] = build_probable_pitcher_index(games)

    for screen_date_str, rows in predictions_by_date.items():
        probable_index = probable_index_by_date.get(screen_date_str, {})
        for prediction in rows:
            entry = probable_index.get(normalize_name(prediction["subject_name"]))
            if not entry:
                continue
            entry_team = normalize_team_abbr(entry.get("team", ""))
            prediction_team = normalize_team_abbr(prediction.get("team", ""))
            if entry_team and prediction_team and entry_team != prediction_team:
                continue
            prediction["subject_id"] = entry.get("subject_id")


def _resolve_outcome(side: str, line: float, actual: float) -> tuple[str, float]:
    if actual == line:
        return "push", 0.0
    if side == "OVER":
        return ("win", actual - line) if actual > line else ("loss", actual - line)
    return ("win", line - actual) if actual < line else ("loss", line - actual)


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _classify_miss_type(prediction: dict, log: PitcherGameLog, outcome: str) -> str | None:
    if outcome != "loss":
        return None

    projected_outs = _safe_float(prediction.get("projected_outs"))
    projected_bf = _safe_float(prediction.get("projected_batters_faced"))
    projected_ks = _safe_float(prediction.get("projected_strikeouts"))
    avg_outs_last_5 = _safe_float(prediction.get("avg_outs_last_5"))
    avg_pitch_count_last_5 = _safe_float(prediction.get("avg_pitch_count_last_5"))
    avg_k_rate_last_5 = _safe_float(prediction.get("avg_k_rate_last_5"))
    avg_walk_rate_last_5 = _safe_float(prediction.get("avg_walk_rate_last_5"))

    actual_k_rate = log.strikeouts / max(log.batters_faced, 1)
    actual_walk_rate = log.walks / max(log.batters_faced, 1)

    if projected_outs is not None and log.outs_recorded >= projected_outs + 3:
        return "Opportunity beat (deeper outing)"
    if projected_outs is not None and log.outs_recorded <= projected_outs - 3:
        return "Opportunity miss (short outing)"
    if projected_bf is not None and log.batters_faced >= projected_bf + 3:
        return "Traffic spike / extra batters"
    if projected_ks is not None and log.strikeouts >= projected_ks + 1.5:
        return "Strikeout conversion beat"
    if projected_ks is not None and log.strikeouts <= projected_ks - 1.5:
        return "Strikeout conversion miss"
    if avg_outs_last_5 is not None and log.outs_recorded >= avg_outs_last_5 + 3:
        return "Deeper outing than expected"
    if avg_outs_last_5 is not None and log.outs_recorded <= avg_outs_last_5 - 3:
        return "Short outing"
    if avg_pitch_count_last_5 is not None and log.pitches_thrown >= avg_pitch_count_last_5 + 10:
        return "Pitch count spike"
    if avg_k_rate_last_5 is not None and actual_k_rate >= avg_k_rate_last_5 + 0.06:
        return "Strikeout conversion spike"
    if avg_walk_rate_last_5 is not None and actual_walk_rate >= avg_walk_rate_last_5 + 0.05:
        return "Control trouble"
    if log.earned_runs >= 4:
        return "Run prevention collapse"
    return "Mixed / situational miss"


def _latest_log_date(logs: list[PitcherGameLog]) -> date | None:
    if not logs:
        return None
    return max(log.game_date for log in logs)


def _match_log(prediction: dict, logs: list[PitcherGameLog]) -> tuple[PitcherGameLog | None, str | None, str | None]:
    screen_date = date.fromisoformat(prediction["screen_date"])
    normalized_opponent = normalize_team_abbr(prediction["opponent"])
    same_date_logs = [log for log in logs if log.game_date == screen_date]
    if not same_date_logs:
        latest_log_date = _latest_log_date(logs)
        if latest_log_date is not None and latest_log_date < screen_date:
            return None, None, "source_stale"
        if latest_log_date is not None and latest_log_date > screen_date:
            return None, None, "void_no_start"
        return None, None, "no_log_on_date"

    exact_opponent_logs = [log for log in same_date_logs if log.opponent == prediction["opponent"]]
    if len(exact_opponent_logs) == 1:
        return exact_opponent_logs[0], "exact", None
    if len(exact_opponent_logs) > 1:
        return None, None, "ambiguous_exact_opponent_match"

    normalized_opponent_logs = [
        log for log in same_date_logs if normalize_team_abbr(log.opponent) == normalized_opponent
    ]
    if len(normalized_opponent_logs) == 1:
        return normalized_opponent_logs[0], "normalized_opponent", None
    if len(normalized_opponent_logs) > 1:
        return None, None, "ambiguous_normalized_opponent_match"

    if len(same_date_logs) == 1:
        return same_date_logs[0], "date_only", None
    return None, None, "ambiguous_date_match"


def _load_logs_for_prediction(prediction: dict, logs_source: MlbStatsApiPitcherLogsSource) -> list[PitcherGameLog]:
    screen_date = date.fromisoformat(prediction["screen_date"])
    season = screen_date.year
    return logs_source.fetch_logs(
        pitcher_id=prediction.get("subject_id"),
        pitcher_name=prediction["subject_name"],
        team_hint=prediction["team"],
        season=season,
    )


def _resolve_predictions(predictions: list[dict]) -> ResolutionReport:
    # Backtests need fresh postgame data; reusing the nightly shared cache can
    # leave us stuck on pregame pitcher logs for up to 24 hours.
    run_cache_root = Path(tempfile.mkdtemp(prefix="mlb_props_backtest_"))
    fresh_cache = JsonCache(run_cache_root, ttl_hours=6)
    _fill_missing_subject_ids(predictions, fresh_cache)
    logs_source = MlbStatsApiPitcherLogsSource(fresh_cache)
    logs_cache: dict[tuple[int | None, str, str, int], list[PitcherGameLog]] = {}
    resolved: list[ResolvedPrediction] = []
    unresolved: list[UnresolvedPrediction] = []
    voided: list[VoidPrediction] = []
    pending = 0
    today = date.today()

    for prediction in predictions:
        screen_date = date.fromisoformat(prediction["screen_date"])
        if screen_date >= today:
            pending += 1
            continue
        cache_key = (
            prediction.get("subject_id"),
            prediction["subject_name"],
            prediction["team"],
            screen_date.year,
        )
        if cache_key not in logs_cache:
            logs_cache[cache_key] = _load_logs_for_prediction(prediction, logs_source)
        logs = logs_cache[cache_key]
        if not logs:
            unresolved.append(
                UnresolvedPrediction(
                    screen_date=screen_date,
                    pitcher_name=prediction["subject_name"],
                    team=prediction["team"],
                    opponent=prediction["opponent"],
                    prop_type=prediction["prop_type"],
                    side=prediction["side"],
                    line=float(prediction["line"]),
                    reason="no_logs_loaded",
                )
            )
            continue
        matching_log, resolution_method, unresolved_reason = _match_log(prediction, logs)
        if matching_log is None:
            if unresolved_reason == "void_no_start":
                voided.append(
                    VoidPrediction(
                        screen_date=screen_date,
                        pitcher_name=prediction["subject_name"],
                        team=prediction["team"],
                        opponent=prediction["opponent"],
                        prop_type=prediction["prop_type"],
                        side=prediction["side"],
                        line=float(prediction["line"]),
                        reason="void_no_start",
                    )
                )
            else:
                unresolved.append(
                    UnresolvedPrediction(
                        screen_date=screen_date,
                        pitcher_name=prediction["subject_name"],
                        team=prediction["team"],
                        opponent=prediction["opponent"],
                        prop_type=prediction["prop_type"],
                        side=prediction["side"],
                        line=float(prediction["line"]),
                        reason=unresolved_reason or "unmatched",
                    )
                )
            continue
        actual = float(matching_log.strikeouts)
        outcome, edge = _resolve_outcome(prediction["side"], float(prediction["line"]), actual)
        resolved.append(
            ResolvedPrediction(
                screen_date=screen_date,
                pitcher_name=prediction["subject_name"],
                team=prediction["team"],
                opponent=prediction["opponent"],
                prop_type=prediction["prop_type"],
                side=prediction["side"],
                line=float(prediction["line"]),
                score=int(prediction["score"]),
                edge_signal=prediction.get("delta_avg_last_5"),
                flags=list(prediction.get("flags", [])),
                actual=actual,
                actual_outs=matching_log.outs_recorded,
                actual_pitches=matching_log.pitches_thrown,
                actual_batters_faced=matching_log.batters_faced,
                actual_walks=matching_log.walks,
                actual_hits_allowed=matching_log.hits_allowed,
                actual_earned_runs=matching_log.earned_runs,
                projected_outs=_safe_float(prediction.get("projected_outs")),
                projected_batters_faced=_safe_float(prediction.get("projected_batters_faced")),
                projected_k_rate=_safe_float(prediction.get("projected_k_rate")),
                projected_strikeouts=_safe_float(prediction.get("projected_strikeouts")),
                avg_pitch_count_last_5=_safe_float(prediction.get("avg_pitch_count_last_5")),
                avg_outs_last_5=_safe_float(prediction.get("avg_outs_last_5")),
                avg_k_rate_last_5=_safe_float(prediction.get("avg_k_rate_last_5")),
                avg_walk_rate_last_5=_safe_float(prediction.get("avg_walk_rate_last_5")),
                outcome=outcome,
                edge=edge,
                projection_edge=(
                    _safe_float(prediction.get("projected_strikeouts")) - float(prediction["line"])
                    if _safe_float(prediction.get("projected_strikeouts")) is not None
                    else None
                ),
                resolution_method=resolution_method or "exact",
                miss_type=_classify_miss_type(prediction, matching_log, outcome),
            )
        )

    return ResolutionReport(
        resolved=resolved,
        voided=voided,
        unresolved=unresolved,
        pending=pending,
    )


def _score_band(score: int) -> str:
    if score >= 11:
        return "11+"
    if score <= -5:
        return "-5 or worse"
    return str(score)


def _edge_band(signal: float | None) -> str:
    if signal is None:
        return "unknown"
    value = abs(signal)
    if value >= 2.0:
        return "2.0+"
    if value >= 1.0:
        return "1.0-1.99"
    if value >= 0.5:
        return "0.5-0.99"
    return "<0.5"


def _hit_rate(rows: list[ResolvedPrediction]) -> float:
    graded = [row for row in rows if row.outcome != "push"]
    if not graded:
        return 0.0
    wins = sum(1 for row in graded if row.outcome == "win")
    return wins / len(graded)


def _render_score_bands(rows: list[ResolvedPrediction]) -> list[str]:
    bands: dict[str, list[ResolvedPrediction]] = defaultdict(list)
    for row in rows:
        bands[_score_band(row.score)].append(row)
    ordered_labels = [label for label in ["-5 or worse", "-4", "-3", "-2", "-1", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11+"] if label in bands]
    lines = ["Score bands:"]
    for label in ordered_labels:
        band_rows = bands[label]
        pushes = sum(1 for row in band_rows if row.outcome == "push")
        avg_edge = mean(row.edge for row in band_rows) if band_rows else 0.0
        lines.append(f"- {label}: {len(band_rows)} plays, {(_hit_rate(band_rows) * 100):.1f}% hit, {pushes} pushes, avg edge {avg_edge:+.2f}")
    return lines


def _render_edge_bands(rows: list[ResolvedPrediction]) -> list[str]:
    bands: dict[str, list[ResolvedPrediction]] = defaultdict(list)
    for row in rows:
        bands[_edge_band(row.edge_signal)].append(row)
    order = ["<0.5", "0.5-0.99", "1.0-1.99", "2.0+", "unknown"]
    lines = ["Signal edge bands:"]
    for label in order:
        if label not in bands:
            continue
        band_rows = bands[label]
        pushes = sum(1 for row in band_rows if row.outcome == "push")
        avg_result_edge = mean(row.edge for row in band_rows) if band_rows else 0.0
        lines.append(f"- {label}: {len(band_rows)} plays, {(_hit_rate(band_rows) * 100):.1f}% hit, {pushes} pushes, avg result edge {avg_result_edge:+.2f}")
    return lines


def _render_side_performance(rows: list[ResolvedPrediction]) -> list[str]:
    buckets: dict[str, list[ResolvedPrediction]] = defaultdict(list)
    for row in rows:
        buckets[row.side].append(row)
    lines = ["Side performance:"]
    for side in ("OVER", "UNDER"):
        if side not in buckets:
            continue
        side_rows = buckets[side]
        pushes = sum(1 for row in side_rows if row.outcome == "push")
        avg_edge = mean(row.edge for row in side_rows) if side_rows else 0.0
        lines.append(f"- {side}: {len(side_rows)} plays, {(_hit_rate(side_rows) * 100):.1f}% hit, {pushes} pushes, avg edge {avg_edge:+.2f}")
    return lines


def _render_flag_performance(rows: list[ResolvedPrediction], min_samples: int = 5) -> list[str]:
    flags_to_rows: dict[str, list[ResolvedPrediction]] = defaultdict(list)
    for row in rows:
        for flag in row.flags:
            flags_to_rows[flag].append(row)
    ranked = sorted(
        ((flag, flag_rows) for flag, flag_rows in flags_to_rows.items() if len(flag_rows) >= min_samples),
        key=lambda item: (-_hit_rate(item[1]), -len(item[1]), item[0]),
    )
    lines = [f"Flag performance (min {min_samples} samples):"]
    if not ranked:
        lines.append("- No flags met the sample threshold yet.")
        return lines
    for flag, flag_rows in ranked[:15]:
        pushes = sum(1 for row in flag_rows if row.outcome == "push")
        avg_edge = mean(row.edge for row in flag_rows) if flag_rows else 0.0
        lines.append(f"- {flag}: {len(flag_rows)} plays, {(_hit_rate(flag_rows) * 100):.1f}% hit, {pushes} pushes, avg edge {avg_edge:+.2f}")
    return lines


def _render_resolution_methods(rows: list[ResolvedPrediction]) -> list[str]:
    methods = Counter(row.resolution_method for row in rows)
    lines = ["Resolution methods:"]
    for method, count in sorted(methods.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {method}: {count}")
    return lines


def _render_postgame_diagnostics(rows: list[ResolvedPrediction]) -> list[str]:
    lines = ["Postgame diagnostics:"]
    if not rows:
        lines.append("- No resolved predictions.")
        return lines

    lines.append(f"- Avg actual outs: {mean(row.actual_outs for row in rows):.1f}")
    lines.append(f"- Avg actual pitches: {mean(row.actual_pitches for row in rows):.1f}")
    lines.append(f"- Avg actual batters faced: {mean(row.actual_batters_faced for row in rows):.1f}")
    lines.append(f"- Avg actual walks: {mean(row.actual_walks for row in rows):.1f}")
    lines.append(f"- Avg actual hits allowed: {mean(row.actual_hits_allowed for row in rows):.1f}")
    lines.append(f"- Avg actual earned runs: {mean(row.actual_earned_runs for row in rows):.1f}")

    losses = [row for row in rows if row.outcome == "loss"]
    if not losses:
        lines.append("- No losses to classify.")
        return lines

    miss_types = Counter(row.miss_type or "Unclassified" for row in losses)
    rendered = ", ".join(f"{miss_type} {count}" for miss_type, count in miss_types.most_common())
    lines.append(f"- Loss types: {rendered}")
    return lines


def _render_projection_diagnostics(rows: list[ResolvedPrediction]) -> list[str]:
    lines = ["Projection diagnostics:"]
    if not rows:
        lines.append("- No resolved predictions.")
        return lines

    rows_with_outs = [row for row in rows if row.projected_outs is not None]
    rows_with_bf = [row for row in rows if row.projected_batters_faced is not None]
    rows_with_ks = [row for row in rows if row.projected_strikeouts is not None]

    if rows_with_outs:
        avg_outs_error = mean(row.actual_outs - row.projected_outs for row in rows_with_outs)
        lines.append(f"- Avg actual-minus-projected outs: {avg_outs_error:+.2f}")
    if rows_with_bf:
        avg_bf_error = mean(row.actual_batters_faced - row.projected_batters_faced for row in rows_with_bf)
        lines.append(f"- Avg actual-minus-projected BF: {avg_bf_error:+.2f}")
    if rows_with_ks:
        avg_ks_error = mean(row.actual - row.projected_strikeouts for row in rows_with_ks)
        lines.append(f"- Avg actual-minus-projected Ks: {avg_ks_error:+.2f}")

    losses = [row for row in rows if row.outcome == "loss" and row.projected_strikeouts is not None]
    if not losses:
        lines.append("- No projection-tagged losses yet.")
        return lines

    biggest_outs = max(losses, key=lambda row: abs((row.actual_outs - row.projected_outs) if row.projected_outs is not None else 0.0))
    biggest_ks = max(losses, key=lambda row: abs(row.actual - row.projected_strikeouts) if row.projected_strikeouts is not None else 0.0)
    if biggest_outs.projected_outs is not None:
        lines.append(
            f"- Largest outs miss: {biggest_outs.pitcher_name} {biggest_outs.actual_outs - biggest_outs.projected_outs:+.1f} "
            f"(proj {biggest_outs.projected_outs:.1f}, actual {biggest_outs.actual_outs})"
        )
    if biggest_ks.projected_strikeouts is not None:
        lines.append(
            f"- Largest Ks miss: {biggest_ks.pitcher_name} {biggest_ks.actual - biggest_ks.projected_strikeouts:+.1f} "
            f"(proj {biggest_ks.projected_strikeouts:.1f}, actual {biggest_ks.actual:.1f})"
        )
    return lines


def _render_loss_review(rows: list[ResolvedPrediction], max_rows: int = 12) -> list[str]:
    losses = [row for row in rows if row.outcome == "loss"]
    lines = ["Loss review:"]
    if not losses:
        lines.append("- No losses on this slate.")
        return lines

    lines.append(f"- Losses: {len(losses)}")
    side_counts = Counter(row.side for row in losses)
    score_counts = Counter(_score_band(row.score) for row in losses)
    edge_counts = Counter(_edge_band(row.edge_signal) for row in losses)
    flag_counts = Counter(flag for row in losses for flag in row.flags)

    if side_counts:
        rendered = ", ".join(f"{side} {count}" for side, count in sorted(side_counts.items()))
        lines.append(f"- Side split: {rendered}")
    if score_counts:
        rendered = ", ".join(
            f"{label} {score_counts[label]}"
            for label in ["-5 or worse", "-4", "-3", "-2", "-1", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11+"]
            if label in score_counts
        )
        lines.append(f"- Score bands: {rendered}")
    if edge_counts:
        rendered = ", ".join(
            f"{label} {edge_counts[label]}"
            for label in ["<0.5", "0.5-0.99", "1.0-1.99", "2.0+", "unknown"]
            if label in edge_counts
        )
        lines.append(f"- Signal edge bands: {rendered}")
    top_flags = [f"{flag} {count}" for flag, count in flag_counts.most_common(5)]
    if top_flags:
        lines.append(f"- Most common loss flags: {', '.join(top_flags)}")
    miss_type_counts = Counter(row.miss_type or "Unclassified" for row in losses)
    if miss_type_counts:
        rendered = ", ".join(f"{label} {count}" for label, count in miss_type_counts.most_common())
        lines.append(f"- Miss types: {rendered}")

    lines.append("- Misses:")
    ordered_losses = sorted(
        losses,
        key=lambda row: (
            -abs(row.score),
            -(abs(row.edge_signal) if row.edge_signal is not None else -1.0),
            row.pitcher_name,
        ),
        reverse=False,
    )
    for row in ordered_losses[:max_rows]:
        signal = "?" if row.edge_signal is None else f"{row.edge_signal:+.2f}"
        flags = ",".join(row.flags[:5]) if row.flags else "-"
        proj_ks = "-" if row.projected_strikeouts is None else f"{row.projected_strikeouts:.1f}"
        proj_outs = "-" if row.projected_outs is None else f"{row.projected_outs:.1f}"
        proj_bf = "-" if row.projected_batters_faced is None else f"{row.projected_batters_faced:.1f}"
        lines.append(
            f"  {row.pitcher_name} ({row.team} vs {row.opponent}) "
            f"{row.side} {row.line:g} | proj ks {proj_ks} | proj outs {proj_outs} | proj bf {proj_bf} | actual {row.actual:g} | outs {row.actual_outs} | pitches {row.actual_pitches} | "
            f"bf {row.actual_batters_faced} | bb {row.actual_walks} | h {row.actual_hits_allowed} | er {row.actual_earned_runs} | "
            f"score {row.score} | signal {signal} | result edge {row.edge:+.2f} | miss {row.miss_type or '-'} | flags {flags}"
        )
    if len(ordered_losses) > max_rows:
        lines.append(f"  ... {len(ordered_losses) - max_rows} more losses omitted")
    return lines


def _render_unresolved_breakdown(rows: list[UnresolvedPrediction], max_examples: int = 8) -> list[str]:
    lines = ["Unresolved breakdown:"]
    if not rows:
        lines.append("- No unresolved finished predictions.")
        return lines
    by_reason = Counter(row.reason for row in rows)
    for reason, count in sorted(by_reason.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"- {reason}: {count}")
    lines.append("- Examples:")
    for row in rows[:max_examples]:
        lines.append(f"  {row.pitcher_name} ({row.team} vs {row.opponent}) {row.side} {row.line:g} [{row.reason}]")
    return lines


def _export_backtest_report(screen_date: str, report_text: str, mode: str = "single") -> Path:
    backtests_dir = OUTPUTS_DIR / "backtests"
    backtests_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if mode == "single" else "_all_history"
    path = backtests_dir / f"backtest_{screen_date}{suffix}.txt"
    path.write_text(report_text)
    return path


def main() -> int:
    loaded = _load_latest_predictions()
    predictions = loaded.predictions
    included_dates = loaded.included_dates
    if not predictions:
        history_files = _history_files()
        if not history_files:
            print("No screen run history found in outputs/history yet.")
            print("Run EXPORT_HISTORY=true python3 run_nightly.py first to generate MLB backtest snapshots.")
            return 0

        print("No backtestable strikeout predictions were found in the selected history snapshot.")
        print(f"- Prediction scope: {prediction_scope_label()}")
        if loaded.selected_mode:
            print(f"- Selected history mode: {loaded.selected_mode}")
        if loaded.selected_path:
            print(f"- Selected history file: {loaded.selected_path}")
        print(f"- Displayed core plays in snapshot: {loaded.selected_displayed_count}")
        print(f"- Lean plays in snapshot: {loaded.selected_lean_count}")
        print(f"- Watchlist plays in snapshot: {loaded.selected_watch_count}")
        print(f"- Qualified candidates before score filtering: {loaded.selected_candidate_count}")
        if not include_leans_enabled() and loaded.selected_lean_count > 0:
            print("- Tip: run python3 backtest.py --include-leans to grade the broader displayed board.")
        elif not include_watch_enabled() and loaded.selected_watch_count > 0:
            print("- Tip: run python3 backtest.py --include-watch to grade core, leans, and watchlist.")
        elif loaded.selected_candidate_count > 0:
            print("- Note: this slate had qualified candidates, but none made the displayed board for the selected prediction scope.")
        return 0

    report = _resolve_predictions(predictions)
    resolved = report.resolved
    if not resolved and not report.voided:
        print("No finished strikeout predictions could be resolved yet.")
        print(f"- Stored predictions found: {len(predictions)}")
        print(f"- Pending/future predictions skipped: {report.pending}")
        print(f"- Unresolved finished predictions: {len(report.unresolved)}")
        if loaded.selected_mode:
            print(f"- Selected history mode: {loaded.selected_mode}")
        if loaded.selected_path:
            print(f"- Selected history file: {loaded.selected_path}")
        if loaded.selected_mode != "live":
            print("- Note: the latest completed snapshot is not a live run. Export a live slate with EXPORT_HISTORY=true DATA_MODE=live python3 run_nightly.py for a real reconciliation.")
        return 0

    pushes = sum(1 for row in resolved if row.outcome == "push")
    lines: list[str] = []
    lines.append("Backtest summary:")
    if all_history_mode_enabled():
        lines.append(f"- Screen dates included: {included_dates[0]} to {included_dates[-1]} ({len(included_dates)} slates)")
    else:
        lines.append(f"- Screen date included: {included_dates[0]}")
    if loaded.selected_mode and not all_history_mode_enabled():
        lines.append(f"- History mode used: {loaded.selected_mode}")
    lines.append(f"- Prediction scope: {prediction_scope_label()}")
    lines.append("- Prop type included: PITCHER_STRIKEOUTS")
    lines.append(f"- Latest unique predictions loaded: {len(predictions)}")
    lines.append(f"- Finished predictions graded: {len(resolved)}")
    lines.append(f"- Void predictions: {len(report.voided)}")
    lines.append(f"- Unresolved finished predictions: {len(report.unresolved)}")
    lines.append(f"- Pending/future predictions skipped: {report.pending}")
    if resolved:
        lines.append(f"- Overall hit rate: {(_hit_rate(resolved) * 100):.1f}%")
        lines.append(f"- Pushes: {pushes}")
        lines.append(f"- Average result edge vs line: {mean(row.edge for row in resolved):+.2f}")
    lines.append("")
    if resolved:
        lines.extend(_render_score_bands(resolved))
        lines.append("")
        lines.extend(_render_edge_bands(resolved))
        lines.append("")
        lines.extend(_render_side_performance(resolved))
        lines.append("")
        lines.extend(_render_flag_performance(resolved))
        lines.append("")
        lines.extend(_render_projection_diagnostics(resolved))
        lines.append("")
        lines.extend(_render_postgame_diagnostics(resolved))
        lines.append("")
        lines.extend(_render_resolution_methods(resolved))
        lines.append("")
        lines.extend(_render_loss_review(resolved))
        lines.append("")
    lines.extend(_render_unresolved_breakdown(report.unresolved))

    report_text = "\n".join(lines)
    print(report_text)
    export_path = _export_backtest_report(
        screen_date=included_dates[0] if len(included_dates) == 1 else f"{included_dates[0]}_to_{included_dates[-1]}",
        report_text=report_text + "\n",
        mode="all_history" if all_history_mode_enabled() else "single",
    )
    print(f"\n- Backtest report exported to {export_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
