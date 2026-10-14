"""MLB Statcast data pipeline.

Collects pitch-level Statcast data via pybaseball (free, no API key),
aggregates to pitcher/team level, and produces feature-ready profiles
for the game model.

Design:
- Daily collection: pull yesterday's Statcast, aggregate to per-pitcher
  per-day rows. Raw pitch-by-pitch is NOT stored (VM lean rule).
- Trailing profiles: 30-day rolling aggregates per pitcher, matching the
  existing l30 pattern in the game engine.
- Features (prototype, not yet wired into production):
  - SP stuff+: velocity + spin + movement composite
  - Pitch mix vs lineup: SP's pitch mix against lineup's xwOBA by pitch type
  - Bullpen quality: trailing xwOBA allowed for relievers
  - xwOBA allowed: better than RA for true talent

Usage:
    from mlb_props.statcast import StatcastPipeline
    pipe = StatcastPipeline(cache_root=Path(".cache/statcast"))
    pipe.collect_day(date(2026, 10, 12))  # pull + aggregate one day
    profile = pipe.pitcher_profile(pitcher_id=12345, as_of=date(2026, 10, 13))
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .cache import JsonCache
from .utils import ensure_dir

# Statcast pipeline version. Bump when the aggregation schema changes.
STATCAST_PIPELINE_VERSION = "statcast-v1"

# Trailing window for pitcher profiles, matching the game engine's l30.
TRAILING_DAYS = 30

# An empty cached day is re-fetched while still this fresh (days). Guards
# against a transient empty fetch permanently poisoning the cache.
EMPTY_RETRY_DAYS = 3

# Minimum pitches for a reliable profile. Below this, we shrink to league avg.
MIN_PITCHES_PROFILE = 100

# Pitch type groups for mix analysis. Maps Statcast codes to families.
PITCH_FAMILIES = {
    "FF": "fastball",  # 4-seam
    "SI": "fastball",  # sinker
    "FC": "fastball",  # cutter
    "FS": "offspeed",  # splitter
    "CH": "offspeed",  # changeup
    "SL": "breaking",  # slider
    "CU": "breaking",  # curveball
    "KC": "breaking",  # knuckle curve
    "ST": "breaking",  # sweeper
    "SV": "breaking",  # slurve
    "KN": "breaking",  # knuckleball
    "EP": "offspeed",  # eephus
    "SC": "breaking",  # screwball
}


def _safe_mean(values: list[float]) -> float | None:
    items = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not items:
        return None
    return sum(items) / len(items)


def _safe_div(num: float, den: float) -> float | None:
    if den == 0:
        return None
    return num / den


@dataclass
class DailyPitcherRow:
    """One pitcher's aggregated Statcast for one day. Lean storage."""

    pitcher_id: int
    pitcher_name: str
    game_date: str  # ISO
    pitches: int
    # Velocity (mph)
    avg_velo_ff: float | None = None  # 4-seam avg
    avg_velo_overall: float | None = None
    max_velo: float | None = None
    # Spin (rpm)
    avg_spin_ff: float | None = None
    avg_spin_br: float | None = None  # breaking balls
    # Movement (inches)
    avg_pfx_x_ff: float | None = None
    avg_pfx_z_ff: float | None = None
    # Whiff / swing
    whiff_pct: float | None = None  # swinging strikes / swings
    chase_pct: float | None = None  # O-swing / out-of-zone pitches
    # Outcomes
    xwoba_allowed: float | None = None
    k_pct: float | None = None
    bb_pct: float | None = None
    # Pitch mix (fractions)
    mix_fastball: float | None = None
    mix_breaking: float | None = None
    mix_offspeed: float | None = None
    # Batted ball
    avg_exit_velo_against: float | None = None
    barrel_pct_against: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "DailyPitcherRow":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


@dataclass
class PitcherStatcastProfile:
    """Trailing-30d Statcast profile for one pitcher. Feature-ready."""

    pitcher_id: int
    as_of: str
    total_pitches: int
    # Stuff+ components (z-scored later at model time; raw here)
    avg_velo_ff: float | None = None
    avg_spin_ff: float | None = None
    avg_spin_br: float | None = None
    whiff_pct: float | None = None
    chase_pct: float | None = None
    xwoba_allowed: float | None = None
    k_pct: float | None = None
    bb_pct: float | None = None
    mix_fastball: float | None = None
    mix_breaking: float | None = None
    mix_offspeed: float | None = None
    avg_exit_velo_against: float | None = None
    # Reliability flag
    reliable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class TeamLineupStatcast:
    """Trailing-30d lineup xwOBA by pitch family. For pitch-mix matchup."""

    team: str
    as_of: str
    xwoba_vs_fastball: float | None = None
    xwoba_vs_breaking: float | None = None
    xwoba_vs_offspeed: float | None = None
    xwoba_overall: float | None = None
    pa_sample: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _is_swing(description: str | None) -> bool:
    if not description:
        return False
    d = description.lower()
    return "swinging" in d or "foul" in d or "in play" in d


def _is_whiff(description: str | None) -> bool:
    if not description:
        return False
    return "swinging_strike" in description.lower()


def _is_out_of_zone(zone: Any) -> bool | None:
    try:
        z = int(zone)
        return z > 9 or z < 1
    except (TypeError, ValueError):
        return None


def aggregate_pitcher_day(pitcher_id: int, pitcher_name: str, game_date: str, pitches: list[dict]) -> DailyPitcherRow:
    """Aggregate one pitcher's pitch list into a lean daily row."""
    row = DailyPitcherRow(
        pitcher_id=pitcher_id,
        pitcher_name=pitcher_name,
        game_date=game_date,
        pitches=len(pitches),
    )
    if not pitches:
        return row

    def col(key: str, pitch_filter=None) -> list[float]:
        vals = []
        for p in pitches:
            if pitch_filter and not pitch_filter(p):
                continue
            v = p.get(key)
            try:
                f = float(v) if v not in (None, "") else None
                if f is not None and not math.isnan(f):
                    vals.append(f)
            except (TypeError, ValueError):
                pass
        return vals

    def is_ff(p: dict) -> bool:
        return str(p.get("pitch_type", "")).upper() == "FF"

    def is_breaking(p: dict) -> bool:
        return PITCH_FAMILIES.get(str(p.get("pitch_type", "")).upper()) == "breaking"

    # Velocity
    ff_velo = col("release_speed", is_ff)
    all_velo = col("release_speed")
    row.avg_velo_ff = _safe_mean(ff_velo)
    row.avg_velo_overall = _safe_mean(all_velo)
    row.max_velo = max(all_velo) if all_velo else None

    # Spin
    row.avg_spin_ff = _safe_mean(col("release_spin_rate", is_ff))
    row.avg_spin_br = _safe_mean(col("release_spin_rate", is_breaking))

    # Movement (4-seam)
    row.avg_pfx_x_ff = _safe_mean(col("pfx_x", is_ff))
    row.avg_pfx_z_ff = _safe_mean(col("pfx_z", is_ff))

    # Whiff / chase
    swings = sum(1 for p in pitches if _is_swing(p.get("description")))
    whiffs = sum(1 for p in pitches if _is_whiff(p.get("description")))
    row.whiff_pct = _safe_div(whiffs, swings)

    ooz_pitches = [p for p in pitches if _is_out_of_zone(p.get("zone")) is True]
    ooz_swings = sum(1 for p in ooz_pitches if _is_swing(p.get("description")))
    row.chase_pct = _safe_div(ooz_swings, len(ooz_pitches))

    # Outcomes
    row.xwoba_allowed = _safe_mean(col("estimated_woba_using_speedangle"))

    # K / BB rates (per PA approximated by events)
    events = [str(p.get("events", "")).lower() for p in pitches]
    pa_events = [e for e in events if e not in ("", "null", "none")]
    # Count unique PAs by at_bat_number
    pa_ids = set()
    k_count = 0
    bb_count = 0
    for p in pitches:
        ab = p.get("at_bat_number")
        ev = str(p.get("events", "")).lower()
        if ab is not None and ev not in ("", "null", "none"):
            key = (p.get("game_pk"), ab)
            if key not in pa_ids:
                pa_ids.add(key)
                if "strikeout" in ev:
                    k_count += 1
                elif ev in ("walk", "intent_walk", "hit_by_pitch"):
                    bb_count += 1
    n_pa = len(pa_ids)
    row.k_pct = _safe_div(k_count, n_pa)
    row.bb_pct = _safe_div(bb_count, n_pa)

    # Pitch mix
    fams = [PITCH_FAMILIES.get(str(p.get("pitch_type", "")).upper()) for p in pitches]
    fams = [f for f in fams if f]
    n = len(fams)
    if n:
        row.mix_fastball = sum(1 for f in fams if f == "fastball") / n
        row.mix_breaking = sum(1 for f in fams if f == "breaking") / n
        row.mix_offspeed = sum(1 for f in fams if f == "offspeed") / n

    # Batted ball against
    row.avg_exit_velo_against = _safe_mean(col("launch_speed"))

    return row


class StatcastPipeline:
    """Daily Statcast collection + trailing profiles. VM-lean by design."""

    def __init__(self, cache_root: Path):
        self.cache = JsonCache(cache_root, ttl_hours=24 * 7)  # 7-day TTL
        self.root = cache_root
        ensure_dir(cache_root)

    def _day_key(self, day: date) -> str:
        return f"day-{day.isoformat()}"

    @staticmethod
    def _empty_retry_due(day: date, cached: dict[str, Any]) -> bool:
        """True when a cached empty day should be re-fetched instead of served.

        A transient pybaseball failure returns zero rows, and caching that
        empty result permanently loses the day. Statcast data can also land
        late. So an empty cached day still within EMPTY_RETRY_DAYS of today
        is re-fetched; older empty days are treated as true off-days.
        """
        summary = cached.get("summary", {})
        age_days = (date.today() - day).days
        return summary.get("pitches", 0) == 0 and 0 <= age_days <= EMPTY_RETRY_DAYS

    def collect_day(self, day: date) -> dict[str, Any]:
        """Pull one day of Statcast, aggregate per pitcher. Returns summary."""
        key = self._day_key(day)
        cached = self.cache.get(key)
        if cached is not None and not self._empty_retry_due(day, cached):
            return {"cached": True, "summary": cached.get("summary", {})}

        # Import here so the module loads without pybaseball installed.
        from pybaseball import statcast

        import warnings

        warnings.filterwarnings("ignore")
        df = statcast(start_dt=day.isoformat(), end_dt=day.isoformat())
        if df is None or len(df) == 0:
            summary = {"date": day.isoformat(), "pitchers": 0, "pitches": 0}
            self.cache.set(key, {"summary": summary, "rows": []})
            return {"cached": False, "summary": summary}

        # Group by pitcher
        pitchers: dict[int, list[dict]] = {}
        names: dict[int, str] = {}
        for _, r in df.iterrows():
            try:
                pid = int(r["pitcher"])
            except (TypeError, ValueError):
                continue
            pitchers.setdefault(pid, []).append(r.to_dict())
            names[pid] = str(r.get("player_name", ""))

        rows = []
        for pid, plist in pitchers.items():
            prow = aggregate_pitcher_day(pid, names.get(pid, ""), day.isoformat(), plist)
            rows.append(prow.to_dict())

        summary = {
            "date": day.isoformat(),
            "pitchers": len(rows),
            "pitches": int(sum(r["pitches"] for r in rows)),
            "version": STATCAST_PIPELINE_VERSION,
        }
        self.cache.set(key, {"summary": summary, "rows": rows})
        return {"cached": False, "summary": summary}

    def _iter_day_rows(self, start: date, end: date):
        """Yield daily pitcher rows in [start, end]. Uses stale-allowed reads."""
        day = start
        while day <= end:
            payload = self.cache.get_allow_stale(self._day_key(day))
            if payload:
                for r in payload.get("rows", []):
                    yield r
            day += timedelta(days=1)

    def pitcher_profile(self, pitcher_id: int, as_of: date) -> PitcherStatcastProfile | None:
        """Trailing-30d Statcast profile for one pitcher."""
        start = as_of - timedelta(days=TRAILING_DAYS)
        end = as_of - timedelta(days=1)  # exclude as_of (no leakage)

        relevant = [r for r in self._iter_day_rows(start, end) if r.get("pitcher_id") == pitcher_id]
        if not relevant:
            return None

        total_pitches = sum(r.get("pitches", 0) for r in relevant)

        def wavg(key: str) -> float | None:
            num, den = 0.0, 0
            for r in relevant:
                v = r.get(key)
                n = r.get("pitches", 0)
                if v is not None and n:
                    num += v * n
                    den += n
            return num / den if den else None

        # xwOBA and rates need pitch-weighted recomputation, not avg-of-avgs.
        # wavg by pitches is the right approximation for daily aggregates.
        profile = PitcherStatcastProfile(
            pitcher_id=pitcher_id,
            as_of=as_of.isoformat(),
            total_pitches=total_pitches,
            avg_velo_ff=wavg("avg_velo_ff"),
            avg_spin_ff=wavg("avg_spin_ff"),
            avg_spin_br=wavg("avg_spin_br"),
            whiff_pct=wavg("whiff_pct"),
            chase_pct=wavg("chase_pct"),
            xwoba_allowed=wavg("xwoba_allowed"),
            k_pct=wavg("k_pct"),
            bb_pct=wavg("bb_pct"),
            mix_fastball=wavg("mix_fastball"),
            mix_breaking=wavg("mix_breaking"),
            mix_offspeed=wavg("mix_offspeed"),
            avg_exit_velo_against=wavg("avg_exit_velo_against"),
            reliable=total_pitches >= MIN_PITCHES_PROFILE,
        )
        return profile

    def stuff_plus_inputs(self, profile: PitcherStatcastProfile) -> dict[str, float | None]:
        """Raw inputs for a stuff+ composite. Z-scoring happens at model time
        against league distributions (computed separately)."""
        return {
            "velo_ff": profile.avg_velo_ff,
            "spin_ff": profile.avg_spin_ff,
            "spin_br": profile.avg_spin_br,
            "whiff_pct": profile.whiff_pct,
            "chase_pct": profile.chase_pct,
        }


def collect_statcast_range(pipeline: StatcastPipeline, start: date, end: date) -> dict[str, Any]:
    """Backfill helper: collect every day in [start, end]."""
    results = {"days": 0, "cached": 0, "fresh": 0, "errors": []}
    day = start
    while day <= end:
        try:
            out = pipeline.collect_day(day)
            results["days"] += 1
            if out.get("cached"):
                results["cached"] += 1
            else:
                results["fresh"] += 1
        except Exception as exc:  # noqa: BLE001 - backfill must not die on one bad day
            results["errors"].append(f"{day.isoformat()}: {exc}")
        day += timedelta(days=1)
    return results
