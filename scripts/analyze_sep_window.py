"""Deep win/loss attribution study for the forecast board, 2026-09-10..2026-09-22.

Read-only research over an outputs tree (default: the Azure VM's ~/mlb_props).
It joins graded canonical rows to:
  * source feature rows (pitcher candidates, game markets, board rows),
  * historical MLB standings for each date (team playoff situation),
  * actual boxscore pitching lines (outs / batters faced / pitches / ER).

Then it reports accuracy, calibration, model-vs-market, a win/loss taxonomy
(opportunity vs conversion, with a winning counterpart), and a deep feature-level
harm/help screen with Benjamini-Hochberg FDR.

Usage:
  python3 scripts/analyze_sep_window.py --root ~/mlb_props --out /tmp/research
"""
from __future__ import annotations

import argparse
import json
import math
import re
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

STATS_BASE = "https://statsapi.mlb.com/api/v1"
WINDOW_START = "2026-09-10"
WINDOW_END = "2026-09-22"
FAMILIES = ("pitcher_k", "game_ml", "game_total", "game_rl")


# ------------------------------------------------------------------ helpers


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def american_prob(price):
    if price is None:
        return None
    p = float(price)
    return 100.0 / (p + 100.0) if p > 0 else (-p) / (-p + 100.0)


def american_payout(price):
    if price is None:
        return None
    p = float(price)
    return p / 100.0 if p > 0 else 100.0 / (-p)


def no_vig(price_a, price_b):
    pa, pb = american_prob(price_a), american_prob(price_b)
    if pa is None or pb is None or (pa + pb) <= 0:
        return None, None
    return pa / (pa + pb), pb / (pa + pb)


def parse_ts(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "mlb-props-research/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def flatten_numeric(obj, prefix=""):
    out = {}
    if not isinstance(obj, dict):
        return out
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, bool):
            out[name] = 1.0 if value else 0.0
        elif isinstance(value, (int, float)):
            out[name] = float(value)
        elif isinstance(value, dict):
            out.update(flatten_numeric(value, prefix=f"{name}."))
    return out


def flatten_flags(obj, prefix=""):
    out = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "flags" and isinstance(value, list):
                out.extend(f"{prefix}{f}" for f in value)
            elif isinstance(value, dict):
                out.extend(flatten_flags(value, prefix=f"{prefix}{key}."))
    return out


# ---------------------------------------------------------------- standings


class Standings:
    def __init__(self):
        self._cache: dict[str, dict] = {}

    def for_date(self, day: str) -> dict:
        if day in self._cache:
            return self._cache[day]
        url = (
            f"{STATS_BASE}/standings?leagueId=103,104&season=2026"
            f"&date={day}&standingsTypes=regularSeason&hydrate=team"
        )
        teams: dict[str, dict] = {}
        try:
            payload = fetch_json(url)
        except Exception as exc:  # noqa: BLE001
            print(f"  standings fetch failed {day}: {exc}")
            self._cache[day] = teams
            return teams
        for record in payload.get("records", []) or []:
            for team in record.get("teamRecords", []) or []:
                info = team.get("team", {}) or {}
                tid = str(info.get("id"))
                indicator = team.get("clinchIndicator")
                if indicator in ("y", "z"):
                    status = "clinched_division"
                elif indicator == "x":
                    status = "clinched_playoff"
                elif indicator == "e":
                    status = "eliminated"
                else:
                    status = "contending"
                teams[tid] = {
                    "team_id": tid,
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
                    "streak": (team.get("streak") or {}).get("streakCode"),
                }
        self._cache[day] = teams
        return teams


# ------------------------------------------------------------- boxscore io


class Boxscores:
    def __init__(self):
        self._cache: dict[str, dict] = {}

    def lines(self, game_pk: str) -> dict:
        if game_pk in self._cache:
            return self._cache[game_pk]
        out: dict[str, dict] = {}
        try:
            payload = fetch_json(f"{STATS_BASE}/game/{game_pk}/boxscore")
        except Exception as exc:  # noqa: BLE001
            print(f"  boxscore fetch failed {game_pk}: {exc}")
            self._cache[game_pk] = out
            return out
        for side in ("home", "away"):
            team = (payload.get("teams") or {}).get(side) or {}
            for player in (team.get("players") or {}).values():
                stats = (player.get("stats") or {}).get("pitching") or {}
                if not stats:
                    continue
                person = player.get("person") or {}
                key = str(person.get("id"))
                out[key] = {
                    "name": person.get("fullName"),
                    "name_key": norm(person.get("fullName")),
                    "outs": stats.get("outs"),
                    "batters_faced": stats.get("battersFaced"),
                    "pitches": stats.get("numberOfPitches"),
                    "strikeouts": stats.get("strikeOuts"),
                    "hits": stats.get("hits"),
                    "walks": stats.get("baseOnBalls"),
                    "earned_runs": stats.get("earnedRuns"),
                    "side": side,
                }
        self._cache[game_pk] = out
        return out


# --------------------------------------------------------------- data load


def load_grades(root: Path) -> list[dict]:
    grades_dir = root / "outputs/grades"
    rows = []
    for path in sorted(grades_dir.glob("forecast_board_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        day = payload.get("screen_date", "")
        if not (WINDOW_START <= day <= WINDOW_END):
            continue
        canonical = payload.get("canonical") or {}
        for row in canonical.get("rows") or []:
            if row.get("result") not in ("WIN", "LOSS", "PUSH", "VOID"):
                continue
            rows.append({**row, "screen_date": day})
    return rows


def load_board_rows(root: Path) -> dict:
    boards_dir = root / "outputs/forecast_boards"
    by_run: dict[str, dict] = {}
    for path in sorted(boards_dir.glob("forecast_board_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        run_id = payload.get("run_id")
        rows = {}
        for section in (payload.get("sections") or {}).values():
            for row in section:
                rows[row.get("proposition_id")] = row
        by_run[run_id] = {"screen_date": payload.get("screen_date"), "slot": payload.get("slot"), "rows": rows}
    return by_run


def load_pitcher_candidates(root: Path) -> dict:
    hist = root / "outputs/history"
    lookup: dict[tuple, list] = defaultdict(list)
    for path in sorted(hist.glob("pitcher_props_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        day = payload.get("screen_date", "")
        if not (WINDOW_START <= day <= WINDOW_END):
            continue
        exported = payload.get("exported_at")
        opinions = {}
        for op in payload.get("model_opinions") or []:
            opinions[str(op.get("pitcher_id"))] = op
        for cand in payload.get("candidates") or []:
            key = (day, str(cand.get("event_id")), norm(cand.get("subject_name")), cand.get("line"))
            lookup[key].append({"exported_at": exported, "candidate": cand, "opinions": opinions.get(str(cand.get("subject_id")), {})})
    for key in lookup:
        lookup[key].sort(key=lambda item: item["exported_at"] or "")
    return lookup


def load_game_markets(root: Path) -> dict:
    hist = root / "outputs/history"
    lookup: dict[tuple, list] = defaultdict(list)
    for path in sorted(hist.glob("game_markets_*.json")):
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        day = payload.get("screen_date", "")
        if not (WINDOW_START <= day <= WINDOW_END):
            continue
        exported = payload.get("exported_at")
        for game in payload.get("games") or []:
            lookup[(day, str(game.get("game_id")))].append({"exported_at": exported, "game": game})
    for key in lookup:
        lookup[key].sort(key=lambda item: item["exported_at"] or "")
    return lookup


# ---------------------------------------------------------------- assembly


def pitcher_side_prob(shadow, side):
    if not shadow:
        return None
    if side == "over":
        return shadow.get("over_no_vig_probability")
    return shadow.get("under_no_vig_probability")


def build_record(graded, board_rows, pit_lookup, gm_lookup, standings, boxscores):
    day = graded["screen_date"]
    family = graded["family"]
    run_id = str(graded.get("run_id"))
    prop = str(graded.get("proposition_id"))
    board = (board_rows.get(run_id) or {}).get("rows", {}).get(prop, {})
    slot = (board_rows.get(run_id) or {}).get("slot")

    rec = {
        "screen_date": day,
        "slot": slot,
        "family": family,
        "proposition_id": prop,
        "subject": graded.get("subject"),
        "game_pk": str(graded.get("game_pk")),
        "pick": graded.get("pick"),
        "line": graded.get("line"),
        "price": graded.get("price"),
        "p_pick": graded.get("p_pick"),
        "result": graded.get("result"),
        "units": graded.get("units"),
        "detail": graded.get("detail"),
        "ev": board.get("ev"),
        "ev_flag": board.get("ev_flag"),
        "book": board.get("book"),
        "engine_version": board.get("engine_version"),
    }
    for key in ("p_over", "p_under", "p_home", "p_away", "mean_home_runs", "mean_away_runs",
                "home_team", "away_team", "subject_id"):
        if key in board:
            rec[key] = board.get(key)

    # ----- market probability for the side we picked
    market_p = None
    if family == "pitcher_k":
        key = (day, str(graded.get("game_pk")), norm(graded.get("subject")), graded.get("line"))
        cands = pit_lookup.get(key)
        if cands:
            source = cands[-1]
            cand = source["candidate"]
            rec["features"] = flatten_numeric(cand)
            rec["features"].update(flatten_numeric(source.get("opinions"), "op."))
            rec["flags"] = flatten_flags(cand)
            rec["subject_role"] = cand.get("subject_role")
            market_p = pitcher_side_prob(cand.get("price_shadow"), graded.get("pick"))
            rec["market_p"] = market_p
            rec["projected_strikeouts"] = cand.get("projected_strikeouts")
            rec["projected_outs"] = cand.get("projected_outs")
            rec["projected_bf"] = cand.get("projected_batters_faced")
            rec["projected_k_rate"] = cand.get("projected_k_rate")
            rec["opportunity_confidence"] = (cand.get("opportunity_shadow") or {}).get("opportunity_confidence")
            rec["team"] = cand.get("team")
            rec["opponent"] = cand.get("opponent")
            # actual boxscore line
            lines = boxscores.lines(rec["game_pk"])
            sid = str(cand.get("subject_id"))
            actual = lines.get(sid)
            if actual is None:
                for line in lines.values():
                    if line["name_key"] == norm(cand.get("subject_name")):
                        actual = line
                        break
            if actual:
                rec["actual"] = {k: actual.get(k) for k in ("outs", "batters_faced", "pitches", "strikeouts", "hits", "walks", "earned_runs")}
                actual_bf = actual.get("batters_faced")
                actual_k = actual.get("strikeouts")
                proj_bf = rec.get("projected_bf")
                proj_k_rate = rec.get("projected_k_rate")
                if actual_bf and proj_bf:
                    rec["opp_ratio"] = round(actual_bf / proj_bf, 3)
                if actual_bf and proj_k_rate is not None:
                    expected_k = proj_k_rate * actual_bf
                    rec["expected_k_at_actual_bf"] = round(expected_k, 2)
                    if actual_k is not None:
                        rec["k_conversion_delta"] = round(actual_k - expected_k, 2)
    else:
        key = (day, str(graded.get("game_pk")))
        markets = gm_lookup.get(key)
        if markets:
            game = markets[-1]["game"]
            rec["features"] = flatten_numeric(game)
            baseline = game.get("market_baseline") or {}
            moneyline = game.get("moneyline") or {}
            total = game.get("total") or {}
            spread = game.get("spread") or {}
            pick = graded.get("pick")
            if family == "game_ml":
                market_p = baseline.get("home_win_no_vig") if pick == "home" else baseline.get("away_win_no_vig")
                rec["home_team"] = game.get("home_team")
                rec["away_team"] = game.get("away_team")
            elif family == "game_total":
                market_p = baseline.get("over_no_vig") if pick == "over" else baseline.get("under_no_vig")
            elif family == "game_rl":
                pa, pb = no_vig(spread.get("price_a"), spread.get("price_b"))
                market_p = pa if pick == "home_covers" else pb
            rec["market_p"] = market_p
            rec["market_baseline"] = baseline
            rec["start_time_utc"] = game.get("start_time_utc")
            rec["ml_price_a"] = moneyline.get("price_a")
            rec["ml_price_b"] = moneyline.get("price_b")
            rec["total_line"] = total.get("line")
            rec["spread_line"] = spread.get("line")
            rec["home_team"] = rec.get("home_team") or game.get("home_team")
            rec["away_team"] = rec.get("away_team") or game.get("away_team")

    # ----- team playoff situation
    day_standings = standings.for_date(day)
    team_status = None
    opp_status = None
    if family == "pitcher_k" and rec.get("team"):
        for info in day_standings.values():
            if info.get("abbr") == rec.get("team"):
                team_status = info
            if info.get("abbr") == rec.get("opponent"):
                opp_status = info
    elif family.startswith("game"):
        for info in day_standings.values():
            if info.get("abbr") == rec.get("home_team"):
                rec["home_status"] = info
            if info.get("abbr") == rec.get("away_team"):
                rec["away_status"] = info
    rec["team_status"] = team_status
    rec["opp_status"] = opp_status

    # ----- model vs market
    if rec.get("p_pick") is not None and market_p is not None:
        rec["model_minus_market"] = round(rec["p_pick"] - market_p, 4)
    return rec


# ------------------------------------------------------------- statistics


def brier(rows, prob_key):
    vals = [(r[prob_key], 1.0 if r["result"] == "WIN" else 0.0) for r in rows
            if r.get(prob_key) is not None and r["result"] in ("WIN", "LOSS")]
    if not vals:
        return None
    return sum((p - y) ** 2 for p, y in vals) / len(vals)


def logloss(rows, prob_key):
    vals = [(min(max(r[prob_key], 1e-6), 1 - 1e-6), 1.0 if r["result"] == "WIN" else 0.0)
            for r in rows if r.get(prob_key) is not None and r["result"] in ("WIN", "LOSS")]
    if not vals:
        return None
    return -sum(y * math.log(p) + (1 - y) * math.log(1 - p) for p, y in vals) / len(vals)


def summarize(rows):
    decided = [r for r in rows if r["result"] in ("WIN", "LOSS")]
    wins = sum(r["result"] == "WIN" for r in decided)
    priced = [r for r in decided if r.get("units") is not None]
    units = sum(r["units"] or 0.0 for r in priced)
    return {
        "n": len(decided),
        "wins": wins,
        "losses": len(decided) - wins,
        "hit_rate": round(wins / len(decided), 4) if decided else None,
        "priced_n": len(priced),
        "units": round(units, 3),
        "roi": round(units / len(priced), 4) if priced else None,
        "mean_p_pick": round(sum(r["p_pick"] for r in decided if r.get("p_pick") is not None) /
                             max(1, sum(1 for r in decided if r.get("p_pick") is not None)), 4),
        "mean_market_p": round(sum(r["market_p"] for r in decided if r.get("market_p") is not None) /
                               max(1, sum(1 for r in decided if r.get("market_p") is not None)), 4),
        "brier_model": brier(decided, "p_pick"),
        "brier_market": brier(decided, "market_p"),
        "logloss_model": logloss(decided, "p_pick"),
        "logloss_market": logloss(decided, "market_p"),
    }


def two_prop_z(w1, n1, w2, n2):
    if n1 == 0 or n2 == 0:
        return None
    p1, p2 = w1 / n1, w2 / n2
    p = (w1 + w2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return None
    z = (p1 - p2) / se
    pval = math.erfc(abs(z) / math.sqrt(2))
    return z, pval


def bh_fdr(pvals):
    indexed = sorted(enumerate(pvals), key=lambda item: item[1])
    m = len(pvals)
    qvals = [1.0] * m
    prev = 1.0
    for rank, (idx, p) in enumerate(reversed(indexed), start=1):
        k = m - rank + 1
        q = min(prev, p * m / k)
        qvals[idx] = q
        prev = q
    return qvals


# ----------------------------------------------------------------- screen


def feature_screen(rows, min_n=12):
    decided = [r for r in rows if r["result"] in ("WIN", "LOSS")]
    tests = []

    numeric_values = defaultdict(list)
    for rec in decided:
        for key, value in (rec.get("features") or {}).items():
            numeric_values[key].append(value)
    for key, values in numeric_values.items():
        pairs = [(r, (r.get("features") or {}).get(key)) for r in decided]
        pairs = [(r, v) for r, v in pairs if v is not None]
        if len(pairs) < min_n:
            continue
        wins = [v for r, v in pairs if r["result"] == "WIN"]
        losses = [v for r, v in pairs if r["result"] == "LOSS"]
        if len(wins) < 4 or len(losses) < 4:
            continue
        if max(v for _, v in pairs) - min(v for _, v in pairs) < 1e-9:
            continue
        mean_w, mean_l = sum(wins) / len(wins), sum(losses) / len(losses)
        sd = math.sqrt(sum((v - (mean_w if r["result"] == "WIN" else mean_l)) ** 2 for r, v in pairs) /
                       max(1, len(pairs) - 2))
        if sd <= 1e-9:
            continue
        se = sd * math.sqrt(1 / len(wins) + 1 / len(losses))
        t = (mean_w - mean_l) / se
        pval = math.erfc(abs(t) / math.sqrt(2))
        # median split hit rate
        ordered = sorted(v for _, v in pairs)
        median = ordered[len(ordered) // 2]
        hi = [r for r, v in pairs if v >= median]
        lo = [r for r, v in pairs if v < median]
        if not hi or not lo:
            continue
        hi_w = sum(r["result"] == "WIN" for r in hi)
        lo_w = sum(r["result"] == "WIN" for r in lo)
        tests.append({
            "type": "numeric", "feature": key, "n": len(pairs),
            "mean_win": round(mean_w, 4), "mean_loss": round(mean_l, 4),
            "t": round(t, 3), "p": pval,
            "hi_n": len(hi), "hi_hit": round(hi_w / len(hi), 3) if hi else None,
            "lo_n": len(lo), "lo_hit": round(lo_w / len(lo), 3) if lo else None,
        })

    # boolean flags (only rows that actually carry a flag vector)
    flagged_rows = [r for r in decided if r.get("flags") is not None]
    flag_names = sorted({f for r in flagged_rows for f in (r.get("flags") or [])})
    for flag in flag_names:
        yes = [r for r in flagged_rows if flag in (r.get("flags") or [])]
        no = [r for r in flagged_rows if flag not in (r.get("flags") or [])]
        if len(yes) < 6 or len(no) < 6:
            continue
        yw = sum(r["result"] == "WIN" for r in yes)
        nw = sum(r["result"] == "WIN" for r in no)
        z = two_prop_z(yw, len(yes), nw, len(no))
        if z is None:
            continue
        tests.append({
            "type": "flag", "feature": flag, "n": len(yes) + len(no),
            "yes_n": len(yes), "yes_hit": round(yw / len(yes), 3),
            "no_n": len(no), "no_hit": round(nw / len(no), 3),
            "z": round(z[0], 3), "p": z[1],
        })

    # team situation categoricals
    for field in ("team_status", "opp_status", "home_status", "away_status"):
        groups = defaultdict(lambda: [0, 0])
        for r in decided:
            info = r.get(field)
            if not info:
                continue
            bucket = info.get("status")
            groups[bucket][0] += 1
            groups[bucket][1] += r["result"] == "WIN"
        if len(groups) < 2:
            continue
        for bucket, (n, w) in sorted(groups.items()):
            rest_n = sum(v[0] for k, v in groups.items() if k != bucket)
            rest_w = sum(v[1] for k, v in groups.items() if k != bucket)
            z = two_prop_z(w, n, rest_w, rest_n)
            if z is None:
                continue
            tests.append({
                "type": f"status:{field}", "feature": bucket, "n": n + rest_n,
                "yes_n": n, "yes_hit": round(w / n, 3) if n else None,
                "no_n": rest_n, "no_hit": round(rest_w / rest_n, 3) if rest_n else None,
                "z": round(z[0], 3), "p": z[1],
            })

    qvals = bh_fdr([t["p"] for t in tests])
    for test, q in zip(tests, qvals):
        test["q"] = round(q, 4)
    tests.sort(key=lambda t: t["p"])
    return tests


# -------------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(Path.home() / "mlb_props"))
    parser.add_argument("--out", default="/tmp/research")
    args = parser.parse_args()
    root = Path(args.root).expanduser()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    print("loading grades...")
    graded = load_grades(root)
    print(f"  {len(graded)} canonical graded rows in window")
    print("loading boards / candidates / markets...")
    board_rows = load_board_rows(root)
    pit_lookup = load_pitcher_candidates(root)
    gm_lookup = load_game_markets(root)
    standings = Standings()
    boxscores = Boxscores()

    print("assembling research table...")
    table = []
    for graded_row in graded:
        try:
            table.append(build_record(graded_row, board_rows, pit_lookup, gm_lookup, standings, boxscores))
        except Exception as exc:  # noqa: BLE001
            print(f"  record failed {graded_row.get('proposition_id')}: {type(exc).__name__}: {exc}")

    (out / "research_table.json").write_text(json.dumps(table, indent=1, default=str))

    # -------- metrics
    decided = [r for r in table if r["result"] in ("WIN", "LOSS")]
    metrics = {
        "window": [WINDOW_START, WINDOW_END],
        "overall": summarize(decided),
        "by_family": {fam: summarize([r for r in decided if r["family"] == fam]) for fam in FAMILIES},
        "by_side": {},
        "calibration": {},
    }
    for family in FAMILIES:
        for side in sorted({r.get("pick") for r in decided if r["family"] == family}):
            metrics["by_side"][f"{family}:{side}"] = summarize(
                [r for r in decided if r["family"] == family and r.get("pick") == side])

    # calibration buckets on model probability
    buckets = defaultdict(lambda: [0, 0])
    for r in decided:
        if r.get("p_pick") is None:
            continue
        bucket = round(r["p_pick"] * 20) / 20
        buckets[bucket][0] += 1
        buckets[bucket][1] += r["result"] == "WIN"
    metrics["calibration"]["model"] = {
        f"{k:.2f}": {"n": v[0], "observed": round(v[1] / v[0], 3)} for k, v in sorted(buckets.items())
    }
    mbuckets = defaultdict(lambda: [0, 0])
    for r in decided:
        if r.get("market_p") is None:
            continue
        bucket = round(r["market_p"] * 20) / 20
        mbuckets[bucket][0] += 1
        mbuckets[bucket][1] += r["result"] == "WIN"
    metrics["calibration"]["market"] = {
        f"{k:.2f}": {"n": v[0], "observed": round(v[1] / v[0], 3)} for k, v in sorted(mbuckets.items())
    }

    # model vs market agreement
    agree = [r for r in decided if r.get("model_minus_market") is not None]
    metrics["agreement"] = {
        "with_market": summarize([r for r in agree if r["model_minus_market"] >= 0]),
        "against_market": summarize([r for r in agree if r["model_minus_market"] < 0]),
    }

    # -------- pitcher taxonomy (opportunity vs conversion)
    def pitcher_components(r):
        """Decompose actual-vs-projected Ks into workload and conversion parts,
        signed so positive helps the side we picked."""
        actual = r.get("actual") or {}
        actual_k = actual.get("strikeouts")
        actual_bf = actual.get("batters_faced")
        proj_bf = r.get("projected_bf")
        proj_k = r.get("projected_strikeouts")
        proj_k_rate = r.get("projected_k_rate")
        if None in (actual_k, actual_bf, proj_bf, proj_k) or not proj_bf:
            return None, None
        if proj_k_rate is None:
            proj_k_rate = proj_k / proj_bf if proj_bf else None
        if proj_k_rate is None:
            return None, None
        expected_k_at_actual_bf = proj_k_rate * actual_bf
        opportunity = expected_k_at_actual_bf - proj_k
        conversion = actual_k - expected_k_at_actual_bf
        sign = 1.0 if r.get("pick") == "over" else -1.0
        return sign * opportunity, sign * conversion

    def pitcher_bucket(r):
        opp, conv = pitcher_components(r)
        if opp is None:
            return "no_data"
        if opp <= -1.0 and opp <= conv:
            return "workload_short"
        if conv <= -1.0 and conv < opp:
            return "conversion_cold"
        if opp >= 1.0 and opp >= conv:
            return "workload_long"
        if conv >= 1.0 and conv > opp:
            return "conversion_hot"
        return "near_line"

    taxonomy = defaultdict(lambda: {"n": 0, "wins": 0, "units": 0.0, "priced": 0})
    for r in decided:
        if r["family"] != "pitcher_k":
            continue
        bucket_name = pitcher_bucket(r)
        r["pitcher_bucket"] = bucket_name
        result_bucket = "win" if r["result"] == "WIN" else "loss"
        bucket = taxonomy[f"{result_bucket}:{bucket_name}"]
        bucket["n"] += 1
        bucket["wins"] += r["result"] == "WIN"
        if r.get("units") is not None:
            bucket["units"] += r["units"]
            bucket["priced"] += 1
    metrics["pitcher_taxonomy"] = {
        k: {**v, "units": round(v["units"], 3)} for k, v in sorted(taxonomy.items())
    }

    # -------- generic breakdowns (side, ev_flag, price band, gap, slot)
    def band(value, edges, labels):
        if value is None:
            return "na"
        for edge, label in zip(edges, labels):
            if value < edge:
                return label
        return labels[-1]

    def table(rows, keyfn):
        groups = defaultdict(lambda: {"n": 0, "wins": 0, "units": 0.0, "priced": 0})
        for r in rows:
            g = groups[keyfn(r)]
            g["n"] += 1
            g["wins"] += r["result"] == "WIN"
            if r.get("units") is not None:
                g["units"] += r["units"]
                g["priced"] += 1
        return {k: {"n": v["n"], "wins": v["wins"], "hit": round(v["wins"] / v["n"], 3),
                    "units": round(v["units"], 2)} for k, v in sorted(groups.items(), key=lambda i: str(i[0]))}

    metrics["by_ev_flag"] = table(decided, lambda r: r.get("ev_flag") or "na")
    metrics["by_slot"] = table(decided, lambda r: r.get("slot") or "legacy")
    metrics["by_price_band"] = table(decided, lambda r: band(r.get("price"),
                                                            [-150, -110, 100, 150, 9999],
                                                            ["<-150", "-150..-110", "-110..+100", "+100..+150", ">+150"]))
    metrics["by_gap_band"] = table([r for r in decided if r.get("model_minus_market") is not None],
                                   lambda r: band(r["model_minus_market"], [-0.05, 0.0, 0.05, 0.10, 9999],
                                                  ["<-5pt", "-5..0pt", "0..5pt", "5..10pt", ">10pt"]))
    metrics["by_opportunity_conf"] = table([r for r in decided if r["family"] == "pitcher_k"],
                                           lambda r: r.get("opportunity_confidence") or "na")
    metrics["by_pitcher_bucket"] = table([r for r in decided if r["family"] == "pitcher_k"],
                                         lambda r: r.get("pitcher_bucket") or "na")
    metrics["pitcher_by_side"] = table([r for r in decided if r["family"] == "pitcher_k"],
                                       lambda r: r.get("pick") or "na")
    metrics["agreement_by_family"] = {
        fam: {
            "with_market": summarize([r for r in decided if r["family"] == fam and (r.get("model_minus_market") or -1) >= 0]),
            "against_market": summarize([r for r in decided if r["family"] == fam and (r.get("model_minus_market") or 0) < 0]),
        } for fam in FAMILIES
    }

    # -------- feature screen (overall + per family)
    screens = {"all": feature_screen(decided)}
    for fam in FAMILIES:
        fam_rows = [r for r in decided if r["family"] == fam]
        if len(fam_rows) >= 30:
            screens[fam] = feature_screen(fam_rows, min_n=10)
    (out / "feature_screen.json").write_text(json.dumps(screens, indent=1, default=str))
    screen = screens["all"]

    # -------- team situation tables
    def status_table(field):
        groups = defaultdict(lambda: {"n": 0, "wins": 0, "units": 0.0, "priced": 0})
        for r in decided:
            info = r.get(field)
            if not info:
                continue
            g = groups[info.get("status")]
            g["n"] += 1
            g["wins"] += r["result"] == "WIN"
            if r.get("units") is not None:
                g["units"] += r["units"]
                g["priced"] += 1
        return {k: {"n": v["n"], "wins": v["wins"], "hit": round(v["wins"] / v["n"], 3),
                    "units": round(v["units"], 2), "priced": v["priced"]}
                for k, v in sorted(groups.items())}

    metrics["team_status_tables"] = {
        "pitcher_team": status_table("team_status"),
        "pitcher_opponent": status_table("opp_status"),
        "home_team": status_table("home_status"),
        "away_team": status_table("away_status"),
    }

    (out / "metrics.json").write_text(json.dumps(metrics, indent=1, default=str))

    # -------- report
    lines = [f"# Sep 10-22 Forecast Board Win/Loss Study", ""]
    lines.append(f"Decided plays: {metrics['overall']['n']}  "
                 f"({metrics['overall']['wins']}-{metrics['overall']['losses']}, "
                 f"{metrics['overall']['hit_rate']:.1%})  "
                 f"units {metrics['overall']['units']:+.2f}  ROI {metrics['overall']['roi']:+.1%}")
    lines.append("")
    lines.append("## Accuracy by family")
    lines.append("| family | n | W-L | hit | units | ROI | model p | market p | BrierM | BrierMkt |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for fam in FAMILIES:
        s = metrics["by_family"][fam]
        if not s["n"]:
            continue
        lines.append(f"| {fam} | {s['n']} | {s['wins']}-{s['losses']} | {s['hit_rate']:.1%} | "
                     f"{s['units']:+.2f} | {s['roi']:+.1%} | {s['mean_p_pick']:.3f} | {s['mean_market_p']:.3f} | "
                     f"{s['brier_model']:.4f} | {s['brier_market']:.4f} |")
    lines.append("")
    lines.append("## Model vs market agreement")
    for key, s in metrics["agreement"].items():
        lines.append(f"- {key}: n={s['n']} {s['wins']}-{s['losses']} ({s['hit_rate']:.1%}) units {s['units']:+.2f}")
    lines.append("")
    lines.append("## Pitcher taxonomy (opportunity vs conversion)")
    for key, v in metrics["pitcher_taxonomy"].items():
        lines.append(f"- {key}: n={v['n']} wins={v['wins']} units {v['units']:+.2f}")
    lines.append("")
    for section, key in (("By EV flag", "by_ev_flag"), ("By slot", "by_slot"),
                         ("By price band", "by_price_band"), ("By model-market gap", "by_gap_band"),
                         ("Pitcher by side", "pitcher_by_side"), ("Pitcher by opportunity confidence", "by_opportunity_conf"),
                         ("Pitcher by failure bucket", "by_pitcher_bucket")):
        lines.append(f"## {section}")
        for k, v in metrics[key].items():
            lines.append(f"- {k}: n={v['n']} {v['wins']}-{v['n'] - v['wins']} ({v['hit']:.0%}) units {v['units']:+.2f}")
        lines.append("")
    lines.append("## Agreement by family")
    for fam, groups in metrics["agreement_by_family"].items():
        w = groups["with_market"]
        a = groups["against_market"]
        lines.append(f"- {fam}: with market n={w['n']} ({w['hit_rate']:.0%}, {w['units']:+.1f}u) | "
                     f"against market n={a['n']} ({a['hit_rate']:.0%}, {a['units']:+.1f}u)")
    lines.append("")
    lines.append("## Team playoff situation")
    for field, table in metrics["team_status_tables"].items():
        lines.append(f"- {field}: " + "; ".join(f"{k} {v['n']}p {v['hit']:.0%} {v['units']:+.1f}u"
                                                for k, v in table.items()))
    lines.append("")
    lines.append("## Calibration (model vs market)")
    lines.append("| bucket | model n | model obs | market n | market obs |")
    lines.append("|---|---:|---:|---:|---:|")
    cal_m = metrics["calibration"]["model"]
    cal_k = metrics["calibration"]["market"]
    for bucket in sorted(set(cal_m) | set(cal_k)):
        m = cal_m.get(bucket, {})
        k = cal_k.get(bucket, {})
        lines.append(f"| {bucket} | {m.get('n', 0)} | {m.get('observed', '')} | {k.get('n', 0)} | {k.get('observed', '')} |")
    lines.append("")
    lines.append("## Top feature signals (by p-value; q = FDR-adjusted)")
    for label, tests in screens.items():
        lines.append(f"### {label}")
        lines.append("| type | feature | n | yes/hi hit | no/lo hit | p | q |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for t in tests[:12]:
            yes = t.get("yes_hit", t.get("hi_hit"))
            no = t.get("no_hit", t.get("lo_hit"))
            lines.append(f"| {t['type']} | {t['feature']} | {t['n']} | {yes} | {no} | {t['p']:.4f} | {t['q']:.3f} |")
        lines.append("")
    (out / "report.md").write_text("\n".join(lines))

    print(f"\nwrote {out}/research_table.json, metrics.json, feature_screen.json, report.md")
    print("\n".join(lines[:40]))


if __name__ == "__main__":
    main()
