from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import run_forecast_pipeline as pipeline


class NewestExportTests(unittest.TestCase):
    def test_selects_newest_since_and_ignores_older(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "pitcher_props_old.json"
            new = root / "pitcher_props_new.json"
            old.write_text("{}")
            new.write_text("{}")
            os_time = time.time()

            os.utime(old, (os_time - 100, os_time - 100))
            os.utime(new, (os_time, os_time))
            with mock.patch.object(pipeline, "HISTORY_DIR", root):
                self.assertEqual(pipeline.newest_export("pitcher_props_*.json", os_time - 1), new)
                self.assertIsNone(pipeline.newest_export("pitcher_props_*.json", os_time + 10))

    def test_snapshot_detects_file_with_coarse_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fresh = root / "pitcher_props_fresh.json"
            fresh.write_text("{}")
            os_time = time.time()
            os.utime(fresh, (os_time - 5, os_time - 5))  # mtime below the grace window
            with mock.patch.object(pipeline, "HISTORY_DIR", root):
                self.assertEqual(
                    pipeline.newest_export("pitcher_props_*.json", os_time, set()),
                    fresh,
                )
                self.assertIsNone(
                    pipeline.newest_export("pitcher_props_*.json", os_time, {fresh})
                )


class _FakeRunner:
    def __init__(self, fail_stage: str | None = None, write_exports: bool = True):
        self.fail_stage = fail_stage
        self.write_exports = write_exports
        self.commands: list[list[str]] = []
        self.counter = 0

    def __call__(self, command, env=None, cwd=None):
        self.commands.append(list(command))
        script = Path(command[1]).name
        if self.fail_stage and script == self.fail_stage:
            return SimpleNamespace(returncode=1)
        if self.write_exports:
            self.counter += 1
            if script == "run_nightly.py":
                (pipeline.HISTORY_DIR / f"pitcher_props_{self.counter}.json").write_text("{}")
            elif script == "run_game_markets.py":
                (pipeline.HISTORY_DIR / f"game_markets_{self.counter}.json").write_text("{}")
        return SimpleNamespace(returncode=0)


class RunPipelineTests(unittest.TestCase):
    def _patched(self, root: Path):
        return mock.patch.object(pipeline, "HISTORY_DIR", root)

    def test_pitcher_failure_stops_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = _FakeRunner(fail_stage="run_nightly.py")
            with self._patched(Path(tmp)):
                code = pipeline.run_pipeline(
                    slot="noon", screen="2026-09-11", send_discord=True, runner=runner
                )
            self.assertEqual(code, 1)
            self.assertEqual(len(runner.commands), 1)

    def test_missing_fresh_pitcher_export_stops_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = _FakeRunner(write_exports=False)
            with self._patched(Path(tmp)):
                code = pipeline.run_pipeline(
                    slot="noon", screen="2026-09-11", send_discord=True, runner=runner
                )
            self.assertEqual(code, 1)
            self.assertEqual(len(runner.commands), 1)

    def test_market_failure_stops_pipeline_before_board(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = _FakeRunner(fail_stage="run_game_markets.py")
            with self._patched(Path(tmp)):
                code = pipeline.run_pipeline(
                    slot="noon", screen="2026-09-11", send_discord=True, runner=runner
                )
            self.assertEqual(code, 1)
            self.assertEqual([Path(c[1]).name for c in runner.commands], ["run_nightly.py", "run_game_markets.py"])

    def test_success_passes_exact_fresh_exports_to_board(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = _FakeRunner()
            with self._patched(Path(tmp)):
                code = pipeline.run_pipeline(
                    slot="afternoon",
                    screen="2026-09-11",
                    send_discord=True,
                    force_send=True,
                    webhook_url="https://example.test/hook",
                    runner=runner,
                )
            self.assertEqual(code, 0)
            board_command = runner.commands[-1]
            self.assertEqual(Path(board_command[1]).name, "run_forecast_board.py")
            joined = " ".join(board_command)
            self.assertIn("--slot afternoon", joined)
            self.assertIn("--send-discord", joined)
            self.assertIn("--force-send", joined)
            self.assertIn("--max-export-age-minutes 10", joined)
            self.assertIn("--as-of", joined)
            self.assertIn("--run-id board-2026-09-11-afternoon", joined)
            self.assertIn("--pitcher-export", joined)
            self.assertIn("--game-markets-file", joined)
            self.assertIn("pitcher_props_", joined)
            self.assertIn("game_markets_", joined)

    def test_board_failure_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = _FakeRunner(fail_stage="run_forecast_board.py")
            with self._patched(Path(tmp)):
                code = pipeline.run_pipeline(
                    slot="noon", screen="2026-09-11", send_discord=True, runner=runner
                )
            self.assertEqual(code, 1)

    def test_python_and_screen_propagate_to_board(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = _FakeRunner()
            with self._patched(Path(tmp)):
                pipeline.run_pipeline(
                    slot="noon", screen="2026-09-11", send_discord=False, runner=runner
                )
            board_command = runner.commands[-1]
            self.assertNotIn("--send-discord", board_command)
            self.assertIn("--date", board_command)
            self.assertEqual(board_command[board_command.index("--date") + 1], "2026-09-11")


class ParseArgsTests(unittest.TestCase):
    def test_slot_required(self) -> None:
        with self.assertRaises(SystemExit):
            pipeline.parse_args([])

    def test_defaults(self) -> None:
        args = pipeline.parse_args(["--slot", "noon"])
        self.assertEqual(args.slot, "noon")
        self.assertFalse(args.send_discord)
        self.assertEqual(args.max_export_age_minutes, 10)


if __name__ == "__main__":
    unittest.main()
