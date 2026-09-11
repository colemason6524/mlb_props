"""Fit and honestly evaluate the pitcher strikeout engine.

Reads the consolidated evidence tree, resolves every graded candidate
against final MLB game logs, then:
  - fits the logistic + isotonic head on rows before the OOF cutoff
  - evaluates on the rows after the cutoff (rolling-origin split)
  - pairs model vs old provisional confidence vs same-side no-vig market
    (only where both-side prices exist)
Outputs evidence/engines/pitcher_fit.json + a text report.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mlb_props import pitcher_grading as PG
from mlb_props.calibration import LogisticModel, brier, log_loss, shrink_logit
from mlb_props.calibration import shrink_logit
from mlb_props.forecasting.pitcher_k import PitcherEngineResult
from mlb_props.forecasting.pitcher_k import (
    PitcherFeatures,
    fit_pitcher_engine,
    line_probabilities_from_logit,
    pick_side,
)

HISTORY_DIR = ROOT / "evidence/history/pitcher"
OUT_DIR = ROOT / "evidence/engines"
OOF_START = date(2026, 9, 1)


def load_all_candidates() -> dict[str, dict]:
    """One GradedCandidate per candidate row across every schema; features
    joined by (file, subject, line, collected_at)."""
    joined: dict[str, dict] = {}
    for path in sorted(HISTORY_DIR.rglob("pitcher_props_*.json")):
        try:
            payload = json.load(open(path))
        except Exception:
            continue
        screen_raw = payload.get("screen_date")
        if not screen_raw:
            continue
        screen = date.fromisoformat(screen_raw)
        for tier_key in ("candidates",):
            rows = payload.get(tier_key) or []
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                if str(row.get("prop_type") or "") != "PITCHER_STRIKEOUTS":
                    continue
                key = f"{path.name}|{row.get('subject_id')}|{row.get('subject_name')}|{row.get('line')}|{index}"
                graded = PG._graded_candidate_from_row(
                    row, screen_date=screen, file_name=path.name, tier="none"
                )
                joined[key] = {"row": row, "graded": graded, "index": index}
    return joined


def build_feature(row: dict, graded: PG.GradedCandidate) -> PitcherFeatures:
    opportunity = row.get("opportunity_shadow") or {}
    recency = row.get("recency_shadow") or {}
    return PitcherFeatures(
        game_pk=str(row.get("event_id") or "") or None,
        screen_date=screen_iso(graded.screen_date),
        pitcher_name=graded.pitcher_name,
        line=float(graded.line),
        projected_strikeouts=float(graded.projected_strikeouts or 0.0),
        projected_outs=float(graded.projected_outs or 0.0),
        projected_batters_faced=float(graded.projected_batters_faced or 0.0),
        season_prop_avg=float(row.get("season_avg") or 0.0),
        avg_last_10=float(row.get("avg_last_10") or 0.0),
        avg_last_5=float(row.get("avg_last_5") or 0.0),
        avg_walk_rate_last_5=row.get("avg_walk_rate_last_5"),
        workload_stability=row.get("workload_stability"),
        opponent_k_rate_vs_hand=row.get("opponent_k_rate_vs_hand"),
        opponent_outs_factor=row.get("opponent_outs_factor"),
        park_run_factor=row.get("park_run_factor"),
        moneyline=row.get("moneyline"),
        days_since_last_start=opportunity.get("days_since_last_start"),
        season_k_rate_recency=recency.get("season_k_rate"),
        k_rate_last_10_recency=recency.get("k_rate_last_10"),
        k_rate_last_5_recency=recency.get("k_rate_last_5"),
    )


def screen_iso(value: date) -> str:
    return value.isoformat()


def resolve_all(candidates: list[PG.GradedCandidate]) -> None:
    client = PG.MlbGradingClient()
    PG.resolve_candidates(candidates, client)


def old_model_probability(row: dict, graded: PG.GradedCandidate) -> float | None:
    """Map the stored price-agnostic side probability to P(over):
    OVER rows carry the over probability; UNDER rows carry 1 - p."""
    confidence = row.get("confidence_estimate") or {}
    p_side = confidence.get("win_probability")
    if p_side is None:
        return None
    p_side = float(p_side)
    if str(graded.side).upper() == "UNDER":
        return 1.0 - p_side
    return p_side


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    joined = load_all_candidates()
    values = list(joined.values())
    resolve_all([entry["graded"] for entry in values])
    print(f"resolved {len(values)} candidate rows")

    # Dedupe: one row per (pitcher, line, date). Both sides of a line have
    # an identical over-clear outcome, so keep only one representative.
    deduped: dict[str, dict] = {}
    for entry in values:
        row, graded = entry["row"], entry["graded"]
        key = (graded.pitcher_name, graded.screen_date, graded.line)
        if key in deduped:
            continue
        deduped[key] = entry
    values = list(deduped.values())
    print(f"deduped to {len(values)} distinct pitcher-line rows")

    fit_rows: list[dict] = []
    test_rows: list[dict] = []
    priced_test_rows: list[dict] = []
    for entry in values:
        row, graded = entry["row"], entry["graded"]
        if graded.outcome not in {"win", "loss", "push"}:
            continue
        if row.get("projected_strikeouts") is None:
            continue
        over_win = None
        if graded.outcome in {"win", "loss"}:
            side_cleared = 1 if graded.outcome == "win" else 0
            over_flag = 1 if str(graded.side).upper() == "OVER" else 0
            over_win = 1 if (side_cleared == (over_flag == 1)) else 0
        features = build_feature(row, graded)
        record = {
            "features": features,
                "over_win": over_win,
            "actual_k": int(graded.actual) if graded.actual is not None else None,
            "projected_k": features.projected_strikeouts,
            "date": graded.screen_date,
            "is_oof": graded.screen_date >= OOF_START,
            "p_old_over": old_model_probability(row, graded),
            "p_market_over": None,
            "price": row.get("price_shadow"),
        }
        if record["price"] and record["price"].get("over_no_vig_probability"):
            record["p_market_over"] = float(record["price"]["over_no_vig_probability"])
        fit_rows.append(record)
        if record["is_oof"]:
            test_rows.append(record)
            if record["p_market_over"] is not None:
                priced_test_rows.append(record)

    engine = fit_pitcher_engine([record for record in fit_rows if not record["is_oof"]])
    if engine is None:
        print("insufficient fit rows")
        return 1

    # inner chronological split: select the shrink factor on the tail of
    # the fit window, never on the OOF rows.
    fit_only = [
        record for record in fit_rows
        if not record["is_oof"] and record["over_win"] is not None
    ]
    fit_only.sort(key=lambda r: r["date"])
    inner_split = int(len(fit_only) * 0.7)
    inner_train, inner_valid = fit_only[:inner_split], fit_only[inner_split:]

    candidate_factors = (0.5, 0.7, 0.85, 1.0)
    best_factor, best_inner_brier = None, None
    for factor in candidate_factors:
        logit = LogisticModel()
        logit.fit(
            rows=[r["features"].row() for r in inner_train],
            targets=[r["over_win"] for r in inner_train],
        )
        pairs = [(shrink_logit(logit.predict_proba(r["features"].row()), factor), r["over_win"]) for r in inner_valid]
        score = brier(pairs)
        print(f"- inner split factor {factor}: brier {score:.4f}")
        if best_inner_brier is None or score < best_inner_brier:
            best_factor, best_inner_brier = factor, score

    # final refit on all fit rows with the inner-selected factor
    logit_final = LogisticModel()
    logit_final.fit(
        rows=[r["features"].row() for r in fit_only],
        targets=[r["over_win"] for r in fit_only],
    )
    engine = PitcherEngineResult(
        logit=logit_final,
        isotonic=None,
        factor=best_factor,
        dispersion=engine.dispersion,
        n_rows=len(fit_only),
    )
    raw_fit_pairs = [(logit_final.predict_proba(r["features"].row()), r["over_win"]) for r in fit_only]
    raw_in_sample_brier = brier(raw_fit_pairs)
    print(f"selected calibration factor: {best_factor} (inner Brier {best_inner_brier:.4f})")
    print(f"in-sample raw-logit Brier: {raw_in_sample_brier:.4f}")
    # OOF evaluation
    pairs_model, pairs_book, pairs_old, pairs_base = [], [], [], []
    for record in test_rows:
        if record["over_win"] is None:
            continue
        features = record["features"]
        probs = line_probabilities_from_logit(engine, features)
        y = record["over_win"]
        pairs_model.append((probs.p_over, y))
        if record["p_market_over"] is not None:
            pairs_book.append((record["p_market_over"], y))
        if record["p_old_over"] is not None:
            pairs_old.append((record["p_old_over"], y))
    # base rate from the fit sample
    fit_ab = [r for r in fit_rows if not r["is_oof"] and r["over_win"] is not None]
    base_rate = sum(r["over_win"] for r in fit_ab) / max(len(fit_ab), 1)
    pairs_base = [(base_rate, r["over_win"]) for r in test_rows if r["over_win"] is not None]

    paired_book = {record["features"].game_pk: record for record in priced_test_rows}
    summary = {
        "engine": "pitcher-k-dist-v1",
        "dispersion_fit": engine.dispersion,
        "fit_rows": len(fit_rows),
        "oof_rows": len(test_rows),
        "oof_cutoff": OOF_START.isoformat(),
        "model_brier": brier(pairs_model),
        "model_log_loss": log_loss(pairs_model),
        "old_model_brier": brier(pairs_old),
        "book_brier_on_priced": None,
        "model_brier_on_priced": None,
        "base_rate": base_rate,
        "push_rate_row_begin": None,
    }
    # paired with book
    model_pairs_priced = []
    book_pairs_priced = []
    bullish_rows = []
    for record in test_rows:
        if record["over_win"] is None or record["p_market_over"] is None:
            continue
        features = record["features"]
        probs = line_probabilities_from_logit(engine, features)
        bullish_rows.append(
            {
                "date": record["date"].isoformat(),
                "pitcher": features.pitcher_name,
                "line": features.line,
                "p_model": probs.p_over,
                "p_book": record["p_market_over"],
                "actual": record["actual_k"],
                "y_over": record["over_win"],
            }
        )
        model_pairs_priced.append((probs.p_over, record["over_win"]))
        book_pairs_priced.append((record["p_market_over"], record["over_win"]))
    summary["model_brier_on_priced"] = brier(model_pairs_priced)
    summary["book_brier_on_priced"] = brier(book_pairs_priced)
    summary["hit_rate_model"] = (
        sum(1 for p, y in pairs_model if (p >= 0.5) == (y == 1)) / len(pairs_model)
    ) if pairs_model else None
    summary["paired_log_loss"] = {
        "model": log_loss(pairs_model),
        "old": log_loss(pairs_old),
        "book": log_loss(book_pairs_priced),
    }

    (OUT_DIR / "pitcher_fit.json").write_text(json.dumps(summary, indent=1, default=str))
    (OUT_DIR / "pitcher_bullish_rows.json").write_text(json.dumps(bullish_rows, indent=1, default=str))

    base_rate_brier = brier(pairs_base)
    brier_old = summary["old_model_brier"]
    pairwise_book = summary["book_brier_on_priced"]
    lines = [
        "Pitcher engine (pitcher-k-dist-v1) fit + honest OOF window",
        f"- fit rows (through {OOF_START}): {len(fit_rows) - len(test_rows)}",
        f"- OOF rows (from {OOF_START.isoformat()}): {len(test_rows)}",
        f"- dispersion: {engine.dispersion:.2f}",
        f"- model Brier (all OOF): {summary['model_brier']:.4f}",
        f"- old model Brier (all OOF): {brier_old if brier_old else 'n/a'}",
        f"- base-rate Brier (no features): {base_rate_brier:.4f}" if base_rate_brier else "",
        f"- paired priced subset: model {summary['model_brier_on_priced']:.4f} vs book {pairwise_book:.4f}",
        f"- hit rate of picks (OOF): {summary['hit_rate_model']:.1%}" if summary['hit_rate_model'] is not None else "",
    ]
    report = "\n".join(lines)
    (OUT_DIR / "pitcher_fit.txt").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
