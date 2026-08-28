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

And a game-level market shadow collector (observation-only, no model opinions):

- moneyline / run line / game total snapshots via `run_game_markets.py`

## Repo shape

- `run_nightly.py`: pitcher props CLI entry point
- `run_hot_hits.py`: live hot hitters CLI for one-hit candidates
- `mlb_props/config.py`: settings, thresholds, prop constants
- `mlb_props/models.py`: normalized MLB data models
- `mlb_props/sources/`: source adapters
- `mlb_props/screener.py`: pitcher scoring engine
- `mlb_props/tiers.py`: pitcher Core/Lean/Watch tier policy
- `mlb_props/pitcher_presentation.py`: relative slate rank, display-only opportunity reliability, and Best Available policy
- `mlb_props/pitcher_confidence.py`: provisional price-agnostic side-win probability and confidence labels
- `mlb_props/opportunity.py`: research-only pitcher workload/opportunity shadow model
- `mlb_props/version.py`: pitcher history, active-model, tier-policy, shadow-feature, and display-policy versions
- `mlb_props/output.py`: terminal rendering
- `backtest.py`: reconciles saved pitcher strikeout snapshots against actual MLB results
- `scripts/run_pitcher_props_task.*`: Windows Task Scheduler wrappers for the daily pitcher board
- `scripts/run_pitcher_props_backtest_task.*`: optional Windows Task Scheduler wrappers for daily pitcher backtests
- `run_game_markets.py`: game-market shadow collector CLI (Bovada primary, ESPN cross-check)
- `mlb_props/game_markets.py`: two-way price models and no-vig market-baseline math
- `scripts/run_game_markets_task.*`: Windows Task Scheduler wrappers for the morning + evening shadow runs

## Game-level market shadow (added 2026-08-25)

Observation-only collection of moneyline, run line, and game total lines.
No model opinions, no Discord, no staking claims. The goal is to accumulate
a versioned price history now so that the future `game-ml` / `game-total`
shadow models can be graded against a real market baseline from day one.

- Sources: Bovada free coupon JSON API (`sources/bovada_mlb.py`, primary,
  both-side American prices) with ESPN odds page cross-check
  (`sources/espn_odds.py`, totals + run lines; curl User-Agent fallback for
  its AWS WAF challenge). No Odds API usage or fees.
- Market baseline: no-vig probabilities per game via
  `game_markets.market_baseline_payload` (home/away win, over/under).
- History: `outputs/history/game_markets_*.json`, schema
  `GAME_MARKETS_HISTORY_SCHEMA_VERSION = 1`, shadow version
  `game-price-shadow-v1`. Coverage diagnostics per source are embedded in
  every export.
- Run locally: `DATA_MODE=live python3 run_game_markets.py`
  (add `EXPORT_HISTORY=true` to export).

Windows Task Scheduler registration (morning pregame + evening
lineup-confirmation refresh):

```bat
schtasks /Create /TN "MLB_game_markets" /TR "C:\Users\muski\mlb_props\scripts\run_game_markets_task.cmd" /SC DAILY /ST 11:40 /F
schtasks /Create /TN "MLB_game_markets_evening" /TR "C:\Users\muski\mlb_props\scripts\run_game_markets_evening_task.cmd" /SC DAILY /ST 16:35 /F
```

The evening run re-snapshots lines after lineups post; starter changes
between snapshots will be detectable later by comparing probable pitchers
in successive history exports.

Empty coverage still exits 0 (so the scheduled task is not marked failed)
but now always writes a history file with source diagnostics. Treat those
exports as unsuitable for evaluation. Bovada's prematch coupon can still
list the previous night's unsettled games at 11:40 ET before today's slate
is posted; the collector filters those as stale. ESPN is a cross-check only
and does not supply moneylines, so it cannot fill a Bovada-empty morning.

## Architecture notes

- The repo mirrors the NBA system's flow, not its sport logic.
- Pitcher props use baseball-native workload signals like pitch count, outs volume, and leash stability.
- The nightly run now prints both a prop board and an all-probable-starters board, so you can assess the full starter slate even when books have not posted every line.
- `PropLine.subject_role` keeps the model extensible so batter props can slot in later without rewriting the line model.
- Source adapters are isolated behind `sources/`, so live feeds can replace the sample data layer later.
- `DATA_MODE=live` now uses MLB Stats API for slate/probable starters and pitcher logs, and defaults to a scrape-first source that combines FanDuel matchup/Ks scraping with an experimental DraftKings outs adapter.
- Pitcher raw scoring, absolute tiering, and relative presentation are intentionally separate. Signal balance remains useful for diagnostics/backtests, `mlb_props/tiers.py` decides Core/Lean/Watch, and `mlb_props/pitcher_presentation.py` decides slate rank and display role.

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

### Pitcher Props Objective And Design

The pitcher board is a daily research tool for posted pitcher strikeout props. The current objective is not to maximize the number of picks; it is to keep a strict Core board while still saving broader model opinions for learning. The intended workflow is:

- run one full pregame slate capture each day from the Windows PC
- use FanDuel as the primary no-key strikeout line source
- combine pitcher projection, expected outs/batters-faced opportunity, handedness matchup, recent K rate, walk risk, and workload context
- show Core plays separately from Leans and Watchlist so the daily output stays useful even when few Core plays qualify
- export history every run so later backtests can evaluate both displayed plays and missed/near-miss profiles

The current scoring approach intentionally moved away from pure consistency streaks such as `4/5` recent hit rate. Strikeouts proved too volatile for an NBA-style consistency model. Current pitcher selection puts more weight on projected strikeouts versus the posted line, projected opportunity, matchup context, and risk flags.

Current pitcher tiers:

- Core: score clears `MIN_DISPLAY_SCORE` and passes the stricter tier policy in `mlb_props/tiers.py`
- Lean: score/context is interesting and projected edge is real, but one or more Core requirements is missing
- Watchlist: broad data-collection tier; useful for model learning, not meant as auto-plays

Core tier gates are deliberately stricter than raw score:

- side-adjusted projected edge must be at least `1.0`
- volatile candidates need at least `1.25` edge
- unders need at least `1.25` edge
- unders are blocked from Core when projected opportunity is high: projected outs at least `18.0`, projected batters faced at least `24.5`, or workload/depth flags such as `DEPTH_PLUS`, `WORKLOAD_PLUS`, or `QS_PLUS`

This split was added after weekly reviews showed small-edge plays and broad Watchlist picks were noisy, while stricter Core plays looked more promising. Keep evaluating that decision with fresh samples; do not treat the current thresholds as proven by a large sample yet.

#### Pitcher research checkpoint: 2026-07-28

The July 20-27 Windows history review contained 33 saved Core/Lean/Watch strikeout opinions. One pitcher did not start, leaving 32 graded plays:

- overall: `15-17` (`46.9%`)
- Core: `0-1`
- Leans: `3-3`
- Watchlist: `12-13`
- overs: `10-10`
- unders: `5-7`

The more important finding was projection error, not the small-sample win rate:

- 12 of 17 losses (`70.6%`) were classified as opportunity-related
- 8 losses came from shorter-than-projected outings
- 4 losses came from deeper-than-projected outings
- outs bias, actual minus projected: `-0.40`
- outs mean absolute error: `3.50`
- batters-faced bias, actual minus projected: `-0.65`
- batters-faced mean absolute error: `2.78`
- strikeout bias, actual minus projected: `-0.16`
- strikeout mean absolute error: `2.42`

Aggregate strikeout bias was close to neutral, but individual strikeout projections were often made over the wrong amount of opportunity. Large projected line edges also underperformed smaller edges during this window. Do not conclude that small edges are inherently better; the useful lesson is that large edges can be falsely precise when projected outing length is wrong.

The current response to this review is:

- do not loosen Core
- keep Leans and Watchlist broad enough to preserve learning data
- keep unders cautious because deeper outings and extra batters can defeat them
- improve pitch budget, batters faced, outs, role/leash, and uncertainty before adding more scoring bonuses
- do not retune production thresholds from this one week alone

#### Shadow opportunity collection

Commit `7925666` added a research-only opportunity profile. It is intentionally a shadow model: it is exported for later grading but does not change projected strikeouts, current projected outs, current projected batters faced, score, or Core/Lean/Watch tier. Starting with the 2026-08-02 presentation update, its `HIGH`/`MEDIUM`/`LOW` confidence and warning flags may be shown as display-only opportunity context.

Current version metadata:

- history schema: `5`
- active pitcher model: `pitcher-k-situational-v1`
- tier policy: `core-lean-watch-v1`
- shadow feature set: `opportunity-shadow-v1`
- confidence model: `pitcher-confidence-provisional-v1`
- display policy: `provisional-confidence-rank-v1`

Every eligible candidate and full starter-board entry now saves:

- dates, pitch counts, outs, and batters faced from the last three starts
- pitches per batter faced for each of those starts
- days since the last start
- last-three average pitch count and last-five maximum pitch count
- pitch-count and outs trend direction and magnitude
- last-five pitch-count and outs volatility
- last-five short-start count
- experimental pitch budget, projected batters faced, and projected outs
- `HIGH`, `MEDIUM`, or `LOW` opportunity confidence
- separate shadow warnings such as workload ramp/decline, long layoff, mixed recent role, low recent pitch count, volatility, and recent short starts

Safeguards:

- the shadow builder only uses appearances with `game_date < screen_date`
- shadow opportunity uses starts for its numeric projections; recent relief appearances can still trigger a mixed-role warning
- raw recent values are saved so each experimental estimate is auditable
- shadow warnings are stored separately from production scoring flags
- older schema-1 and schema-2 history remains backtestable

The sample board was byte-for-byte identical before and after the original shadow implementation. The existing Windows task still runs unchanged. Review after 3-5 completed slates for obvious data-quality issues, but do not let display-only reliability labels influence score or tier from that early sample. Target at least 50-100 graded candidates before deciding whether any shadow estimate or warning should influence production recommendations.

#### Pitcher presentation checkpoint: 2026-08-02

Recent production runs exposed a user-facing calibration problem without showing that the projection model itself had changed. From July 29 through August 2, the maximum daily raw score was `5`, `4`, `5`, `2`, then `13`, while daily medians remained between `-6` and `-3`. Four consecutive slates had no candidate reach the Core score threshold even though several candidates had meaningful projection edges.

The old `Score` label was misleading because the integer is an unbounded additive signal balance, not a probability or a cross-slate confidence measure. Correlated projection, opportunity, matchup, and risk inputs can stack in either direction. A score of `0` means the positive and negative adjustments canceled; it does not mean zero confidence.

Display policy `edge-reliability-rank-v1` changes presentation, not recommendations:

- `Score` is relabeled `Signal` or `Signal balance` and described as an internal diagnostic
- `Side Edge` is positive when the projection supports the listed Over or Under
- `Opportunity`/`Opp Rel` shows display-only `HIGH`, `MEDIUM`, or `LOW` workload reliability
- `# Rank` is relative to the current eligible slate, while Core/Lean/Watch remains the absolute tier
- when Core is empty, the top three already-eligible Lean/Watch candidates are labeled `Best Available`; they are not promoted to Core
- schema-4 history saves `display_policy_version` and `display_rankings` so the exact delivered rank, tier, role, reliability, side edge, and signal balance remain auditable

Do not interpret `Best Available` as a forced bet. It exists so a user can identify the strongest supported opinion on a weak slate without weakening the Core standard.

#### Provisional confidence checkpoint: 2026-08-04

Schema 5 adds a price-agnostic confidence estimate for each eligible pitcher prop. It does not convert the raw signal score into a percentage. Instead, `pitcher-confidence-provisional-v1` builds an overdispersed outcome distribution around projected strikeouts (or projected outs), includes recent volatility and shadow opportunity uncertainty, and then shrinks the directional probability toward `50%` when workload reliability, sample size, or risk flags are weak.

The public meaning is intentionally narrow:

> Provisional confidence is the estimated chance that the listed side clears the posted line. Sportsbook price is not included.

Provisional labels are:

- `60%+`: Strong
- `57-59%`: Solid
- `54-56%`: Cautious
- `51-53%`: Higher Risk
- `50%`: No Pick

Safeguards:

- confidence does not change projected strikeouts, projected opportunity, raw score, candidate qualification, or Core/Lean/Watch tier
- estimates are deterministic and capped at `68%` while uncalibrated
- risk and low workload reliability can only shrink directional confidence toward `50%`; they do not create an edge
- Discord omits raw signal balance and leads with provisional confidence, side, line, projection edge, and workload reliability
- terminal output retains signal balance for diagnosis
- schema-5 history stores the full estimate, method version, raw probability, uncertainty, shrinkage weight, label, and explicit `price_included: false`
- backtests report forecast-versus-observed results by confidence band plus Brier score

Do not describe these percentages as profitable-bet probabilities or expected value. Exact FanDuel Over/Under prices are not collected. Keep normal outcome grading on singles, collect at least 50-100 resolved estimates, and verify that each confidence band behaves approximately as advertised before removing the `PROVISIONAL` label or changing tier policy.

#### L5 recency research checkpoint: 2026-08-04

Recent strikeout results are useful evidence, but they are not stable enough to act like a deterministic streak signal. The active model currently uses L5 in several places: K rate, opportunity, raw line deltas, hit-rate adjustments, stability, control, and workload. That means L5 can have more total influence than the visible `60% recent / 40% season` K-rate formula suggests.

Schema 6 adds `recency-shadow-v1` beside every qualified candidate. It is observation-only and makes no change to production projections, qualification, signal balance, confidence shown to users, Core/Lean/Watch tier, terminal output, or Discord output.

The alternative projection deliberately separates recent skill from recent prop outcomes:

- K ability uses aggregate strikeouts divided by aggregate batters faced, not the average of per-game K rates
- the shadow K-rate anchor is `50% season + 30% L10 + 20% L5`, followed by the existing matchup adjustments and a L10 walk-risk adjustment
- projected outs remain the active workload projection because recent pitch count, role, leash, and short-start evidence should remain important
- batters faced per out blends `60% L5 + 40% season` instead of relying entirely on L5
- each saved shadow includes current-versus-shadow projected Ks, projected BF, side edge, and a separate provisional confidence estimate
- all shadow logs are restricted to `game_date < screen_date` to prevent lookahead

Backtests now report L5 outcome bands (`0-1/5`, `2/5`, `3/5`, `4-5/5`) with hit rate, actual outs/BF, short-outing rate, active confidence, and active-versus-shadow K error. They also compare active and shadow K/BF mean absolute error and confidence Brier score.

The comparison is limited to candidates admitted by the active model and selected by the requested backtest scope. It cannot prove how excluded lines would have performed. Treat the first 3-5 normal slates as a data-quality check and wait for roughly 50-100 resolved candidates before considering any production weight change. If the shadow wins on projection error and calibration across multiple windows, activation should be a separate commit with a new `PITCHER_MODEL_VERSION`.

### Hot Hits Objective And Design

The hot-hits board is a line-independent research tool for one-hit parlay candidates. It is not trying to price a sportsbook market directly. The intended workflow is:

Detailed Hot Hits research history, rollout status, and next-review instructions are maintained in [`docs/HOT_HITS_HANDOFF.md`](docs/HOT_HITS_HANDOFF.md).

- build the daily MLB slate from probable starters
- identify likely bats from recent batting orders
- require recent hit form first, then check season average as an anchor
- use probable-starter contact context, recent hit-allowed rate, strikeout rate, and optional BvP as supporting context
- keep the terminal board broad for research, but keep Discord tighter and easier to act on

Discord is intentionally stricter than terminal output. Terminal output may show every qualified candidate so you can inspect the full board. Live Discord uses the `core-first-v1` policy: it recommends up to four Core names, allows a stable one- or two-leg Core card, and never pads the recommended parlay with a weaker tier. Thin candidates are not sent to Discord.

Value candidates are displayed separately as optional higher-risk research plays. They are not part of the recommended Core parlay. When four Core names qualify, no Value names are shown; with three Core names, at most one Value is shown; with zero to two Core names, at most two Value names are shown. A day with no Core names is reported honestly rather than forcing a recommended play.

Current Discord support signals are:

- batting order 1-4
- matchup rating at least `+0.20`
- probable starter recent hit-allowed rate at least `.260`
- season batting average at least `.280`
- last-5 batting average at least `.380`

Current Discord tiers are intentionally qualitative:

- Core: stronger hit profile with top-order and support-signal backing
- Value: supported fallback research candidate, explicitly labeled higher risk and play-at-your-own-risk
- Thin: broad terminal/history research only; never part of the live Discord card

Sportsbook odds are deliberately not integrated. The mental model is that top-order and obvious hitters may be priced poorly, while some value bats may require a lower raw hit probability to be useful in parlays. Avoid adding odds APIs unless that is explicitly revisited; it was deferred to keep the system simple and avoid fragile integrations.

Hot Hits also collects Baseball Savant contact quality as an observation-only shadow layer. `contact-quality-shadow-v1` batches a broader research pool into cached Statcast CSV requests and uses only regular-season events through the day before the screen date. It records season and last-10-game xBA, contact-only xBA over the latest 25 tracked batted balls, hard-hit rate, sweet-spot rate, barrel rate, average exit velocity, and recent at-bat opportunity. Batch failure is fail-open: successful batches remain usable and the production board continues even if Savant is unavailable.

The broader pool is deliberately separate from the production gate. The existing production requirements and Core/Value/Thin policy are unchanged. A research profile needs at least 10 games, 12 last-5 at-bats, batting order 1-7, season average at least `.230`, and at least one of season average `.250`, last-10 average `.240`, or last-5 average `.300`. Every hitter who clears the current production gate is retained in the research pool even if that broader rule would otherwise omit them. Each profile records whether it cleared the current gate and the exact failed gates.

`hot-hits-confidence-provisional-v1` ranks this broader pool by an estimated chance of at least one hit. It blends season and last-10 xBA with a recent weight capped at `35%`, applies only a small matchup adjustment, converts the per-at-bat estimate using expected at-bats and batting-order opportunity, and shrinks toward the season anchor when Statcast reliability is weak. If Savant is unavailable, it falls back to season and last-10 batting average and marks the estimate as results-based and lower reliability.

Provisional Hot Hits labels are:

- `78%+`: Strong
- `72-77%`: Solid
- `66-71%`: Cautious
- `60-65%`: Higher Risk
- below `60%`: No Pick

The confidence research board appears only in terminal output and history. It does not affect raw score, production qualification, Core/Value/Thin tier, Discord eligibility, card order, or Discord content. The percentage is price-independent, capped to `45-88%` while provisional, and is not a claim of calibration, betting value, or expected return. Collect and grade a materially larger normal-slate sample before changing production selection.

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
export HOT_HITS_INCLUDE_CONTACT_SHADOW=true
export HOT_HITS_RESEARCH_MIN_SEASON_AVG=0.230
export HOT_HITS_RESEARCH_MIN_AVG_L10=0.240
export HOT_HITS_RESEARCH_MIN_AVG_L5=0.300
export HOT_HITS_RESEARCH_STRONG_SEASON_AVG=0.250
export HOT_HITS_RESEARCH_MAX_BATTING_ORDER=7
export HOT_HITS_DISCORD_MIN_SCORE=10
export HOT_HITS_CARD_POLICY=core-first-v1
export HOT_HITS_CORE_LIMIT=4
export HOT_HITS_VALUE_LIMIT=2
export SEND_DISCORD=false
export DISCORD_WEBHOOK_URL=your_discord_webhook_url
export PITCHER_PROPS_DISCORD_WEBHOOK_URL=your_pitcher_props_discord_webhook_url
export PITCHER_PROPS_DISCORD_CORE_LIMIT=5
export PITCHER_PROPS_DISCORD_WATCH_LIMIT=5
export RUN_NOTE="scheduled full pregame run"
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

The PowerShell wrapper accepts `DisplayLimit`, which controls the detailed terminal board. Discord selection is configured separately and defaults to `core-first-v1`, four Core names, and at most two optional Value fallbacks within the Core-first slot rules. Existing Task Scheduler commands require no change because these defaults are built in. Set the three `HOT_HITS_*` card variables for the Windows task account only if you need an explicit override or temporary rollback to `current-v1`.

### Hot Hits Review And Grading

Use `hot_hits_report.py` to grade exported hot-hit history files against MLB Stats API boxscores:

```bash
python3 hot_hits_report.py --history-dir outputs/history --since 2026-07-01
```

The default report policy is the live `core-first-v1` policy with a four-name Core limit and two-name Value fallback limit. Replay the former six-name policy explicitly when making comparisons:

```bash
python3 hot_hits_report.py \
  --history-dir outputs/history \
  --card-policy current-v1 \
  --limit 6
```

New exports record the selected Core and optional Value names plus Discord delivery status. Grade only the exact successfully delivered cards with:

```bash
python3 hot_hits_report.py \
  --history-dir outputs/history \
  --card-policy delivered
```

It can also read transferred zip files:

```bash
python3 hot_hits_report.py --zip mlb_props_logs_week.zip --include-pending
```

Useful report outputs:

```bash
python3 hot_hits_report.py \
  --history-dir hot_hits_from_windows \
  --since 2026-07-01 \
  --output .analysis/hot_hits_report_latest.txt \
  --json-output .analysis/hot_hits_report_latest_rows.json \
  --include-pending
```

For `core-first-v1` and `current-v1`, the report recomputes hot-hit scores using the current model before simulating Discord selection. That makes old history useful for testing current rules, but it also means reported score bands may differ from the score printed on the original run day if scoring logic has changed since then. The `delivered` policy instead uses delivery metadata from newer exports and ignores exports where Discord was not successfully sent.

Exports containing `confidence_research_pool` are graded across that broader pool. The report still limits policy replays to profiles that were production-qualified, so research-only names cannot leak into a simulated Discord card. It also reports observed hit rate, mean forecast and Brier score by confidence label; current-gate pass/fail; confidence-ranked one- through four-leg shadow cards; and hits excluded by the current gate. Older exports without the new pool continue to grade their original `candidates` array.

For Windows-to-Mac review, either zip logs/history on Windows or pull raw files with `scp`. History JSON files are the grading source of truth; task logs are mainly for debugging scheduled-run failures. Local pull folders such as `hot_hits_from_windows/` and `pitcher_props_from_windows/` are ignored by git.

### Windows Task Scheduler: Pitcher Props

Use this for the once-per-day full pregame pitcher strikeout board. The wrapper runs live mode and exports history automatically.

The pitcher-props task can reuse `DISCORD_WEBHOOK_URL` from hot hits, or you can set a separate channel:

```powershell
setx PITCHER_PROPS_DISCORD_WEBHOOK_URL "your_pitcher_props_discord_webhook_url"
```

Close and reopen PowerShell after `setx` so Task Scheduler sees the updated environment.

For Task Scheduler, edit `scripts\run_pitcher_props_task.cmd` if your repo is not at `C:\Users\muski\mlb_props`.
Then create the task with:

- Program/script: `C:\Windows\System32\cmd.exe`
- Add arguments: `/c ""C:\Users\muski\mlb_props\scripts\run_pitcher_props_task.cmd""`
- Start in: `C:\Users\muski\mlb_props`
- Run whether user is logged on or not: enabled if you want it fully unattended
- Run with highest privileges: enabled

Before using Task Scheduler, test the exact command manually:

```powershell
C:\Windows\System32\cmd.exe /c ""C:\Users\muski\mlb_props\scripts\run_pitcher_props_task.cmd""
```

The pitcher-props task wrapper writes diagnostics to:

```text
C:\Users\muski\mlb_props\logs\pitcher_props_cmd_bootstrap.log
C:\Users\muski\mlb_props\logs\pitcher_props_task.log
```

The PowerShell task wrapper writes command output through a temporary file and then appends it to the task log as UTF-8 lines. This is intentional. Earlier versions used direct `*>>` redirection and produced mixed-encoding logs that obscured failures. If Python exits nonzero or PowerShell catches an exception, the wrapper should now include the command output plus full PowerShell failure details.

The scheduled pitcher-props wrapper exports the full board, model opinions, line coverage, and optional run note to:

```text
C:\Users\muski\mlb_props\outputs\history\pitcher_props_*.json
```

By default, the Discord message includes Core plays plus up to three ranked Lean/Watch alternatives. When Core is empty, those alternatives are labeled `Best Available` without changing their underlying tier. `DiscordCoreLimit` remains configurable; `DiscordWatchLimit` can lower the alternative count, while the display policy caps it at three.

The daily pitcher task is the only required scheduled task. The backtest task below is optional; if it is not scheduled, `outputs\backtests` will not exist on Windows. That is expected and does not mean the daily run failed.

No Task Scheduler command change is required for schema 6, the confidence/display policy, or either pitcher shadow. After Windows pulls the current `main`, the existing wrapper automatically writes the new metadata and rankings. A healthy current export contains:

```text
history_schema_version : 6
model_version           : pitcher-k-situational-v1
tier_policy_version     : core-lean-watch-v1
shadow_feature_version  : opportunity-shadow-v1
recency_shadow_version  : recency-shadow-v1
confidence_model_version: pitcher-confidence-provisional-v1
display_policy_version  : provisional-confidence-rank-v1
```

Verify the most recent Windows export with:

```powershell
$Latest = Get-ChildItem .\outputs\history\pitcher_props_*.json |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

$History = Get-Content $Latest.FullName -Raw | ConvertFrom-Json

$History |
  Select-Object history_schema_version, model_version, tier_policy_version, shadow_feature_version, recency_shadow_version, confidence_model_version, display_policy_version, run_note
```

### Windows Task Scheduler: Daily Pitcher Props Backtest

Use this as a morning health-check task before the next slate is exported. It runs the latest-slate backtest and writes the report to `outputs\backtests`.

For Task Scheduler, edit `scripts\run_pitcher_props_backtest_task.cmd` if your repo is not at `C:\Users\muski\mlb_props`.
Then create the task with:

- Program/script: `C:\Windows\System32\cmd.exe`
- Add arguments: `/c ""C:\Users\muski\mlb_props\scripts\run_pitcher_props_backtest_task.cmd""`
- Start in: `C:\Users\muski\mlb_props`
- Run whether user is logged on or not: enabled if you want it fully unattended
- Run with highest privileges: enabled

Before using Task Scheduler, test the exact command manually:

```powershell
C:\Windows\System32\cmd.exe /c ""C:\Users\muski\mlb_props\scripts\run_pitcher_props_backtest_task.cmd""
```

The backtest task wrapper writes diagnostics to:

```text
C:\Users\muski\mlb_props\logs\pitcher_props_backtest_cmd_bootstrap.log
C:\Users\muski\mlb_props\logs\pitcher_props_backtest_task.log
```

Daily backtest reports are exported to:

```text
C:\Users\muski\mlb_props\outputs\backtests\backtest_*.txt
```

For a weekly/manual full-history review, run:

```powershell
python backtest.py --all-history
```

Backtest scopes:

- `python backtest.py`: grades the latest completed saved pitcher strikeout snapshot
- `python backtest.py --all-history`: grades the latest snapshot for every saved screen date
- `python backtest.py --core-only`: grades only Core plays
- `python backtest.py --include-leans`: grades Core plus Leans, excluding Watchlist
- `python backtest.py --include-watch`: explicitly grades Core plus Leans plus Watchlist
- `python backtest.py --all-history --include-watch --history-dir <folder>`: grades a transferred Windows history folder directly
- default scope without `--core-only` or `--include-leans`: grades Core plus Leans plus Watchlist

Pitcher history exports include separate history-schema, projection-model, tier-policy, shadow-feature, and display-policy versions. Older snapshots remain backtestable and are labeled `legacy-unversioned` in reports. Once schema-3-or-newer plays are finished, the report adds:

- current versus shadow outs mean absolute error
- current versus shadow batters-faced mean absolute error
- shadow pitch-budget error and bias
- error and short-outing rates by opportunity-confidence group
- shadow-warning performance once a warning reaches the minimum sample size

Backtests require network access to MLB Stats API because they fetch postgame pitching logs. They intentionally create a fresh short-lived cache so a morning grade is not stuck on pregame logs.

To send logs plus saved history to another machine for review:

```powershell
Compress-Archive -Path C:\Users\muski\mlb_props\logs\*,C:\Users\muski\mlb_props\outputs\history\hot_hits_*.json,C:\Users\muski\mlb_props\outputs\history\pitcher_props_*.json,C:\Users\muski\mlb_props\outputs\backtests\backtest_*.txt -DestinationPath C:\Users\muski\Desktop\mlb_props_logs.zip -Force
```

If daily backtests are not scheduled and `outputs\backtests` does not exist, omit that path from `Compress-Archive` or pull only `logs\*` and `outputs\history\pitcher_props_*.json`.

For Mac-side review of Windows pitcher history, common pull commands are:

```bash
mkdir -p /Users/colemason/mlb_props/pitcher_props_from_windows
scp colemason41@100.77.131.65:"C:/Users/muski/mlb_props/outputs/history/pitcher_props_202607*.json" /Users/colemason/mlb_props/pitcher_props_from_windows/
scp colemason41@100.77.131.65:"C:/Users/muski/mlb_props/logs/pitcher_props*.log" /Users/colemason/mlb_props/pitcher_props_from_windows/
```

The log path is top-level `C:\Users\muski\mlb_props\logs`, not `outputs\logs`.

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
- `5/5` hit-game streaks are useful but intentionally no longer dominate scoring on their own
- batting-order spots 5-7 are penalized when matchup context is weak; batting-order 8-9 remains a thin/value-only area
- positive matchup context, especially `Match >= +0.20`, is one of the cleaner support signals seen in backtests

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
- projected strikeouts, projected K rate, projected outs, and projected batters faced
- side-adjusted projected edge, where under edge is `line - projection` and over edge is `projection - line`
- Core/Lean/Watch tier rules in `mlb_props/tiers.py`, which deliberately gate Core more tightly than raw score

Pitcher presentation semantics:

- provisional confidence is the price-agnostic estimated chance that the listed side clears the line; it is not yet calibrated or an expected-value estimate
- raw score remains available as `Signal balance` in terminal/history diagnostics, but it is omitted from the public Discord card
- slate rank follows provisional confidence and remains separate from the absolute Core/Lean/Watch tier
- `Best Available` is a display role for up to three existing Lean/Watch candidates when no Core exists; it does not alter history tier or backtest scope
- workload reliability and opportunity warnings are display-only shadow context, distinct from the win-confidence percentage
- display policy lives in `mlb_props/pitcher_presentation.py`; tier policy remains centralized in `mlb_props/tiers.py`

Shadow-only pitcher research inputs, not current scoring inputs:

- last-three pitch counts, outs, batters faced, and pitches per batter faced
- days since last start and recent role continuity
- pitch-count and outs trends
- recent workload volatility and short-start frequency
- experimental pitch budget, batters faced, and outs
- opportunity confidence and workload/leash warning flags
- aggregate season, L10, and L5 strikeout rates per batter faced
- season/L5 batters-faced-per-out and the blended BF projection
- `recency-shadow-v1` projected K rate, strikeouts, side edge, and provisional confidence

Do not add these shadow values to score or tier policy merely because they are present in history. They need outcome calibration first.

## Current assumptions

- The repo runs on bundled sample data by default so it is immediately usable locally.
- Live integrations currently cover slate/probable starters, pitcher logs, live pitcher strikeout lines, and an experimental scrape path for pitcher outs recorded.
- Scrape mode is the default live line source, matching the scrape-first philosophy of the NBA repo.
- Live matchup context now uses a projected-lineup proxy built from recent actual batting orders when available, with an active-roster fallback when recent lineup signal is thin.
- Live scrape mode now emits coverage diagnostics and writes issue snapshots under `outputs/diagnostics/` when line capture is unusually thin for the slate.
- FanDuel remains the primary source for visible pitcher strikeout props and matchup context.
- Pitcher outs recorded is now wired through an experimental DraftKings scrape path. It is integrated into the same `PropLine` flow, but still needs morning/early-slate validation before we treat it as fully reliable.
- DraftKings scraping currently sees many markets but has often returned zero usable Ks/outs lines. Treat it as experimental diagnostics, not the primary production source.
- If a live board comes back empty under the primary thresholds, the runner automatically retries with a softer live fallback profile so you still get a usable practice board.
- The starter board is line-independent and is meant for daily assessment; its matchup columns are pitcher-friendly when positive and tougher when negative.
- `EXPORT_HISTORY=true` writes backtest-ready screen snapshots to `outputs/history/`, and `python3 backtest.py` reconciles saved strikeout plays the next day.
- Current schema-6 pitcher exports save `opportunity-shadow-v1` profiles, `recency-shadow-v1` alternative projections, `pitcher-confidence-provisional-v1` estimates, and `provisional-confidence-rank-v1` display rankings, but the active production model remains `pitcher-k-situational-v1`.
- History schema changes, projection-model changes, tier-policy changes, and shadow-feature changes are versioned separately. A schema bump does not by itself mean recommendations changed.
- Confidence, display-policy, and recency-shadow changes are versioned separately. The schema-6 bump records additional research history; it does not change active projections, score calculations, or Core/Lean/Watch eligibility.
- Shadow collection requires no Task Scheduler modification; pulling current `main` is sufficient.
- An early 3-5-slate shadow review is for data-quality checks only. Use a larger 50-100-candidate sample before considering production activation.
- Only exported displayed buckets are graded by the normal backtest scopes. `model_opinions` and `starter_board` are saved for later research, but they are not the default backtest target.
- Late or in-progress slate runs can produce thin line coverage because books remove markets after games start. Do not tune the model from thin/source-failed runs.
- All-Star break, no-game days, one-game slates, and other abnormal windows can produce empty or tiny pitcher samples. Treat those as operational diagnostics rather than model-quality evidence.
- Recent pitcher reviews showed Core has looked better than the broader displayed board, but samples are still small. Watchlist should stay broad for data collection; Core should remain stricter until more evidence accumulates.
- Empty Core slates should not be filled by lowering the absolute standard. Use relative slate rank and the explicit `Best Available` label to surface the strongest Lean/Watch opinions honestly.
- Pitcher unders are intentionally treated more cautiously than overs because they can fail on deeper-than-expected outings, traffic/extra batters, or one-game K-rate spikes.
- Known limitation: the FanDuel scraper depends on sportsbook page structure and team pages. If coverage is thin, inspect `outputs/diagnostics/scrape_sources_*.json` and task logs before assuming the model found no opportunities.
- Batter hits are wired as a live, line-independent hot-hitter board; posted sportsbook hit odds are not scraped yet.
- Hot-hits DNPs should be tracked in grading, but they are not treated as a reason by themselves to over-tighten the model. In betting workflows these often void, and early-day betting can happen before final lineups.
- Live Hot Hits Discord output is Core-first: Core names are the only recommended parlay legs, Value names are separate optional risks, and Thin names remain terminal/history research only.
- Scheduled hot-hits runs depend on probable starters and recent lineup projections available at run time. A fixed 11:30 AM run can behave differently on noon-heavy slates than on evening-heavy slates.
- The All-Star break and other abnormal slate windows can produce empty or tiny hot-hits samples. Do not tune the model from those periods alone.
- Recent model review found the broad hot-hits candidate pool was useful, but the old Discord top-eight card was too impressed by heat. The current tradeoff is a smaller Discord card that may miss some broader hits but should be easier to review.
- Backtests showed top score alone does not reliably beat the full card. Useful support patterns have included top-half batting order, positive matchup rating, and starter hit-allowed context; pure `5/5` hit streaks and `.450+` last-5 average alone were not enough.
- The production Hot Hits gate still requires at least four hit games in the last five and a `.350` last-5 average. A broader confidence research pool now exists specifically to measure whether that gate excludes stronger xBA and opportunity profiles; it does not loosen the live card.
- Hot Hits confidence is a provisional estimate of recording at least one hit, not a conversion of the additive score. It is independent of sportsbook price and cannot be interpreted as expected value.
- Baseball Savant season/last-10 xBA and expected at-bat opportunity drive the confidence shadow. Hit-game streak count is recorded as current-gate context but does not directly increase confidence.
- Existing Hot Hits Task Scheduler definitions require no change for the broader pool or confidence shadow. The default settings are in code, and the existing wrapper will export the new nested history fields after deployment.
- Known limitation: `hot_hits_report.py` fetches MLB boxscores over the network and can be slow if pointed at a large raw history folder. Prefer a focused `--since` window or a small copied directory when reviewing recent changes.

## Git workflow for the shared pitcher/hitter repo

Pitchers and Hot Hits intentionally live in the same repository because they share MLB models, sources, output utilities, history infrastructure, Windows deployment, and tests. The repository does not need to be split. Isolate simultaneous work with branches or separate worktrees, and always confirm the active branch before committing.

For the simplest sequential solo workflow:

```bash
git switch main
git pull --ff-only origin main
git status -sb
```

Then stage exact files rather than using `git add .`:

```bash
git add path/to/intended_file.py tests/test_intended_change.py
git diff --cached
git commit -m "Describe the isolated change"
git push origin main
```

For feature-branch work:

```bash
git switch main
git pull --ff-only origin main
git switch -c codex/descriptive-task-name
```

Commit and push the current feature branch with:

```bash
git push -u origin HEAD
```

Merge it intentionally through a pull request or a known fast-forward workflow, then update local `main`. A command such as `git push origin main` always pushes the local branch named `main`; it does not push the currently checked-out feature branch. The July 28 non-fast-forward error happened because the pitcher commit was on `codex/hot-hits-core-first` while local `main` was behind remote `main`. It was a branch-pointer issue, not a pitcher-versus-hitter repository conflict.

Before switching branches, preserve or commit unrelated edits. Never discard `.gitignore`, README, tier-policy, transferred-history, or local analysis changes just to make a branch operation easier.

## Handoff Notes

Focused continuation documents:

- pitcher props: `docs/PITCHER_PROPS_HANDOFF.md`
- copy-ready pitcher continuation prompt: `docs/PITCHER_PROPS_CONTINUATION_PROMPT.md`
- Hot Hits: `docs/HOT_HITS_HANDOFF.md`

The focused pitcher handoff is the canonical source for the August 17 operational state, schema-6 collection status, recent modeling lessons, and next analysis task. Keep this README as the broad shared-project reference.

### Pickup checklist

- Read `Pitcher Props Objective And Design`, `Current scoring inputs`, `Current assumptions`, and this handoff section before changing pitcher logic.
- Before changing Hot Hits scoring or Discord selection, read `Hot Hits Objective And Design`, `Hot Hits Review And Grading`, and `docs/HOT_HITS_HANDOFF.md`.
- Run `git status -sb` first and preserve unrelated uncommitted work. Pitcher Props and Hot Hits intentionally share this repository.
- Confirm the active versions in `mlb_props/version.py`. As of 2026-08-04 they are history schema `6`, model `pitcher-k-situational-v1`, tier policy `core-lean-watch-v1`, opportunity shadow `opportunity-shadow-v1`, recency shadow `recency-shadow-v1`, confidence model `pitcher-confidence-provisional-v1`, and display policy `provisional-confidence-rank-v1`.
- Treat opportunity estimates as observation-only for recommendation logic. Confidence and warning flags may be displayed, but they must not feed scores or tier decisions until the review gates below are met.
- After transferring a completed Windows collection window, review all saved learning tiers with:

```bash
python3 backtest.py --all-history --include-watch \
  --history-dir pitcher_props_from_windows/YYYY-MM-DD_to_YYYY-MM-DD
```

- Distinguish late/in-progress line-source coverage from model quality. A thin late slate is not, by itself, evidence that the projection model failed.
- Before committing, run the relevant tests and inspect the exact staged diff. Stage named files instead of using `git add .`.

### Historical status checkpoint: 2026-07-28

- Current objective: maintain a local-first MLB props research system with pitcher prop screens and a hot-hits one-hit parlay research board, plus scheduled Discord delivery from a Windows PC.
- Current implementation status: Hot Hits and pitcher props both run in live mode, send Discord notifications, and export history for later grading. Hot Hits uses MLB Stats API data only and does not scrape hit odds. Pitcher props use FanDuel scrape-first line capture, MLB Stats API pitcher/slate data, and experimental DraftKings diagnostics.
- Pitcher implementation status: baseline/version reporting landed in `c4c45ba`; shadow opportunity collection landed in `7925666`. Current `main` includes both plus the Hot Hits Core-first work from `5ce5114`.
- Immediate pitcher task at that checkpoint: collect schema-3 Windows history for several normal pregame slates without changing scoring or tiers while the first shadow sample accumulated.
- First shadow review: after 3-5 completed slates, verify population, missingness, rest dates, role flags, confidence distribution, and current-versus-shadow error. Treat this as a data-quality review.
- Activation review: wait for roughly 50-100 graded candidates, then decide which shadow inputs improve opportunity error. Any production activation must bump the active pitcher model version and receive separate validation.
- Windows deployment at that checkpoint: the existing pitcher Task Scheduler command remained correct and wrote schema-3 history automatically.
- Windows production path currently documented throughout this README is `C:\Users\muski\mlb_props`. Change the task wrapper defaults if deploying elsewhere.
- Discord webhooks are channel-specific. Use separate environment variables or wrapper configuration when different projects should post to different Discord channels; do not hard-code webhook URLs in source.
- Keep terminal output detailed. The Discord hot-hits output should stay compact until an AI summary layer is intentionally added.
- Hot Hits Task Scheduler needs no command change for the Core-first policy because the defaults live in configuration. Environment overrides must be stored for the same Windows account that runs the task.
- Do not commit transferred logs, history pull folders, `.analysis/`, `.cache/`, `outputs/`, or zip archives. They are local review artifacts.
- When changing hot-hits scoring, update `hot_hits_report.py` if the report needs to simulate the current model against old exports.
- When changing pitcher tiering, update `mlb_props/tiers.py` and verify `run_nightly.py`, `mlb_props/output.py`, and backtest history export all agree on Core/Lean/Watch buckets.
- When changing shadow opportunity calculations, update `mlb_props/opportunity.py`, bump `PITCHER_OPPORTUNITY_SHADOW_VERSION` when semantics change, and keep the active model version unchanged unless production recommendations also change.
- When activating shadow inputs in production, bump `PITCHER_MODEL_VERSION`, verify sample output changes intentionally, and compare current versus proposed results before changing `mlb_props/tiers.py`.
- Before committing, run `PYTHONPYCACHEPREFIX=.pycache python3 -m py_compile run_nightly.py backtest.py hot_hits_report.py mlb_props/*.py` from the repo root. Use `PYTHONPYCACHEPREFIX` on macOS to avoid writing bytecode under protected system cache paths.
- For pitcher changes, also run `PYTHONPYCACHEPREFIX=.pycache python3 run_nightly.py` on sample data. For live validation, run `DATA_MODE=live python3 run_nightly.py` when network access and slate timing make sense.
- For hot-hits changes, run the relevant live/report command when network access is available.
- Windows scheduled runs observed Python `3.13.14`; local Mac runs may use `python3`. Keep wrappers configurable via `PythonExe`.

### Status checkpoint: 2026-08-02

- The July 29-August 1 schema-3 Lean/Watch board went `8-4`; the broader qualified pool filtered below those tiers went `22-24`. This supports keeping the absolute tier policy intact, but the sample remains small.
- The user-facing raw score was found to be unstable across slates and non-probabilistic. It is now presented as `Signal balance`, while rank, side edge, opportunity reliability, and recommendation tier are separate concepts.
- `mlb_props/pitcher_presentation.py` is the source of truth for display ranking and `Best Available`; `mlb_props/tiers.py` remains the source of truth for Core/Lean/Watch eligibility.
- When no Core exists, only already-eligible Lean/Watch candidates can receive the `Best Available` display role. Never grade that role as a new tier or describe it as Core.
- Schema-4 exports add `display_policy_version` and `display_rankings`. Backtests remain based on the saved Core/Lean/Watch candidate arrays, so historical outcome scope is unchanged.
- The active projection model and tier policy versions remain unchanged because this update does not alter projections, raw score calculations, or recommendation eligibility.
- Windows Task Scheduler needs no definition change. Pull the new commit and let the existing pitcher wrapper produce the updated console, Discord, and history formats.

### Status checkpoint: 2026-08-04

- Confidence percentages were added as a versioned shadow/presentation layer after deciding that an abstract additive score was hard for future Discord users to interpret.
- The percentage estimates win probability at the posted line only. It does not include sportsbook price, implied probability, vig, expected value, or staking advice.
- `mlb_props/pitcher_confidence.py` is the source of truth for the provisional distribution, reliability shrinkage, `50-68%` cap, and Strong/Solid/Cautious/Higher Risk labels.
- `mlb_props/pitcher_presentation.py` ranks eligible candidates by provisional probability. `mlb_props/tiers.py` remains the unchanged source of truth for Core/Lean/Watch.
- Discord leads with percentage and label, calls opportunity context `Workload reliability`, and omits signal balance. Terminal/history keep the internal signal for research.
- Backtest confidence calibration is the next review path. Do not tune label boundaries from a few slates; target at least 50-100 resolved confidence estimates.

### Hot Hits confidence checkpoint: 2026-08-04

- Thin Aug. 2-4 boards confirmed that last-5 results have structural authority at the initial gate and again in scoring/support/tiering. A zero-Core slate can also result from strict Core support requirements, so the gate is not the only cause of thin action.
- The production screen and `core-first-v1` Discord card remain unchanged. The new `screen_hot_hitters_with_research()` path returns the same production candidates plus a broader observation pool.
- `mlb_props/hot_hits_confidence.py` owns `hot-hits-confidence-provisional-v1`. It uses xBA/contact reliability, expected at-bats, batting order, a capped matchup adjustment, and an explicit results-based fallback when Savant is unavailable.
- Terminal output now adds a separate confidence research table. Discord continues to render only the existing production candidates and does not read confidence.
- New history exports save `confidence_research_pool`, per-profile current-gate failures and confidence estimates, and top-level confidence metadata. `hot_hits_report.py` grades the broader pool while preserving the production boundary for policy simulation.
- Do not adjust the current gate, confidence weights, or label cutoffs from the first few slates. First verify collection coverage and missingness, then target at least 50-100 resolved research profiles across normal slates for calibration and gate-exclusion analysis.
- The active projection and tier-policy versions remain unchanged. Existing Windows Task Scheduler definitions still require no modification.
- L5 outcome performance was identified as potentially overrepresented because it enters the active K-rate projection, line deltas, recent hit bonuses/penalties, volatility, and several workload/risk adjustments. The working hypothesis is not that all L5 data is bad: L5 opportunity evidence should remain strong, while raw strikeout outcomes and prop hit streaks should receive less authority.
- `mlb_props/recency_shadow.py` now records the alternative `50% season / 30% L10 / 20% L5` aggregate K/BF projection plus a season/L5 BF-per-out blend. It is leakage-safe and research-only.
- Schema-6 backtests add L5 outcome-band auditing and current-versus-recency-shadow K MAE, BF MAE, bias, and Brier comparisons. Do not promote the formula from a short or abnormal-slate sample.
- No scheduled-task definition change is needed for schema 6. Once deployed, the existing scheduled run will collect the new nested fields automatically.

### Status checkpoint: 2026-08-17

- `8b23aab` is deployed on Mac/remote main and Windows main. The active production model and tier policy remain unchanged; schema 6 adds research history only.
- Windows collected 12 schema-6 snapshots from August 5 through August 17 containing 195 qualified candidates and 195 populated recency shadows. August 16 is missing because a machine-wide outbound HTTPS/TLS incident stopped Tennis, WNBA, Hot Hits, pitcher props, and Discord during the morning; service recovered later that day.
- Both MLB scheduled tasks succeeded again on August 17. Pitcher props found 17 FanDuel K lines, exported history, and sent Discord. No scheduler change or retry framework was made; reconsider bounded retries only if the incident recurs.
- The schema-6 sample has not been graded. The newest Windows backtest still ends August 2. The next task is a read-only Core/Lean/Watch backtest comparing active versus recency-shadow K/BF error, confidence calibration/Brier, and L5 outcome bands before any production tuning.
- Use `docs/PITCHER_PROPS_HANDOFF.md` as the canonical pitcher handoff and `docs/PITCHER_PROPS_CONTINUATION_PROMPT.md` to start a fresh agent conversation.
