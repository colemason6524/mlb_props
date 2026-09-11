# Azure VM Operations (active 2026-09-11)

The Windows desktop is retired (chronic wifi loss + required restarts). Daily
MLB collection now runs on a cheap Azure VM. The Mac is the source of documents
and grading; the Windows hard drive retains historical logs/exports. The VM
stays lean: repo + outputs + logs only. No bulk history, no zips.

## Connection

```bash
ssh azure    # alias in ~/.ssh/config; explicit form:
ssh -i /Users/colemason/Downloads/RunThemScripts_key.pem azureuser@130.131.0.6
```

## What runs (systemd user units, linger enabled)

| Timer | When (ET) | Task | Notes |
| --- | --- | --- | --- |
| `sports-mlb-pipeline-noon.timer` | 12:15 | forecast-pipeline-noon | collect fresh inputs then publish full slate |
| `sports-mlb-pipeline-afternoon.timer` | 16:45 | forecast-pipeline-afternoon | collect fresh inputs then publish remaining pregame |
| `sports-mlb-grade-board.timer` | 06:00 | grade-board | grade prior day, settle ROI ledger, post recap |

- Timezones are explicit: `OnCalendar=*-*-* HH:MM:00 America/Detroit` — VM
  clock itself is UTC; do not "fix" the units to drop the TZ suffix.
- All units: user-level, `Type=oneshot`, `TimeoutStartSec=infinity` (the
  runner adds its own 45m/20m `timeout`), `ConditionPathExists` on the runner.
- `Persistent=false` is intentional: a missed run does not catch up hours
  later, because a late export would win latest-by-date grading with thin
  line coverage. Prefer a missed run over a bad one.
- Runner: `~/mlb_props/scripts/run_linux_task.sh <task>` — sources
  `~/.config/mlb_props/env` (secrets), `TZ=America/Detroit`, venv python
  (`~/mlb_props/.venv/bin/python`), flock at
  `~/.local/state/mlb_props/run.lock`, per-task log in `~/mlb_props/logs/`.
- The board requires `FORECAST_BOARD_DISCORD_WEBHOOK_URL`. Collection jobs do
  not require Discord secrets.
- Each pipeline run collects fresh pitcher props and fresh game markets, then
  immediately builds and publishes the board from exactly those exports. It
  stops if a collection stage fails or produces no new export, so the board
  can never publish from a stale same-day snapshot.
- The noon board covers the full remaining slate; the afternoon board covers
  only events still pregame (starts more than 10 minutes away). Both are
  standalone posts labeled `Noon Board` / `Afternoon Update`.
- A successful date/slot delivery is recorded; re-runs refuse to repost unless
  invoked with `--force-send`. Failed or partial deliveries may retry.
- The 6:00 AM grader (`grade_forecast_board.py`) reads the ledgers, resolves
  finals/boxscores from the free MLB Stats API, settles priced `PENDING` ROI
  rows, and posts a recap to the board channel (deduped per date as slot
  `grade`). An API failure suppresses the recap and marks the run failed.
- Hot Hits and evening market captures remain available as manual commands
  (`run_linux_task.sh hot-hits`, `... game-markets-evening`) but are
  intentionally unscheduled. They do not feed the board.
- Runtime retention defaults: history and game cache 400 days, rendered boards
  45 days, and two 5 MB backups per task log. Override with
  `MLB_HISTORY_RETENTION_DAYS`, `MLB_CACHE_RETENTION_DAYS`,
  `MLB_BOARD_RETENTION_DAYS`, and `MLB_PROPS_MAX_LOG_BYTES`.
- A leftover tmux scheduler experiment (`scripts/*tmux*`,
  `mlb-props-tmux.service`) is DISABLED and its processes are gone. Do not
  re-enable it — it would double-send Discord cards.

## 2026-09-08 validation results (pre-first-fire)

Pre-flight pass (~00:15 ET, 11h before first fire):

- `systemd-analyze --user verify` on all four services + timers: rc=0.
- Calendar math exact: next elapses 15:19/15:37/15:53/20:31 UTC =
  11:19/11:37/11:53/16:31 ET (timezone suffix resolves correctly on systemd 249).
- Runner secret gate tested safely: env file without the pitcher webhook fails
  fast (`FAILED: ... not configured`, exit 1) — env sourcing + gate proven.
- History hygiene: no dry-run pollution; only 4 valid game_markets snapshots
  (3 from 9/7 tmux/manual, 1 from the E2E unit test).
- VM state: 55G free disk, ~450M RAM available, lock file present, WIP tree
  intact (still uncommitted — commit deliberately before any rebuild).

First-fire results below should be appended after 2026-09-08 11:45 ET.

- Hot-hits dry run (no Discord, no export): full pipeline OK from Azure —
  14 slate games, 252 batters, 12 qualified, Savant shadow 124/124, Python
  3.10 compatible. Log: `logs/dry_hot_hits.log`.
- Pitcher dry run: completed clean on an empty-line slate (~18 min; the
  0-line fallback rerun is slow). 0 FanDuel K lines at midnight ET was
  confirmed as timing, not IP blocking — the Mac (residential) fetched 0 lines
  in the same hour. First real coverage test is the scheduled 11:37 ET run.
- Markets unit E2E via `systemctl --user start`: exit 0, Bovada 14/14 games
  matched (ML/RL/total + ESPN cross-check), history exported. The 2/10
  coverage seen at 18:39 ET on 9/7 was evening-coupon weirdness, not Azure.
- Webhooks: both `POST {}` returned 400 (reachable + authed, nothing posted).

## Daily 5-minute health pass

```bash
ssh azure 'systemctl --user list-timers --all | grep mlb'
ssh azure 'tail -4 ~/mlb_props/logs/forecast_pipeline_noon_task.log ~/mlb_props/logs/forecast_pipeline_afternoon_task.log'
ssh azure 'ls -t ~/mlb_props/outputs/forecast_boards/ | head -4'
```

Expect, per pipeline: one fresh pitcher export, one fresh game-market export,
one rendered board (`forecast_board_<date>_<slot>.json`), and a task log ending
`exit code 0`. The board fails closed if either required family is missing or
empty.

## Pull day's history to the Mac (grading happens here, not on the VM)

```bash
mkdir -p pitcher_props_from_windows/vm game_markets_from_windows/vm
scp 'azure:~/mlb_props/outputs/history/pitcher_props_*.json' pitcher_props_from_windows/vm/
scp 'azure:~/mlb_props/outputs/history/game_markets_*.json' game_markets_from_windows/vm/
```

## VM hygiene (do not overcrowd)

- Keep only the repo, `outputs/`, and `logs/` on the VM. The Mac holds
  documents/analysis; the Windows hard drive holds historical exports.
- Do not transfer zips, `.analysis/` folders, or history archives to the VM.
- Repo sync from the Mac side: `git push origin main`, then
  `ssh azure 'cd mlb_props && git pull --ff-only'`. The VM worktree currently
  carries uncommitted local work (ActionNetwork game-lines source, tests,
  scheduler scripts) — commit/push it deliberately before any rebuild.
