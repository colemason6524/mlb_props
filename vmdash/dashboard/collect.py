"""Collect a cached snapshot of VM-owned facts for every project.

This module builds the read-only job set from configuration, runs it in one
bounded SSH call, and stores a per-project payload. It never triggers jobs.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from . import vm
from .checkpoint import fetch_remote
from .config import Config, Project
from .files import normalize_dates
from .store import Store
from .performance import PERFORMANCE_PROJECTS


def _mtime_iso(epoch: float | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat()


def _build_jobs(
    project: Project,
    baseline_epoch: float | None,
    target_date: str | None = None,
    *,
    local: bool = False,
) -> tuple[list[dict], list[tuple[str, str, int]]]:
    """Return (jobs, index) where index maps (category, key) to job position."""
    jobs: list[dict] = []
    index: list[tuple[str, str, int]] = []

    def add(category: str, key: str, job: dict) -> None:
        index.append((category, key, len(jobs)))
        jobs.append(job)

    # Remote jobs reference the repo by name relative to the remote home dir;
    # local jobs reference the absolute checkout path on this machine.
    base = {"base": str(project.mac_path) if local else project.vm_path}

    if project.slate:
        job = {"kind": "dates", "glob": project.slate.glob, "date_field": project.slate.date_field, **base}
        if project.slate.list_field:
            job["list_field"] = project.slate.list_field
        if project.slate.games_path:
            job["games_path"] = project.slate.games_path
        add("slate", "slate", job)

    if project.horses_summary:
        add(
            "horses_summary",
            "horses_summary",
            {
                "kind": "horses_summary",
                "data_dir": project.horses_summary.data_dir,
                **base,
            },
        )

    if project.grade_event and baseline_epoch is not None:
        add(
            "grade_event",
            "grade_event",
            {
                "kind": "latest_date",
                "glob": project.grade_event.glob,
                "baseline_epoch": baseline_epoch,
                "target_date": target_date,
                **base,
            },
        )

    if project.id in PERFORMANCE_PROJECTS:
        add(
            "performance",
            "performance",
            {
                "kind": "performance",
                "project_id": project.id,
                "today": date.today().isoformat(),
                **base,
            },
        )

    if project.run_status:
        add("run_status", "run_status", {"kind": "latest", "glob": project.run_status, "read": True, "max_bytes": 20000, **base})

    for report in project.reports:
        if report.kind == "mlb_season":
            # A season figure needs every grade artifact summed, which a
            # per-file "latest" job cannot express.
            add("report", report.key, {"kind": "mlb_season", "glob": report.glob, **base})
        elif report.fields:
            add(
                "report",
                report.key,
                {"kind": "json_summary", "glob": report.glob, "fields": list(report.fields), **base},
            )
        else:
            add(
                "report",
                report.key,
                {"kind": "latest", "glob": report.glob, "read": True, "max_bytes": report.max_bytes, **base},
            )

    return jobs, index


def collect_project(project: Project, results: list, index: list[tuple[str, str, int]], fetched_at: str) -> dict:
    payload: dict = {
        "vm_path": project.vm_path,
        "fetched_at": fetched_at,
        "slate": None,
        "grade_event": None,
        "run_status": None,
        "reports": {},
        "performance": None,
        "horses_summary": None,
    }
    for category, key, position in index:
        result = results[position]
        raw = result.raw if result.ok else {"ok": False, "error": result.error}
        if category == "slate":
            if raw.get("ok"):
                dates = sorted(normalize_dates(raw.get("values", []), project.timezone))
                payload["slate"] = {
                    "available": True,
                    "files": raw.get("files", 0),
                    "files_with_games": raw.get("files_with_games", 0),
                    "dates": [d.isoformat() for d in dates],
                    "errors": raw.get("errors", []),
                }
            else:
                payload["slate"] = {"available": False, "error": raw.get("error")}
        elif category == "grade_event":
            if raw.get("ok"):
                payload["grade_event"] = {
                    "applicable": True,
                    "satisfied": bool(raw.get("satisfied")),
                    "path": raw.get("path"),
                    "mtime": _mtime_iso(raw.get("mtime")),
                    "date": raw.get("date"),
                }
            else:
                payload["grade_event"] = {"applicable": True, "satisfied": False, "error": raw.get("error")}
        elif category == "performance":
            if raw.get("ok"):
                payload["performance"] = raw.get("performance")
            else:
                payload["performance"] = {"ok": False, "error": raw.get("error")}
        elif category == "horses_summary":
            if raw.get("ok"):
                entry = {
                    "available": True,
                    "race_days": raw.get("race_days", 0),
                    "first_day": raw.get("first_day"),
                    "last_day": raw.get("last_day"),
                    "gaps": raw.get("gaps", []),
                    "sl_result_days": raw.get("sl_result_days", 0),
                    "sl_last": raw.get("sl_last"),
                }
                latest = raw.get("latest") or {}
                if latest:
                    entry["latest"] = {
                        "snapshot": latest.get("snapshot"),
                        "mtime": _mtime_iso(latest.get("mtime")),
                        "summary": latest.get("summary", ""),
                    }
                payload["horses_summary"] = entry
            else:
                payload["horses_summary"] = {"available": False, "error": raw.get("error")}
        elif category == "run_status":
            if raw.get("ok") and raw.get("exists"):
                payload["run_status"] = {
                    "exists": True,
                    "path": raw.get("path"),
                    "mtime": _mtime_iso(raw.get("mtime")),
                    "text": raw.get("text", ""),
                }
            else:
                payload["run_status"] = {"exists": False}
        elif category == "report":
            report = next((r for r in project.reports if r.key == key), None)
            if raw.get("ok") and raw.get("exists"):
                entry = {
                    "exists": True,
                    "path": raw.get("path"),
                    "mtime": _mtime_iso(raw.get("mtime")),
                    "size": raw.get("size"),
                }
                if "text" in raw:
                    entry["text"] = raw.get("text", "")
                if report is not None and report.kind == "mlb_season":
                    entry["fields"] = {"season": raw.get("season")}
                elif report is not None and report.fields:
                    skip = {"ok", "exists", "path", "mtime", "size"}
                    entry["fields"] = {k: raw.get(k) for k in raw if k not in skip}
                payload["reports"][key] = entry
            else:
                payload["reports"][key] = {"exists": False}
    return payload


def rules_from_checkpoints(cfg: Config, store: Store | None = None) -> dict[str, dict]:
    """Per-project grade-event rules derived from the current checkpoint.

    Returns {project_id: {"baseline_epoch": float|None, "target_date": str|None}}.
    """
    from .checkpoint import load_current_project

    rules: dict[str, dict] = {}
    for project in cfg.projects:
        current, _entries, _err = load_current_project(
            project.mac_path,
            project.checkpoint_path,
            project_id=project.id,
            source=project.diary_source,
            remote=project.diary_remote,
        )
        if current is None:
            rules[project.id] = {"baseline_epoch": None, "target_date": None}
            continue
        rules[project.id] = {
            "baseline_epoch": current.check_back.baseline.timestamp(),
            "target_date": current.check_back.target_date.isoformat()
            if current.check_back.target_date
            else None,
        }
    return rules


def refresh(cfg: Config, store: Store, rules: dict[str, dict] | None = None) -> dict:
    """Run a bounded, read-only refresh and cache the results.

    Returns a small status dict. Never raises; failures are recorded so the UI
    can show an unreachable/stale state instead of failing the page.
    """
    from .store import utcnow

    rules = rules or {}
    started = utcnow().isoformat()
    store.set_meta("refresh_started", started)

    # Diaries live in the project repos on GitHub. Refresh the remote-tracking
    # refs so both the Mac and the VM see the other's pushed updates without
    # pulling project code into the working tree. Best-effort, never fatal.
    if cfg.diary_source == "git":
        for project in cfg.projects:
            fetch_remote(project.mac_path, project.diary_remote)

    try:
        reachable, reason = vm.check_reachable(cfg)
        if not reachable:
            store.set_meta("vm_reachable", False)
            store.set_meta("refresh_ok", False)
            store.set_meta("refresh_error", f"VM unreachable: {reason}")
            store.set_meta("refresh_finished", utcnow().isoformat())
            return {"ok": False, "error": reason, "reachable": False}

        store.set_meta("vm_reachable", True)
        for unit in (u for p in cfg.projects for u in p.all_units):
            vm.sanitize_unit_name(unit)

        all_units = [u for p in cfg.projects for u in p.all_units]
        units = vm.fetch_units(cfg, all_units)
        store.set_meta(
            "vm_units",
            {
                "units": units.units,
                "fetched_at": units.fetched_at.isoformat(),
            },
        )

        all_jobs: list[dict] = []
        per_project_index: dict[str, list[tuple[str, str, int]]] = {}
        for project in cfg.projects:
            rule = rules.get(project.id) or {}
            jobs, index = _build_jobs(
                project, rule.get("baseline_epoch"), rule.get("target_date"), local=cfg.local
            )
            offset = len(all_jobs)
            per_project_index[project.id] = [(c, k, i + offset) for (c, k, i) in index]
            all_jobs.extend(jobs)

        results = vm.remote_jobs(cfg, all_jobs)
        if len(results) != len(all_jobs):
            raise vm.VMError(
                f"expected {len(all_jobs)} file results, received {len(results)}"
            )

        fetched_at = utcnow().isoformat()
        for project in cfg.projects:
            payload = collect_project(project, results, per_project_index[project.id], fetched_at)
            store.set_vm_cache(project.id, payload, fetched_at)

        store.set_meta("refresh_ok", True)
        store.set_meta("refresh_error", None)
        store.set_meta("refresh_finished", fetched_at)
        return {"ok": True, "error": None, "reachable": True}
    except Exception as exc:  # noqa: BLE001 - status must never crash the UI
        store.set_meta("refresh_ok", False)
        store.set_meta("refresh_error", str(exc)[:500])
        store.set_meta("refresh_finished", utcnow().isoformat())
        return {"ok": False, "error": str(exc), "reachable": None}


def slate_dates(payload: dict | None) -> tuple[set[date], bool]:
    """Distinct game dates from a slate source, and whether that source is usable.

    A source is usable only when at least one matching file actually contains
    games, so an empty/offseason slate never produces a fabricated count.
    """
    if not payload or not payload.get("slate") or not payload["slate"].get("available"):
        return set(), False
    if payload["slate"].get("files_with_games", 0) < 1:
        return set(), False
    dates: set[date] = set()
    for value in payload["slate"].get("dates", []):
        try:
            dates.add(date.fromisoformat(value))
        except ValueError:
            continue
    return dates, True
