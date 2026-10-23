"""Project-specific report summaries.

Reports are either plain text (parsed here) or JSON fields extracted on the VM
(rendered here). Summaries are short plain-text lines; the UI escapes them.
"""

from __future__ import annotations

import json
import re

from .config import Report


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)[:400]
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value[:8])
    return str(value)


def summarize(report: Report, info: dict) -> list[str]:
    """Return short human-readable lines for a report, or [] when unparsed."""
    handler = _HANDLERS.get(report.kind)
    if handler is None:
        return []
    try:
        return handler(report, info)
    except Exception:  # noqa: BLE001 - a bad summary must never break the page
        return []


def _cfb_grade(report: Report, info: dict) -> list[str]:
    text = info.get("text") or ""
    lines: list[str] = []
    m = re.search(r"forecast-grading-v\d+\s*\(([^)]+)\)", text)
    if m:
        lines.append(f"model: {m.group(1)}")
    m = re.search(r"snapshots (\d+) \| forecasts (\d+) \| pending (\d+)", text)
    if m:
        lines.append(f"snapshots {m.group(1)} · forecasts {m.group(2)} · pending {m.group(3)}")
    m = re.search(r"winner n=(\d+) accuracy=([\d.]+) brier=([\d.]+)", text)
    if m:
        lines.append(f"winner accuracy {m.group(2)} (n={m.group(1)})")
    m = re.search(r"Latest slate: ([\d-]+) \(([\d.]+)%\)", text)
    if m:
        lines.append(f"latest slate {m.group(1)} ({m.group(2)}%)")
    m = re.search(r"Cumulative: \d+ graded forecasts \| ([\d.]+)% accuracy", text)
    if m:
        lines.append(f"cumulative accuracy {m.group(1)}%")
    m = re.search(r"Priced ROI: ([+-][\d.]+u) across \d+ refs \(([+-][\d.]+%)\)", text)
    if m:
        lines.append(f"priced ROI {m.group(1)} ({m.group(2)})")
    return lines


def _mlb_learning_review(report: Report, info: dict) -> list[str]:
    text = info.get("text") or ""
    lines: list[str] = []
    m = re.search(r"Decided plays: (\d+)", text)
    if m:
        lines.append(f"decided plays {m.group(1)}")
    m = re.search(r"\|\s*all\s*\|\s*(\d+)\s*\|\s*(\d+)-(\d+)\s*\|\s*(\d+)%\s*\|\s*([+-]?[\d.]+)\s*\|", text)
    if m:
        lines.append(f"all: {m.group(2)}-{m.group(3)} ({m.group(4)}%) · {m.group(5)}u")
    return lines


def _mlb_grade(report: Report, info: dict) -> list[str]:
    fields = info.get("fields") or {}
    lines: list[str] = []
    if fields.get("screen_date"):
        lines.append(f"screen {fields['screen_date']}")
    totals = fields.get("latest_summary.totals")
    if isinstance(totals, dict):
        w, l = totals.get("w", 0), totals.get("l", 0)
        p = totals.get("p", 0)
        hit = totals.get("hit_rate")
        lines.append(f"board {w}-{l}-{p}" + (f" · hit {float(hit)*100:.1f}%" if hit is not None else ""))
    canonical_totals = fields.get("canonical.summary.totals")
    if isinstance(canonical_totals, dict):
        w, l = canonical_totals.get("w", 0), canonical_totals.get("l", 0)
        p = canonical_totals.get("p", 0)
        lines.append(f"date record {w}-{l}-{p}")
    roi = fields.get("canonical.roi")
    if isinstance(roi, dict):
        plays = roi.get("plays")
        units = roi.get("units")
        rate = roi.get("roi")
        lines.append(
            "date roi: "
            + (f"{plays} priced · " if plays is not None else "")
            + (f"{units}u" if units is not None else "")
            + (f" ({float(rate)*100:.1f}%)" if rate is not None else "")
        )
    return lines


def _mlb_season(report: Report, info: dict) -> list[str]:
    """Season record: canonical rows summed across every screen date."""
    fields = info.get("fields") or {}
    season = fields.get("season")
    if not isinstance(season, dict):
        return []
    record = season.get("record") or {}
    rec = "{w}-{l}-{p}".format(
        w=record.get("w", 0), l=record.get("l", 0), p=record.get("p", 0))
    plays = season.get("plays")
    units = season.get("units")
    rate = season.get("roi")
    line = f"season (canonical) {rec}"
    if plays is not None:
        line += f" · {plays} priced"
    if units is not None:
        line += f" · {float(units):+.3f}u"
    if rate is not None:
        line += f" ({float(rate)*100:.1f}%)"
    lines = [line]
    dates = season.get("dates")
    screen_dates = season.get("screen_dates")
    if dates is not None and screen_dates is not None:
        extra = ""
        missing = season.get("missing_canonical_dates") or []
        if missing:
            extra = f" · {len(missing)} date(s) rebuilt from the ledger, not included"
        lines.append(f"dates {dates}/{screen_dates} canonical{extra}")
    return lines


def _nhl_snapshot(report: Report, info: dict) -> list[str]:
    fields = info.get("fields") or {}
    lines: list[str] = []
    board = fields.get("board_date")
    run = fields.get("run_label")
    if board:
        lines.append(f"board {board}" + (f" ({run})" if run else ""))
    summary = fields.get("summary")
    if isinstance(summary, dict):
        lines.append(
            "games " + ", ".join(
                f"{k} {v}" for k, v in summary.items()
                if k in {"games", "available", "predictions", "priced", "favorable"}
            )
        )
    return lines


def _soccer_snapshot(report: Report, info: dict) -> list[str]:
    fields = info.get("fields") or {}
    lines: list[str] = []
    board = fields.get("board_date")
    run = fields.get("run_label")
    if board:
        lines.append(f"board {board}" + (f" ({run})" if run else ""))
    summary = fields.get("summary")
    if isinstance(summary, dict):
        lines.append(
            ", ".join(
                f"{k} {v}" for k, v in summary.items()
                if k in {"fixtures", "available", "priced", "stale_bovada"}
            )
        )
    return lines


def _cfb_snapshot(report: Report, info: dict) -> list[str]:
    fields = info.get("fields") or {}
    lines: list[str] = []
    if fields.get("model_version"):
        lines.append(f"model: {fields['model_version']}")
    summary = fields.get("summary")
    if isinstance(summary, dict):
        lines.append(
            ", ".join(
                f"{k} {v}" for k, v in summary.items()
                if k in {"games", "available", "priced", "unpriced", "horizon_days"}
            )
        )
    return lines


def _tennis_shadow(report: Report, info: dict) -> list[str]:
    fields = info.get("fields") or {}
    lines: list[str] = []
    counts = fields.get("forecast_counts")
    if isinstance(counts, dict):
        rows = counts.get("shadow_rows")
        if rows is not None:
            lines.append(f"shadow rows {rows}")
    decision = fields.get("decision")
    if decision:
        lines.append(f"decision: {decision}")
    by_tour = fields.get("by_tour")
    if isinstance(by_tour, dict):
        tours = []
        for tour, data in by_tour.items():
            if isinstance(data, dict) and data:
                tours.append(tour)
            elif isinstance(data, dict):
                tours.append(tour)
        if tours:
            lines.append("by tour: " + ", ".join(tours))
    return lines


_HANDLERS = {
    "cfb_grade": _cfb_grade,
    "cfb_snapshot": _cfb_snapshot,
    "mlb_learning_review": _mlb_learning_review,
    "mlb_grade": _mlb_grade,
    "mlb_season": _mlb_season,
    "nhl_snapshot": _nhl_snapshot,
    "soccer_snapshot": _soccer_snapshot,
    "tennis_shadow": _tennis_shadow,
}