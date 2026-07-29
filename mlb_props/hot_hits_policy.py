from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Iterable, Protocol, TypeVar


HOT_HITS_POLICY_VERSION = "current-v1"
CORE_FIRST_POLICY_VERSION = "core-first-v1"
HOT_HITS_CARD_POLICIES = {
    HOT_HITS_POLICY_VERSION,
    CORE_FIRST_POLICY_VERSION,
}


@dataclass(frozen=True)
class HotHitScoringInput:
    avg_last_5: float
    avg_last_10: float
    season_avg: float
    hit_games_last_5: int
    hit_games_last_10: int
    batting_order: int | None
    matchup_rating: float
    pitcher_hits_allowed_rate_last_5: float
    pitcher_k_rate_last_5: float
    pitcher_walk_rate_last_5: float
    pitcher_has_data: bool
    batter_vs_pitcher_ab: int
    batter_vs_pitcher_avg: float | None
    batter_vs_pitcher_available: bool


@dataclass(frozen=True)
class HotHitScore:
    score: int
    flags: list[str]


class HotHitPolicyCandidate(Protocol):
    score: int
    batting_order: int | None
    matchup_rating: float
    pitcher_hits_allowed_rate_last_5: float
    season_avg: float
    avg_last_5: float
    hit_games_last_5: int


@dataclass(frozen=True)
class HotHitPolicySnapshot:
    score: int
    batting_order: int | None
    matchup_rating: float
    pitcher_hits_allowed_rate_last_5: float
    season_avg: float
    avg_last_5: float
    hit_games_last_5: int

    @classmethod
    def from_mapping(cls, row: dict) -> "HotHitPolicySnapshot":
        batting_order = row.get("batting_order")
        return cls(
            score=int(row.get("score", 0) or 0),
            batting_order=int(batting_order) if batting_order is not None else None,
            matchup_rating=float(row.get("matchup_rating", 0.0) or 0.0),
            pitcher_hits_allowed_rate_last_5=float(
                row.get("pitcher_hits_allowed_rate_last_5", 0.0) or 0.0
            ),
            season_avg=float(row.get("season_avg", 0.0) or 0.0),
            avg_last_5=float(row.get("avg_last_5", 0.0) or 0.0),
            hit_games_last_5=int(row.get("hit_games_last_5", 0) or 0),
        )


TPolicyCandidate = TypeVar("TPolicyCandidate", bound=HotHitPolicyCandidate)


@dataclass(frozen=True)
class HotHitsCardSelection(Generic[TPolicyCandidate]):
    core: list[TPolicyCandidate]
    value: list[TPolicyCandidate]
    thin: list[TPolicyCandidate]

    @property
    def shown(self) -> list[TPolicyCandidate]:
        return self.core + self.value + self.thin


def score_hot_hit_candidate(
    inputs: HotHitScoringInput,
    *,
    strong_hot_avg: float = 0.400,
) -> HotHitScore:
    score = 0
    flags: list[str] = []

    if inputs.hit_games_last_5 >= 5:
        score += 2
    elif inputs.hit_games_last_5 >= 4:
        score += 1

    if inputs.hit_games_last_10 >= 8:
        score += 2
        flags.append("HIT_STREAK")
    elif inputs.hit_games_last_10 >= 7:
        score += 1

    if inputs.avg_last_5 >= strong_hot_avg:
        score += 3
        flags.append("VERY_HOT")
    else:
        score += 2
        flags.append("HOT")

    if inputs.avg_last_10 >= 0.320:
        score += 1
    if inputs.season_avg >= 0.285:
        score += 2
        flags.append("SEASON_PLUS")
    elif inputs.season_avg >= 0.260:
        score += 1

    if inputs.avg_last_5 - inputs.season_avg >= 0.080:
        score += 1
        flags.append("TREND_SPIKE")
    elif inputs.avg_last_5 < inputs.season_avg:
        score -= 1

    if inputs.batting_order is not None and inputs.batting_order <= 4:
        score += 1
        flags.append("ORDER_TOP")
    elif inputs.batting_order is not None and 5 <= inputs.batting_order <= 7:
        if (
            inputs.matchup_rating < 0.0
            and inputs.pitcher_hits_allowed_rate_last_5 < 0.260
        ):
            score -= 1
        flags.append("ORDER_VALUE")
    elif inputs.batting_order is not None and inputs.batting_order >= 8:
        score -= 2
        flags.append("ORDER_LOW")

    if inputs.matchup_rating >= 0.20:
        score += 3
        flags.append("MATCHUP_PLUS")
    elif inputs.matchup_rating <= -0.20:
        score -= 2
        flags.append("MATCHUP_MINUS")

    if inputs.pitcher_hits_allowed_rate_last_5 >= 0.280:
        score += 2
        flags.append("PITCHER_HITS")
    elif inputs.pitcher_hits_allowed_rate_last_5 >= 0.260:
        score += 1
        flags.append("PITCHER_HITS")
    elif inputs.pitcher_hits_allowed_rate_last_5 <= 0.220 and inputs.pitcher_has_data:
        score -= 1

    if inputs.pitcher_k_rate_last_5 <= 0.200 and inputs.pitcher_has_data:
        score += 1
        flags.append("CONTACT_PLUS")
    elif inputs.pitcher_k_rate_last_5 >= 0.285:
        score -= 1
        flags.append("PITCHER_K_RISK")

    if inputs.pitcher_walk_rate_last_5 >= 0.095:
        score -= 1
        flags.append("WALK_RISK")

    if inputs.batter_vs_pitcher_available:
        if (
            inputs.batter_vs_pitcher_ab >= 8
            and (inputs.batter_vs_pitcher_avg or 0.0) >= 0.300
        ):
            score += 1
            flags.append("BVP_PLUS")
        elif 0 < inputs.batter_vs_pitcher_ab < 8:
            flags.append("BVP_THIN")
        elif (
            inputs.batter_vs_pitcher_ab >= 8
            and (inputs.batter_vs_pitcher_avg or 0.0) <= 0.180
        ):
            score -= 1
            flags.append("BVP_MINUS")

    return HotHitScore(score=score, flags=flags)


def hot_hit_support_count(item: HotHitPolicyCandidate) -> int:
    return sum(
        [
            bool(item.batting_order is not None and item.batting_order <= 4),
            item.matchup_rating >= 0.20,
            item.pitcher_hits_allowed_rate_last_5 >= 0.260,
            item.season_avg >= 0.280,
            item.avg_last_5 >= 0.380,
        ]
    )


def hot_hit_tier(item: HotHitPolicyCandidate) -> str:
    batting_order = item.batting_order or 99
    low_order = batting_order >= 8
    value_order = 5 <= batting_order <= 7
    matchup_floor = item.matchup_rating > -0.20
    strong_contact_spot = (
        item.pitcher_hits_allowed_rate_last_5 >= 0.280
        or item.matchup_rating >= 0.20
    )
    hot_hand = item.avg_last_5 >= 0.400 or item.hit_games_last_5 >= 5
    support_count = hot_hit_support_count(item)

    if item.score >= 14 and batting_order <= 4 and matchup_floor and support_count >= 2:
        return "Core"
    if (
        item.score >= 12
        and hot_hand
        and matchup_floor
        and not low_order
        and support_count >= 2
    ):
        return "Value"
    if (
        item.score >= 11
        and value_order
        and hot_hand
        and strong_contact_spot
        and support_count >= 2
    ):
        return "Value"
    return "Thin"


def hot_hit_discord_eligible(
    item: HotHitPolicyCandidate,
    min_score: int = 10,
) -> bool:
    batting_order = item.batting_order or 99
    support_count = hot_hit_support_count(item)
    if item.score >= 14 and support_count >= 1 and batting_order <= 7:
        return True
    if item.score >= max(min_score, 12) and support_count >= 2 and batting_order <= 7:
        return True
    return False


def hot_hit_discord_sort_key(
    item: HotHitPolicyCandidate,
) -> tuple[int, int, int, float, float, float, int]:
    batting_order = item.batting_order or 99
    return (
        hot_hit_support_count(item),
        item.score,
        1 if batting_order <= 4 else 0,
        item.matchup_rating,
        item.pitcher_hits_allowed_rate_last_5,
        item.season_avg,
        -batting_order,
    )


def select_current_hot_hits_card(
    candidates: Iterable[TPolicyCandidate],
    *,
    limit: int = 6,
    min_score: int = 10,
) -> HotHitsCardSelection[TPolicyCandidate]:
    eligible = [
        item for item in candidates if hot_hit_discord_eligible(item, min_score=min_score)
    ]
    eligible = sorted(eligible, key=hot_hit_discord_sort_key, reverse=True)
    core = [item for item in eligible if hot_hit_tier(item) == "Core"]
    value = [item for item in eligible if hot_hit_tier(item) == "Value"]
    thin = [item for item in eligible if hot_hit_tier(item) == "Thin"]
    shown_core = core[:limit]
    shown_value = value[: max(0, limit - len(shown_core))]
    shown_thin = thin[: max(0, limit - len(shown_core) - len(shown_value))]
    return HotHitsCardSelection(
        core=shown_core,
        value=shown_value,
        thin=shown_thin,
    )


def select_core_first_hot_hits_card(
    candidates: Iterable[TPolicyCandidate],
    *,
    core_limit: int = 4,
    value_limit: int = 2,
    min_score: int = 10,
) -> HotHitsCardSelection[TPolicyCandidate]:
    eligible = [
        item for item in candidates if hot_hit_discord_eligible(item, min_score=min_score)
    ]
    eligible = sorted(eligible, key=hot_hit_discord_sort_key, reverse=True)
    core = [item for item in eligible if hot_hit_tier(item) == "Core"]
    value = [item for item in eligible if hot_hit_tier(item) == "Value"]
    shown_core = core[: max(0, core_limit)]
    available_value_slots = min(
        max(0, value_limit),
        max(0, core_limit - len(shown_core)),
    )
    shown_value = value[:available_value_slots]
    return HotHitsCardSelection(
        core=shown_core,
        value=shown_value,
        thin=[],
    )


def select_hot_hits_card(
    candidates: Iterable[TPolicyCandidate],
    *,
    card_policy: str = CORE_FIRST_POLICY_VERSION,
    limit: int = 4,
    value_limit: int = 2,
    min_score: int = 10,
) -> HotHitsCardSelection[TPolicyCandidate]:
    if card_policy == CORE_FIRST_POLICY_VERSION:
        return select_core_first_hot_hits_card(
            candidates,
            core_limit=limit,
            value_limit=value_limit,
            min_score=min_score,
        )
    if card_policy == HOT_HITS_POLICY_VERSION:
        return select_current_hot_hits_card(
            candidates,
            limit=limit,
            min_score=min_score,
        )
    raise ValueError(f"Unsupported Hot Hits card policy: {card_policy}")
