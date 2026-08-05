from __future__ import annotations


# Increment the schema version when the shape of exported pitcher history changes.
PITCHER_HISTORY_SCHEMA_VERSION = 5

# Increment the model version only when projection inputs, formulas, or scoring
# behavior change. This version names the situational model that existed before
# the opportunity-model rebuild began.
PITCHER_MODEL_VERSION = "pitcher-k-situational-v1"

# Tier policy is versioned separately because threshold changes can alter the
# displayed board without changing the underlying projection.
PITCHER_TIER_POLICY_VERSION = "core-lean-watch-v1"

# Shadow features are collected for research and may supply a display-only
# opportunity-reliability label. They do not affect projections, scores, or tiers.
PITCHER_OPPORTUNITY_SHADOW_VERSION = "opportunity-shadow-v1"

# Confidence is versioned independently because it is a provisional,
# price-agnostic probability layer and does not change the active projection.
PITCHER_CONFIDENCE_MODEL_VERSION = "pitcher-confidence-provisional-v1"

# The display policy is versioned separately because slate rank and user-facing
# labels can change without changing the underlying recommendation model.
PITCHER_DISPLAY_POLICY_VERSION = "provisional-confidence-rank-v1"
