"""MLB injury check: fetch ESPN injuries + probable-pitcher cross-check,
compute model adjustments, and write the per-date artifact the forecast
board consumes BEFORE it builds predictions.

Pipeline stage order: pitcher collection -> game markets -> INJURY CHECK
-> board. The board reads outputs/injuries/injury_adjustments_{date}.json
and applies SP overrides, offense debits, and scratched-SP prop filtering.

A degraded injury feed does NOT fail the pipeline: the artifact records
``status: degraded`` and the board publishes with the MLBAM slate alone,
flagged visibly. Only a total slate failure exits nonzero.

Usage:
  python3 run_injury_check.py --date 2026-10-13
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mlb_props.cache import JsonCache
from mlb_props.config import CACHE_DIR
from mlb_props.forecasting import game_data
from mlb_props.injuries import build_adjustments
from mlb_props.sources.espn_injuries import EspnInjurySource

INJURY_DIR = ROOT / "outputs" / "injuries"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build MLB injury adjustments.")
    parser.add_argument("--date", default=None, help="Screen date YYYY-MM-DD; defaults to today.")
    return parser.parse_args(argv)


def artifact_path(screen: str) -> Path:
    return INJURY_DIR / f"injury_adjustments_{screen}.json"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    screen = args.date or date.today().isoformat()

    try:
        slate = game_data.fetch_slate(screen)
    except Exception as exc:
        print(f"[injuries] slate fetch failed: {type(exc).__name__}: {exc}")
        return 1
    if not slate:
        print(f"[injuries] no games scheduled for {screen}; writing empty artifact")
        adjustments = {
            "screen_date": screen,
            "generated_at": date.today().isoformat(),
            "status": "no_slate",
            "espn_reachable": True,
            "games": {},
            "injuries": [],
            "errors": [],
        }
    else:
        injury_cache = JsonCache(CACHE_DIR / "injuries", ttl_hours=6)
        id_cache = JsonCache(CACHE_DIR / "injuries" / "player_ids", ttl_hours=24 * 7)
        stats_cache = JsonCache(CACHE_DIR / "injuries" / "season_stats", ttl_hours=12)
        source = EspnInjurySource(injury_cache)
        adjustments = build_adjustments(screen, slate, id_cache, stats_cache, source)
        adjustments["generated_at"] = datetime.now(timezone.utc).isoformat()

    INJURY_DIR.mkdir(parents=True, exist_ok=True)
    path = artifact_path(screen)
    path.write_text(json.dumps(adjustments, indent=1, default=str))

    n_games = len(adjustments["games"])
    n_out = sum(1 for i in adjustments["injuries"] if i.get("impact_level") in ("sp_scratch", "key_hitter"))
    n_notes = sum(len(g.get("notes", [])) for g in adjustments["games"].values())
    print(
        f"[injuries] screen={screen} status={adjustments['status']} "
        f"games={n_games} impactful_injuries={n_out} notes={n_notes} -> {path.name}"
    )
    for pk, entry in adjustments["games"].items():
        for note in entry.get("notes", []):
            print(f"[injuries]   game {pk}: {note}")
    if adjustments.get("errors"):
        for err in adjustments["errors"]:
            print(f"[injuries]   WARNING: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
