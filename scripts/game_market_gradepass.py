"""Descriptive game-market pass over the consolidated snapshots.

No model claims. Per date this reports AM/PM line movement and how the
pre-start closing snapshot's sides ran against official MLB final scores.

Outputs evidence/grades/game_markets_summary.{json,txt}."""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GM_DIR = ROOT / "evidence/history/game_markets"
OUT_DIR = ROOT / "evidence/grades"


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "mlb-props-gm-gradepass/1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def per_date_snapshots() -> dict[str, list[dict]]:
    by_date: dict[str, list[dict]] = {}
    for path in sorted(GM_DIR.glob("game_markets_*.json")):
        try:
            payload = json.load(open(path))
        except Exception:
            continue
        screen = payload.get("screen_date")
        exported = payload.get("exported_at")
        if not screen or not exported:
            continue
        by_date.setdefault(screen, []).append(
            {"file": path.name, "exported_at": exported, "games": payload.get("games") or []}
        )
    return by_date


def captures_by_game(snapshots: list[dict]) -> dict[str, list[dict]]:
    """Return per-game list of pre-start captures sorted by export time."""
    captures: dict[str, list[dict]] = {}
    for snap in snapshots:
        try:
            pct = datetime.fromisoformat(snap["exported_at"].replace("Z", "+00:00"))
        except Exception:
            continue
        for game in snap["games"]:
            start_raw = game.get("start_time_utc")
            if not start_raw:
                continue
            try:
                spt = datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
            except Exception:
                continue
            if pct >= spt:
                continue  # post-start capture; not a graded line
            record = {"exported_at": snap["exported_at"], "capture": pct, "game": game}
            captures.setdefault(str(game.get("game_id")), []).append(record)
    return {gid: sorted(rows, key=lambda r: r["capture"]) for gid, rows in captures.items()}


def grade_date(screen_date: str, snapshots: list[dict]) -> dict:
    caps = captures_by_game(snapshots)
    # final scores
    try:
        data = fetch_json(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={screen_date}")
    except Exception as exc:
        return {"date": screen_date, "error": str(exc)[:80], "games": []}
    finals = {
        str(game["gamePk"]): {
            "home": game["teams"]["home"].get("score"),
            "away": game["teams"]["away"].get("score"),
            "game_date": game.get("gameDate"),
        }
        for d in data.get("dates", [])
        for game in d.get("games", [])
        if game.get("status", {}).get("abstractGameState") == "Final"
    }
    games: list[dict] = []
    for gid, rows in sorted(caps.items()):
        if not rows:
            continue
        early = rows[0]["game"]
        late = rows[-1]["game"]
        outcome = finals.get(gid)
        row: dict = {
            "game_id": gid,
            "captures": len(rows),
            "earliest_capture": rows[0]["exported_at"],
            "closing_capture": rows[-1]["exported_at"],
            "moneyline_moved": (early.get("moneyline") != late.get("moneyline")),
            "total_moved": (
                (early.get("total") or {}).get("price_a") != (late.get("total") or {}).get("price_a")
                or (early.get("total") or {}).get("price_b") != (late.get("total") or {}).get("price_b")
            ),
            "total_line_moved": (early.get("total") or {}).get("line") != (late.get("total") or {}).get("line"),
        }
        if outcome and outcome["home"] is not None:
            home, away = outcome["home"], outcome["away"]
            margin = home - away
            total_runs = home + away
            row.update({"final": {"home": home, "away": away}, "margin": margin, "total_runs": total_runs})
            ml = late.get("moneyline") or {}
            if ml.get("price_a") is not None and ml.get("price_b") is not None:
                fav_home = ml["price_a"] < ml["price_b"]
                row["ml_fav_cover"] = (margin > 0) if fav_home else (margin < 0)
            spread = late.get("spread") or {}
            if spread.get("line") is not None and margin != 0:
                line = float(spread["line"])
                home_cover = margin + line > 0  # home covers a negative spread when margin > -line
                row["spread_line"] = line
                row["spread_home_covers_margin"] = margin + line
            total = late.get("total") or {}
            if total.get("line") is not None:
                row["total_line"] = float(total["line"])
                row["total_outcome"] = "over" if total_runs > float(total["line"]) else ("push" if total_runs == float(total["line"]) else "under")
        games.append(row)
    return {"date": screen_date, "snapshots": len(snapshots), "games": games}


def main() -> int:
    by_date = per_date_snapshots()
    results = [grade_date(screen, snaps) for screen, snaps in sorted(by_date.items())]

    ml_n = ml_fav = rl_n = rl_push = rl_fav = tot_n = tot_over = tot_push = 0
    moved_games = 0
    for r in results:
        for g in r.get("games", []):
            if g.get("ml_fav_cover") is not None:
                ml_n += 1
                ml_fav += int(g["ml_fav_cover"])
            if g.get("spread_line") is not None and g.get("margin") is not None:
                cover_margin = g["margin"] + g["spread_line"]
                if cover_margin == 0:
                    rl_push += 1
                else:
                    rl_n += 1
                    if cover_margin > 0:
                        rl_fav += 1
            if g.get("total_outcome"):
                tot_n += 1
                if g["total_outcome"] == "push":
                    tot_push += 1
                elif g["total_outcome"] == "over":
                    tot_over += 1
            if g.get("moneyline_moved") or g.get("total_moved") or g.get("total_line_moved"):
                moved_games += 1

    snapshot_days = len(results)
    lines = [
        "Game-market consolidated descriptive summary (observation only)",
        f"- {snapshot_days} snapshot dates (multiple AM/PM captures per date); "
        f"{sum(len(r.get('games', [])) for r in results)} pre-start game captures",
        f"- Games with any AM/closing price movement: {moved_games}",
        f"- Moneyline favourites covered: {ml_fav}/{ml_n} ({(ml_fav / ml_n * 100):.1f}%)",
        f"- Run-line (closing snapshot, home side covers its posted spread): {rl_fav}/{rl_n}; pushes {rl_push}",
        f"- Totals (closing snapshot): over {tot_over}/{tot_n} ({(tot_over / tot_n * 100 if tot_n else 0):.1f}%); pushes {tot_push}",
        "No model claims; prices and movement only.",
    ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "game_markets_summary.json").write_text(json.dumps({"results": results}, indent=1, default=str))
    (OUT_DIR / "game_markets_summary.txt").write_text("\n".join(lines))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
