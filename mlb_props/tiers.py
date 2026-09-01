from __future__ import annotations

from .config import PITCHER_OUTS_RECORDED


# Keep these thresholds centralized so console output, Discord output, history
# export, and backtests all use the same Core/Lean/Watch interpretation.
CORE_EDGE_MIN = 1.0
UNDER_CORE_EDGE_MIN = 1.25
VOLATILE_EDGE_MIN = 1.25
UNDER_CORE_MAX_OUTS = 18.0
UNDER_CORE_MAX_BF = 24.5

# core-lean-watch-v2 gates, rebuilt from the graded August 5-30 sample:
# combined Core was 2-13 (15.4%) under the old shape, with large edges the
# worst segment (2.0+ edge hit 16.7%) and overs clearly weaker than unders
# (OVER 48.6% vs UNDER 59.8%). Core now requires the UNDER side, caps the
# projection edge, and demands market support when both-side prices exist.
# Unpriced lines fall back to the non-price gates so learning data still flows.
CORE_EDGE_MAX = 1.5
CORE_MIN_NO_VIG_PROBABILITY = 0.55


def projected_value(item) -> float:
    if getattr(item, "prop_type", None) == PITCHER_OUTS_RECORDED:
        return float(getattr(item, "projected_outs", 0.0))
    return float(getattr(item, "projected_strikeouts", 0.0))


def side_projection_edge(item) -> float:
    projected = projected_value(item)
    line = float(getattr(item, "line", 0.0))
    side = getattr(item, "side", "")
    if side == "UNDER":
        return line - projected
    return projected - line


def side_no_vig_probability(item) -> float | None:
    """Return the market's no-vig win probability for the item's side, if priced."""
    price_shadow = getattr(item, "price_shadow", None)
    if price_shadow is None:
        return None
    side = getattr(item, "side", "")
    if side == "UNDER":
        return getattr(price_shadow, "under_no_vig_probability", None)
    if side == "OVER":
        return getattr(price_shadow, "over_no_vig_probability", None)
    return None


def core_block_reasons(item) -> list[str]:
    reasons: list[str] = []
    edge = side_projection_edge(item)
    side = getattr(item, "side", "")
    flags = set(getattr(item, "flags", []) or [])

    if side != "UNDER":
        reasons.append(f"core requires UNDER ({side})")
    else:
        if edge < UNDER_CORE_EDGE_MIN:
            reasons.append(f"under edge {edge:.2f}/{UNDER_CORE_EDGE_MIN:.2f}")
        if float(getattr(item, "projected_outs", 0.0)) >= UNDER_CORE_MAX_OUTS:
            reasons.append(f"under outs {float(getattr(item, 'projected_outs', 0.0)):.1f}/{UNDER_CORE_MAX_OUTS:.1f}")
        if float(getattr(item, "projected_batters_faced", 0.0)) >= UNDER_CORE_MAX_BF:
            reasons.append(f"under BF {float(getattr(item, 'projected_batters_faced', 0.0)):.1f}/{UNDER_CORE_MAX_BF:.1f}")
        if flags.intersection({"DEPTH_PLUS", "WORKLOAD_PLUS", "QS_PLUS"}):
            reasons.append("under workload/depth")

    if edge > CORE_EDGE_MAX:
        reasons.append(f"edge {edge:.2f}>{CORE_EDGE_MAX:.2f}")

    if "VOLATILE" in flags and edge < VOLATILE_EDGE_MIN:
        reasons.append(f"volatile edge {edge:.2f}/{VOLATILE_EDGE_MIN:.2f}")

    no_vig = side_no_vig_probability(item)
    if no_vig is not None and no_vig < CORE_MIN_NO_VIG_PROBABILITY:
        reasons.append(f"no-vig {no_vig:.2f}<{CORE_MIN_NO_VIG_PROBABILITY:.2f}")

    return reasons


def core_eligible(item, min_score: int) -> bool:
    return int(getattr(item, "score", -999)) >= min_score and not core_block_reasons(item)


def lean_eligible(item, lean_min_score: int) -> bool:
    if int(getattr(item, "score", -999)) < lean_min_score:
        return False
    edge = side_projection_edge(item)
    flags = set(getattr(item, "flags", []) or [])
    if edge < CORE_EDGE_MIN:
        return False
    if "VOLATILE" in flags and edge < VOLATILE_EDGE_MIN:
        return False
    return True


def candidate_tier(item, min_score: int, lean_min_score: int, watch_min_score: int) -> str | None:
    if int(getattr(item, "score", -999)) < watch_min_score:
        return None
    if core_eligible(item, min_score):
        return "core"
    if lean_eligible(item, lean_min_score):
        return "lean"
    return "watch"
