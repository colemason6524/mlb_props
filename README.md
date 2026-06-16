# MLB Nightly Props Screener

Pitcher-first MLB props screener modeled on the NBA project's repeatable flow:

- slate
- prop lines
- recent logs
- matchup context
- risk flags
- scored candidates

Version 1 is intentionally small and local-first. It focuses on pitcher props that are more role-stable:

- `PITCHER_STRIKEOUTS`
- `PITCHER_OUTS_RECORDED`

The repo also includes a live-data hitter screen for one-hit parlay research:

- `BATTER_HITS` hot hitter board via `run_hot_hits.py`

## Repo shape

- `run_nightly.py`: pitcher props CLI entry point
- `run_hot_hits.py`: live hot hitters CLI for one-hit candidates
- `mlb_props/config.py`: settings, thresholds, prop constants
- `mlb_props/models.py`: normalized MLB data models
- `mlb_props/sources/`: source adapters
- `mlb_props/screener.py`: pitcher scoring engine
- `mlb_props/output.py`: terminal rendering

## Architecture notes

- The repo mirrors the NBA system's flow, not its sport logic.
- Pitcher props use baseball-native workload signals like pitch count, outs volume, and leash stability.
- The nightly run now prints both a prop board and an all-probable-starters board, so you can assess the full starter slate even when books have not posted every line.
- `PropLine.subject_role` keeps the model extensible so batter props can slot in later without rewriting the line model.
- Source adapters are isolated behind `sources/`, so live feeds can replace the sample data layer later.
- `DATA_MODE=live` now uses MLB Stats API for slate/probable starters and pitcher logs, and defaults to a scrape-first source that combines FanDuel matchup/Ks scraping with an experimental DraftKings outs adapter.

## Quick start

```bash
python3 run_nightly.py
```

Warm the cache only:

```bash
python3 run_nightly.py --warm-cache
```

Show cache summary:

```bash
python3 run_nightly.py --cache-report
```

Backtest saved strikeout screen runs:

```bash
python3 backtest.py
```

Backtest all saved strikeout screen history:

```bash
python3 backtest.py --all-history
```

Run the hot hitters board:

```bash
DATA_MODE=live python3 run_hot_hits.py
```

Run hot hitters and send the top plays to Discord:

```bash
DATA_MODE=live SEND_DISCORD=true DISCORD_WEBHOOK_URL=your_discord_webhook_url python3 run_hot_hits.py
```

## Environment variables

```bash
export SCREEN_DATE=2026-04-11
export SCREEN_PROP_TYPES=PITCHER_STRIKEOUTS,PITCHER_OUTS_RECORDED
export CACHE_TTL_HOURS=24
export LINES_CACHE_TTL_MINUTES=15
export INCLUDE_UNDERS=true
export EXPORT_HISTORY=false
export MIN_DISPLAY_SCORE=7
export DISPLAY_LIMIT=30
export DATA_MODE=sample
export LINE_SOURCE=fanduel
export ODDS_API_KEY=your_key_here
export ODDS_API_BOOKMAKERS=fanduel,draftkings
export HOT_HITS_AVG_MIN=0.350
export HOT_HITS_STRONG_AVG=0.400
export HOT_HITS_MIN_HIT_GAMES=4
export HOT_HITS_MAX_BATTERS_PER_TEAM=9
export HOT_HITS_INCLUDE_BVP=true
export HOT_HITS_DISCORD_MIN_SCORE=10
export SEND_DISCORD=false
export DISCORD_WEBHOOK_URL=your_discord_webhook_url
```

On Windows, store the webhook once for the same user account that will run the scheduled task:

```powershell
setx DISCORD_WEBHOOK_URL "your_discord_webhook_url"
```

Close and reopen PowerShell after `setx`, then test:

```powershell
cd C:\path\to\mlb_props
$env:DATA_MODE="live"
$env:SEND_DISCORD="true"
python run_hot_hits.py
```

For Task Scheduler, edit `scripts\run_hot_hits_task.cmd` if your repo is not at `C:\Users\muski\mlb_props`.
Then create the task with:

- Program/script: `C:\Windows\System32\cmd.exe`
- Add arguments: `/c ""C:\Users\muski\mlb_props\scripts\run_hot_hits_task.cmd""`
- Start in: `C:\Users\muski\mlb_props`
- Run whether user is logged on or not: enabled if you want it fully unattended
- Run with highest privileges: enabled

Before using Task Scheduler, test the exact command manually:

```powershell
C:\Windows\System32\cmd.exe /c ""C:\Users\muski\mlb_props\scripts\run_hot_hits_task.cmd""
```

The task wrapper writes diagnostics to:

```text
C:\Users\muski\mlb_props\logs\hot_hits_cmd_bootstrap.log
C:\Users\muski\mlb_props\logs\hot_hits_task.log
```

The scheduled hot-hits wrapper also exports every qualified candidate to:

```text
C:\Users\muski\mlb_props\outputs\history\hot_hits_*.json
```

To send logs plus hot-hits history to another machine for review:

```powershell
Compress-Archive -Path C:\Users\muski\mlb_props\logs\*,C:\Users\muski\mlb_props\outputs\history\hot_hits_*.json -DestinationPath C:\Users\muski\Desktop\mlb_props_logs.zip -Force
```

Run live mode:

```bash
DATA_MODE=live python3 run_nightly.py
```

Run live mode with The Odds API instead:

```bash
DATA_MODE=live LINE_SOURCE=odds_api ODDS_API_KEY=your_key_here python3 run_nightly.py
```

## Current scoring inputs

Hot hitter screen:

- probable starter matchup from the daily slate
- likely hitters from recent actual batting orders, with active-roster fallback
- last-5 batting average hotness, with `.350+` as the default gate and `.400+` as a strong signal
- last-5 and last-10 games with at least one hit
- season batting average as an anchor against short-term spikes
- probable starter hit-allowed rate, strikeout rate, and walk rate
- optional MLB Stats API batter-vs-pitcher history when available

Pitcher prop screen:

- recent hit rates over the last 5 and last 10 starts
- rolling averages and medians
- line-versus-average deltas
- season averages as the main anchor, with last 5 used as the primary recent-form check
- pitch-count and outs workload
- recent stability versus volatility
- opponent strikeout tendency by handedness
- opponent patience and quality-of-contact context by handedness
- expected leash context via opponent outs factor
- basic park and moneyline flags
- recent strikeout efficiency, control risk, quality-start support, and short-start risk

## Current assumptions

- The repo runs on bundled sample data by default so it is immediately usable locally.
- Live integrations currently cover slate/probable starters, pitcher logs, live pitcher strikeout lines, and an experimental scrape path for pitcher outs recorded.
- Scrape mode is the default live line source, matching the scrape-first philosophy of the NBA repo.
- Live matchup context now uses a projected-lineup proxy built from recent actual batting orders when available, with an active-roster fallback when recent lineup signal is thin.
- Live scrape mode now emits coverage diagnostics and writes issue snapshots under `outputs/diagnostics/` when line capture is unusually thin for the slate.
- FanDuel remains the primary source for visible pitcher strikeout props and matchup context.
- Pitcher outs recorded is now wired through an experimental DraftKings scrape path. It is integrated into the same `PropLine` flow, but still needs morning/early-slate validation before we treat it as fully reliable.
- If a live board comes back empty under the primary thresholds, the runner automatically retries with a softer live fallback profile so you still get a usable practice board.
- The starter board is line-independent and is meant for daily assessment; its matchup columns are pitcher-friendly when positive and tougher when negative.
- `EXPORT_HISTORY=true` writes backtest-ready screen snapshots to `outputs/history/`, and `python3 backtest.py` reconciles saved strikeout plays the next day.
- Batter hits are wired as a live, line-independent hot-hitter board; posted sportsbook hit odds are not scraped yet.
