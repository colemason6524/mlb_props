"""Read-only file inspection jobs, executed locally or on the VM.

The same self-contained script runs in both places so semantics cannot drift.
It only ever reads files matched by configured globs; it never writes.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# performance.py source, embedded into REMOTE_SCRIPT so the remote job can
# compute ROI windows without extra file transfers. The __future__ import is
# stripped: it must be first in a file, but here the source is embedded
# mid-script (duplicate stdlib imports are harmless).
_PERF_SRC = Path(__file__).with_name("performance.py").read_text().replace(
    "from __future__ import annotations\n", "", 1)

# Self-contained executor. argv[1] = base64(JSON job list). Each job may carry a
# "base" directory; otherwise the process cwd is used. Output is a JSON list of
# results, one per job, in order.
_REMOTE_HEAD = r'''
import sys, json, glob, os, base64, datetime, re

def dig(obj, path):
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur

COUNT_KEYS = {"w", "l", "p", "void", "pending", "error", "win", "lose", "push", "n"}

def file_date(path):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", path)
    if m:
        return m.group(0)
    m = re.search(r"(\d{4})(\d{2})(\d{2})", path)
    if m:
        return "%s-%s-%s" % m.groups()
    return None

def games_count(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        total = 0
        for k, v in value.items():
            if k in COUNT_KEYS and isinstance(v, (int, float)):
                total += int(v)
        return total
    return None

'''
_REMOTE_JOBS = r'''
jobs = json.loads(base64.b64decode(sys.argv[1]).decode("utf-8"))
out = []
for j in jobs:
    try:
        base = j.get("base") or "."
        kind = j["kind"]
        if "glob" in j:
            pattern = j["glob"] if os.path.isabs(j["glob"]) else os.path.join(base, j["glob"])
            files = sorted(glob.glob(pattern))
        else:
            files = []
        if kind == "latest":
            if not files:
                out.append({"ok": True, "exists": False})
                continue
            p = files[-1]
            st = os.stat(p)
            res = {"ok": True, "exists": True, "path": p, "mtime": st.st_mtime, "size": st.st_size}
            if j.get("read"):
                with open(p, "r", errors="replace") as f:
                    res["text"] = f.read(int(j.get("max_bytes", 200000)))
            out.append(res)
        elif kind == "latest_date":
            if not files:
                out.append({"ok": True, "exists": False})
                continue
            p = files[-1]
            st = os.stat(p)
            date_str = file_date(p)
            baseline = float(j.get("baseline_epoch", 0))
            target = j.get("target_date")
            if target:
                satisfied = bool(date_str) and date_str >= target
            else:
                satisfied = st.st_mtime > baseline
            out.append({
                "ok": True, "exists": True, "path": p, "mtime": st.st_mtime,
                "date": date_str, "satisfied": bool(satisfied),
            })
        elif kind == "dates":
            vals = set()
            n = 0
            files_with_games = 0
            errs = []
            for p in files:
                n += 1
                try:
                    with open(p, "r", errors="replace") as f:
                        d = json.load(f)
                    count = None
                    if j.get("games_path"):
                        count = games_count(dig(d, j["games_path"]))
                    elif j.get("list_field"):
                        items = dig(d, j["list_field"])
                        count = len(items) if isinstance(items, list) else 0
                    has_games = count is None or count > 0
                    if has_games:
                        files_with_games += 1
                        if j.get("list_field"):
                            items = dig(d, j["list_field"]) or []
                            if isinstance(items, list):
                                for it in items:
                                    v = dig(it, j["date_field"])
                                    if v:
                                        vals.add(str(v))
                        else:
                            v = dig(d, j["date_field"])
                            if v:
                                vals.add(str(v))
                except Exception as e:
                    errs.append(str(e))
            out.append({
                "ok": True, "files": n, "files_with_games": files_with_games,
                "values": sorted(vals), "errors": errs[:10],
            })
        elif kind == "json_summary":
            if not files:
                out.append({"ok": True, "exists": False})
                continue
            p = files[-1]
            with open(p, "r", errors="replace") as f:
                d = json.load(f)
            res = {"ok": True, "exists": True, "path": p, "mtime": os.stat(p).st_mtime}
            for fld in j.get("fields", []):
                res[fld] = dig(d, fld)
            out.append(res)
        elif kind == "performance":
            out.append(perf_job(j))
        elif kind == "mlb_season":
            out.append(mlb_season_job(j))
        elif kind == "horses_summary":
            data_dir = os.path.join(base, j.get("data_dir", "data/processed"))
            day_dirs = sorted(glob.glob(os.path.join(data_dir, "bbc_*")))
            days = []
            for d in day_dirs:
                m = re.search(r"bbc_(\d{4}-\d{2}-\d{2})$", os.path.basename(d))
                if m:
                    days.append(m.group(1))
            gaps = []
            if len(days) >= 2:
                import datetime as _dt
                seen = set(days)
                cur = _dt.date.fromisoformat(days[0])
                end = _dt.date.fromisoformat(days[-1])
                while cur <= end:
                    iso = cur.isoformat()
                    if iso not in seen:
                        gaps.append(iso)
                    cur += _dt.timedelta(days=1)
            latest = {}
            if day_dirs:
                snaps = sorted(glob.glob(os.path.join(day_dirs[-1], "snapshots", "*")))
                if snaps:
                    sp = os.path.join(snaps[-1], "summary.txt")
                    if os.path.exists(sp):
                        with open(sp, "r", errors="replace") as f:
                            latest["summary"] = f.read(2000)
                        latest["snapshot"] = os.path.basename(snaps[-1])
                        latest["mtime"] = os.stat(sp).st_mtime
            sl_dirs = sorted(glob.glob(os.path.join(data_dir, "sportinglife_results_*")))
            out.append({
                "ok": True,
                "race_days": len(days),
                "first_day": days[0] if days else None,
                "last_day": days[-1] if days else None,
                "gaps": gaps,
                "latest": latest,
                "sl_result_days": len(sl_dirs),
                "sl_last": os.path.basename(sl_dirs[-1]) if sl_dirs else None,
            })
        else:
            out.append({"ok": False, "error": "unknown job kind %r" % kind})
    except Exception as e:
        out.append({"ok": False, "error": str(e)})
print(json.dumps(out))
'''

REMOTE_SCRIPT = _REMOTE_HEAD + _PERF_SRC + _REMOTE_JOBS


@dataclass(frozen=True)
class JobResult:
    ok: bool
    raw: dict

    @property
    def error(self) -> str | None:
        return self.raw.get("error") if not self.ok else None


def _job_payload(jobs: list[dict]) -> str:
    return base64.b64encode(json.dumps(jobs).encode("utf-8")).decode("ascii")


def run_jobs_local(base_dir: Path, jobs: list[dict]) -> list[JobResult]:
    proc = subprocess.run(
        [sys.executable, "-", _job_payload(jobs)],
        input=REMOTE_SCRIPT,
        capture_output=True,
        text=True,
        cwd=str(base_dir),
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"local job runner failed: {proc.stderr.strip()[:400]}")
    return [JobResult(True, item) for item in json.loads(proc.stdout)]


def normalize_dates(values: list[str], tz_name: str) -> set[date]:
    """Convert raw date/timestamp strings to distinct local dates.

    Accepts plain YYYY-MM-DD dates and ISO-8601 timestamps. Unparseable values
    are ignored rather than guessed at.
    """
    tz = ZoneInfo(tz_name)
    dates: set[date] = set()
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            try:
                dates.add(date.fromisoformat(text))
            except ValueError:
                continue
            continue
        iso = text[:-1] + "+00:00" if text.endswith("Z") else text
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dates.add(dt.astimezone(tz).date())
    return dates
