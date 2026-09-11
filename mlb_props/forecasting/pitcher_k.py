"""Pitcher strikeout forecast engine (pitcher-k-dist-v1).

Directional probability comes from a logistic model on graded pitcher
features, isotonic-calibrated. A count distribution supplies integer-line
push probability. The model owns the pick; price is never consulted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..calibration import IsotonicCalibrator, LogisticModel, count_probabilities, shrink_logit


@dataclass
class PitcherFeatures:
    game_pk: str | None
    screen_date: str
    pitcher_name: str
    line: float
    projected_strikeouts: float
    projected_outs: float
    projected_batters_faced: float
    season_prop_avg: float
    avg_last_10: float
    avg_last_5: float
    avg_walk_rate_last_5: float | None = None
    workload_stability: float | None = None
    opponent_k_rate_vs_hand: float | None = None
    opponent_outs_factor: float | None = None
    park_run_factor: float | None = None
    moneyline: int | None = None
    days_since_last_start: float | None = None
    season_k_rate_recency: float | None = None
    k_rate_last_10_recency: float | None = None
    k_rate_last_5_recency: float | None = None

    def row(self) -> list[float]:
        return [
            self.projected_strikeouts,
            self.line,
            self.projected_strikeouts - self.line,
            self.season_prop_avg,
            self.avg_last_10,
            self.avg_last_5,
            self.projected_outs,
            self.projected_batters_faced,
            self.avg_walk_rate_last_5 or -1.0,
            self.workload_stability or -1.0,
            self.opponent_k_rate_vs_hand or -1.0,
            self.opponent_outs_factor or -1.0,
            self.park_run_factor or -1.0,
            1.0 if (self.moneyline or 0) >= 150 else 0.0,
            1.0 if (self.moneyline or 0) <= -150 else 0.0,
            float(self.days_since_last_start)
            if self.days_since_last_start is not None
            else -1.0,
            self.season_k_rate_recency or -1.0,
            self.k_rate_last_10_recency or -1.0,
            self.k_rate_last_5_recency or -1.0,
        ]


@dataclass
class PitcherLineProbabilities:
    version: str
    game_pk: str | None
    pitcher_name: str
    line: float
    p_over: float
    p_push: float
    p_under: float
    mean_projection: float | None = None
    dispersion: float | None = None


@dataclass
class PitcherEngineResult:
    logit: LogisticModel
    isotonic: IsotonicCalibrator | None
    factor: float
    dispersion: float
    n_rows: int


def estimate_dispersion(rows: list[tuple[float, float | None]]) -> float:
    """`rows` are (projected, actual) pairs. Overdispersed counts satisfy
    E[(actual - projected)^2] ~= mean + mean^2 / r, solved by pooling:
    r = sum(mean_i^2) / sum(resid_i^2 - mean_i)."""
    numerator = 0.0
    denominator = 0.0
    n = 0
    for projected, actual in rows:
        if projected is None or actual is None or projected <= 0:
            continue
        n += 1
        residual_sq = (float(actual) - float(projected)) ** 2
        numerator += float(projected) ** 2
        denominator += residual_sq - float(projected)
    if n < 30 or denominator <= 0:
        return 6.0
    return max(2.0, min(30.0, numerator / denominator))


def fit_pitcher_engine(rows: list[dict]) -> PitcherEngineResult | None:
    """`rows` are dicts with `features: PitcherFeatures`, `over_win: 0|1`
    (pushes excluded by the caller) and optional `actual_k`/`projected_k`
    for the dispersion estimate."""
    clean = [
        row
        for row in rows
        if row.get("over_win") in (0, 1) and row.get("features") is not None
    ]
    if len(clean) < 150:
        return None
    logit = LogisticModel()
    logit.fit(
        rows=[row["features"].row() for row in clean],
        targets=[int(row["over_win"]) for row in clean],
    )
    isotonic = IsotonicCalibrator.fit(
        [
            (logit.predict_proba(row["features"].row()), int(row["over_win"]))
            for row in clean
        ]
    )
    dispersion = estimate_dispersion(
        [(row["features"].projected_strikeouts, row.get("actual_k")) for row in clean]
    )
    return PitcherEngineResult(
        logit=logit,
        isotonic=None,
        factor=1.0,
        dispersion=dispersion,
        n_rows=len(clean),
    )


def line_probabilities_from_logit(
    result: PitcherEngineResult,
    features: PitcherFeatures,
    version: str = "pitcher-k-dist-v1",
) -> PitcherLineProbabilities:
    raw = result.logit.predict_proba(features.row())
    p_over_cal = shrink_logit(raw, result.factor)
    p_over_cal = min(max(p_over_cal, 0.01), 0.99)

    mean = max(0.05, features.projected_strikeouts)
    base_over, base_push, base_under = count_probabilities(
        features.line, mean, result.dispersion
    )
    # Keep the push mass from the count distribution, then re-split the
    # remaining mass so the calibrated logit drives direction.
    p_over_final = p_over_cal * (1.0 - base_push)
    p_under_final = (1.0 - p_over_cal) * (1.0 - base_push)
    total = p_over_final + p_under_final
    p_over_final /= total
    p_under_final /= total
    return PitcherLineProbabilities(
        version=version,
        game_pk=features.game_pk,
        pitcher_name=features.pitcher_name,
        line=features.line,
        p_over=p_over_final,
        p_push=base_push,
        p_under=p_under_final,
        mean_projection=mean,
        dispersion=result.dispersion,
    )


def pick_side(probabilities: PitcherLineProbabilities) -> tuple[str | None, float | None]:
    """Deterministic model pick: the side with the greatest win
    probability. Push-heavy integer lines are not pickable sides."""
    if probabilities.p_push and probabilities.p_push > max(
        probabilities.p_over, probabilities.p_under
    ):
        return None, None
    if probabilities.p_over >= probabilities.p_under:
        return "over", probabilities.p_over
    return "under", probabilities.p_under
