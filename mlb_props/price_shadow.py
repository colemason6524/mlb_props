from __future__ import annotations


def american_to_implied_probability(price: int | float | None) -> float | None:
    """Convert American odds to implied probability (0..1), excluding vig by convention.

    Positive prices imply probability 100/(price+100); negative prices imply
    |price|/(|price|+100). Returns None for non-positive or missing prices.
    """
    if price is None:
        return None
    try:
        numeric = float(price)
    except (TypeError, ValueError):
        return None
    if numeric == 0:
        return None
    if numeric > 0:
        return 100.0 / (numeric + 100.0)
    return abs(numeric) / (abs(numeric) + 100.0)


def raw_implied_probabilities(
    over_price: int | float | None,
    under_price: int | float | None,
) -> tuple[float | None, float | None]:
    """Return raw (vig-inclusive) implied probabilities for over and under."""
    return (
        american_to_implied_probability(over_price),
        american_to_implied_probability(under_price),
    )


def no_vig_probabilities(
    over_price: int | float | None,
    under_price: int | float | None,
) -> tuple[float | None, float | None]:
    """Return de-vigged probabilities for over and under.

    Scales both sides by their combined sum so the two probabilities sum to one.
    Returns (None, None) when either side is missing.
    """
    over_raw, under_raw = raw_implied_probabilities(over_price, under_price)
    if over_raw is None or under_raw is None:
        return None, None
    total = over_raw + under_raw
    if total <= 0:
        return None, None
    return over_raw / total, under_raw / total
