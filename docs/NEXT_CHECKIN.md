# Next Check-in (written 2026-09-04)

Purpose: make the next "get to work" session cheap. Verified live against the Mac
checkout and the Windows production box on 2026-09-04. Goal remains a winning
baseball model with graded Core/Lean P&L + CLV — not pipeline busywork.

## 1. Status one-liners

- **Hot Hits**: GRADED 2026-09-04 on all 33 retained Windows exports (8/1 → 9/4).
  Delivered cards: 28/46 legs (60.9%) — Core 14/19 (73.7%), Value 14/27 (51.9%);
  void-adjusted full card 11/26 (42.3%) vs all-Core 9/14 (64.3%). Confidence pool
  (n=2954): observed 61.9% vs forecast 67.1% (overconfident ~5pt), Brier 0.238
  ≈ base-rate-only 0.236 (no edge); current-gate pass 62.9% vs excluded 61.6% (no
  separation). Leave-off: `.analysis/hot_hits_grade_20260904.md`. No production
  change made.
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
| Hot Hits | GRADED 2026-09-04 (delivered policy + confidence calibration, 33 exports 8/1→9/4, n=46 delivered legs / 2954 graded pool rows): Core 14/19 (73.7%), Value 14/27 (51.9%), all-Core parlays 9/14 (64.3%) vs full card 11/26 (42.3%); confidence overconfident ~5pt, Brier 0.238 ≈ base-rate 0.236, L5 gate pass/fail shows no separation. Next: decide (with direction) whether a Core-only default card policy version is warranted once September delivered legs reach n≈25–30; confidence re-leveling is a separate research-only change candidate. | Re-pull Windows `hot_hits_*` and re-grade at the next weekly sync (≥ 2026-09-11); production/card change only after that grade and only as a new policy version commit. |
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
- **Prior graded evidence**: `.analysis/hot_hits_grade_20260904.md` (2026-09-04 Hot
  Hits delivered grade + confidence calibration; full report under
  `.analysis/hot_hits_windows_20260904/`),
  `.analysis/schema6/pitcher_schema6_report.md`,
  `.analysis/first_hunt/` (unders segment derivation behind the card).

## 7. 2026-09-08 — Daily Unders Card relabeled RESEARCH ONLY (rendering only)

- Sep 1–7 card grade: **5–10, −6.41u** at recorded FanDuel prices, below the
  always-under baseline (48.6%). The pre-registered evaluation continues to
  n=100 (success rule unchanged); nothing about selection/gates changed.
- Terminal (`render_daily_card`) and Discord (`_daily_card_embed_field`) blocks
  now carry "Daily Unders Card — RESEARCH ONLY — not a play; tracking to n=100
  (currently 5-10, -6.41u through 2026-09-07)" plus a "no stake is recommended"
  footer. The header no longer says "Core" at all (previously "not Core"), and
  "plays" wording was replaced by "rows". Label lives in
  `mlb_props/daily_card.py` (`daily_card_research_label`, snapshot
  `DAILY_CARD_RUNNING_RECORD`) — **refresh the snapshot at each weekly grade**
  from `pitcher_grading.daily_card_summary` (the function also accepts that dict).
  No `outputs/daily_unders/summary.md` exists on Mac or VM, so there is no live
  read; the record is a hand-maintained constant.
- `mlb_props/version.py` was deliberately **not** bumped
  (`PITCHER_DISPLAY_POLICY_VERSION` convention would call for it): that file is
  locally modified on both Mac and VM by the in-flight game-markets migration
  and touching it would block the VM `git pull --ff-only`. Bump to
  `provisional-confidence-rank-v2` (or add a card display version) in the
  migration commit.
- The "No Discord polish" rule in §4 was overridden for this one change on
  direction, because the block could be read as a play while negative.
- Tests: `tests/test_daily_card.py::DailyCardResearchLabelTests` (210 total).

## 6. Infrastructure change (2026-09-08): Windows retired, Azure VM active

Daily MLB collection moved from the Windows desktop to the Azure VM
(`ssh azure`). All four scheduled tasks now run as systemd user timers
(11:19/11:37/11:53/16:31 ET, timezone-explicit America/Detroit in OnCalendar).
Setup was validated 2026-09-08: hot-hits dry run clean (Savant 124/124),
markets unit E2E exit 0 with 14/14 Bovada coverage, webhooks reachable, no
double-scheduler (tmux experiment disabled). First scheduled fires are
2026-09-08. See `docs/AZURE_VM_OPERATIONS.md` for units, health pass, pull
commands, and VM hygiene. Windows-specific sections above are historical.

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
  candidates + top-40 research profiles. Validated live on the VM: 33/51
  candidates priced, 144 quotes. Fail-open; env kill-switches
  `HOT_HITS_INCLUDE_HIT_PRICES=false` and `HOT_HITS_HIT_PRICE_RESEARCH_LIMIT`.
  `hot_hits_report.py` reports priced leg P&L for delivered cards.
- Bovada event-coupon quirk: same endpoint alternates dict/list payload
  shapes (4430c8c).
- Game-markets schema-2 work (Action/FanDuel fallback) landed from the VM as
  d2dfeed; VM synced to 4430c8c.
