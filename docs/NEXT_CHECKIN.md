# Next Check-in (written 2026-09-11)

Purpose: make the next "get to work" session cheap. Verified live against the Mac
checkout and the Azure VM on 2026-09-11. Goal remains a winning baseball model
with graded Core/Lean P&L + CLV — not pipeline busywork.

## 1. Status one-liners

- **Forecast Board**: LIVE on Azure VM. Noon pipeline (12:15 ET) and afternoon
  pipeline (16:45 ET) both completed successfully. Board renders three readable
  sections — Moneyline, Run Line, Totals — with human-readable team names and
  correct away run-line sign (`+1.5` not `-1.5`). Commit `2bdeeb6` deployed
  2026-09-11. 295 tests pass.
- **Board Grading**: LIVE on Azure VM. 6:00 AM ET daily grader settles priced
  PENDING ROI rows and posts Discord recap. First grade (2026-09-08): pitcher K
  38-24 (61.3%), +4.00u across 61 priced plays. Second grade (2026-09-09):
  9-11 (45.0%), -4.45u across 20 priced plays.
- **Hot Hits**: Production on Windows (retired). Windows history last verified
  2026-08-21 (15/16 runs, 170 production candidates, 1,649 research profiles).
  Windows-specific sections in the handoff are historical. The next Hot Hits
  review should pull Windows exports and grade the retained sample.
- **Pitcher props**: Pipeline healthy on Azure — 9/11 first noon run: 13 slate
  games, pitcher K + game markets collected, board published to Discord. Schema 8,
  all 8/31 version pins live: `pitcher-k-hybrid-v2`, `core-lean-watch-v2`,
  `pitcher-confidence-calibrated-v2`.
- **Daily Unders Card**: `daily-unders-card-v1` (pre-registered 8/31) delivering
  daily under the September validation window. Success rule: ≥55% at n≥100 graded
  plays → trusted; 52.4–55% → marginal (needs price EV check); <52.4% → killed.
  At ~4.4 plays/day, n≈100 lands early October. No September grade exists yet.
- **Core gates (`core-lean-watch-v2`)**: rebuilt 8/31 on graded August evidence
  (old Core was 2-13). UNDER-only, edge cap 1.5, ≥0.55 no-vig market probability
  when priced. Awaiting its own prospective sample — do not touch until graded.
- **Game-market shadow**: collector healthy — 13 slate games on 9/11 with Bovada
  primary + Action/FanDuel cross-check. History exported to VM.

## 2. Next actions, with WHEN

| Stream | Action | WHEN / trigger |
| --- | --- | --- |
| Forecast Board | Monitor noon/afternoon pipeline runs on Azure. Verify Discord delivery. | Daily — automatic via systemd timers. |
| Board Grading | Pull graded recap from Discord. Verify ROI ledger settles correctly. | Daily — automatic at 6:00 AM ET. |
| Hot Hits | Pull Windows `hot_hits_*` exports and grade the retained sample. | Next weekly sync (≥ 2026-09-18). |
| Pitcher props / Daily Card | Pull VM history, run `python3 backtest.py --all-history --include-watch`. | First pull after ~9/18 (≈1 week of September card); formal success-rule check when n≥100 graded card plays (≈ early Oct). |
| Core v2 gates | Grade new-gate Core/Lean sample (~50–100 resolved candidates). | Late September, after the grade — never before. |
| Card v2 no-vig gate | Evaluate `daily-unders-card-v2` only once ~2 weeks of priced rows exist. | Priced rows started 8/31; evaluate ≈ 2026-09-14. |
| Game markets | Build the line-movement report (morning vs evening snapshots, starter-change detection via probable-pitcher diffs). | After two full weeks of collection: ≥ 2026-09-22. |

## 3. What to check on pop-back (5-minute health pass)

1. `ssh azure 'systemctl --user list-timers --all | grep mlb'` — all three timers active.
2. `ssh azure 'tail -4 ~/mlb_props/logs/forecast_pipeline_noon_task.log ~/mlb_props/logs/forecast_pipeline_afternoon_task.log'` — both exit 0.
3. `ssh azure 'ls -t ~/mlb_props/outputs/forecast_boards/ | head -4'` — fresh board files.
4. `ssh azure 'tail -4 ~/mlb_props/logs/grade_board_task.log'` — grader exit 0.
5. Board artifact contains `Moneyline`, `Run Line`, `Totals` sections with team names.
6. Empty Core / empty Card is a valid outcome — verify it is source coverage before reading anything into it.

## 4. What NOT to do

- **No live Core retune.** `core-lean-watch-v2` was rebuilt 8/31 from graded
  evidence and needs its own prospective sample; changes wait for the grade.
- **No Discord polish.** No formatting/embed/limit changes on either channel.
- **Do not recreate or edit healthy systemd timers.** All three timers are correct
  and tested. Do not re-enable the disabled tmux scheduler.
- **Do not redo the Cole 8/31 card.** `daily-unders-card-v1` is pre-registered
  and frozen; changing gates requires a new policy version in a separate commit.
- **No pipeline busywork.** Every change should serve graded P&L / CLV evidence.
- **No `git add .`** in this shared checkout — stage named files only.
- Do not tune from abnormal slates (8/16-style outages, tiny slates, late runs,
  thin coverage) and do not count DNP legs as ordinary Hot Hits misses.

## 5. Leave-off pointer

- **Mac HEAD**: `2bdeeb6` (= `origin/main` at write time). Fixes since 8/31:
  `c25389d` Bovada empty `{}` treated as fresh coupon, `7eb8e98` drop
  `preMatchOnly`, `d0fe13e` atomic noon/afternoon pipeline, `476e830` pre-stage
  fresh-export snapshot, `8b7c5c5` daily grader, `2bdeeb6` split board sections
  with readable team names.
- **Azure HEAD**: `2bdeeb6` (fast-forwarded from Mac via `git pull --ff-only`).
- **Key files**: `run_forecast_pipeline.py` (production entry), `run_forecast_board.py`
  (board builder + rendering), `grade_forecast_board.py` (daily grader),
  `mlb_props/forecasting/game_runs.py` (game engine), `mlb_props/forecasting/pitcher_k.py`
  (pitcher engine), `mlb_props/sources/bovada_mlb.py` (Bovada game lines),
  `mlb_props/sources/action_network.py` (FanDuel fallback).
- **Azure VM**: `ssh azure`, repo at `~/mlb_props`, timers at 12:15/16:45/06:00 ET.
  Secrets in `~/.config/mlb_props/env`. Runner: `scripts/run_linux_task.sh`.
- **Docs to read first**: `README.md`, `docs/NEXT_CHECKIN.md`,
  `docs/AZURE_VM_OPERATIONS.md`, `docs/PITCHER_PROPS_HANDOFF.md`.
- **Prior graded evidence**: 2026-09-08 board grade (38-24, +4.00u), 2026-09-09
  board grade (9-11, -4.45u). Both recaps posted to Discord. ROI ledger settled.

## 6. Infrastructure change (2026-09-08 → 2026-09-11): Windows retired, Azure VM active

Daily MLB collection moved from the Windows desktop to the Azure VM
(`ssh azure`). Three systemd user timers run daily:

| Timer | When (ET) | Task |
| --- | --- | --- |
| `sports-mlb-pipeline-noon.timer` | 12:15 | collect fresh inputs + publish full slate |
| `sports-mlb-pipeline-afternoon.timer` | 16:45 | collect fresh inputs + publish remaining pregame |
| `sports-mlb-grade-board.timer` | 06:00 | grade prior day + settle ROI + post recap |

See `docs/AZURE_VM_OPERATIONS.md` for units, health pass, pull commands, and VM hygiene. Windows-specific sections in other handoffs are historical.

## 7. Pricing shadow + P&L convention (2026-09-09, post-audit decisions)

Decisions taken after the 2026-09-08 portfolio audit (MLB section):

- **Daily Unders Card rides its pre-registered rule to n≈100** (~mid-Oct).
  September priced record: 5-10, **-6.41u at collected prices** (avg breakeven
  57.4%; the -110-flat figure -5.45u is secondary). The card's Discord label
  is RESEARCH ONLY (c74b109). The 9/8 card (4 plays) was pending at the
  09-09 morning regrade; it resolves on the next pull+backtest.
- **Standing P&L convention: units at collected prices.** `backtest.py` card
  section and `pitcher_grading.daily_card_summary` now report priced units
  from saved price_shadow (`priced_units`, `priced_n`, `priced_avg_breakeven_rate`);
  `units_at_minus_110` is retained as the pre-registered rule's basis.
- **Hot Hits pricing shadow built and live** (`hot-hits-price-shadow-v1`,
  commit 49aa78a + 4430c8c): single-sided Bovada YES prices for "Player to
  record a Hit" plus the 2+ Hits alt line, fetched per event via the
  event-scoped coupon (`sources/bovada_props.py`), attached to production
  candidates + top-40 research profiles. Fail-open; env kill-switches
  `HOT_HITS_INCLUDE_HIT_PRICES=false` and `HOT_HITS_HIT_PRICE_RESEARCH_LIMIT`.
  `hot_hits_report.py` reports priced leg P&L for delivered cards.

## 8. Forecast board display format (2026-09-11)

The board renders four sections instead of one flat `game` block:

- **Pitcher Strikeouts**: over/under on posted K lines, sorted by model probability.
- **Moneyline**: team name for the selected side (home or away).
- **Run Line**: team name + selected-side spread (away lines flip from the stored
  home-side spread, e.g. `New York Mets +1.5`).
- **Totals**: `Over` or `Under` + line.

The model owns every pick; price never flips one. EV and ev_flag are display-only.
The board artifact retains the full JSON with `family`, `home_team`, `away_team`,
and `line` fields for grading and ledger use.
