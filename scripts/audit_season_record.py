"""Recompute the MLB forecast-board season record from its own artifacts.

Read-only audit helper for docs/INTEGRITY_AUDIT_2026-10-22.md. It never writes
to outputs/; it only reads the ledgers and grade artifacts and prints tallies.

Definitions used here
---------------------
canonical (bettable)
    One graded row per logical market per screen date, priced finals only
    (price is not None), price = the recorded ledger price. This is the set
    `select_canonical_rows` builds for the consolidated board.
canonical (as-published)
    The same set including rows the board published with no price
    (price is None, e.g. `game_ml` where no market price was captured).
    Those rows are graded WIN/LOSS but contribute 0 units.
latest-revision
    `latest_summary`/`latest_roi` in the grade artifact: the newest run of
    that date only, which is what ~/dashboard `_mlb_grade` displays.
raw ledger finals
    Every WIN/LOSS/PUSH row in outputs/ledger/picks_roi.jsonl, duplicates
    included.

Usage:  .venv/bin/python scripts/audit_season_record.py [--json]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import grade_forecast_board as gfb  # noqa: E402

LEDGER = ROOT / "outputs/ledger/picks_roi.jsonl"
GRADES = ROOT / "outputs/grades"

FINALS = ("WIN", "LOSS", "PUSH")
POSTSEASON_START = "2026-09-29"


def era(screen_date: str) -> str:
    return "postseason" if screen_date >= POSTSEASON_START else "regular"


def load_ledger() -> list[dict]:
    rows: list[dict] = []
    for index, line in enumerate(LEDGER.read_text().splitlines()):
        if not line.strip():
            continue
        row = json.loads(line)
        row["_roi_index"] = index
        rows.append(row)
    return rows


def load_grade_artifacts() -> dict[str, dict]:
    artifacts: dict[str, dict] = {}
    for path in sorted(GRADES.glob("forecast_board_*.json")):
        payload = json.loads(path.read_text())
        artifacts[payload["screen_date"]] = payload
    return artifacts


def canonical_from_artifact(payload: dict) -> list[dict] | None:
    rows = (payload.get("canonical") or {}).get("rows")
    return rows or None


def canonical_from_ledger(rows: list[dict]) -> list[dict]:
    """Rebuild the consolidated set for a date whose artifact predates it.

    Uses the repo's own selector with no schedule, so it falls back to
    revision order (afternoon replaces noon) exactly as grading does when the
    schedule fetch is unavailable.
    """
    selected, _superseded, _stats = gfb.select_canonical_rows(rows, {})
    rebuilt: list[dict] = []
    for row in selected.values():
        outcome = row.get("outcome")
        rebuilt.append({**row, "result": outcome if outcome in FINALS else None})
    return rebuilt


def tally(rows: list[dict], label: str) -> dict:
    """Summarize graded rows. `result` must already be normalized."""
    decided = [r for r in rows if r.get("result") in FINALS]
    wins = sum(1 for r in decided if r["result"] == "WIN")
    losses = sum(1 for r in decided if r["result"] == "LOSS")
    pushes = sum(1 for r in decided if r["result"] == "PUSH")
    priced = [r for r in decided if r.get("price") is not None]
    unpriced = [r for r in decided if r.get("price") is None]
    units = sum(float(r.get("units") or 0.0) for r in priced)
    return {
        "label": label,
        "rows": len(rows),
        "w": wins,
        "l": losses,
        "p": pushes,
        "priced": len(priced),
        "unpriced": len(unpriced),
        "units": round(units, 3),
        "roi": round(units / len(priced), 4) if priced else None,
        "dates": len({r.get("screen_date") for r in decided if r.get("screen_date")}),
    }


def fmt(entry: dict) -> str:
    roi = f"{entry['roi'] * 100:+.2f}%" if entry["roi"] is not None else "n/a"
    return (
        f"{entry['label']}: {entry['w']}-{entry['l']}-{entry['p']} "
        f"| priced {entry['priced']} | unpriced {entry['unpriced']} "
        f"| units {entry['units']:+.3f} | roi {roi} | dates {entry['dates']}"
    )


def build() -> dict:
    ledger = load_ledger()
    artifacts = load_grade_artifacts()

    by_date: dict[str, list[dict]] = defaultdict(list)
    for row in ledger:
        by_date[row["screen_date"]].append(row)

    published: list[dict] = []
    latest: list[dict] = []
    legacy: list[dict] = []
    legacy_dates: list[str] = []

    for screen_date in sorted(by_date):
        payload = artifacts.get(screen_date)
        canonical = canonical_from_artifact(payload) if payload else None
        if canonical:
            published.extend({**r, "screen_date": screen_date} for r in canonical)
        else:
            rebuilt = canonical_from_ledger(by_date[screen_date])
            legacy.extend({**r, "screen_date": screen_date} for r in rebuilt)
            legacy_dates.append(screen_date)
        if payload:
            totals = (payload.get("latest_summary") or {}).get("totals") or {}
            roi = payload.get("latest_roi") or {}
            if totals:
                latest.append(
                    {
                        "screen_date": screen_date,
                        "w": totals.get("w", 0),
                        "l": totals.get("l", 0),
                        "p": totals.get("p", 0),
                        "priced": roi.get("plays", 0),
                        "units": roi.get("units", 0.0),
                    }
                )

    raw_finals = [
        {**r, "result": r.get("outcome")}
        for r in ledger
        if r.get("outcome") in FINALS
    ]

    combined = published + legacy

    def split(rows: list[dict], name: str) -> dict[str, dict]:
        priced = [r for r in rows if r.get("price") is not None]
        return {
            "all": tally(rows, name + " (all decided rows)"),
            "priced": tally(priced, name + " (bettable, priced only)"),
            "regular": tally(
                [r for r in priced if era(r["screen_date"]) == "regular"], name + " regular season (bettable)"
            ),
            "postseason": tally(
                [r for r in priced if era(r["screen_date"]) == "postseason"], name + " postseason (bettable)"
            ),
        }

    latest_entry = {
        "label": "latest revision only (what the dashboard shows)",
        "rows": len(latest),
        "w": sum(int(r["w"]) for r in latest),
        "l": sum(int(r["l"]) for r in latest),
        "p": sum(int(r["p"]) for r in latest),
        "priced": sum(int(r["priced"]) for r in latest),
        "unpriced": 0,
        "units": round(sum(float(r["units"]) for r in latest), 3),
        "roi": None,
        "dates": len(latest),
    }
    if latest_entry["priced"]:
        latest_entry["roi"] = round(latest_entry["units"] / latest_entry["priced"], 4)

    # Denominator integrity: the priced/units basis must agree with the W-L basis.
    phantom = [
        {
            "screen_date": r.get("screen_date"),
            "family": r.get("family"),
            "proposition_id": r.get("proposition_id"),
            "result": r.get("result"),
            "units": r.get("units"),
        }
        for r in combined
        if r.get("result") in FINALS and r.get("price") is None and r.get("units") is not None
    ]
    unpriced_wins = [
        r for r in combined if r.get("result") in FINALS and r.get("price") is None and r.get("result") == "WIN"
    ]
    priced_no_units = [
        r for r in combined if r.get("result") in FINALS and r.get("price") is not None and r.get("units") is None
    ]
    dup_finals = defaultdict(list)
    for r in raw_finals:
        dup_finals[(r.get("screen_date"), r.get("proposition_id"))].append(r)
    dup_rows = {k: v for k, v in dup_finals.items() if len(v) > 1}

    result = {
        "ledger_rows": len(ledger),
        "ledger_dates": sorted(by_date),
        "legacy_dates": legacy_dates,
        "raw_ledger": split(raw_finals, "raw ledger finals"),
        "published_canonical": split(published, "as-published canonical"),
        "season_canonical": split(combined, "full-season canonical"),
        "legacy_only": split(legacy, "legacy-date canonical"),
        "latest_revision": latest_entry,
        "denominators": {
            "phantom_units_rows": len(phantom),
            "phantom_units_total": round(sum(float(r["units"] or 0.0) for r in phantom), 3),
            "phantom_units_detail": phantom,
            "unpriced_wins": len(unpriced_wins),
            "priced_rows_without_units": len(priced_no_units),
            "raw_ledger_duplicate_finals": len(dup_rows),
            "raw_ledger_duplicate_extra_rows": sum(len(v) - 1 for v in dup_rows.values()),
            "raw_ledger_duplicate_extra_units": round(
                sum(float(v[-1].get("units") or 0.0) for v in dup_rows.values()), 3
            ),
        },
    }
    return result


def main(argv: list[str]) -> int:
    data = build()
    if "--json" in argv:
        print(json.dumps(data, indent=1, sort_keys=True))
        return 0
    print(f"ledger rows {data['ledger_rows']} over {len(data['ledger_dates'])} dates")
    print(f"dates without a canonical artifact section: {', '.join(data['legacy_dates']) or 'none'}")
    for group in ("raw_ledger", "latest_revision", "published_canonical", "legacy_only", "season_canonical"):
        section = data[group]
        print("")
        if isinstance(section, dict) and "label" in section:
            print(fmt(section))
            continue
        for key in ("all", "priced", "regular", "postseason"):
            if key in section:
                print(fmt(section[key]))
    den = data["denominators"]
    print("")
    print("denominator integrity")
    print(f"  unpriced rows carrying units (staked without a price): {den['phantom_units_rows']} "
          f"= {den['phantom_units_total']:+.3f}u")
    print(f"  unpriced wins (graded, zero units): {den['unpriced_wins']}")
    print(f"  priced rows missing units: {den['priced_rows_without_units']}")
    print(f"  raw-ledger duplicate finals: {den['raw_ledger_duplicate_finals']} keys / "
          f"{den['raw_ledger_duplicate_extra_rows']} extra rows / "
          f"{den['raw_ledger_duplicate_extra_units']:+.3f}u")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
