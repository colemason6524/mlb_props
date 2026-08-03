from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import asdict, replace
from datetime import date, timedelta
from typing import Callable, Iterable
from urllib.parse import urlencode

from ..cache import JsonCache
from ..models import ContactQualityShadow
from ..utils import fetch_text


CONTACT_QUALITY_SHADOW_VERSION = "contact-quality-shadow-v1"
BASEBALL_SAVANT_CSV_URL = "https://baseballsavant.mlb.com/statcast_search/csv"
STRIKEOUT_EVENTS = {"strikeout", "strikeout_double_play"}


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None


def _mean(values: Iterable[float]) -> float | None:
    items = list(values)
    if not items:
        return None
    return sum(items) / len(items)


def _rounded(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


def _row_sort_key(row: dict[str, str]) -> tuple[str, int, int]:
    return (
        row.get("game_date", ""),
        _int(row.get("game_pk")) or 0,
        _int(row.get("at_bat_number")) or 0,
    )


def _aggregate_xba(rows: list[dict[str, str]]) -> tuple[float | None, int]:
    expected_hits = 0.0
    opportunities = 0
    for row in rows:
        expected_ba = _float(row.get("estimated_ba_using_speedangle"))
        if expected_ba is not None:
            expected_hits += expected_ba
            opportunities += 1
        elif row.get("events", "").strip().lower() in STRIKEOUT_EVENTS:
            opportunities += 1
    if opportunities == 0:
        return None, 0
    return expected_hits / opportunities, opportunities


def _build_profile(
    batter_id: int,
    rows: list[dict[str, str]],
    *,
    screen_date: date,
    query_start: date,
    query_end: date,
) -> ContactQualityShadow | None:
    eligible_rows = [
        row
        for row in rows
        if row.get("game_date", "") < screen_date.isoformat()
    ]
    plate_appearances = [row for row in eligible_rows if row.get("events", "").strip()]
    if not plate_appearances:
        return None

    plate_appearances.sort(key=_row_sort_key, reverse=True)
    game_dates = sorted(
        {row.get("game_date", "") for row in plate_appearances if row.get("game_date")},
        reverse=True,
    )
    recent_dates = set(game_dates[:10])
    recent_pa = [row for row in plate_appearances if row.get("game_date") in recent_dates]

    bbe = [
        row
        for row in plate_appearances
        if _float(row.get("estimated_ba_using_speedangle")) is not None
        or _float(row.get("launch_speed")) is not None
    ]
    recent_bbe = [row for row in bbe if row.get("game_date") in recent_dates]
    last_25_bbe = bbe[:25]

    season_xba, season_opportunities = _aggregate_xba(plate_appearances)
    recent_xba, recent_opportunities = _aggregate_xba(recent_pa)
    xba_last_25 = _mean(
        value
        for row in last_25_bbe
        if (value := _float(row.get("estimated_ba_using_speedangle"))) is not None
    )
    exit_velocities = [
        value
        for row in last_25_bbe
        if (value := _float(row.get("launch_speed"))) is not None
    ]
    launch_angles = [
        value
        for row in last_25_bbe
        if (value := _float(row.get("launch_angle"))) is not None
    ]
    launch_zones = [
        value
        for row in last_25_bbe
        if (value := _int(row.get("launch_speed_angle"))) is not None
    ]

    hard_hit_rate = (
        sum(value >= 95.0 for value in exit_velocities) / len(exit_velocities)
        if exit_velocities
        else None
    )
    sweet_spot_rate = (
        sum(8.0 <= value <= 32.0 for value in launch_angles) / len(launch_angles)
        if launch_angles
        else None
    )
    barrel_rate = (
        sum(value == 6 for value in launch_zones) / len(launch_zones)
        if launch_zones
        else None
    )

    return ContactQualityShadow(
        version=CONTACT_QUALITY_SHADOW_VERSION,
        source="Baseball Savant Statcast Search CSV",
        screen_date=screen_date.isoformat(),
        query_start_date=query_start.isoformat(),
        query_end_date=query_end.isoformat(),
        batter_id=batter_id,
        games_available=len(game_dates),
        recent_game_dates=game_dates[:10],
        plate_appearances_season=len(plate_appearances),
        xba_opportunities_season=season_opportunities,
        xba_opportunities_last_10_games=recent_opportunities,
        tracked_bbe_season=len(bbe),
        tracked_bbe_last_10_games=len(recent_bbe),
        tracked_bbe_last_25=len(last_25_bbe),
        season_xba=_rounded(season_xba),
        xba_last_10_games=_rounded(recent_xba),
        xba_last_25_bbe=_rounded(xba_last_25),
        hard_hit_rate_last_25_bbe=_rounded(hard_hit_rate),
        sweet_spot_rate_last_25_bbe=_rounded(sweet_spot_rate),
        barrel_rate_last_25_bbe=_rounded(barrel_rate),
        avg_exit_velocity_last_25_bbe=_rounded(_mean(exit_velocities), 1),
    )


def enrich_contact_quality_profile(
    profile: ContactQualityShadow,
    *,
    actual_avg_last_10: float,
    expected_at_bats: float | None,
) -> ContactQualityShadow:
    recent_xba = profile.xba_last_10_games
    season_xba = profile.season_xba
    blended_xba: float | None = None
    if recent_xba is not None and season_xba is not None:
        recent_weight = min(
            0.65,
            profile.xba_opportunities_last_10_games
            / (profile.xba_opportunities_last_10_games + 20.0),
        )
        blended_xba = recent_weight * recent_xba + (1.0 - recent_weight) * season_xba
    elif recent_xba is not None:
        blended_xba = recent_xba
    elif season_xba is not None:
        blended_xba = season_xba

    one_hit_probability = None
    if blended_xba is not None and expected_at_bats is not None:
        bounded_xba = min(max(blended_xba, 0.0), 1.0)
        one_hit_probability = 1.0 - (1.0 - bounded_xba) ** expected_at_bats

    gap = None if recent_xba is None else actual_avg_last_10 - recent_xba
    if (
        profile.xba_opportunities_last_10_games >= 20
        and profile.tracked_bbe_last_25 >= 25
    ):
        confidence = "high"
    elif (
        profile.xba_opportunities_last_10_games >= 10
        and profile.tracked_bbe_last_25 >= 15
    ):
        confidence = "medium"
    else:
        confidence = "low"

    flags: list[str] = []
    if confidence == "low":
        flags.append("CONTACT_SAMPLE_THIN")
    if gap is not None and gap >= 0.075:
        flags.append("RESULTS_ABOVE_CONTACT")
    elif gap is not None and gap <= -0.075:
        flags.append("CONTACT_UNLUCKY")
    if (
        recent_xba is not None
        and season_xba is not None
        and recent_xba - season_xba >= 0.025
    ):
        flags.append("XBA_TREND_PLUS")
    if (
        profile.hard_hit_rate_last_25_bbe is not None
        and profile.hard_hit_rate_last_25_bbe >= 0.45
    ):
        flags.append("HARD_HIT_PLUS")

    return replace(
        profile,
        actual_avg_last_10=round(actual_avg_last_10, 3),
        actual_ba_minus_xba_last_10=_rounded(gap),
        expected_at_bats=_rounded(expected_at_bats, 2),
        estimated_one_hit_probability=_rounded(one_hit_probability),
        confidence=confidence,
        flags=flags,
    )


class BaseballSavantContactSource:
    def __init__(
        self,
        cache: JsonCache,
        fetcher: Callable[..., str] = fetch_text,
    ) -> None:
        self.cache = cache
        self.fetcher = fetcher

    def fetch_profiles(
        self,
        batter_ids: Iterable[int],
        *,
        screen_date: date,
    ) -> dict[int, ContactQualityShadow]:
        ids = sorted({int(batter_id) for batter_id in batter_ids})
        if not ids:
            return {}

        query_start = date(screen_date.year, 1, 1)
        query_end = screen_date - timedelta(days=1)
        id_digest = hashlib.sha256(",".join(map(str, ids)).encode()).hexdigest()[:16]
        cache_key = (
            f"savant_contact_{CONTACT_QUALITY_SHADOW_VERSION}_"
            f"{screen_date.isoformat()}_{id_digest}"
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            return {
                int(batter_id): ContactQualityShadow(**payload)
                for batter_id, payload in cached.items()
            }

        params: list[tuple[str, str]] = [
            ("all", "true"),
            ("type", "batter"),
            ("player_type", "batter"),
            ("game_date_gt", query_start.isoformat()),
            ("game_date_lt", query_end.isoformat()),
            ("hfSea", f"{screen_date.year}|"),
            ("hfGT", "R|"),
            ("group_by", "name"),
            ("min_pitches", "0"),
            ("min_results", "0"),
            ("min_pas", "0"),
        ]
        params.extend(("batters_lookup[]", str(batter_id)) for batter_id in ids)
        csv_text = self.fetcher(f"{BASEBALL_SAVANT_CSV_URL}?{urlencode(params)}", timeout=60)
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        rows_by_batter: dict[int, list[dict[str, str]]] = {batter_id: [] for batter_id in ids}
        for row in reader:
            batter_id = _int(row.get("batter"))
            if batter_id in rows_by_batter:
                rows_by_batter[batter_id].append(row)

        profiles = {
            batter_id: profile
            for batter_id, rows in rows_by_batter.items()
            if (
                profile := _build_profile(
                    batter_id,
                    rows,
                    screen_date=screen_date,
                    query_start=query_start,
                    query_end=query_end,
                )
            )
            is not None
        }
        self.cache.set(
            cache_key,
            {str(batter_id): asdict(profile) for batter_id, profile in profiles.items()},
        )
        return profiles
