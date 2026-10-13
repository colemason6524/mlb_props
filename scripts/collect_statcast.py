#!/usr/bin/env python3
"""Daily Statcast collection for the MLB game model.

Pulls yesterday's Statcast data via pybaseball, aggregates to per-pitcher
daily rows, stores in the JSON cache. Raw pitch-by-pitch is never stored
(VM lean rule).

Usage:
    .venv/bin/python scripts/collect_statcast.py [--date YYYY-MM-DD]
    .venv/bin/python scripts/collect_statcast.py --backfill 2026-09-01 2026-10-12

Designed to run from a systemd timer once daily.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mlb_props.statcast import (  # noqa: E402
    StatcastPipeline,
    collect_statcast_range,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Daily Statcast collection")
    ap.add_argument("--date", default=None, help="Collect one day (YYYY-MM-DD), default yesterday")
    ap.add_argument("--backfill", nargs=2, metavar=("START", "END"),
                    help="Backfill range (YYYY-MM-DD YYYY-MM-DD)")
    ap.add_argument("--cache-root", default=".cache/statcast",
                    help="Cache root relative to repo")
    args = ap.parse_args()

    pipe = StatcastPipeline(REPO_ROOT / args.cache_root)

    if args.backfill:
        start = date.fromisoformat(args.backfill[0])
        end = date.fromisoformat(args.backfill[1])
        print(f"Backfilling {start} .. {end}")
        results = collect_statcast_range(pipe, start, end)
        print(f"days={results['days']} fresh={results['fresh']} "
              f"cached={results['cached']} errors={len(results['errors'])}")
        for e in results["errors"][:10]:
            print("  ERROR:", e)
        return 0 if not results["errors"] else 1

    day = date.fromisoformat(args.date) if args.date else date.today() - timedelta(days=1)
    out = pipe.collect_day(day)
    s = out["summary"]
    print(f"{s['date']}: pitchers={s['pitchers']} pitches={s['pitches']} "
          f"cached={out.get('cached', False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
