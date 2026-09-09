from __future__ import annotations

from datetime import date

from .models import Game, HotHitCandidate, HotHitPriceShadow
from .sources.bovada_props import (
    american_to_implied_probability,
    fetch_hit_prop_quotes,
)
from .utils import normalize_name
from .version import HOT_HITS_PRICE_SHADOW_VERSION


def attach_hit_price_shadow(
    *,
    candidates: list[HotHitCandidate],
    games: list[Game],
    screen_date: date,
    cache_dir,
    ttl_hours: float = 0.25,
) -> dict:
    """Attach single-sided Bovada hit prices as an observation-only shadow.

    Mirrors the contact-quality shadow contract: attachment must never change
    score, tier, card selection, or Discord content. Fails open — any source
    failure yields a metadata record and an unchanged candidate list.
    """
    metadata = {
        "version": HOT_HITS_PRICE_SHADOW_VERSION,
        "status": "not_requested",
        "screen_date": screen_date.isoformat(),
        "requested_candidates": len(candidates),
        "matched_candidates": 0,
        "quotes_found": 0,
        "diagnostics": {},
        "error": None,
    }
    if not candidates:
        metadata["status"] = "no_candidates"
        return metadata

    try:
        quotes, diagnostics = fetch_hit_prop_quotes(
            cache_dir=cache_dir,
            games=games,
            screen_date=screen_date,
        )
    except Exception as exc:  # fail open: no quote is a normal operational state
        metadata["status"] = "source_failed"
        metadata["error"] = str(exc)[:300]
        return metadata

    metadata["diagnostics"] = diagnostics
    metadata["quotes_found"] = len(quotes)

    matched = 0
    for candidate in candidates:
        key = (candidate.team, normalize_name(candidate.batter_name))
        quote = quotes.get(key)
        if quote is None:
            continue
        implied = american_to_implied_probability(quote.hit_yes_price)
        alt_implied = american_to_implied_probability(quote.hits_2plus_yes_price)
        flags: list[str] = []
        if quote.hits_2plus_yes_price is None:
            flags.append("ALT_2PLUS_NOT_LISTED")
        if implied is not None and implied >= 0.80:
            flags.append("HEAVY_FAVORITE_PRICE")
        candidate.price_shadow = HotHitPriceShadow(
            version=HOT_HITS_PRICE_SHADOW_VERSION,
            screen_date=screen_date.isoformat(),
            bookmaker="bovada",
            source="bovada_event_coupon",
            market="hit_yes",
            price=quote.hit_yes_price,
            implied_probability=round(implied, 4) if implied is not None else 0.0,
            alt_market="hits_2plus_yes",
            alt_price=quote.hits_2plus_yes_price,
            alt_implied_probability=round(alt_implied, 4) if alt_implied is not None else None,
            event_slug=quote.event_slug,
            collected_at=quote.collected_at_utc,
            flags=flags,
        )
        matched += 1

    metadata["matched_candidates"] = matched
    metadata["status"] = (
        "available"
        if matched == len(candidates)
        else ("partial" if matched else "no_quotes")
    )
    return metadata


def hit_price_research_targets(research_pool: list[HotHitCandidate], limit: int) -> list[HotHitCandidate]:
    """Candidates plus the top-N research profiles by provisional confidence.

    The price shadow covers every production candidate and the top ranked
    research names so the delivered Discord card is always priced while the
    datacenter fetch stays bounded on the small VM.
    """
    if limit <= 0:
        return []
    return research_pool[: max(limit, 0)]


__all__ = [
    "HOT_HITS_PRICE_SHADOW_VERSION",
    "attach_hit_price_shadow",
    "hit_price_research_targets",
]
