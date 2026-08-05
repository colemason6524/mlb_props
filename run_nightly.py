from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from mlb_props.cache import JsonCache
from mlb_props.config import CACHE_DIR, OUTPUTS_DIR, load_settings
from mlb_props.notifiers.discord import send_discord_embeds
from mlb_props.output import (
    render_candidates,
    render_pitcher_props_discord_embeds,
    render_starter_board,
)
from mlb_props.pitcher_presentation import (
    build_pitcher_presentations,
    display_rankings_payload,
)
from mlb_props.screener import build_daily_starter_board, screen_pitcher_props
from mlb_props.sources.mlb_stats_api import MlbStatsApiPitcherLogsSource, MlbStatsApiSlateSource
from mlb_props.sources.odds_api import MlbOddsApiSource
from mlb_props.sources.opponent_context import MlbOpponentContextSource
from mlb_props.sources.scrape import MlbScrapeSource
from mlb_props.sources.sample import SamplePitcherLogsSource, SamplePitcherPropsSource, SampleSlateSource
from mlb_props.tiers import candidate_tier
from mlb_props.version import (
    PITCHER_DISPLAY_POLICY_VERSION,
    PITCHER_CONFIDENCE_MODEL_VERSION,
    PITCHER_HISTORY_SCHEMA_VERSION,
    PITCHER_MODEL_VERSION,
    PITCHER_OPPORTUNITY_SHADOW_VERSION,
    PITCHER_TIER_POLICY_VERSION,
)


def line_source_diagnostics(lines_source) -> dict:
    diagnostics_fn = getattr(lines_source, "diagnostics", None)
    if callable(diagnostics_fn):
        return diagnostics_fn()
    return {}


def discord_notifications_enabled() -> bool:
    return os.environ.get("SEND_DISCORD", "").strip().lower() in {"1", "true", "yes"}


def discord_webhook_url() -> str:
    return (
        os.environ.get("PITCHER_PROPS_DISCORD_WEBHOOK_URL", "").strip()
        or os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    )


def live_coverage_floor(game_count: int) -> int:
    return max(6, min(12, game_count))


def line_coverage_status(diagnostics: dict, prop_line_count: int, game_count: int) -> str:
    if game_count <= 0:
        return "unknown"
    floor = live_coverage_floor(game_count)
    fanduel_loaded = diagnostics.get("fanduel_team_pages_loaded", diagnostics.get("team_pages_loaded", 0))
    fanduel_expected = diagnostics.get("fanduel_expected_team_pages", diagnostics.get("expected_team_pages", 0))
    fanduel_lines = diagnostics.get("fanduel_unique_lines_returned", diagnostics.get("unique_lines_returned", prop_line_count))
    if fanduel_expected and fanduel_loaded >= max(1, fanduel_expected // 2) and fanduel_lines == 0 and prop_line_count == 0:
        return "source_failed"
    if prop_line_count < floor:
        return "thin"
    return "ok"


def maybe_export_live_scrape_snapshot(lines_source, screen_date, prop_line_count: int, game_count: int):
    diagnostics = line_source_diagnostics(lines_source)
    if not diagnostics:
        return None
    if prop_line_count >= live_coverage_floor(game_count):
        return None
    export_fn = getattr(lines_source, "export_diagnostics_snapshot", None)
    if not callable(export_fn):
        return None
    return export_fn(screen_date)


def render_live_diagnostics(diagnostics: dict, prop_lines: list) -> str | None:
    if not diagnostics:
        return None
    if "fanduel_team_pages_loaded" in diagnostics:
        return (
            "Live line diagnostics: "
            f"FanDuel {diagnostics.get('fanduel_team_pages_loaded', 0)}/"
            f"{diagnostics.get('fanduel_expected_team_pages', 0)} team pages, "
            f"{diagnostics.get('fanduel_unique_lines_returned', 0)} Ks lines; "
            f"DraftKings Ks markets {diagnostics.get('draftkings_strikeout_markets_seen', 0)}, "
            f"{diagnostics.get('draftkings_strikeout_unique_lines_returned', 0)} Ks lines; "
            f"DraftKings outs markets {diagnostics.get('draftkings_outs_markets_seen', 0)}, "
            f"{diagnostics.get('draftkings_outs_unique_lines_returned', 0)} outs lines."
        )
    return (
        "Live line diagnostics: "
        f"{diagnostics.get('team_pages_loaded', 0)}/{diagnostics.get('expected_team_pages', 0)} team pages loaded, "
        f"{diagnostics.get('strikeout_lines_found', 0)} raw Ks lines, "
        f"{diagnostics.get('unique_lines_returned', len(prop_lines))} unique lines."
    )


def render_line_coverage_warning(status: str, diagnostics: dict, prop_line_count: int, game_count: int) -> str | None:
    if status == "source_failed":
        return (
            "Warning: live Ks line source appears stale or failed. "
            f"Loaded team pages but found {prop_line_count} Ks lines for a {game_count}-game slate. "
            "Treat this run as unsuitable for model evaluation."
        )
    if status == "thin":
        return (
            f"Warning: live line coverage looks thin for a {game_count}-game slate "
            f"(floor: {live_coverage_floor(game_count)}, found: {prop_line_count})."
        )
    return None


def warm_cache_mode_enabled() -> bool:
    return "--warm-cache" in sys.argv


def cache_report_mode_enabled() -> bool:
    return "--cache-report" in sys.argv


def export_run_history(filename_prefix: str, payload: dict) -> Path:
    history_dir = OUTPUTS_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = history_dir / f"{filename_prefix}_{timestamp}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return path


def lean_min_score(active_screen_settings) -> int:
    return max(4, active_screen_settings.min_display_score - 3)


def watch_min_score(active_screen_settings) -> int:
    return max(0, active_screen_settings.min_display_score - 7)


def pitcher_props_discord_core_limit() -> int:
    return int(os.environ.get("PITCHER_PROPS_DISCORD_CORE_LIMIT", "5"))


def pitcher_props_discord_watch_limit() -> int:
    return int(os.environ.get("PITCHER_PROPS_DISCORD_WATCH_LIMIT", "5"))


def print_cache_report() -> None:
    files = sorted(path for path in CACHE_DIR.rglob("*.json") if path.is_file())
    total_size = sum(path.stat().st_size for path in files)
    print("Cache report:")
    print(f"- Files: {len(files)}")
    print(f"- Size: {total_size / 1024:.1f} KB")
    if files:
        print("- Examples:")
        for path in files[:10]:
            print(f"  - {path.relative_to(CACHE_DIR)}")


def relaxed_live_settings(settings):
    # Fallback should still behave like a selective shortlist, not a broad
    # discovery pass. Relax a bit for thin live slates, but keep the board
    # meaningfully filtered.
    relaxed_thresholds = replace(
        settings.thresholds,
        min_starts=max(3, settings.thresholds.min_starts - 1),
        primary_hits_last_5=max(2, settings.thresholds.primary_hits_last_5 - 1),
        support_hits_last_10=max(3, settings.thresholds.support_hits_last_10 - 2),
        min_delta=max(0.4, settings.thresholds.min_delta - 0.2),
        strong_delta=max(1.0, settings.thresholds.strong_delta - 0.4),
    )
    return replace(
        settings,
        thresholds=relaxed_thresholds,
        min_display_score=max(5, settings.min_display_score - 2),
    )


def main() -> int:
    settings = load_settings()
    warm_cache_only = warm_cache_mode_enabled()
    cache_report_only = cache_report_mode_enabled()

    if cache_report_only:
        print_cache_report()
        return 0

    shared_cache = JsonCache(CACHE_DIR / "shared", ttl_hours=settings.cache_ttl_hours)
    lines_cache = JsonCache(CACHE_DIR / "lines", ttl_hours=settings.lines_cache_ttl_minutes / 60.0)
    opponent_context_source = MlbOpponentContextSource(shared_cache)

    if settings.data_mode == "live":
        slate_source = MlbStatsApiSlateSource(shared_cache)
        if settings.line_source == "odds_api":
            lines_source = MlbOddsApiSource(settings, shared_cache, lines_cache)
        else:
            lines_source = MlbScrapeSource(shared_cache, lines_cache)
        logs_source = MlbStatsApiPitcherLogsSource(shared_cache)
    else:
        slate_source = SampleSlateSource(shared_cache)
        lines_source = SamplePitcherPropsSource(shared_cache, lines_cache)
        logs_source = SamplePitcherLogsSource(shared_cache)

    games = slate_source.fetch_games(settings.screen_date)
    if settings.data_mode == "live":
        games = [
            game
            for game in games
            if game.probable_home_pitcher_id is not None and game.probable_away_pitcher_id is not None
        ]
        if settings.line_source == "odds_api" and not settings.odds_api_key:
            print("Missing ODDS_API_KEY environment variable for DATA_MODE=live.", file=sys.stderr)
            return 1
    prop_lines = lines_source.fetch_prop_lines(games, settings.supported_prop_types)
    diagnostics = line_source_diagnostics(lines_source)
    coverage_status = (
        line_coverage_status(diagnostics, len(prop_lines), len(games))
        if settings.data_mode == "live"
        else "ok"
    )
    if settings.data_mode == "live":
        logs_by_pitcher = logs_source.fetch_logs_for_lines(prop_lines, season=settings.screen_date.year)
        starter_logs_by_pitcher = logs_source.fetch_logs_for_games(games, season=settings.screen_date.year)
    else:
        logs_by_pitcher = logs_source.fetch_logs_for_lines(prop_lines)
        starter_logs_by_pitcher = logs_source.fetch_logs_for_games(games)
    matchup_contexts = lines_source.fetch_matchup_contexts(settings.screen_date)
    if settings.data_mode == "live":
        matchup_contexts = opponent_context_source.build_matchup_contexts(
            prop_lines=prop_lines,
            logs_by_pitcher=starter_logs_by_pitcher,
            base_contexts=matchup_contexts,
            season=settings.screen_date.year,
        )
    starter_board = build_daily_starter_board(
        settings=settings,
        games=games,
        prop_lines=prop_lines,
        logs_by_pitcher=starter_logs_by_pitcher,
        matchup_contexts=matchup_contexts,
        min_starts=settings.thresholds.min_starts,
    )

    if warm_cache_only:
        snapshot_path = None
        if settings.data_mode == "live":
            snapshot_path = maybe_export_live_scrape_snapshot(
                lines_source=lines_source,
                screen_date=settings.screen_date,
                prop_line_count=len(prop_lines),
                game_count=len(games),
            )
        print("Cache warm-up summary:")
        print(f"- Data mode: {settings.data_mode}")
        print(f"- Games loaded: {len(games)}")
        print(f"- Prop lines loaded: {len(prop_lines)}")
        print(f"- Pitchers with logs: {len(logs_by_pitcher)}")
        print(f"- Starter board entries: {len(starter_board)}")
        if diagnostics:
            rendered = render_live_diagnostics(diagnostics, prop_lines)
            if rendered:
                print(f"- {rendered.removeprefix('Live line diagnostics: ')}")
            coverage_warning = render_line_coverage_warning(coverage_status, diagnostics, len(prop_lines), len(games))
            if coverage_warning:
                print(f"- {coverage_warning}")
            if snapshot_path is not None:
                print(f"- Diagnostics snapshot: {snapshot_path}")
        return 0

    result = screen_pitcher_props(
        settings=settings,
        prop_lines=prop_lines,
        logs_by_pitcher=logs_by_pitcher,
        matchup_contexts=matchup_contexts,
    )
    used_relaxed_live_pass = False
    active_screen_settings = settings
    if settings.data_mode == "live" and not result.candidates:
        active_screen_settings = relaxed_live_settings(settings)
        result = screen_pitcher_props(
            settings=active_screen_settings,
            prop_lines=prop_lines,
            logs_by_pitcher=logs_by_pitcher,
            matchup_contexts=matchup_contexts,
        )
        used_relaxed_live_pass = True

    if not warm_cache_only:
        starter_board = build_daily_starter_board(
            settings=active_screen_settings,
            games=games,
            prop_lines=prop_lines,
            logs_by_pitcher=starter_logs_by_pitcher,
            matchup_contexts=matchup_contexts,
            min_starts=active_screen_settings.thresholds.min_starts,
        )

    print(
        render_candidates(
            candidates=result.candidates,
            min_score=active_screen_settings.min_display_score,
            lean_min_score=lean_min_score(active_screen_settings),
            watch_min_score=watch_min_score(active_screen_settings),
            limit=settings.display_limit,
        )
    )
    print("")
    print(render_starter_board(starter_board, limit=settings.display_limit))
    print("")
    snapshot_path = None
    if settings.data_mode == "live":
        snapshot_path = maybe_export_live_scrape_snapshot(
            lines_source=lines_source,
            screen_date=settings.screen_date,
            prop_line_count=len(prop_lines),
            game_count=len(games),
        )
        if diagnostics:
            rendered = render_live_diagnostics(diagnostics, prop_lines)
            if rendered:
                print(rendered)
            coverage_warning = render_line_coverage_warning(coverage_status, diagnostics, len(prop_lines), len(games))
            if coverage_warning:
                print(coverage_warning)
            if snapshot_path is not None:
                print(f"Diagnostics snapshot saved to {snapshot_path}")
    if used_relaxed_live_pass:
        print("Primary live thresholds produced no qualifiers, so the board was rerun with a softer live fallback profile.")
    print(
        f"Evaluated {result.evaluated_prop_lines} prop lines; "
        f"{len(result.candidates)} candidates qualified before signal/tier display filtering."
    )

    if discord_notifications_enabled():
        embeds = render_pitcher_props_discord_embeds(
            candidates=result.candidates,
            screen_date=settings.screen_date.isoformat(),
            games_count=len(games),
            prop_line_count=len(prop_lines),
            coverage_status=coverage_status,
            min_score=active_screen_settings.min_display_score,
            lean_min_score=lean_min_score(active_screen_settings),
            watch_min_score=watch_min_score(active_screen_settings),
            core_limit=pitcher_props_discord_core_limit(),
            watch_limit=pitcher_props_discord_watch_limit(),
        )
        discord_result = send_discord_embeds(discord_webhook_url(), embeds)
        if discord_result.ok:
            print("Discord notification: sent")
        else:
            print(f"Discord notification: failed ({discord_result.error or discord_result.status_code})")

    if settings.export_history:
        display_presentations = build_pitcher_presentations(
            result.candidates,
            min_score=active_screen_settings.min_display_score,
            lean_min_score=lean_min_score(active_screen_settings),
            watch_min_score=watch_min_score(active_screen_settings),
        )
        export_path = export_run_history(
            "pitcher_props",
            {
                "mode": "screen",
                "history_schema_version": PITCHER_HISTORY_SCHEMA_VERSION,
                "model_version": PITCHER_MODEL_VERSION,
                "shadow_feature_version": PITCHER_OPPORTUNITY_SHADOW_VERSION,
                "confidence_model_version": PITCHER_CONFIDENCE_MODEL_VERSION,
                "tier_policy_version": PITCHER_TIER_POLICY_VERSION,
                "display_policy_version": PITCHER_DISPLAY_POLICY_VERSION,
                "screen_date": settings.screen_date.isoformat(),
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "settings": asdict(settings),
                "screen_settings": asdict(active_screen_settings),
                "run_note": settings.run_note,
                "line_coverage_status": coverage_status,
                "line_coverage": {
                    "games": len(games),
                    "prop_lines": len(prop_lines),
                    "floor": live_coverage_floor(len(games)),
                    "diagnostics": diagnostics,
                },
                "candidates": [asdict(item) for item in result.candidates],
                "displayed_candidates": [
                    asdict(item)
                    for item in result.candidates
                    if candidate_tier(
                        item,
                        active_screen_settings.min_display_score,
                        lean_min_score(active_screen_settings),
                        watch_min_score(active_screen_settings),
                    )
                    == "core"
                ],
                "lean_candidates": [
                    asdict(item)
                    for item in result.candidates
                    if candidate_tier(
                        item,
                        active_screen_settings.min_display_score,
                        lean_min_score(active_screen_settings),
                        watch_min_score(active_screen_settings),
                    )
                    == "lean"
                ],
                "watch_candidates": [
                    asdict(item)
                    for item in result.candidates
                    if candidate_tier(
                        item,
                        active_screen_settings.min_display_score,
                        lean_min_score(active_screen_settings),
                        watch_min_score(active_screen_settings),
                    )
                    == "watch"
                ],
                "display_rankings": display_rankings_payload(display_presentations),
                "model_opinions": [
                    asdict(item)
                    for item in starter_board
                    if item.strikeout_line is not None and item.lean_side is not None
                ],
                "result": asdict(result),
                "starter_board": [asdict(item) for item in starter_board],
            },
        )
        print(f"History exported to {export_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
