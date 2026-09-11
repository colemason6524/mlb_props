"""Fit and honestly evaluate the batter 1+ hit engine.

Uses the graded Hot Hits pool rows (dates + outcomes already resolved).
Inner chronological split picks the shrink factor; the OOF window is the
untouched September rows.
Outputs evidence/engines/batter_fit.{json,txt}.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mlb_props.calibration import (
    LogisticModel,
    brier,
    log_loss,
    shrink_logit,
)
from mlb_props.forecasting.batter_hit import BatterFeatures, fit_batter_engine

ROWS_PATH = ROOT / "evidence/grades/hot_hits_delivered_full_rows.json"
OUT_DIR = ROOT / "evidence/engines"


def to_feature(row: dict) -> BatterFeatures:
    return BatterFeatures(
        game_pk=None,
        screen_date=row["date"],
        batter_name=row.get("batter_name") or "unknown",
        batter_id=row.get("batter_id"),
        team=row.get("team") or "",
        season_avg=row.get("season_avg") or 0.0,
        avg_last_10=row.get("avg_last_10") or 0.0,
        avg_last_5=row.get("avg_last_5") or 0.0,
        batting_order=row.get("batting_order"),
        hit_games_last_5=row.get("hit_games_last_5"),
        hit_games_last_10=row.get("hit_games_last_10"),
        at_bats_last_10=row.get("at_bats_last_10"),
        pitcher_hand=None,
        batter_hand=None,
        pitcher_hits_allowed_rate_last_5=row.get("pitcher_hits_allowed_rate_last_5"),
        pitcher_k_rate_last_5=row.get("pitcher_k_rate_last_5"),
        pitcher_walk_rate_last_5=row.get("pitcher_walk_rate_last_5"),
        matchup_rating=row.get("matchup_rating"),
        bvp_ab=row.get("batter_vs_pitcher_ab"),
        bvp_avg=row.get("batter_vs_pitcher_avg"),
    )


def to_y(row: dict) -> int | None:
    if row.get("result") == "HIT":
        return 1
    if row.get("result") == "MISS":
        return 0
    return None


def main() -> int:
    rows = json.load(open(ROWS_PATH))
    rows = [row for row in rows if row.get("confidence_probability") is not None and row.get("result") in {"HIT", "MISS"}]
    pairs = []
    for row in rows:
        y = to_y(row)
        if y is None:
            continue
        features = to_feature(row)
        pairs.append(
            {
                "features": features,
                "over_win": y,
                "date": row["date"],
                "p_old": row.get("confidence_probability"),
            }
        )
    fit_only = [record for record in pairs if record["date"] < "2026-09-01"]
    oof = [record for record in pairs if record["date"] >= "2026-09-01"]
    print(f"fit rows (thru 8/31): {len(fit_only)} | OOF rows (from 9/1): {len(oof)}")

    logit = LogisticModel()
    logit.fit(
        rows=[r["features"].row() for r in fit_only],
        targets=[r["over_win"] for r in fit_only],
    )

    # inner split: chronologically hold out the last 30%
    fit_only.sort(key=lambda r: r["date"])
    inner_split = int(len(fit_only) * 0.7)
    inner_train, inner_valid = fit_only[:inner_split], fit_only[inner_split:]
    inner_logit = LogisticModel()
    inner_logit.fit(
        rows=[r["features"].row() for r in inner_train],
        targets=[r["over_win"] for r in inner_train],
    )
    valid_pairs = [(inner_logit.predict_proba(r["features"].row()), r["over_win"]) for r in inner_valid]
    base_pairs = [(sum(r["over_win"] for r in inner_train) / len(inner_train), r["over_win"]) for r in inner_valid]
    raw_brier = brier(valid_pairs)
    base_brier = brier(base_pairs)
    print(f"inner-split: raw brier {raw_brier:.4f} | inner base-rate {base_brier:.4f}")

    candidates = (0.45, 0.55, 0.7, 0.85, 1.0)
    chosen, chosen_brier = None, None
    for factor in candidates:
        pairs = [
            (shrink_logit(inner_logit.predict_proba(r["features"].row()), factor), r["over_win"])
            for r in inner_valid
        ]
        score = brier(pairs)
        print(f"- factor {factor}: inner brier {score:.4f}")
        if chosen_brier is None or score < chosen_brier:
            chosen, chosen_brier = factor, score

    pairs_oof = [
        (shrink_logit(logit.predict_proba(r["features"].row()), chosen), r["over_win"])
        for r in oof
    ]
    pairs_oof_raw = [(logit.predict_proba(r["features"].row()), r["over_win"]) for r in oof]
    pairs_oof_old = [(r["p_old"], r["over_win"]) for r in oof if r["p_old"] is not None]
    base_rate_oof = sum(r["over_win"] for r in oof) / len(oof)
    pairs_base_oof = [(base_rate_oof, r["over_win"]) for r in oof]

    summary = {
        "engine": "batter-1hit-prob-v1",
        "fit_rows": len(fit_only),
        "oof_rows": len(oof),
        "oof_cutoff": "2026-09-01",
        "selected_factor": chosen,
        "in_sample_raw_brier": brier([(logit.predict_proba(r["features"].row()), r["over_win"]) for r in fit_only]),
        "oof_brier_model": brier(pairs_oof),
        "oof_brier_model_raw": brier(pairs_oof_raw),
        "oof_brier_old": brier(pairs_oof_old),
        "oof_base_rate": base_rate_oof,
        "oof_brier_base": brier(pairs_base_oof),
        "pair_log_loss_model": log_loss(pairs_oof),
        "pair_log_loss_old": log_loss(pairs_oof_old),
    }
    hit_at = sum(1 for p, y in pairs_oof if (p >= 0.5) == (y == 1))
    summary["oof_pick_hit_rate"] = hit_at / len(oof) if oof else None
    (OUT_DIR / "batter_fit.json").write_text(json.dumps(summary, indent=1, default=str))

    lines = [
        "Batter 1+ hit engine (batter-1hit-prob-v1) fit + honest OOF",
        f"- fit rows (thru 8/31): {len(fit_only)} | OOF rows (from 9/1): {len(oof)}",
        f"- selected shrink factor: {chosen} (inner Brier {chosen_brier:.4f})",
        f"- OOF Brier model: {summary['oof_brier_model']:.4f}",
        f"- OOF Brier old (provisional confidence): {summary['oof_brier_old']:.4f}" if summary['oof_brier_old'] is not None else "",
        f"- OOF base-rate Brier: {summary['oof_brier_base']:.4f}",
        f"- OOF pick hit rate (threshold 0.5): {summary['oof_pick_hit_rate']:.1%}",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "batter_fit.txt").write_text("\n".join([x for x in lines if x]))
    print("\n".join([x for x in lines if x]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
