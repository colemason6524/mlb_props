from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from mlb_props.run_ledger import previous_outcome, previous_timestamp_utc


def recovery_window_hours() -> int:
    return int(os.environ.get("PITCHER_RECOVERY_WINDOW_HOURS", "12"))


def should_recover() -> tuple[bool, str]:
    outcome = previous_outcome("pitcher_props")
    if outcome is None:
        return False, "no recorded pitcher_props run to recover"
    if outcome != "failed":
        return False, f"previous pitcher_props run was {outcome}; no recovery needed"
    last_ts = previous_timestamp_utc("pitcher_props")
    if not last_ts:
        return True, "previous run failed; last timestamp unknown"
    try:
        last_time = datetime.fromisoformat(last_ts)
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - last_time).total_seconds() / 3600.0
    except ValueError:
        return True, "previous run failed; last timestamp unparseable"
    if age_hours > recovery_window_hours():
        return False, (
            f"previous run failed {age_hours:.1f}h ago, beyond "
            f"{recovery_window_hours()}h recovery window"
        )
    return True, f"previous run failed {age_hours:.1f}h ago; recovery eligible"


def main() -> int:
    recover, reason = should_recover()
    print(f"Recovery check: {reason}")
    if not recover:
        return 0
    print("Triggering pitcher props rerun")
    return os.system(f'"{sys.executable}" run_nightly.py')


if __name__ == "__main__":
    raise SystemExit(main())
