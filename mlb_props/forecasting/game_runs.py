"""Game forecast engine (game-run-model-v1).

One linear run projection per batting side, fitted by gradient least
squares with feature scaling. Moneyline / run-line / total all come from
the same run difference (normal approximation with residual scale). An
isotonic calibrator sits on the moneyline output. The model owns the
pick; price is never consulted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..calibration import IsotonicCalibrator


@dataclass
class TeamGameFeatures:
    """One batting side's context for a game."""

    runs_per_game_l7: float
    runs_per_game_season: float
    runs_allowed_per_game_l7: float
    opp_starter_runs_allowed_per_start_l5: float
    opp_starter_hits_allowed_per_start: float
    opp_starter_walks_per_start: float
    opp_starter_k_per_start: float
    opp_starter_deep_start_rate: float
    opp_bullpen_pitches_last3: float | None = None
    park_run_factor: float = 1.0
    home: int = 0
    doubleheader: int = 0
    night_game: int = 0
    weather_runs_adj: float = 0.0

    def row(self) -> list[float]:
        return [
            1.0,  # intercept, never scaled
            self.runs_per_game_l7,
            self.runs_per_game_season,
            self.runs_allowed_per_game_l7,
            math.log(max(self.opp_starter_runs_allowed_per_start_l5, 0.1)),
            self.opp_starter_hits_allowed_per_start,
            self.opp_starter_walks_per_start,
            self.opp_starter_k_per_start,
            self.opp_starter_deep_start_rate,
            self.opp_bullpen_pitches_last3 or -1.0,
            self.park_run_factor,
            float(self.home),
            float(self.doubleheader),
            float(self.night_game),
            self.weather_runs_adj,
        ]


@dataclass
class GameFeatures:
    game_pk: str | None
    screen_date: str
    home_side: TeamGameFeatures
    away_side: TeamGameFeatures


@dataclass
class GameForecast:
    version: str
    game_pk: str | None
    screen_date: str
    mean_home_runs: float
    mean_away_runs: float
    p_home: float
    p_away: float
    p_over: float | None
    p_under: float | None
    p_push: float
    p_home_cover_line: float | None
    run_diff_mu: float
    run_diff_sd: float


@dataclass
class GameEngineFit:
    weights: list[float]
    means: list[float]
    stds: list[float]
    resid_scale: float
    ml_isotonic: IsotonicCalibrator | None
    n_games: int


def _normal_cdf(value: float) -> float:
    # Abramowitz & Stegun 7.1.26 via erf() equivalent
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _least_squares(
    rows: list[list[float]],
    targets: list[float],
    l2: float = 0.05,
    lr: float = 0.05,
    epochs: int = 900,
) -> tuple[list[float], list[float], list[float]]:
    """Returns (weights incl. unscaled intercept, column means, stds).
    Column 0 stays at raw 1.0; other columns are standardised."""
    n_features = len(rows[0])
    n = len(rows)
    means = [sum(row[c] for row in rows) / n for c in range(1, n_features)]
    stds = []
    for c in range(1, n_features):
        variance = sum((row[c] - means[c - 1]) ** 2 for row in rows) / n
        stds.append(math.sqrt(variance) or 1.0)
    scaled = [
        [1.0] + [(row[c] - means[c - 1]) / stds[c - 1] for c in range(1, n_features)]
        for row in rows
    ]
    weights = [0.0] * n_features
    for _ in range(epochs):
        grad = [0.0] * n_features
        for row, target in zip(scaled, targets):
            pred = sum(w * x for w, x in zip(weights, row))
            err = pred - target
            for c, x in enumerate(row):
                grad[c] += err * x * lr / n
        weights[0] -= grad[0]
        for c in range(1, n_features):
            grad[c] += l2 * lr * weights[c]
            weights[c] -= grad[c]
    return weights, means, stds


def fit_game_engine_runs(
    game_features: list[GameFeatures],
    home_wins: list[int],
    run_targets: list[tuple[float, float]],
    epochs: int = 900,
) -> GameEngineFit | None:
    """`run_targets[i] = (team_runs_for_the_HOME_team, away_team_runs)`.
    Each team side contributes one row to a pooled linear run projection."""
    indexes = [
        i
        for i, (features, win) in enumerate(zip(game_features, home_wins))
        if win in (0, 1) and features.home_side is not None and features.away_side is not None
        and run_targets[i][0] is not None
    ]
    if len(indexes) < 400:
        return None

    rows: list[list[float]] = []
    targets: list[float] = []
    for i in indexes:
        features = game_features[i]
        rows.append(features.home_side.row())
        rows.append(features.away_side.row())
        targets.append(float(run_targets[i][0]))
        targets.append(float(run_targets[i][1]))

    weights, means, stds = _least_squares(rows, targets, epochs=epochs)

    def mu(side: TeamGameFeatures) -> float:
        row = side.row()
        scaled = [1.0] + [
            (row[c] - means[c - 1]) / stds[c - 1] for c in range(1, len(row))
        ]
        return sum(w * x for w, x in zip(weights, scaled))

    residuals = []
    for i in indexes:
        features = game_features[i]
        residuals.append(float(run_targets[i][0]) - mu(features.home_side))
        residuals.append(float(run_targets[i][1]) - mu(features.away_side))
    variance = sum(r * r for r in residuals) / max(len(residuals) - 1, 1)
    resid_scale = max(1.0, math.sqrt(variance))

    ml_isotonic = IsotonicCalibrator.fit(
        [
            (
                min(max(_normal_cdf((mu(game_features[i].home_side) - mu(game_features[i].away_side)) / (resid_scale * 1.414)), 0.01), 0.99),
                home_wins[i],
            )
            for i in indexes
        ]
    )
    return GameEngineFit(
        weights=weights,
        means=means,
        stds=stds,
        resid_scale=resid_scale,
        ml_isotonic=ml_isotonic,
        n_games=len(indexes),
    )


def mu_of_side(fit: GameEngineFit, side: TeamGameFeatures) -> float:
    row = side.row()
    scaled = [1.0] + [
        (row[c] - fit.means[c - 1]) / fit.stds[c - 1] for c in range(1, len(row))
    ]
    return max(0.1, sum(w * x for w, x in zip(fit.weights, scaled)))


def _run_distribution(mean: float) -> list[float]:
    """Variance-inflated Poisson over 0..14 with modest smearing."""
    lam = mean / 1.15
    base = [math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1)) if lam > 0 else (1.0 if k == 0 else 0.0) for k in range(15)]
    smeared = [0.0] * 15
    for k, prob in enumerate(base):
        share_up = prob * 0.12
        share_down = prob * 0.10
        centre = prob * 0.78
        smeared[k] += centre
        if k + 1 < 15:
            smeared[k + 1] += share_up
        if k - 1 >= 0:
            smeared[k - 1] += share_down
    total = sum(smeared)
    return [p / total for p in smeared]


def forecast_game(
    fit: GameEngineFit,
    features: GameFeatures,
    total_line: float | None = None,
    run_line: float | None = None,
    version: str = "game-run-model-v1",
) -> GameForecast:
    mu_home = float(mu_of_side(fit, features.home_side))
    mu_away = float(mu_of_side(fit, features.away_side))

    dist_home = _run_distribution(mu_home)
    dist_away = _run_distribution(mu_away)

    p_home_raw = 0.0
    p_away_raw = 0.0
    p_push = 0.0
    total_dist = [0.0] * 30
    margin_cover = 0.0
    margin_push = 0.0
    margin_lose = 0.0
    for h, ph in enumerate(dist_home):
        if ph == 0:
            continue
        for a, pa in enumerate(dist_away):
            joint = ph * pa
            if joint == 0:
                continue
            if h > a:
                p_home_raw += joint
            elif h < a:
                p_away_raw += joint
            else:
                p_push += joint
            total_dist[h + a] += joint
            if run_line is not None:
                margin_diff = (h - a) + run_line
                if margin_diff > 1e-9:
                    margin_cover += joint
                elif margin_diff < -1e-9:
                    margin_lose += joint
                else:
                    margin_push += joint

    # moneyline uses the isotonic calibration fitted on the same graded rows
    p_home = p_home_raw
    p_away = p_away_raw
    if fit.ml_isotonic is not None:
        p_home = float(fit.ml_isotonic.predict(p_home_raw))
        p_home = min(max(p_home, 0.02), 0.98)
        remaining = 1.0 - p_push
        p_away = remaining - p_home
        if p_away < 0.02 or p_home + p_away <= 0:
            # degenerate calibration result — fall back to uncalibrated
            p_home = p_home_raw
            p_away = p_away_raw

    p_over = None
    p_under = None
    if total_line is not None:
        over = 0.0
        push = 0.0
        for total_runs, prob in enumerate(total_dist):
            if total_runs > total_line:
                over += prob
            elif abs(total_runs - total_line) < 1e-9:
                push += prob
        p_over = over
        p_under = 1.0 - over - push

    p_home_cover = None
    if run_line is not None and margin_cover > 0:
        p_home_cover = margin_cover / (margin_cover + margin_lose)

    return GameForecast(
        version=version,
        game_pk=features.game_pk,
        screen_date=features.screen_date,
        mean_home_runs=mu_home,
        mean_away_runs=mu_away,
        p_home=p_home,
        p_away=p_away,
        p_over=p_over,
        p_under=p_under,
        p_push=p_push,
        p_home_cover_line=p_home_cover,
        run_diff_mu=mu_home - mu_away,
        run_diff_sd=fit.resid_scale * math.sqrt(2.0),
    )
