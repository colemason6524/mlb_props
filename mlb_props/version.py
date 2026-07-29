from __future__ import annotations


# Increment the schema version when the shape of exported pitcher history changes.
PITCHER_HISTORY_SCHEMA_VERSION = 3

# Increment the model version only when projection inputs, formulas, or scoring
# behavior change. This version names the situational model that existed before
# the opportunity-model rebuild began.
PITCHER_MODEL_VERSION = "pitcher-k-situational-v1"

# Tier policy is versioned separately because threshold changes can alter the
# displayed board without changing the underlying projection.
PITCHER_TIER_POLICY_VERSION = "core-lean-watch-v1"

# Shadow features are collected for research but do not affect the active
# projection, score, tier, console output, or Discord output.
PITCHER_OPPORTUNITY_SHADOW_VERSION = "opportunity-shadow-v1"
