"""Generate the calibration evidence reports from consolidated graded data.

Outputs (report-only; no production change):
- evidence/grades/hot_hits_recalibration.json : isotonic recalibration of the
  provisional HH confidence fitted on August pool rows, evaluated honestly on
  September rows (OOF by construction).
- evidence/grades/pitcher_model_vs_market.json : paired Brier comparison of
  calibrated confidence vs the same-side no-vig market on priced, graded
  pitcher candidates.
- evidence/grades/pitcher_confidence_band_check.json : observed hit rate per
  displayed confidence band across the graded schema-8 period.
"""
from __future__ import annotations

import bisect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mlb_props import pitcher_grading as PG

PITCHER_DIR = ROOT / "evidence/history/pitcher"
HH_ROWS = ROOT / "evidence/grades/hot_hits_delivered_full_rows.json"
OUT_DIR = ROOT / "evidence/grades"


def _brier(pairs: list[tuple[float, int]]) -> float | None:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def hot_hits_recalibration() -> dict:
    rows = json.load(open(HH_ROWS))
    rows = [
        r for r in rows
        if r.get("result") in {"HIT", "MISS"} and r.get("confidence_probability") is not None
    ]
    august = [r for r in rows if r["date"] < "2026-09-01"]
    september = [r for r in rows if r["date"] >= "2026-09-01"]

    xs: list[float] = []
    ys: list[int] = []
    for r in sorted(august, key=lambda r: r["confidence_probability"]):
        xs.append(float(r["confidence_probability"]))
        ys.append(1 if r["result"] == "HIT" else 0)

    blocks = [[float(y), 1] for y in ys]  # value, weight
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0]:
            w = blocks[i][1] + blocks[i + 1][1]
            v = (blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]) / w
            blocks[i:i + 2] = [[v, w]]
            i = max(i - 1, 0)
        else:
            i += 1
    fitted: list[float] = []
    for value, weight in blocks:
        fitted.extend([value] * weight)
    assert len(fitted) == len(xs), "PAVA expansion drift"

    def bisect_left_block(p: float) -> int:
        # xs sorted; fitted[r] is the calibrated value for row r; nearest row
        # at rank >= p
        return bisect.bisect_left(xs, p)

    def evaluate(rows_data: list[dict], name: str, use_lookup: bool) -> dict:
        sel = [
            r for r in rows_data
            if r.get("confidence_probability") is not None
        ]
        before, after = [], []
        for r in sel:
            p_raw = float(r["confidence_probability"])
            y = 1 if r["result"] == "HIT" else 0
            if use_lookup:
                idx = bisect_left_block(min(p_raw, max(xs)))
                p_cal = fitted[idx]
                p_cal = min(0.90, max(0.45, p_cal))
            else:
                p_cal = p_raw
            before.append((p_raw, y))
            after.append((p_cal, y))
        base = sum(y for _p, y in before) / len(before) if before else None
        return {
            "sample": name,
            "n": len(sel),
            "raw_brier": _brier(before),
            "calibrated_brier": _brier(after),
            "base_rate_brier": (sum((base - y) ** 2 for p, y in after) / len(after)) if base is not None else None,
            "base_rate": base,
        }

    return {
        "method": "isotonic (PAVA) fitted on dated rows < 2026-09-01; applied to untouched 2026-09-01..08 rows",
        "august_fit": evaluate(august, "august_in_sample_fit", True),
        "september_oof": evaluate(september, "september_oof", True),
        "n_aug": len(august),
        "n_sep": len(september),
    }


def pitcher_eval() -> dict:
    history = PG.load_history(PITCHER_DIR, schema_version=8)
    priced = [c for c in history.candidates if (c.price_shadow or {}).get("over_price")]
    client = PG.MlbGradingClient()
    PG.resolve_candidates(priced, client)

    paired = []
    for row in priced:
        if row.outcome not in {"win", "loss"}:
            continue
        p_model = row.provisional_win_probability
        shadow = row.price_shadow
        p_market = (
            shadow.get("under_no_vig_probability")
            if row.side == "UNDER"
            else shadow.get("over_no_vig_probability")
        )
        if p_model is None or p_market is None:
            continue
        paired.append(
            {
                "date": row.screen_date.isoformat(),
                "pitcher": row.pitcher_name,
                "side": row.side,
                "line": row.line,
                "actual": row.actual,
                "outcome": row.outcome,
                "p_model": p_model,
                "p_market": p_market,
            }
        )

    model_pairs = [(p["p_model"], 1 if p["outcome"] == "win" else 0) for p in paired]
    market_pairs = [(p["p_market"], 1 if p["outcome"] == "win" else 0) for p in paired]

    # confidence band check across the whole graded schema-8 set (prices or not)
    all_graded = [c for c in history.candidates]
    bands: dict[str, dict] = {}
    for row in all_graded:
        if row.outcome not in {"win", "loss"}:
            continue
        pct = row.confidence_percentage
        if pct is None or pct < 50:
            continue
        band = "50-51" if pct <= 51 else ("52-53" if pct <= 53 else ("54-56" if pct <= 56 else "57+"))
        entry = bands.setdefault(band, {"n": 0, "wins": 0, "mean_forecast": 0.0})
        entry["n"] += 1
        entry["wins"] += 1 if row.outcome == "win" else 0
        entry["mean_forecast"] += pct / 100.0
    for band, entry in bands.items():
        entry["observed"] = entry["wins"] / entry["n"] if entry["n"] else None
        entry["mean_forecast"] /= entry["n"]

    return {
        "n_candidates_schema8": len(history.candidates),
        "n_priced": len(priced),
        "n_paired_graded": len(paired),
        "brier": {
            "model": _brier(model_pairs),
            "market": _brier(market_pairs),
        },
        "model_hit_rate": (sum(1 for _p, y in model_pairs if y == 1) / len(model_pairs)) if model_pairs else None,
        "market_mean_implied": (sum(p for p, _y in market_pairs) / len(market_pairs)) if market_pairs else None,
        "bands": bands,
        "paired": paired,
    }


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    hh = hot_hits_recalibration()
    (OUT_DIR / "hot_hits_recalibration.json").write_text(json.dumps(hh, indent=1))
    print("hot_hits_recalibration:")
    for key in ("august_fit", "september_oof"):
        s = hh[key]
        print(
            f"- {s['sample']}: n={s['n']} raw_brier={s['raw_brier']:.4f} "
            f"calibrated_brier={s['calibrated_brier']:.4f} base_rate={s['base_rate']:.1%} "
            f"base_rate_brier={s['base_rate_brier']:.4f}"
        )

    p = pitcher_eval()
    (OUT_DIR / "pitcher_model_vs_market.json").write_text(json.dumps(p, indent=1, default=str))
    mb, kb = p["brier"]["model"], p["brier"]["market"]
    print("\npitcher model vs market (paired, priced, graded schema-8 rows):")
    print(f"- n paired: {p['n_paired_graded']} (from {p['n_priced']} priced candidates)")
    print(f"- model Brier: {mb:.4f} | market Brier: {kb:.4f} | delta {mb - kb:+.4f}")
    print(f"- model hit rate: {p['model_hit_rate']:.1%} | mean market no-vig p on the same side: {p['market_mean_implied']:.1%}")
    print("- confidence bands (all graded schema-8 candidates):")
    for band, entry in sorted(p["bands"].items()):
        print(
            f"  {band}: observed {entry['observed']:.1%} (n={entry['n']}) "
            f"vs mean displayed {entry['mean_forecast']:.1%}"
        )
