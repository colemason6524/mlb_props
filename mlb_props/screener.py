from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from math import ceil
from statistics import mean, median, pstdev
from types import SimpleNamespace

from .config import PITCHER_OUTS_RECORDED, PITCHER_STRIKEOUTS, Settings
from .models import Candidate, MatchupContext, PitcherGameLog, PropLine, ScreeningResult, StarterAssessment
from .tiers import core_block_reasons
from .utils import normalize_name


@dataclass(frozen=True)
class PropDefinition:
    prop_type: str
    extractor: Callable[[PitcherGameLog], float]
    volatility_warning: float
    display_name: str


PROP_DEFINITIONS: dict[str, PropDefinition] = {
    PITCHER_STRIKEOUTS: PropDefinition(
        prop_type=PITCHER_STRIKEOUTS,
        extractor=lambda log: float(log.strikeouts),
        volatility_warning=2.2,
        display_name="Strikeouts",
    ),
    PITCHER_OUTS_RECORDED: PropDefinition(
        prop_type=PITCHER_OUTS_RECORDED,
        extractor=lambda log: float(log.outs_recorded),
        volatility_warning=4.5,
        display_name="Outs Recorded",
    ),
}


def screen_pitcher_props(
    settings: Settings,
    prop_lines: Iterable[PropLine],
    logs_by_pitcher: dict[str, list[PitcherGameLog]],
    matchup_contexts: dict[tuple[str, str], MatchupContext],
) -> ScreeningResult:
    candidates: list[Candidate] = []
    evaluated_prop_lines = 0
    non_qualifying_prop_lines = 0

    for line in prop_lines:
        prop_definition = PROP_DEFINITIONS.get(line.prop_type)
        if prop_definition is None:
            continue

        pitcher_key = normalize_name(line.subject_name_raw)
        logs = [log for log in logs_by_pitcher.get(pitcher_key, []) if log.did_start]
        evaluated_prop_lines += 1
        if len(logs) < settings.thresholds.min_starts:
            non_qualifying_prop_lines += 1
            continue

        last_5 = logs[: settings.thresholds.recent_window]
        last_10 = logs[: settings.thresholds.form_window]
        values_last_5 = [prop_definition.extractor(log) for log in last_5]
        values_last_10 = [prop_definition.extractor(log) for log in last_10]
        all_values = [prop_definition.extractor(log) for log in logs]

        avg_last_5 = mean(values_last_5)
        avg_last_10 = mean(values_last_10)
        median_last_5 = median(values_last_5)
        median_last_10 = median(values_last_10)
        season_avg = mean(all_values)
        delta_avg_last_5 = avg_last_5 - line.line
        delta_avg_last_10 = avg_last_10 - line.line
        delta_season_avg = season_avg - line.line
        avg_pitch_count_last_5 = mean(log.pitches_thrown for log in last_5)
        avg_pitch_count_last_10 = mean(log.pitches_thrown for log in last_10)
        avg_outs_last_5 = mean(log.outs_recorded for log in last_5)
        avg_outs_last_10 = mean(log.outs_recorded for log in last_10)
        avg_k_rate_last_5 = mean(log.strikeouts / max(log.batters_faced, 1) for log in last_5)
        avg_walk_rate_last_5 = mean(log.walks / max(log.batters_faced, 1) for log in last_5)
        avg_earned_runs_last_5 = mean(log.earned_runs for log in last_5)
        quality_starts_last_10 = sum(1 for log in last_10 if log.outs_recorded >= 18 and log.earned_runs <= 3)
        short_starts_last_10 = sum(1 for log in last_10 if log.outs_recorded < 15)
        workload_stability = _workload_stability(last_5, prop_definition)
        matchup_context = matchup_contexts.get((line.team, line.opponent))
        matchup_rating = _matchup_rating(line.prop_type, matchup_context)
        projected_outs, projected_batters_faced = _project_opportunity(
            logs=logs,
            matchup_context=matchup_context,
            avg_outs_last_5=avg_outs_last_5,
            avg_outs_season=mean(log.outs_recorded for log in logs),
            avg_pitch_count_last_5=avg_pitch_count_last_5,
            avg_walk_rate_last_5=avg_walk_rate_last_5,
            avg_earned_runs_last_5=avg_earned_runs_last_5,
            quality_starts_recent=sum(1 for log in last_5 if log.outs_recorded >= 18 and log.earned_runs <= 3),
            short_starts_recent=sum(1 for log in last_5 if log.outs_recorded < 15),
            outs_stability=_workload_stability(last_5, PROP_DEFINITIONS[PITCHER_OUTS_RECORDED]),
        )
        projected_k_rate = _project_k_rate(
            logs=logs,
            matchup_context=matchup_context,
            avg_k_rate_last_5=avg_k_rate_last_5,
            avg_walk_rate_last_5=avg_walk_rate_last_5,
        )
        projected_strikeouts = round(projected_batters_faced * projected_k_rate, 1)

        line_candidates = _build_candidates(
            settings=settings,
            line=line,
            logs=logs,
            prop_definition=prop_definition,
            avg_last_5=avg_last_5,
            avg_last_10=avg_last_10,
            median_last_5=median_last_5,
            median_last_10=median_last_10,
            season_avg=season_avg,
            delta_avg_last_5=delta_avg_last_5,
            delta_avg_last_10=delta_avg_last_10,
            delta_season_avg=delta_season_avg,
            avg_pitch_count_last_5=avg_pitch_count_last_5,
            avg_pitch_count_last_10=avg_pitch_count_last_10,
            avg_outs_last_5=avg_outs_last_5,
            avg_outs_last_10=avg_outs_last_10,
            avg_k_rate_last_5=avg_k_rate_last_5,
            avg_walk_rate_last_5=avg_walk_rate_last_5,
            avg_earned_runs_last_5=avg_earned_runs_last_5,
            quality_starts_last_10=quality_starts_last_10,
            short_starts_last_10=short_starts_last_10,
            workload_stability=workload_stability,
            matchup_context=matchup_context,
            matchup_rating=matchup_rating,
            values_last_5=values_last_5,
            values_last_10=values_last_10,
            projected_outs=projected_outs,
            projected_batters_faced=projected_batters_faced,
            projected_k_rate=projected_k_rate,
            projected_strikeouts=projected_strikeouts,
        )
        if line_candidates:
            candidates.extend(line_candidates)
        else:
            non_qualifying_prop_lines += 1

    return ScreeningResult(
        candidates=sorted(
            candidates,
            key=lambda item: (item.score, item.hits_last_5, item.delta_avg_last_5, item.hits_last_10),
            reverse=True,
        ),
        evaluated_prop_lines=evaluated_prop_lines,
        non_qualifying_prop_lines=non_qualifying_prop_lines,
    )


def _build_candidates(
    settings: Settings,
    line: PropLine,
    logs: list[PitcherGameLog],
    prop_definition: PropDefinition,
    avg_last_5: float,
    avg_last_10: float,
    median_last_5: float,
    median_last_10: float,
    season_avg: float,
    delta_avg_last_5: float,
    delta_avg_last_10: float,
    delta_season_avg: float,
    avg_pitch_count_last_5: float,
    avg_pitch_count_last_10: float,
    avg_outs_last_5: float,
    avg_outs_last_10: float,
    avg_k_rate_last_5: float,
    avg_walk_rate_last_5: float,
    avg_earned_runs_last_5: float,
    quality_starts_last_10: int,
    short_starts_last_10: int,
    workload_stability: float,
    matchup_context: MatchupContext | None,
    matchup_rating: float,
    values_last_5: list[float],
    values_last_10: list[float],
    projected_outs: float,
    projected_batters_faced: float,
    projected_k_rate: float,
    projected_strikeouts: float,
) -> list[Candidate]:
    effective_delta_last_5 = delta_avg_last_5
    effective_delta_last_10 = delta_avg_last_10
    effective_delta_season = delta_season_avg
    if line.prop_type == PITCHER_STRIKEOUTS:
        projection_edge = projected_strikeouts - line.line
        effective_delta_last_5 = (delta_avg_last_5 * 0.35) + (projection_edge * 0.65)
        effective_delta_last_10 = (delta_avg_last_10 * 0.5) + (projection_edge * 0.5)
        effective_delta_season = (delta_season_avg * 0.5) + (projection_edge * 0.5)

    side_configs = [
        (
            "OVER",
            sum(1 for value in values_last_5 if value > line.line),
            sum(1 for value in values_last_10 if value > line.line),
            effective_delta_last_5,
            effective_delta_last_10,
            effective_delta_season,
        )
    ]
    if settings.include_under_candidates:
        side_configs.append(
            (
                "UNDER",
                sum(1 for value in values_last_5 if value < line.line),
                sum(1 for value in values_last_10 if value < line.line),
                -effective_delta_last_5,
                -effective_delta_last_10,
                -effective_delta_season,
            )
        )

    candidates: list[Candidate] = []
    for side, hits_last_5, hits_last_10, side_delta_last_5, side_delta_last_10, side_delta_season in side_configs:
        if not _passes_projection_rule(
            settings,
            side_delta_last_5,
            side_delta_last_10,
            side_delta_season,
        ):
            continue
        score, flags = _score_candidate(
            settings=settings,
            prop_type=line.prop_type,
            side=side,
            hits_last_5=hits_last_5,
            played_last_5=len(values_last_5),
            hits_last_10=hits_last_10,
            played_last_10=len(values_last_10),
            side_delta_last_5=side_delta_last_5,
            side_delta_last_10=side_delta_last_10,
            side_delta_season=side_delta_season,
            avg_pitch_count_last_5=avg_pitch_count_last_5,
            avg_outs_last_5=avg_outs_last_5,
            avg_k_rate_last_5=avg_k_rate_last_5,
            avg_walk_rate_last_5=avg_walk_rate_last_5,
            avg_earned_runs_last_5=avg_earned_runs_last_5,
            quality_starts_last_10=quality_starts_last_10,
            short_starts_last_10=short_starts_last_10,
            workload_stability=workload_stability,
            projected_outs=projected_outs,
            projected_batters_faced=projected_batters_faced,
            projected_edge=max(side_delta_last_5, side_delta_last_10, side_delta_season),
            matchup_context=matchup_context,
            matchup_rating=matchup_rating,
        )
        candidates.append(
            Candidate(
                subject_name=line.subject_name_raw,
                subject_id=line.subject_id,
                subject_role=line.subject_role,
                team=line.team,
                opponent=line.opponent,
                hand=line.hand,
                prop_type=line.prop_type,
                side=side,
                line=line.line,
                bookmaker=line.bookmaker,
                hits_last_5=hits_last_5,
                played_last_5=len(values_last_5),
                hits_last_10=hits_last_10,
                played_last_10=len(values_last_10),
                avg_last_5=avg_last_5,
                avg_last_10=avg_last_10,
                median_last_5=median_last_5,
                median_last_10=median_last_10,
                season_avg=season_avg,
                delta_avg_last_5=side_delta_last_5,
                delta_avg_last_10=side_delta_last_10,
                avg_pitch_count_last_5=avg_pitch_count_last_5,
                avg_pitch_count_last_10=avg_pitch_count_last_10,
                avg_outs_last_5=avg_outs_last_5,
                avg_outs_last_10=avg_outs_last_10,
                avg_k_rate_last_5=avg_k_rate_last_5,
                avg_walk_rate_last_5=avg_walk_rate_last_5,
                avg_earned_runs_last_5=avg_earned_runs_last_5,
                quality_starts_last_10=quality_starts_last_10,
                short_starts_last_10=short_starts_last_10,
                workload_stability=workload_stability,
                matchup_rating=matchup_rating,
                projected_outs=projected_outs,
                projected_batters_faced=projected_batters_faced,
                projected_k_rate=projected_k_rate,
                projected_strikeouts=projected_strikeouts,
                score=score,
                flags=flags,
                opponent_k_rate_vs_hand=getattr(matchup_context, "opponent_k_rate_vs_hand", None),
                opponent_outs_factor=getattr(matchup_context, "opponent_outs_factor", None),
                park_run_factor=getattr(matchup_context, "park_run_factor", None),
                moneyline=getattr(matchup_context, "moneyline", None),
            )
        )
    return candidates


def _required_support_hits(settings: Settings, played_last_10: int) -> int:
    if played_last_10 <= 0:
        return settings.thresholds.support_hits_last_10
    if played_last_10 <= 5:
        return max(3, ceil(played_last_10 * 0.7))
    return settings.thresholds.support_hits_last_10


def _projection_floor(settings: Settings) -> float:
    return max(0.4, settings.thresholds.min_delta - 0.2)


def _passes_projection_rule(
    settings: Settings,
    side_delta_last_5: float,
    side_delta_last_10: float,
    side_delta_season: float,
) -> bool:
    return max(side_delta_last_5, side_delta_last_10, side_delta_season) >= _projection_floor(settings)


def _score_candidate(
    settings: Settings,
    prop_type: str,
    side: str,
    hits_last_5: int,
    played_last_5: int,
    hits_last_10: int,
    played_last_10: int,
    side_delta_last_5: float,
    side_delta_last_10: float,
    side_delta_season: float,
    avg_pitch_count_last_5: float,
    avg_outs_last_5: float,
    avg_k_rate_last_5: float,
    avg_walk_rate_last_5: float,
    avg_earned_runs_last_5: float,
    quality_starts_last_10: int,
    short_starts_last_10: int,
    workload_stability: float,
    projected_outs: float,
    projected_batters_faced: float,
    projected_edge: float,
    matchup_context: MatchupContext | None,
    matchup_rating: float,
) -> tuple[int, list[str]]:
    score = 0
    flags: list[str] = []

    score += _recent_confidence_adjustment(
        hits_last_5=hits_last_5,
        played_last_5=played_last_5,
        hits_last_10=hits_last_10,
        played_last_10=played_last_10,
        flags=flags,
    )
    score += _projection_confidence_adjustment(
        prop_type=prop_type,
        side=side,
        projected_edge=projected_edge,
        projected_outs=projected_outs,
        projected_batters_faced=projected_batters_faced,
        flags=flags,
    )
    score += _matchup_confidence_adjustment(
        prop_type=prop_type,
        side=side,
        matchup_context=matchup_context,
        flags=flags,
    )

    if side_delta_season >= settings.thresholds.strong_delta:
        score += 3
        flags.append("SEASON_PLUS")
    elif side_delta_season >= settings.thresholds.min_delta:
        score += 2

    if side_delta_last_5 >= settings.thresholds.strong_delta:
        score += 2
        flags.append("TREND_PLUS")
    elif side_delta_last_5 >= settings.thresholds.min_delta:
        score += 1

    if side_delta_last_10 >= settings.thresholds.min_delta:
        score += 1

    if avg_pitch_count_last_5 >= settings.thresholds.strong_pitch_count:
        score += 2
        flags.append("WORKLOAD_PLUS")
    elif avg_pitch_count_last_5 <= settings.thresholds.low_pitch_count:
        score -= 2
        flags.append("LOW_PITCH")

    if prop_type == PITCHER_OUTS_RECORDED and avg_outs_last_5 < 16.0:
        score -= 2
        flags.append("SHORT_LEASH")

    if avg_k_rate_last_5 >= 0.28:
        score += 1
        flags.append("K_EFF")
    elif prop_type == PITCHER_STRIKEOUTS and avg_k_rate_last_5 <= 0.21:
        score -= 2
        flags.append("K_EFF_LOW")

    if avg_walk_rate_last_5 >= 0.09:
        score -= 2
        flags.append("CONTROL_RISK")
    elif avg_walk_rate_last_5 <= 0.06:
        score += 1

    if avg_earned_runs_last_5 >= 3.2:
        score -= 1
        flags.append("RUN_RISK")
    elif avg_earned_runs_last_5 <= 2.0:
        score += 1

    if quality_starts_last_10 >= 6:
        score += 1
        flags.append("QS_PLUS")

    if short_starts_last_10 >= 2:
        score -= 2
        flags.append("SHORT_START_RISK")

    if workload_stability >= 0.72:
        score += 2
    elif workload_stability <= 0.42:
        score -= 2
        flags.append("VOLATILE")

    if matchup_rating >= 0.2:
        score += 2
        flags.append("GOOD_MATCHUP")
    elif matchup_rating <= -0.2:
        score -= 2
        flags.append("TOUGH_MATCHUP")

    if matchup_context is not None:
        if matchup_context.opponent_walk_rate_vs_hand >= 0.09:
            if side == "UNDER":
                score += 1
            else:
                score -= 1
            flags.append("PATIENT_OPP")
        elif matchup_context.opponent_walk_rate_vs_hand <= 0.075:
            if side == "UNDER":
                score -= 1
            else:
                score += 1
            flags.append("FREE_SWING_OPP")

        if matchup_context.moneyline <= -140:
            score += 1
            flags.append("FAVORITE")
        elif matchup_context.moneyline >= 130:
            score -= 1
            flags.append("DOG")

        if matchup_context.park_run_factor >= 1.06:
            if side == "UNDER":
                score += 1
            else:
                score -= 1
            flags.append("PARK_HITTER")
        elif matchup_context.park_run_factor <= 0.97:
            if side == "UNDER":
                score -= 1
            else:
                score += 1
            flags.append("PARK_PITCHER")

    if side == "UNDER":
        score -= 1
        flags.append("UNDER")

    if side_delta_season >= 2.0 or side_delta_last_5 >= 2.0:
        flags.append("LINE_LOW")
    elif side_delta_season <= 0.75 and side_delta_last_5 <= 0.75:
        flags.append("LINE_HIGH")

    score += _risk_stack_adjustment(
        side=side,
        flags=flags,
        side_delta_last_5=side_delta_last_5,
        side_delta_season=side_delta_season,
    )

    return score, flags


def _recent_confidence_adjustment(
    hits_last_5: int,
    played_last_5: int,
    hits_last_10: int,
    played_last_10: int,
    flags: list[str],
) -> int:
    adjustment = 0
    rate_last_5 = hits_last_5 / played_last_5 if played_last_5 else 0.0
    rate_last_10 = hits_last_10 / played_last_10 if played_last_10 else 0.0

    if played_last_5 >= 4 and rate_last_5 >= 0.8:
        adjustment += 2
        flags.append("CONSISTENT")
    elif played_last_5 >= 4 and rate_last_5 >= 0.6:
        adjustment += 1
    elif played_last_5 >= 4 and rate_last_5 <= 0.4:
        adjustment -= 1
        flags.append("RECENT_WEAK")

    if played_last_10 >= 5 and rate_last_10 >= 0.7:
        adjustment += 1
    elif played_last_10 >= 5 and rate_last_10 <= 0.45:
        adjustment -= 1

    return adjustment


def _projection_confidence_adjustment(
    prop_type: str,
    side: str,
    projected_edge: float,
    projected_outs: float,
    projected_batters_faced: float,
    flags: list[str],
) -> int:
    adjustment = 0

    if projected_edge >= 2.0:
        adjustment += 2
        flags.append("EDGE_PLUS")
        flags.append("EDGE_EXTREME")
    elif projected_edge >= 1.5:
        adjustment += 2
        flags.append("EDGE_PLUS")
    elif projected_edge >= 1.0:
        adjustment += 1
    elif projected_edge >= 0.75:
        adjustment += 1
    elif projected_edge <= 0.25:
        adjustment -= 1

    if prop_type != PITCHER_STRIKEOUTS:
        return adjustment

    if side == "OVER":
        if projected_outs >= 18.0:
            adjustment += 1
            flags.append("DEPTH_PLUS")
        elif projected_outs <= 14.5:
            adjustment -= 1

        if projected_batters_faced >= 25.0:
            adjustment += 1
        elif projected_batters_faced <= 20.5:
            adjustment -= 1
    else:
        if projected_outs <= 14.5:
            adjustment += 1
            flags.append("SHORT_LEASH_PLUS")
        elif projected_outs >= 18.5:
            adjustment -= 1

        if projected_batters_faced <= 21.0:
            adjustment += 1
        elif projected_batters_faced >= 26.0:
            adjustment -= 1

    return adjustment


def _matchup_confidence_adjustment(
    prop_type: str,
    side: str,
    matchup_context: MatchupContext | None,
    flags: list[str],
) -> int:
    if prop_type != PITCHER_STRIKEOUTS or matchup_context is None:
        return 0

    adjustment = 0
    opponent_k_rate = matchup_context.opponent_k_rate_vs_hand
    opponent_outs_factor = matchup_context.opponent_outs_factor

    if side == "OVER":
        if opponent_k_rate >= 0.245:
            adjustment += 2
            flags.append("MATCHUP_K_PLUS")
        elif opponent_k_rate >= 0.23:
            adjustment += 1
        elif opponent_k_rate <= 0.205:
            adjustment -= 2
            flags.append("MATCHUP_K_MINUS")
        elif opponent_k_rate <= 0.215:
            adjustment -= 1

        if opponent_outs_factor >= 1.04:
            adjustment += 1
            flags.append("OPP_OUTS_PLUS")
        elif opponent_outs_factor <= 0.97:
            adjustment -= 1
    else:
        if opponent_k_rate <= 0.205:
            adjustment += 2
            flags.append("MATCHUP_K_MINUS")
        elif opponent_k_rate <= 0.215:
            adjustment += 1
        elif opponent_k_rate >= 0.245:
            adjustment -= 2
            flags.append("MATCHUP_K_PLUS")
        elif opponent_k_rate >= 0.23:
            adjustment -= 1

        if opponent_outs_factor <= 0.97:
            adjustment += 1
        elif opponent_outs_factor >= 1.04:
            adjustment -= 1
            flags.append("OPP_OUTS_PLUS")

    return adjustment


def _risk_stack_adjustment(
    side: str,
    flags: list[str],
    side_delta_last_5: float,
    side_delta_season: float,
) -> int:
    caution_flags = {
        "VOLATILE",
        "CONTROL_RISK",
        "SHORT_START_RISK",
        "TOUGH_MATCHUP",
        "PATIENT_OPP",
    }
    if side == "OVER":
        caution_flags.add("LOW_PITCH")

    caution_count = sum(1 for flag in caution_flags if flag in flags)
    adjustment = 0
    if caution_count >= 2:
        adjustment -= 2
    if caution_count >= 3:
        adjustment -= 2
    if caution_count >= 4:
        adjustment -= 1

    if side == "OVER" and "TOUGH_MATCHUP" in flags and "PATIENT_OPP" in flags:
        adjustment -= 1
    if side == "UNDER" and "GOOD_MATCHUP" in flags:
        adjustment -= 1
    if "LINE_HIGH" in flags and caution_count >= 2:
        adjustment -= 1
    if (side_delta_last_5 >= 2.0 or side_delta_season >= 2.0) and caution_count >= 2:
        adjustment -= 1
    if "EDGE_EXTREME" in flags and caution_count >= 1:
        adjustment -= 1

    # Volatile over spots were still reaching Core too easily off trend/matchup support.
    # We want cheap-looking lines and demanding lines alike to require cleaner stability.
    if side == "OVER" and "VOLATILE" in flags:
        adjustment -= 1

        if "LINE_LOW" in flags and (
            "LOW_PITCH" in flags
            or "SHORT_START_RISK" in flags
            or "CONTROL_RISK" in flags
        ):
            adjustment -= 1

        if "LINE_HIGH" in flags:
            adjustment -= 1

        if "GOOD_MATCHUP" in flags or "FREE_SWING_OPP" in flags:
            adjustment -= 1

    return adjustment


def _workload_stability(logs: list[PitcherGameLog], prop_definition: PropDefinition) -> float:
    if len(logs) <= 1:
        return 0.0
    values = [prop_definition.extractor(log) for log in logs]
    deviation = pstdev(values)
    bounded = max(0.0, 1.0 - (deviation / prop_definition.volatility_warning))
    return round(bounded, 2)


def _project_opportunity(
    logs: list[PitcherGameLog],
    matchup_context: MatchupContext | None,
    avg_outs_last_5: float,
    avg_outs_season: float,
    avg_pitch_count_last_5: float,
    avg_walk_rate_last_5: float,
    avg_earned_runs_last_5: float,
    quality_starts_recent: int,
    short_starts_recent: int,
    outs_stability: float,
) -> tuple[float, float]:
    recent_logs = logs[:5]
    avg_bf_per_out_last_5 = mean(log.batters_faced / max(log.outs_recorded, 1) for log in recent_logs)

    projected_outs = (avg_outs_last_5 * 0.6) + (avg_outs_season * 0.4)

    if avg_pitch_count_last_5 >= 95:
        projected_outs += 0.6
    elif avg_pitch_count_last_5 <= 80:
        projected_outs -= 1.0

    if outs_stability >= 0.72:
        projected_outs += 0.4
    elif outs_stability <= 0.42:
        projected_outs -= 0.7

    projected_outs += min(quality_starts_recent, 3) * 0.2
    projected_outs -= min(short_starts_recent, 3) * 0.4

    if avg_walk_rate_last_5 >= 0.09:
        projected_outs -= 0.6
    elif avg_walk_rate_last_5 <= 0.06:
        projected_outs += 0.3

    if avg_earned_runs_last_5 >= 3.2:
        projected_outs -= 0.5
    elif avg_earned_runs_last_5 <= 2.0:
        projected_outs += 0.3

    if matchup_context is not None:
        projected_outs += (matchup_context.opponent_outs_factor - 1.0) * 9.0
        if matchup_context.moneyline <= -140:
            projected_outs += 0.3
        elif matchup_context.moneyline >= 130:
            projected_outs -= 0.3

    projected_outs = round(min(max(projected_outs, 9.0), 24.0), 1)
    projected_batters_faced = round(min(max(projected_outs * avg_bf_per_out_last_5, projected_outs + 3.0), 36.0), 1)
    return projected_outs, projected_batters_faced


def _project_k_rate(
    logs: list[PitcherGameLog],
    matchup_context: MatchupContext | None,
    avg_k_rate_last_5: float,
    avg_walk_rate_last_5: float,
) -> float:
    avg_k_rate_season = mean(log.strikeouts / max(log.batters_faced, 1) for log in logs)
    projected_k_rate = (avg_k_rate_last_5 * 0.6) + (avg_k_rate_season * 0.4)

    if matchup_context is not None:
        projected_k_rate += (matchup_context.opponent_k_rate_vs_hand - 0.22) * 0.6
        if matchup_context.opponent_walk_rate_vs_hand >= 0.09:
            projected_k_rate -= 0.008
        elif matchup_context.opponent_walk_rate_vs_hand <= 0.075:
            projected_k_rate += 0.005

    if avg_walk_rate_last_5 >= 0.09:
        projected_k_rate -= 0.006

    return round(min(max(projected_k_rate, 0.08), 0.42), 3)


def _matchup_rating(prop_type: str, matchup_context: MatchupContext | None) -> float:
    if matchup_context is None:
        return 0.0
    if prop_type == PITCHER_STRIKEOUTS:
        return round(
            ((matchup_context.opponent_k_rate_vs_hand - 0.22) * 10.0)
            - ((matchup_context.opponent_walk_rate_vs_hand - 0.08) * 8.0)
            + ((matchup_context.opponent_outs_factor - 1.0) * 1.5),
            2,
        )
    return round(
        ((matchup_context.opponent_outs_factor - 1.0) * 2.5)
        - ((matchup_context.opponent_walk_rate_vs_hand - 0.08) * 6.0)
        - ((matchup_context.opponent_woba_vs_hand - 0.315) * 8.0)
        - ((matchup_context.park_run_factor - 1.0) * 2.0),
        2,
    )


def build_daily_starter_board(
    settings: Settings,
    games: Iterable,
    prop_lines: Iterable[PropLine],
    logs_by_pitcher: dict[str, list[PitcherGameLog]],
    matchup_contexts: dict[tuple[str, str], MatchupContext],
    min_starts: int,
) -> list[StarterAssessment]:
    strikeout_lines = {
        (line.team, normalize_name(line.subject_name_raw)): line
        for line in prop_lines
        if line.prop_type == PITCHER_STRIKEOUTS
    }
    assessments: list[StarterAssessment] = []
    seen: set[str] = set()
    for game in games:
        for pitcher_name, team, opponent in (
            (game.probable_home_pitcher, game.home_team, game.away_team),
            (game.probable_away_pitcher, game.away_team, game.home_team),
        ):
            if not pitcher_name:
                continue
            pitcher_key = normalize_name(pitcher_name)
            if pitcher_key in seen:
                continue
            seen.add(pitcher_key)
            logs = [log for log in logs_by_pitcher.get(pitcher_key, []) if log.did_start]
            if not logs:
                strikeout_line = strikeout_lines.get((team, pitcher_key))
                assessments.append(
                    StarterAssessment(
                        pitcher_name=pitcher_name,
                        pitcher_id=None,
                        team=team,
                        opponent=opponent,
                        hand="",
                        season_starts=0,
                        avg_strikeouts_last_5=0.0,
                        avg_strikeouts_season=0.0,
                        avg_outs_last_5=0.0,
                        avg_outs_season=0.0,
                        avg_pitch_count_last_5=0.0,
                        avg_k_rate_last_5=0.0,
                        avg_walk_rate_last_5=0.0,
                        avg_earned_runs_last_5=0.0,
                        workload_stability_ks=0.0,
                        workload_stability_outs=0.0,
                        quality_starts_recent=0,
                        short_starts_recent=0,
                        matchup_rating_ks=_matchup_rating(PITCHER_STRIKEOUTS, matchup_contexts.get((team, opponent))),
                        matchup_rating_outs=_matchup_rating(PITCHER_OUTS_RECORDED, matchup_contexts.get((team, opponent))),
                        overall_score=-99,
                        ks_signal=0.0,
                        outs_signal=0.0,
                        projected_outs=0.0,
                        projected_batters_faced=0.0,
                        projected_k_rate=0.0,
                        projected_strikeouts=0.0,
                        strikeout_line=strikeout_line.line if strikeout_line else None,
                        line_bookmaker=strikeout_line.bookmaker if strikeout_line else None,
                        shortlist_status="NoData" if strikeout_line else "NoLine",
                        shortlist_reason="No usable starter logs" if strikeout_line else "No posted strikeout line",
                        flags=["THIN"],
                    )
                )
                continue
            last_5 = logs[:5]
            last_10 = logs[:10]
            ks_last_5 = [float(log.strikeouts) for log in last_5]
            ks_last_10 = [float(log.strikeouts) for log in last_10]
            ks_all = [float(log.strikeouts) for log in logs]
            outs_last_5 = [float(log.outs_recorded) for log in last_5]
            outs_all = [float(log.outs_recorded) for log in logs]
            avg_pitch_count_last_5 = mean(log.pitches_thrown for log in last_5)
            avg_k_rate_last_5 = mean(log.strikeouts / max(log.batters_faced, 1) for log in last_5)
            avg_walk_rate_last_5 = mean(log.walks / max(log.batters_faced, 1) for log in last_5)
            avg_earned_runs_last_5 = mean(log.earned_runs for log in last_5)
            quality_starts_recent = sum(1 for log in last_5 if log.outs_recorded >= 18 and log.earned_runs <= 3)
            short_starts_recent = sum(1 for log in last_5 if log.outs_recorded < 15)
            quality_starts_last_10 = sum(1 for log in last_10 if log.outs_recorded >= 18 and log.earned_runs <= 3)
            short_starts_last_10 = sum(1 for log in last_10 if log.outs_recorded < 15)
            matchup_context = matchup_contexts.get((team, opponent))
            matchup_rating_ks = _matchup_rating(PITCHER_STRIKEOUTS, matchup_context)
            matchup_rating_outs = _matchup_rating(PITCHER_OUTS_RECORDED, matchup_context)
            ks_stability = _workload_stability(last_5, PROP_DEFINITIONS[PITCHER_STRIKEOUTS])
            outs_stability = _workload_stability(last_5, PROP_DEFINITIONS[PITCHER_OUTS_RECORDED])
            ks_signal = round((mean(ks_last_5) * 0.45) + (mean(ks_all) * 0.55) + matchup_rating_ks, 2)
            outs_signal = round((mean(outs_last_5) * 0.45) + (mean(outs_all) * 0.55) + (matchup_rating_outs * 2.0), 2)
            projected_outs, projected_batters_faced = _project_opportunity(
                logs=logs,
                matchup_context=matchup_context,
                avg_outs_last_5=mean(outs_last_5),
                avg_outs_season=mean(outs_all),
                avg_pitch_count_last_5=avg_pitch_count_last_5,
                avg_walk_rate_last_5=avg_walk_rate_last_5,
                avg_earned_runs_last_5=avg_earned_runs_last_5,
                quality_starts_recent=quality_starts_recent,
                short_starts_recent=short_starts_recent,
                outs_stability=outs_stability,
            )
            projected_k_rate = _project_k_rate(
                logs=logs,
                matchup_context=matchup_context,
                avg_k_rate_last_5=avg_k_rate_last_5,
                avg_walk_rate_last_5=avg_walk_rate_last_5,
            )
            projected_strikeouts = round(projected_batters_faced * projected_k_rate, 1)
            strikeout_line = strikeout_lines.get((team, pitcher_key))
            lean_side = None
            lean_edge = None
            lean_score = None
            shortlist_status = "NoLine"
            shortlist_reason = "No posted strikeout line"
            if strikeout_line is not None:
                lean_side = "OVER" if projected_strikeouts >= strikeout_line.line else "UNDER"
                lean_edge = round(projected_strikeouts - strikeout_line.line, 2)
                hits_last_5 = sum(1 for value in ks_last_5 if value > strikeout_line.line) if lean_side == "OVER" else sum(1 for value in ks_last_5 if value < strikeout_line.line)
                hits_last_10 = sum(1 for value in ks_last_10 if value > strikeout_line.line) if lean_side == "OVER" else sum(1 for value in ks_last_10 if value < strikeout_line.line)
                projection_edge = projected_strikeouts - strikeout_line.line
                delta_last_5 = (mean(ks_last_5) - strikeout_line.line) * 0.35 + (projection_edge * 0.65)
                delta_last_10 = (mean(ks_last_10) - strikeout_line.line) * 0.5 + (projection_edge * 0.5)
                delta_season = (mean(ks_all) - strikeout_line.line) * 0.5 + (projection_edge * 0.5)
                if lean_side == "UNDER":
                    delta_last_5 *= -1
                    delta_last_10 *= -1
                    delta_season *= -1
                lean_score, lean_flags = _score_candidate(
                    settings=settings,
                    prop_type=PITCHER_STRIKEOUTS,
                    side=lean_side,
                    hits_last_5=hits_last_5,
                    played_last_5=len(ks_last_5),
                    hits_last_10=hits_last_10,
                    played_last_10=len(ks_last_10),
                    side_delta_last_5=delta_last_5,
                    side_delta_last_10=delta_last_10,
                    side_delta_season=delta_season,
                    avg_pitch_count_last_5=avg_pitch_count_last_5,
                    avg_outs_last_5=mean(outs_last_5),
                    avg_k_rate_last_5=avg_k_rate_last_5,
                    avg_walk_rate_last_5=avg_walk_rate_last_5,
                    avg_earned_runs_last_5=avg_earned_runs_last_5,
                    quality_starts_last_10=quality_starts_last_10,
                    short_starts_last_10=short_starts_last_10,
                    workload_stability=ks_stability,
                    projected_outs=projected_outs,
                    projected_batters_faced=projected_batters_faced,
                    projected_edge=max(delta_last_5, delta_last_10, delta_season),
                    matchup_context=matchup_context,
                    matchup_rating=matchup_rating_ks,
                )
                shortlist_status, shortlist_reason = _starter_shortlist_status(
                    settings=settings,
                    side=lean_side,
                    hits_last_5=hits_last_5,
                    played_last_5=len(ks_last_5),
                    hits_last_10=hits_last_10,
                    played_last_10=len(ks_last_10),
                    side_delta_last_5=delta_last_5,
                    side_delta_last_10=delta_last_10,
                    side_delta_season=delta_season,
                    lean_score=lean_score,
                    line=strikeout_line.line,
                    projected_strikeouts=projected_strikeouts,
                    projected_outs=projected_outs,
                    projected_batters_faced=projected_batters_faced,
                    flags=lean_flags,
                )
            assessments.append(
                StarterAssessment(
                    pitcher_name=pitcher_name,
                    pitcher_id=last_5[0].pitcher_id if last_5 else None,
                    team=team,
                    opponent=opponent,
                    hand=last_5[0].hand if last_5 else "",
                    season_starts=len(logs),
                    avg_strikeouts_last_5=mean(ks_last_5),
                    avg_strikeouts_season=mean(ks_all),
                    avg_outs_last_5=mean(outs_last_5),
                    avg_outs_season=mean(outs_all),
                    avg_pitch_count_last_5=avg_pitch_count_last_5,
                    avg_k_rate_last_5=avg_k_rate_last_5,
                    avg_walk_rate_last_5=avg_walk_rate_last_5,
                    avg_earned_runs_last_5=avg_earned_runs_last_5,
                    workload_stability_ks=ks_stability,
                    workload_stability_outs=outs_stability,
                    quality_starts_recent=quality_starts_recent,
                    short_starts_recent=short_starts_recent,
                    matchup_rating_ks=matchup_rating_ks,
                    matchup_rating_outs=matchup_rating_outs,
                    overall_score=_starter_board_score(
                        avg_pitch_count_last_5=avg_pitch_count_last_5,
                        avg_k_rate_last_5=avg_k_rate_last_5,
                        avg_walk_rate_last_5=avg_walk_rate_last_5,
                        avg_earned_runs_last_5=avg_earned_runs_last_5,
                        quality_starts_recent=quality_starts_recent,
                        short_starts_recent=short_starts_recent,
                        ks_stability=ks_stability,
                        outs_stability=outs_stability,
                        matchup_rating_ks=matchup_rating_ks,
                        matchup_rating_outs=matchup_rating_outs,
                    ),
                    ks_signal=ks_signal,
                    outs_signal=outs_signal,
                    projected_outs=projected_outs,
                    projected_batters_faced=projected_batters_faced,
                    projected_k_rate=projected_k_rate,
                    projected_strikeouts=projected_strikeouts,
                    strikeout_line=strikeout_line.line if strikeout_line else None,
                    line_bookmaker=strikeout_line.bookmaker if strikeout_line else None,
                    lean_side=lean_side,
                    lean_edge=lean_edge,
                    lean_score=lean_score,
                    shortlist_status=shortlist_status,
                    shortlist_reason=shortlist_reason,
                    flags=_starter_board_flags(
                        season_starts=len(logs),
                        min_starts=min_starts,
                        avg_pitch_count_last_5=avg_pitch_count_last_5,
                        avg_k_rate_last_5=avg_k_rate_last_5,
                        avg_walk_rate_last_5=avg_walk_rate_last_5,
                        quality_starts_recent=quality_starts_recent,
                        short_starts_recent=short_starts_recent,
                        matchup_rating_ks=matchup_rating_ks,
                        matchup_rating_outs=matchup_rating_outs,
                    ),
                )
            )
    return sorted(
        assessments,
        key=lambda item: (item.lean_score if item.lean_score is not None else -999, item.overall_score, item.outs_signal, item.ks_signal, item.avg_pitch_count_last_5),
        reverse=True,
    )


def _starter_shortlist_status(
    settings: Settings,
    side: str,
    hits_last_5: int,
    played_last_5: int,
    hits_last_10: int,
    played_last_10: int,
    side_delta_last_5: float,
    side_delta_last_10: float,
    side_delta_season: float,
    lean_score: int,
    line: float,
    projected_strikeouts: float,
    projected_outs: float,
    projected_batters_faced: float,
    flags: list[str],
) -> tuple[str, str]:
    if not _passes_projection_rule(
        settings,
        side_delta_last_5,
        side_delta_last_10,
        side_delta_season,
    ):
        best_edge = max(side_delta_last_5, side_delta_last_10, side_delta_season)
        return "Blocked", f"Edge {best_edge:.2f}/{_projection_floor(settings):.2f}"
    if lean_score < settings.min_display_score:
        support_note = _starter_support_note(settings, hits_last_5, played_last_5, hits_last_10, played_last_10)
        if support_note:
            return "Watch", f"Below display threshold ({settings.min_display_score}); {support_note}"
        return "Watch", f"Below display threshold ({settings.min_display_score})"
    tier_probe = SimpleNamespace(
        prop_type=PITCHER_STRIKEOUTS,
        side=side,
        line=line,
        score=lean_score,
        projected_strikeouts=projected_strikeouts,
        projected_outs=projected_outs,
        projected_batters_faced=projected_batters_faced,
        flags=flags,
    )
    core_reasons = core_block_reasons(tier_probe)
    if core_reasons:
        return "Watch", f"Core gate: {', '.join(core_reasons[:2])}"
    return "Qualified", f"{side} candidate made shortlist"


def _starter_support_note(
    settings: Settings,
    hits_last_5: int,
    played_last_5: int,
    hits_last_10: int,
    played_last_10: int,
) -> str | None:
    support_needed = _required_support_hits(settings, played_last_10)
    if hits_last_10 < support_needed:
        return f"support {hits_last_10}/{played_last_10}"
    if played_last_5 >= 4 and hits_last_5 / played_last_5 <= 0.4:
        return f"recent {hits_last_5}/{played_last_5}"
    return None


def _starter_board_score(
    avg_pitch_count_last_5: float,
    avg_k_rate_last_5: float,
    avg_walk_rate_last_5: float,
    avg_earned_runs_last_5: float,
    quality_starts_recent: int,
    short_starts_recent: int,
    ks_stability: float,
    outs_stability: float,
    matchup_rating_ks: float,
    matchup_rating_outs: float,
) -> int:
    score = 0
    if avg_pitch_count_last_5 >= 95:
        score += 2
    elif avg_pitch_count_last_5 <= 85:
        score -= 1
    if avg_k_rate_last_5 >= 0.28:
        score += 2
    elif avg_k_rate_last_5 <= 0.21:
        score -= 1
    if avg_walk_rate_last_5 >= 0.09:
        score -= 2
    elif avg_walk_rate_last_5 <= 0.06:
        score += 1
    if avg_earned_runs_last_5 <= 2.5:
        score += 1
    elif avg_earned_runs_last_5 >= 3.5:
        score -= 1
    score += quality_starts_recent
    score -= short_starts_recent
    if ks_stability >= 0.72:
        score += 1
    if outs_stability >= 0.72:
        score += 1
    if matchup_rating_ks >= 0.2:
        score += 1
    elif matchup_rating_ks <= -0.2:
        score -= 1
    if matchup_rating_outs >= 0.2:
        score += 1
    elif matchup_rating_outs <= -0.2:
        score -= 1
    return score


def _starter_board_flags(
    season_starts: int,
    min_starts: int,
    avg_pitch_count_last_5: float,
    avg_k_rate_last_5: float,
    avg_walk_rate_last_5: float,
    quality_starts_recent: int,
    short_starts_recent: int,
    matchup_rating_ks: float,
    matchup_rating_outs: float,
) -> list[str]:
    flags: list[str] = []
    if season_starts < min_starts:
        flags.append("THIN")
    if avg_pitch_count_last_5 >= 95:
        flags.append("WORKLOAD_PLUS")
    elif avg_pitch_count_last_5 <= 85:
        flags.append("LOW_PITCH")
    if avg_k_rate_last_5 >= 0.28:
        flags.append("K_EFF")
    elif avg_k_rate_last_5 <= 0.21:
        flags.append("K_EFF_LOW")
    if avg_walk_rate_last_5 >= 0.09:
        flags.append("CONTROL_RISK")
    if quality_starts_recent >= 3:
        flags.append("QS_PLUS")
    if short_starts_recent >= 2:
        flags.append("SHORT_START_RISK")
    if matchup_rating_ks >= 0.2 or matchup_rating_outs >= 0.2:
        flags.append("GOOD_MATCHUP")
    elif matchup_rating_ks <= -0.2 or matchup_rating_outs <= -0.2:
        flags.append("TOUGH_MATCHUP")
    return flags
