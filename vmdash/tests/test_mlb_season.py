"""MLB season figure resolves from the canonical field, not `latest_roi`.

Staged with vmdash/ (Cole's rule: dashboard changes are staged in the mlb_props
repo and relayed; ~/dashboard is never edited directly).

`latest_roi` is the newest *revision* of a single screen date; summed over the
glob it answers "what did the last run of each day say" (383-359-4, -59.549u on
724 priced rows) and is ~293 priced rows short of the record. The season figure
must be the sum of each date's `canonical` block, where `price is not None` is
the stake predicate (mlb_props commit 0c62483).

Run from a dashboard checkout:

    python -m pytest tests/test_mlb_season.py -q
"""

import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from dashboard import collect, files, performance, summaries
from dashboard.config import Project, Report

CONFIG = Path(__file__).resolve().parents[1] / "config" / "projects.json"


def _artifact(directory, screen_date, plays, units, record=(10, 8, 0), rows=True,
              latest_roi=(2, 99.0)):
    """Write one grade artifact with divergent latest_roi and canonical blocks."""
    totals = {"w": record[0], "l": record[1], "p": record[2], "hit_rate": 0.5}
    payload = {
        "screen_date": screen_date,
        "latest_summary": {"totals": dict(totals)},
        "latest_roi": ({"plays": latest_roi[0], "units": latest_roi[1],
                        "roi": latest_roi[1] / latest_roi[0]} if latest_roi else None),
        "canonical": {
            "roi": {"plays": plays, "units": units,
                    "roi": (units / plays if plays else None)},
            "summary": {"totals": dict(totals)},
            "rows": [{"proposition_id": "p1"}] if rows else [],
        },
    }
    path = Path(directory) / ("forecast_board_%s.json" % screen_date)
    path.write_text(json.dumps(payload))
    return str(path)


def test_season_sums_canonical_not_latest_roi(tmp_path):
    paths = [
        _artifact(tmp_path, "2026-09-10", 10, -3.0),
        _artifact(tmp_path, "2026-09-11", 12, -4.5),
        _artifact(tmp_path, "2026-09-12", 8, 1.5),
    ]
    result = performance.parse_mlb_season(paths)
    season = result["season"]
    assert result["ok"] and result["exists"]
    assert season["source"] == "canonical"
    # canonical: 30 priced / -6.0u.  latest_roi glob would say 6 priced / +297u.
    assert season["plays"] == 30
    assert season["units"] == pytest.approx(-6.0)
    assert season["roi"] == pytest.approx(-0.2)
    assert season["record"] == {"w": 30, "l": 24, "p": 0}
    assert season["dates"] == 3 and season["screen_dates"] == 3
    assert season["first_date"] == "2026-09-10"
    assert season["last_date"] == "2026-09-12"


def test_dates_without_canonical_rows_are_flagged_not_summed(tmp_path):
    paths = [
        _artifact(tmp_path, "2026-09-10", 0, 0.0, rows=False, latest_roi=(5, -9.0)),
        _artifact(tmp_path, "2026-09-11", 4, 2.0),
    ]
    season = performance.parse_mlb_season(paths)["season"]
    assert season["dates"] == 1 and season["screen_dates"] == 2
    assert season["missing_canonical_dates"] == ["2026-09-10"]
    assert season["plays"] == 4
    assert season["units"] == pytest.approx(2.0)


def test_newest_artifact_per_screen_date_wins(tmp_path):
    path = _artifact(tmp_path, "2026-09-11", 4, -1.0)
    _artifact(tmp_path, "2026-09-11", 5, -2.0)  # same date, later run wins
    season = performance.parse_mlb_season([path])["season"]
    assert season["dates"] == 1
    assert season["plays"] == 5
    assert season["units"] == pytest.approx(-2.0)


def test_empty_glob_reports_missing_not_zero():
    assert performance.parse_mlb_season([]) == {"ok": True, "exists": False}


def test_remote_script_dispatches_mlb_season(tmp_path):
    """The staged REMOTE_SCRIPT itself resolves the season over the glob."""
    grades = tmp_path / "outputs" / "grades"
    grades.mkdir(parents=True)
    _artifact(grades, "2026-09-10", 10, -3.0)
    _artifact(grades, "2026-09-11", 12, -4.5)
    job = {"kind": "mlb_season", "base": str(tmp_path),
           "glob": "outputs/grades/forecast_board_*.json"}
    payload = base64.b64encode(json.dumps([job]).encode()).decode()
    script = tmp_path / "remote_script.py"
    script.write_text(files.REMOTE_SCRIPT)
    proc = subprocess.run([sys.executable, str(script), payload],
                          capture_output=True, text=True, check=True)
    out = json.loads(proc.stdout)
    season = out[0]["season"]
    assert season["source"] == "canonical"
    assert season["plays"] == 22
    assert season["units"] == pytest.approx(-7.5)


def test_config_uses_season_report_and_drops_latest_roi():
    data = json.loads(CONFIG.read_text())
    projects = data["projects"] if isinstance(data, dict) else data
    mlb = next(p for p in projects if p["id"] == "mlb_props")
    by_kind = {r["kind"]: r for r in mlb["reports"]}
    assert "mlb_season" in by_kind, "season_record report missing from config"
    season = by_kind["mlb_season"]
    assert season["glob"] == "outputs/grades/forecast_board_*.json"
    assert season["active"] is True
    for report in mlb["reports"]:
        assert "latest_roi" not in report.get("fields", []), report["key"]
        assert "latest_roi" not in str(report.get("glob", "")), report["key"]


def _mlb_project():
    """Registry entry shape for mlb_props (full config load needs every project)."""
    data = json.loads(CONFIG.read_text())
    projects = data["projects"] if isinstance(data, dict) else data
    entry = next(p for p in projects if p["id"] == "mlb_props")
    reports = tuple(Report(key=r["key"], label=r["label"], glob=r["glob"],
                           kind=r["kind"], active=r.get("active", True),
                           fields=tuple(r.get("fields", ())))
                    for r in entry["reports"])
    return Project(
        id=entry["id"], name=entry["name"], sport=entry["sport"],
        mac_path=Path(entry["mac_path"]), vm_path=entry["vm_path"],
        timezone=entry["timezone"], docs=(), timers=(), services=(),
        run_status=entry.get("run_status"), reports=reports,
        grade_event=None, slate=None)


def test_collect_builds_a_season_job_for_mlb():
    mlb = _mlb_project()
    jobs, index = collect._build_jobs(mlb, baseline_epoch=None, local=False)
    keys = {(category, key) for category, key, _ in index}
    assert ("report", "season_record") in keys
    season_jobs = [j for j in jobs if j.get("kind") == "mlb_season"]
    assert len(season_jobs) == 1
    assert season_jobs[0]["glob"] == "outputs/grades/forecast_board_*.json"


def test_summary_renders_canonical_season_line():
    report = Report(key="season_record", label="Season record", glob="g",
                    kind="mlb_season")
    info = {"fields": {"season": {
        "source": "canonical", "dates": 32, "screen_dates": 36,
        "missing_canonical_dates": ["2026-09-10", "2026-09-11", "2026-09-12",
                                    "2026-09-13"],
        "plays": 822, "units": -79.545, "roi": -0.0968,
        "record": {"w": 429, "l": 412, "p": 3}}}}
    lines = summaries.summarize(report, info)
    assert any("season (canonical) 429-412-3" in line for line in lines)
    assert any("822 priced" in line and "-79.545u" in line for line in lines)
    assert any("dates 32/36 canonical" in line for line in lines)


def test_mlb_grade_summary_reads_canonical_fields():
    report = Report(key="grade_board", label="Graded board", glob="g",
                    kind="mlb_grade")
    info = {"fields": {
        "screen_date": "2026-10-15",
        "latest_summary.totals": {"w": 0, "l": 1, "p": 0, "hit_rate": 0.0},
        "canonical.summary.totals": {"w": 7, "l": 1, "p": 0, "hit_rate": 0.875},
        "canonical.roi": {"plays": 7, "units": 1.737, "roi": 0.2481}}}
    lines = summaries.summarize(report, info)
    assert any("date record 7-1-0" in line for line in lines)
    assert any("7 priced" in line and "1.737u" in line for line in lines)
