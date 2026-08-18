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
   - Windows Task Scheduler: Pitcher Props
   - Windows Task Scheduler: Daily Pitcher Props Backtest
   - Current scoring inputs
   - Current assumptions
   - Handoff Notes
2. Read `docs/PITCHER_PROPS_HANDOFF.md` fully.
3. Inspect `git status -sb`, the active branch, and recent commits. Preserve unrelated `.gitignore`, tier-comment, Hot Hits, transferred-history, log, and analysis work.
4. Inspect:
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
5. Inspect Windows directly with `ssh windows`; do not copy logs/history to Mac unless useful.

Current deployed state as of 2026-08-17:

- production commit `8b23aab`
- history schema `6`
- active model `pitcher-k-situational-v1`
- tiers `core-lean-watch-v1`
- confidence `pitcher-confidence-provisional-v1`
- display `provisional-confidence-rank-v1`
- opportunity shadow `opportunity-shadow-v1`
- recency shadow `recency-shadow-v1`
- Windows repo `C:\Users\muski\mlb_props`
- task `MLB Pitcher Plays`, daily 11:35 AM, exporting history
- August 17 task succeeded, sent Discord, and exported history

Important model direction:

- Core remains strict; Lean/Watch remain broader learning tiers.
- `Best Available` never promotes a Lean/Watch play to Core.
- `Signal balance` is an internal additive diagnostic, not probability.
- confidence is provisional, price-independent, capped at 68%, and cannot be described as EV or profitability.
- current L5 influence may be too strong for strikeout outcomes, but recent workload/opportunity remains important.
- `recency-shadow-v1` tests aggregate K/BF weighted 50% season, 30% L10, 20% L5 and BF/out weighted 60% L5, 40% season.
- shadows do not affect production scoring, qualification, tiers, terminal, or Discord.
- player IDs plus current slate/lineup context are the chosen trade-deadline safeguard; no frequent roster polling is planned.
- FanDuel is the primary line source; DraftKings remains diagnostic.

Current evidence state:

- Windows has 12 schema-6 snapshots from August 5–17, with August 16 missing due a temporary machine-wide HTTPS/TLS incident.
- Those files contain 195 qualified candidates and 195 populated recency shadows.
- That is raw collected volume, not 195 resolved predictions.
- The newest Windows backtest is still through August 2, so the schema-6 shadow has not been graded.
- Do not tune production from the raw sample.

Your first task is read-only analysis:

1. Verify the current Windows task, repo commit, history files, and latest logs.
2. Grade the schema-6 history with Core, Lean, and Watch included, preserving point-in-time integrity.
3. Compare active versus recency-shadow K/BF bias and MAE, confidence Brier/calibration, L5 outcome bands, side, tier, workload reliability, edge, and flags.
4. Report what worked, what did not, sample limitations, and whether the shadow deserves continued observation.
5. Do not activate the shadow or change tiers until you demonstrate the evidence and receive approval.

Operational note: on August 16, Tennis Abstract, Bovada, Discord, ESPN, and MLB HTTPS requests all timed out during the morning. The 3 PM tennis run and both August 17 MLB tasks succeeded. Treat it as a one-off unless it recurs. If it recurs, recommend bounded HTTP/Discord retries, cache fallback, recovery scheduling, and PowerShell traceback logging repair.

Before any later implementation, explain the proposed isolated change, validation method, version bump requirements, and how production behavior will remain auditable. Use named-file staging, run the full test suite with `PYTHONPYCACHEPREFIX=.pycache`, and never use `git add .` in this mixed worktree.

Start by summarizing your verified understanding and the exact schema-6 grading plan. Do not modify production logic until that summary is accurate.

---
