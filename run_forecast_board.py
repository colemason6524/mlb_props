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
import sys
from datetime import date, datetime, timezone
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

ENGINES_DIR = ROOT / "evidence/engines"
BOARD_DIR = OUTPUTS_DIR / "forecast_boards"
LEDGER_DIR = OUTPUTS_DIR / "ledger"
FORECAST_LEDGER = LEDGER_DIR / "forecast_ledger.jsonl"
ROI_LEDGER = LEDGER_DIR / "picks_roi.jsonl"
DISCORD_CHUNK_LIMIT = 1900


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


def render_board_text(screen: str, sections: dict[str, list[dict]]) -> str:
    lines = [f"MLB Forecast Board - {screen}", ""]
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
    parser.add_argument("--run-id", default=None,
                        help="Stable run identifier for ledger idempotency. "
                             "Defaults to board-<date>; pass an explicit value "
                             "for a second same-day run (e.g. an afternoon refresh).")
    parser.add_argument("--skip-games", action="store_true")
    parser.add_argument("--send-discord", action="store_true")
    parser.add_argument("--webhook-url", default=None)
    return parser.parse_args(argv)


def latest_export(pattern: str) -> Path | None:
    matches = sorted(glob.glob(pattern))
    return Path(matches[-1]) if matches else None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    screen = args.date
    run_id = args.run_id or f"board-{screen}"
    exported_at = datetime.now(timezone.utc).isoformat()

    pitcher_engine = load_pitcher_engine()
    game_engine = None if args.skip_games else load_game_engine()

    sections: dict[str, list[dict]] = {}
    statuses: dict[str, str] = {}

    pitcher_path = Path(args.pitcher_export) if args.pitcher_export else latest_export("outputs/history/pitcher_props_*.json")
    if pitcher_engine is None:
        statuses["pitcher_k"] = "missing_artifact"
    elif pitcher_path is None or not pitcher_path.exists():
        statuses["pitcher_k"] = "no_export"
    else:
        export = json.loads(pitcher_path.read_text())
        if export.get("screen_date") != screen:
            statuses["pitcher_k"] = f"export_date_mismatch:{export.get('screen_date')}"
        else:
            sections["pitcher_k"] = pitcher_rows(export, pitcher_engine)
            statuses["pitcher_k"] = "ok"

    banner_rows: list[dict] = []

    if args.skip_games:
        statuses["game"] = "skipped"
    elif game_engine is None:
        statuses["game"] = "missing_artifact"
    else:
        markets_dir = Path(args.game_markets_dir) if args.game_markets_dir else Path("outputs/history")
        markets_files = sorted(markets_dir.glob("game_markets_*.json"))
        try:
            sections["game"] = game_rows(screen, game_engine, markets_files)
            statuses["game"] = "ok"
        except Exception as exc:
            statuses["game"] = f"error:{type(exc).__name__}:{exc}"
            sections["game"] = []

    banner_rows = [
        row
        for family_rows in sections.values()
        for row in family_rows
    ]
    board = {
        "run_id": run_id,
        "screen_date": screen,
        "exported_at": exported_at,
        "statuses": statuses,
        "sections": sections,
        "row_count": len(banner_rows),
    }
    BOARD_DIR.mkdir(parents=True, exist_ok=True)
    (BOARD_DIR / f"forecast_board_{screen}.json").write_text(json.dumps(board, indent=1, default=str))

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

    text = render_board_text(screen, sections)
    print(text)
    print(f"\nboard rows: {len(banner_rows)} | ledger +{forecast_n} | roi +{roi_n} | statuses {statuses}")

    delivery = {"sent": 0, "failed": 0, "status": "dry_run"}
    if args.send_discord:
        webhook = args.webhook_url or os.environ.get("FORECAST_BOARD_DISCORD_WEBHOOK_URL", "")
        if not webhook:
            print("ERROR: --send-discord requires FORECAST_BOARD_DISCORD_WEBHOOK_URL")
            record_run(outcome="failed", task="forecast_board", message="missing webhook", screen_date=screen)
            return 2
        for chunk in chunk_messages(text):
            result = send_discord_message(webhook, chunk)
            if result.ok:
                delivery["sent"] += 1
            else:
                delivery["failed"] += 1
                print(f"discord chunk failed: {result.error or result.status_code}")
        delivery["status"] = "sent" if delivery["failed"] == 0 else "partial"

    record_run(
        outcome="success" if delivery["failed"] == 0 else "failed",
        task="forecast_board",
        message=f"{len(banner_rows)} rows, ledger +{forecast_n}, delivery {delivery['status']}",
        screen_date=screen,
    )
    return 0 if delivery["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
