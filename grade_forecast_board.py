"""Grade the published forecast board against official MLB final results.

Reads the forecast/ROI ledgers for a screen date, resolves every proposition
against the free MLB Stats API (schedule finals + boxscores), writes a durable
grade artifact, settles priced PENDING rows in the ROI ledger, and posts a
concise recap to Discord.

Observation/grading only: this never changes model artifacts or the board.

Usage:
  python3 grade_forecast_board.py --date 2026-09-10
  python3 grade_forecast_board.py --date 2026-09-10 --send-discord
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mlb_props.config import OUTPUTS_DIR
from mlb_props.notifiers.discord import send_discord_message
from mlb_props.run_ledger import record_run
from mlb_props.utils import fetch_json
from run_forecast_board import (
    american_payout,
    delivery_already_sent,
    norm_name,
    record_delivery,
)

GRADES_DIR = OUTPUTS_DIR / "grades"
FORECAST_LEDGER = OUTPUTS_DIR / "ledger" / "forecast_ledger.jsonl"
ROI_LEDGER = OUTPUTS_DIR / "ledger" / "picks_roi.jsonl"
STATS_BASE = "https://statsapi.mlb.com/api/v1"
DISCORD_CHUNK_LIMIT = 1900
FAMILY_LABELS = {
    "pitcher_k": "Pitcher Ks",
    "game_ml": "Game ML",
    "game_total": "Totals",
    "game_rl": "Run lines",
}
GRADEABLE_FAMILIES = tuple(FAMILY_LABELS)
VOID_STATES = ("postponed", "cancelled", "canceled", "suspended")

WIN, LOSS, PUSH, VOID, PENDING, ERROR = "WIN", "LOSS", "PUSH", "VOID", "PENDING", "ERROR"
SUPERSEDED = "SUPERSEDED"


# --------------------------------------------------------- pure grading


def match_key(name: str) -> str:
    """Accent- and separator-insensitive key for player-name matching."""
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_name = decomposed.encode("ascii", "ignore").decode("ascii").lower()
    return "".join(ch for ch in ascii_name if ch.isalnum())


def grade_pitcher_k(pick: str, line: float, strikeouts: int) -> str:
    if strikeouts > line:
        over_result = WIN
    elif strikeouts < line:
        over_result = LOSS
    else:
        over_result = PUSH
    if pick == "over":
        return over_result
    if over_result == WIN:
        return LOSS
    if over_result == LOSS:
        return WIN
    return PUSH


def grade_moneyline(pick: str, home_score: int, away_score: int) -> str:
    if home_score == away_score:
        return PUSH
    home_won = home_score > away_score
    if pick == "home":
        return WIN if home_won else LOSS
    return WIN if not home_won else LOSS


def grade_total(pick: str, line: float, total_runs: int) -> str:
    if total_runs > line:
        over_result = WIN
    elif total_runs < line:
        over_result = LOSS
    else:
        over_result = PUSH
    if pick == "over":
        return over_result
    if over_result == WIN:
        return LOSS
    if over_result == LOSS:
        return WIN
    return PUSH


def grade_run_line(pick: str, line: float, home_score: int, away_score: int) -> str:
    margin = home_score - away_score
    home_cover = margin + line
    if home_cover == 0:
        return PUSH
    if pick == "home_covers":
        return WIN if home_cover > 0 else LOSS
    return WIN if home_cover < 0 else LOSS


def settle_units(result: str, price: int | None) -> float | None:
    if result == WIN:
        payout = american_payout(price)
        return payout if payout is not None else None
    if result == LOSS:
        return -1.0
    if result in (PUSH, VOID):
        return 0.0
    return None


def _team_meta(team: dict) -> dict:
    return {
        "id": str(team.get("id")) if team.get("id") is not None else None,
        "name": team.get("name"),
        "abbr": team.get("abbreviation") or team.get("teamCode"),
    }


def parse_standings(payload: dict) -> dict:
    """Pure parse of the MLB standings payload into id/abbr lookups."""
    by_id: dict[str, dict] = {}
    by_abbr: dict[str, dict] = {}
    for record in (payload or {}).get("records", []) or []:
        for team in record.get("teamRecords", []) or []:
            info = team.get("team", {}) or {}
            indicator = team.get("clinchIndicator")
            if indicator in ("y", "z"):
                status = "clinched_division"
            elif indicator == "x":
                status = "clinched_playoff"
            elif indicator == "e":
                status = "eliminated"
            else:
                status = "contending"
            entry = {
                "team_id": str(info.get("id")) if info.get("id") is not None else None,
                "name": info.get("name"),
                "abbr": info.get("abbreviation"),
                "wins": team.get("wins"),
                "losses": team.get("losses"),
                "division_rank": team.get("divisionRank"),
                "wildcard_rank": team.get("wildCardRank"),
                "games_back": team.get("gamesBack"),
                "wc_games_back": team.get("wildCardGamesBack"),
                "elimination_number": team.get("eliminationNumber"),
                "clinch_indicator": indicator,
                "status": status,
            }
            if entry["team_id"]:
                by_id[entry["team_id"]] = entry
            if entry["abbr"]:
                by_abbr[entry["abbr"]] = entry
    return {"by_id": by_id, "by_abbr": by_abbr}


def lookup_team_status(standings: dict | None, team_meta: dict | None) -> dict | None:
    if not standings or not team_meta:
        return None
    by_id = standings.get("by_id") or {}
    by_abbr = standings.get("by_abbr") or {}
    tid = team_meta.get("id")
    if tid is not None and str(tid) in by_id:
        return by_id[str(tid)]
    abbr = team_meta.get("abbr")
    if abbr and abbr in by_abbr:
        return by_abbr[abbr]
    return None


def result_margin(family: str, pick: str, line, home: int, away: int, strikeouts: int | None) -> float | None:
    """Signed distance from the line toward the picked side (positive = winning)."""
    try:
        if family == "pitcher_k":
            if strikeouts is None or line is None:
                return None
            return round(strikeouts - float(line), 2) if pick == "over" else round(float(line) - strikeouts, 2)
        if family == "game_total":
            if line is None:
                return None
            total = home + away
            return round(total - float(line), 2) if pick == "over" else round(float(line) - total, 2)
        if family == "game_ml":
            return float(home - away) if pick == "home" else float(away - home)
        if family == "game_rl":
            if line is None:
                return None
            cover = (home - away) + float(line)
            return round(cover, 2) if pick == "home_covers" else round(-cover, 2)
    except (TypeError, ValueError):
        return None
    return None


def parse_proposition_id(proposition_id: str) -> dict | None:
    parts = str(proposition_id or "").split(":")
    if len(parts) >= 4 and parts[0] == "p":
        try:
            return {"family": "pitcher_k", "game_pk": parts[1], "name_key": parts[2], "line": float(parts[3])}
        except ValueError:
            return None
    if parts and parts[0] == "g" and len(parts) >= 3:
        market = parts[2]
        if market == "ml":
            return {"family": "game_ml", "game_pk": parts[1], "line": None}
        if market in ("total", "spread") and len(parts) >= 4:
            try:
                line = float(parts[3])
            except ValueError:
                return None
            family = "game_total" if market == "total" else "game_rl"
            return {"family": family, "game_pk": parts[1], "line": line}
    return None


# -------------------------------------------------------------- mlb api


class MlbClient:
    def __init__(self, fetch=fetch_json) -> None:
        self._fetch = fetch
        self._schedule_cache: dict[str, dict] = {}
        self._box_cache: dict[str, dict] = {}

    def schedule(self, screen: str) -> dict:
        if screen not in self._schedule_cache:
            self._schedule_cache[screen] = self._fetch(
                f"{STATS_BASE}/schedule?sportId=1&date={screen}"
            )
        return self._schedule_cache[screen]

    def boxscore(self, game_pk: str) -> dict:
        if game_pk not in self._box_cache:
            self._box_cache[game_pk] = self._fetch(f"{STATS_BASE}/game/{game_pk}/boxscore")
        return self._box_cache[game_pk]

    def finals(self, screen: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for day in self.schedule(screen).get("dates", []):
            for game in day.get("games", []):
                status = game.get("status") or {}
                home = (game.get("teams") or {}).get("home") or {}
                away = (game.get("teams") or {}).get("away") or {}
                out[str(game.get("gamePk"))] = {
                    "final": status.get("abstractGameState") == "Final",
                    "state": status.get("detailedState") or status.get("abstractGameState") or "",
                    "home_score": home.get("score"),
                    "away_score": away.get("score"),
                    "home_team": _team_meta(home.get("team") or {}),
                    "away_team": _team_meta(away.get("team") or {}),
                }
        return out

    def standings(self, screen: str) -> dict:
        """Historical team situation for the screen date (one API call)."""
        payload = self._fetch(
            f"{STATS_BASE}/standings?leagueId=103,104&season=2026"
            f"&date={screen}&standingsTypes=regularSeason&hydrate=team"
        )
        return parse_standings(payload)

    def pitcher_lookup(self, game_pk: str, subject_id: int | None, name_key: str | None) -> dict:
        box = self.boxscore(game_pk)
        match = None
        match_side = None
        for side in ("home", "away"):
            for player in ((box.get("teams") or {}).get(side) or {}).get("players", {}).values():
                person = player.get("person") or {}
                if subject_id is not None and person.get("id") == subject_id:
                    match = player
                    match_side = side
                    break
                if name_key and match_key(person.get("fullName") or "") == match_key(name_key):
                    match = player
                    match_side = side
                    break
            if match is not None:
                break
        if match is None:
            return {"found": False, "appeared": False, "strikeouts": None, "side": None}
        pitching = (match.get("stats") or {}).get("pitching") or {}
        appeared = bool(pitching)
        return {
            "found": True,
            "appeared": appeared,
            "strikeouts": int(pitching.get("strikeOuts") or 0) if appeared else None,
            "outs": pitching.get("outs"),
            "batters_faced": pitching.get("battersFaced"),
            "pitches": pitching.get("numberOfPitches"),
            "hits": pitching.get("hits"),
            "walks": pitching.get("baseOnBalls"),
            "earned_runs": pitching.get("earnedRuns"),
            "side": match_side,
        }


# ----------------------------------------------------------- ledger io

BOARD_DIR = OUTPUTS_DIR / "forecast_boards"
# Prediction fields the grader enriches rows with; older ledger rows predate
# them, so the board artifact is used as a read-only fallback.
CONTEXT_FIELDS = (
    "market_p",
    "ev",
    "ev_flag",
    "projected_strikeouts",
    "projected_outs",
    "projected_batters_faced",
    "projected_k_rate",
    "opportunity_confidence",
)


def load_board_context(screen: str) -> dict[tuple[str, str], dict]:
    """Latest board row per (run_id, proposition_id) for a screen date."""
    context: dict[tuple[str, str], dict] = {}
    if not BOARD_DIR.exists():
        return context
    for path in sorted(BOARD_DIR.glob(f"forecast_board_{screen}*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if payload.get("screen_date") != screen:
            continue
        run_id = str(payload.get("run_id"))
        is_slot = str(run_id).endswith(("-noon", "-afternoon"))
        for section in (payload.get("sections") or {}).values():
            for row in section:
                key = (run_id, str(row.get("proposition_id")))
                # Prefer a real slot board over a legacy one when both exist.
                if key in context and not is_slot:
                    continue
                context[key] = row
    return context


def enrich_context(row: dict, context: dict[tuple[str, str], dict]) -> dict:
    board_row = context.get((str(row.get("run_id")), str(row.get("proposition_id"))))
    if not board_row:
        return row
    for field in CONTEXT_FIELDS:
        if row.get(field) is None and board_row.get(field) is not None:
            row[field] = board_row.get(field)
    return row


def load_screen_rows(screen: str) -> list[dict]:
    forecast: dict[tuple[str, str], dict] = {}
    if FORECAST_LEDGER.exists():
        for line in FORECAST_LEDGER.read_text().splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("screen_date") != screen:
                continue
            forecast[(str(record.get("run_id")), str(record.get("proposition_id")))] = record

    roi: dict[tuple[str, str], tuple[int, dict]] = {}
    if ROI_LEDGER.exists():
        for index, line in enumerate(ROI_LEDGER.read_text().splitlines()):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if record.get("screen_date") != screen:
                continue
            roi[(str(record.get("run_id")), str(record.get("proposition_id")))] = (index, record)

    rows: list[dict] = []
    for key, forecast_row in forecast.items():
        index, roi_row = roi.get(key, (None, {}))
        rows.append(
            {
                **forecast_row,
                "price": roi_row.get("price", forecast_row.get("price")),
                "outcome": roi_row.get("outcome"),
                "graded": roi_row.get("graded"),
                "_roi_index": index,
            }
        )
    for key, (index, roi_row) in roi.items():
        if key not in forecast:
            rows.append({**roi_row, "_roi_index": index})

    context = load_board_context(screen)
    if context:
        for row in rows:
            enrich_context(row, context)
    return rows


def settle_roi_ledger(path: Path, settlements: list[dict]) -> int:
    if not path.exists():
        return 0
    by_index = {
        int(item["_roi_index"]): item
        for item in settlements
        if item.get("_roi_index") is not None
    }
    lines = path.read_text().splitlines()
    changed = 0
    output: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for index, line in enumerate(lines):
        settlement = by_index.get(index)
        if settlement is None or not line.strip():
            output.append(line)
            continue
        record = json.loads(line)
        if record.get("outcome") == "PENDING" and settlement["result"] in (WIN, LOSS, PUSH):
            record["outcome"] = settlement["result"]
            record["units"] = settlement["units"]
            record["graded"] = True
            record["graded_at"] = now
            changed += 1
            output.append(json.dumps(record, default=str))
        else:
            output.append(line)
    path.write_text("\n".join(output) + ("\n" if output else ""))
    return changed


def mark_superseded_rows(path: Path, superseded: list[dict]) -> int:
    """Mark older duplicate ROI rows replaced by a later pregame snapshot.

    Preserves the original outcome/units for audit and excludes the row from
    canonical ROI by setting outcome=SUPERSEDED with units 0.0.
    """
    if not path.exists():
        return 0
    by_index: dict[int, dict] = {}
    for item in superseded or []:
        if item.get("_roi_index") is None:
            continue
        try:
            by_index[int(item["_roi_index"])] = item
        except (TypeError, ValueError):
            continue
    if not by_index:
        return 0
    lines = path.read_text().splitlines()
    changed = 0
    output: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    for index, line in enumerate(lines):
        marker = by_index.get(index)
        if marker is None or not line.strip():
            output.append(line)
            continue
        try:
            record = json.loads(line)
        except ValueError:
            output.append(line)
            continue
        if record.get("outcome") == SUPERSEDED:
            output.append(line)
            continue
        if "original_outcome" not in record:
            record["original_outcome"] = record.get("outcome")
        if "original_units" not in record:
            record["original_units"] = record.get("units")
        record["outcome"] = SUPERSEDED
        record["units"] = 0.0
        record["graded"] = True
        record["superseded_by_run_id"] = marker.get("superseded_by_run_id")
        record["superseded_by_proposition_id"] = marker.get("superseded_by_proposition_id")
        record["superseded_at"] = now
        changed += 1
        output.append(json.dumps(record, default=str))
    path.write_text("\n".join(output) + ("\n" if output else ""))
    return changed


# -------------------------------------------------------------- grading


def _resolve_meta(row: dict) -> dict:
    parsed = parse_proposition_id(row.get("proposition_id") or "")
    meta = dict(parsed or {})
    for field in ("family", "game_pk", "line"):
        if row.get(field) not in (None, ""):
            meta[field] = row[field]
    if "name_key" not in meta:
        subject = row.get("subject")
        if row.get("family") == "pitcher_k" and subject:
            meta["name_key"] = norm_name(subject)
    return meta


def canonical_market_key(row: dict) -> str:
    """Logical market identity, excluding line/price so moves revise same market."""
    meta = _resolve_meta(row)
    family = meta.get("family") or row.get("family") or "unknown"
    game_pk = str(meta.get("game_pk") or row.get("game_pk") or "")
    if family == "pitcher_k":
        sid = row.get("subject_id")
        if sid is not None:
            pid = f"sid:{sid}"
        else:
            nk = meta.get("name_key") or match_key(str(row.get("subject") or ""))
            pid = f"name:{nk}"
        if not game_pk:
            return f"pitcher_k:{row.get('proposition_id')}"
        return f"pitcher_k:{game_pk}:{pid}"
    if not game_pk:
        return f"{family}:{row.get('proposition_id')}"
    return f"{family}:{game_pk}"


def parse_row_time(row: dict) -> datetime | None:
    for field in ("captured_at", "exported_at"):
        value = row.get(field)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def run_rank_value(run_id: str | None) -> int:
    text = str(run_id or "")
    if text.endswith("-afternoon"):
        return 2
    if text.endswith("-noon"):
        return 1
    return 0


def start_times_from_schedule(schedule_payload: dict) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for day in (schedule_payload or {}).get("dates", []) or []:
        for game in day.get("games", []) or []:
            pk = game.get("gamePk")
            if pk is None:
                continue
            raw = game.get("gameDate")
            if not raw:
                continue
            try:
                parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            out[str(pk)] = parsed.astimezone(timezone.utc)
    return out


def select_canonical_rows(
    rows: list[dict],
    start_by_pk: dict[str, datetime] | None = None,
) -> tuple[dict[str, dict], list[dict], dict]:
    """Select latest valid pregame row per logical market.

    Returns (selected_by_key, superseded_rows, stats). Superseded rows are
    older duplicates replaced by a later pregame snapshot.
    """
    start_by_pk = start_by_pk or {}
    by_key: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_key[canonical_market_key(row)].append(row)

    selected: dict[str, dict] = {}
    superseded: list[dict] = []
    replaced_by_afternoon = 0
    noon_only_fallback = 0
    for key, candidates in by_key.items():
        # Partition pregame-valid vs post-start captures.
        valid: list[dict] = []
        for cand in candidates:
            meta = _resolve_meta(cand)
            game_pk = str(meta.get("game_pk") or cand.get("game_pk") or "")
            start = start_by_pk.get(game_pk)
            moment = parse_row_time(cand)
            if start is not None and moment is not None and moment >= start:
                continue
            valid.append(cand)
        pool = valid if valid else list(candidates)

        def sort_key(cand: dict) -> tuple:
            moment = parse_row_time(cand)
            # Unknown time sorts before any known time; revision order breaks ties.
            has_time = 1 if moment is not None else 0
            moment_value = moment if moment is not None else datetime.min.replace(tzinfo=timezone.utc)
            roi_index = cand.get("_roi_index")
            try:
                roi_value = int(roi_index) if roi_index is not None else -1
            except (TypeError, ValueError):
                roi_value = -1
            return (has_time, moment_value, run_rank_value(cand.get("run_id")), roi_value)

        ordered = sorted(pool, key=sort_key)
        winner = ordered[-1]
        selected[key] = winner
        # Stats: did afternoon replace noon, or did noon survive alone?
        run_ids = {str(c.get("run_id")) for c in candidates}
        winner_run = str(winner.get("run_id"))
        has_noon = any(r.endswith("-noon") for r in run_ids)
        has_afternoon = any(r.endswith("-afternoon") for r in run_ids)
        if has_noon and has_afternoon and winner_run.endswith("-afternoon"):
            replaced_by_afternoon += 1
        if has_noon and not has_afternoon:
            noon_only_fallback += 1
        for cand in candidates:
            if cand is winner:
                continue
            # Only mark rows in the valid pool as superseded; post-start
            # captures excluded from selection are not ledger duplicates.
            if cand not in pool:
                continue
            superseded.append(
                {
                    "_roi_index": cand.get("_roi_index"),
                    "run_id": cand.get("run_id"),
                    "proposition_id": cand.get("proposition_id"),
                    "superseded_by_run_id": winner.get("run_id"),
                    "superseded_by_proposition_id": winner.get("proposition_id"),
                    "canonical_key": key,
                }
            )

    stats = {
        "unique_markets": len(by_key),
        "replaced_by_afternoon": replaced_by_afternoon,
        "noon_only_fallback": noon_only_fallback,
        "superseded_rows": len(superseded),
    }
    return selected, superseded, stats


def grade_row(
    row: dict,
    finals: dict[str, dict],
    client: MlbClient,
    standings: dict | None = None,
) -> dict:
    meta = _resolve_meta(row)
    graded = {
        "run_id": row.get("run_id"),
        "family": meta.get("family") or row.get("family"),
        "proposition_id": row.get("proposition_id"),
        "pick": row.get("pick"),
        "line": meta.get("line", row.get("line")),
        "price": row.get("price"),
        "p_pick": row.get("p_pick"),
        "market_p": row.get("market_p"),
        "ev": row.get("ev"),
        "ev_flag": row.get("ev_flag"),
        "subject": row.get("subject"),
        "game_pk": meta.get("game_pk"),
        "_roi_index": row.get("_roi_index"),
    }
    game_pk = str(meta.get("game_pk") or "")
    info = finals.get(game_pk)
    if info is None:
        graded.update({"result": PENDING, "units": None, "detail": "game not found"})
        return graded
    state = (info.get("state") or "").lower()
    if any(token in state for token in VOID_STATES):
        graded.update({"result": VOID, "units": 0.0, "detail": info.get("state")})
        return graded
    if not info.get("final") or info.get("home_score") is None or info.get("away_score") is None:
        graded.update({"result": PENDING, "units": None, "detail": info.get("state") or "not final"})
        return graded

    home, away = int(info["home_score"]), int(info["away_score"])
    family = meta.get("family")
    strikeouts = None
    if family == "pitcher_k":
        lookup = client.pitcher_lookup(game_pk, row.get("subject_id"), meta.get("name_key"))
        if not lookup["found"]:
            graded.update({"result": VOID, "units": 0.0, "detail": "pitcher not in boxscore"})
            return graded
        if not lookup["appeared"]:
            graded.update({"result": VOID, "units": 0.0, "detail": "pitcher did not appear"})
            return graded
        line = float(meta.get("line"))
        strikeouts = lookup["strikeouts"]
        result = grade_pitcher_k(row.get("pick"), line, strikeouts)
        graded.update({"result": result, "units": None, "detail": f"{strikeouts} K vs {line}"})
        graded["actual"] = {
            "strikeouts": lookup.get("strikeouts"),
            "outs": lookup.get("outs"),
            "batters_faced": lookup.get("batters_faced"),
            "pitches": lookup.get("pitches"),
            "hits": lookup.get("hits"),
            "walks": lookup.get("walks"),
            "earned_runs": lookup.get("earned_runs"),
        }
        graded["projected_strikeouts"] = row.get("projected_strikeouts")
        graded["projected_outs"] = row.get("projected_outs")
        graded["projected_batters_faced"] = row.get("projected_batters_faced")
        graded["projected_k_rate"] = row.get("projected_k_rate")
        graded["opportunity_confidence"] = row.get("opportunity_confidence")
        side = lookup.get("side")
        team_meta = info.get("home_team") if side == "home" else info.get("away_team")
        opp_meta = info.get("away_team") if side == "home" else info.get("home_team")
        graded["team"] = (team_meta or {}).get("name")
        graded["opponent"] = (opp_meta or {}).get("name")
        graded["team_status"] = lookup_team_status(standings, team_meta)
        graded["opp_status"] = lookup_team_status(standings, opp_meta)
    elif family == "game_ml":
        result = grade_moneyline(row.get("pick"), home, away)
        graded.update({"result": result, "units": None, "detail": f"{away}-{home}"})
    elif family == "game_total":
        line = float(meta.get("line"))
        result = grade_total(row.get("pick"), line, home + away)
        graded.update({"result": result, "units": None, "detail": f"{home + away} runs vs {line}"})
    elif family == "game_rl":
        line = float(meta.get("line"))
        result = grade_run_line(row.get("pick"), line, home, away)
        graded.update({"result": result, "units": None, "detail": f"margin {home - away} vs {line}"})
    else:
        graded.update({"result": ERROR, "units": None, "detail": "unknown family"})
        return graded

    if family and family.startswith("game"):
        graded["home_team"] = (info.get("home_team") or {}).get("name")
        graded["away_team"] = (info.get("away_team") or {}).get("name")
        graded["home_status"] = lookup_team_status(standings, info.get("home_team"))
        graded["away_status"] = lookup_team_status(standings, info.get("away_team"))

    graded["result_margin"] = result_margin(family, row.get("pick"), meta.get("line"), home, away, strikeouts)
    graded["units"] = settle_units(graded["result"], row.get("price"))
    return graded


def summarize(rows: list[dict]) -> dict:
    families: dict[str, dict] = defaultdict(lambda: {"w": 0, "l": 0, "p": 0, "void": 0, "pending": 0, "error": 0})
    for row in rows:
        bucket = families[row["family"]]
        result = row["result"]
        if result == WIN:
            bucket["w"] += 1
        elif result == LOSS:
            bucket["l"] += 1
        elif result == PUSH:
            bucket["p"] += 1
        elif result == VOID:
            bucket["void"] += 1
        elif result == ERROR:
            bucket["error"] += 1
        else:
            bucket["pending"] += 1
    totals = {"w": 0, "l": 0, "p": 0, "void": 0, "pending": 0, "error": 0}
    for bucket in families.values():
        for key in totals:
            totals[key] += bucket[key]
    decided = totals["w"] + totals["l"]
    totals["hit_rate"] = round(totals["w"] / decided, 4) if decided else None
    return {"families": dict(families), "totals": totals}


def roi_summary(rows: list[dict]) -> dict:
    priced = [r for r in rows if r.get("price") is not None and r["result"] in (WIN, LOSS, PUSH)]
    units = sum(r["units"] or 0.0 for r in priced)
    return {
        "plays": len(priced),
        "units": round(units, 3),
        "roi": round(units / len(priced), 4) if priced else None,
    }


def latest_run_id(run_ids: list[str]) -> str | None:
    if not run_ids:
        return None

    def rank(run_id: str) -> int:
        if run_id.endswith("-afternoon"):
            return 2
        if run_id.endswith("-noon"):
            return 1
        return 0

    return max(run_ids, key=rank)


def slot_label(run_id: str) -> str:
    if run_id.endswith("-afternoon"):
        return "afternoon"
    if run_id.endswith("-noon"):
        return "noon"
    return "legacy"


def grade_screen(screen: str, client: MlbClient | None = None) -> dict:
    client = client or MlbClient()
    api_error = False
    try:
        finals = client.finals(screen)
    except Exception as exc:  # noqa: BLE001 - surfaced as incomplete grading
        finals = {}
        api_error = True
        print(f"ERROR: schedule fetch failed: {type(exc).__name__}: {exc}")

    standings = None
    try:
        if hasattr(client, "standings"):
            standings = client.standings(screen)
    except Exception as exc:  # noqa: BLE001 - context is optional, never blocks grading
        standings = None
        print(f"WARN: standings fetch failed: {type(exc).__name__}: {exc}")

    rows = load_screen_rows(screen)
    # Game start times bound canonical selection to pregame snapshots.
    start_by_pk: dict[str, datetime] = {}
    try:
        schedule = client.schedule(screen) if hasattr(client, "schedule") else {}
        start_by_pk = start_times_from_schedule(schedule or {})
    except Exception:  # noqa: BLE001 - selection falls back to revision order
        start_by_pk = {}
    selected_by_key, superseded_markers, selection_stats = select_canonical_rows(rows, start_by_pk)

    by_run: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_run[str(row.get("run_id"))].append(row)

    revisions: dict[str, dict] = {}
    all_graded_by_id: dict[tuple, dict] = {}
    for run_id, run_rows in sorted(by_run.items()):
        graded_rows = []
        for row in run_rows:
            family = _resolve_meta(row).get("family") or row.get("family")
            if family not in GRADEABLE_FAMILIES:
                continue
            try:
                graded = grade_row(row, finals, client, standings)
            except Exception as exc:  # noqa: BLE001 - one bad row must not kill the run
                api_error = True
                graded = {
                    "run_id": run_id,
                    "family": _resolve_meta(row).get("family") or row.get("family"),
                    "proposition_id": row.get("proposition_id"),
                    "pick": row.get("pick"),
                    "line": row.get("line"),
                    "price": row.get("price"),
                    "p_pick": row.get("p_pick"),
                    "subject": row.get("subject"),
                    "game_pk": row.get("game_pk"),
                    "result": ERROR,
                    "units": None,
                    "_roi_index": row.get("_roi_index"),
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            graded_rows.append(graded)
            key = (str(graded.get("run_id")), str(graded.get("proposition_id")), graded.get("_roi_index"))
            all_graded_by_id[key] = graded
        revisions[run_id] = {
            "slot": slot_label(run_id),
            "summary": summarize(graded_rows),
            "roi": roi_summary(graded_rows),
            "rows": graded_rows,
        }

    # Canonical consolidated board: one graded row per logical market.
    canonical_rows: list[dict] = []
    for key, selected_row in selected_by_key.items():
        family = _resolve_meta(selected_row).get("family") or selected_row.get("family")
        if family not in GRADEABLE_FAMILIES:
            continue
        lookup = (
            str(selected_row.get("run_id")),
            str(selected_row.get("proposition_id")),
            selected_row.get("_roi_index"),
        )
        graded = all_graded_by_id.get(lookup)
        if graded is None:
            try:
                graded = grade_row(selected_row, finals, client, standings)
            except Exception as exc:  # noqa: BLE001 - keep canonical robust
                api_error = True
                graded = {
                    "run_id": selected_row.get("run_id"),
                    "family": family,
                    "proposition_id": selected_row.get("proposition_id"),
                    "pick": selected_row.get("pick"),
                    "line": selected_row.get("line"),
                    "price": selected_row.get("price"),
                    "p_pick": selected_row.get("p_pick"),
                    "subject": selected_row.get("subject"),
                    "game_pk": selected_row.get("game_pk"),
                    "result": ERROR,
                    "units": None,
                    "_roi_index": selected_row.get("_roi_index"),
                    "detail": f"{type(exc).__name__}: {exc}",
                }
        canonical_rows.append({**graded, "canonical_key": key})
    canonical_summary = summarize(canonical_rows) if canonical_rows else None
    canonical_roi = roi_summary(canonical_rows) if canonical_rows else None
    settlements = [r for r in canonical_rows if r.get("_roi_index") is not None]
    learning = build_learning_review(canonical_rows) if canonical_rows else None

    latest = latest_run_id(list(revisions.keys()))
    pending_total = sum(r["summary"]["totals"]["pending"] for r in revisions.values())
    canonical_pending = int((canonical_summary or {}).get("totals", {}).get("pending", 0) or 0)
    result = {
        "screen_date": screen,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "api_error": api_error,
        "latest_run_id": latest,
        "latest_summary": revisions[latest]["summary"] if latest else None,
        "latest_roi": revisions[latest]["roi"] if latest else None,
        "pending_total": pending_total,
        "revisions": revisions,
        "settlements": settlements,
        "canonical": {
            "summary": canonical_summary,
            "roi": canonical_roi,
            "rows": canonical_rows,
            "selection": selection_stats,
            "pending": canonical_pending,
        },
        "learning": learning,
        "superseded": superseded_markers,
    }
    return result


# -------------------------------------------------------- learning review


def _bucket_stats(rows: list[dict]) -> dict:
    decided = [r for r in rows if r.get("result") in (WIN, LOSS)]
    wins = sum(r["result"] == WIN for r in decided)
    priced = [r for r in decided if r.get("units") is not None]
    units = sum(r.get("units") or 0.0 for r in priced)
    return {
        "n": len(decided),
        "wins": wins,
        "losses": len(decided) - wins,
        "hit": round(wins / len(decided), 4) if decided else None,
        "units": round(units, 3),
    }


def _group_table(rows: list[dict], key_fn) -> dict:
    groups: dict = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    return {key: _bucket_stats(group) for key, group in sorted(groups.items(), key=lambda kv: str(kv[0]))}


def pitcher_bucket(row: dict) -> str:
    """Attribute a pitcher K result to workload vs strikeout-rate conversion."""
    actual = row.get("actual") or {}
    actual_k = actual.get("strikeouts")
    actual_bf = actual.get("batters_faced")
    proj_bf = row.get("projected_batters_faced")
    proj_k = row.get("projected_strikeouts")
    proj_k_rate = row.get("projected_k_rate")
    if None in (actual_k, actual_bf, proj_bf, proj_k):
        return "no_data"
    if proj_k_rate is None:
        proj_k_rate = proj_k / proj_bf if proj_bf else None
    if proj_k_rate is None:
        return "no_data"
    expected_at_actual_bf = proj_k_rate * actual_bf
    opportunity = expected_at_actual_bf - proj_k
    conversion = actual_k - expected_at_actual_bf
    sign = 1.0 if row.get("pick") == "over" else -1.0
    opportunity *= sign
    conversion *= sign
    if opportunity <= -1.0 and opportunity <= conversion:
        return "workload_short"
    if conversion <= -1.0 and conversion < opportunity:
        return "conversion_cold"
    if opportunity >= 1.0 and opportunity >= conversion:
        return "workload_long"
    if conversion >= 1.0 and conversion > opportunity:
        return "conversion_hot"
    return "near_line"


def _price_band(price) -> str:
    if price is None:
        return "na"
    if price < -150:
        return "<-150"
    if price < -110:
        return "-150..-110"
    if price <= 100:
        return "-110..+100"
    if price <= 150:
        return "+100..+150"
    return ">+150"


def _gap_band(gap) -> str:
    if gap is None:
        return "na"
    if gap < -0.05:
        return "<-5pt"
    if gap < 0.0:
        return "-5..0pt"
    if gap < 0.05:
        return "0..5pt"
    if gap < 0.10:
        return "5..10pt"
    return ">10pt"


def build_learning_review(rows: list[dict]) -> dict:
    """Descriptive win/loss breakdowns over one date's canonical graded rows."""
    decided = [r for r in rows if r.get("result") in (WIN, LOSS)]
    pitcher = [r for r in decided if r.get("family") == "pitcher_k"]
    groups = {
        "overall": {"all": _bucket_stats(decided)},
        "by_family": _group_table(decided, lambda r: r.get("family")),
        "by_side": _group_table(decided, lambda r: f"{r.get('family')}:{r.get('pick')}"),
        "by_ev_flag": _group_table(decided, lambda r: r.get("ev_flag") or "na"),
        "by_price_band": _group_table(decided, lambda r: _price_band(r.get("price"))),
        "by_gap_band": _group_table(
            [r for r in decided if r.get("market_p") is not None and r.get("p_pick") is not None],
            lambda r: _gap_band(r["p_pick"] - r["market_p"]),
        ),
        "pitcher_by_bucket": _group_table(pitcher, pitcher_bucket),
        "pitcher_by_opportunity": _group_table(pitcher, lambda r: r.get("opportunity_confidence") or "na"),
        "by_team_status": _group_table(
            [r for r in decided if r.get("team_status")],
            lambda r: (r.get("team_status") or {}).get("status") or "na",
        ),
        "by_opp_status": _group_table(
            [r for r in decided if r.get("opp_status")],
            lambda r: (r.get("opp_status") or {}).get("status") or "na",
        ),
    }
    return {"decided": len(decided), "groups": groups, "markdown": render_learning_markdown(decided, groups)}


LEARNING_SECTIONS = (
    ("Overall", "overall"),
    ("By family", "by_family"),
    ("By side", "by_side"),
    ("By EV flag", "by_ev_flag"),
    ("By price band", "by_price_band"),
    ("By model-market gap", "by_gap_band"),
    ("Pitcher by workload/conversion", "pitcher_by_bucket"),
    ("Pitcher by opportunity confidence", "pitcher_by_opportunity"),
    ("By pitcher team situation", "by_team_status"),
    ("By opponent situation", "by_opp_status"),
)


def render_learning_markdown(decided: list[dict], groups: dict) -> str:
    lines = ["## Daily learning review", ""]
    lines.append(f"Decided plays: {len(decided)}")
    lines.append("")
    for label, key in LEARNING_SECTIONS:
        table = groups.get(key) or {}
        if not table:
            continue
        lines.append(f"### {label}")
        lines.append("| bucket | n | W-L | hit | units |")
        lines.append("|---|---:|---:|---:|---:|")
        for bucket, stats in table.items():
            hit = f"{stats['hit']:.0%}" if stats["hit"] is not None else "-"
            lines.append(f"| {bucket} | {stats['n']} | {stats['wins']}-{stats['losses']} | {hit} | {stats['units']:+.2f} |")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------- reporting


def _record_phrase(totals: dict) -> str:
    base = f"{totals['w']}-{totals['l']}"
    if totals["p"]:
        base += f"-{totals['p']}"
    rate = totals.get("hit_rate")
    if rate is not None:
        base += f" ({rate * 100:.1f}%)"
    return base


def render_recap(result: dict) -> str:
    screen = result["screen_date"]
    latest = result.get("latest_run_id")
    lines = [f"MLB Board Recap - {screen}"]
    canonical = result.get("canonical") or {}
    canonical_summary = canonical.get("summary")
    if canonical_summary:
        totals = canonical_summary["totals"]
        lines.append(f"Canonical board: {_record_phrase(totals)}")
        for family, bucket in canonical_summary["families"].items():
            if bucket["w"] + bucket["l"] + bucket["p"] == 0:
                continue
            fam = f"{bucket['w']}-{bucket['l']}"
            if bucket["p"]:
                fam += f"-{bucket['p']}"
            lines.append(f"  {FAMILY_LABELS.get(family, family)}: {fam}")
        roi = canonical.get("roi") or {}
        if roi.get("plays"):
            sign = "+" if (roi.get("units") or 0) >= 0 else ""
            pct = f" ({roi['roi'] * 100:+.1f}%)" if roi.get("roi") is not None else ""
            lines.append(f"Priced ROI: {sign}{roi['units']:.2f}u across {roi['plays']} plays{pct}")
        selection = canonical.get("selection") or {}
        if selection.get("unique_markets"):
            lines.append(
                f"Canonical board: {selection.get('unique_markets')} unique markets "
                f"(afternoon replaced noon: {selection.get('replaced_by_afternoon', 0)}, "
                f"noon-only: {selection.get('noon_only_fallback', 0)})"
            )
        if not latest:
            if canonical.get("pending"):
                lines.append(f"Pending: {canonical['pending']} props not yet final")
            return "\n".join(lines)
        # Revision audit line preserves noon/afternoon visibility.
        slot_runs = [rid for rid in result.get("revisions", {}) if slot_label(rid) in ("noon", "afternoon")]
        if len(slot_runs) > 1:
            parts = []
            for rid in sorted(slot_runs, key=lambda r: 0 if r.endswith("-noon") else 1):
                rev = result["revisions"][rid]
                parts.append(f"{rev['slot'].title()}: {_record_phrase(rev['summary']['totals'])}")
            lines.append(" | ".join(parts))
        if canonical.get("pending"):
            lines.append(f"Pending: {canonical['pending']} props not yet final")
        elif result.get("pending_total"):
            lines.append(f"Pending: {result['pending_total']} props not yet final")
        return "\n".join(lines)

    if not latest:
        lines.append("No graded board rows found for this date.")
        return "\n".join(lines)

    latest_rev = result["revisions"][latest]
    label = latest_rev["slot"]
    totals = latest_rev["summary"]["totals"]
    lines.append(f"Latest board ({label}): {_record_phrase(totals)}")
    for family, bucket in latest_rev["summary"]["families"].items():
        if bucket["w"] + bucket["l"] + bucket["p"] == 0:
            continue
        fam = f"{bucket['w']}-{bucket['l']}"
        if bucket["p"]:
            fam += f"-{bucket['p']}"
        lines.append(f"  {FAMILY_LABELS.get(family, family)}: {fam}")

    roi = latest_rev["roi"]
    if roi["plays"]:
        sign = "+" if roi["units"] >= 0 else ""
        pct = f" ({roi['roi'] * 100:+.1f}%)" if roi["roi"] is not None else ""
        lines.append(f"Priced ROI: {sign}{roi['units']:.2f}u across {roi['plays']} plays{pct}")

    slot_runs = [rid for rid in result["revisions"] if slot_label(rid) in ("noon", "afternoon")]
    if len(slot_runs) > 1:
        parts = []
        for rid in sorted(slot_runs, key=lambda r: 0 if r.endswith("-noon") else 1):
            rev = result["revisions"][rid]
            parts.append(f"{rev['slot'].title()}: {_record_phrase(rev['summary']['totals'])}")
        lines.append(" | ".join(parts))

    if result["pending_total"]:
        lines.append(f"Pending: {result['pending_total']} props not yet final")
    return "\n".join(lines)


def chunk_messages(text: str, limit: int = DISCORD_CHUNK_LIMIT) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    for line in text.splitlines(keepends=True):
        if used + len(line) > limit and current:
            chunks.append("".join(current))
            current = []
            used = 0
        current.append(line)
        used += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


# ---------------------------------------------------------------- main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade the forecast board for a date.")
    parser.add_argument("--date", default=None, help="Screen date YYYY-MM-DD; defaults to yesterday (UTC).")
    parser.add_argument("--send-discord", action="store_true")
    parser.add_argument("--force-send", action="store_true")
    parser.add_argument("--webhook-url", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    screen = args.date or (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

    result = grade_screen(screen)
    GRADES_DIR.mkdir(parents=True, exist_ok=True)
    (GRADES_DIR / f"forecast_board_{screen}.json").write_text(
        json.dumps(result, indent=1, default=str)
    )
    learning = result.get("learning")
    if learning:
        (GRADES_DIR / f"learning_review_{screen}.json").write_text(
            json.dumps(learning, indent=1, default=str)
        )
        (GRADES_DIR / f"learning_review_{screen}.md").write_text(learning["markdown"])
    changed = settle_roi_ledger(ROI_LEDGER, result["settlements"])
    changed += mark_superseded_rows(ROI_LEDGER, result.get("superseded") or [])

    recap = render_recap(result)
    print(recap)
    print(f"\ngraded {screen}: ledger updates {changed}, api_error={result['api_error']}")
    if learning:
        print(learning["markdown"])

    if result["api_error"]:
        record_run(
            outcome="failed",
            task="forecast_board_grade",
            message="grading incomplete: MLB API error",
            screen_date=screen,
        )
        return 1

    delivery = {"sent": 0, "failed": 0, "status": "dry_run"}
    if args.send_discord:
        webhook = args.webhook_url or os.environ.get("FORECAST_BOARD_DISCORD_WEBHOOK_URL", "")
        if not webhook:
            print("ERROR: --send-discord requires FORECAST_BOARD_DISCORD_WEBHOOK_URL")
            record_run(
                outcome="failed",
                task="forecast_board_grade",
                message="missing webhook",
                screen_date=screen,
            )
            return 2
        if delivery_already_sent(screen, "grade") and not args.force_send:
            print(f"ERROR: {screen} recap already delivered; refusing to repost without --force-send")
            record_run(
                outcome="failed",
                task="forecast_board_grade",
                message="recap already delivered",
                screen_date=screen,
            )
            return 3
        for chunk in chunk_messages(recap):
            send = send_discord_message(webhook, chunk)
            if send.ok:
                delivery["sent"] += 1
            else:
                delivery["failed"] += 1
                print(f"discord chunk failed: {send.error or send.status_code}")
        delivery["status"] = "sent" if delivery["failed"] == 0 else "partial"
        record_delivery(screen, "grade", f"grade-{screen}", delivery["status"], delivery["sent"])

    record_run(
        outcome="success" if delivery["failed"] == 0 else "failed",
        task="forecast_board_grade",
        message=f"ledger updates {changed}, delivery {delivery['status']}",
        screen_date=screen,
    )
    return 0 if delivery["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
