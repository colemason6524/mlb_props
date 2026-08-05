from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Game:
    game_id: str
    game_date: date
    game_time: datetime
    home_team: str
    away_team: str
    probable_home_pitcher: str
    probable_away_pitcher: str
    source: str
    probable_home_pitcher_id: int | None = None
    probable_away_pitcher_id: int | None = None


@dataclass
class PropLine:
    event_id: str
    game_date: date
    subject_name_raw: str
    subject_name_norm: str
    subject_role: str
    team: str
    opponent: str
    hand: str
    prop_type: str
    line: float
    bookmaker: str
    source: str
    collected_at: datetime
    subject_id: int | None = None


@dataclass
class PitcherGameLog:
    pitcher_name_raw: str
    pitcher_name_norm: str
    game_date: date
    team: str
    opponent: str
    hand: str
    outs_recorded: int
    strikeouts: int
    pitches_thrown: int
    batters_faced: int
    walks: int
    hits_allowed: int
    earned_runs: int
    did_start: bool
    source: str
    pitcher_id: int | None = None

    @property
    def innings_pitched(self) -> float:
        return self.outs_recorded / 3.0


@dataclass
class BatterGameLog:
    batter_name_raw: str
    batter_name_norm: str
    game_date: date
    team: str
    opponent: str
    bat_side: str
    position: str
    batting_order: int | None
    at_bats: int
    hits: int
    doubles: int
    triples: int
    home_runs: int
    walks: int
    strikeouts: int
    plate_appearances: int
    source: str
    batter_id: int | None = None


@dataclass
class BatterPitcherHistory:
    batter_id: int
    pitcher_id: int
    at_bats: int
    hits: int
    extra_base_hits: int
    walks: int
    strikeouts: int
    source: str

    @property
    def batting_average(self) -> float | None:
        if self.at_bats <= 0:
            return None
        return self.hits / self.at_bats


@dataclass
class MatchupContext:
    team: str
    opponent: str
    opponent_k_rate_vs_hand: float
    opponent_walk_rate_vs_hand: float
    opponent_woba_vs_hand: float
    opponent_outs_factor: float
    park_run_factor: float
    moneyline: int
    source: str


@dataclass
class OpportunityShadow:
    version: str
    screen_date: str
    starts_available: int
    recent_start_dates: list[str]
    pitch_counts_last_3: list[int]
    outs_last_3: list[int]
    batters_faced_last_3: list[int]
    pitches_per_bf_last_3: list[float | None]
    days_since_last_start: int | None
    avg_pitch_count_last_3: float | None
    max_pitch_count_last_5: int | None
    pitch_count_trend: str
    pitch_count_trend_delta: float | None
    outs_trend: str
    outs_trend_delta: float | None
    pitch_count_volatility_last_5: float | None
    outs_volatility_last_5: float | None
    short_starts_last_5: int
    shadow_pitch_budget: float | None
    shadow_projected_batters_faced: float | None
    shadow_projected_outs: float | None
    opportunity_confidence: str
    flags: list[str] = field(default_factory=list)


@dataclass
class ContactQualityShadow:
    version: str
    source: str
    screen_date: str
    query_start_date: str
    query_end_date: str
    batter_id: int
    games_available: int
    recent_game_dates: list[str]
    plate_appearances_season: int
    xba_opportunities_season: int
    xba_opportunities_last_10_games: int
    tracked_bbe_season: int
    tracked_bbe_last_10_games: int
    tracked_bbe_last_25: int
    season_xba: float | None
    xba_last_10_games: float | None
    xba_last_25_bbe: float | None
    hard_hit_rate_last_25_bbe: float | None
    sweet_spot_rate_last_25_bbe: float | None
    barrel_rate_last_25_bbe: float | None
    avg_exit_velocity_last_25_bbe: float | None
    actual_avg_last_10: float | None = None
    actual_ba_minus_xba_last_10: float | None = None
    expected_at_bats: float | None = None
    estimated_one_hit_probability: float | None = None
    confidence: str = "low"
    flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PitcherConfidenceEstimate:
    version: str
    calibration_status: str
    win_probability: float
    confidence_percentage: int
    label: str
    projected_mean: float
    projected_standard_deviation: float
    raw_win_probability: float
    reliability_weight: float
    price_included: bool = False
    flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HotHitConfidenceEstimate:
    version: str
    calibration_status: str
    hit_probability: float
    confidence_percentage: int
    label: str
    per_at_bat_probability: float
    season_anchor_probability: float
    expected_at_bats: float
    reliability_weight: float
    price_included: bool = False
    flags: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    subject_name: str
    subject_id: int | None
    subject_role: str
    team: str
    opponent: str
    hand: str
    prop_type: str
    side: str
    line: float
    bookmaker: str
    hits_last_5: int
    played_last_5: int
    hits_last_10: int
    played_last_10: int
    avg_last_5: float
    avg_last_10: float
    median_last_5: float
    median_last_10: float
    season_avg: float
    delta_avg_last_5: float
    delta_avg_last_10: float
    avg_pitch_count_last_5: float
    avg_pitch_count_last_10: float
    avg_outs_last_5: float
    avg_outs_last_10: float
    avg_k_rate_last_5: float
    avg_walk_rate_last_5: float
    avg_earned_runs_last_5: float
    quality_starts_last_10: int
    short_starts_last_10: int
    workload_stability: float
    matchup_rating: float
    projected_outs: float
    projected_batters_faced: float
    projected_k_rate: float
    projected_strikeouts: float
    score: int
    flags: list[str] = field(default_factory=list)
    opponent_k_rate_vs_hand: float | None = None
    opponent_outs_factor: float | None = None
    park_run_factor: float | None = None
    moneyline: int | None = None
    opportunity_shadow: OpportunityShadow | None = None
    confidence_estimate: PitcherConfidenceEstimate | None = None


@dataclass
class StarterAssessment:
    pitcher_name: str
    pitcher_id: int | None
    team: str
    opponent: str
    hand: str
    season_starts: int
    avg_strikeouts_last_5: float
    avg_strikeouts_season: float
    avg_outs_last_5: float
    avg_outs_season: float
    avg_pitch_count_last_5: float
    avg_k_rate_last_5: float
    avg_walk_rate_last_5: float
    avg_earned_runs_last_5: float
    workload_stability_ks: float
    workload_stability_outs: float
    quality_starts_recent: int
    short_starts_recent: int
    matchup_rating_ks: float
    matchup_rating_outs: float
    overall_score: int
    ks_signal: float
    outs_signal: float
    projected_outs: float
    projected_batters_faced: float
    projected_k_rate: float
    projected_strikeouts: float
    strikeout_line: float | None = None
    line_bookmaker: str | None = None
    lean_side: str | None = None
    lean_edge: float | None = None
    lean_score: int | None = None
    shortlist_status: str | None = None
    shortlist_reason: str | None = None
    flags: list[str] = field(default_factory=list)
    opportunity_shadow: OpportunityShadow | None = None


@dataclass
class HotHitCandidate:
    batter_name: str
    batter_id: int | None
    team: str
    opponent: str
    bat_side: str
    position: str
    batting_order: int | None
    probable_pitcher: str
    probable_pitcher_id: int | None
    pitcher_hand: str
    games_played: int
    avg_last_5: float
    avg_last_10: float
    season_avg: float
    obp_last_5: float
    hit_games_last_5: int
    hit_games_last_10: int
    at_bats_last_5: int
    hits_last_5: int
    hits_last_10: int
    season_hits: int
    season_at_bats: int
    pitcher_hits_allowed_rate_last_5: float
    pitcher_hits_allowed_rate_season: float
    pitcher_k_rate_last_5: float
    pitcher_walk_rate_last_5: float
    batter_vs_pitcher_avg: float | None
    batter_vs_pitcher_ab: int | None
    matchup_rating: float
    score: int
    flags: list[str] = field(default_factory=list)
    contact_quality_shadow: ContactQualityShadow | None = None
    at_bats_last_10: int = 0
    current_gate_qualified: bool = True
    current_display_qualified: bool = True
    gate_failures: list[str] = field(default_factory=list)
    confidence_estimate: HotHitConfidenceEstimate | None = None


@dataclass
class HotHitsScreeningResult:
    candidates: list[HotHitCandidate]
    research_pool: list[HotHitCandidate]


@dataclass
class ScreeningResult:
    candidates: list[Candidate]
    evaluated_prop_lines: int
    non_qualifying_prop_lines: int
