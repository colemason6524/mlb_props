# Legacy & Documentation Inventory — 2026-10-22

Step 3 of the ledger/report integrity audit (`docs/INTEGRITY_AUDIT_2026-10-22.md`).
Everything here was verified live on the VM on 2026-10-22 (`systemctl --user
list-timers --all`, `git log`, `ls -lt outputs/`). Nothing in this file was
changed; the only edits made by the audit are listed in the audit doc.

## 1. What is actually live

Five user-level timers, all `Persistent=false` except statcast:

| Timer | OnCalendar | Task (`scripts/run_linux_task.sh`) | Writes |
|---|---|---|---|
| `sports-mlb-grade-board.timer` | `06:00:00 America/Detroit` | `grade-board` | `outputs/grades/forecast_board_<date>.json`, `learning_review_<date>.json/.md`, settles `outputs/ledger/picks_roi.jsonl` |
| `sports-mlb-statcast.timer` | `12:00` (no TZ suffix → **UTC**, i.e. 08:00 ET) | — (`collect_statcast.py` directly) | `.cache/statcast/day-<date>.json` |
| `sports-mlb-pipeline-noon.timer` | `12:15:00 America/Detroit` | `forecast-pipeline-noon` | `outputs/history/pitcher_props_*`, `outputs/history/game_markets_*`, `outputs/forecast_boards/*`, board JSON |
| `sports-mlb-pipeline-afternoon.timer` | `16:45:00 America/Detroit` | `forecast-pipeline-afternoon` | same, afternoon slot |
| `sports-mlb-pipeline-evening.timer` | `18:45:00 America/Detroit` | `forecast-pipeline-evening` | same, evening slot (post-lineup injury check) |

`run_forecast_pipeline.py` is the only live board publisher: pitcher collection
(`run_nightly.py`) → market collection (`run_game_markets.py`) → injury check →
board (`run_forecast_board.py`), failing closed if a collection stage produces
no fresh export. The board is the sole Discord publisher.

## 2. Dead code paths

`scripts/run_linux_task.sh` still carries five task branches that no timer, no
service, and no dashboard action invokes:

- `hot-hits` → `run_hot_hits.py` (last history export `2026-09-10T152041Z`)
- `pitcher-props` → `run_nightly.py` standalone (superseded by `forecast-pipeline-*`)
- `game-markets-morning` → `run_game_markets.py` standalone (same)
- `game-markets-evening` → `run_game_markets.py` standalone (same)
- `forecast-board` → `run_forecast_board.py` standalone (same)

`docs/AZURE_VM_OPERATIONS.md` already states hot hits and evening market
captures "remain available as manual commands … intentionally unscheduled",
so the branches are documented-dead, not surprise-dead. They are harmless
(no schedule, no reader) but they are the exact shape of NHL defect class 2:
a writer that still exists while its output has been frozen for six weeks.

Orphan artifact writers with no live schedule and no reader:

- `outputs/history/hot_hits_*.json` — 3 files, newest `2026-09-10T152041Z`,
  frozen. Nothing reads them (`grep -rn hot_hits_ *.py` finds only the writer
  and the config/threshold helpers it uses).
- `outputs/backtests/` — **0 files**. `backtest.py::_export_backtest_report`
  (line 1447) has never run in production; `backtest.py` is an interactive
  analysis tool, and no timer invokes it. No reader.
- `outputs/history/pitcher_props_*` (102 files) and `game_markets_*` (109
  files) are live, written by every pipeline run — newest `2026-10-22T2245Z`.

Retired OS wrappers, Windows-only, unreferenced from any current doc path:
`scripts/run_game_markets_evening_task.cmd`, `run_game_markets_task.cmd`,
`run_game_markets_task.ps1`, `run_hot_hits_task.cmd`, `run_hot_hits_task.ps1`,
`run_pitcher_props_backtest_task.cmd/.ps1`, `run_pitcher_props_recovery.cmd/.ps1`,
`run_pitcher_props_task.cmd/.ps1` (11 files).

The tmux scheduler experiment (`scripts/start_tmux_scheduler.sh`,
`scripts/run_daily_tmux_task.sh`, `scripts/mlb-props-tmux.service`,
`mlb-props-tmux.service`) is `disabled`/`inactive` and untracked in git; the
docs already warn not to re-enable it (double Discord sends).

## 3. Legacy environment flags

| Flag | Read by | Status |
|---|---|---|
| `DATA_MODE` | `mlb_props/config.py:316` (`data_mode`) | live — pipeline forces `live`; default `sample` |
| `EXPORT_HISTORY` | `mlb_props/config.py:313` (`export_history`) → `run_nightly.py:440`, `run_game_markets.py:356` | live — pipeline forces `true`; this is what feeds the board |
| `REFRESH_LINES` | `run_game_markets.py:277` | live — pipeline forces `true` |
| `RUN_NOTE` | `mlb_props/config.py:318` | live — pipeline sets `scheduled <slot> pipeline` |
| `DISPLAY_LIMIT` | `mlb_props/config.py:315` | dead for the live path — only the standalone `hot-hits`/`pitcher-props` branches set it |
| `SEND_DISCORD` | `run_nightly.py:54`, `run_hot_hits.py:51` | dead for the live path — pipeline sets `false`; the board posts via `--send-discord` |
| `FORECAST_BOARD_SEND_DISCORD` | `scripts/run_linux_task.sh` only | live — unset in `~/.config/mlb_props/env`, so it defaults to `true` and every pipeline posts |
| `HOT_HITS_*` (`CARD_POLICY`, `CORE_LIMIT`, `VALUE_LIMIT`) | `mlb_props/config.py:244-268` | dead — only the unscheduled `hot-hits` task uses them |

`SEND_DISCORD=false` and `DISPLAY_LIMIT` are the two flags most likely to
mislead a future reader: both look like Discord/board controls and neither
affects the live pipeline's output.

## 4. Documentation drift

| Doc | Dated | Describes current system? |
|---|---|---|
| `README.md` | pointer dated 2026-09-24 | Partly. Header points at `NEXT_CHECKIN.md`/`AZURE_VM_OPERATIONS.md` and says Windows is retired, but the body still carries Windows Task Scheduler instructions and dated checkpoints (flagged as historical). No timer list of its own. |
| `docs/NEXT_CHECKIN.md` | 2026-09-30 | **No.** Lists 3 timers (06:00 / 12:15 / 16:45 ET). Missing `sports-mlb-pipeline-evening` (18:45 ET) and `sports-mlb-statcast` (12:00 UTC). Says "The timers keep running through the postseason" — true, but a reader would think two boards/day, not three. |
| `docs/AZURE_VM_OPERATIONS.md` | verified 2026-09-24 | **No.** Same 3-timer table; no statcast timer; states timezones are always explicit, while `sports-mlb-statcast.timer` has `OnCalendar=12:00` (UTC) and `Persistent=true` — the one timer that breaks both stated conventions. |
| `docs/HOT_HITS_HANDOFF.md` | 2026-08-21 / verified 2026-09-24 | Correct about scheduling ("not scheduled … manual VM task") and self-flags its Windows sections as archival, but describes a pipeline whose exports have not been written since 2026-09-10. |
| `docs/PITCHER_PROPS_HANDOFF.md` | 2026-09-24 | Mostly. Model design is current; the operational section predates the pipeline consolidation. |
| `docs/PITCHER_PROPS_CONTINUATION_PROMPT.md` | 2026-09-23 | Points at `NEXT_CHECKIN.md` (correct) but names the Mac path `/Users/colemason/mlb_props` as the working repo, which is only true for edits — production runs on the VM. |
| `docs/SEASON_2026_RECAP.md` | frozen 2026-09-28 | Frozen by design. Reproduces exactly from the stored canonical artifacts (see audit §4); do not retro-edit. |
| `docs/POSTSEASON_2026_PLAYBOOK.md` | written 2026-09-28 | Yes — era rules still match `evidence_era` in the live grader. |

## 5. What this inventory means for the audit

- Class 2 (frozen writer/reader): **present but orphaned.** The one frozen
  artifact family (`hot_hits_*`) has no reader and no schedule; the one
  never-written family (`outputs/backtests/`) has no reader either. Nothing
  live reads a stale source — the failure mode is discoverability, not wrong
  numbers.
- Class 3 (multiple apparent sources of truth): the docs make it worse. Two
  "authoritative" docs list an incomplete timer set, and the only doc that
  presents a season record (`SEASON_2026_RECAP.md`) is frozen while three
  live artifacts (ledger, canonical, latest-revision) give three different
  current totals.
- Step 4 fixes therefore target (a) the units/price denominator, which is a
  real number-correctness defect, and (b) labelling the derived view; the
  dead paths and stale timer tables are corrected in documentation, and no
  dead file is deleted (deleting a documented manual command is a scope
  decision, not an audit finding).
