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
                }
        return out

    def pitcher_lookup(self, game_pk: str, subject_id: int | None, name_key: str | None) -> dict:
        box = self.boxscore(game_pk)
        match = None
        for side in ("home", "away"):
            for player in ((box.get("teams") or {}).get(side) or {}).get("players", {}).values():
                person = player.get("person") or {}
                if subject_id is not None and person.get("id") == subject_id:
                    match = player
                    break
                if name_key and match_key(person.get("fullName") or "") == match_key(name_key):
                    match = player
                    break
            if match is not None:
                break
        if match is None:
            return {"found": False, "appeared": False, "strikeouts": None}
        pitching = (match.get("stats") or {}).get("pitching") or {}
        appeared = bool(pitching)
        return {
            "found": True,
            "appeared": appeared,
            "strikeouts": int(pitching.get("strikeOuts") or 0) if appeared else None,
        }


# ----------------------------------------------------------- ledger io


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


def grade_row(row: dict, finals: dict[str, dict], client: MlbClient) -> dict:
    meta = _resolve_meta(row)
    graded = {
        "run_id": row.get("run_id"),
        "family": meta.get("family") or row.get("family"),
        "proposition_id": row.get("proposition_id"),
        "pick": row.get("pick"),
        "line": meta.get("line", row.get("line")),
        "price": row.get("price"),
        "p_pick": row.get("p_pick"),
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
    if family == "pitcher_k":
        lookup = client.pitcher_lookup(game_pk, row.get("subject_id"), meta.get("name_key"))
        if not lookup["found"]:
            graded.update({"result": VOID, "units": 0.0, "detail": "pitcher not in boxscore"})
            return graded
        if not lookup["appeared"]:
            graded.update({"result": VOID, "units": 0.0, "detail": "pitcher did not appear"})
            return graded
        line = float(meta.get("line"))
        result = grade_pitcher_k(row.get("pick"), line, lookup["strikeouts"])
        graded.update({"result": result, "units": None, "detail": f"{lookup['strikeouts']} K vs {line}"})
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

    rows = load_screen_rows(screen)
    by_run: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_run[str(row.get("run_id"))].append(row)

    revisions: dict[str, dict] = {}
    settlements: list[dict] = []
    for run_id, run_rows in sorted(by_run.items()):
        graded_rows = []
        for row in run_rows:
            family = _resolve_meta(row).get("family") or row.get("family")
            if family not in GRADEABLE_FAMILIES:
                continue
            try:
                graded = grade_row(row, finals, client)
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
            if graded.get("_roi_index") is not None:
                settlements.append(graded)
        revisions[run_id] = {
            "slot": slot_label(run_id),
            "summary": summarize(graded_rows),
            "roi": roi_summary(graded_rows),
            "rows": graded_rows,
        }

    latest = latest_run_id(list(revisions.keys()))
    pending_total = sum(r["summary"]["totals"]["pending"] for r in revisions.values())
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
    }
    return result


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
    changed = settle_roi_ledger(ROI_LEDGER, result["settlements"])

    recap = render_recap(result)
    print(recap)
    print(f"\ngraded {screen}: ledger updates {changed}, api_error={result['api_error']}")

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
