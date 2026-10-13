"""Tests for Statcast feature prototypes (mlb_props/statcast_features.py)."""
from __future__ import annotations

import unittest

from mlb_props.statcast import PitcherStatcastProfile, TeamLineupStatcast
from mlb_props.statcast_features import (
    bullpen_statcast_edge,
    framing_runs_proxy,
    pitch_mix_edge,
    stuff_plus,
)


def _sp(**overrides):
    base = dict(
        pitcher_id=1,
        as_of="2026-10-13",
        total_pitches=500,
        avg_velo_ff=93.5,
        avg_spin_ff=2250.0,
        avg_spin_br=2450.0,
        whiff_pct=0.28,
        chase_pct=0.30,
        mix_fastball=0.5,
        mix_breaking=0.3,
        mix_offspeed=0.2,
        reliable=True,
    )
    base.update(overrides)
    return PitcherStatcastProfile(**base)


class TestStuffPlus(unittest.TestCase):
    def test_league_average_is_100(self):
        # All inputs at league baseline -> stuff+ ~ 100
        self.assertAlmostEqual(stuff_plus(_sp()), 100.0, places=1)

    def test_power_pitcher_above_100(self):
        p = _sp(avg_velo_ff=98.0, whiff_pct=0.36)
        self.assertGreater(stuff_plus(p), 110.0)

    def test_soft_tosser_below_100(self):
        p = _sp(avg_velo_ff=89.0, whiff_pct=0.20)
        self.assertLess(stuff_plus(p), 95.0)

    def test_unreliable_returns_none(self):
        self.assertIsNone(stuff_plus(_sp(reliable=False)))

    def test_missing_inputs_returns_none(self):
        p = _sp(avg_velo_ff=None, avg_spin_ff=None, avg_spin_br=None,
                whiff_pct=None, chase_pct=None)
        self.assertIsNone(stuff_plus(p))


class TestPitchMixEdge(unittest.TestCase):
    def _lineup(self, fb=0.340, br=0.310, off=0.325):
        return TeamLineupStatcast(
            team="TST", as_of="2026-10-13",
            xwoba_vs_fastball=fb, xwoba_vs_breaking=br, xwoba_vs_offspeed=off)

    def test_neutral_lineup_is_zero(self):
        edge = pitch_mix_edge(_sp(), self._lineup())
        self.assertAlmostEqual(edge, 0.0, places=4)

    def test_pitcher_favorable_is_negative(self):
        # Lineup can't hit fastballs; SP throws 60% fastballs
        edge = pitch_mix_edge(_sp(mix_fastball=0.6, mix_breaking=0.3, mix_offspeed=0.1),
                              self._lineup(fb=0.290))
        self.assertLess(edge, 0.0)

    def test_unfavorable_is_positive(self):
        # Lineup crushes fastballs; SP is fastball-heavy
        edge = pitch_mix_edge(_sp(mix_fastball=0.7, mix_breaking=0.2, mix_offspeed=0.1),
                              self._lineup(fb=0.390))
        self.assertGreater(edge, 0.0)

    def test_unreliable_sp_returns_none(self):
        self.assertIsNone(pitch_mix_edge(_sp(reliable=False), self._lineup()))


class TestBullpenEdge(unittest.TestCase):
    def test_weighted_average(self):
        p1 = _sp(pitcher_id=1, xwoba_allowed=0.280, total_pitches=300)
        p2 = _sp(pitcher_id=2, xwoba_allowed=0.340, total_pitches=300)
        # Equal leverage -> simple average (log weights cancel when equal)
        edge = bullpen_statcast_edge([p1, p2])
        self.assertIsNotNone(edge)
        self.assertAlmostEqual(edge.reliever_xwoba_allowed, 0.310, places=3)
        self.assertFalse(edge.reliable)  # only 2 relievers

    def test_leverage_weighting(self):
        p1 = _sp(pitcher_id=1, xwoba_allowed=0.280, total_pitches=300)
        p2 = _sp(pitcher_id=2, xwoba_allowed=0.360, total_pitches=300)
        # p1 gets 3x leverage -> pulls average down
        edge = bullpen_statcast_edge([p1, p2], leverage_weights=[3.0, 1.0])
        self.assertLess(edge.reliever_xwoba_allowed, 0.310)

    def test_empty_returns_none(self):
        self.assertIsNone(bullpen_statcast_edge([]))

    def test_unreliable_profiles_skipped(self):
        p1 = _sp(pitcher_id=1, xwoba_allowed=0.280, reliable=False)
        self.assertIsNone(bullpen_statcast_edge([p1]))


class TestFramingProxy(unittest.TestCase):
    def test_returns_none_honestly(self):
        # Placeholder must not fabricate a number.
        self.assertIsNone(framing_runs_proxy(catcher_id=123))


if __name__ == "__main__":
    unittest.main()
