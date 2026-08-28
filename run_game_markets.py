"""Game-level market shadow collector (moneyline / run line / game total).

Observation-only nightly companion to run_nightly.py. It snapshots sportsbook
game lines (Bovada primary, ESPN cross-check), converts them into no-vig
market-baseline probabilities, and exports a versioned history file for later
grading against actual game results.

No model opinions, no Discord, no staking claims. This exists so that by the
time the game-ml / game-total shadow models are built, there is already a
price history to compare them against.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from mlb_props.cache import JsonCache
from mlb_props.config import CACHE_DIR, OUTPUTS_DIR, load_settings
from mlb_props.game_markets import GameMarketSnapshot, TwoWayPrice, market_baseline_payload
from mlb_props.run_ledger import record_run
from mlb_props.sources.bovada_mlb import fetch_game_markets
from mlb_props.sources.espn_odds import EspnMlbOddsSource
from mlb_props.version import (
    GAME_MARKETS_HISTORY_SCHEMA_VERSION,
    GAME_MARKETS_PRICE_SHADOW_VERSION,
)


def _env_flag(name: str, default: str = "true") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes"}


def export_run_history(filename_prefix: str, payload: dict):
    history_dir = OUTPUTS_DIR / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = history_dir / f"{filename_prefix}_{timestamp}.json"

    import json

    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return path


def build_snapshot(game, bovada_entry: dict | None, espn_context=None) -> GameMarketSnapshot | None:
    if bovada_entry is None:
        return None

    def two_way(payload: dict | None) -> TwoWayPrice | None:
        if not payload:
            return None
        price_a = payload.get("price_a")
        price_b = payload.get("price_b")
        if price_a is None or price_b is None:
            return None
        line = payload.get("line")
        return TwoWayPrice(line=float(line) if line is not None else None, price_a=int(price_a), price_b=int(price_b))

    return GameMarketSnapshot(
        game_id=game.game_id,
        game_date=game.game_date.isoformat(),
        home_team=game.home_team,
        away_team=game.away_team,
        start_time_utc=bovada_entry.get("start_time_utc"),
        moneyline=two_way(bovada_entry.get("moneyline")),
        spread=two_way(bovada_entry.get("spread")),
        total=two_way(bovada_entry.get("total")),
        source="bovada",
        espn_total=espn_context.total if espn_context else None,
        espn_away_spread=espn_context.away_spread if espn_context else None,
        espn_home_spread=espn_context.home_spread if espn_context else None,
    )


def render_board(snapshots: list[GameMarketSnapshot]) -> str:
    lines = [
        f"{'Matchup':<24} {'ML':>12} {'Run Line':>14} {'Total':>16} {'No-Vig Home':>12} {'No-Vig Over':>12}"
    ]
    for snapshot in snapshots:
        matchup = f"{snapshot.away_team} @ {snapshot.home_team}"

        def fmt_two_way(price: TwoWayPrice | None, with_line: bool) -> str:
            if price is None:
                return "-"
            sides = f"{_signed(price.price_a)}/{_signed(price.price_b)}"
            if not with_line or price.line is None:
                return sides
            return f"{price.line:g} ({sides})"

        baseline = market_baseline_payload(snapshot)
        home_p = baseline["home_win_no_vig"]
        over_p = baseline["over_no_vig"]
        lines.append(
            f"{matchup:<24} "
            f"{fmt_two_way(snapshot.moneyline, False):>12} "
            f"{fmt_two_way(snapshot.spread, True):>14} "
            f"{fmt_two_way(snapshot.total, True):>16} "
            f"{(f'{home_p * 100:.1f}%' if home_p is not None else '-'):>12} "
            f"{(f'{over_p * 100:.1f}%' if over_p is not None else '-'):>12}"
        )
    return "\n".join(lines)


def _signed(value: int) -> str:
    return f"+{value}" if value > 0 else str(value)


def format_bovada_diagnostics(diagnostics: dict) -> str:
    fetch = diagnostics.get("coupon_fetch") or {}
    parts = [
        f"mode={fetch.get('mode') or 'unknown'}",
        f"events={diagnostics.get('events_seen', 0)}",
        f"parsed={diagnostics.get('games_parsed', 0)}",
        f"current-day={diagnostics.get('games_matched_to_slate', 0)}",
        f"stale={diagnostics.get('stale_games_filtered', 0)}",
        f"wrong-date={diagnostics.get('wrong_date_games_filtered', 0)}",
        f"empty-markets={diagnostics.get('empty_markets_filtered', 0)}",
        f"unmatched-teams={len(diagnostics.get('unmatched_teams') or [])}",
    ]
    if fetch.get("error"):
        parts.append(f"fetch-error={fetch['error']}")
    lines = ["Bovada diagnostics: " + ", ".join(parts)]
    stale_games = diagnostics.get("stale_games") or []
    if stale_games:
        lines.append("Bovada stale games: " + "; ".join(stale_games))
    wrong_date_games = diagnostics.get("wrong_date_games") or []
    if wrong_date_games:
        lines.append("Bovada wrong-date games: " + "; ".join(wrong_date_games))
    unmatched_teams = diagnostics.get("unmatched_teams") or []
    if unmatched_teams:
        lines.append("Bovada unmatched teams: " + "; ".join(unmatched_teams))
    return "\n".join(lines)


def format_espn_diagnostics(diagnostics: dict) -> str:
    parts = [
        f"mode={diagnostics.get('mode') or 'unknown'}",
        f"parsed={diagnostics.get('games_parsed', 0)}",
        f"totals={diagnostics.get('with_total', 0)}",
        f"spreads={diagnostics.get('with_spreads', 0)}",
    ]
    if diagnostics.get("error"):
        parts.append(f"error={diagnostics['error']}")
    return "ESPN diagnostics: " + ", ".join(parts)


def main() -> int:
    try:
        exit_code, message = _run()
        record_run(
            outcome="success" if exit_code == 0 else "failed",
            task="game_markets_shadow",
            message=message,
        )
        return exit_code
    except Exception as exc:
        record_run(
            outcome="failed",
            task="game_markets_shadow",
            message=f"{type(exc).__name__}: {exc}",
        )
        raise


def _run() -> tuple[int, str]:
    settings = load_settings()
    shared_cache = JsonCache(CACHE_DIR / "shared", ttl_hours=settings.cache_ttl_hours)
    lines_cache = JsonCache(CACHE_DIR / "lines", ttl_hours=settings.lines_cache_ttl_minutes / 60.0)

    from mlb_props.sources.mlb_stats_api import MlbStatsApiSlateSource

    slate_source = MlbStatsApiSlateSource(shared_cache)
    games = slate_source.fetch_games(settings.screen_date)
    games = [
        game
        for game in games
        if game.probable_home_pitcher_id is not None and game.probable_away_pitcher_id is not None
    ]
    print(f"Slate loaded: {len(games)} games for {settings.screen_date.isoformat()}")

    refresh = _env_flag("REFRESH_LINES", "true")
    markets_by_key, bovada_diagnostics = fetch_game_markets(
        cache_dir=CACHE_DIR / "lines",
        screen_date=settings.screen_date,
        refresh=refresh,
    )
    espn_source = EspnMlbOddsSource(lines_cache)
    try:
        espn_contexts, espn_diagnostics = espn_source.fetch_game_context(settings.screen_date)
    except Exception as exc:
        espn_contexts, espn_diagnostics = {}, {"mode": "error", "error": str(exc)}

    snapshots: list[GameMarketSnapshot] = []
    unmatched_slate_games: list[str] = []
    for game in games:
        key = (game.away_team, game.home_team)
        bovada_entry = markets_by_key.get(key)
        if bovada_entry is None:
            unmatched_slate_games.append(f"{game.away_team} @ {game.home_team}")
            continue
        espn_context = espn_contexts.get(key)
        snapshot = build_snapshot(game, bovada_entry, espn_context)
        if snapshot is not None:
            snapshots.append(snapshot)

    print("")
    print(render_board(snapshots))
    print("")
    coverage = {
        "slate_games": len(games),
        "matched_with_lines": len(snapshots),
        "with_moneyline": sum(1 for s in snapshots if s.moneyline),
        "with_spread": sum(1 for s in snapshots if s.spread),
        "with_total": sum(1 for s in snapshots if s.total),
        "espn_cross_check_totals": sum(1 for s in snapshots if s.espn_total is not None),
        "unmatched_slate_games": len(unmatched_slate_games),
    }
    print(
        "Coverage: "
        f"{coverage['matched_with_lines']}/{coverage['slate_games']} games matched; "
        f"ML {coverage['with_moneyline']}, RL {coverage['with_spread']}, "
        f"total {coverage['with_total']}; ESPN cross-check {coverage['espn_cross_check_totals']} totals."
    )
    if unmatched_slate_games:
        print("Unmatched slate games: " + "; ".join(unmatched_slate_games))
    print(format_bovada_diagnostics(bovada_diagnostics))
    print(format_espn_diagnostics(espn_diagnostics))

    empty_message = ""
    if not snapshots:
        empty_message = (
            "no game market snapshots collected; "
            f"coverage {coverage['matched_with_lines']}/{coverage['slate_games']}"
        )
        print(
            "Warning: no game market snapshots collected; treat this run as unsuitable for evaluation.",
            file=sys.stderr,
        )

    # Export even on empty coverage so scheduled runs leave a diagnosed history
    # file instead of a silent gap. Empty files are unsuitable for evaluation.
    if settings.export_history:
        export_path = export_run_history(
            "game_markets",
            {
                "history_schema_version": GAME_MARKETS_HISTORY_SCHEMA_VERSION,
                "price_shadow_version": GAME_MARKETS_PRICE_SHADOW_VERSION,
                "screen_date": settings.screen_date.isoformat(),
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "run_note": settings.run_note,
                "observation_only": True,
                "coverage": coverage,
                "unmatched_slate_game_names": unmatched_slate_games,
                "source_diagnostics": {
                    "bovada": bovada_diagnostics,
                    "espn": espn_diagnostics,
                },
                "games": [
                    {**snapshot.as_dict(), "market_baseline": market_baseline_payload(snapshot)}
                    for snapshot in snapshots
                ],
            },
        )
        print(f"History exported to {export_path}")

    return 0, empty_message


if __name__ == "__main__":
    raise SystemExit(main())
