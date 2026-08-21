from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from mlb_props.run_ledger import (
    RUN_STATUS_PATH,
    _read_status,
    clear_task,
    last_run_failed,
    previous_outcome,
    record_run,
)


class RunLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._root = Path(self._tmp.name)
        self._status_path = self._root / "run_status.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _use_status_path(self) -> None:
        patch_run = patch("mlb_props.run_ledger.RUN_STATUS_PATH", self._status_path)
        patch_run.start()
        self.addCleanup(patch_run.stop)

    def test_record_run_writes_status(self) -> None:
        self._use_status_path()
        record_run(outcome="success", task="pitcher_props", screen_date="2026-08-05")
        saved = _read_status()
        self.assertEqual(saved["pitcher_props"]["outcome"], "success")
        self.assertEqual(saved["pitcher_props"]["screen_date"], "2026-08-05")

    def test_previous_outcome_and_failure_flag(self) -> None:
        self._use_status_path()
        self.assertIsNone(previous_outcome("pitcher_props"))
        self.assertFalse(last_run_failed("pitcher_props"))
        record_run(outcome="failed", task="pitcher_props", message="boom")
        self.assertEqual(previous_outcome("pitcher_props"), "failed")
        self.assertTrue(last_run_failed("pitcher_props"))

    def test_clear_task_removes_entry(self) -> None:
        self._use_status_path()
        record_run(outcome="failed", task="pitcher_props")
        clear_task("pitcher_props")
        self.assertIsNone(previous_outcome("pitcher_props"))


class RecoveryTests(unittest.TestCase):
    def test_no_recorded_run_means_no_recovery(self) -> None:
        from run_pitcher_recovery import should_recover

        with patch("run_pitcher_recovery.previous_outcome", return_value=None):
            recover, _ = should_recover()
        self.assertFalse(recover)

    def test_successful_previous_run_means_no_recovery(self) -> None:
        from run_pitcher_recovery import should_recover

        with patch("run_pitcher_recovery.previous_outcome", return_value="success"):
            recover, _ = should_recover()
        self.assertFalse(recover)

    def test_failed_run_within_window_triggers_recovery(self) -> None:
        from run_pitcher_recovery import should_recover

        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with patch("run_pitcher_recovery.previous_outcome", return_value="failed"), patch(
            "run_pitcher_recovery.previous_timestamp_utc", return_value=recent
        ):
            recover, reason = should_recover()
        self.assertTrue(recover)
        self.assertIn("recovery eligible", reason)

    def test_failed_run_beyond_window_does_not_recover(self) -> None:
        from run_pitcher_recovery import should_recover

        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        with patch("run_pitcher_recovery.previous_outcome", return_value="failed"), patch(
            "run_pitcher_recovery.previous_timestamp_utc", return_value=old
        ), patch("run_pitcher_recovery.recovery_window_hours", return_value=12):
            recover, reason = should_recover()
        self.assertFalse(recover)
        self.assertIn("window", reason)


if __name__ == "__main__":
    unittest.main()
