from __future__ import annotations

from math import ceil, erf, floor, sqrt

from .config import PITCHER_OUTS_RECORDED, PITCHER_STRIKEOUTS
from .models import Candidate, PitcherConfidenceEstimate
from .version import PITCHER_CONFIDENCE_MODEL_VERSION


CALIBRATION_STATUS = "PROVISIONAL"
MIN_DISPLAY_PROBABILITY = 0.50
MAX_DISPLAY_PROBABILITY = 0.68

_SEVERE_RISK_FLAGS = {
    "LOW_PITCH",
    "SHORT_LEASH",
    "SHORT_LEASH_PLUS",
    "SHORT_START_RISK",
    "VOLATILE",
    "CONTROL_RISK",
    "MIXED_RECENT_ROLE",
    "MULTIPLE_SHORT_STARTS",
    "VERY_LOW_RECENT_PITCH_COUNT",
}
_MODERATE_RISK_FLAGS = {
    "RECENT_SHORT_START",
    "LOW_RECENT_PITCH_COUNT",
    "PITCH_COUNT_VOLATILE",
    "OUTS_VOLATILE",
    "WORKLOAD_DECLINE",
    "LONG_LAYOFF",
    "RUN_RISK",
}


def estimate_pitcher_confidence(candidate: Candidate) -> PitcherConfidenceEstimate:
    """Estimate side win probability without changing projection, score, or tier.

    This is intentionally a provisional, price-agnostic estimate. It starts with
    an overdispersed distribution around the active projection, then shrinks the
    directional probability toward 50% when workload/sample reliability is weak.
    """
    projected_mean = _projected_mean(candidate)
    projected_sd = _projected_standard_deviation(candidate, projected_mean)
    raw_probability = _side_win_probability(
        side=candidate.side,
        line=candidate.line,
        projected_mean=projected_mean,
        projected_sd=projected_sd,
    )
    reliability_weight, flags = _reliability_weight(candidate)
    probability = 0.5 + ((raw_probability - 0.5) * reliability_weight)
    probability = min(MAX_DISPLAY_PROBABILITY, max(MIN_DISPLAY_PROBABILITY, probability))
    rounded_probability = round(probability, 3)
    percentage = int(round(rounded_probability * 100))

    return PitcherConfidenceEstimate(
        version=PITCHER_CONFIDENCE_MODEL_VERSION,
        calibration_status=CALIBRATION_STATUS,
        win_probability=rounded_probability,
        confidence_percentage=percentage,
        label=confidence_label(percentage),
        projected_mean=round(projected_mean, 3),
        projected_standard_deviation=round(projected_sd, 3),
        raw_win_probability=round(raw_probability, 3),
        reliability_weight=round(reliability_weight, 3),
        price_included=False,
        flags=flags,
    )


def confidence_label(percentage: int) -> str:
    if percentage >= 60:
        return "STRONG"
    if percentage >= 57:
        return "SOLID"
    if percentage >= 54:
        return "CAUTIOUS"
    if percentage >= 51:
        return "HIGHER RISK"
    return "NO PICK"


def _projected_mean(candidate: Candidate) -> float:
    if candidate.prop_type == PITCHER_OUTS_RECORDED:
        return max(float(candidate.projected_outs), 0.0)
    return max(float(candidate.projected_strikeouts), 0.0)


def _projected_standard_deviation(candidate: Candidate, projected_mean: float) -> float:
    stability = min(1.0, max(0.0, float(candidate.workload_stability)))
    shadow = candidate.opportunity_shadow

    if candidate.prop_type == PITCHER_OUTS_RECORDED:
        recent_sd = max(2.4, (1.0 - stability) * 4.5)
        shadow_sd = float(shadow.outs_volatility_last_5 or 0.0) if shadow else 0.0
        return max(2.4, sqrt((recent_sd**2) + (0.35 * (shadow_sd**2))))

    # Strikeouts are count data whose variance generally exceeds the Poisson
    # mean once outing length and pitcher-form uncertainty are included.
    variance_multiplier = 1.15 + ((1.0 - stability) * 0.65)
    variance = max(projected_mean, 1.0) * variance_multiplier
    if shadow and shadow.outs_volatility_last_5 is not None:
        bf_per_out = candidate.projected_batters_faced / max(candidate.projected_outs, 1.0)
        opportunity_k_sd = (
            float(shadow.outs_volatility_last_5)
            * bf_per_out
            * max(candidate.projected_k_rate, 0.0)
        )
        variance += opportunity_k_sd**2
    return max(1.5, sqrt(variance))


def _side_win_probability(
    *,
    side: str,
    line: float,
    projected_mean: float,
    projected_sd: float,
) -> float:
    if side == "UNDER":
        # An Under 5.5 wins at five or fewer; an Under 5.0 wins at four or fewer.
        continuity_boundary = ceil(line) - 0.5
        return _normal_cdf((continuity_boundary - projected_mean) / projected_sd)

    # An Over 5.5 or Over 5.0 both require six or more strikeouts to win.
    continuity_boundary = floor(line) + 0.5
    return 1.0 - _normal_cdf((continuity_boundary - projected_mean) / projected_sd)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + erf(value / sqrt(2.0)))


def _reliability_weight(candidate: Candidate) -> tuple[float, list[str]]:
    shadow = candidate.opportunity_shadow
    opportunity_reliability = (
        str(shadow.opportunity_confidence).upper() if shadow is not None else "MEDIUM"
    )
    base_weight = {"HIGH": 0.72, "MEDIUM": 0.60, "LOW": 0.45}.get(
        opportunity_reliability,
        0.55,
    )

    starts_available = int(shadow.starts_available) if shadow is not None else candidate.played_last_10
    sample_factor = 0.75 + (0.25 * min(max(starts_available, 0), 10) / 10.0)
    weight = base_weight * sample_factor

    all_flags = set(candidate.flags)
    if shadow is not None:
        all_flags.update(shadow.flags)
    severe_count = len(all_flags.intersection(_SEVERE_RISK_FLAGS))
    moderate_count = len(all_flags.intersection(_MODERATE_RISK_FLAGS))
    weight -= min(0.18, (0.055 * severe_count) + (0.025 * moderate_count))
    weight = min(0.75, max(0.25, weight))

    flags = ["UNCALIBRATED", "PRICE_NOT_INCLUDED"]
    if opportunity_reliability == "LOW":
        flags.append("LOW_WORKLOAD_RELIABILITY")
    if starts_available < 5:
        flags.append("THIN_START_SAMPLE")
    if severe_count:
        flags.append("SEVERE_RISK_SHRINKAGE")
    elif moderate_count:
        flags.append("RISK_SHRINKAGE")
    if raw_edge_against_projection(candidate) <= 0.0:
        flags.append("NO_PROJECTED_SIDE_EDGE")
    return weight, flags


def raw_edge_against_projection(candidate: Candidate) -> float:
    projected = _projected_mean(candidate)
    if candidate.side == "UNDER":
        return candidate.line - projected
    return projected - candidate.line
