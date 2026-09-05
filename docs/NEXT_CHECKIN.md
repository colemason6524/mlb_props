# Next Check-in (written 2026-09-04)

Purpose: make the next "get to work" session cheap. Verified live against the Mac
checkout and the Windows production box on 2026-09-04. Goal remains a winning
baseball model with graded Core/Lean P&L + CLV — not pipeline busywork.

## 1. Status one-liners

- **Hot Hits**: healthy and now unblocked — history retention is FIXED (33 exports
  on Windows, 2026-08-01 → 2026-09-04, only expected gaps 8/16 outage + 8/24); the
  8/5–8/20 confidence exports the 8/21 handoff called missing are all present.
  Discord sent daily (9/4: 14 production candidates, 135 research pool).
  Grading is still pending and is the next real work.
- **Pitcher props**: pipeline healthy on Windows — 9/4 scheduled run: schema 8,
  20 qualified candidates, 0 Core / 0 Lean / 0 Watch (honest empty board), Daily
  Card delivered with 4 small-edge unders. All 8/31 version pins live:
  `pitcher-k-hybrid-v2`, `core-lean-watch-v2`, `pitcher-confidence-calibrated-v2`.
  No September grading done yet.
- **Daily Unders Card**: `daily-unders-card-v1` (pre-registered 8/31) delivering
  daily under the September validation window. Success rule: ≥55% at n≥100 graded
  plays → trusted; 52.4–55% → marginal (needs price EV check); <52.4% → killed.
  At ~4.4 plays/day, n≈100 lands early October. No September grade exists yet.
- **Core gates (`core-lean-watch-v2`)**: rebuilt 8/31 on graded August evidence
  (old Core was 2-13). UNDER-only, edge cap 1.5, ≥0.55 no-vig market probability
  when priced. Awaiting its own prospective sample — do not touch until graded.
- **Game-market shadow**: collector healthy — 9 distinct dates (8/27 → 9/4),
  9/4 full coverage (16/16 slate games with moneyline, spread, total, ESPN
  cross-check 16). FanDuel both-side prices (from `8de164b`) are landing:
  81 price_shadow rows / 206 candidates so far.

## 2. Next actions, with WHEN

| Stream | Action | WHEN / trigger |
| --- | --- | --- |
| Hot Hits | Grade retained history: `hot_hits_report.py --history-dir <windows pull> --card-policy delivered` plus confidence-pool calibration (forecast vs observed hit rate + Brier by label, current-gate pass/fail). | Data exists NOW — first session with ~1 hour; the 8/21 retention blocker is gone. |
| Pitcher props / Daily Card | Pull Windows history, run `python3 backtest.py --all-history --include-watch --history-dir pitcher_props_from_windows/<window>` and weekly `pitcher_grading.daily_card_summary` (hit rate, units @ -110, always-under baseline). | First pull after ~9/8 (≈1 week of September card); formal success-rule check when n≥100 graded card plays (≈ early Oct). |
| Core v2 gates | Grade new-gate Core/Lean sample (~50–100 resolved candidates). If the 0.55 no-vig threshold proves wrong, that is a separate `core-lean-watch-v3` commit. | Late September, after the grade — never before. |
| Game markets | Build the line-movement report (morning vs evening snapshots, starter-change detection via probable-pitcher diffs). | After two full weeks of collection: ≥ 2026-09-10. |
| Card v2 no-vig gate | Evaluate `daily-unders-card-v2` only once ~2 weeks of priced rows exist. | Priced rows started 8/31; evaluate ≈ 2026-09-14. |
| Windows `99f53b5` | Hand-made commit "Normalize repo metadata and docs" (adds `logs/` to .gitignore, `.gitattributes`, README schema-8 version refresh) — verified benign, but unpushed. Either push it deliberately or rebase the next pull over it. | At next Windows sync. |

## 3. What to check on pop-back (5-minute health pass)

1. `git status -sb` on Mac and `ssh windows "cd C:/Users/muski/mlb_props && git status -sb"`.
2. All four schtasks show `Last Result: 0` for today: `MLB_hot_hits`,
   `MLB Pitcher Plays`, `MLB_game_markets`, `MLB_game_markets_evening`.
3. `outputs/history` on Windows has a fresh file from each stream today
   (`hot_hits_*`, `pitcher_props_*`, two `game_markets_*`).
4. Latest pitcher export pins: `history_schema_version: 8`,
   `model_version: pitcher-k-hybrid-v2`, `tier_policy_version: core-lean-watch-v2`,
   `confidence_model_version: pitcher-confidence-calibrated-v2`,
   `daily_card_policy_version: daily-unders-card-v1`; `daily_card` rows present;
   `price_shadow` populated on eligible candidates.
5. Latest game_markets export coverage: `matched_with_lines` ≈ `slate_games`.
6. Empty Core / empty Card is a valid outcome — verify it is source coverage
   (`line_coverage`, `outputs/diagnostics/`) before reading anything into it.

## 4. What NOT to do

- **No live Core retune.** `core-lean-watch-v2` was rebuilt 8/31 from graded
  evidence and needs its own prospective sample; changes wait for the grade.
- **No Discord polish.** No formatting/embed/limit changes on either channel.
- **Do not recreate or edit healthy schtasks.** All four tasks ran result 0 on
  9/4; their schedules (11:30 / 11:35 / 11:40 / 16:35 ET) are correct.
- **Do not redo the Cole 8/31 card.** `daily-unders-card-v1` is pre-registered
  and frozen; changing gates requires a new policy version in a separate commit.
- **No pipeline busywork.** Every change should serve graded P&L / CLV evidence.
- **No `git add .`** in this shared checkout — stage named files only.
- Do not tune from abnormal slates (8/16-style outages, tiny slates, late runs,
  thin coverage) and do not count DNP legs as ordinary Hot Hits misses.

## 5. Leave-off pointer

- **Mac HEAD**: `7eb8e98` (= `origin/main` at write time). Fixes since 8/31:
  `c25389d` Bovada empty `{}` treated as fresh coupon, `7eb8e98` drop
  `preMatchOnly` so morning coupons are not empty.
- **Windows HEAD**: `99f53b5` (7eb8e98 + hand-made docs/metadata commit, unpushed).
- **Key files**: `run_nightly.py`, `backtest.py`, `mlb_props/tiers.py` (Core
  policy), `mlb_props/daily_card.py` (card policy), `mlb_props/pitcher_grading.py`
  (strict grader + `daily_card_summary`), `mlb_props/game_markets.py`,
  `run_game_markets.py`, `hot_hits_report.py`, `mlb_props/version.py`.
- **Windows**: `C:\Users\muski\mlb_props`, history in `outputs\history\`,
  daily tasks `MLB_hot_hits` 11:30, `MLB Pitcher Plays` 11:35,
  `MLB_game_markets` 11:40, `MLB_game_markets_evening` 16:35 (all daily, ET).
  Expected untracked Windows files: `New Text Document.txt`, `logs/` (now
  gitignored via 99f53b5), `run_hot_hits_task.ps1` — leave them alone.
- **Docs to read first**: `README.md` (pitcher objective, assumptions, checkpoints),
  `docs/PITCHER_PROPS_HANDOFF.md`, `docs/HOT_HITS_HANDOFF.md`, this file.
- **Prior graded evidence**: `.analysis/schema6/pitcher_schema6_report.md`,
  `.analysis/first_hunt/` (unders segment derivation behind the card).
