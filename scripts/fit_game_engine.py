"""Fit + rolling OOF evaluate the game-run engine on real 2026 games.

Data: MLB Stats API schedule (hydrate probablePitcher+linescore), cached
locally. Features per team side use games strictly BEFORE the screening
date (rolling). OOF folds: one per calendar month from May. The target
family for today's data is the moneyline (no game totals/lines are
confirmed per game at the schedule stage); predicted total runs error is
reported under a descriptive no-market-baseline note.

Outputs evidence/engines/game_fit.{json,txt} and caches .cache/games/."""
from __future__ import annotations

import json
import sys
import urllib.request
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mlb_props.calibration import IsotonicCalibrator, brier, log_loss
from mlb_props.forecasting.game_runs import (
    GameFeatures,
    TeamGameFeatures,
    fit_game_engine_runs,
    forecast_game,
)

from mlb_props.forecasting.game_data import (
    build_row,
    cache_dir,
    cache_schedule_chunks,
    team_history_map,
)

CACHE_DIR = cache_dir()
OUT_DIR = ROOT / "evidence/engines"




def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    finals = [
        game for game in cache_schedule_chunks()
        if game["status"].get("abstractGameState") == "Final"
    ]
    finals.sort(key=lambda g: g["gameDate"])
    history = team_history_map(finals)
    day_map: dict[str, list] = defaultdict(list)
    for game in finals:
        day_map[game["gameDate"][:10]].append(game)
    dates = sorted(day_map.keys())
    print(f"final games: {len(finals)}")

    folds: list[dict] = []
    months = sorted({d[:7] for d in dates})
    for month in [m for m in months if m >= "2026-05"]:
        first_day = f"{month}-01"
        X, y, runs = [], [], []
        for d in dates:
            if d >= first_day:
                break
            for game in day_map[d]:
                features, win, run_target = build_row(game, day_map, history)
                if features is None:
                    continue
                X.append(features)
                y.append(win)
                runs.append(run_target)
        if len(X) < 400:
            continue
        engine = fit_game_engine_runs(X, y, runs)
        if engine is None:
            continue

        pairs: list[tuple[float, int]] = []
        base: list[tuple[float, int]] = []
        for d in dates:
            if d[:7] != month:
                continue
            for game in day_map[d]:
                features, win, run_target = build_row(game, day_map, history)
                if features is None:
                    continue
                forecast = forecast_game(engine, features)
                pairs.append((forecast.p_home, win))
                base.append((0.5, win))
        if not pairs:
            continue
        folds.append({
            "month": month,
            "fit_games": len(X),
            "test_games": len(pairs),
            "model_brier": brier(pairs),
            "base_brier": brier(base),
            "model_log_loss": log_loss(pairs),
            "base_log_loss": log_loss(base),
            "model_acc": sum(1 for p, w in pairs if (p >= 0.5) == (w == 1)) / len(pairs),
        })
        print(f"{month}: fit {len(X)} test {len(pairs)} brier {folds[-1]['model_brier']:.4f} base {folds[-1]['base_brier']:.4f} acc {folds[-1]['model_acc']:.1%}")

    summary = {"engine": "game-run-model-v1", "folds": folds}
    # ship artifact: fit on ALL finals to date and persist weights so a
    # production board script can build forecasts without a refit.
    X_all, y_all, runs_all = [], [], []
    for d in dates:
        for game in day_map[d]:
            features, win, run_target = build_row(game, day_map, history)
            if features is None:
                continue
            X_all.append(features)
            y_all.append(win)
            runs_all.append(run_target)
    ship = fit_game_engine_runs(X_all, y_all, runs_all)

    # paired model-vs-book on the priced game-markets captures (these rows
    # also entered the ship fit, so treat as a descriptive check only)
    model_vs_book = None
    if ship is not None:
        gm_path = ROOT / "evidence/grades/game_markets_summary.json"
        gm_summary = json.loads(gm_path.read_text())["results"] if gm_path.exists() else []
        try:
            from game_market_gradepass import captures_by_game
        except Exception:
            captures_by_game = None
        if captures_by_game is not None:
            import math as _math

            def devig_ml(a, b):
                pa = (100 / (100 + a)) if a > 0 else (abs(a) / (abs(a) + 100))
                pb = (100 / (100 + b)) if b > 0 else (abs(b) / (abs(b) + 100))
                s = pa + pb
                return (pa / s) if s else None

            def ab_prob(price):
                if not price:
                    return None
                return (100 / (100 + price)) if price > 0 else (abs(price) / (abs(price) + 100))

            model_pairs, book_pairs = [], []
            closing_rows = {}
            for path in sorted((ROOT / "evidence/history/game_markets").glob("game_markets_*.json")):
                try:
                    payload = json.loads(path.read_text())
                except Exception:
                    continue
                exported = payload.get("exported_at")
                for g in payload.get("games", []):
                    start = g.get("start_time_utc")
                    if not start or not exported:
                        continue
                    try:
                        spt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                        pct = datetime.fromisoformat(exported.replace("Z", "+00:00"))
                    except Exception:
                        continue
                    if pct >= spt:
                        continue
                    pk = str(g.get("game_id"))
                    row = {"exported": exported, "pct": pct, "game": g}
                    cur = closing_rows.get(pk)
                    if cur is None or row["pct"] > cur["pct"]:
                        closing_rows[pk] = row
            def devig_home(a, b):
                pa = ab_prob(a)
                pb = ab_prob(b)
                if pa is None or pb is None:
                    return None
                return pa / (pa + pb)
            for pk, row in closing_rows.items():
                g = row["game"]
                ml = g.get("moneyline") or {}
                pa = ab_prob(ml.get("price_a"))
                pb = ab_prob(ml.get("price_b"))
                if pa is None or pb is None:
                    continue
                target_game = None
                for g2 in finals:
                    if str(g2["gamePk"]) == pk:
                        target_game = g2
                        break
                if target_game is None:
                    continue
                features, win, _ = build_row(target_game, day_map, history)
                if features is None or win is None:
                    continue
                forecast = forecast_game(ship, features)
                p_market_home = pa / (pa + pb)
                model_pairs.append((forecast.p_home, win))
                book_pairs.append((p_market_home, win))
            if model_pairs:
                model_vs_book = {
                    "n": len(model_pairs),
                    "model_brier": brier(model_pairs),
                    "book_brier": brier(book_pairs),
                    "note": "ship fit includes these rows; descriptive only",
                }
    summary = {"engine": "game-run-model-v1", "folds": folds, "model_vs_book": model_vs_book}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "game_fit.json").write_text(json.dumps(summary, indent=1, default=str))
    if ship is not None:
        artifact = {
            "engine": "game-run-model-v1",
            "fitted_on": len(X_all),
            "weights": ship.weights,
            "means": ship.means,
            "stds": ship.stds,
            "resid_scale": ship.resid_scale,
            "ml_isotonic": ship.ml_isotonic.to_dict() if ship.ml_isotonic is not None else None,
        }
        (OUT_DIR / "game_engine_artifact.json").write_text(json.dumps(artifact, indent=1, default=str))
    lines = [
        "Game engine (game-run-model-v1) rolling monthly OOF, model-only vs 50/50",
        "month | fit | test | model Brier | base | acc",
    ]
    for s in folds:
        lines.append(
            f"{s['month']} | {s['fit_games']} | {s['test_games']} | "
            f"{s['model_brier']:.4f} | {s['base_brier']:.4f} | {s['model_acc']:.1%}"
        )
    if model_vs_book:
        lines.append(
            f"descriptive paired check vs book on {model_vs_book['n']} priced rows: "
            f"model {model_vs_book['model_brier']:.4f} vs book {model_vs_book['book_brier']:.4f}"
        )
    (OUT_DIR / "game_fit.txt").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
