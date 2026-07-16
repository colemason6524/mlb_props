from __future__ import annotations

from .config import PITCHER_OUTS_RECORDED


CORE_EDGE_MIN = 1.0
UNDER_CORE_EDGE_MIN = 1.25
VOLATILE_EDGE_MIN = 1.25
UNDER_CORE_MAX_OUTS = 18.0
UNDER_CORE_MAX_BF = 24.5


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


def core_block_reasons(item) -> list[str]:
    reasons: list[str] = []
    edge = side_projection_edge(item)
    side = getattr(item, "side", "")
    flags = set(getattr(item, "flags", []) or [])

    if edge < CORE_EDGE_MIN:
        reasons.append(f"edge {edge:.2f}/{CORE_EDGE_MIN:.2f}")

    if "VOLATILE" in flags and edge < VOLATILE_EDGE_MIN:
        reasons.append(f"volatile edge {edge:.2f}/{VOLATILE_EDGE_MIN:.2f}")

    if side == "UNDER":
        if edge < UNDER_CORE_EDGE_MIN:
            reasons.append(f"under edge {edge:.2f}/{UNDER_CORE_EDGE_MIN:.2f}")
        if float(getattr(item, "projected_outs", 0.0)) >= UNDER_CORE_MAX_OUTS:
            reasons.append(f"under outs {float(getattr(item, 'projected_outs', 0.0)):.1f}/{UNDER_CORE_MAX_OUTS:.1f}")
        if float(getattr(item, "projected_batters_faced", 0.0)) >= UNDER_CORE_MAX_BF:
            reasons.append(f"under BF {float(getattr(item, 'projected_batters_faced', 0.0)):.1f}/{UNDER_CORE_MAX_BF:.1f}")
        if flags.intersection({"DEPTH_PLUS", "WORKLOAD_PLUS", "QS_PLUS"}):
            reasons.append("under workload/depth")

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
