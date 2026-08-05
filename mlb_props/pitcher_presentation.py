from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import Candidate, PitcherConfidenceEstimate
from .pitcher_confidence import estimate_pitcher_confidence
from .tiers import candidate_tier, side_projection_edge


BEST_AVAILABLE_LIMIT = 3


@dataclass(frozen=True)
class PitcherPresentation:
    candidate: Candidate
    recommendation_tier: str
    slate_rank: int
    display_role: str
    opportunity_reliability: str
    confidence_estimate: PitcherConfidenceEstimate


def build_pitcher_presentations(
    candidates: Iterable[Candidate],
    *,
    min_score: int,
    lean_min_score: int,
    watch_min_score: int,
    best_available_limit: int = BEST_AVAILABLE_LIMIT,
) -> list[PitcherPresentation]:
    """Rank eligible plays without changing their absolute recommendation tier."""
    tiered: list[tuple[Candidate, str, PitcherConfidenceEstimate]] = []
    for candidate in candidates:
        tier = candidate_tier(candidate, min_score, lean_min_score, watch_min_score)
        if tier is not None:
            estimate = candidate.confidence_estimate or estimate_pitcher_confidence(candidate)
            tiered.append((candidate, tier, estimate))

    tiered.sort(key=lambda item: _pitcher_rank_key(item[0], item[2]), reverse=True)
    has_core = any(tier == "core" for _, tier, _ in tiered)

    presentations: list[PitcherPresentation] = []
    for index, (candidate, tier, estimate) in enumerate(tiered, start=1):
        display_role = tier
        if not has_core and index <= max(best_available_limit, 0):
            display_role = "best_available"
        presentations.append(
            PitcherPresentation(
                candidate=candidate,
                recommendation_tier=tier,
                slate_rank=index,
                display_role=display_role,
                opportunity_reliability=opportunity_reliability(candidate),
                confidence_estimate=estimate,
            )
        )
    return presentations


def opportunity_reliability(candidate: object) -> str:
    """Return a display-only workload reliability label, never a tier input."""
    shadow = getattr(candidate, "opportunity_shadow", None)
    if shadow is not None:
        confidence = str(shadow.opportunity_confidence or "").upper()
        if confidence in {"HIGH", "MEDIUM", "LOW"}:
            return confidence

    pitch_count = float(getattr(candidate, "avg_pitch_count_last_5", 0.0))
    short_starts = int(
        getattr(
            candidate,
            "short_starts_last_10",
            getattr(candidate, "short_starts_recent", 0),
        )
    )
    workload_stability = float(
        getattr(
            candidate,
            "workload_stability",
            getattr(candidate, "workload_stability_ks", 0.0),
        )
    )

    if pitch_count <= 85.0 or short_starts >= 2 or workload_stability <= 0.42:
        return "LOW"
    if pitch_count >= 95.0 and short_starts == 0 and workload_stability >= 0.72:
        return "HIGH"
    return "MEDIUM"


def display_rankings_payload(
    presentations: Iterable[PitcherPresentation],
) -> list[dict[str, object]]:
    """Save the exact user-facing ranking without duplicating full candidates."""
    payload: list[dict[str, object]] = []
    for presentation in presentations:
        candidate = presentation.candidate
        payload.append(
            {
                "slate_rank": presentation.slate_rank,
                "recommendation_tier": presentation.recommendation_tier,
                "display_role": presentation.display_role,
                "opportunity_reliability": presentation.opportunity_reliability,
                "confidence_model_version": presentation.confidence_estimate.version,
                "calibration_status": presentation.confidence_estimate.calibration_status,
                "confidence_percentage": presentation.confidence_estimate.confidence_percentage,
                "confidence_label": presentation.confidence_estimate.label,
                "win_probability": presentation.confidence_estimate.win_probability,
                "price_included": presentation.confidence_estimate.price_included,
                "subject_name": candidate.subject_name,
                "subject_id": candidate.subject_id,
                "prop_type": candidate.prop_type,
                "side": candidate.side,
                "line": candidate.line,
                "side_projection_edge": round(side_projection_edge(candidate), 3),
                "signal_balance": candidate.score,
            }
        )
    return payload


def _pitcher_rank_key(
    candidate: Candidate,
    estimate: PitcherConfidenceEstimate,
) -> tuple[float, int, float, float, int, float]:
    return (
        estimate.win_probability,
        candidate.score,
        side_projection_edge(candidate),
        candidate.projected_outs,
        candidate.hits_last_5,
        candidate.delta_avg_last_5,
    )
