from __future__ import annotations

from .models import HotHitCandidate, HotHitConfidenceEstimate


HOT_HITS_CONFIDENCE_MODEL_VERSION = "hot-hits-confidence-provisional-v1"
CALIBRATION_STATUS = "PROVISIONAL"
MIN_DISPLAY_PROBABILITY = 0.45
MAX_DISPLAY_PROBABILITY = 0.88


def estimate_hot_hit_confidence(candidate: HotHitCandidate) -> HotHitConfidenceEstimate:
    """Estimate one-hit probability without changing score, tier, or Discord selection."""
    contact = candidate.contact_quality_shadow
    flags = ["UNCALIBRATED", "PRICE_NOT_INCLUDED"]

    if contact is not None and contact.season_xba is not None:
        season_anchor = float(contact.season_xba)
        recent_anchor = (
            float(contact.xba_last_10_games)
            if contact.xba_last_10_games is not None
            else season_anchor
        )
        recent_opportunities = int(contact.xba_opportunities_last_10_games)
        reliability = {"high": 0.85, "medium": 0.70, "low": 0.55}.get(
            str(contact.confidence).lower(),
            0.50,
        )
        if str(contact.confidence).lower() == "low":
            flags.append("LOW_CONTACT_RELIABILITY")
    else:
        season_anchor = float(candidate.season_avg)
        recent_anchor = float(candidate.avg_last_10)
        recent_opportunities = int(candidate.at_bats_last_10)
        reliability = 0.40
        flags.extend(["CONTACT_DATA_UNAVAILABLE", "RESULTS_BASED_FALLBACK"])

    recent_weight = min(
        0.35,
        recent_opportunities / (recent_opportunities + 45.0),
    )
    per_at_bat = (
        ((1.0 - recent_weight) * season_anchor)
        + (recent_weight * recent_anchor)
    )

    # Matchup adjustment is deliberately small because the rating already blends
    # several correlated starter statistics and is not yet calibrated for hit props.
    matchup_adjustment = min(0.015, max(-0.015, float(candidate.matchup_rating) * 0.03))
    per_at_bat = min(0.500, max(0.120, per_at_bat + matchup_adjustment))

    recent_expected_ab = (
        float(contact.expected_at_bats)
        if contact is not None and contact.expected_at_bats is not None
        else (
            candidate.at_bats_last_10 / 10.0
            if candidate.at_bats_last_10 > 0
            else candidate.at_bats_last_5 / 5.0
        )
    )
    order_target = _batting_order_at_bat_target(candidate.batting_order)
    expected_at_bats = (0.65 * recent_expected_ab) + (0.35 * order_target)
    expected_at_bats = min(5.0, max(2.5, expected_at_bats))

    raw_probability = 1.0 - ((1.0 - per_at_bat) ** expected_at_bats)
    season_probability = 1.0 - ((1.0 - min(0.500, max(0.120, season_anchor))) ** expected_at_bats)
    probability = season_probability + ((raw_probability - season_probability) * reliability)
    probability = min(MAX_DISPLAY_PROBABILITY, max(MIN_DISPLAY_PROBABILITY, probability))
    rounded_probability = round(probability, 3)
    percentage = int(round(rounded_probability * 100))

    if not candidate.current_gate_qualified:
        flags.append("CURRENT_GATE_FAIL")
    if candidate.batting_order is None:
        flags.append("BATTING_ORDER_UNCERTAIN")
    if candidate.batting_order is not None and candidate.batting_order >= 6:
        flags.append("LOWER_ORDER_OPPORTUNITY")

    return HotHitConfidenceEstimate(
        version=HOT_HITS_CONFIDENCE_MODEL_VERSION,
        calibration_status=CALIBRATION_STATUS,
        hit_probability=rounded_probability,
        confidence_percentage=percentage,
        label=confidence_label(percentage),
        per_at_bat_probability=round(per_at_bat, 3),
        season_anchor_probability=round(season_probability, 3),
        expected_at_bats=round(expected_at_bats, 2),
        reliability_weight=round(reliability, 3),
        price_included=False,
        flags=flags,
    )


def confidence_label(percentage: int) -> str:
    if percentage >= 78:
        return "STRONG"
    if percentage >= 72:
        return "SOLID"
    if percentage >= 66:
        return "CAUTIOUS"
    if percentage >= 60:
        return "HIGHER RISK"
    return "NO PICK"


def hot_hit_confidence_sort_key(candidate: HotHitCandidate) -> tuple[float, float, int, int, float]:
    estimate = candidate.confidence_estimate or estimate_hot_hit_confidence(candidate)
    return (
        estimate.hit_probability,
        estimate.reliability_weight,
        1 if candidate.current_display_qualified else 0,
        -(candidate.batting_order or 99),
        candidate.season_avg,
    )


def _batting_order_at_bat_target(batting_order: int | None) -> float:
    return {
        1: 4.5,
        2: 4.4,
        3: 4.2,
        4: 4.1,
        5: 4.0,
        6: 3.8,
        7: 3.7,
        8: 3.5,
        9: 3.4,
    }.get(batting_order, 3.8)
