"""Per-project ROI windows for the dashboard (dashboard display only).

Computes W/L, units and ROI% over yesterday (single-day), 7-day, 30-day,
since-last-model-change and lifetime windows from each sport's grade artifacts.

This module never touches Discord output. It is purely a dashboard display
change.

Sanity checks: every window is checked for implausible pick density
(MAX_PICKS_PER_DAY per sport). A window that trips the check is flagged
suspect rather than silently displayed -- this is the lesson from the
2026-10-14 soccer incident where a backlog grade file (189 rows across 18
board dates) was misread as a single day's 122 picks.

Model-change tracking: the "since last change" window is anchored at the
most recent journal entry (``.dashboard/journal.jsonl`` in the project repo)
whose summary starts with the ``[model-change]`` prefix. Attribution is by
board print time, not the change date: a board printed before the change
deployed used the old logic even if its games fall on/after the change date
(picks carry an optional ``board_print`` timestamp; without one the window
falls back to the change date). Record one with::

    python3 -m dashboard checkpoint-update --project <id> --status observing \\
        --kind grade_event --label "model-change" \\
        --summary "[model-change] <human-readable description>"

Until a marker exists, the since-change window reports "no marker recorded".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import glob
import json
import os
import re


MODEL_CHANGE_PREFIX = "[model-change]"

# Plausible upper bound on graded picks per calendar day, per sport.
# Anything above this on a single date is flagged, not silently aggregated.
# Caps reflect physical maximums (games x markets), not typical volume --
# a busy Saturday legitimately exceeds a quiet Tuesday.
MAX_PICKS_PER_DAY = {
    "nfl_props": 60,    # 16 games x 3 families + margin
    "cfb_props": 150,   # ~60 games on a big Saturday
    "nba_stats": 50,
    "nhl_props": 55,    # 16 games x 3 markets + margin
    "mlb_props": 75,    # ~15 games x 5 markets + margin
    "soccer_props": 100,  # many leagues worldwide
    "tennis_props": 60,
    "ufc_props": 50,
    "wnba_props": 200,   # player props: ~40/game x up to 5 games
    "golf_props": 40,
    "horses": 60,
}

DEFAULT_MAX_PICKS_PER_DAY = 60

# Projects with no supported grade source. Windows are reported as n/a.
UNSUPPORTED = {
    "nba_stats": "no grade artifacts",
    "horses": "collection only, no graded picks",
    "golf_props": "no grade artifacts",
}


# Cup competitions that are pooled separately from the domestic-league
# headline: they have a different grading basis (knockout draws, rotation,
# small samples per team) and were never part of the original model's
# intended scope. Matched case-insensitively on the row's `league` field.
SOCCER_UCL_LEAGUE = "UCL"


# Projects with a supported grade source for ROI windows.
PERFORMANCE_PROJECTS = frozenset({
    "nhl_props",
    "ufc_props",
    "soccer_props",
    "nfl_props",
    "cfb_props",
    "mlb_props",
    "tennis_props",
    "wnba_props",
})


@dataclass
class Pick:
    """One graded pick."""

    day: date  # calendar day the game was decided (game date, not board date)
    won: bool | None  # True/False; None = push (excluded from W/L, 0 units)
    profit_units: float  # signed profit in units (negative on loss)
    staked_units: float = 1.0  # amount risked
    league: str = ""  # competition code carried from the grade row (e.g. "UCL")
    board_print: datetime | None = None  # when the board that generated
        # this pick was printed (tz-aware). Used to attribute picks to the
        # correct side of a model change: a board printed before the change
        # deployed used the old logic even if its games fall on/after the
        # change date. None = unknown, falls back to day-based filtering.


@dataclass
class CumeSnapshot:
    """One cumulative totals snapshot."""

    day: date  # as-of date of the artifact
    wins: int
    losses: int
    pushes: int
    units: float


@dataclass
class Window:
    label: str
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    units: float = 0.0
    staked: float = 0.0
    suspect: bool = False
    suspect_reason: str = ""

    @property
    def roi_pct(self) -> float | None:
        if self.staked <= 0:
            return None
        return 100.0 * self.units / self.staked

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "wins": self.wins,
            "losses": self.losses,
            "pushes": self.pushes,
            "units": round(self.units, 2),
            "staked": round(self.staked, 2),
            "roi_pct": round(self.roi_pct, 2) if self.roi_pct is not None else None,
            "suspect": self.suspect,
            "suspect_reason": self.suspect_reason,
        }


def _parse_date(value) -> date | None:
    if not value:
        return None
    text = str(value)[:10]
    try:
        y, m, d = (int(x) for x in text.split("-"))
        return date(y, m, d)
    except (ValueError, AttributeError):
        return None


def _parse_datetime(value) -> datetime | None:
    """Parse an ISO-8601 timestamp; returns tz-aware datetime or None."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _date_from_filename(path: str) -> date | None:
    name = os.path.basename(path)
    m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def _aggregate_picks(picks: list[Pick], label: str, start: date | None,
                     end: date, max_per_day: int) -> Window:
    w = Window(label=label)
    per_day: dict[date, int] = {}
    for p in picks:
        if start is not None and p.day < start:
            continue
        if p.day > end:
            continue
        per_day[p.day] = per_day.get(p.day, 0) + 1
        if p.won is None:
            w.pushes += 1
        elif p.won:
            w.wins += 1
        else:
            w.losses += 1
        w.units += p.profit_units
        w.staked += p.staked_units
    bad = [(d, n) for d, n in sorted(per_day.items()) if n > max_per_day]
    if bad:
        w.suspect = True
        w.suspect_reason = (
            "implausible pick density: "
            + ", ".join(f"{d.isoformat()}={n}" for d, n in bad[:5])
            + f" (cap {max_per_day}/day)"
        )
    return w


def _diff_snapshots(snaps: list[CumeSnapshot], label: str,
                    start: date | None, end: date,
                    max_per_day: int) -> Window:
    """Window aggregate from cumulative snapshots via differencing."""
    w = Window(label=label)
    ordered = sorted(snaps, key=lambda s: s.day)
    end_snap = None
    for s in ordered:
        if s.day <= end:
            end_snap = s
    start_snap = None
    for s in ordered:
        if start is not None and s.day < start:
            start_snap = s
    if end_snap is None:
        w.suspect = True
        w.suspect_reason = "no grade snapshot in window"
        return w
    base = start_snap or CumeSnapshot(day=start or end_snap.day, wins=0,
                                      losses=0, pushes=0, units=0.0)
    w.wins = end_snap.wins - base.wins
    w.losses = end_snap.losses - base.losses
    w.pushes = end_snap.pushes - base.pushes
    w.units = end_snap.units - base.units
    n = w.wins + w.losses + w.pushes
    w.staked = float(n)  # flat 1u staking
    if n < 0:
        w.suspect = True
        w.suspect_reason = "non-monotonic snapshots (n went negative)"
        return w
    span_days = max(1, (end_snap.day - base.day).days or 1)
    # For open-ended windows (lifetime), measure density over the actual
    # span of snapshots, not the degenerate 1-day base.
    if start is None and ordered:
        span_days = max(1, (end_snap.day - ordered[0].day).days or 1)
    window_days = span_days if start is None else max(1, (end - start).days or 1)
    density = n / window_days if n else 0
    if density > max_per_day:
        w.suspect = True
        w.suspect_reason = (
            f"implausible pick density: {density:.0f}/day over {window_days}d "
            f"(cap {max_per_day}/day)"
        )
    return w


def _windows_from_picks(picks: list[Pick], project_id: str, today: date,
                        change_date: date | None,
                        change_label: str | None,
                        change_ts: datetime | None = None) -> list[dict]:
    cap = MAX_PICKS_PER_DAY.get(project_id, DEFAULT_MAX_PICKS_PER_DAY)
    yesterday = today - timedelta(days=1)
    out = [
        _aggregate_picks(picks, "yesterday", yesterday, yesterday, cap),
        _aggregate_picks(picks, "7d", today - timedelta(days=7), today, cap),
        _aggregate_picks(picks, "30d", today - timedelta(days=30), today, cap),
    ]
    if change_date is not None:
        # A board printed before the change deployed used the old logic,
        # even if its games fall on/after the change date (2026-10-15 NHL:
        # 6pm board printed 22:00Z, rework landed 22:42Z). Picks carrying a
        # board_print timestamp are attributed by it; others fall back to
        # the day-based filter in _aggregate_picks.
        since_picks = picks
        if change_ts is not None:
            since_picks = [p for p in picks
                           if p.board_print is None or p.board_print >= change_ts]
        out.append(_aggregate_picks(
            since_picks,            f"since {change_label or change_date.isoformat()}",
            change_date, today, cap))
    else:
        w = _aggregate_picks(picks, "since last change", None, today, cap)
        w.suspect = True
        w.suspect_reason = "no model-change marker recorded"
        out.append(w)
    out.append(_aggregate_picks(picks, "lifetime", None, today, cap))
    return [w.as_dict() for w in out]


def _windows_from_snapshots(snaps: list[CumeSnapshot], project_id: str,
                            today: date, change_date: date | None,
                            change_label: str | None) -> list[dict]:
    cap = MAX_PICKS_PER_DAY.get(project_id, DEFAULT_MAX_PICKS_PER_DAY)
    yesterday = today - timedelta(days=1)
    out = [
        _diff_snapshots(snaps, "yesterday", yesterday, yesterday, cap),
        _diff_snapshots(snaps, "7d", today - timedelta(days=7), today, cap),
        _diff_snapshots(snaps, "30d", today - timedelta(days=30), today, cap),
    ]
    if change_date is not None:
        out.append(_diff_snapshots(
            snaps, f"since {change_label or change_date.isoformat()}",
            change_date, today, cap))
    else:
        w = _diff_snapshots(snaps, "since last change", None, today, cap)
        w.suspect = True
        w.suspect_reason = "no model-change marker recorded"
        out.append(w)
    out.append(_diff_snapshots(snaps, "lifetime", None, today, cap))
    return [w.as_dict() for w in out]


# ---------------------------------------------------------------------------
# Per-sport parsers
# ---------------------------------------------------------------------------

def parse_nhl_ledger(lines: list[str]) -> list[Pick]:
    picks: list[Pick] = []
    for line in lines:
        try:
            r = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(r, dict):
            continue
        result = (r.get("result") or "").lower()
        if result not in ("win", "loss", "push"):
            continue
        day = _parse_date(r.get("nhl_game_date") or r.get("board_date"))
        if day is None:
            continue
        try:
            profit = float(r.get("units"))
        except (TypeError, ValueError):
            continue
        won = True if result == "win" else (False if result == "loss" else None)
        board_print = _parse_datetime(
            r.get("canonical_generated_at_utc") or r.get("snapshot_timestamp"))
        picks.append(Pick(day=day, won=won, profit_units=profit,
                          staked_units=1.0, board_print=board_print))
    return picks


def _profit_from_odds(won: bool | None, american=None, decimal=None) -> float:
    if won is None:
        return 0.0
    if not won:
        return -1.0
    if decimal is not None:
        try:
            return max(0.0, float(decimal) - 1.0)
        except (TypeError, ValueError):
            pass
    if american is not None:
        try:
            a = float(american)
        except (TypeError, ValueError):
            return 0.0
        if a > 0:
            return a / 100.0
        if a < 0:
            return 100.0 / abs(a)
    return 0.0


# Tiers that represent actual bets (vs "Watch" which is monitoring only).
UFC_BET_TIERS = {"Core", "Lean"}


def _ufc_snapshot_id(row: dict) -> str:
    """Snapshot identity of a ledger row: the ``grade_id`` prefix.

    Ledger v2 ids are ``{snapshot_id}:{candidate_id}`` (see ``grade.py`` in
    ufc_props); the prefix names the board print the row was graded from.
    """
    gid = str(row.get("grade_id") or "")
    return gid.split(":")[0] if ":" in gid else gid


def _ufc_snapshot_ts(row: dict) -> str:
    """Ordering timestamp for a snapshot row (ISO-8601 strings sort correctly)."""
    return str(row.get("snapshot_generated_at") or row.get("graded_at") or "")


def _ufc_latest_snapshot_groups(rows: list[dict]) -> list[list[dict]]:
    """One row group per fight, limited to that fight's latest snapshot.

    This mirrors the end-of-card recap rule in ufc_props ``grade.py``
    (``_latest_snapshot_rows`` + ``_staked_picks``): a fight contributes at
    most one wager, taken from the newest board snapshot, and only when the
    board staked a side in *that* snapshot. Pooling snapshots double-counts
    bets the board later re-tiered away -- e.g. a Lean loss in a 09-22
    snapshot that the 09-26 snapshot re-priced to Watch.

    Snapshot ordering is (latest snapshot_generated_at/graded_at in the
    group, snapshot id); the id breaks exact timestamp ties so the choice
    never depends on row order. Rows carrying no snapshot metadata (no
    ``fight_id``) are returned each as its own group, unchanged: there is
    nothing to group them by, and dropping them would lose real history.

    Drift guard: ``tests/test_ufc_ledger_snapshot.py`` pins the totals of
    this rule to the recap's 2-2 / -1.39u on real ledger rows, and
    ``ufc_props/tests/test_ledger_snapshot_contract.py`` pins the recap side
    to the same fixture. The logic is duplicated rather than imported: the
    recap lives in a separately deployed repo, and a cross-repo import would
    couple the two deploys.
    """
    groups: list[list[dict]] = []
    by_fight: dict[str, list[dict]] = {}
    for r in rows:
        fight_id = r.get("fight_id")
        if not fight_id:
            groups.append([r])
            continue
        by_fight.setdefault(str(fight_id), []).append(r)
    # Sorted so the output order never depends on the input order.
    for fight_id in sorted(by_fight):
        by_snap: dict[str, list[dict]] = {}
        for r in by_fight[fight_id]:
            by_snap.setdefault(_ufc_snapshot_id(r), []).append(r)
        latest = max(by_snap, key=lambda s: (
            max(_ufc_snapshot_ts(r) for r in by_snap[s]), s))
        groups.append(by_snap[latest])
    return groups


def parse_ufc_ledger(lines: list[str]) -> list[Pick]:
    """Ledger v2 -> Picks: one wager per fight, latest snapshot only.

    Tier filtering happens *after* snapshot selection, exactly as the recap
    does it. Filtering first would keep a staked row from a superseded
    snapshot whose fight no longer has a stake in the newest one.
    """
    rows: list[dict] = []
    for line in lines:
        try:
            r = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(r, dict):
            rows.append(r)
    picks: list[Pick] = []
    for fight_rows in _ufc_latest_snapshot_groups(rows):
        # Only count actual bets; "Watch" entries are monitoring, not wagers.
        # (Counting both sides of every fight would force a .500 record.)
        staked: list[tuple[dict, date, str]] = []
        for r in fight_rows:
            if r.get("tier") not in UFC_BET_TIERS:
                continue
            result = (r.get("result") or "").lower()
            if result not in ("win", "loss", "push"):
                continue
            day = _parse_date(r.get("event_date"))
            if day is None:
                continue
            staked.append((r, day, result))
        if not staked:
            continue
        # At most one wager per fight: the highest-p_fair staked side, the
        # same tie-break the recap's _staked_picks uses.
        r, day, result = max(staked, key=lambda t: float(t[0].get("p_fair") or 0))
        won = True if result == "win" else (False if result == "loss" else None)
        profit = _profit_from_odds(won, r.get("american"), r.get("decimal"))
        picks.append(Pick(day=day, won=won, profit_units=profit, staked_units=1.0))
    return picks


def _soccer_is_ucl(league) -> bool:
    return str(league or "").strip().upper() == SOCCER_UCL_LEAGUE


def parse_soccer_grades(rows: list[dict], valid_ids: set | None = None,
                        cohort: str | None = None) -> list[Pick]:
    """Soccer grade rows -> Picks.

    cohort: None = every league (default, historical behaviour);
            "domestic" = exclude SOCCER_UCL_LEAGUE (case-insensitive);
            "ucl" = only SOCCER_UCL_LEAGUE.
    """
    picks: list[Pick] = []
    seen_ids: set = set()
    # Filter to settled rows BEFORE deduping: a pick appears as "pending"
    # in early files and "settled" later; we want the settled occurrence.
    settled = [
        r for r in rows
        if isinstance(r, dict)
        and (r.get("result_status") or "").lower() == "settled"
    ]
    for r in settled:
        # Dedupe: the same pick appears in many backlog grade files.
        fid = r.get("forecast_id") or (r.get("match_id"), r.get("selected"))
        # valid_ids (from the newest file) is the source of truth for which
        # picks exist. Picks removed from the newest file (junk rows,
        # corrections) are excluded even if settled in older files.
        if valid_ids is not None and fid not in valid_ids:
            continue
        if fid in seen_ids:
            continue
        league = str(r.get("league") or "")
        if cohort == "domestic" and _soccer_is_ucl(league):
            continue
        if cohort == "ucl" and not _soccer_is_ucl(league):
            continue
        seen_ids.add(fid)
        result = (r.get("result") or "").lower()
        if result not in ("win", "loss", "push"):
            continue
        # Grade on the day the game was decided, not the board date.
        # Fall back to kickoff/board date when verification date is missing.
        day = (_parse_date(r.get("verified_result_date"))
               or _parse_date(r.get("verified_kickoff_utc"))
               or _parse_date(r.get("board_date")))
        if day is None:
            continue
        try:
            profit = float(r.get("units"))
        except (TypeError, ValueError):
            continue
        won = True if result == "win" else (False if result == "loss" else None)
        picks.append(Pick(day=day, won=won, profit_units=profit,
                          staked_units=1.0, league=league))
    return picks



def parse_tennis_grades(report_paths, source="bovada"):
    picks = []
    seen = set()
    for rpath in sorted(report_paths):
        try:
            with open(rpath, errors="replace") as f:
                report = json.load(f)
        except (OSError, ValueError):
            continue
        daily = report.get("daily") or {}
        if not isinstance(daily, dict):
            continue
        for day_str, day_data in daily.items():
            if not isinstance(day_data, dict):
                continue
            day = _parse_date(day_str)
            if day is None:
                continue
            refs = day_data.get("reference_returns") or {}
            summary = refs.get(source)
            if not isinstance(summary, dict):
                continue
            key = (day_str, source)
            if key in seen:
                continue
            seen.add(key)
            settled = int(summary.get("settled") or 0)
            wins = int(summary.get("wins") or 0)
            if settled <= 0:
                continue
            losses = settled - wins
            try:
                units = float(summary.get("hypothetical_units") or 0.0)
            except (TypeError, ValueError):
                continue
            if wins > 0:
                avg_win = (units + losses) / wins
                for _ in range(wins):
                    picks.append(Pick(day=day, won=True,
                                      profit_units=avg_win, staked_units=1.0))
            for _ in range(losses):
                picks.append(Pick(day=day, won=False,
                                  profit_units=-1.0, staked_units=1.0))
    return picks


def parse_mlb_daily(path: str) -> list[Pick]:
    """One learning_review md file = one day of decided plays."""
    day = _date_from_filename(path)
    if day is None:
        try:
            day = date.fromtimestamp(os.stat(path).st_mtime).astimezone().date()
        except OSError:
            return []
    picks: list[Pick] = []
    try:
        with open(path, errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    m = re.search(r"\|\s*all\s*\|\s*(\d+)\s*\|\s*(\d+)-(\d+)(?:-(\d+))?\s*\|[^|]*\|\s*([+-]?[\d.]+)",
                  text)
    if not m:
        return []
    n, wins, losses = int(m.group(1)), int(m.group(2)), int(m.group(3))
    pushes = int(m.group(4) or 0)
    units = float(m.group(5))
    if wins + losses <= 0:
        return []
    # Per-pick reconstruction: losses are flat -1u, so each win averaged
    # (units + losses) / wins. W/L counts and total units stay exact.
    win_profit = (units + losses) / wins if wins else 0.0
    for _ in range(wins):
        picks.append(Pick(day=day, won=True, profit_units=win_profit,
                          staked_units=1.0))
    for _ in range(losses):
        picks.append(Pick(day=day, won=False, profit_units=-1.0,
                          staked_units=1.0))
    for _ in range(pushes):
        picks.append(Pick(day=day, won=None, profit_units=0.0,
                          staked_units=1.0))
    return picks


def parse_mlb_ledger(lines: list[str]) -> list[Pick]:
    """Parse MLB picks_roi.jsonl — the Discord/grading source of truth.

    Only settled rows (outcome WIN/LOSS) count. SUPERSEDED and UNPRICED
    rows are excluded, matching the grading pipeline.
    """
    picks: list[Pick] = []
    for line in lines:
        try:
            r = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(r, dict):
            continue
        outcome = (r.get("outcome") or "").upper()
        if outcome not in ("WIN", "LOSS"):
            continue
        day = _parse_date(r.get("screen_date"))
        if day is None:
            continue
        try:
            profit = float(r.get("units"))
        except (TypeError, ValueError):
            continue
        won = True if outcome == "WIN" else False
        picks.append(Pick(day=day, won=won, profit_units=profit,
                          staked_units=1.0))
    return picks


def parse_wnba_ledger(lines: list[str]) -> list[Pick]:
    """Parse WNBA outputs/ledger/forecast_ledger.jsonl.

    This is the Discord/grading source of truth. Only settled,
    non-superseded rows count: superseded rows were replaced by a later
    snapshot (latest-snapshot grading); UNPRICED/PENDING rows are not
    settled. PUSH/VOID settle at 0.0 units.
    """
    picks: list[Pick] = []
    for line in lines:
        try:
            r = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(r, dict):
            continue
        if not r.get("graded"):
            continue
        if r.get("superseded_by"):
            continue
        outcome = (r.get("outcome") or "").upper()
        if outcome not in ("WIN", "LOSS", "PUSH", "VOID"):
            continue
        day = _parse_date(r.get("game_date"))
        if day is None:
            continue
        try:
            profit = float(r.get("units"))
        except (TypeError, ValueError):
            continue
        won = True if outcome == "WIN" else (False if outcome == "LOSS" else None)
        picks.append(Pick(day=day, won=won, profit_units=profit,
                          staked_units=1.0))
    return picks


def parse_nfl_cume(path: str) -> CumeSnapshot | None:
    day = _date_from_filename(path)
    if day is None:
        return None
    try:
        with open(path, errors="replace") as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    tot = {"wins": 0, "losses": 0, "pushes": 0, "units": 0.0}
    def walk(o, depth=0):
        if depth > 5 or not isinstance(o, dict):
            return
        for k, v in o.items():
            if isinstance(v, dict) and {"n", "wins", "units"} <= set(v.keys()) \
                    and k in ("bovada", "polymarket"):
                for kk in tot:
                    try:
                        tot[kk] += float(v.get(kk, 0))
                    except (TypeError, ValueError):
                        pass
            else:
                walk(v, depth + 1)
    walk(d.get("references", {}))
    return CumeSnapshot(day=day, wins=int(tot["wins"]), losses=int(tot["losses"]),
                        pushes=int(tot["pushes"]), units=tot["units"])


_CFB_SRC_RE = re.compile(
    r"^\s*(bovada|polymarket)\s+n=(\d+)\s+(\d+)-(\d+)-(\d+)\s+units=([+-]?[\d.]+)",
    re.MULTILINE)


def parse_cfb_cume(path: str) -> CumeSnapshot | None:
    day = _date_from_filename(path)
    if day is None:
        return None
    try:
        with open(path, errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    wins = losses = pushes = 0
    units = 0.0
    found = False
    for m in _CFB_SRC_RE.finditer(text):
        found = True
        wins += int(m.group(3))
        losses += int(m.group(4))
        pushes += int(m.group(5))
        units += float(m.group(6))
    if not found:
        return None
    return CumeSnapshot(day=day, wins=wins, losses=losses, pushes=pushes,
                        units=units)


# ---------------------------------------------------------------------------
# Model-change tracking via journal markers
# ---------------------------------------------------------------------------

def find_model_change(repo_dir: str) -> tuple[datetime | None, date | None, str | None]:
    """Return (timestamp, date, label) of the latest [model-change] entry.

    The timestamp is the full deployment time from the journal entry; the
    "since last change" window uses it to exclude boards printed before
    the change deployed (see _windows_from_picks).
    """
    path = os.path.join(repo_dir, ".dashboard", "journal.jsonl")
    best: tuple[datetime | None, date | None, str | None] = (None, None, None)
    try:
        with open(path, errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                summary = str(e.get("summary") or "")
                if not summary.startswith(MODEL_CHANGE_PREFIX):
                    continue
                ts = _parse_datetime(e.get("updated_at"))
                if ts is None:
                    continue
                day = ts.date()
                label = summary[len(MODEL_CHANGE_PREFIX):].strip()[:80]
                if best[0] is None or ts >= best[0]:
                    best = (ts, day, label or None)
    except OSError:
        pass
    return best


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------

def _read_lines(path: str) -> list[str]:
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def compute_for_project(project_id: str, repo_dir: str, today: date) -> dict:
    """Compute ROI windows for one project. Never raises on bad data."""
    if project_id in UNSUPPORTED:
        return {"ok": True, "supported": False,
                "reason": UNSUPPORTED[project_id], "cohorts": []}
    change_ts, change_date, change_label = find_model_change(repo_dir)
    # Cohort lines (currently only soccer UCL) are additive sub-breakdowns and
    # are never folded into `windows`, so every caller keeps its old shape.
    cohorts: list[dict] = []
    try:
        if project_id == "nhl_props":
            # roi_ledger.jsonl is the Discord source of truth: only priced +
            # bettable rows. canonical_ledger.jsonl includes unpriced and
            # non-bettable rows that Discord excludes.
            picks = parse_nhl_ledger(
                _read_lines(os.path.join(repo_dir, "outputs/ledger/roi_ledger.jsonl")))
            windows = _windows_from_picks(picks, project_id, today, change_date, change_label,
                                          change_ts=change_ts)
        elif project_id == "ufc_props":
            picks = parse_ufc_ledger(
                _read_lines(os.path.join(repo_dir, "outputs/grades/ledger_v2.jsonl")))
            windows = _windows_from_picks(picks, project_id, today, change_date, change_label,
                                          change_ts=change_ts)
        elif project_id == "soccer_props":
            all_rows: list[dict] = []
            newest_ids: set = set()
            # Newest file first: grade files are cumulative backlog artifacts,
            # and parse_soccer_grades keeps the first occurrence per
            # forecast_id. Scanning newest-first means the latest occurrence
            # wins (picks up result/unit corrections).
            file_list = sorted(glob.glob(os.path.join(
                    repo_dir, "outputs/forecast_grades/grade_forecast_*.json")),
                    reverse=True)
            for i, path in enumerate(file_list):
                try:
                    with open(path, errors="replace") as f:
                        d = json.load(f)
                    rows = d.get("rows") if isinstance(d, dict) else None
                    if isinstance(rows, list):
                        # The newest file is the source of truth for which
                        # picks exist. Collect its forecast_ids so picks
                        # removed from it (junk rows, corrections) are not
                        # counted from stale older files.
                        if i == 0:
                            for r in rows:
                                if isinstance(r, dict):
                                    fid = (r.get("forecast_id")
                                           or (r.get("match_id"), r.get("selected")))
                                    newest_ids.add(fid)
                        all_rows.extend(rows)
                except (OSError, json.JSONDecodeError):
                    continue
            # parse_soccer_grades dedupes by forecast_id; newest occurrence wins,
            # restricted to picks present in the newest file.
            # UCL is pooled separately (cup competition, different grading
            # basis, small per-team samples): the headline `windows` cover
            # domestic leagues only, and UCL is reported as a cohort line.
            domestic = parse_soccer_grades(all_rows, valid_ids=newest_ids,
                                           cohort="domestic")
            ucl = parse_soccer_grades(all_rows, valid_ids=newest_ids,
                                      cohort="ucl")
            windows = _windows_from_picks(domestic, project_id, today,
                                          change_date, change_label,
                                          change_ts=change_ts)
            ucl_windows = _windows_from_picks(ucl, project_id, today,
                                              change_date, change_label,
                                              change_ts=change_ts)
            by_label = {w["label"]: w for w in ucl_windows}
            for label, text in (("lifetime", "UCL lifetime"),
                                ("7d", "UCL 7d")):
                w = by_label.get(label)
                if w is None:
                    continue
                if not (w.get("wins") or w.get("losses") or w.get("pushes")):
                    continue
                entry = dict(w)
                entry["label"] = text
                entry["cohort"] = SOCCER_UCL_LEAGUE
                cohorts.append(entry)
        elif project_id == "mlb_props":
            # picks_roi.jsonl is the Discord/grading source of truth.
            # (parse_mlb_daily on learning_review_*.md is legacy; the md
            # summary tables can disagree with settled ledger outcomes.)
            picks = parse_mlb_ledger(
                _read_lines(os.path.join(repo_dir, "outputs/ledger/picks_roi.jsonl")))
            windows = _windows_from_picks(picks, project_id, today, change_date, change_label,
                                          change_ts=change_ts)
        elif project_id == "nfl_props":
            snaps = [s for s in (
                parse_nfl_cume(p) for p in sorted(glob.glob(os.path.join(
                    repo_dir, "outputs/forecast_backtests/grade_forecast_*.json"))))
                if s is not None]
            windows = _windows_from_snapshots(snaps, project_id, today, change_date, change_label)
        elif project_id == "cfb_props":
            snaps = [s for s in (
                parse_cfb_cume(p) for p in sorted(glob.glob(os.path.join(
                    repo_dir, "outputs/forecast_backtests/grade_forecast_*.txt"))))
                if s is not None]
            windows = _windows_from_snapshots(snaps, project_id, today, change_date, change_label)
        elif project_id == "tennis_props":
            picks = parse_tennis_grades(sorted(glob.glob(os.path.join(
                repo_dir, "outputs/grades/forecast_board_*.json"))))
            windows = _windows_from_picks(picks, project_id, today,
                                          change_date, change_label,
                                          change_ts=change_ts)
        elif project_id == "wnba_props":
            # forecast_ledger.jsonl is the Discord/grading source of truth.
            # Only settled, non-superseded rows count (latest-snapshot grading).
            picks = parse_wnba_ledger(
                _read_lines(os.path.join(repo_dir, "outputs/ledger/forecast_ledger.jsonl")))
            windows = _windows_from_picks(picks, project_id, today,
                                          change_date, change_label,
                                          change_ts=change_ts)
        else:
            return {"ok": True, "supported": False, "cohorts": [],
                    "reason": f"no parser for {project_id}"}
    except Exception as exc:  # noqa: BLE001 - display must never crash
        return {"ok": False, "error": str(exc)[:200]}
    return {
        "ok": True,
        "supported": True,
        "project_id": project_id,
        "as_of": today.isoformat(),
        "change_date": change_date.isoformat() if change_date else None,
        "change_label": change_label,
        "windows": windows,
        "cohorts": cohorts,
    }


# ---------------------------------------------------------------------------
# MLB season record -- canonical, price-predicated
# ---------------------------------------------------------------------------

# The season figure is the sum of every screen date's `canonical` block: one
# row per proposition_id, graded at the recorded ledger price. `units` is only
# defined when `price is not None` (mlb_props commit 0c62483), so summing
# canonical is the live record the integrity audit recomputes.
#
# LEGACY -- NEVER A SEASON FIGURE: `latest_roi` is the newest *revision* of one
# screen date, because later runs of the same date supersede earlier ones.
# Summed over the glob it answers "what did the last run of each day say"
# (383-359-4, -59.549u on 724 priced rows), ~293 priced rows short of the
# record. It may label a single date; the season line must not read it.

MLB_SEASON_GLOB = "outputs/grades/forecast_board_*.json"


def _artifact_date(path):
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", os.path.basename(path))
    return m.group(0) if m else None


def parse_mlb_season(paths):
    """Sum per-date canonical ROI/summary blocks into the season record."""
    by_date = {}
    for path in paths:
        try:
            with open(path, errors="replace") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        screen_date = payload.get("screen_date") or _artifact_date(path)
        if not screen_date:
            continue
        canonical = payload.get("canonical")
        canonical = canonical if isinstance(canonical, dict) else {}
        rows = canonical.get("rows")
        roi = canonical.get("roi")
        roi = roi if isinstance(roi, dict) else {}
        summary = canonical.get("summary")
        summary = summary if isinstance(summary, dict) else {}
        # Newest artifact per screen date wins (paths arrive sorted ascending).
        by_date[screen_date] = {
            "graded": bool(rows),
            "plays": int(roi.get("plays") or 0),
            "units": float(roi.get("units") or 0.0),
            "totals": summary.get("totals") or {},
        }
    if not by_date:
        return {"ok": True, "exists": False}
    graded = {d: v for d, v in by_date.items() if v["graded"]}
    missing = sorted(d for d, v in by_date.items() if not v["graded"])
    plays = sum(v["plays"] for v in graded.values())
    units = round(sum(v["units"] for v in graded.values()), 3)
    wins = losses = pushes = 0
    for value in graded.values():
        wins += int(value["totals"].get("w") or 0)
        losses += int(value["totals"].get("l") or 0)
        pushes += int(value["totals"].get("p") or 0)
    season = {
        "source": "canonical",
        "label": "canonical: one row per proposition per screen date; price is the stake predicate",
        "dates": len(graded),
        "screen_dates": len(by_date),
        "missing_canonical_dates": missing,
        "first_date": min(graded) if graded else None,
        "last_date": max(graded) if graded else None,
        "plays": plays,
        "units": units,
        "roi": round(units / plays, 4) if plays else None,
        "record": {"w": wins, "l": losses, "p": pushes},
    }
    newest = paths[-1]
    return {
        "ok": True,
        "exists": True,
        "path": newest,
        "mtime": os.path.getmtime(newest),
        "season": season,
    }


def mlb_season_job(job):
    """Entry point for the dashboard REMOTE_SCRIPT 'mlb_season' job kind."""
    base = job.get("base") or "."
    pattern = job.get("glob") or MLB_SEASON_GLOB
    if not os.path.isabs(pattern):
        pattern = os.path.join(base, pattern)
    try:
        return parse_mlb_season(sorted(glob.glob(pattern)))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}


def perf_job(job: dict) -> dict:
    """Entry point for the dashboard REMOTE_SCRIPT 'performance' job kind."""
    project_id = job.get("project_id", "")
    base = job.get("base") or "."
    today = _parse_date(job.get("today")) or date.today()
    try:
        result = compute_for_project(project_id, base, today)
        return {"ok": True, "performance": result}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:300]}
