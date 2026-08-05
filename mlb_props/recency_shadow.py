from __future__ import annotations

from datetime import date

from .models import MatchupContext, PitcherGameLog, RecencyProjectionShadow
from .version import PITCHER_RECENCY_SHADOW_VERSION


def _aggregate_rate(
    logs: list[PitcherGameLog],
    numerator: str,
    denominator: str,
) -> float:
    denominator_total = sum(max(int(getattr(log, denominator)), 0) for log in logs)
    if denominator_total <= 0:
        return 0.0
    numerator_total = sum(max(int(getattr(log, numerator)), 0) for log in logs)
    return numerator_total / denominator_total


def build_recency_projection_shadow(
    logs: list[PitcherGameLog],
    screen_date: date,
    matchup_context: MatchupContext | None,
    active_projected_outs: float,
) -> RecencyProjectionShadow:
    """Build a leakage-safe, research-only alternative strikeout projection."""
    starts = sorted(
        (
            log
            for log in logs
            if log.did_start and log.game_date < screen_date
        ),
        key=lambda log: log.game_date,
        reverse=True,
    )
    last_10 = starts[:10]
    last_5 = starts[:5]

    season_k_rate = _aggregate_rate(starts, "strikeouts", "batters_faced")
    k_rate_last_10 = _aggregate_rate(last_10, "strikeouts", "batters_faced")
    k_rate_last_5 = _aggregate_rate(last_5, "strikeouts", "batters_faced")
    walk_rate_last_10 = _aggregate_rate(last_10, "walks", "batters_faced")
    season_bf_per_out = _aggregate_rate(starts, "batters_faced", "outs_recorded")
    bf_per_out_last_5 = _aggregate_rate(last_5, "batters_faced", "outs_recorded")

    projected_k_rate = (
        (season_k_rate * 0.50)
        + (k_rate_last_10 * 0.30)
        + (k_rate_last_5 * 0.20)
    )
    if matchup_context is not None:
        projected_k_rate += (matchup_context.opponent_k_rate_vs_hand - 0.22) * 0.6
        if matchup_context.opponent_walk_rate_vs_hand >= 0.09:
            projected_k_rate -= 0.008
        elif matchup_context.opponent_walk_rate_vs_hand <= 0.075:
            projected_k_rate += 0.005
    if walk_rate_last_10 >= 0.09:
        projected_k_rate -= 0.006
    projected_k_rate = min(max(projected_k_rate, 0.08), 0.42)

    blended_bf_per_out = (bf_per_out_last_5 * 0.60) + (season_bf_per_out * 0.40)
    projected_bf = min(
        max(active_projected_outs * blended_bf_per_out, active_projected_outs + 3.0),
        36.0,
    )
    projected_ks = projected_bf * projected_k_rate

    flags = ["RESEARCH_ONLY", "AGGREGATE_RATE_WINDOWS"]
    if len(starts) < 10:
        flags.append("THIN_L10_SAMPLE")

    return RecencyProjectionShadow(
        version=PITCHER_RECENCY_SHADOW_VERSION,
        screen_date=screen_date.isoformat(),
        starts_available=len(starts),
        starts_used_last_10=len(last_10),
        starts_used_last_5=len(last_5),
        season_k_rate=round(season_k_rate, 4),
        k_rate_last_10=round(k_rate_last_10, 4),
        k_rate_last_5=round(k_rate_last_5, 4),
        walk_rate_last_10=round(walk_rate_last_10, 4),
        season_bf_per_out=round(season_bf_per_out, 4),
        bf_per_out_last_5=round(bf_per_out_last_5, 4),
        blended_bf_per_out=round(blended_bf_per_out, 4),
        shadow_projected_outs=round(active_projected_outs, 1),
        shadow_projected_batters_faced=round(projected_bf, 1),
        shadow_projected_k_rate=round(projected_k_rate, 3),
        shadow_projected_strikeouts=round(projected_ks, 1),
        flags=flags,
    )
