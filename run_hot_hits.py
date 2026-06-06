from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from mlb_props.cache import JsonCache
from mlb_props.config import CACHE_DIR, OUTPUTS_DIR, load_settings
from mlb_props.hot_hits import screen_hot_hitters
from mlb_props.notifiers.discord import send_discord_embeds
from mlb_props.output import render_hot_hit_candidates, render_hot_hits_discord_embeds
from mlb_props.sources.mlb_stats_api import (
    MlbStatsApiBatterLogsSource,
    MlbStatsApiPitcherLogsSource,
    MlbStatsApiSlateSource,
)


def export_hot_hits_history(payload: dict) -> Path:
    history_dir = OUTPUTS_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = history_dir / f"hot_hits_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return path


def discord_notifications_enabled() -> bool:
    return os.environ.get("SEND_DISCORD", "").strip().lower() in {"1", "true", "yes"}


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

    print(render_hot_hit_candidates(candidates, limit=settings.display_limit))
    print("")
    print("Hot hits run summary:")
    print(f"- Date: {settings.screen_date.isoformat()}")
    print(f"- Games with probable starters: {len(games)}")
    print(f"- Projected batters checked: {len(projected_batters)}")
    print(f"- Batters with logs: {sum(1 for logs in logs_by_batter.values() if logs)}")
    print(f"- Qualified candidates: {len(candidates)}")

    if discord_notifications_enabled():
        webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
        embeds = render_hot_hits_discord_embeds(
            candidates=candidates,
            screen_date=settings.screen_date.isoformat(),
            games_count=len(games),
            checked_count=len(projected_batters),
            limit=min(settings.display_limit, 8),
            min_score=settings.hot_hits_thresholds.discord_min_score,
        )
        result = send_discord_embeds(webhook_url, embeds)
        if result.ok:
            print("- Discord notification: sent")
        else:
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
            }
        )
        print(f"- History export: {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
