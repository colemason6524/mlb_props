# Current Project Checkpoint — 2026-09-28

This is the authoritative current-status and continuation note. For live timer
definitions and VM commands, see `docs/AZURE_VM_OPERATIONS.md`. Older dated
Windows checkpoints in the README and handoffs are historical; do not follow
them as current operational instructions.

**Era note:** the 2026 regular season ended 2026-09-27 and the postseason begins
2026-09-29 (2026-09-28 has no games). The regular season has been closed as an
evidence sample and frozen. Read, in order:

1. [`SEASON_2026_RECAP.md`](SEASON_2026_RECAP.md) — frozen record of what the
   regular season taught us. Never retro-edited.
2. [`POSTSEASON_2026_PLAYBOOK.md`](POSTSEASON_2026_PLAYBOOK.md) — how that
   evidence is used from 2026-09-29 onward.

The governing direction: **the regular season is the basis for the postseason
until there is enough postseason context of its own; postseason results never
revise the regular-season sample; and we display postseason output at full
confidence rather than adopting a cautious shadow posture.**

## Operating model

- **Mac:** edit code and docs, run tests, commit and push changes, and perform
  season analysis. Preserve pre-existing work in the shared checkout.
- **Azure VM (`ssh azure`):** production collection, board publication, and
  grading. Deploy with `git pull --ff-only`; do not run the scheduled grader on
  the Mac.
- **Windows:** retired. Windows Task Scheduler instructions in older docs are
  archive material only.
- **Current verified deployed implementation:** Mac and VM are at the same HEAD
  as of 2026-09-28 (era-labeling work, the season recap and postseason playbook,
  and their documentation corrections). Always verify `git log --oneline -3` and
  `git status` on both machines before work; do not trust a hash copied into a
  document.
- **Pre-existing work to preserve:** on the Mac, modified
  `scripts/fit_batter_engine.py` and `scripts/fit_pitcher_engine.py` plus
  untracked tmux/systemd migration scripts and the untracked `.codewhale/` and
  `.dashboard/` directories. The VM also has pre-existing untracked tmux
  scheduler files. Do not stage, delete, or re-enable these as part of unrelated
  work.
- **Era work status:** committed and deployed. `grade_forecast_board.py`,
  `mlb_props/version.py`, `scripts/analyze_sep_window.py`,
  `tests/test_grade_forecast_board.py`, and the new `docs/SEASON_2026_RECAP.md`,
  `docs/POSTSEASON_2026_PLAYBOOK.md`, `docs/NEXT_CHECKIN.md` are all on `main`
  and fast-forwarded onto the VM. The era labeling is reporting-only and never
  changes selection, so nothing about this deployment can alter a pick.

## Production schedule

All times are America/Detroit. Timers are user-level systemd units on Azure:

| Timer | Time | Task |
|---|---:|---|
| `sports-mlb-grade-board.timer` | 06:00 | Grade the prior date, settle the ledger, post recap, write the learning review |
| `sports-mlb-pipeline-noon.timer` | 12:15 | Collect fresh inputs and publish the full remaining slate |
| `sports-mlb-pipeline-afternoon.timer` | 16:45 | Collect fresh inputs and publish the remaining pregame slate |

The noon board is the early snapshot; the latest valid pregame snapshot is the
canonical graded play. For markets present in both, the learning review records
noon-to-afternoon movement. This is **market movement, not closing-line value**.
The grader is `grade_forecast_board.py`, invoked by
`scripts/run_linux_task.sh grade-board`; no separate grader schedule is needed.

**The timers keep running through the postseason.** A playoff date with no
`forecast_board_*` artifact is a process incident, not a quiet day. See Rule 4 of
the playbook.

## Current grading/research workflow

Each successful daily grade writes:

- `outputs/grades/forecast_board_<date>.json`
- `outputs/grades/learning_review_<date>.json`
- `outputs/grades/learning_review_<date>.md`

The review includes side, price, EV flag, model/market gap, pitcher workload vs.
strikeout conversion, historical team situation, exact-input join/price audit,
and noon/afternoon movement. It grades the full board; there is no new selection
filter or family cut. As of the era work above it also prints
`Evidence era: <regular_season|postseason|between>`, derived from
`mlb_props/version.py::evidence_era()`.

Implementation commits deployed for this work:

- `7c09f3b` — full pitcher boxscore line, team situation, daily review
- `22d730b` — older ledger context fallback from board artifacts
- `a156151` — pitcher projection context from the day's source export
- `9b349b4` — exact input joins and noon/afternoon movement
- `b7d5167` — separate source market-probability availability from stored-value validation

Validation baseline: 320 tests pass locally (`python3 -m unittest discover -s
tests`, plus focused grader tests on Azure). The Sep 22 VM review found 57 paired
market snapshots, with 52 same-line probability comparisons. It joined 63/65 rows
to exact inputs; all 63 available source prices matched. The two unmatched rows
were unpriced moneylines. Older boards predate persisted `market_p`, so their
source probabilities can be reconstructed but not validated against a stored
value. New boards persist it for future validation.

## Evidence: regular season is closed, postseason is open

**Frozen regular season (2026-09-10 → 2026-09-27). COMPLETE as of
2026-09-28:** 712 decided plays, **356–356 (50.0%)**, **−88.27 units over 701
priced plays (−12.6% ROI)**, mean model probability 0.586 against a mean market
probability 0.532, with Brier 0.2612 (model) vs 0.2428 (market) and log loss
0.7189 vs 0.6785. 729 canonical rows = 712 decided + 3 pushes + 14 voids across
**18 graded dates**. The full eight findings, with denominators and caveats, are
in [`SEASON_2026_RECAP.md`](SEASON_2026_RECAP.md).

Load-bearing points for the postseason:

- Large claimed edges are the loss driver: the 5–10pt and >10pt gap bands are
  **−69.07u of −88.27u on 356 of 712 plays** (50% of volume, 78% of damage),
  overconfident by 17–22 points.
- The EV flag is inverted in-sample; do **not** gate on it.
- Pitcher K is a strikeout-*conversion* problem, not a workload problem, in this
  window (`conversion_cold` −71.44u on 78 vs `workload_short` −0.24u on 20).
- Contending-vs-contending pitcher plays are the worst slice (**n=140, 61–79,
  43.6%, −28.79u, −20.6% ROI** — 70% of pitcher-K volume, 75% of the family's
  loss). **Every postseason game is that slice**, so the seasonal −12.6% ROI is a
  ceiling, not a baseline. (Caveat: team status is recorded on pitcher_k rows
  only in this sample.)
- **Pitcher K lines at 3.5 or below are 29% of the family's volume and 66% of
  its loss** (17–40, −25.56u on 57). Same population as `projected_k_rate < .180`
  (33.9%, −21.79u on 56). This is the cleanest actionable cut in the study.
- Matchup variables are the only monotone, favorably-signed separator class:
  `matchup_rating` positive 55.9% / +0.78u vs negative 39.5% / −23.68u;
  `opponent_k_rate_vs_hand` ≥.230 → 52.7%. The boolean flags are all weak
  (every q ≥ 0.68).
- **The top-ranked signals in the FDR screen are the market price itself**
  (`moneyline.price_a` p=0.0038; no-vig shadow probabilities p=0.0058–0.0059).
  Minimum q improved from 0.978 (13-day window) to **0.219** (full window), but
  **no slice yet survives FDR at q<0.05**; 18 graded days is still a small window.

**Do not tune production selection or model probabilities from this window
alone.** Nothing in the recap was promoted to a rule, and that was deliberate.

**Pre-registered Daily Unders Card — resolved and failed.** The one policy in
this project whose success rule was fixed before it ran (`daily-unders-card-v1`,
pre-registered 2026-08-31) has now run to its declared end: **38–40, 48.7%,
n=78** over 25 card-bearing snapshots (2026-09-01 → 09-27), **−5.45u at −110**
and **−9.15u** at collected prices against a **0.5560** average breakeven.
`48.7%` sits in the pre-declared `< 52.4%` kill band, and the card also lost to
simply taking every under in the same snapshots (**95–85, 52.8%, n=180**). The
card's own volume explains it: 47% of its plays came from pitchers projected
below a `.180` K rate (43.2%) and 22% from `line <= 3.5` (41.2%) — the two worst
buckets in the entire study. The `n >= 100` trust denominator was never reached
(n=78), so the kill rests on the point estimate, not a powered test.
**Do not carry the card into October as a staking policy.** Full write-up:
`SEASON_2026_RECAP.md` Finding 9; artifact
`evidence/grades/daily_card_summary.json`.

**Postseason (from 2026-09-29).** Governed by
[`POSTSEASON_2026_PLAYBOOK.md`](POSTSEASON_2026_PLAYBOOK.md): era-stamped
reporting with no blending, matchup evidence first, non-strikeout-heavy pitchers
and short-leash starters get explicit caution, and no shadow posture — output is
displayed at full confidence for a single reader.

## Next events and review

1. ~~Pull the 2026-09-23 → 2026-09-27 grade artifacts and re-run the study over
   the full window so the recap headline becomes the true final season figure.~~
   **DONE 2026-09-28.** VM SSH restored; `outputs/` rsynced (187 files, no
   `--delete`); study re-run over 09-10 → 09-27 → 729 rows. The recap and this
   file now carry the complete figures.
2. ~~Confirm the actual postseason opener against the live schedule.~~ **DONE
   2026-09-28.** Verified via `statsapi.mlb.com` schedule API: Wild Card opens
   **2026-09-29** (PHI@ATL, CWS@HOU, BOS@NYY, CHC@SD; `gameType=F`), Division
   Series from 2026-10-03 (`gameType=D`). `POSTSEASON_2026_START` is correct as
   written; no code change.
3. ~~Resolve the pre-registered Daily Unders Card gate.~~ **DONE 2026-09-28.**
   `scripts/consolidate_history.py` (201 manifest dates, 68 pitcher dates) then
   `scripts/grade_daily_card_full_season.py` → **38–40, 48.7%, n=78**, below the
   pre-registered `< 52.4%` kill line. The always-under baseline (**52.8%,
n=180**) was computed manually this session because the standing grader reports
   `baseline_rows 0`; see Finding 9 and the new playbook open item.
4. ~~Commit and deploy the era work.~~ **DONE 2026-09-28.** Pushed and
   fast-forwarded onto the VM; confirm with `git rev-parse --short HEAD` **on the
   VM** rather than trusting a hash written here. Commits of record: `a9f1ade`
   (era labeling), `9251671` (season recap + postseason playbook), then
   documentation corrections. **Remaining half:** verify one playoff-day cycle
   end-to-end — pipeline runs → board written → grader settles it → the learning
   review reads `Evidence era: postseason`. Time-gated: no games 2026-09-28; the
   Wild Card opens 2026-09-29 and the grader timer settles it at 06:00 ET
   2026-09-30.
   **Baseline note (do not misread as a defect):**
   `outputs/grades/learning_review_2026-09-27.md` has **no** era line and its
   `learning` dict has no `era` key. That is correct — it was written
   2026-09-27T10:00:01Z by the 06:00 ET timer, roughly 5.5 hours *before* the era
   work reached the VM. The deployed path is proven correct on the same rows,
   non-mutating, using the production call shape
   (`build_learning_review(rows, board_context, screen)`,
   `grade_forecast_board.py:1093`): `era=regular_season`, `decided=50` (equal to
   the artifact's own `learning.decided`), and the rendered markdown contains
   `Evidence era: regular_season (regular season and postseason are never
   blended)`. The first era-labeled production review is therefore the one
   covering the first graded postseason slate.
5. **Pre-register the pitcher-K line rule** (playbook Open item 5) before
   2026-09-29: exclude `line ≤ 3.5` K plays or require a favorable
   `matchup_rating` override; baseline to beat is −25.56u on 57 plays. Finding 9
   is independent corroboration — the Daily Card failed by routing volume into
   exactly that bucket.
6. **Do not blend postseason results into the regular-season sample.** Promotion
   from "regular season is the basis" to "postseason is its own basis" follows
   only the pre-registered triggers in Rule 3 of the playbook.
7. Optional, not yet done: stamp `era` into the `grade_screen` result dict of
   `forecast_board_<date>.json`, not just the learning review, so the artifact is
   self-describing.
8. **Known production behavior — an off-day run is recorded as `failed`.**
   **Observed 2026-09-28.**
   `run_forecast_board.required_family_errors` (`run_forecast_board.py:687`)
   refuses to publish when any required family is empty, and it does **not**
   distinguish *"MLB has no games this date"* from *"the sources returned
   nothing"*. The 2026-09-28 noon slot (zero games) fired at
   `2026-09-28T16:15:03Z`, exited 1 (`ExecMainStatus=1`), and recorded the
   identical message to the 2026-09-27 source failure in
   `outputs/run_status.json` — even though its collectors reported `statuses`
   both `ok` and simply returned a legitimately empty slate (`row_count: 0`).
   The 2026-09-27 afternoon failure (`2026-09-27T20:45:17Z`) had games, already
   started. **The two are indistinguishable in the artifact**, which is why the
   gate cannot simply be removed. The grader does **not** mirror this:
   `grade_screen("2026-09-28")` returns `learning={}`, writes no review, and
   records `success`. Today's afternoon slot (16:45 ET) is expected to repeat the
   board failure; every postseason off day will too.
   **Decision pending: skip-and-succeed on an empty slate, or keep failing
   closed.** No picks are affected — 2026-09-29's first game is 18:00Z, so the
   noon slot still collects fresh pregame lines. Deliberately not changed without
   that decision.

Pull review artifacts from the Mac with the verified SSH form (the `azure`
config alias was never confirmed; the literal host and key are):

```bash
rsync -a --stats \
  -e "ssh -i /Users/colemason/Downloads/RunThemScripts_key.pem -o BatchMode=yes -o ConnectTimeout=15" \
  azureuser@130.131.0.6:mlb_props/outputs/ outputs/
```

## Safe continuation checklist

1. Read this file and `docs/AZURE_VM_OPERATIONS.md` first, then
   `docs/SEASON_2026_RECAP.md` and `docs/POSTSEASON_2026_PLAYBOOK.md`. Read the
   pitcher or Hot Hits handoff only for that subsystem's model/design details.
2. Inspect `git status -sb`, recent commits, active timers, latest board/grade
   artifacts, and task logs before diagnosing production.
3. Make and test changes locally. Stage named files only; preserve unrelated
   work listed above. Do not use `git add .`.
4. Commit and push intentionally, then fast-forward the VM with
   `ssh azure 'cd ~/mlb_props && git pull --ff-only'`.
5. Verify the scheduled or explicitly requested VM run and pull its output back
   to the Mac. Do not change selection policy without a separately agreed,
   pre-registered evidence plan — and never retro-edit the regular-season record.
