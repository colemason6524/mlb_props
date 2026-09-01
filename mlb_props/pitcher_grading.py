from __future__ import annotations

import hashlib
import json
import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from urllib.parse import quote

from .config import ODDS_API_TEAM_ABBR
from .utils import fetch_json, normalize_name, normalize_team_abbr

PITCHER_STRIKEOUTS = "PITCHER_STRIKEOUTS"

BAND_60 = "60%+"
BAND_57 = "57-59%"
BAND_54 = "54-56%"
BAND_51 = "51-53%"
BAND_50 = "50%"

CONFIDENCE_BANDS = (BAND_60, BAND_57, BAND_54, BAND_51, BAND_50)

BETTING_RESOLVED = {"win", "loss", "push"}
VOID_STATUSES = {"void_no_start"}
OPEN_STATUSES = {"pending", "unmatched", "ambiguous", "source_error"}


@dataclass
class GradedCandidate:
    screen_date: date
    file_name: str
    subject_id: int | None
    pitcher_name: str
    team: str
    opponent: str
    prop_type: str
    side: str
    line: float
    bookmaker: str
    tier: str
    qualified: bool
    projected_outs: float | None = None
    projected_batters_faced: float | None = None
    projected_k_rate: float | None = None
    projected_strikeouts: float | None = None
    score: int = 0
    flags: list[str] = field(default_factory=list)
    hits_last_5: int = 0
    played_last_5: int = 0
    opportunity_confidence: str | None = None
    opportunity_flags: list[str] = field(default_factory=list)
    shadow_projected_outs: float | None = None
    shadow_projected_batters_faced: float | None = None
    shadow_pitch_budget: float | None = None
    confidence_percentage: int | None = None
    provisional_win_probability: float | None = None
    confidence_label: str | None = None
    recency_shadow_projected_k_rate: float | None = None
    recency_shadow_projected_strikeouts: float | None = None
    recency_shadow_projected_batters_faced: float | None = None
    recency_shadow_projected_outs: float | None = None
    recency_shadow_projection_edge: float | None = None
    recency_shadow_win_probability: float | None = None
    recency_shadow_confidence_percentage: int | None = None
    outcome: str = "pending"
    actual: float | None = None
    actual_outs: int | None = None
    actual_pitches: int | None = None
    actual_batters_faced: int | None = None
    actual_walks: int | None = None
    actual_hits: int | None = None
    actual_er: int | None = None
    game_pk: int | None = None
    resolution_method: str | None = None
    result_edge: float | None = None
    resolution_note: str | None = None
    event_id: str | None = None
    line_source: str | None = None
    line_collected_at: str | None = None
    price_shadow: dict | None = None


@dataclass
class ManifestEntry:
    file_name: str
    file_hash: str
    screen_date: date
    exported_at: str
    schema_version: int
    model_version: str
    tier_policy_version: str
    confidence_model_version: str
    display_policy_version: str
    opportunity_shadow_version: str
    recency_shadow_version: str
    run_note: str
    mode: str | None
    candidate_count: int
    shadow_count: int
    core_count: int
    lean_count: int
    watch_count: int
    games: int
    prop_lines: int
    coverage_floor: int | None
    coverage_status: str | None
    fanduel_pages_expected: int | None
    fanduel_pages_loaded: int | None
    fanduel_k_lines: int | None
    dk_k_lines: int | None
    dk_outs_lines: int | None
    earliest_first_pitch_utc: str | None = None
    pregame: bool | None = None
    discord_delivered: bool | None = None
    discord_sent_at: str | None = None
    conflicts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class GradedHistory:
    entries: list[ManifestEntry]
    candidates: list[GradedCandidate]
    integrity_errors: list[str] = field(default_factory=list)
    daily_card: list[GradedCandidate] = field(default_factory=list)
    daily_card_policy_version: str | None = None


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_key(candidate: dict) -> tuple:
    subject_id = candidate.get("subject_id")
    subject_key = subject_id if subject_id is not None else normalize_name(candidate.get("subject_name", ""))
    return (
        normalize_name(candidate.get("subject_name", "")),
        subject_key,
        candidate.get("prop_type"),
        str(candidate.get("side")),
        str(candidate.get("line")),
    )


def _tier_index(payload: dict) -> dict[str, set]:
    tiers: dict[str, set] = {"core": set(), "lean": set(), "watch": set()}
    for tier, key in (
        ("core", "displayed_candidates"),
        ("lean", "lean_candidates"),
        ("watch", "watch_candidates"),
    ):
        rows = payload.get(key)
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    tiers[tier].add(_candidate_key(row))
    return tiers


def _candidate_rows(payload: dict) -> list[dict]:
    rows = payload.get("candidates")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def _daily_card_rows(payload: dict) -> list[dict]:
    rows = payload.get("daily_card")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return []


def _graded_candidate_from_row(
    row: dict,
    *,
    screen_date: date,
    file_name: str,
    tier: str,
) -> GradedCandidate:
    confidence = _confidence_from_candidate(row)
    recency = _recency_shadow_from_candidate(row)
    opportunity = _opportunity_shadow_from_candidate(row)
    recency_confidence = recency.get("confidence_estimate")
    if not isinstance(recency_confidence, dict):
        recency_confidence = {}
    return GradedCandidate(
        screen_date=screen_date,
        file_name=file_name,
        subject_id=_safe_int(row.get("subject_id")),
        pitcher_name=str(row.get("subject_name") or "unknown"),
        team=str(row.get("team") or ""),
        opponent=str(row.get("opponent") or ""),
        prop_type=str(row.get("prop_type") or ""),
        side=str(row.get("side") or ""),
        line=_safe_float(row.get("line"), 0.0) or 0.0,
        bookmaker=str(row.get("bookmaker") or ""),
        tier=tier,
        qualified=True,
        projected_outs=_safe_float(row.get("projected_outs")),
        projected_batters_faced=_safe_float(row.get("projected_batters_faced")),
        projected_k_rate=_safe_float(row.get("projected_k_rate")),
        projected_strikeouts=_safe_float(row.get("projected_strikeouts")),
        score=_safe_int(row.get("score"), 0) or 0,
        flags=list(row.get("flags") or []),
        hits_last_5=_safe_int(row.get("hits_last_5"), 0) or 0,
        played_last_5=_safe_int(row.get("played_last_5"), 0) or 0,
        opportunity_confidence=str(opportunity.get("opportunity_confidence") or "")
        if opportunity.get("opportunity_confidence")
        else None,
        opportunity_flags=list(opportunity.get("flags") or []),
        shadow_projected_outs=_safe_float(opportunity.get("shadow_projected_outs")),
        shadow_projected_batters_faced=_safe_float(
            opportunity.get("shadow_projected_batters_faced")
        ),
        shadow_pitch_budget=_safe_float(opportunity.get("shadow_pitch_budget")),
        confidence_percentage=_safe_int(confidence.get("confidence_percentage")),
        provisional_win_probability=_safe_float(confidence.get("win_probability")),
        confidence_label=str(confidence.get("label") or "")
        if confidence.get("label")
        else None,
        recency_shadow_projected_k_rate=_safe_float(
            recency.get("shadow_projected_k_rate")
        ),
        recency_shadow_projected_strikeouts=_safe_float(
            recency.get("shadow_projected_strikeouts")
        ),
        recency_shadow_projected_batters_faced=_safe_float(
            recency.get("shadow_projected_batters_faced")
        ),
        recency_shadow_projected_outs=_safe_float(
            recency.get("shadow_projected_outs")
        ),
        recency_shadow_projection_edge=_safe_float(
            recency.get("shadow_projection_edge")
        ),
        recency_shadow_win_probability=_safe_float(
            recency_confidence.get("win_probability")
        ),
        recency_shadow_confidence_percentage=_safe_int(
            recency_confidence.get("confidence_percentage")
        ),
        event_id=str(row.get("event_id") or "") or None,
        line_source=str(row.get("line_source") or "") or None,
        line_collected_at=str(row.get("line_collected_at") or "") or None,
        price_shadow=row.get("price_shadow") if isinstance(row.get("price_shadow"), dict) else None,
    )


def _confidence_from_candidate(candidate: dict) -> dict:
    estimate = candidate.get("confidence_estimate")
    if isinstance(estimate, dict):
        return estimate
    return {}


def _recency_shadow_from_candidate(candidate: dict) -> dict:
    shadow = candidate.get("recency_shadow")
    if isinstance(shadow, dict):
        return shadow
    return {}


def _opportunity_shadow_from_candidate(candidate: dict) -> dict:
    shadow = candidate.get("opportunity_shadow")
    if isinstance(shadow, dict):
        return shadow
    return {}


def _delivery_status(payload: dict) -> tuple[bool | None, str | None]:
    delivery = payload.get("discord_delivery")
    if not isinstance(delivery, dict):
        return None, None
    enabled = bool(delivery.get("enabled"))
    attempted = bool(delivery.get("attempted"))
    ok = bool(delivery.get("ok"))
    sent_at = str(delivery.get("sent_at") or "") or None
    if not enabled:
        return None, sent_at
    if not attempted:
        return False, sent_at
    return bool(ok), sent_at


def _earliest_game_time_utc(payload: dict) -> str | None:
    slate_games = payload.get("slate_games")
    if not isinstance(slate_games, list) or not slate_games:
        return None
    earliest: datetime | None = None
    for game in slate_games:
        if not isinstance(game, dict):
            continue
        game_time = game.get("game_time_utc")
        if not game_time:
            continue
        try:
            normalized = game_time.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized).astimezone(timezone.utc)
        except ValueError:
            continue
        if earliest is None or parsed < earliest:
            earliest = parsed
    return earliest.isoformat() if earliest is not None else None


def _pregame_from_payload(payload: dict) -> bool | None:
    earliest = _earliest_game_time_utc(payload)
    exported_at = payload.get("exported_at")
    if not earliest or not exported_at:
        return None
    try:
        export_time = datetime.fromisoformat(str(exported_at))
        if export_time.tzinfo is None:
            export_time = export_time.replace(tzinfo=timezone.utc)
        first_pitch_time = datetime.fromisoformat(earliest.replace("Z", "+00:00")).astimezone(timezone.utc)
        return export_time < first_pitch_time
    except ValueError:
        return None


def _safe_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_history(history_dir: Path, schema_version: int) -> GradedHistory:
    entries: list[ManifestEntry] = []
    candidates: list[GradedCandidate] = []
    daily_card: list[GradedCandidate] = []
    integrity_errors: list[str] = []
    policy_version: str | None = None
    files = sorted(history_dir.glob("pitcher_props_*.json"))

    for path in files:
        try:
            payload = json.loads(path.read_text())
        except Exception as exc:
            integrity_errors.append(f"{path.name}: unreadable JSON: {exc}")
            continue
        screen_date_raw = payload.get("screen_date")
        if not screen_date_raw:
            integrity_errors.append(f"{path.name}: missing screen_date")
            continue
        screen_date = date.fromisoformat(screen_date_raw)
        schema = _safe_int(payload.get("history_schema_version"), 0) or 0
        if schema != schema_version:
            continue

        entry = ManifestEntry(
            file_name=path.name,
            file_hash=_file_hash(path),
            screen_date=screen_date,
            exported_at=str(payload.get("exported_at") or ""),
            schema_version=schema,
            model_version=str(payload.get("model_version") or "unknown"),
            tier_policy_version=str(payload.get("tier_policy_version") or "unknown"),
            confidence_model_version=str(payload.get("confidence_model_version") or "unknown"),
            display_policy_version=str(payload.get("display_policy_version") or "unknown"),
            opportunity_shadow_version=str(payload.get("shadow_feature_version") or "unknown"),
            recency_shadow_version=str(payload.get("recency_shadow_version") or "unknown"),
            run_note=str(payload.get("run_note") or ""),
            mode=(payload.get("settings") or {}).get("data_mode") if isinstance(payload.get("settings"), dict) else None,
            candidate_count=len(_candidate_rows(payload)),
            shadow_count=sum(
                1
                for row in _candidate_rows(payload)
                if isinstance(row.get("recency_shadow"), dict)
            ),
            core_count=len(payload.get("displayed_candidates") or [])
            if isinstance(payload.get("displayed_candidates"), list)
            else 0,
            lean_count=len(payload.get("lean_candidates") or [])
            if isinstance(payload.get("lean_candidates"), list)
            else 0,
            watch_count=len(payload.get("watch_candidates") or [])
            if isinstance(payload.get("watch_candidates"), list)
            else 0,
            games=_safe_int((payload.get("line_coverage") or {}).get("games")),
            prop_lines=_safe_int((payload.get("line_coverage") or {}).get("prop_lines")),
            coverage_floor=_safe_int((payload.get("line_coverage") or {}).get("floor")),
            coverage_status=payload.get("line_coverage_status"),
            fanduel_pages_expected=_safe_int(
                (payload.get("line_coverage") or {}).get("diagnostics", {}).get("fanduel_expected_team_pages")
            ),
            fanduel_pages_loaded=_safe_int(
                (payload.get("line_coverage") or {}).get("diagnostics", {}).get("fanduel_team_pages_loaded")
            ),
            fanduel_k_lines=_safe_int(
                (payload.get("line_coverage") or {}).get("diagnostics", {}).get("fanduel_strikeout_lines_found")
            ),
            dk_k_lines=_safe_int(
                (payload.get("line_coverage") or {}).get("diagnostics", {}).get("draftkings_strikeout_lines_found")
            ),
            dk_outs_lines=_safe_int(
                (payload.get("line_coverage") or {}).get("diagnostics", {}).get("draftkings_outs_lines_found")
            ),
            earliest_first_pitch_utc=_earliest_game_time_utc(payload),
            pregame=_pregame_from_payload(payload),
            discord_delivered=_delivery_status(payload)[0],
            discord_sent_at=_delivery_status(payload)[1],
        )

        if entry.candidate_count != entry.shadow_count:
            entry.conflicts.append(
                f"candidate_count={entry.candidate_count} shadow_count={entry.shadow_count}"
            )
        if isinstance(payload.get("candidates"), list) and (
            entry.core_count + entry.lean_count + entry.watch_count
        ) > entry.candidate_count:
            entry.conflicts.append(
                "tier arrays exceed candidate count"
            )

        tier_index = _tier_index(payload)
        for row in _candidate_rows(payload):
            if row.get("prop_type") != PITCHER_STRIKEOUTS:
                continue
            key = _candidate_key(row)
            tier = "none"
            for candidate_tier, keys in tier_index.items():
                if key in keys:
                    tier = candidate_tier
                    break
            candidates.append(
                _graded_candidate_from_row(
                    row,
                    screen_date=screen_date,
                    file_name=path.name,
                    tier=tier,
                )
            )
        for row in _daily_card_rows(payload):
            if row.get("prop_type") != PITCHER_STRIKEOUTS:
                continue
            daily_card.append(
                _graded_candidate_from_row(
                    row,
                    screen_date=screen_date,
                    file_name=path.name,
                    tier="daily_card",
                )
            )
        card_version = payload.get("daily_card_policy_version")
        if isinstance(card_version, str) and card_version:
            policy_version = card_version
        entries.append(entry)

    entries.sort(key=lambda entry: entry.screen_date)
    candidates.sort(key=lambda candidate: (candidate.screen_date, candidate.pitcher_name))
    return GradedHistory(
        entries=entries,
        candidates=candidates,
        integrity_errors=integrity_errors,
        daily_card=daily_card,
        daily_card_policy_version=policy_version,
    )


def earliest_first_pitch(
    entries: list[ManifestEntry], first_pitch_by_date: dict[date, str]
) -> None:
    for entry in entries:
        if entry.earliest_first_pitch_utc:
            if entry.pregame is None:
                entry.pregame = _pregame_from_export_and_first_pitch(
                    entry.exported_at, entry.earliest_first_pitch_utc
                )
            continue
        first_pitch = first_pitch_by_date.get(entry.screen_date)
        entry.earliest_first_pitch_utc = first_pitch
        if not first_pitch or not entry.exported_at:
            entry.pregame = None
            continue
        entry.pregame = _pregame_from_export_and_first_pitch(entry.exported_at, first_pitch)


def _pregame_from_export_and_first_pitch(exported_at: str, first_pitch: str) -> bool | None:
    if not exported_at or not first_pitch:
        return None
    try:
        export_time = datetime.fromisoformat(exported_at)
        if export_time.tzinfo is None:
            export_time = export_time.replace(tzinfo=timezone.utc)
        first_pitch_normalized = first_pitch.replace("Z", "+00:00")
        first_pitch_time = datetime.fromisoformat(first_pitch_normalized).astimezone(timezone.utc)
        return export_time < first_pitch_time
    except ValueError:
        return None


def _team_abbr(team_obj: dict | None) -> str:
    if not isinstance(team_obj, dict):
        return ""
    abbreviation = team_obj.get("abbreviation") or ""
    name = team_obj.get("name") or ""
    if abbreviation:
        return normalize_team_abbr(abbreviation)
    return normalize_team_abbr(ODDS_API_TEAM_ABBR.get(name, abbreviation))


class MlbGradingClient:
    BASE_URL = "https://statsapi.mlb.com/api/v1"

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir

    def _cached_json(self, cache_key: str, url: str) -> dict:
        if self.cache_dir is not None:
            path = self.cache_dir / f"{cache_key}.json"
            if path.exists():
                return json.loads(path.read_text())
        data = fetch_json(url)
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path = self.cache_dir / f"{cache_key}.json"
            path.write_text(json.dumps(data))
        return data

    def fetch_schedule(self, screen_date: date) -> list[dict]:
        hydrate = quote("probablePitcher(note),team", safe="(),")
        url = f"{self.BASE_URL}/schedule?sportId=1&date={screen_date.isoformat()}&hydrate={hydrate}"
        data = self._cached_json(f"schedule_{screen_date.isoformat()}", url)
        games: list[dict] = []
        for day in data.get("dates", []):
            for game in day.get("games", []):
                teams = game.get("teams", {})
                home = teams.get("home", {})
                away = teams.get("away", {})
                home_team = normalize_team_abbr((home.get("team") or {}).get("abbreviation", ""))
                away_team = normalize_team_abbr((away.get("team") or {}).get("abbreviation", ""))
                if not home_team or not away_team:
                    continue
                home_pitcher = home.get("probablePitcher") or {}
                away_pitcher = away.get("probablePitcher") or {}
                games.append(
                    {
                        "game_pk": game.get("gamePk"),
                        "game_date": game.get("gameDate"),
                        "home_team": home_team,
                        "away_team": away_team,
                        "home_pitcher_id": home_pitcher.get("id"),
                        "home_pitcher_name": home_pitcher.get("fullName", ""),
                        "away_pitcher_id": away_pitcher.get("id"),
                        "away_pitcher_name": away_pitcher.get("fullName", ""),
                        "double_header": game.get("doubleHeader"),
                    }
                )
        return games

    def fetch_pitcher_logs(self, pitcher_id: int, season: int) -> list[dict]:
        hydrate = quote(f"stats(group=[pitching],type=[gameLog],season={season})", safe="=[](),")
        url = f"{self.BASE_URL}/people?personIds={pitcher_id}&hydrate={hydrate}"
        data = self._cached_json(f"pitcher_gamelog_{pitcher_id}_{season}", url)
        people = data.get("people", [])
        if not people:
            return []
        person = people[0]
        splits: list[dict] = []
        for stat_block in person.get("stats", []):
            splits.extend(stat_block.get("splits", []))
        appearances: list[dict] = []
        for split in splits:
            stat = split.get("stat", {})
            game = split.get("game", {})
            game_date_raw = split.get("date") or game.get("gameDate")
            if not game_date_raw:
                continue
            game_date = game_date_raw[:10]
            appearances.append(
                {
                    "game_date": game_date,
                    "game_pk": game.get("gamePk"),
                    "team": _team_abbr(split.get("team")),
                    "opponent": _team_abbr(split.get("opponent")),
                    "did_start": bool(_safe_int(stat.get("gamesStarted"), 0)),
                    "strikeouts": _safe_int(stat.get("strikeOuts"), 0) or 0,
                    "outs": _safe_int(stat.get("outs"), 0) or 0,
                    "pitches": _safe_int(stat.get("numberOfPitches"), 0) or 0,
                    "batters_faced": _safe_int(stat.get("battersFaced"), 0) or 0,
                    "walks": _safe_int(stat.get("baseOnBalls"), 0) or 0,
                    "hits": _safe_int(stat.get("hits"), 0) or 0,
                    "er": _safe_int(stat.get("earnedRuns"), 0) or 0,
                }
            )
        return appearances


def resolve_candidates(
    candidates: list[GradedCandidate],
    client: MlbGradingClient,
    today: date | None = None,
) -> None:
    today = today or date.today()
    probable_index_by_date: dict[date, dict[tuple[str, str], int]] = {}
    for candidate in candidates:
        if candidate.screen_date >= today:
            candidate.outcome = "pending"
            candidate.resolution_note = "screen_date not yet final"
            continue
        if candidate.subject_id is None:
            games = client.fetch_schedule(candidate.screen_date)
            index = probable_index_by_date.setdefault(candidate.screen_date, {})
            if not index:
                for game in games:
                    for pitcher_id, pitcher_name, team in (
                        (game["home_pitcher_id"], game["home_pitcher_name"], game["home_team"]),
                        (game["away_pitcher_id"], game["away_pitcher_name"], game["away_team"]),
                    ):
                        if pitcher_id and pitcher_name:
                            index[(normalize_name(pitcher_name), team)] = pitcher_id
            candidate.subject_id = index.get(
                (normalize_name(candidate.pitcher_name), normalize_team_abbr(candidate.team))
            )
            if candidate.subject_id is None:
                candidate.outcome = "unmatched"
                candidate.resolution_note = "no subject_id and no probable-pitcher match"
                continue
        try:
            appearances = client.fetch_pitcher_logs(candidate.subject_id, candidate.screen_date.year)
        except Exception as exc:
            candidate.outcome = "source_error"
            candidate.resolution_note = str(exc)[:200]
            continue
        if not appearances:
            candidate.outcome = "unmatched"
            candidate.resolution_note = "no gamelog rows returned"
            continue
        candidate.resolution_method = "pitcher_gamelog"
        date_appearances = [a for a in appearances if a["game_date"] == candidate.screen_date.isoformat()]
        if not date_appearances:
            candidate.outcome = "void_no_start"
            candidate.resolution_note = "no appearance on screen date"
            continue
        starts = [a for a in date_appearances if a["did_start"]]
        if not starts:
            candidate.outcome = "void_no_start"
            candidate.resolution_note = "relief-only appearance by probable starter"
            continue
        expected_opponent = normalize_team_abbr(candidate.opponent)
        if candidate.event_id:
            event_matches = [
                a for a in starts if str(a["game_pk"]) == str(candidate.event_id)
            ]
            if len(event_matches) == 1:
                _apply_appearance(candidate, event_matches[0])
                continue
            if len(event_matches) > 1:
                candidate.outcome = "ambiguous"
                candidate.resolution_note = "multiple starts match event_id"
                continue
        matching = [a for a in starts if normalize_team_abbr(a["opponent"]) == expected_opponent]
        if len(matching) == 1:
            appearance = matching[0]
        elif len(matching) == 0 and len(starts) == 1:
            appearance = starts[0]
            candidate.resolution_note = (
                f"opponent mismatch (expected {candidate.opponent}, got {starts[0]['opponent']})"
            )
        elif len(matching) > 1:
            candidate.outcome = "ambiguous"
            candidate.resolution_note = "multiple starting appearances match opponent"
            continue
        else:
            candidate.outcome = "ambiguous"
            candidate.resolution_note = "multiple starts, none matching opponent"
            continue
        _apply_appearance(candidate, appearance)


def strict_eligible_candidates(
    history: GradedHistory,
    *,
    require_pregame: bool = True,
    require_delivered: bool = True,
) -> list[GradedCandidate]:
    entry_by_file = {entry.file_name: entry for entry in history.entries}
    eligible: list[GradedCandidate] = []
    excluded: list[dict] = []
    for candidate in history.candidates:
        entry = entry_by_file.get(candidate.file_name)
        reasons: list[str] = []
        if entry is None:
            reasons.append("no_manifest_entry")
        else:
            if require_pregame and entry.pregame is not True:
                reasons.append(
                    f"not_strictly_pregame"
                    if entry.pregame is not None
                    else "pregame_unknown"
                )
            if require_delivered and entry.discord_delivered is not True:
                reasons.append(
                    f"not_delivered"
                    if entry.discord_delivered is not None
                    else "delivery_unknown"
                )
        if reasons:
            excluded.append(
                {
                    "screen_date": candidate.screen_date.isoformat(),
                    "file_name": candidate.file_name,
                    "pitcher_name": candidate.pitcher_name,
                    "reasons": reasons,
                }
            )
            continue
        eligible.append(candidate)
    return eligible


def _apply_appearance(candidate: GradedCandidate, appearance: dict) -> None:
    actual = float(appearance["strikeouts"])
    candidate.actual = actual
    candidate.actual_outs = appearance["outs"]
    candidate.actual_pitches = appearance["pitches"]
    candidate.actual_batters_faced = appearance["batters_faced"]
    candidate.actual_walks = appearance["walks"]
    candidate.actual_hits = appearance["hits"]
    candidate.actual_er = appearance["er"]
    candidate.game_pk = appearance["game_pk"]
    if actual == candidate.line:
        candidate.outcome = "push"
    elif candidate.side == "OVER":
        candidate.outcome = "win" if actual > candidate.line else "loss"
    else:
        candidate.outcome = "win" if actual < candidate.line else "loss"
    candidate.result_edge = (
        actual - candidate.line if candidate.side == "OVER" else candidate.line - actual
    )
    if candidate.resolution_note is None:
        candidate.resolution_note = f"game_pk={appearance['game_pk']}"


def _mae(values: list[float]) -> float | None:
    return mean(abs(value) for value in values) if values else None


def _bias(values: list[float]) -> float | None:
    return mean(values) if values else None


def _rmse(values: list[float]) -> float | None:
    if not values:
        return None
    return (sum(value * value for value in values) / len(values)) ** 0.5


def _hit_rate(rows: list[GradedCandidate]) -> float | None:
    graded = [row for row in rows if row.outcome in {"win", "loss"}]
    if not graded:
        return None
    return sum(1 for row in graded if row.outcome == "win") / len(graded)


def active_vs_shadow_metrics(rows: list[GradedCandidate]) -> dict:
    active_k = [
        (row.actual or 0.0) - row.projected_strikeouts
        for row in rows
        if row.outcome in {"win", "loss", "push"}
        and row.projected_strikeouts is not None
        and row.actual is not None
    ]
    shadow_k = [
        (row.actual or 0.0) - row.recency_shadow_projected_strikeouts
        for row in rows
        if row.outcome in {"win", "loss", "push"}
        and row.recency_shadow_projected_strikeouts is not None
        and row.actual is not None
    ]
    active_bf = [
        (row.actual_batters_faced or 0) - row.projected_batters_faced
        for row in rows
        if row.outcome in {"win", "loss", "push"}
        and row.projected_batters_faced is not None
        and row.actual_batters_faced is not None
    ]
    shadow_bf = [
        (row.actual_batters_faced or 0) - row.recency_shadow_projected_batters_faced
        for row in rows
        if row.outcome in {"win", "loss", "push"}
        and row.recency_shadow_projected_batters_faced is not None
        and row.actual_batters_faced is not None
    ]
    active_outs = [
        (row.actual_outs or 0) - row.projected_outs
        for row in rows
        if row.outcome in {"win", "loss", "push"}
        and row.projected_outs is not None
        and row.actual_outs is not None
    ]
    shadow_outs = [
        (row.actual_outs or 0) - row.shadow_projected_outs
        for row in rows
        if row.outcome in {"win", "loss", "push"}
        and row.shadow_projected_outs is not None
        and row.actual_outs is not None
    ]
    shadow_bf_opp = [
        (row.actual_batters_faced or 0) - row.shadow_projected_batters_faced
        for row in rows
        if row.outcome in {"win", "loss", "push"}
        and row.shadow_projected_batters_faced is not None
        and row.actual_batters_faced is not None
    ]
    pitch_budget = [
        (row.actual_pitches or 0) - row.shadow_pitch_budget
        for row in rows
        if row.outcome in {"win", "loss", "push"}
        and row.shadow_pitch_budget is not None
        and row.actual_pitches is not None
    ]
    return {
        "k": {
            "active": {"bias": _bias(active_k), "mae": _mae(active_k), "rmse": _rmse(active_k)},
            "shadow": {"bias": _bias(shadow_k), "mae": _mae(shadow_k), "rmse": _rmse(shadow_k)},
            "n": len(active_k),
        },
        "bf": {
            "active": {"bias": _bias(active_bf), "mae": _mae(active_bf), "rmse": _rmse(active_bf)},
            "shadow": {"bias": _bias(shadow_bf), "mae": _mae(shadow_bf), "rmse": _rmse(shadow_bf)},
            "n": len(active_bf),
        },
        "outs": {
            "active": {"bias": _bias(active_outs), "mae": _mae(active_outs), "rmse": _rmse(active_outs)},
            "opportunity_shadow": {
                "bias": _bias(shadow_outs),
                "mae": _mae(shadow_outs),
                "rmse": _rmse(shadow_outs),
            },
            "opportunity_bf": {
                "bias": _bias(shadow_bf_opp),
                "mae": _mae(shadow_bf_opp),
                "rmse": _rmse(shadow_bf_opp),
            },
            "pitch_budget": {
                "bias": _bias(pitch_budget),
                "mae": _mae(pitch_budget),
                "rmse": _rmse(pitch_budget),
            },
            "n": len(active_outs),
        },
    }


def _confidence_band(percentage: int | None) -> str:
    if percentage is None:
        return "unknown"
    if percentage >= 60:
        return BAND_60
    if percentage >= 57:
        return BAND_57
    if percentage >= 54:
        return BAND_54
    if percentage >= 51:
        return BAND_51
    return BAND_50


def brier_and_calibration(rows: list[GradedCandidate]) -> dict:
    graded = [
        row
        for row in rows
        if row.outcome in {"win", "loss"}
        and row.provisional_win_probability is not None
    ]
    shadow_graded = [
        row
        for row in rows
        if row.outcome in {"win", "loss"}
        and row.provisional_win_probability is not None
        and row.recency_shadow_win_probability is not None
    ]
    active_brier = (
        mean(
            (row.provisional_win_probability - (1.0 if row.outcome == "win" else 0.0)) ** 2
            for row in graded
        )
        if graded
        else None
    )
    shadow_brier = (
        mean(
            (row.recency_shadow_win_probability - (1.0 if row.outcome == "win" else 0.0)) ** 2
            for row in shadow_graded
        )
        if shadow_graded
        else None
    )
    bands: dict[str, dict] = {}
    for band in CONFIDENCE_BANDS + ("unknown",):
        band_rows = [row for row in rows if _confidence_band(row.confidence_percentage) == band]
        graded_band = [row for row in band_rows if row.outcome in {"win", "loss"}]
        if not graded_band:
            continue
        average_forecast = mean(row.provisional_win_probability for row in graded_band)
        observed = sum(1 for row in graded_band if row.outcome == "win") / len(graded_band)
        bands[band] = {
            "n": len(graded_band),
            "avg_forecast": average_forecast,
            "observed": observed,
            "gap": observed - average_forecast,
            "hit_rate": _hit_rate(band_rows),
            "pushes": sum(1 for row in band_rows if row.outcome == "push"),
        }
    return {
        "active_brier": active_brier,
        "shadow_brier": shadow_brier,
        "common_brier_n": len(shadow_graded),
        "bands": bands,
        "graded_n": len(graded),
    }


def price_shadow_analysis(rows: list[GradedCandidate]) -> dict:
    priced = [row for row in rows if row.price_shadow]
    supports = 0
    against = 0
    for row in priced:
        flags = row.price_shadow.get("flags") or []
        if "PRICE_SUPPORTS_SIDE" in flags:
            supports += 1
        elif "PRICE_AGAINST_SIDE" in flags:
            against += 1
    return {
        "priced_rows": len(priced),
        "price_supports_side": supports,
        "price_against_side": against,
        "flagged_rows": supports + against,
        "unpriced_rows": sum(1 for row in rows if not row.price_shadow),
    }


def units_at_minus_110(wins: int, losses: int) -> float:
    """Flat 1-unit profit at -110 prices: win +100/110, loss -1."""
    return wins * (100 / 110) - losses


def daily_card_summary(history: GradedHistory) -> dict:
    """Summarize graded Daily Card rows against the always-under candidate baseline.

    The card rows must already be resolved (outcome set by resolve_candidates).
    The baseline is every resolved UNDER-side candidate from the same snapshots,
    regardless of line or edge, because the pre-registered success rule asks
    whether the card's segment gates beat simply taking unders.
    """
    card_rows = history.daily_card
    graded = [row for row in card_rows if row.outcome in ("win", "loss")]
    wins = sum(1 for row in graded if row.outcome == "win")
    losses = len(graded) - wins

    card_file_names = {row.file_name for row in card_rows}
    baseline_rows = [
        row
        for row in history.candidates
        if row.side == "UNDER"
        and row.file_name in card_file_names
        and row.outcome in ("win", "loss")
    ]
    baseline_wins = sum(1 for row in baseline_rows if row.outcome == "win")

    return {
        "card_rows": len(card_rows),
        "graded": len(graded),
        "wins": wins,
        "losses": losses,
        "pushes": sum(1 for row in card_rows if row.outcome == "push"),
        "voids": sum(1 for row in card_rows if row.outcome == "void_no_start"),
        "pending": sum(1 for row in card_rows if row.outcome == "pending"),
        "unresolved": sum(
            1
            for row in card_rows
            if row.outcome not in ("win", "loss", "push", "void_no_start", "pending")
        ),
        "hit_rate": (wins / len(graded)) if graded else None,
        "units_at_minus_110": units_at_minus_110(wins, losses) if graded else None,
        "baseline_rows": len(baseline_rows),
        "baseline_hit_rate": (baseline_wins / len(baseline_rows)) if baseline_rows else None,
        "policy_version": str(
            getattr(history, "daily_card_policy_version", "") or "unknown"
        ),
    }


def _l5_band(candidate: GradedCandidate) -> str:
    if candidate.played_last_5 <= 0:
        return "unknown"
    rate = candidate.hits_last_5 / candidate.played_last_5
    if rate < 0.40:
        return "0-1/5"
    if rate < 0.60:
        return "2/5"
    if rate < 0.80:
        return "3/5"
    return "4-5/5"


def _edge_band(candidate: GradedCandidate) -> str:
    if candidate.projected_strikeouts is None:
        return "unknown"
    edge = (
        candidate.line - candidate.projected_strikeouts
        if candidate.side == "UNDER"
        else candidate.projected_strikeouts - candidate.line
    )
    if edge >= 2.0:
        return "2.0+"
    if edge >= 1.5:
        return "1.50-1.99"
    if edge >= 1.25:
        return "1.25-1.49"
    if edge >= 1.0:
        return "1.00-1.24"
    if edge >= 0.5:
        return "0.50-0.99"
    return "<0.50"


def _reliability_band(candidate: GradedCandidate) -> str:
    return candidate.opportunity_confidence or "unknown"


def segment_summary(rows: list[GradedCandidate], label_fn) -> list[dict]:
    groups: dict[str, list[GradedCandidate]] = defaultdict(list)
    for row in rows:
        groups[label_fn(row)].append(row)
    summaries: list[dict] = []
    for label in sorted(groups):
        group = groups[label]
        graded = [row for row in group if row.outcome in {"win", "loss"}]
        metrics = active_vs_shadow_metrics(group)
        summaries.append(
            {
                "segment": label,
                "n": len(group),
                "graded": len(graded),
                "hit_rate": _hit_rate(group),
                "wins": sum(1 for row in graded if row.outcome == "win"),
                "losses": sum(1 for row in graded if row.outcome == "loss"),
                "pushes": sum(1 for row in group if row.outcome == "push"),
                "voids": sum(1 for row in group if row.outcome in VOID_STATUSES),
                "open": sum(1 for row in group if row.outcome in OPEN_STATUSES),
                "short_outing_rate": (
                    sum(
                        1
                        for row in group
                        if row.actual_outs is not None and row.actual_outs < 15
                    )
                    / len(group)
                    if group
                    else None
                ),
                "active_k_mae": metrics["k"]["active"]["mae"],
                "shadow_k_mae": metrics["k"]["shadow"]["mae"],
                "active_bf_mae": metrics["bf"]["active"]["mae"],
                "shadow_bf_mae": metrics["bf"]["shadow"]["mae"],
            }
        )
    return summaries


def disagreement_analysis(rows: list[GradedCandidate]) -> dict:
    paired = [
        row
        for row in rows
        if row.projected_strikeouts is not None
        and row.recency_shadow_projected_strikeouts is not None
    ]
    side_flips: list[GradedCandidate] = []
    big_k_moves: list[GradedCandidate] = []
    big_confidence_moves: list[GradedCandidate] = []
    edge_diffs: list[float] = []
    shadow_better_k = shadow_worse_k = 0
    for row in paired:
        active_side = "OVER" if row.projected_strikeouts > row.line else "UNDER"
        shadow_side = (
            "OVER"
            if row.recency_shadow_projected_strikeouts > row.line
            else "UNDER"
        )
        if active_side != shadow_side and row.projected_strikeouts != row.recency_shadow_projected_strikeouts:
            side_flips.append(row)
        k_move = row.recency_shadow_projected_strikeouts - row.projected_strikeouts
        if abs(k_move) >= 0.5:
            big_k_moves.append(row)
        edge_diff = (row.recency_shadow_projection_edge or 0.0) - (
            row.line - row.projected_strikeouts
            if row.side == "UNDER"
            else row.projected_strikeouts - row.line
        )
        edge_diffs.append(edge_diff)
        if row.provisional_win_probability is not None and row.recency_shadow_win_probability is not None:
            if abs(row.recency_shadow_win_probability - row.provisional_win_probability) >= 0.02:
                big_confidence_moves.append(row)
        if row.outcome in {"win", "loss"} and row.actual is not None:
            active_err = abs(row.actual - row.projected_strikeouts)
            shadow_err = abs(row.actual - row.recency_shadow_projected_strikeouts)
            if shadow_err < active_err:
                shadow_better_k += 1
            elif shadow_err > active_err:
                shadow_worse_k += 1
    return {
        "paired_n": len(paired),
        "side_flips": len(side_flips),
        "side_flip_rows": [
            {
                "screen_date": row.screen_date.isoformat(),
                "pitcher": row.pitcher_name,
                "line": row.line,
                "side": row.side,
                "active_proj": row.projected_strikeouts,
                "shadow_proj": row.recency_shadow_projected_strikeouts,
                "actual": row.actual,
                "outcome": row.outcome,
            }
            for row in side_flips
        ],
        "big_k_moves": len(big_k_moves),
        "big_confidence_moves": len(big_confidence_moves),
        "avg_edge_diff": _bias(edge_diffs),
        "shadow_k_error_better": shadow_better_k,
        "shadow_k_error_worse": shadow_worse_k,
        "shadow_k_error_tied": len(paired) - shadow_better_k - shadow_worse_k,
    }


def stability_analysis(rows: list[GradedCandidate]) -> dict:
    by_date: dict[date, list[GradedCandidate]] = defaultdict(list)
    for row in rows:
        by_date[row.screen_date].append(row)
    date_summaries: list[dict] = []
    for screen_date in sorted(by_date):
        group = by_date[screen_date]
        metrics = active_vs_shadow_metrics(group)
        shadow_win = shadow_loss = shadow_tie = 0
        for row in group:
            if row.outcome not in {"win", "loss"} or row.actual is None:
                continue
            if row.projected_strikeouts is None or row.recency_shadow_projected_strikeouts is None:
                continue
            active_err = abs(row.actual - row.projected_strikeouts)
            shadow_err = abs(row.actual - row.recency_shadow_projected_strikeouts)
            if shadow_err < active_err:
                shadow_win += 1
            elif shadow_err > active_err:
                shadow_loss += 1
            else:
                shadow_tie += 1
        date_summaries.append(
            {
                "screen_date": screen_date.isoformat(),
                "n": len(group),
                "graded": sum(1 for row in group if row.outcome in {"win", "loss"}),
                "hit_rate": _hit_rate(group),
                "active_k_mae": metrics["k"]["active"]["mae"],
                "shadow_k_mae": metrics["k"]["shadow"]["mae"],
                "active_bf_mae": metrics["bf"]["active"]["mae"],
                "shadow_bf_mae": metrics["bf"]["shadow"]["mae"],
                "shadow_k_error_better": shadow_win,
                "shadow_k_error_worse": shadow_loss,
                "shadow_k_error_tied": shadow_tie,
            }
        )
    distinct_pitchers = {row.subject_id or row.pitcher_name for row in rows}
    pitcher_counts = Counter(row.subject_id or row.pitcher_name for row in rows)
    pitcher_name_by_id = {
        row.subject_id: row.pitcher_name
        for row in rows
        if row.subject_id is not None
    }
    named_pitcher_counts = {
        f"{pitcher_name_by_id.get(key, 'name-unknown')} ({key})": count
        for key, count in pitcher_counts.most_common()
    }
    active_k_mae_all = active_vs_shadow_metrics(rows)["k"]["active"]["mae"]
    shadow_k_mae_all = active_vs_shadow_metrics(rows)["k"]["shadow"]["mae"]
    leave_one_out: list[dict] = []
    for screen_date in sorted(by_date):
        remaining = [row for row in rows if row.screen_date != screen_date]
        metrics = active_vs_shadow_metrics(remaining)
        leave_one_out.append(
            {
                "excluded": screen_date.isoformat(),
                "n": len(remaining),
                "active_k_mae": metrics["k"]["active"]["mae"],
                "shadow_k_mae": metrics["k"]["shadow"]["mae"],
                "shadow_delta": (
                    (metrics["k"]["shadow"]["mae"] or 0.0)
                    - (metrics["k"]["active"]["mae"] or 0.0)
                ),
            }
        )
    return {
        "dates": date_summaries,
        "distinct_pitchers": len(distinct_pitchers),
        "pitcher_counts": named_pitcher_counts,
        "all_active_k_mae": active_k_mae_all,
        "all_shadow_k_mae": shadow_k_mae_all,
        "leave_one_out": leave_one_out,
    }


def bootstrap_by_date(rows: list[GradedCandidate], iterations: int = 2000, seed: int = 7) -> dict:
    by_date: dict[date, list[GradedCandidate]] = defaultdict(list)
    for row in rows:
        by_date[row.screen_date].append(row)
    dates = sorted(by_date)
    if not dates:
        return {}
    rng = random.Random(seed)
    diffs: list[float] = []
    for _ in range(iterations):
        sample_dates = [rng.choice(dates) for _ in dates]
        sample_rows: list[GradedCandidate] = []
        for screen_date in sample_dates:
            sample_rows.extend(by_date[screen_date])
        metrics = active_vs_shadow_metrics(sample_rows)
        active_mae = metrics["k"]["active"]["mae"]
        shadow_mae = metrics["k"]["shadow"]["mae"]
        if active_mae is not None and shadow_mae is not None:
            diffs.append(shadow_mae - active_mae)
    if not diffs:
        return {}
    diffs.sort()
    lower = diffs[int(iterations * 0.025)]
    upper = diffs[int(iterations * 0.975)]
    return {
        "iterations": iterations,
        "dates_resampled": len(dates),
        "median_shadow_minus_active_k_mae": mean(diffs),
        "pct2_5": lower,
        "pct97_5": upper,
        "shadow_worse_pct": sum(1 for value in diffs if value > 0) / len(diffs),
    }


def graded_to_dict(candidate: GradedCandidate) -> dict:
    return {
        "screen_date": candidate.screen_date.isoformat(),
        "file_name": candidate.file_name,
        "subject_id": candidate.subject_id,
        "pitcher_name": candidate.pitcher_name,
        "team": candidate.team,
        "opponent": candidate.opponent,
        "prop_type": candidate.prop_type,
        "side": candidate.side,
        "line": candidate.line,
        "bookmaker": candidate.bookmaker,
        "tier": candidate.tier,
        "qualified": candidate.qualified,
        "projected_outs": candidate.projected_outs,
        "projected_batters_faced": candidate.projected_batters_faced,
        "projected_k_rate": candidate.projected_k_rate,
        "projected_strikeouts": candidate.projected_strikeouts,
        "score": candidate.score,
        "flags": candidate.flags,
        "hits_last_5": candidate.hits_last_5,
        "played_last_5": candidate.played_last_5,
        "opportunity_confidence": candidate.opportunity_confidence,
        "opportunity_flags": candidate.opportunity_flags,
        "shadow_projected_outs": candidate.shadow_projected_outs,
        "shadow_projected_batters_faced": candidate.shadow_projected_batters_faced,
        "shadow_pitch_budget": candidate.shadow_pitch_budget,
        "confidence_percentage": candidate.confidence_percentage,
        "provisional_win_probability": candidate.provisional_win_probability,
        "confidence_label": candidate.confidence_label,
        "recency_shadow_projected_k_rate": candidate.recency_shadow_projected_k_rate,
        "recency_shadow_projected_strikeouts": candidate.recency_shadow_projected_strikeouts,
        "recency_shadow_projected_batters_faced": candidate.recency_shadow_projected_batters_faced,
        "recency_shadow_projected_outs": candidate.recency_shadow_projected_outs,
        "recency_shadow_projection_edge": candidate.recency_shadow_projection_edge,
        "recency_shadow_win_probability": candidate.recency_shadow_win_probability,
        "recency_shadow_confidence_percentage": candidate.recency_shadow_confidence_percentage,
        "outcome": candidate.outcome,
        "actual": candidate.actual,
        "actual_outs": candidate.actual_outs,
        "actual_pitches": candidate.actual_pitches,
        "actual_batters_faced": candidate.actual_batters_faced,
        "actual_walks": candidate.actual_walks,
        "actual_hits": candidate.actual_hits,
        "actual_er": candidate.actual_er,
        "game_pk": candidate.game_pk,
        "resolution_method": candidate.resolution_method,
        "result_edge": candidate.result_edge,
        "resolution_note": candidate.resolution_note,
        "event_id": candidate.event_id,
        "line_source": candidate.line_source,
        "line_collected_at": candidate.line_collected_at,
        "price_shadow": candidate.price_shadow,
    }
