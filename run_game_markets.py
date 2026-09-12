"""Game-level market shadow collector (moneyline / run line / game total).

Observation-only nightly companion to run_nightly.py. It snapshots sportsbook
game lines (Bovada primary, Action Network/FanDuel cross-check and fallback), converts them into no-vig
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
from typing import Any

from mlb_props.cache import JsonCache
from mlb_props.config import CACHE_DIR, OUTPUTS_DIR, load_settings
from mlb_props.game_markets import GameMarketSnapshot, TwoWayPrice, market_baseline_payload, select_total_market
from mlb_props.run_ledger import record_run
from mlb_props.sources.action_network import FANDUEL_SOURCE, fetch_action_markets
from mlb_props.sources.bovada_mlb import fetch_game_markets
from mlb_props.version import (
    GAME_MARKETS_HISTORY_SCHEMA_VERSION,
    GAME_MARKETS_PRICE_SHADOW_VERSION,
    GAME_MARKETS_SOURCE_POLICY_VERSION,
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


def build_snapshot(game, primary_entry: dict | None, cross_check_entry: dict | None = None) -> GameMarketSnapshot | None:
    if primary_entry is None:
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

    primary_is_bovada = str(primary_entry.get("source") or "") == "bovada"
    # Select total market with whole->half preference before constructing snapshot.
    # Never mix line from one book with prices from another.
    if primary_is_bovada and cross_check_entry is not None:
        bovada_total = two_way(primary_entry.get("total"))
        fanduel_total = two_way(cross_check_entry.get("total"))
        selected_total, selected_source, selected_updated_at, reason = select_total_market(
            bovada_total,
            str(primary_entry.get("source") or "bovada"),
            primary_entry.get("source_updated_at"),
            fanduel_total,
            str(cross_check_entry.get("source") or FANDUEL_SOURCE),
            cross_check_entry.get("source_updated_at"),
        )
        # Build snapshot with selected total and provenance.
        if selected_total is not None:
            # When FanDuel preferred, keep Bovada total as cross-check for auditability.
            if reason == "fanduel_nonwhole_total_preferred":
                return GameMarketSnapshot(
                    game_id=game.game_id,
                    game_date=game.game_date.isoformat(),
                    home_team=game.home_team,
                    away_team=game.away_team,
                    start_time_utc=primary_entry.get("start_time_utc"),
                    moneyline=two_way(primary_entry.get("moneyline")),
                    spread=two_way(primary_entry.get("spread")),
                    total=selected_total,
                    source=str(primary_entry.get("source") or "unknown"),
                    source_updated_at=primary_entry.get("source_updated_at"),
                    cross_check_source=str(cross_check_entry.get("source")) if cross_check_entry else None,
                    cross_check_updated_at=cross_check_entry.get("source_updated_at") if cross_check_entry else None,
                    cross_check_moneyline=two_way(cross_check_entry.get("moneyline")) if cross_check_entry else None,
                    cross_check_spread=two_way(cross_check_entry.get("spread")) if cross_check_entry else None,
                    cross_check_total=bovada_total,
                    total_source=selected_source,
                    total_source_updated_at=selected_updated_at,
                    total_selection_reason=reason,
                )
            # Bovada total retained (whole but no usable FanDuel half, or already half).
            return GameMarketSnapshot(
                game_id=game.game_id,
                game_date=game.game_date.isoformat(),
                home_team=game.home_team,
                away_team=game.away_team,
                start_time_utc=primary_entry.get("start_time_utc"),
                moneyline=two_way(primary_entry.get("moneyline")),
                spread=two_way(primary_entry.get("spread")),
                total=selected_total,
                source=str(primary_entry.get("source") or "unknown"),
                source_updated_at=primary_entry.get("source_updated_at"),
                cross_check_source=str(cross_check_entry.get("source")) if cross_check_entry else None,
                cross_check_updated_at=cross_check_entry.get("source_updated_at") if cross_check_entry else None,
                cross_check_moneyline=two_way(cross_check_entry.get("moneyline")) if cross_check_entry else None,
                cross_check_spread=two_way(cross_check_entry.get("spread")) if cross_check_entry else None,
                cross_check_total=fanduel_total,
                total_source=selected_source,
                total_source_updated_at=selected_updated_at,
                total_selection_reason=reason,
            )

    # Default path: no selection swap (FanDuel fallback, no cross-check, or Bovada without FanDuel).
    primary_total = two_way(primary_entry.get("total"))
    total_src = str(primary_entry.get("source") or "unknown") if primary_total else None
    total_updated = primary_entry.get("source_updated_at") if primary_total else None
    reason_default = "primary_total_selected" if primary_total else None
    return GameMarketSnapshot(
        game_id=game.game_id,
        game_date=game.game_date.isoformat(),
        home_team=game.home_team,
        away_team=game.away_team,
        start_time_utc=primary_entry.get("start_time_utc"),
        moneyline=two_way(primary_entry.get("moneyline")),
        spread=two_way(primary_entry.get("spread")),
        total=primary_total,
        source=str(primary_entry.get("source") or "unknown"),
        source_updated_at=primary_entry.get("source_updated_at"),
        cross_check_source=str(cross_check_entry.get("source")) if cross_check_entry else None,
        cross_check_updated_at=cross_check_entry.get("source_updated_at") if cross_check_entry else None,
        cross_check_moneyline=two_way(cross_check_entry.get("moneyline")) if cross_check_entry else None,
        cross_check_spread=two_way(cross_check_entry.get("spread")) if cross_check_entry else None,
        cross_check_total=two_way(cross_check_entry.get("total")) if cross_check_entry else None,
        total_source=total_src,
        total_source_updated_at=total_updated,
        total_selection_reason=reason_default,
    )


def market_entry_for_game(markets: dict[tuple[str, str, str], dict], game: Any) -> dict | None:
    candidates = [
        entry
        for (away, home, _start), entry in markets.items()
        if away == game.away_team and home == game.home_team
    ]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    game_time = game.game_time
    if game_time.tzinfo is None:
        game_time = game_time.replace(tzinfo=timezone.utc)

    def distance(entry: dict) -> float:
        try:
            start = datetime.fromisoformat(str(entry.get("start_time_utc")).replace("Z", "+00:00"))
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            return abs((start - game_time).total_seconds())
        except (TypeError, ValueError):
            return float("inf")

    return min(candidates, key=distance)


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


def format_action_diagnostics(diagnostics: dict) -> str:
    parts = [
        f"mode={diagnostics.get('mode') or 'unknown'}",
        f"games={diagnostics.get('games_seen', 0)}",
        f"pregame={diagnostics.get('pregame_games', 0)}",
        f"fanduel={diagnostics.get('games_with_fanduel', 0)}",
        f"complete={diagnostics.get('games_with_complete_markets', 0)}",
    ]
    if diagnostics.get("error"):
        parts.append(f"error={diagnostics['error']}")
    return "Action Network diagnostics: " + ", ".join(parts)


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
    try:
        action_markets, action_diagnostics = fetch_action_markets(
            cache_dir=CACHE_DIR / "lines",
            screen_date=settings.screen_date,
        )
    except Exception as exc:
        action_markets = {}
        action_diagnostics = {"mode": "error", "error": f"{type(exc).__name__}: {exc}"}

    snapshots: list[GameMarketSnapshot] = []
    unmatched_slate_games: list[str] = []
    for game in games:
        bovada_entry = market_entry_for_game(markets_by_key, game)
        action_entry = market_entry_for_game(action_markets, game)
        primary_entry = bovada_entry or action_entry
        if primary_entry is None:
            unmatched_slate_games.append(f"{game.away_team} @ {game.home_team}")
            continue
        snapshot = build_snapshot(
            game,
            primary_entry,
            action_entry if bovada_entry is not None else None,
        )
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
        "bovada_primary": sum(1 for s in snapshots if s.source == "bovada"),
        "action_fanduel_fallback": sum(1 for s in snapshots if s.source == FANDUEL_SOURCE),
        "action_fanduel_cross_checks": sum(1 for s in snapshots if s.cross_check_source == FANDUEL_SOURCE),
        "total_fanduel_nonwhole_preferred": sum(
            1 for s in snapshots if s.total_selection_reason == "fanduel_nonwhole_total_preferred"
        ),
        "total_source_bovada": sum(1 for s in snapshots if (s.total_source or s.source) == "bovada" and s.total),
        "total_source_fanduel": sum(1 for s in snapshots if s.total_source == FANDUEL_SOURCE and s.total),
        "unmatched_slate_games": len(unmatched_slate_games),
    }
    print(
        "Coverage: "
        f"{coverage['matched_with_lines']}/{coverage['slate_games']} games matched; "
        f"ML {coverage['with_moneyline']}, RL {coverage['with_spread']}, "
        f"total {coverage['with_total']} (Bovada {coverage['total_source_bovada']}, "
        f"FanDuel {coverage['total_source_fanduel']}, pref {coverage['total_fanduel_nonwhole_preferred']}); "
        f"Bovada primary {coverage['bovada_primary']}; "
        f"Action/FanDuel fallback {coverage['action_fanduel_fallback']}; "
        f"cross-check {coverage['action_fanduel_cross_checks']}."
    )
    if unmatched_slate_games:
        print("Unmatched slate games: " + "; ".join(unmatched_slate_games))
    print(format_bovada_diagnostics(bovada_diagnostics))
    print(format_action_diagnostics(action_diagnostics))

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
                "source_policy_version": GAME_MARKETS_SOURCE_POLICY_VERSION,
                "screen_date": settings.screen_date.isoformat(),
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "run_note": settings.run_note,
                "observation_only": True,
                "coverage": coverage,
                "unmatched_slate_game_names": unmatched_slate_games,
                "source_diagnostics": {
                    "bovada": bovada_diagnostics,
                    "action_network": action_diagnostics,
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
