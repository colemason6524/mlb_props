"""Game-level market models and market-baseline probability helpers.

This module supports the shadow collection of moneyline, run-line, and
game-total markets. It is observation-only: nothing here changes pitcher or
hitter projections, scores, or tiers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .price_shadow import (
    american_to_implied_probability,
    no_vig_probabilities,
)


@dataclass
class TwoWayPrice:
    """Both-side American prices for a two-outcome market.

    Semantics depend on the market:
    - moneyline/run line: side_a = home, side_b = away
    - game total: side_a = over, side_b = under
    """

    line: float | None
    price_a: int | None
    price_b: int | None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class GameMarketSnapshot:
    """Sportsbook lines for one game, matched to the MLB slate by team abbr."""

    game_id: str
    game_date: str
    home_team: str
    away_team: str
    start_time_utc: str | None
    moneyline: TwoWayPrice | None
    spread: TwoWayPrice | None
    total: TwoWayPrice | None
    source: str
    espn_total: float | None = None
    espn_away_spread: float | None = None
    espn_home_spread: float | None = None

    def as_dict(self) -> dict:
        return {
            "game_id": self.game_id,
            "game_date": self.game_date,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "start_time_utc": self.start_time_utc,
            "moneyline": self.moneyline.as_dict() if self.moneyline else None,
            "spread": self.spread.as_dict() if self.spread else None,
            "total": self.total.as_dict() if self.total else None,
            "source": self.source,
            "espn_cross_check": {
                "total": self.espn_total,
                "away_spread": self.espn_away_spread,
                "home_spread": self.espn_home_spread,
            },
        }


def home_win_no_vig(
    moneyline: TwoWayPrice | None,
) -> tuple[float | None, float | None]:
    """Return no-vig (home win, away win) probabilities from moneyline prices."""
    if moneyline is None:
        return None, None
    return no_vig_probabilities(moneyline.price_a, moneyline.price_b)


def over_no_vig(total: TwoWayPrice | None) -> tuple[float | None, float | None]:
    """Return no-vig (over, under) probabilities from game-total prices."""
    if total is None:
        return None, None
    return no_vig_probabilities(total.price_a, total.price_b)


def implied_home_moneyline_probability(price: int | None) -> float | None:
    """Raw (vig-inclusive) implied home-win probability from an American price."""
    return american_to_implied_probability(price)


def market_baseline_payload(snapshot: GameMarketSnapshot) -> dict:
    """Build the observation-only market-baseline record for one game.

    Contains no-vig probabilities plus raw prices so later research can
    compare any model opinion against the book without refetching lines.
    """
    home_p, away_p = home_win_no_vig(snapshot.moneyline)
    over_p, under_p = over_no_vig(snapshot.total)
    return {
        "home_win_no_vig": _round_or_none(home_p),
        "away_win_no_vig": _round_or_none(away_p),
        "over_no_vig": _round_or_none(over_p),
        "under_no_vig": _round_or_none(under_p),
    }


def _round_or_none(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)
