"""Build the forecast-first board for a screen date.

Reads the day's existing exports (pitcher props, game markets),
applies the fitted engine artifacts, attaches the most recent pre-game
price per proposition, computes EV (display only -- price never flips a
pick), writes the board JSON, appends the two family ledgers, and renders
terminal + Discord output. Dry-run by default; --send-discord posts.

Usage:
  python3 run_forecast_board.py --date 2026-09-08
  python3 run_forecast_board.py --date 2026-09-08 --game-markets-dir game_markets_from_windows/sep2026
  python3 run_forecast_board.py --date 2026-09-08 --send-discord
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mlb_props.calibration import IsotonicCalibrator, LogisticModel
from mlb_props.config import OUTPUTS_DIR
from mlb_props.forecasting.game_runs import GameEngineFit, forecast_game
from mlb_props.forecasting import game_data
from mlb_props.forecasting.pitcher_k import (
    PitcherFeatures,
    PitcherEngineResult,
    line_probabilities_from_logit,
    pick_side,
)
from mlb_props.notifiers.discord import send_discord_message
from mlb_props.run_ledger import record_run

ENGINES_DIR = ROOT / "mlb_props/artifacts"
BOARD_DIR = OUTPUTS_DIR / "forecast_boards"
LEDGER_DIR = OUTPUTS_DIR / "ledger"
FORECAST_LEDGER = LEDGER_DIR / "forecast_ledger.jsonl"
ROI_LEDGER = LEDGER_DIR / "picks_roi.jsonl"
DELIVERY_LEDGER = LEDGER_DIR / "discord_delivery.jsonl"
HISTORY_RETENTION_DAYS = 400
BOARD_RETENTION_DAYS = 45
CACHE_RETENTION_DAYS = 400
DISCORD_CHUNK_LIMIT = 1900
START_BUFFER_MINUTES = 10
SLOT_LABELS = {"noon": "Noon Board", "afternoon": "Afternoon Update"}


# ------------------------------------------------------------------ prices


def american_payout(price: int | None) -> float | None:
    if price is None:
        return None
    if price > 0:
        return price / 100.0
    return 100.0 / abs(price)


def expected_value(pick_prob: float, price: int | None) -> float | None:
    payout = american_payout(price)
    if payout is None:
        return None
    return pick_prob * payout - (1.0 - pick_prob)


def ev_flag(ev: float | None) -> str:
    if ev is None:
        return "unpriced"
    if ev >= 0.03:
        return "playable"
    if ev >= -0.02:
        return "thin"
    return "no_value"


def norm_name(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


# ------------------------------------------------------------ scheduling


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def start_times_from_pitcher_export(export: dict) -> dict[str, str]:
    starts = {}
    for game in export.get("slate_games") or []:
        game_id = game.get("game_id")
        if game_id is not None:
            starts[str(game_id)] = game.get("game_time_utc")
    return starts


def filter_rows_by_start(
    rows: list[dict],
    start_by_pk: dict[str, str],
    as_of: datetime,
    buffer_minutes: int = START_BUFFER_MINUTES,
) -> tuple[list[dict], dict[str, int]]:
    kept: list[dict] = []
    stats = {"started": 0, "too_close": 0, "unknown_start": 0}
    cutoff = as_of + timedelta(minutes=buffer_minutes)
    for row in rows:
        start = parse_utc(start_by_pk.get(str(row.get("game_pk"))))
        if start is None:
            stats["unknown_start"] += 1
            kept.append(row)
            continue
        if start <= as_of:
            stats["started"] += 1
            continue
        if start <= cutoff:
            stats["too_close"] += 1
            continue
        kept.append(row)
    return kept, stats


def export_age_minutes(path: Path, as_of: datetime) -> float | None:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    exported = parse_utc(payload.get("exported_at"))
    if exported is None:
        return None
    return (as_of - exported).total_seconds() / 60.0


def delivery_already_sent(screen: str, slot: str | None) -> bool:
    if slot is None or not DELIVERY_LEDGER.exists():
        return False
    for line in DELIVERY_LEDGER.read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if (
            record.get("screen_date") == screen
            and record.get("slot") == slot
            and record.get("status") == "sent"
        ):
            return True
    return False


def record_delivery(screen: str, slot: str | None, run_id: str, status: str, chunks: int) -> None:
    if slot is None:
        return
    DELIVERY_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "screen_date": screen,
        "slot": slot,
        "run_id": run_id,
        "status": status,
        "chunks": chunks,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    with DELIVERY_LEDGER.open("a") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


# ----------------------------------------------------------------- engines


def load_pitcher_engine() -> PitcherEngineResult | None:
    path = ENGINES_DIR / "pitcher_engine_artifact.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    return PitcherEngineResult(
        logit=LogisticModel.from_dict(payload["logit"]),
        isotonic=None,
        factor=float(payload.get("factor", 1.0)),
        dispersion=float(payload.get("dispersion", 6.0)),
        n_rows=int(payload.get("n_rows", 0)),
    )


def load_game_engine() -> GameEngineFit | None:
    path = ENGINES_DIR / "game_engine_artifact.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text())
    isotonic = payload.get("ml_isotonic")
    return GameEngineFit(
        weights=list(payload["weights"]),
        means=list(payload["means"]),
        stds=list(payload["stds"]),
        resid_scale=float(payload["resid_scale"]),
        ml_isotonic=IsotonicCalibrator.from_dict(isotonic) if isotonic else None,
        n_games=int(payload.get("fitted_on", 0)),
    )


# ------------------------------------------------------------- board rows


def pitcher_rows(export: dict, engine: PitcherEngineResult) -> list[dict]:
    rows = []
    for cand in export.get("candidates") or []:
        if str(cand.get("prop_type") or "") != "PITCHER_STRIKEOUTS":
            continue
        line = cand.get("line")
        proj = cand.get("projected_strikeouts")
        if line is None or proj is None:
            continue
        opportunity = cand.get("opportunity_shadow") or {}
        recency = cand.get("recency_shadow") or {}
        features = PitcherFeatures(
            game_pk=str(cand.get("event_id") or "") or None,
            screen_date=str(export.get("screen_date") or ""),
            pitcher_name=cand.get("subject_name") or "unknown",
            line=float(line),
            projected_strikeouts=float(proj),
            projected_outs=float(cand.get("projected_outs") or 0.0),
            projected_batters_faced=float(cand.get("projected_batters_faced") or 0.0),
            season_prop_avg=float(cand.get("season_avg") or 0.0),
            avg_last_10=float(cand.get("avg_last_10") or 0.0),
            avg_last_5=float(cand.get("avg_last_5") or 0.0),
            avg_walk_rate_last_5=cand.get("avg_walk_rate_last_5"),
            workload_stability=cand.get("workload_stability"),
            opponent_k_rate_vs_hand=cand.get("opponent_k_rate_vs_hand"),
            opponent_outs_factor=cand.get("opponent_outs_factor"),
            park_run_factor=cand.get("park_run_factor"),
            moneyline=cand.get("moneyline"),
            days_since_last_start=opportunity.get("days_since_last_start"),
            season_k_rate_recency=recency.get("season_k_rate"),
            k_rate_last_10_recency=recency.get("k_rate_last_10"),
            k_rate_last_5_recency=recency.get("k_rate_last_5"),
        )
        probs = line_probabilities_from_logit(engine, features)
        side, p_side = pick_side(probs)
        if side is None:
            continue
        shadow = cand.get("price_shadow") or {}
        price = shadow.get("over_price") if side == "over" else shadow.get("under_price")
        ev = expected_value(p_side, price)
        rows.append({
            "family": "pitcher_k",
            "proposition_id": f"p:{features.game_pk or 'nogame'}:{norm_name(features.pitcher_name)}:{features.line}",
            "game_pk": features.game_pk,
            "subject": features.pitcher_name,
            "team": cand.get("team"),
            "opponent": cand.get("opponent"),
            "pick": side,
            "line": features.line,
            "p_pick": round(p_side, 4),
            "p_over": round(probs.p_over, 4),
            "p_under": round(probs.p_under, 4),
            "p_push": round(probs.p_push, 4),
            "price": price,
            "book": shadow.get("bookmaker"),
            "source": shadow.get("source"),
            "captured_at": shadow.get("price_collected_at"),
            "ev": round(ev, 4) if ev is not None else None,
            "ev_flag": ev_flag(ev),
            "engine_version": probs.version,
            "model_side_vs_export_side": cand.get("side"),
        })
    return rows


def game_rows(
    screen: str,
    engine: GameEngineFit,
    markets_files: list[Path],
    as_of: datetime | None = None,
    filter_stats: dict[str, int] | None = None,
) -> list[dict]:
    from datetime import date as _date

    yesterday = (date.fromisoformat(screen) - __import__("datetime").timedelta(days=1)).isoformat()
    finals = [
        game for game in game_data.cache_schedule_chunks(end=_date.fromisoformat(yesterday))
        if game["status"].get("abstractGameState") == "Final"
    ]
    history = game_data.team_history_map(finals)
    slate = game_data.fetch_slate(screen)
    day_map = {screen: slate}

    # latest pre-start capture per game from the markets exports
    closing: dict[str, dict] = {}
    for path in markets_files:
        try:
            payload = json.loads(path.read_text())
        except Exception:
            continue
        if payload.get("screen_date") != screen:
            continue
        exported = payload.get("exported_at") or ""
        for game in payload.get("games") or []:
            pk = str(game.get("game_id"))
            start = game.get("start_time_utc") or ""
            try:
                pct = datetime.fromisoformat(exported.replace("Z", "+00:00"))
                spt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            except Exception:
                continue
            if pct >= spt:
                continue
            current = closing.get(pk)
            if current is None or pct > current["pct"]:
                closing[pk] = {"pct": pct, "exported_at": exported, "game": game}

    rows = []
    for game in slate:
        pk = str(game["gamePk"])
        features = game_data.build_features_pregame(game, day_map, history)
        if features is None:
            continue
        capture = closing.get(pk, {})
        markets = capture.get("game") or {}
        moneyline = markets.get("moneyline") or {}
        total = markets.get("total") or {}
        spread = markets.get("spread") or {}
        forecast = forecast_game(
            engine,
            features,
            total_line=(total.get("line") if total.get("line") is not None else None),
            run_line=(spread.get("line") if spread.get("line") is not None else None),
        )
        home_name = game["teams"]["home"]["team"].get("name", "HOME")
        away_name = game["teams"]["away"]["team"].get("name", "AWAY")

        ml_pick = "home" if forecast.p_home >= forecast.p_away else "away"
        ml_price = moneyline.get("price_a") if ml_pick == "home" else moneyline.get("price_b")
        ml_ev = expected_value(forecast.p_home if ml_pick == "home" else forecast.p_away, ml_price)
        rows.append({
            "family": "game_ml",
            "proposition_id": f"g:{pk}:ml",
            "game_pk": pk,
            "subject": f"{away_name} @ {home_name}",
            "team": None,
            "opponent": None,
            "pick": ml_pick,
            "line": None,
            "p_pick": round(forecast.p_home if ml_pick == "home" else forecast.p_away, 4),
            "p_home": round(forecast.p_home, 4),
            "p_away": round(forecast.p_away, 4),
            "price": ml_price,
            "book": markets.get("source"),
            "source": markets.get("source"),
            "captured_at": capture.get("exported_at"),
            "ev": round(ml_ev, 4) if ml_ev is not None else None,
            "ev_flag": ev_flag(ml_ev),
            "engine_version": forecast.version,
            "mean_home_runs": round(forecast.mean_home_runs, 2),
            "mean_away_runs": round(forecast.mean_away_runs, 2),
        })
        if total.get("line") is not None and forecast.p_over is not None:
            total_pick = "over" if forecast.p_over >= (forecast.p_under or 0.0) else "under"
            total_price = total.get("price_a") if total_pick == "over" else total.get("price_b")
            total_ev = expected_value(
                forecast.p_over if total_pick == "over" else (forecast.p_under or 0.0),
                total_price,
            )
            rows.append({
                "family": "game_total",
                "proposition_id": f"g:{pk}:total:{total.get('line')}",
                "game_pk": pk,
                "subject": f"{away_name} @ {home_name}",
                "team": None,
                "opponent": None,
                "pick": total_pick,
                "line": total.get("line"),
                "p_pick": round(forecast.p_over if total_pick == "over" else (forecast.p_under or 0.0), 4),
                "p_over": round(forecast.p_over, 4),
                "p_under": round(forecast.p_under if forecast.p_under is not None else 0.0, 4),
                "price": total_price,
                "book": markets.get("source"),
                "source": markets.get("source"),
                "captured_at": capture.get("exported_at"),
                "ev": round(total_ev, 4) if total_ev is not None else None,
                "ev_flag": ev_flag(total_ev),
                "engine_version": forecast.version,
                "mean_home_runs": round(forecast.mean_home_runs, 2),
                "mean_away_runs": round(forecast.mean_away_runs, 2),
            })
        if spread.get("line") is not None and forecast.p_home_cover_line is not None:
            rl_pick = "home_covers" if forecast.p_home_cover_line >= 0.5 else "away_covers"
            rl_price = spread.get("price_a") if rl_pick == "home_covers" else spread.get("price_b")
            rl_p = forecast.p_home_cover_line if rl_pick == "home_covers" else 1.0 - forecast.p_home_cover_line
            rl_ev = expected_value(rl_p, rl_price)
            rows.append({
                "family": "game_rl",
                "proposition_id": f"g:{pk}:spread:{spread.get('line')}",
                "game_pk": pk,
                "subject": f"{away_name} @ {home_name}",
                "team": None,
                "opponent": None,
                "pick": rl_pick,
                "line": spread.get("line"),
                "p_pick": round(rl_p, 4),
                "price": rl_price,
                "book": markets.get("source"),
                "source": markets.get("source"),
                "captured_at": capture.get("exported_at"),
                "ev": round(rl_ev, 4) if rl_ev is not None else None,
                "ev_flag": ev_flag(rl_ev),
                "engine_version": forecast.version,
            })
    if as_of is not None:
        start_by_pk = {str(game["gamePk"]): game.get("gameDate") for game in slate}
        rows, stats = filter_rows_by_start(rows, start_by_pk, as_of)
        if filter_stats is not None:
            for key, value in stats.items():
                filter_stats[key] = filter_stats.get(key, 0) + value
    return rows


# ---------------------------------------------------------------- ledgers


def append_jsonl(path: Path, rows: list[dict], key: tuple[str, str]) -> int:
    existing: set[tuple[str, str]] = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                record = json.loads(line)
                existing.add((str(record.get(key[0])), str(record.get(key[1]))))
            except (ValueError, KeyError):
                continue
    path.parent.mkdir(parents=True, exist_ok=True)
    appended = 0
    with path.open("a") as handle:
        for row in rows:
            marker = (str(row.get(key[0])), str(row.get(key[1])))
            if marker in existing:
                continue
            handle.write(json.dumps(row, default=str) + "\n")
            existing.add(marker)
            appended += 1
    return appended


# ----------------------------------------------------------------- render


def render_board_text(screen: str, sections: dict[str, list[dict]], slot: str | None = None) -> str:
    header = f"MLB Forecast Board - {screen}"
    if slot in SLOT_LABELS:
        header = f"{header} ({SLOT_LABELS[slot]})"
    lines = [header, ""]
    for family, rows in sections.items():
        lines.append(f"== {family} ({len(rows)}) ==")
        for row in sorted(rows, key=lambda r: r.get("p_pick") or 0.0, reverse=True)[:40]:
            price = row.get("price")
            price_txt = f" @{price:+d}" if isinstance(price, int) else " (unpriced)"
            ev = row.get("ev")
            ev_txt = f"EV {ev:+.2f}" if ev is not None else "EV n/a"
            line_txt = f" {row.get('line')}" if row.get("line") is not None else ""
            lines.append(
                f"- {row.get('subject')} | {row.get('pick')}{line_txt}{price_txt} | "
                f"p={row.get('p_pick'):.0%} | {ev_txt} [{row.get('ev_flag')}]"
            )
        lines.append("")
    return "\n".join(lines)


def chunk_messages(text: str, limit: int = DISCORD_CHUNK_LIMIT) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for line in text.splitlines(keepends=True):
        if used + len(line) > limit and current:
            chunks.append("".join(current))
            current = []
            used = 0
        current.append(line)
        used += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


# -------------------------------------------------------------------- main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the forecast-first board.")
    parser.add_argument("--date", required=True, help="Screen date YYYY-MM-DD.")
    parser.add_argument("--pitcher-export", default=None)
    parser.add_argument("--game-markets-dir", default=None)
    parser.add_argument("--game-markets-file", action="append", default=None,
                        help="Exact game-market export file(s); repeatable.")
    parser.add_argument("--slot", choices=("noon", "afternoon"), default=None,
                        help="Board revision. Sets the run id and Discord label.")
    parser.add_argument("--as-of", default=None,
                        help="Explicit UTC timestamp; enables started-event filtering.")
    parser.add_argument("--max-export-age-minutes", type=float, default=None,
                        help="Reject explicit exports older than this many minutes.")
    parser.add_argument("--run-id", default=None,
                        help="Stable run identifier for ledger idempotency. "
                             "Defaults to board-<date>[-<slot>].")
    parser.add_argument("--skip-games", action="store_true")
    parser.add_argument("--send-discord", action="store_true")
    parser.add_argument("--force-send", action="store_true",
                        help="Repost even if this date/slot was already delivered.")
    parser.add_argument("--webhook-url", default=None)
    return parser.parse_args(argv)


def latest_export(pattern: str) -> Path | None:
    matches = sorted(glob.glob(pattern))
    return Path(matches[-1]) if matches else None


def prune_old_files(path: Path, pattern: str, retention_days: int) -> int:
    """Remove runtime files older than the configured retention window."""
    if not path.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    removed = 0
    for candidate in path.glob(pattern):
        if candidate.is_file() and candidate.stat().st_mtime < cutoff:
            candidate.unlink()
            removed += 1
    return removed


def prune_runtime_files() -> int:
    history_days = int(os.environ.get("MLB_HISTORY_RETENTION_DAYS", HISTORY_RETENTION_DAYS))
    board_days = int(os.environ.get("MLB_BOARD_RETENTION_DAYS", BOARD_RETENTION_DAYS))
    cache_days = int(os.environ.get("MLB_CACHE_RETENTION_DAYS", CACHE_RETENTION_DAYS))
    return sum((
        prune_old_files(OUTPUTS_DIR / "history", "*.json", history_days),
        prune_old_files(BOARD_DIR, "*.json", board_days),
        prune_old_files(ROOT / ".cache/games", "*.json", cache_days),
    ))


def derive_run_id(screen: str, slot: str | None, explicit: str | None = None) -> str:
    if explicit:
        return explicit
    if slot:
        return f"board-{screen}-{slot}"
    return f"board-{screen}"


def board_filename(screen: str, slot: str | None = None) -> str:
    suffix = f"_{slot}" if slot else ""
    return f"forecast_board_{screen}{suffix}.json"


def required_family_errors(statuses: dict[str, str], sections: dict[str, list[dict]]) -> list[str]:
    errors = []
    for family, section in (("pitcher_k", "pitcher_k"), ("game", "game")):
        status = statuses.get(family, "missing")
        if status != "ok":
            errors.append(f"{family}={status}")
        elif not sections.get(section):
            errors.append(f"{family}=empty")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    pruned = prune_runtime_files()
    if pruned:
        print(f"pruned {pruned} expired runtime files")
    screen = args.date
    slot = args.slot
    run_id = derive_run_id(screen, slot, args.run_id)
    as_of_provided = args.as_of is not None
    as_of = parse_utc(args.as_of) or datetime.now(timezone.utc)
    filter_started = as_of_provided or slot is not None
    exported_at = datetime.now(timezone.utc).isoformat()

    pitcher_engine = load_pitcher_engine()
    game_engine = None if args.skip_games else load_game_engine()

    sections: dict[str, list[dict]] = {}
    statuses: dict[str, str] = {}
    input_meta: dict[str, object] = {}
    filter_stats = {"started": 0, "too_close": 0, "unknown_start": 0}

    pitcher_path = Path(args.pitcher_export) if args.pitcher_export else latest_export("outputs/history/pitcher_props_*.json")
    pitcher_export: dict | None = None
    if pitcher_engine is None:
        statuses["pitcher_k"] = "missing_artifact"
    elif pitcher_path is None or not pitcher_path.exists():
        statuses["pitcher_k"] = "no_export"
    else:
        pitcher_export = json.loads(pitcher_path.read_text())
        if pitcher_export.get("screen_date") != screen:
            statuses["pitcher_k"] = f"export_date_mismatch:{pitcher_export.get('screen_date')}"
        elif args.max_export_age_minutes is not None:
            age = export_age_minutes(pitcher_path, as_of)
            if age is None or age > args.max_export_age_minutes:
                statuses["pitcher_k"] = f"stale_export:{age if age is not None else 'unknown'}"
            else:
                sections["pitcher_k"] = pitcher_rows(pitcher_export, pitcher_engine)
                statuses["pitcher_k"] = "ok"
        else:
            sections["pitcher_k"] = pitcher_rows(pitcher_export, pitcher_engine)
            statuses["pitcher_k"] = "ok"
        input_meta["pitcher_export"] = {
            "path": str(pitcher_path),
            "exported_at": pitcher_export.get("exported_at"),
        }

    if filter_started and sections.get("pitcher_k"):
        starts = start_times_from_pitcher_export(pitcher_export or {})
        sections["pitcher_k"], stats = filter_rows_by_start(sections["pitcher_k"], starts, as_of)
        for key, value in stats.items():
            filter_stats[key] += value

    if args.skip_games:
        statuses["game"] = "skipped"
    elif game_engine is None:
        statuses["game"] = "missing_artifact"
    else:
        if args.game_markets_file:
            markets_files = [Path(item) for item in args.game_markets_file]
        else:
            markets_dir = Path(args.game_markets_dir) if args.game_markets_dir else Path("outputs/history")
            markets_files = sorted(markets_dir.glob("game_markets_*.json"))
        game_error = None
        if args.max_export_age_minutes is not None and args.game_markets_file:
            for path_obj in markets_files:
                try:
                    payload = json.loads(path_obj.read_text())
                except (OSError, ValueError) as exc:
                    game_error = f"unreadable_export:{type(exc).__name__}"
                    break
                if payload.get("screen_date") != screen:
                    game_error = f"export_date_mismatch:{payload.get('screen_date')}"
                    break
                age = export_age_minutes(path_obj, as_of)
                if age is None or age > args.max_export_age_minutes:
                    game_error = f"stale_export:{age if age is not None else 'unknown'}"
                    break
        if game_error:
            statuses["game"] = game_error
        else:
            try:
                sections["game"] = game_rows(
                    screen,
                    game_engine,
                    markets_files,
                    as_of=as_of if filter_started else None,
                    filter_stats=filter_stats,
                )
                statuses["game"] = "ok"
            except Exception as exc:
                statuses["game"] = f"error:{type(exc).__name__}:{exc}"
                sections["game"] = []
        if markets_files:
            input_meta["game_markets_files"] = [str(path) for path in markets_files]

    banner_rows = [
        row
        for family_rows in sections.values()
        for row in family_rows
    ]
    board = {
        "run_id": run_id,
        "screen_date": screen,
        "slot": slot,
        "as_of": as_of.isoformat(),
        "exported_at": exported_at,
        "statuses": statuses,
        "inputs": input_meta,
        "filter_stats": filter_stats,
        "sections": sections,
        "row_count": len(banner_rows),
    }
    BOARD_DIR.mkdir(parents=True, exist_ok=True)
    board_path = BOARD_DIR / board_filename(screen, slot)
    board_path.write_text(json.dumps(board, indent=1, default=str))

    family_errors = required_family_errors(statuses, sections)
    if family_errors:
        message = f"required board family unavailable: {', '.join(family_errors)}"
        print(f"ERROR: {message}; refusing to publish")
        record_run(outcome="failed", task="forecast_board", message=message, screen_date=screen)
        return 1

    forecast_rows = [
        {
            "run_id": run_id,
            "screen_date": screen,
            "family": row["family"],
            "proposition_id": row["proposition_id"],
            "pick": row["pick"],
            "line": row.get("line"),
            "p_pick": row.get("p_pick"),
            "engine_version": row.get("engine_version"),
            "price": row.get("price"),
            "book": row.get("book"),
            "source": row.get("source"),
            "captured_at": row.get("captured_at"),
            "exported_at": exported_at,
        }
        for row in banner_rows
    ]
    roi_rows = [
        {
            "run_id": run_id,
            "screen_date": screen,
            "family": row["family"],
            "proposition_id": row["proposition_id"],
            "pick": row["pick"],
            "line": row.get("line"),
            "price": row.get("price"),
            "payout": american_payout(row.get("price")),
            "outcome": "UNPRICED" if row.get("price") is None else "PENDING",
            "graded": False,
        }
        for row in banner_rows
    ]
    forecast_n = append_jsonl(FORECAST_LEDGER, forecast_rows, ("run_id", "proposition_id"))
    roi_n = append_jsonl(ROI_LEDGER, roi_rows, ("run_id", "proposition_id"))

    text = render_board_text(screen, sections, slot)
    print(text)
    print(f"\nboard rows: {len(banner_rows)} | ledger +{forecast_n} | roi +{roi_n} | statuses {statuses}")

    delivery = {"sent": 0, "failed": 0, "status": "dry_run"}
    if args.send_discord:
        webhook = args.webhook_url or os.environ.get("FORECAST_BOARD_DISCORD_WEBHOOK_URL", "")
        if not webhook:
            print("ERROR: --send-discord requires FORECAST_BOARD_DISCORD_WEBHOOK_URL")
            record_run(outcome="failed", task="forecast_board", message="missing webhook", screen_date=screen)
            return 2
        if delivery_already_sent(screen, slot) and not args.force_send:
            message = f"{screen} {slot} already delivered; refusing to repost without --force-send"
            print(f"ERROR: {message}")
            record_run(outcome="failed", task="forecast_board", message=message, screen_date=screen)
            return 3
        for chunk in chunk_messages(text):
            result = send_discord_message(webhook, chunk)
            if result.ok:
                delivery["sent"] += 1
            else:
                delivery["failed"] += 1
                print(f"discord chunk failed: {result.error or result.status_code}")
        delivery["status"] = "sent" if delivery["failed"] == 0 else "partial"
        record_delivery(screen, slot, run_id, delivery["status"], delivery["sent"])

    record_run(
        outcome="success" if delivery["failed"] == 0 else "failed",
        task="forecast_board",
        message=f"{len(banner_rows)} rows, ledger +{forecast_n}, delivery {delivery['status']}",
        screen_date=screen,
    )
    return 0 if delivery["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
