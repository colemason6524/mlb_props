"""Unit tests for checkpoint validation, countdowns, and file helpers."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.checkpoint import (  # noqa: E402
    Checkpoint,
    CheckpointError,
    append_entry,
    is_substantive_path,
    load_checkpoint,
    load_current,
    load_journal,
)
from dashboard.countdown import DUE, PENDING, UNKNOWN, count_game_days, evaluate  # noqa: E402
from dashboard.files import normalize_dates, run_jobs_local  # noqa: E402

TZ = "America/Detroit"


def write_checkpoint(tmp: Path, **overrides) -> Path:
    data = {
        "schema_version": 1,
        "project_id": "demo",
        "updated_at": "2026-09-20T12:00:00-04:00",
        "status": "observing",
        "summary": "Testing something.",
        "check_back": {
            "kind": "game_days",
            "target": 7,
            "label": "after a week of games",
            "baseline": "2026-09-20T12:00:00-04:00",
        },
    }
    data.update(overrides)
    path = tmp / "checkpoint.json"
    path.write_text(json.dumps(data))
    return path


class TestCheckpoint(unittest.TestCase):
    def test_valid_load(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_checkpoint(Path(d))
            c = load_checkpoint(path, project_id="demo")
            self.assertEqual(c.check_back.kind, "game_days")
            self.assertEqual(c.check_back.target, 7)
            self.assertTrue(c.check_back.baseline.tzinfo)

    def test_project_id_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_checkpoint(Path(d))
            with self.assertRaises(CheckpointError):
                load_checkpoint(path, project_id="other")

    def test_missing_target_for_game_days(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_checkpoint(
                Path(d), check_back={"kind": "game_days", "label": "later"}
            )
            with self.assertRaises(CheckpointError):
                load_checkpoint(path, project_id="demo")

    def test_bad_status(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_checkpoint(Path(d), status="weird")
            with self.assertRaises(CheckpointError):
                load_checkpoint(path, project_id="demo")

    def test_target_date_requires_grade_event(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_checkpoint(
                Path(d),
                check_back={
                    "kind": "calendar",
                    "target": 7,
                    "label": "x",
                    "target_date": "2026-09-27",
                },
            )
            with self.assertRaises(CheckpointError):
                load_checkpoint(path, project_id="demo")

    def test_target_date_bad_format(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_checkpoint(
                Path(d),
                check_back={
                    "kind": "grade_event",
                    "label": "x",
                    "target_date": "not-a-date",
                },
            )
            with self.assertRaises(CheckpointError):
                load_checkpoint(path, project_id="demo")

    def test_journal_loads_last_entry_and_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            journal = root / "journal.jsonl"
            first = write_checkpoint(root, updated_at="2026-09-01T12:00:00-04:00")
            first.unlink()  # only the journal should matter
            journal.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "project_id": "demo",
                        "updated_at": "2026-09-10T12:00:00-04:00",
                        "status": "observing",
                        "summary": "first",
                        "check_back": {"kind": "calendar", "target": 3, "label": "a"},
                    }
                )
                + "\n"
                + "not-json\n"
                + json.dumps(
                    {
                        "schema_version": 1,
                        "project_id": "demo",
                        "updated_at": "2026-09-20T12:00:00-04:00",
                        "status": "observing",
                        "summary": "second",
                        "check_back": {"kind": "calendar", "target": 3, "label": "b"},
                    }
                )
                + "\n"
            )
            current, entries, warning = load_current(journal, project_id="demo")
            self.assertEqual(current.summary, "second")
            self.assertEqual(len(entries), 2)
            self.assertIn("1 invalid", warning)

    def test_target_date_parses(self):
        with tempfile.TemporaryDirectory() as d:
            path = write_checkpoint(
                Path(d),
                check_back={
                    "kind": "grade_event",
                    "label": "after Sep 27 graded",
                    "target_date": "2026-09-27",
                    "baseline": "2026-09-26T00:00:00-04:00",
                },
            )
            c = load_checkpoint(path, project_id="demo")
            self.assertEqual(c.check_back.target_date.isoformat(), "2026-09-27")

    def test_append_entry_validates_before_writing(self):
        with tempfile.TemporaryDirectory() as d:
            ckpt = Path(d) / ".dashboard" / "checkpoint.json"
            bad = {"schema_version": 1, "project_id": "demo", "summary": "x"}  # missing fields
            with self.assertRaises(CheckpointError):
                append_entry(bad, "demo", ckpt)
            self.assertFalse(ckpt.exists())
            self.assertFalse((Path(d) / ".dashboard" / "journal.jsonl").exists())

    def test_append_entry_writes_journal_and_mirror(self):
        with tempfile.TemporaryDirectory() as d:
            ckpt = Path(d) / ".dashboard" / "checkpoint.json"
            entry = {
                "schema_version": 1,
                "project_id": "demo",
                "updated_at": "2026-09-26T12:00:00-04:00",
                "status": "observing",
                "summary": "second entry",
                "check_back": {"kind": "calendar", "target": 3, "label": "check later"},
            }
            journal = append_entry(entry, "demo", ckpt)
            self.assertTrue(journal.exists())
            current, entries, _ = load_current(ckpt, project_id="demo")
            self.assertEqual(current.summary, "second entry")
            self.assertEqual(len(entries), 1)
            # mirror exists and matches
            mirrored = json.loads(ckpt.read_text())
            self.assertEqual(mirrored["summary"], "second entry")

    def test_substantive_paths(self):
        self.assertTrue(is_substantive_path("src/model.py"))
        self.assertTrue(is_substantive_path("run_board.py"))
        self.assertFalse(is_substantive_path("docs/PLAN.md"))
        self.assertFalse(is_substantive_path(".dashboard/checkpoint.json"))
        self.assertFalse(is_substantive_path("README.md"))
        self.assertFalse(is_substantive_path("notes.txt"))

    def test_check_staleness_ignores_docs_only_commits(self):
        import os
        import subprocess
        from datetime import datetime, timezone

        from dashboard.checkpoint import CheckBack, check_staleness

        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q"], cwd=d, check=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
            os.makedirs(os.path.join(d, "src"))
            os.makedirs(os.path.join(d, "docs"))
            with open(os.path.join(d, "src", "model.py"), "w") as f:
                f.write("x=1\n")
            subprocess.run(["git", "add", "-A"], cwd=d, check=True)
            subprocess.run(["git", "commit", "-qm", "first"], cwd=d, check=True)
            with open(os.path.join(d, "docs", "PLAN.md"), "w") as f:
                f.write("plan\n")
            subprocess.run(["git", "add", "-A"], cwd=d, check=True)
            subprocess.run(["git", "commit", "-qm", "docs update"], cwd=d, check=True)
            with open(os.path.join(d, "src", "model.py"), "w") as f:
                f.write("x=2\n")
            subprocess.run(["git", "add", "-A"], cwd=d, check=True)
            subprocess.run(["git", "commit", "-qm", "real change"], cwd=d, check=True)

            c = Checkpoint(
                schema_version=1,
                project_id="x",
                updated_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                status="observing",
                summary="s",
                experiment=None,
                watching_for=None,
                next_action=None,
                deployed=True,
                git_commit=None,
                check_back=CheckBack(
                    kind="calendar",
                    label="l",
                    target=1,
                    baseline=datetime(2020, 1, 1, tzinfo=timezone.utc),
                    events=(),
                ),
                docs=(),
                source_path="",
            )
            report = check_staleness(Path(d), c)
            self.assertTrue(report.needs_attention)
            subjects = [s for _, s in report.commits]
            self.assertTrue(any("real change" in s for s in subjects))
            self.assertTrue(all("docs update" not in s for s in subjects))


class TestCountdown(unittest.TestCase):
    def _cb(self, kind, target=None, label="x", baseline="2026-09-20T12:00:00+00:00"):
        from dashboard.checkpoint import CheckBack

        return CheckBack(
            kind=kind,
            label=label,
            target=target,
            baseline=datetime.fromisoformat(baseline),
            events=(),
        )

    def test_calendar_pending_and_due(self):
        now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
        cb = self._cb("calendar", 7)
        info = evaluate(cb, tz_name=TZ, now=now)
        self.assertEqual(info.state, PENDING)
        self.assertEqual(info.remaining, 3)

        now = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
        info = evaluate(cb, tz_name=TZ, now=now)
        self.assertEqual(info.state, DUE)

    def test_game_days_counts_distinct_slate_dates(self):
        baseline = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)  # Sep 20 noon ET
        now = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)
        slate = {date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)}
        elapsed = count_game_days(slate, baseline, TZ, now)
        # Sep 20 is the baseline day and is not counted; 21,22,23 count.
        self.assertEqual(elapsed, 3)

    def test_game_days_unknown_when_source_missing(self):
        cb = self._cb("game_days", 7)
        info = evaluate(cb, tz_name=TZ, now=datetime(2026, 9, 25, tzinfo=timezone.utc), slate_available=False)
        self.assertEqual(info.state, UNKNOWN)
        self.assertIsNone(info.elapsed)

    def test_grade_event(self):
        cb = self._cb("grade_event", label="after Sunday grade")
        pending = evaluate(cb, tz_name=TZ, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
        self.assertEqual(pending.state, PENDING)
        due = evaluate(
            cb,
            tz_name=TZ,
            now=datetime(2026, 9, 25, tzinfo=timezone.utc),
            grade_satisfied=True,
            grade_detail="grade artifact seen",
        )
        self.assertEqual(due.state, DUE)


class TestFiles(unittest.TestCase):
    def test_normalize_dates_mixed(self):
        values = ["2026-09-20", "2026-09-20T23:30:00Z", "2026-09-21T03:00:00.000Z", "garbage"]
        dates = normalize_dates(values, TZ)
        # 03:00Z on Sep 21 is Sep 20 23:00 ET
        self.assertIn(date(2026, 9, 20), dates)
        self.assertNotIn(date(2026, 9, 21), dates)

    def test_run_jobs_local_dates(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.json").write_text(json.dumps({"board_date": "2026-09-20"}))
            (root / "b.json").write_text(json.dumps({"board_date": "2026-09-21"}))
            results = run_jobs_local(
                root,
                [{"kind": "dates", "glob": "*.json", "date_field": "board_date"}],
            )
            self.assertTrue(results[0].ok)
            self.assertEqual(results[0].raw["values"], ["2026-09-20", "2026-09-21"])

    def test_run_jobs_local_latest_date(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = root / "learning_review_2026-09-20.md"
            f.write_text("grade")
            past = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()
            results = run_jobs_local(
                root,
                [{"kind": "latest_date", "glob": "learning_review_*.md", "baseline_epoch": past}],
            )
            self.assertTrue(results[0].raw["satisfied"])
            self.assertEqual(results[0].raw["date"], "2026-09-20")

    def test_run_jobs_local_latest_date_target(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "learning_review_2026-09-26.md").write_text("x")
            # target in the future -> not satisfied despite mtime > baseline
            results = run_jobs_local(
                root,
                [{"kind": "latest_date", "glob": "learning_review_*.md",
                  "baseline_epoch": 0.0, "target_date": "2026-09-27"}],
            )
            self.assertFalse(results[0].raw["satisfied"])
            # target on the artifact date -> satisfied
            results = run_jobs_local(
                root,
                [{"kind": "latest_date", "glob": "learning_review_*.md",
                  "baseline_epoch": 0.0, "target_date": "2026-09-26"}],
            )
            self.assertTrue(results[0].raw["satisfied"])

    def test_run_jobs_local_dates_games_filter(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.json").write_text(json.dumps({"screen_date": "2026-09-20", "totals": {"w": 5, "l": 4}}))
            (root / "b.json").write_text(json.dumps({"screen_date": "2026-09-21", "totals": {"w": 0, "l": 0, "p": 0}}))
            results = run_jobs_local(
                root,
                [{"kind": "dates", "glob": "*.json", "date_field": "screen_date", "games_path": "totals"}],
            )
            self.assertEqual(results[0].raw["values"], ["2026-09-20"])
            self.assertEqual(results[0].raw["files_with_games"], 1)

    def test_run_jobs_local_horses_summary(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dp = root / "data" / "processed"
            dp.mkdir(parents=True)
            for day in ["2026-10-14", "2026-10-15", "2026-10-16"]:
                day_dir = dp / ("bbc_" + day)
                snap = day_dir / "snapshots" / (day + "T13-00-00Z")
                snap.mkdir(parents=True)
                (snap / "summary.txt").write_text(
                    "BBC Racecards Summary\nRaces: 40\nRunners: 417\n"
                )
            results = run_jobs_local(
                root,
                [{"kind": "horses_summary", "data_dir": "data/processed", "base": str(root)}],
            )
            self.assertTrue(results[0].ok)
            raw = results[0].raw
            self.assertEqual(raw["race_days"], 3)
            self.assertEqual(raw["first_day"], "2026-10-14")
            self.assertEqual(raw["last_day"], "2026-10-16")
            self.assertEqual(raw["gaps"], [])
            self.assertIn("40", raw["latest"]["summary"])

    def test_run_jobs_local_horses_summary_gaps(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dp = root / "data" / "processed"
            dp.mkdir(parents=True)
            # skip 2026-10-15 -> gap
            for day in ["2026-10-14", "2026-10-16"]:
                (dp / ("bbc_" + day)).mkdir(parents=True)
            results = run_jobs_local(
                root,
                [{"kind": "horses_summary", "data_dir": "data/processed", "base": str(root)}],
            )
            self.assertTrue(results[0].ok)
            self.assertEqual(results[0].raw["gaps"], ["2026-10-15"])

    def test_horses_html_renders(self):
        from dashboard.render import _horses_html
        hs = {
            "available": True,
            "race_days": 40,
            "first_day": "2026-09-07",
            "last_day": "2026-10-16",
            "gaps": [],
            "sl_result_days": 4,
            "latest": {"summary": "Races: 40\nRunners: 417\n"},
        }
        html = _horses_html(hs)
        self.assertIn("40", html)
        self.assertIn("40/75", html)
        self.assertIn("no date gaps", html)
        # unavailable -> empty
        self.assertEqual(_horses_html(None), "")
        self.assertEqual(_horses_html({"available": False}), "")

    def test_summaries_parse(self):
        from dashboard.config import Report
        from dashboard.summaries import summarize

        cfb = Report(key="forecast_grade", label="x", glob="*", kind="cfb_grade")
        text = (
            "CFB forecast grade — forecast-grading-v1 (cfb-forecast-v2)\n"
            "generated 2026-09-20T13:17:34Z | snapshots 8 | forecasts 150 | pending 46\n"
            "Cumulative — forecast quality:\n  winner n=104 accuracy=0.75 brier=0.1626\n"
            "Latest slate: 43-13 (76.8%)\n"
            "Cumulative: 104 graded forecasts | 75.0% accuracy\n"
            "Priced ROI: +1.28u across 152 refs (+0.8%)\n"
        )
        lines = summarize(cfb, {"text": text})
        self.assertTrue(any("model: cfb-forecast-v2" in l for l in lines))
        self.assertTrue(any("cumulative accuracy 75.0%" in l for l in lines))
        self.assertTrue(any("priced ROI +1.28u" in l for l in lines))

        mlb = Report(key="grade_board", label="x", glob="*", kind="mlb_grade")
        lines = summarize(
            mlb,
            {"fields": {
                "screen_date": "2026-09-24",
                "latest_summary.totals": {"w": 10, "l": 14, "p": 0, "hit_rate": 0.4167},
                "canonical.summary.totals": {"w": 12, "l": 14, "p": 0, "hit_rate": 0.4615},
                "canonical.roi": {"plays": 23, "units": -5.809, "roi": -0.2526},
            }},
        )
        self.assertTrue(any("screen 2026-09-24" in l for l in lines))
        self.assertTrue(any("board 10-14-0" in l for l in lines))
        self.assertTrue(any("date record 12-14-0" in l for l in lines))
        self.assertTrue(any("date roi: 23 priced" in l for l in lines))
        for line in lines:
            self.assertNotIn("latest_roi", line)


if __name__ == "__main__":
    unittest.main()
