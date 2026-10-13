"""Statcast feature prototypes for the MLB game model.

PROTOTYPE STATUS: not wired into production. These functions consume
PitcherStatcastProfile / TeamLineupStatcast objects from mlb_props.statcast
and produce model-ready features. They will be validated and shipped in
spring when there's volume.

Features:
- stuff_plus: velocity + spin + whiff composite (z-scored vs league)
- pitch_mix_edge: SP's pitch mix vs opposing lineup's xwOBA by pitch family
- bullpen_statcast_edge: trailing reliever xwOBA allowed, leverage-weighted
- framing_runs: catcher framing estimate (proxy until direct data available)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .statcast import PitcherStatcastProfile, TeamLineupStatcast

# League-average baselines for stuff+ z-scoring. These are initial priors
# from 2024-2026 Statcast; they get refreshed from our own collected data
# once we have 30+ days of profiles. Units noted per field.
LEAGUE_STUFF_BASELINES = {
    # mean, sd
    "velo_ff": (93.5, 2.1),      # mph
    "spin_ff": (2250.0, 180.0),  # rpm
    "spin_br": (2450.0, 250.0),  # rpm
    "whiff_pct": (0.28, 0.05),   # fraction
    "chase_pct": (0.30, 0.05),   # fraction
}

# Weights for the stuff+ composite. Whiff% is the most predictive of the
# five in the literature; velocity second.
STUFF_WEIGHTS = {
    "velo_ff": 0.25,
    "spin_ff": 0.10,
    "spin_br": 0.10,
    "whiff_pct": 0.40,
    "chase_pct": 0.15,
}

# xwOBA baselines by pitch family for the mix-edge calculation.
LEAGUE_XWOBA_BY_FAMILY = {
    "fastball": 0.340,
    "breaking": 0.310,
    "offspeed": 0.325,
}


def _z(value: float | None, mean: float, sd: float) -> float | None:
    if value is None or sd == 0:
        return None
    return (value - mean) / sd


def stuff_plus(profile: PitcherStatcastProfile) -> float | None:
    """Composite stuff metric, roughly 100 = league average.

    Z-scores each component vs league baselines, takes the weighted sum,
    and scales to a 100-centered index. Returns None when the profile is
    unreliable or missing key inputs.
    """
    if not profile.reliable:
        return None
    inputs = {
        "velo_ff": profile.avg_velo_ff,
        "spin_ff": profile.avg_spin_ff,
        "spin_br": profile.avg_spin_br,
        "whiff_pct": profile.whiff_pct,
        "chase_pct": profile.chase_pct,
    }
    total_w, acc = 0.0, 0.0
    for key, weight in STUFF_WEIGHTS.items():
        mean, sd = LEAGUE_STUFF_BASELINES[key]
        z = _z(inputs.get(key), mean, sd)
        if z is None:
            continue
        acc += weight * z
        total_w += weight
    if total_w < 0.5:  # need at least half the components
        return None
    # Renormalize for missing components, scale to 100-centered.
    composite_z = acc / total_w
    return 100.0 + 12.0 * composite_z


def pitch_mix_edge(
    sp_profile: PitcherStatcastProfile,
    lineup: TeamLineupStatcast,
) -> float | None:
    """Expected xwOBA edge from the SP's pitch mix vs the lineup's weaknesses.

    Computes the SP's mix-weighted expected xwOBA using the *lineup's*
    xwOBA by pitch family (their weakness), minus the league-average
    expectation for that same mix. Negative = favorable for the pitcher
    (lineup hits this mix worse than average).

    Returns runs-ish units (xwOBA points * ~1.0 scale). None when inputs
    are missing.
    """
    if not sp_profile.reliable:
        return None
    mix = {
        "fastball": sp_profile.mix_fastball,
        "breaking": sp_profile.mix_breaking,
        "offspeed": sp_profile.mix_offspeed,
    }
    lineup_xwoba = {
        "fastball": lineup.xwoba_vs_fastball,
        "breaking": lineup.xwoba_vs_breaking,
        "offspeed": lineup.xwoba_vs_offspeed,
    }
    exp_vs_lineup, exp_vs_league, total_w = 0.0, 0.0, 0.0
    for fam in ("fastball", "breaking", "offspeed"):
        w = mix.get(fam)
        lx = lineup_xwoba.get(fam)
        lg = LEAGUE_XWOBA_BY_FAMILY[fam]
        if w is None or lx is None:
            continue
        exp_vs_lineup += w * lx
        exp_vs_league += w * lg
        total_w += w
    if total_w < 0.5:
        return None
    # Negative = pitcher-favorable (lineup worse than average vs this mix).
    return (exp_vs_lineup - exp_vs_league) / total_w


@dataclass
class BullpenStatcastEdge:
    """Leverage-weighted bullpen quality from Statcast."""

    team: str
    as_of: str
    reliever_xwoba_allowed: float | None = None  # trailing, leverage-weighted
    n_relievers: int = 0
    reliable: bool = False


def bullpen_statcast_edge(
    reliever_profiles: list[PitcherStatcastProfile],
    leverage_weights: list[float] | None = None,
) -> BullpenStatcastEdge | None:
    """Aggregate reliever xwOBA allowed, weighted by leverage.

    reliever_profiles: trailing profiles for the team's relievers.
    leverage_weights: per-reliever leverage index (1.0 = average). When
        omitted, weights are uniform.
    """
    if not reliever_profiles:
        return None
    if leverage_weights is None:
        leverage_weights = [1.0] * len(reliever_profiles)
    num, den, n = 0.0, 0.0, 0
    for prof, lev in zip(reliever_profiles, leverage_weights):
        if prof.xwoba_allowed is None or not prof.reliable:
            continue
        # Weight by both leverage and pitch volume (more pitches = more trust).
        w = lev * math.log1p(prof.total_pitches)
        num += w * prof.xwoba_allowed
        den += w
        n += 1
    if den == 0:
        return None
    return BullpenStatcastEdge(
        team="",
        as_of=reliever_profiles[0].as_of,
        reliever_xwoba_allowed=num / den,
        n_relievers=n,
        reliable=n >= 3,
    )


# Catcher framing: pybaseball's Statcast feed does not include framing runs
# directly. This is a documented placeholder — the real implementation will
# scrape Baseball Savant's catcher leaderboard (free, no key) in spring.
# For now we expose the interface so the model code can reference it.
def framing_runs_proxy(
    catcher_id: int | None,
    strikes_called_oz: int = 0,
    balls_called_iz: int = 0,
) -> float | None:
    """Placeholder framing estimate.

    The honest version needs pitch-level zone data per catcher, which we
    can derive from our collected Statcast (catcher isn't in the feed
    though — needs the Savant leaderboard scrape). Until then, returns
    None rather than a fabricated number.
    """
    _ = (catcher_id, strikes_called_oz, balls_called_iz)
    return None
