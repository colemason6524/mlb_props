from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import OUTPUTS_DIR

RUN_STATUS_PATH = OUTPUTS_DIR / "run_status.json"


def _read_status() -> dict:
    if not RUN_STATUS_PATH.exists():
        return {}
    try:
        return json.loads(RUN_STATUS_PATH.read_text())
    except (ValueError, OSError):
        return {}


def record_run(
    *,
    outcome: str,
    task: str,
    message: str = "",
    screen_date: str | None = None,
) -> dict:
    status = _read_status()
    status[task] = {
        "outcome": outcome,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "message": message,
        "screen_date": screen_date,
    }
    RUN_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUN_STATUS_PATH.write_text(json.dumps(status, indent=2, sort_keys=True))
    return status


def previous_outcome(task: str) -> str | None:
    return _read_status().get(task, {}).get("outcome")


def previous_timestamp_utc(task: str) -> str | None:
    return _read_status().get(task, {}).get("timestamp_utc")


def last_run_failed(task: str) -> bool:
    return previous_outcome(task) == "failed"


def clear_task(task: str) -> None:
    status = _read_status()
    status.pop(task, None)
    RUN_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUN_STATUS_PATH.write_text(json.dumps(status, indent=2, sort_keys=True))
