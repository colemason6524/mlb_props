from __future__ import annotations

from datetime import date
from statistics import mean, pstdev

from .models import OpportunityShadow, PitcherGameLog
from .version import PITCHER_OPPORTUNITY_SHADOW_VERSION


def build_opportunity_shadow(
    logs: list[PitcherGameLog],
    screen_date: date,
) -> OpportunityShadow:
    """Build research-only workload estimates from information available pregame."""
    prior_appearances = sorted(
        (log for log in logs if log.game_date < screen_date),
        key=lambda log: log.game_date,
        reverse=True,
    )
    starts = [log for log in prior_appearances if log.did_start]
    last_3 = starts[:3]
    last_5 = starts[:5]

    if not last_3:
        return OpportunityShadow(
            version=PITCHER_OPPORTUNITY_SHADOW_VERSION,
            screen_date=screen_date.isoformat(),
            starts_available=0,
            recent_start_dates=[],
            pitch_counts_last_3=[],
            outs_last_3=[],
            batters_faced_last_3=[],
            pitches_per_bf_last_3=[],
            days_since_last_start=None,
            avg_pitch_count_last_3=None,
            max_pitch_count_last_5=None,
            pitch_count_trend="UNKNOWN",
            pitch_count_trend_delta=None,
            outs_trend="UNKNOWN",
            outs_trend_delta=None,
            pitch_count_volatility_last_5=None,
            outs_volatility_last_5=None,
            short_starts_last_5=0,
            shadow_pitch_budget=None,
            shadow_projected_batters_faced=None,
            shadow_projected_outs=None,
            opportunity_confidence="LOW",
            flags=["NO_START_DATA"],
        )

    pitch_counts = [log.pitches_thrown for log in last_3]
    outs = [log.outs_recorded for log in last_3]
    batters_faced = [log.batters_faced for log in last_3]
    pitches_per_bf = [
        round(log.pitches_thrown / log.batters_faced, 3)
        if log.batters_faced > 0
        else None
        for log in last_3
    ]
    days_since_last_start = (screen_date - last_3[0].game_date).days
    pitch_trend_delta = _latest_vs_prior_delta(pitch_counts)
    outs_trend_delta = _latest_vs_prior_delta(outs)
    pitch_volatility = _volatility([log.pitches_thrown for log in last_5])
    outs_volatility = _volatility([log.outs_recorded for log in last_5])
    short_starts = sum(1 for log in last_5 if log.outs_recorded < 15)

    shadow_pitch_budget = _weighted_recent(pitch_counts)
    valid_pitches_per_bf = [value for value in pitches_per_bf if value is not None and value > 0]
    weighted_pitches_per_bf = (
        _weighted_recent(valid_pitches_per_bf)
        if len(valid_pitches_per_bf) == len(last_3)
        else None
    )
    out_rates = [
        log.outs_recorded / log.batters_faced
        for log in last_3
        if log.batters_faced > 0
    ]
    weighted_out_rate = (
        _weighted_recent(out_rates)
        if len(out_rates) == len(last_3)
        else None
    )

    shadow_bf = None
    shadow_outs = None
    if weighted_pitches_per_bf and weighted_out_rate:
        shadow_bf = round(min(max(shadow_pitch_budget / weighted_pitches_per_bf, 10.0), 36.0), 1)
        shadow_outs = round(min(max(shadow_bf * weighted_out_rate, 6.0), 24.0), 1)

    flags = _opportunity_flags(
        prior_appearances=prior_appearances,
        starts=starts,
        last_3=last_3,
        days_since_last_start=days_since_last_start,
        pitch_trend_delta=pitch_trend_delta,
        pitch_volatility=pitch_volatility,
        outs_volatility=outs_volatility,
        short_starts=short_starts,
    )
    confidence = _opportunity_confidence(
        starts_available=len(starts),
        flags=flags,
    )

    return OpportunityShadow(
        version=PITCHER_OPPORTUNITY_SHADOW_VERSION,
        screen_date=screen_date.isoformat(),
        starts_available=len(starts),
        recent_start_dates=[log.game_date.isoformat() for log in last_3],
        pitch_counts_last_3=pitch_counts,
        outs_last_3=outs,
        batters_faced_last_3=batters_faced,
        pitches_per_bf_last_3=pitches_per_bf,
        days_since_last_start=days_since_last_start,
        avg_pitch_count_last_3=round(mean(pitch_counts), 1),
        max_pitch_count_last_5=max(log.pitches_thrown for log in last_5),
        pitch_count_trend=_trend_label(pitch_trend_delta, threshold=8.0),
        pitch_count_trend_delta=(
            round(pitch_trend_delta, 1) if pitch_trend_delta is not None else None
        ),
        outs_trend=_trend_label(outs_trend_delta, threshold=3.0),
        outs_trend_delta=(
            round(outs_trend_delta, 1) if outs_trend_delta is not None else None
        ),
        pitch_count_volatility_last_5=pitch_volatility,
        outs_volatility_last_5=outs_volatility,
        short_starts_last_5=short_starts,
        shadow_pitch_budget=round(shadow_pitch_budget, 1),
        shadow_projected_batters_faced=shadow_bf,
        shadow_projected_outs=shadow_outs,
        opportunity_confidence=confidence,
        flags=flags,
    )


def _weighted_recent(values: list[float | int]) -> float:
    weights_by_length = {
        1: [1.0],
        2: [0.625, 0.375],
        3: [0.5, 0.3, 0.2],
    }
    weights = weights_by_length[len(values)]
    return sum(float(value) * weight for value, weight in zip(values, weights))


def _latest_vs_prior_delta(values: list[float | int]) -> float | None:
    if len(values) < 2:
        return None
    return float(values[0]) - mean(float(value) for value in values[1:])


def _trend_label(delta: float | None, threshold: float) -> str:
    if delta is None:
        return "UNKNOWN"
    if delta >= threshold:
        return "UP"
    if delta <= -threshold:
        return "DOWN"
    return "STABLE"


def _volatility(values: list[int]) -> float | None:
    if len(values) < 2:
        return None
    return round(pstdev(values), 2)


def _opportunity_flags(
    prior_appearances: list[PitcherGameLog],
    starts: list[PitcherGameLog],
    last_3: list[PitcherGameLog],
    days_since_last_start: int,
    pitch_trend_delta: float | None,
    pitch_volatility: float | None,
    outs_volatility: float | None,
    short_starts: int,
) -> list[str]:
    flags: list[str] = []
    if len(starts) < 3:
        flags.append("THIN_START_SAMPLE")
    elif len(starts) < 5:
        flags.append("LIMITED_START_SAMPLE")

    if days_since_last_start >= 10:
        flags.append("LONG_LAYOFF")
    elif days_since_last_start <= 3:
        flags.append("QUICK_RETURN")

    latest_pitch_count = last_3[0].pitches_thrown
    if latest_pitch_count < 70:
        flags.append("VERY_LOW_RECENT_PITCH_COUNT")
    elif latest_pitch_count < 80:
        flags.append("LOW_RECENT_PITCH_COUNT")

    if pitch_trend_delta is not None and pitch_trend_delta >= 10:
        flags.append("WORKLOAD_RAMP")
    elif pitch_trend_delta is not None and pitch_trend_delta <= -10:
        flags.append("WORKLOAD_DECLINE")

    if pitch_volatility is not None and pitch_volatility >= 12:
        flags.append("PITCH_COUNT_VOLATILE")
    if outs_volatility is not None and outs_volatility >= 4:
        flags.append("OUTS_VOLATILE")
    if short_starts >= 2:
        flags.append("MULTIPLE_SHORT_STARTS")
    elif short_starts == 1:
        flags.append("RECENT_SHORT_START")

    recent_appearances = prior_appearances[:5]
    if any(not log.did_start for log in recent_appearances):
        flags.append("MIXED_RECENT_ROLE")
    if any(log.batters_faced <= 0 for log in last_3):
        flags.append("INCOMPLETE_BF_DATA")
    return flags


def _opportunity_confidence(starts_available: int, flags: list[str]) -> str:
    low_confidence_flags = {
        "THIN_START_SAMPLE",
        "LONG_LAYOFF",
        "VERY_LOW_RECENT_PITCH_COUNT",
        "MULTIPLE_SHORT_STARTS",
        "MIXED_RECENT_ROLE",
        "INCOMPLETE_BF_DATA",
    }
    instability_flags = {
        "LOW_RECENT_PITCH_COUNT",
        "WORKLOAD_RAMP",
        "WORKLOAD_DECLINE",
        "PITCH_COUNT_VOLATILE",
        "OUTS_VOLATILE",
        "RECENT_SHORT_START",
        "QUICK_RETURN",
    }
    if starts_available < 3 or low_confidence_flags.intersection(flags):
        return "LOW"
    if starts_available < 5 or instability_flags.intersection(flags):
        return "MEDIUM"
    return "HIGH"
