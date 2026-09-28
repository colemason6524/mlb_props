"""Atomic MLB forecast pipeline: collect fresh inputs, then publish the board.

Runs the pitcher collection, then the game-market collection, then the board
runner on exactly the exports produced by this invocation. A collection stage
that fails or produces no new export stops the pipeline, so the board can never
publish from stale inputs. A date with no games scheduled is the one deliberate
skip: the board marks the artifact and the pipeline treats it as a pass. The
whole pipeline runs under the shared task lock held by scripts/run_linux_task.sh.

Usage:
  python3 run_forecast_pipeline.py --slot noon --send-discord
  python3 run_forecast_pipeline.py --slot afternoon --send-discord
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mlb_props.config import OUTPUTS_DIR

HISTORY_DIR = OUTPUTS_DIR / "history"
BOARD_DIR = OUTPUTS_DIR / "forecast_boards"
MAX_EXPORT_AGE_MINUTES = 10
PITCHER_SCRIPT = ROOT / "run_nightly.py"
MARKETS_SCRIPT = ROOT / "run_game_markets.py"
BOARD_SCRIPT = ROOT / "run_forecast_board.py"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the atomic MLB forecast pipeline.")
    parser.add_argument("--slot", required=True, choices=("noon", "afternoon"))
    parser.add_argument("--date", default=None, help="Screen date YYYY-MM-DD; defaults to today.")
    parser.add_argument("--send-discord", action="store_true")
    parser.add_argument("--force-send", action="store_true")
    parser.add_argument("--webhook-url", default=None)
    parser.add_argument("--max-export-age-minutes", type=float, default=MAX_EXPORT_AGE_MINUTES)
    return parser.parse_args(argv)


def newest_export(pattern: str, since: float, before: set[Path] | None = None) -> Path | None:
    """Newest history export produced by this run.

    A file qualifies when it is not in the pre-stage snapshot, or (for an
    overwritten file) when its mtime is at or after the start. The snapshot
    avoids relying on filesystem timestamp precision, which can round a
    just-written file slightly before the captured start time.
    """
    matches = []
    for path in HISTORY_DIR.glob(pattern):
        if not path.is_file():
            continue
        if before is not None and path not in before:
            matches.append(path)
        elif path.stat().st_mtime >= since - 2.0:
            matches.append(path)
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _run_stage(command: list[str], env: dict[str, str], runner, cwd: Path = ROOT):
    return runner(command, env=env, cwd=str(cwd))


def board_skip_reason(screen: str, slot: str, since: float) -> str | None:
    """The board's own skip marker for this run, when it deliberately published nothing.

    The marker only counts when the board artifact was rewritten by this run, so
    a skip from an earlier run of the same date and slot cannot be re-reported.
    The mtime grace mirrors newest_export, which dodges coarse timestamp
    precision without trusting an old file.
    """
    path = BOARD_DIR / f"forecast_board_{screen}_{slot}.json"
    try:
        if path.stat().st_mtime < since - 2.0:
            return None
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    reason = payload.get("skip_reason")
    return str(reason) if reason else None


def run_pipeline(
    *,
    slot: str,
    screen: str,
    send_discord: bool,
    force_send: bool = False,
    webhook_url: str | None = None,
    max_export_age_minutes: float = MAX_EXPORT_AGE_MINUTES,
    runner=subprocess.run,
) -> int:
    start_ts = time.time()
    base_env = dict(os.environ)
    collect_env = {
        **base_env,
        "DATA_MODE": "live",
        "SEND_DISCORD": "false",
        "EXPORT_HISTORY": "true",
        "REFRESH_LINES": "true",
        "RUN_NOTE": f"scheduled {slot} pipeline",
    }

    print(f"[pipeline] slot={slot} date={screen} stage=pitcher")
    pitcher_before = set(HISTORY_DIR.glob("pitcher_props_*.json"))
    pitcher_proc = _run_stage([sys.executable, str(PITCHER_SCRIPT)], collect_env, runner)
    if pitcher_proc.returncode != 0:
        print(f"[pipeline] pitcher collection failed rc={pitcher_proc.returncode}")
        return 1
    pitcher_export = newest_export("pitcher_props_*.json", start_ts, pitcher_before)
    if pitcher_export is None:
        print("[pipeline] no fresh pitcher export produced")
        return 1

    print("[pipeline] stage=markets")
    markets_before = set(HISTORY_DIR.glob("game_markets_*.json"))
    markets_proc = _run_stage([sys.executable, str(MARKETS_SCRIPT)], collect_env, runner)
    if markets_proc.returncode != 0:
        print(f"[pipeline] market collection failed rc={markets_proc.returncode}")
        return 1
    markets_export = newest_export("game_markets_*.json", start_ts, markets_before)
    if markets_export is None:
        print("[pipeline] no fresh game-market export produced")
        return 1

    as_of = datetime.now(timezone.utc).isoformat()
    board_cmd = [
        sys.executable,
        str(BOARD_SCRIPT),
        "--date",
        screen,
        "--slot",
        slot,
        "--pitcher-export",
        str(pitcher_export),
        "--game-markets-file",
        str(markets_export),
        "--max-export-age-minutes",
        str(max_export_age_minutes),
        "--as-of",
        as_of,
        "--run-id",
        f"board-{screen}-{slot}",
    ]
    if send_discord:
        board_cmd.append("--send-discord")
    if force_send:
        board_cmd.append("--force-send")
    if webhook_url:
        board_cmd.extend(["--webhook-url", webhook_url])

    print(f"[pipeline] stage=board inputs={pitcher_export.name},{markets_export.name}")
    board_proc = _run_stage(board_cmd, base_env, runner)
    if board_proc.returncode == 0:
        skip_reason = board_skip_reason(screen, slot, start_ts)
        if skip_reason:
            # No games scheduled: the board published nothing on purpose, and
            # an empty slate is a pass rather than a gap in the record.
            print(f"[pipeline] board skipped ({skip_reason}); nothing published")
    return board_proc.returncode


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    screen = args.date or datetime.now().date().isoformat()
    return run_pipeline(
        slot=args.slot,
        screen=screen,
        send_discord=args.send_discord,
        force_send=args.force_send,
        webhook_url=args.webhook_url,
        max_export_age_minutes=args.max_export_age_minutes,
    )


if __name__ == "__main__":
    raise SystemExit(main())
