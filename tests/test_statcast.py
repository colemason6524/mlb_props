"""Tests for the Statcast pipeline (mlb_props/statcast.py).

Uses synthetic pitch dicts — no network, no pybaseball needed.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from mlb_props.statcast import (
    DailyPitcherRow,
    StatcastPipeline,
    aggregate_pitcher_day,
)


def _pitch(**overrides):
    base = {
        "pitch_type": "FF",
        "game_date": "2026-10-05",
        "release_speed": 95.0,
        "release_spin_rate": 2200.0,
        "pfx_x": 5.0,
        "pfx_z": 12.0,
        "player_name": "Test Pitcher",
        "batter": 111,
        "pitcher": 222,
        "events": "",
        "description": "called_strike",
        "zone": 5,
        "estimated_woba_using_speedangle": 0.300,
        "launch_speed": 90.0,
        "game_pk": 999,
        "at_bat_number": 1,
    }
    base.update(overrides)
    return base


class TestAggregatePitcherDay(unittest.TestCase):
    def test_basic_aggregation(self):
        pitches = [
            _pitch(description="swinging_strike", at_bat_number=1, events="strikeout"),
            _pitch(description="foul", at_bat_number=1),
            _pitch(description="called_strike", at_bat_number=2, events="walk"),
            _pitch(pitch_type="SL", release_speed=85.0, release_spin_rate=2500.0,
                   description="ball", at_bat_number=3, events="single",
                   estimated_woba_using_speedangle=0.800),
        ]
        row = aggregate_pitcher_day(222, "Test Pitcher", "2026-10-05", pitches)
        self.assertEqual(row.pitches, 4)
        # 3 of 4 are FF
        self.assertAlmostEqual(row.avg_velo_ff, 95.0)
        self.assertAlmostEqual(row.avg_spin_ff, 2200.0)
        self.assertAlmostEqual(row.avg_spin_br, 2500.0)
        # whiff: 1 swinging strike / 3 swings (swinging_strike, foul, foul? no:
        # swinging_strike + foul = 2 swings... "ball" is not a swing)
        # swings: swinging_strike, foul = 2; whiffs: swinging_strike = 1
        self.assertAlmostEqual(row.whiff_pct, 0.5)
        # mix: 3 FF / 4
        self.assertAlmostEqual(row.mix_fastball, 0.75)
        self.assertAlmostEqual(row.mix_breaking, 0.25)
        # xwOBA: mean of 0.3,0.3,0.3,0.8
        self.assertAlmostEqual(row.xwoba_allowed, 0.425)

    def test_empty_pitches(self):
        row = aggregate_pitcher_day(222, "Test", "2026-10-05", [])
        self.assertEqual(row.pitches, 0)
        self.assertIsNone(row.avg_velo_ff)

    def test_k_bb_rates(self):
        pitches = [
            _pitch(at_bat_number=1, events="strikeout", description="swinging_strike"),
            _pitch(at_bat_number=1, description="foul"),
            _pitch(at_bat_number=2, events="walk", description="ball"),
            _pitch(at_bat_number=3, events="single", description="in_play"),
        ]
        row = aggregate_pitcher_day(222, "Test", "2026-10-05", pitches)
        # 3 PAs: 1 K, 1 BB
        self.assertAlmostEqual(row.k_pct, 1 / 3, places=4)
        self.assertAlmostEqual(row.bb_pct, 1 / 3, places=4)

    def test_roundtrip_dict(self):
        row = DailyPitcherRow(pitcher_id=1, pitcher_name="X", game_date="2026-10-05", pitches=10)
        d = row.to_dict()
        back = DailyPitcherRow.from_dict(d)
        self.assertEqual(back.pitcher_id, 1)
        self.assertEqual(back.pitches, 10)


class TestPipelineProfile(unittest.TestCase):
    def test_trailing_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipe = StatcastPipeline(Path(tmp))
            # Inject two days of rows directly into cache
            for day_iso in ("2026-10-03", "2026-10-04"):
                rows = [aggregate_pitcher_day(
                    777, "Arm", day_iso,
                    [_pitch(release_speed=96.0)] * 60,  # 60 pitches/day
                ).to_dict()]
                pipe.cache.set(pipe._day_key(date.fromisoformat(day_iso)),
                               {"summary": {}, "rows": rows})
            prof = pipe.pitcher_profile(777, date(2026, 10, 5))
            self.assertIsNotNone(prof)
            self.assertEqual(prof.total_pitches, 120)
            self.assertAlmostEqual(prof.avg_velo_ff, 96.0)
            self.assertTrue(prof.reliable)  # >= 100 pitches

    def test_no_leakage(self):
        """Rows on as_of date itself must not be included."""
        with tempfile.TemporaryDirectory() as tmp:
            pipe = StatcastPipeline(Path(tmp))
            rows = [aggregate_pitcher_day(
                888, "Arm", "2026-10-05", [_pitch()] * 200).to_dict()]
            pipe.cache.set(pipe._day_key(date(2026, 10, 5)), {"summary": {}, "rows": rows})
            prof = pipe.pitcher_profile(888, date(2026, 10, 5))
            # Only data strictly before as_of counts; the 10-05 rows are excluded
            self.assertIsNone(prof)

    def test_missing_pitcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            pipe = StatcastPipeline(Path(tmp))
            self.assertIsNone(pipe.pitcher_profile(12345, date(2026, 10, 5)))


if __name__ == "__main__":
    unittest.main()
