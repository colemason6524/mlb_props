from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import PITCHER_STRIKEOUTS
from .models import Candidate
from .version import PITCHER_DAILY_CARD_POLICY_VERSION


# Pre-registered gates for daily-unders-card-v1. Frozen 2026-08-31 from the
# graded Aug 5-29 sample before any September data existed. Changing these
# values requires a new policy version and a separate commit.
DAILY_CARD_MAX_LINE = 5.5
DAILY_CARD_MAX_ABS_EDGE = 1.0
DAILY_CARD_LIMIT = 4
_EPSILON = 1e-9

# The card is a pre-registered evaluation, not a recommendation. It keeps
# rendering so the sample reaches the success-rule size, but every surface must
# say so. The running record is a hand-refreshed snapshot from
# pitcher_grading.daily_card_summary (the live renderer runs pre-slate with no
# grader output in scope); update it at each weekly grade.
DAILY_CARD_TRACKING_TARGET_N = 100
DAILY_CARD_RUNNING_RECORD = {
    "wins": 5,
    "losses": 10,
    "units": -6.41,  # flat 1u at recorded FanDuel prices
    "through": "2026-09-07",
}
DAILY_CARD_RESEARCH_LABEL = "RESEARCH ONLY — not a play"


def daily_card_research_label(record: dict | None = None) -> str:
    """Banner text for the card block: research-only status plus the running record.

    ``record`` accepts either the module snapshot shape (``wins``/``losses``/
    ``units``/``through``) or a ``pitcher_grading.daily_card_summary`` dict
    (``wins``/``losses``/``units_at_minus_110``). Falls back to the static
    "negative so far" wording when no record is available.
    """
    snapshot = DAILY_CARD_RUNNING_RECORD if record is None else record
    label = f"{DAILY_CARD_RESEARCH_LABEL}; tracking to n={DAILY_CARD_TRACKING_TARGET_N}"
    wins = snapshot.get("wins")
    losses = snapshot.get("losses")
    units = snapshot.get("units", snapshot.get("units_at_minus_110"))
    if wins is None or losses is None:
        return f"{label} (research only, negative so far)"
    detail = f"currently {int(wins)}-{int(losses)}"
    if units is not None:
        detail += f", {float(units):+.2f}u"
    if snapshot.get("through"):
        detail += f" through {snapshot['through']}"
    return f"{label} ({detail})"


@dataclass(frozen=True)
class DailyCardPlay:
    candidate: Candidate
    card_rank: int
    side_edge: float


def daily_card_side_edge(candidate: Candidate) -> float:
    """Side edge for UNDER card plays: line minus projected strikeouts."""
    return float(candidate.line) - float(candidate.projected_strikeouts)


def qualifies_for_daily_card(candidate: Candidate) -> bool:
    """Return whether a qualified candidate meets the pre-registered card gates."""
    if candidate.prop_type != PITCHER_STRIKEOUTS:
        return False
    if candidate.side != "UNDER":
        return False
    if float(candidate.line) > DAILY_CARD_MAX_LINE + _EPSILON:
        return False
    if abs(float(candidate.projected_strikeouts) - float(candidate.line)) > (
        DAILY_CARD_MAX_ABS_EDGE + _EPSILON
    ):
        return False
    return True


def build_daily_card(
    candidates: list[Candidate],
    limit: int = DAILY_CARD_LIMIT,
) -> list[DailyCardPlay]:
    """Select and rank the pre-registered Daily Unders Card from qualified candidates.

    The card draws from every qualified candidate, including non-displayed
    tiers, because the graded Aug 5-29 sample showed the best-performing UNDER
    segment was mostly undisplayed. Ranking is deterministic: calibrated
    confidence, then side edge, then signal balance.
    """
    eligible = [item for item in candidates if qualifies_for_daily_card(item)]
    ranked = sorted(eligible, key=_card_rank_key, reverse=True)
    return [
        DailyCardPlay(
            candidate=item,
            card_rank=index + 1,
            side_edge=round(daily_card_side_edge(item), 2),
        )
        for index, item in enumerate(ranked[: max(limit, 0)])
    ]


def _card_rank_key(candidate: Candidate) -> tuple[int, float, int]:
    estimate = candidate.confidence_estimate
    confidence = estimate.confidence_percentage if estimate is not None else 0
    return (confidence, daily_card_side_edge(candidate), candidate.score)


def daily_card_payload(card: list[DailyCardPlay]) -> list[dict]:
    """Serialize the delivered card so grading uses the exact delivered rows."""
    return [
        asdict(play.candidate)
        | {
            "card_rank": play.card_rank,
            "card_side_edge": play.side_edge,
            "history_tier": "daily_card",
        }
        for play in card
    ]


def daily_card_policy_payload() -> dict:
    """Describe the frozen policy for exports and audit."""
    return {
        "version": PITCHER_DAILY_CARD_POLICY_VERSION,
        "prop_type": PITCHER_STRIKEOUTS,
        "side": "UNDER",
        "max_line": DAILY_CARD_MAX_LINE,
        "max_abs_edge": DAILY_CARD_MAX_ABS_EDGE,
        "limit": DAILY_CARD_LIMIT,
        "market_support_gate": None,
        "preregistered_at": "2026-08-31",
    }
