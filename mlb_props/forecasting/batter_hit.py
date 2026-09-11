"""Batter 1+ hit forecast engine (batter-1hit-prob-v1).

Promotes the Savant contact-quality layer from shadow observation to a
real model input and calibrates the hit probability on the graded
season pool. The model owns the pick; price is never consulted."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..calibration import IsotonicCalibrator, LogisticModel


@dataclass
class BatterFeatures:
    game_pk: str | None
    screen_date: str
    batter_name: str
    batter_id: int | None
    team: str
    season_avg: float
    avg_last_10: float
    avg_last_5: float
    batting_order: float | None = None
    hit_games_last_5: float | None = None
    hit_games_last_10: float | None = None
    at_bats_last_10: float | None = None
    season_xba: float | None = None
    xba_last_10: float | None = None
    xba_contact_last_25: float | None = None
    hard_hit_rate_last_25: float | None = None
    avg_exit_velocity_last_25: float | None = None
    expected_at_bats_contact: float | None = None
    pitcher_hand: str | None = None
    batter_hand: str | None = None
    pitcher_hits_allowed_rate_last_5: float | None = None
    pitcher_k_rate_last_5: float | None = None
    pitcher_walk_rate_last_5: float | None = None
    matchup_rating: float | None = None
    bvp_ab: int | None = None
    bvp_avg: float | None = None
    opposing_bullpen_rest_days: int | None = None
    home_away: int | None = None  # 1 home, 0 away, -1 unknown

    def row(self) -> list[float]:
        order = self.batting_order
        return [
            float(order) if order is not None else -1.0,
            self.season_avg,
            self.avg_last_10,
            self.avg_last_5,
            self.hit_games_last_5 or -1.0,
            self.hit_games_last_10 or -1.0,
            self.at_bats_last_10 or -1.0,
            self.season_xba or -1.0,
            self.xba_last_10 or -1.0,
            self.xba_contact_last_25 or -1.0,
            self.hard_hit_rate_last_25 or -1.0,
            self.avg_exit_velocity_last_25 or -1.0,
            self.expected_at_bats_contact or -1.0,
            self.pitcher_hits_allowed_rate_last_5 or -1.0,
            self.pitcher_k_rate_last_5 or -1.0,
            self.pitcher_walk_rate_last_5 or -1.0,
            self.matchup_rating or -1.0,
            float(self.bvp_ab) if self.bvp_ab else -1.0,
            self.bvp_avg or -1.0,
            float(self.opposing_bullpen_rest_days) if self.opposing_bullpen_rest_days is not None else -1.0,
            float(self.home_away) if self.home_away is not None else -1.0,
        ]


@dataclass
class BatterEngineResult:
    logit: LogisticModel
    isotonic: IsotonicCalibrator
    n_rows: int

    def predict_hit_probability(self, features: BatterFeatures) -> tuple[float, float]:
        """Return (raw_logit_estimate, calibrated_hit_p)."""
        raw = self.logit.predict_proba(features.row())
        calibrated = float(self.isotonic.predict(raw))
        calibrated = min(max(calibrated, 0.10), 0.92)
        return raw, calibrated


def fit_batter_engine(rows: list[tuple[BatterFeatures, int]]) -> BatterEngineResult | None:
    """`rows` are (features, hit_win) pairs — hit_win 1 for recorded at
    least one hit, 0 otherwise. Saves isotonic calibration run separately."""
    clean = [row for row in rows if row[1] in (0, 1)]
    if len(clean) < 400:
        return None
    logit = LogisticModel()
    logit.fit(
        rows=[f.row() for f, _y in clean],
        targets=[y for _f, y in clean],
    )
    isotonic = IsotonicCalibrator.fit(
        [(logit.predict_proba(f.row()), y) for f, y in clean]
    )
    return BatterEngineResult(
        logit=logit,
        isotonic=isotonic,
        n_rows=len(clean),
    )
