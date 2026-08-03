from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mlb_props.cache import JsonCache
from mlb_props.config import CACHE_DIR, OUTPUTS_DIR, load_settings
from mlb_props.hot_hits import screen_hot_hitters
from mlb_props.hot_hits_policy import hot_hit_tier, select_hot_hits_card
from mlb_props.notifiers.discord import send_discord_embeds
from mlb_props.output import render_hot_hit_candidates, render_hot_hits_discord_embeds
from mlb_props.sources.mlb_stats_api import (
    MlbStatsApiBatterLogsSource,
    MlbStatsApiPitcherLogsSource,
    MlbStatsApiSlateSource,
)
from mlb_props.sources.baseball_savant import (
    CONTACT_QUALITY_SHADOW_VERSION,
    BaseballSavantContactSource,
    enrich_contact_quality_profile,
)
from mlb_props.utils import normalize_name


def export_hot_hits_history(payload: dict) -> Path:
    history_dir = OUTPUTS_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = history_dir / f"hot_hits_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return path


def discord_notifications_enabled() -> bool:
    return os.environ.get("SEND_DISCORD", "").strip().lower() in {"1", "true", "yes"}


def _history_selection_item(candidate, rank: int) -> dict:
    return {
        "rank": rank,
        "batter_id": candidate.batter_id,
        "batter_name": candidate.batter_name,
        "team": candidate.team,
        "score": candidate.score,
        "tier": hot_hit_tier(candidate),
    }


def attach_contact_quality_shadow(
    *,
    candidates,
    logs_by_batter,
    screen_date,
    source,
) -> dict:
    batter_ids = sorted(
        {candidate.batter_id for candidate in candidates if candidate.batter_id is not None}
    )
    metadata = {
        "version": CONTACT_QUALITY_SHADOW_VERSION,
        "status": "not_requested",
        "as_of_date": (screen_date - timedelta(days=1)).isoformat(),
        "requested_candidates": len(batter_ids),
        "profiles_available": 0,
        "error": None,
    }
    if not batter_ids:
        metadata["status"] = "no_candidates"
        return metadata

    try:
        profiles = source.fetch_profiles(batter_ids, screen_date=screen_date)
    except Exception as exc:
        metadata["status"] = "source_failed"
        metadata["error"] = str(exc)
        return metadata

    for candidate in candidates:
        if candidate.batter_id is None or candidate.batter_id not in profiles:
            continue
        recent_logs = [
            log
            for log in logs_by_batter.get(normalize_name(candidate.batter_name), [])
            if log.game_date < screen_date
        ][:10]
        expected_at_bats = (
            sum(log.at_bats for log in recent_logs) / len(recent_logs)
            if recent_logs
            else None
        )
        candidate.contact_quality_shadow = enrich_contact_quality_profile(
            profiles[candidate.batter_id],
            actual_avg_last_10=candidate.avg_last_10,
            expected_at_bats=expected_at_bats,
        )

    available = sum(candidate.contact_quality_shadow is not None for candidate in candidates)
    metadata["profiles_available"] = available
    metadata["status"] = "available" if available == len(batter_ids) else "partial"
    return metadata


def main() -> int:
    settings = load_settings()
    if settings.data_mode != "live":
        print("Hot hits currently uses live MLB Stats API data. Run with DATA_MODE=live.")
        return 1

    shared_cache = JsonCache(CACHE_DIR / "shared", ttl_hours=settings.cache_ttl_hours)
    slate_source = MlbStatsApiSlateSource(shared_cache)
    pitcher_logs_source = MlbStatsApiPitcherLogsSource(shared_cache)
    batter_logs_source = MlbStatsApiBatterLogsSource(shared_cache)
    batter_logs_source.MAX_BATTERS_PER_TEAM = settings.hot_hits_thresholds.max_batters_per_team

    games = slate_source.fetch_games(settings.screen_date)
    games = [
        game
        for game in games
        if game.probable_home_pitcher_id is not None and game.probable_away_pitcher_id is not None
    ]
    projected_batters = batter_logs_source.fetch_projected_batters_for_games(games)
    logs_by_batter = batter_logs_source.fetch_logs_for_batters(
        projected_batters,
        season=settings.screen_date.year,
    )
    logs_by_pitcher = pitcher_logs_source.fetch_logs_for_games(games, season=settings.screen_date.year)

    candidates = screen_hot_hitters(
        settings=settings,
        games=games,
        projected_batters=projected_batters,
        logs_by_batter=logs_by_batter,
        logs_by_pitcher=logs_by_pitcher,
        batter_pitcher_history=(
            batter_logs_source.fetch_batter_pitcher_history
            if settings.hot_hits_thresholds.include_bvp
            else None
        ),
    )

    contact_quality_metadata = {
        "version": CONTACT_QUALITY_SHADOW_VERSION,
        "status": "disabled",
        "as_of_date": None,
        "requested_candidates": 0,
        "profiles_available": 0,
        "error": None,
    }
    if settings.hot_hits_thresholds.include_contact_quality_shadow:
        contact_quality_source = BaseballSavantContactSource(
            JsonCache(CACHE_DIR / "savant", ttl_hours=settings.cache_ttl_hours)
        )
        contact_quality_metadata = attach_contact_quality_shadow(
            candidates=candidates,
            logs_by_batter=logs_by_batter,
            screen_date=settings.screen_date,
            source=contact_quality_source,
        )

    print(render_hot_hit_candidates(candidates, limit=settings.display_limit))
    print("")
    print("Hot hits run summary:")
    print(f"- Date: {settings.screen_date.isoformat()}")
    print(f"- Games with probable starters: {len(games)}")
    print(f"- Projected batters checked: {len(projected_batters)}")
    print(f"- Batters with logs: {sum(1 for logs in logs_by_batter.values() if logs)}")
    print(f"- Qualified candidates: {len(candidates)}")
    contact_status = contact_quality_metadata["status"]
    if contact_status == "source_failed":
        print(f"- Contact-quality shadow: source failed ({contact_quality_metadata['error']})")
    elif contact_status == "disabled":
        print("- Contact-quality shadow: disabled")
    else:
        print(
            "- Contact-quality shadow: "
            f"{contact_status} "
            f"({contact_quality_metadata['profiles_available']}/"
            f"{contact_quality_metadata['requested_candidates']} profiles, "
            f"through {contact_quality_metadata['as_of_date']})"
        )

    card_settings = settings.hot_hits_thresholds
    discord_selection = select_hot_hits_card(
        candidates,
        card_policy=card_settings.discord_card_policy,
        limit=card_settings.discord_core_limit,
        value_limit=card_settings.discord_value_limit,
        min_score=card_settings.discord_min_score,
    )
    discord_status = "not_requested"
    if discord_notifications_enabled():
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        embeds = render_hot_hits_discord_embeds(
            candidates=candidates,
            screen_date=settings.screen_date.isoformat(),
            games_count=len(games),
            checked_count=len(projected_batters),
            limit=card_settings.discord_core_limit,
            min_score=card_settings.discord_min_score,
            card_policy=card_settings.discord_card_policy,
            value_limit=card_settings.discord_value_limit,
        )
        result = send_discord_embeds(webhook_url, embeds)
        if result.ok:
            discord_status = "sent"
            print("- Discord notification: sent")
        else:
            discord_status = "failed"
            print(f"- Discord notification: failed ({result.error or result.status_code})")

    if settings.export_history:
        path = export_hot_hits_history(
            {
                "screen_date": settings.screen_date.isoformat(),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "candidates": [asdict(candidate) for candidate in candidates],
                "settings": {
                    "display_limit": settings.display_limit,
                    "hot_hits_thresholds": asdict(settings.hot_hits_thresholds),
                },
                "contact_quality_shadow": contact_quality_metadata,
                "discord_delivery": {
                    "status": discord_status,
                    "policy_version": card_settings.discord_card_policy,
                    "core_limit": card_settings.discord_core_limit,
                    "value_limit": card_settings.discord_value_limit,
                    "min_score": card_settings.discord_min_score,
                    "core": [
                        _history_selection_item(candidate, rank)
                        for rank, candidate in enumerate(discord_selection.core, start=1)
                    ],
                    "optional_value": [
                        _history_selection_item(
                            candidate,
                            len(discord_selection.core) + rank,
                        )
                        for rank, candidate in enumerate(discord_selection.value, start=1)
                    ],
                    "thin": [
                        _history_selection_item(
                            candidate,
                            len(discord_selection.core)
                            + len(discord_selection.value)
                            + rank,
                        )
                        for rank, candidate in enumerate(discord_selection.thin, start=1)
                    ],
                },
            }
        )
        print(f"- History export: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
