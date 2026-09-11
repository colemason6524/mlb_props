# Pitcher Props Continuation Prompt

Copy and paste the prompt below into a fresh agent conversation.

---
You are taking over development and research for the pitcher-props side of this repository:

```text
/Users/colemason/mlb_props
```

Primary focus: MLB pitcher strikeout props. Hot Hits shares this repository but has a separate handoff. Do not change Hot Hits unless the task requires shared infrastructure and you explain the overlap.

Before making code changes:

1. Read `README.md` fully, especially:
   - Pitcher Props Objective And Design
   - Forecast board (production entry point)
   - Daily grading
   - Current scoring inputs
   - Current assumptions
   - Handoff Notes
2. Read `docs/PITCHER_PROPS_HANDOFF.md` fully.
3. Inspect `git status -sb`, the active branch, and recent commits. Preserve unrelated `.gitignore`, tier-comment, Hot Hits, transferred-history, log, and analysis work.
4. Inspect:
   - `run_forecast_pipeline.py` (production entry point)
   - `run_forecast_board.py` (board builder)
   - `grade_forecast_board.py` (daily grader)
   - `run_nightly.py`
   - `backtest.py`
   - `mlb_props/screener.py`
   - `mlb_props/tiers.py`
   - `mlb_props/pitcher_confidence.py`
   - `mlb_props/pitcher_presentation.py`
   - `mlb_props/opportunity.py`
   - `mlb_props/recency_shadow.py`
   - `mlb_props/output.py`
   - `mlb_props/models.py`
   - `mlb_props/version.py`
   - `mlb_props/sources/`
   - pitcher task wrappers and relevant tests
5. Inspect Azure VM directly with `ssh azure`; do not copy logs/history to Mac unless useful.

Current deployed state as of 2026-09-11:

- production commit `2bdeeb6`
- history schema `8`
- active model `pitcher-k-hybrid-v2`
- tiers `core-lean-watch-v2`
- confidence `pitcher-confidence-calibrated-v2`
- display `provisional-confidence-rank-v1`
- opportunity shadow `opportunity-shadow-v1`
- recency shadow `recency-shadow-v1`
- Azure VM: `ssh azure`, repo at `~/mlb_props`
- noon pipeline timer: 12:15 ET, afternoon: 16:45 ET, grader: 06:00 ET
- First noon pipeline succeeded 2026-09-11, sent Discord, exported history

Important model direction:

- Core remains strict; Lean/Watch remain broader learning tiers.
- `Best Available` never promotes a Lean/Watch play to Core.
- `Signal balance` is an internal additive diagnostic, not probability.
- confidence is calibrated (v1, shrink 0.55, capped 57%), price-independent, and cannot be described as EV or profitability.
- current L5 influence may be too strong for strikeout outcomes, but recent workload/opportunity remains important.
- `recency-shadow-v1` tests aggregate K/BF weighted 50% season, 30% L10, 20% L5 and BF/out weighted 60% L5, 40% season.
- shadows do not affect production scoring, qualification, tiers, terminal, or Discord.
- player IDs plus current slate/lineup context are the chosen trade-deadline safeguard; no frequent roster polling is planned.
- FanDuel is the primary line source; DraftKings remains diagnostic.

Current evidence state:

- Azure VM has the production pipeline: noon/afternoon board + daily grader.
- Windows has 12 schema-6 snapshots from August 5–17 (historical, retired).
- The forecast board produces pitcher K + game ML/RL/totals from fitted engines.
- Board grading settles priced PENDING ROI rows and posts Discord recap.
- Do not tune production from raw samples without graded evidence.

Your first task is read-only analysis:

1. Verify the current Azure timers, repo commit, board artifacts, and latest logs.
2. Grade the board history with the daily grader, preserving point-in-time integrity.
3. Compare pitcher K accuracy, game ML/RL/totals calibration, and priced ROI.
4. Report what worked, what did not, sample limitations, and whether any model component deserves adjustment.
5. Do not change tiers or engines until you demonstrate the evidence and receive approval.

Operational note: on August 16, Tennis Abstract, Bovada, Discord, ESPN, and MLB HTTPS requests all timed out during the morning. The 3 PM tennis run and both August 17 MLB tasks succeeded. Treat it as a one-off unless it recurs. If it recurs, recommend bounded HTTP/Discord retries, cache fallback, recovery scheduling, and PowerShell traceback logging repair.

Azure VM operational notes:
- Connection: `ssh azure` (alias in `~/.ssh/config`)
- Timers: noon 12:15 ET, afternoon 16:45 ET, grader 06:00 ET
- Runner: `~/mlb_props/scripts/run_linux_task.sh <task>`
- Secrets: `~/.config/mlb_props/env`
- Health pass: `ssh azure 'systemctl --user list-timers --all | grep mlb'`

Before any later implementation, explain the proposed isolated change, validation method, version bump requirements, and how production behavior will remain auditable. Use named-file staging, run the full test suite with `PYTHONPYCACHEPREFIX=.pycache`, and never use `git add .` in this mixed worktree.

Start by summarizing your verified understanding and the current board/grading state. Do not modify production logic until that summary is accurate.

---
