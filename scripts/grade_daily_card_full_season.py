"""Resolve and grade the pre-registered Daily Unders Card over every
card-bearing snapshot in the consolidated evidence directory.

Writes evidence/grades/daily_card_summary.json + .txt. Read-only on
history data; grades via the standard strict grader (no-start voids, exact
event matching for schema 7/8)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_props import pitcher_grading as PG

HISTORY_DIR = Path(__file__).resolve().parent.parent / "evidence/history/pitcher"
OUT_DIR = Path(__file__).resolve().parent.parent / "evidence/grades"


def main() -> int:
    history = PG.load_history(HISTORY_DIR, schema_version=8)
    card = history.daily_card
    snapshots_with_card = len({row.file_name for row in card})
    print(f"Card rows loaded: {len(card)}; policy {history.daily_card_policy_version}")
    client = PG.MlbGradingClient()
    PG.resolve_candidates(card, client)
    summary = PG.daily_card_summary(history)

    pending = [row for row in card if row.outcome == "pending"]
    resolved = [row for row in card if row.outcome in {"win", "loss", "push"}]
    wins = sum(row.outcome == "win" for row in resolved)
    losses = sum(row.outcome == "loss" for row in resolved)

    lines = [
        "Daily Unders Card — pre-registered record over consolidated history",
        f"Policy: {history.daily_card_policy_version}",
        f"Snapshots with a card: {snapshots_with_card}",
        f"Card rows: {len(card)} | resolved: {len(resolved)} | pending: {len(pending)}",
        f"Record: {wins}-{losses} ({(wins / (wins + losses) * 100) if losses + wins else 0:.1f}%)",
        "",
        json.dumps(summary, indent=1, default=str)[:2200],
    ]
    print("\n".join(lines[:4]))

    counts = {}
    for row in card:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    by_date = {}
    for row in card:
        by_date.setdefault(row.screen_date.isoformat(), []).append(row.outcome)

    payload = {
        "policy": history.daily_card_policy_version,
        "rows": len(card),
        "outcome_counts": counts,
        "by_date": by_date,
        "summary": summary,
        "pre_registered_rule": "trust >=55% at n>=100; marginal 52.4-55%; kill <52.4%",
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "daily_card_summary.json").write_text(json.dumps(payload, indent=1, default=str))
    (OUT_DIR / "daily_card_summary.txt").write_text("\n".join(lines))
    print("\n".join(lines[:5]))
    print(f"\nSaved {OUT_DIR}/daily_card_summary.{{json,txt}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
