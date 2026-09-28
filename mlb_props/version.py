from __future__ import annotations

from datetime import date as _date


# Increment the schema version when the shape of exported pitcher history changes.
# Schema 8 (2026-08-31) is additive: exports gain a `daily_card` array and a
# `daily_card_policy_version` string. Nothing existing is renamed or removed.
PITCHER_HISTORY_SCHEMA_VERSION = 8

# Increment the model version only when projection inputs, formulas, or scoring
# behavior change. pitcher-k-hybrid-v2 activates the recency-shadow aggregate
# K/BF blend (50% season, 30% L10, 20% L5) as the production K-rate while
# keeping the situational opportunity projection for outs and batters faced.
# Validation: graded schema-6 sample, shadow K MAE 1.89 vs active 1.95,
# date-blocked bootstrap P(shadow worse) = 0.4%.
PITCHER_MODEL_VERSION = "pitcher-k-hybrid-v2"

# Tier policy is versioned separately because threshold changes can alter the
# displayed board without changing the underlying projection.
# core-lean-watch-v2: Core requires the UNDER side, caps projection edge at 1.5,
# and requires market support (side no-vig probability >= 0.55) when both-side
# prices are available. Evidence: combined Aug 5-30 Core 2-13 (15.4%),
# 2.0+ edge hit 16.7%, OVER 48.6% vs UNDER 59.8%.
PITCHER_TIER_POLICY_VERSION = "core-lean-watch-v2"

# Shadow features are collected for research and may supply a display-only
# opportunity-reliability label. They do not affect projections, scores, or tiers.
PITCHER_OPPORTUNITY_SHADOW_VERSION = "opportunity-shadow-v1"

# Recency research is versioned separately because it compares an alternative
# K-rate/BF projection without changing the active production recommendation.
PITCHER_RECENCY_SHADOW_VERSION = "recency-shadow-v1"

# Confidence is versioned independently because it is a price-agnostic
# probability layer and does not change the active projection.
# pitcher-confidence-calibrated-v2 applies a first-pass calibration shrink
# (0.55, display capped at 57%) fitted on the graded schema-6 sample after the
# old top bands observed 15-32 points below their forecasts.
PITCHER_CONFIDENCE_MODEL_VERSION = "pitcher-confidence-calibrated-v2"

# The display policy is versioned separately because slate rank and user-facing
# labels can change without changing the underlying recommendation model.
PITCHER_DISPLAY_POLICY_VERSION = "provisional-confidence-rank-v1"

# The forecast board is a research-only capture of every evaluated prop line,
# including non-qualifying ones. It never affects production output.
PITCHER_FORECAST_BOARD_VERSION = "forecast-board-v1"

# The price shadow captures both-side sportsbook prices when a source exposes
# them. It is observation-only and never used for EV or staking claims.
PITCHER_PRICE_SHADOW_VERSION = "price-shadow-v1"

# The Daily Card is a pre-registered segment policy, not a Core/Lean/Watch tier.
# Gates frozen 2026-08-31 from the graded Aug 5-29 sample (n=97, 61.9% hit,
# stable 61.8%/61.9% across both windows): PITCHER_STRIKEOUTS, UNDER side,
# line <= 5.5, |projected Ks - line| <= 1.0, ranked by calibrated confidence,
# capped at 4. The no-vig market-support gate is deferred to a v2 policy once
# price history accumulates. Success rule for September: trust at >= 55% with
# n >= 100; marginal at 52.4-55%; kill below 52.4%.
PITCHER_DAILY_CARD_POLICY_VERSION = "daily-unders-card-v1"

# Game-level market shadow collection (moneyline / run line / game total).
# History shape changes bump the schema version; the market baseline is an
# observation-only no-vig conversion of collected prices, not a model opinion.
GAME_MARKETS_HISTORY_SCHEMA_VERSION = 2
GAME_MARKETS_PRICE_SHADOW_VERSION = "game-price-shadow-v1"
GAME_MARKETS_SOURCE_POLICY_VERSION = "bovada-primary-fanduel-nonwhole-total-preferred-v2"

# Hot Hits pricing shadow: single-sided Bovada YES prices for "Player to
# record a Hit" (plus the 2+ Hits alt line) captured per event at run time.
# Observation-only research prices; never used for EV, gating, or Discord.
HOT_HITS_PRICE_SHADOW_VERSION = "hot-hits-price-shadow-v1"

# --- Evidence eras (2026) ---------------------------------------------------
# The 2026 regular season is a CLOSED sample. Its graded window is frozen at the
# dates below: findings, calibration, and slice rates fitted there stay the
# basis for selection, and postseason results never revise them. Postseason
# observations accumulate in parallel under their own era label and are only
# promoted to the basis once they are large enough to stand alone.
#
# The graded sample starts 2026-09-10, the first date the full-board grading
# workflow wrote forecast_board_*/learning_review_* artifacts. Earlier dates
# exist as pitcher-only snapshots and are not part of the full-board sample.
REGULAR_SEASON_2026_SAMPLE_START = "2026-09-10"
REGULAR_SEASON_2026_SAMPLE_END = "2026-09-27"
POSTSEASON_2026_START = "2026-09-29"


def evidence_era(screen_date: str | None) -> str | None:
    """Return the evidence era for an ISO `screen_date`, or None when undated.

    Eras are reporting labels only: they separate the closed regular-season
    sample from postseason results so the two are never blended by accident.
    They do not change projections, scores, tiers, or selection.
    """
    if screen_date is None:
        return None
    try:
        day = _date.fromisoformat(str(screen_date)[:10])
        regular_end = _date.fromisoformat(REGULAR_SEASON_2026_SAMPLE_END)
        postseason_start = _date.fromisoformat(POSTSEASON_2026_START)
    except ValueError:
        return None
    if day <= regular_end:
        return "regular_season"
    if day >= postseason_start:
        return "postseason"
    return "between"
