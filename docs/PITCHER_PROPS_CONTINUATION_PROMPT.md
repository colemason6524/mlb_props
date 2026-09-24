# Pitcher Props Continuation Prompt

Copy the prompt below into a fresh agent conversation. Current repo/VM status
must be verified from `docs/NEXT_CHECKIN.md`, not inferred from old checkpoints.

---

You are continuing MLB pitcher-props research in `/Users/colemason/mlb_props`.
Hot Hits shares this repository but has a separate handoff. Read
`docs/NEXT_CHECKIN.md` first for the current operating state and immediate
project plan, then read `docs/AZURE_VM_OPERATIONS.md` for VM operations and
`docs/PITCHER_PROPS_HANDOFF.md` for pitcher model design.

Current operating rules:

- The Mac is the source-edit/test/commit/analysis machine. Azure (`ssh azure`)
  runs production collection, board publication, and daily grading via
  systemd. Windows is retired; do not follow Windows Task Scheduler commands in
  archived documentation.
- Deploy intentional changes by committing/pushing from the Mac and pulling on
  Azure with `git pull --ff-only`. Preserve unrelated working-tree changes.
- The grade review records full pitcher boxscore lines, historical team
  situation, exact source-input joins, and noon/afternoon market movement. That
  movement is descriptive, **not CLV**.
- The analysis window is Sep 10–22, 2026. Continue collecting the full board
  through the regular season. Do not introduce family-level or selection cuts;
  learn which individual model aspects repeat out of sample.
- Keep playoff games as a separately labeled period.

Before any code change:

1. Run `git status -sb`, inspect recent commits, and preserve unrelated edits.
2. Verify active model/version values in `mlb_props/version.py`.
3. Read the current learning-review artifact and verify its exact-input audit
   before interpreting a feature association.
4. State the proposed scope and validation plan. If it would alter production
   selection, probabilities, model inputs, or timers beyond an agreed plan,
   pause and ask first.
5. Add focused tests, run the full test suite, inspect the staged diff, and
   stage named files only. Never use `git add .`.

Useful files:

- `run_forecast_pipeline.py`: scheduled collection-and-publication entry point.
- `run_forecast_board.py`: board builder and prediction-context ledger fields.
- `grade_forecast_board.py`: VM grader, exact-input audit, and daily learning
  review.
- `scripts/analyze_sep_window.py`: reproducible Sep 10–22 research analysis.
- `outputs/grades/learning_review_<date>.md` and `.json`: daily diagnostic
  artifacts (pull from Azure to the Mac for season review).
- `evidence/research/FINDINGS.md`: local, gitignored detailed baseline note;
  use the committed `docs/NEXT_CHECKIN.md` as the durable summary.

---
