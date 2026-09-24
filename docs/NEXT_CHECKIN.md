# Current Project Checkpoint — 2026-09-24

This is the authoritative current-status and continuation note. For live timer
definitions and VM commands, see `docs/AZURE_VM_OPERATIONS.md`. Older dated
Windows checkpoints in the README and handoffs are historical; do not follow
them as current operational instructions.

## Operating model

- **Mac:** edit code and docs, run tests, commit and push changes, and perform
  season analysis. Preserve pre-existing work in the shared checkout.
- **Azure VM (`ssh azure`):** production collection, board publication, and
  grading. Deploy with `git pull --ff-only`; do not run the scheduled grader on
  the Mac.
- **Windows:** retired. Windows Task Scheduler instructions in older docs are
  archive material only.
- **Current verified deployed implementation:** Mac and VM included implementation
  commit `b7d5167` before this documentation refresh. Documentation commits may
  advance `main`; always verify `git log` and `git status` before work.
- **Pre-existing work to preserve:** on the Mac, modified
  `scripts/fit_batter_engine.py` and `scripts/fit_pitcher_engine.py` plus
  untracked tmux/systemd migration scripts. The VM also has pre-existing
  untracked tmux scheduler files. Do not stage, delete, or re-enable these as
  part of unrelated work.

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

## Current grading/research workflow

Each successful daily grade writes:

- `outputs/grades/forecast_board_<date>.json`
- `outputs/grades/learning_review_<date>.json`
- `outputs/grades/learning_review_<date>.md`

The review includes side, price, EV flag, model/market gap, pitcher workload vs.
strikeout conversion, historical team situation, exact-input join/price audit,
and noon/afternoon movement. It grades the full board; there is no new selection
filter or family cut.

Implementation commits deployed for this work:

- `7c09f3b` — full pitcher boxscore line, team situation, daily review
- `22d730b` — older ledger context fallback from board artifacts
- `a156151` — pitcher projection context from the day's source export
- `9b349b4` — exact input joins and noon/afternoon movement
- `b7d5167` — separate source market-probability availability from stored-value validation

Validation: 318 tests and 12 subtests passed locally; focused grader tests passed
on Azure. The Sep 22 VM review found 57 paired market snapshots, with 52
same-line probability comparisons. It joined 63/65 rows to exact inputs; all 63
available source prices matched. The two unmatched rows were unpriced moneylines.
The older boards predate persisted `market_p`, so their source probabilities can
be reconstructed but not validated against a stored value. New boards persist
it for future validation.

## Evidence so far and interpretation

The Sep 10–22 full-board baseline is 448 decided plays, 230–218, −43.9 units
(−9.8% ROI). The descriptive study suggests that large model-versus-market
probability gaps were overconfident and that pitcher-K losses in this window
were more associated with strikeout conversion than short workload. A
contending-team versus contending-team pitcher slice was also weak.

These are hypotheses, not proven selection rules: the window is short, feature
tests do not survive multiple-comparison correction, and no families or plays
have been cut. **Keep collecting the full board through the regular season;
do not tune production selection or model probabilities from this window alone.**

The detailed analysis is in `evidence/research/FINDINGS.md` and is gitignored;
that file is a local artifact, not the durable handoff. Recreate/pull research
artifacts when needed using `scripts/analyze_sep_window.py` and the VM outputs.

## Next events and review

The MLB schedule currently has regular-season games through **Sunday,
2026-09-27**, no games Sep 28, and postseason games beginning Sep 29.

1. Let the existing Azure timers collect and grade the remaining regular-season
   slates; do not manually rerun production pipelines or alter picks.
2. After Sep 27 is graded on Sep 28, pull that date's grade JSON and learning
   review artifacts to `outputs/grades/` on the Mac. Pull other dates as useful
   for the season review.
3. Reassess the Sep 10–27 sample against the current baseline: calibration,
   market disagreement, pitcher workload/conversion, team context, and
   same-line market movement. Keep hypotheses distinct from confirmed results.
4. Treat playoff games as a separate evaluation period; do not blend their
   results into the regular-season sample without an explicit era label.

Pull review artifacts from the Mac with:

```bash
mkdir -p outputs/grades
scp 'azure:~/mlb_props/outputs/grades/forecast_board_*.json' outputs/grades/
scp 'azure:~/mlb_props/outputs/grades/learning_review_*.json' outputs/grades/
scp 'azure:~/mlb_props/outputs/grades/learning_review_*.md' outputs/grades/
```

## Safe continuation checklist

1. Read this file and `docs/AZURE_VM_OPERATIONS.md` first. Read the pitcher or
   Hot Hits handoff only for that subsystem's model/design details.
2. Inspect `git status -sb`, recent commits, active timers, latest board/grade
   artifacts, and task logs before diagnosing production.
3. Make and test changes locally. Stage named files only; preserve unrelated
   work listed above. Do not use `git add .`.
4. Commit and push intentionally, then fast-forward the VM with
   `ssh azure 'cd ~/mlb_props && git pull --ff-only'`.
5. Verify the scheduled or explicitly requested VM run and pull its output back
   to the Mac. Do not change selection policy without a separately agreed,
   pre-registered evidence plan.
