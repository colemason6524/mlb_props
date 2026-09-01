# Pitcher Props Handoff

Status checkpoint: 2026-08-17

This is the canonical handoff for the pitcher-strikeout side of the shared MLB repository. Read it before changing pitcher projection, scoring, confidence, tiers, output, history, backtests, or Windows scheduling. Hot Hits remains in the same checkout but has its own handoff in `docs/HOT_HITS_HANDOFF.md`.

## Purpose

The pitcher system is a local-first MLB prop research and daily Discord pipeline focused primarily on pitcher strikeouts. Its practical job is to turn FanDuel strikeout lines, pitcher skill, projected workload, matchup context, and risk into a readable board.

The project is trying to become useful every day without pretending every slate has a high-confidence wager. Core, Lean, and Watchlist are separate recommendation tiers. Core stays strict; Lean and Watchlist stay broad enough to surface the best available opinions and collect learning data.

The long-term goal is a Discord board that another group could depend on. That requires honest uncertainty, calibrated probabilities, repeatable production collection, and evidence from saved pregame history. It does not justify padding Core or claiming profitable edge without sportsbook prices and calibration.

## Current Production State

Production commit: `8b23aab Collect pitcher recency projection shadow`

Deployed versions (production, as of 2026-08-31):

- history schema: `7`
- active projection model: `pitcher-k-hybrid-v2`
- tier policy: `core-lean-watch-v2`
- opportunity shadow: `opportunity-shadow-v1`
- recency shadow: `recency-shadow-v1`
- confidence model: `pitcher-confidence-calibrated-v2`
- display policy: `provisional-confidence-rank-v1`

2026-08-31 changes, each a separate versioned commit: the recency-shadow
aggregate K/BF blend is now the production K-rate (`pitcher-k-hybrid-v2`); Core
requires the UNDER side with a 1.5 edge cap and a 0.55 no-vig market-probability
requirement when prices exist (`core-lean-watch-v2`); confidence applies a
0.55 calibration shrink capped at 57% (`pitcher-confidence-calibrated-v2`).

### Local branch schema-7 research candidate (not yet deployed to Windows)

The `codex/pitcher-recency-shadow` local branch contains a schema-7 research candidate that is NOT yet on the Windows production checkout. Review and deploy it separately, then update this table.

- history schema: `7` (audit identity, delivery ledger, sanitized settings)
- forecast board: `forecast-board-v1` (research-only)
- price shadow: `price-shadow-v1` (research-only)

Windows production checkout:

```text
C:\Users\muski\mlb_props
```

Scheduled task:

```text
Task name: MLB Pitcher Plays
Schedule: daily at 11:35 AM America/Detroit
Action: C:\Windows\System32\cmd.exe /c ""C:\Users\muski\mlb_props\scripts\run_pitcher_props_task.cmd""
Working directory: C:\Users\muski\mlb_props
```

The CMD wrapper passes `-ExportHistory` and `scheduled full pregame run` to the PowerShell wrapper. No scheduled-task definition change is needed for schema 6.

Verified on 2026-08-17:

- Windows `main` matched `origin/main` at `8b23aab`
- task state was `Ready`
- task result was `0`
- Python was `3.13.14`
- Discord reported `sent`
- history exported to `pitcher_props_20260817T153547Z.json`
- latest slate had 17 FanDuel strikeout lines, 11 qualified candidates, no Core, one Lean, and three displayed Watch plays

Expected untracked Windows items are `New Text Document.txt`, `logs/`, and `run_hot_hits_task.ps1`. Do not delete them as cleanup without confirming ownership.

The Mac checkout was left on `codex/pitcher-recency-shadow`, with `main`, `origin/main`, and that branch all at `8b23aab` before this documentation update. Two older local edits were intentionally preserved and were not part of the pitcher deployment:

- `.gitignore`: ignores transferred Windows history folders
- `mlb_props/tiers.py`: adds a source-of-truth comment only

Always run `git status -sb` before staging. Stage named files; do not use `git add .` in this shared pitcher/Hot Hits checkout.

## Runtime And Data Flow

The daily pitcher path is:

1. `run_nightly.py` loads settings and the MLB slate.
2. MLB Stats API supplies games, probable pitcher IDs, pitcher logs, and projected-lineup context.
3. FanDuel team pages are the primary strikeout-line source.
4. DraftKings Ks/outs scraping remains experimental diagnostics and often reports markets but zero usable lines.
5. `mlb_props/screener.py` builds active projections, scores, candidates, confidence estimates, and research shadows.
6. `mlb_props/tiers.py` determines Core eligibility and is the tier-policy source of truth.
7. `mlb_props/pitcher_presentation.py` ranks already-eligible plays and marks `Best Available` when Core is empty.
8. `mlb_props/output.py` renders detailed terminal output and compact Discord cards.
9. `run_nightly.py` sends Discord and exports a versioned JSON history snapshot.
10. `backtest.py` resolves saved strikeout predictions against final MLB game logs.

Important files:

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
- `scripts/run_pitcher_props_task.cmd`
- `scripts/run_pitcher_props_task.ps1`
- `scripts/run_pitcher_props_backtest_task.cmd`
- `scripts/run_pitcher_props_backtest_task.ps1`
- `tests/test_pitcher_confidence.py`
- `tests/test_pitcher_presentation.py`
- `tests/test_opportunity_shadow.py`
- `tests/test_recency_shadow.py`
- `tests/test_backtest.py`

## Active Projection And L5 Influence

The active model is situational, but L5 enters more than once.

Active K rate:

- averages each start's K/BF rate
- uses `60% L5 + 40% season`
- adds opponent handedness K-rate context
- adjusts for opponent patience and recent pitcher walk risk

Active opportunity:

- projected outs begin with `60% L5 outs + 40% season outs`
- recent pitch count, outs stability, quality starts, short starts, walks, earned runs, opponent outs factor, and moneyline adjust the result
- active projected batters faced uses L5 batters-faced-per-out

L5 also enters line deltas, recent hit-rate adjustments, workload stability, volatility flags, control risk, and several score bonuses/penalties. This creates more total recency influence than the visible K-rate formula alone suggests.

The working lesson is not “remove L5.” Separate it by meaning:

- recent raw strikeouts and prop hit streaks are volatile outcome evidence and should have limited authority
- recent K/BF and walk rate are skill evidence and deserve moderate weight
- recent pitch count, outs, BF, role, leash, and short starts are opportunity evidence and deserve strong weight

Do not change the active formula until the schema-6 shadow is graded.

## Recommendation And Presentation Semantics

`mlb_props/tiers.py` owns Core policy. Presentation must never silently promote Lean or Watch to Core.

- Core is the strictest, most actionable tier.
- Lean is supported but below the Core standard.
- Watchlist is a broader learning and higher-risk tier.
- `Best Available` is only a display role for up to three existing Lean/Watch candidates when Core is empty.
- An empty Core slate is valid and should not be filled by lowering the standard.

The old additive score is now called `Signal balance`. It is a diagnostic, not a probability and not comparable across slates. A score of zero means positive and negative adjustments canceled; it does not mean zero confidence.

Public Discord output leads with provisional confidence, side, line, projection edge, and workload reliability. Terminal and history retain the raw signal for diagnosis.

## Calibrated Confidence

`pitcher-confidence-calibrated-v2` estimates the price-independent chance that the listed side clears the posted line. It is not a conversion of signal score.

It uses an overdispersed count distribution around projected strikeouts or outs, incorporates opportunity/volatility uncertainty, shrinks toward 50% when workload reliability, sample size, or risk flags are weak, and then applies a first-pass calibration shrink of `0.55` fitted on the graded schema-6 sample (August 5-20). That grading showed every old forecast band above 57% was overconfident: `60%+` observed 40.0% and `57-59%` observed 42.4%, while mid bands roughly matched.

Labels:

- `60%+`: Strong (intentionally unreachable until a larger sample proves a deserving band)
- `57-59%`: Solid (only the `57%` cap value lands here)
- `54-56%`: Cautious
- `51-53%`: Higher Risk
- `50%`: No Pick

Safeguards:

- capped at `57%`
- `calibration_status: CALIBRATED_V1`, `calibration_shrink: 0.55` recorded on every estimate
- `price_included: false`
- cannot change active projection, qualification, or score; Core tier now gates on market no-vig probability separately
- low reliability and risk only shrink toward 50%; they cannot manufacture edge

These percentages are not expected value, profitability, or staking recommendations. Singles are the correct unit for calibration; parlays compound estimation error and correlation. FanDuel both-side prices are collected in the price shadow as of commit `8de164b`; they feed the Core market-support gate and history, but confidence itself remains price-agnostic by design.

## Research Shadows

### Opportunity shadow

`opportunity-shadow-v1` records recent pitch counts, outs, BF, pitches per BF, rest, role continuity, workload trends, volatility, short starts, experimental pitch budget, projected BF/outs, reliability, and warning flags.

It is observation-only. Earlier review found 12 of 17 losses (`70.6%`) in the July 20–27 sample were classified as opportunity-related. That made workload/leash modeling the first research priority, but the sample was not large enough to activate the shadow.

### Recency projection shadow

`recency-shadow-v1` was deployed in `8b23aab` to test whether recent strikeout outcomes are overweighted.

It uses:

- aggregate K divided by aggregate BF rather than averaging per-game rates
- `50% season + 30% L10 + 20% L5` K/BF
- existing matchup adjustments
- L10 walk-risk adjustment
- the active projected-outs value, preserving the strong recent workload signal
- BF/out blended `60% L5 + 40% season`
- strict `game_date < screen_date` filtering

Each qualified candidate saves the shadow projected K rate, Ks, outs, BF, side edge, and separate provisional confidence. It does not change production output.

Backtests can compare active versus shadow K/BF bias and MAE, confidence Brier score, and performance across L5 hit bands (`0-1/5`, `2/5`, `3/5`, `4-5/5`). The comparison only covers candidates admitted by the active model; it cannot prove how excluded lines would have performed.

## Saved Learning Data

Schema-6 history is written under:

```text
outputs/history/pitcher_props_*.json
```

Each export preserves:

- model, schema, tier, confidence, display, opportunity-shadow, and recency-shadow versions
- screen date, export time, settings, run note, and line coverage diagnostics
- every qualified candidate before display filtering
- saved Core, Lean, and Watch arrays
- projected outs, BF, K rate, Ks, line edge, signal, flags, and recent metrics
- opportunity shadow
- recency shadow
- active and shadow confidence estimates
- display rankings and exact recommendation/display roles
- line-independent starter board/model opinions

### Schema-7 additions (local branch research candidate)

When schema 7 is deployed, each export additionally preserves:

- sanitized settings with `odds_api_key` removed
- a `slate_games` array (game id, game time UTC, teams, probable pitchers and IDs) for strict pregame checks
- a `discord_delivery` ledger (enabled/attempted/sent time/ok/status/error/embed titles/field titles)
- per-candidate audit identity: `event_id`, `line_source`, and `line_collected_at` (tied to the PropLine)
- a `forecast_rows` board capturing every evaluated line, including non-qualifying ones and their qualification reason
- per-candidate `price_shadow` when the line source exposes both-side prices (`over_price`, `under_price`, implied and no-vig probabilities)

The local grader (`mlb_props/pitcher_grading.py`) consumes schema-7 identity for strict grading: it only grades rows whose snapshot was strictly pregame and (optionally) delivered, resolves doubleheaders exactly via `event_id`, and reports price-shadow support.

As of 2026-08-17, Windows had:

- 12 schema-6 scheduled snapshots
- screen dates from August 5 through August 17
- no August 16 snapshot because all morning outbound HTTPS requests timed out
- 195 qualified candidate profiles
- 195/195 populated recency-shadow profiles

This is enough collected volume to begin the first recency-shadow review, but it is not yet resolved evidence. The newest saved Windows backtest was still `backtest_2026-07-01_to_2026-08-02_all_history.txt`; schema-6 history has not been graded.

## Lessons From The Recent Development Sequence

1. Pitcher Ks are too volatile for an NBA-style `4/5` consistency model. Recent prop results should not dominate situational projection.
2. Opportunity matters disproportionately. Short outings, deeper-than-expected outings, traffic, pitch count, and leash can overwhelm K-rate skill.
3. Core should remain absolute. Relative rank and `Best Available` solve the empty-Core usability problem without pretending weak plays are Core.
4. An additive score is not a user-friendly confidence measure. Keep it as `Signal balance`; show a separate probability with explicit provisional language.
5. Confidence without price can communicate forecast strength but cannot establish betting value. Do not claim EV or PnL edge.
6. Grade singles for calibration. Combining picks multiplies uncertainty and usually worsens long-run bankroll variance.
7. Trade-deadline roster movement is handled primarily through stable MLB player IDs plus current slate/probable-pitcher and projected-lineup data. Do not add 15-minute roster polling unless evidence shows a real failure. Verify real lineups when available.
8. Late or in-progress runs can have thin sportsbook coverage. On August 16 at approximately 4:28 PM, a diagnostic run found only four FanDuel K lines on a 15-game slate. That was a timing/source-coverage issue, not model failure.
9. Shared pitcher and Hot Hits code in one repository is intentional. Branch/worktree discipline is the solution; splitting the repository is not currently needed.

## August 16 Connectivity Incident

On the morning of 2026-08-16, multiple independent Windows jobs failed across different domains:

- Tennis Abstract SSL handshake timeout at 9:00 AM
- Bovada and Discord webhook SSL handshake timeouts at 10:00 AM
- ESPN WNBA SSL handshake timeout at 11:00 AM
- Hot Hits and pitcher MLB requests timed out at 11:30/11:35 AM
- the 3:00 PM tennis job and Discord delivery succeeded

Windows recorded no Wi-Fi disconnect. The evidence points to a temporary outbound HTTPS/TLS path failure affecting the machine, router, ISP, or upstream routing. It was not a Discord configuration problem and not an MLB model failure.

The current decision is to treat this as a one-off. No retry framework or scheduler change was made. If it recurs, implement shared HTTP retries with bounded backoff, independent Discord retries, safe cache fallbacks, and a delayed recovery task. Also repair the pitcher/Hot Hits PowerShell native-command logging, which captured only the first traceback line because `$ErrorActionPreference = "Stop"` treated stderr as a terminating `NativeCommandError` before the temporary output could be appended.

Both MLB scheduled tasks recovered normally on 2026-08-17 and sent Discord successfully.

## Important Assumptions And Limitations

- FanDuel scraping is the primary line source and is structurally fragile.
- DraftKings Ks/outs parsing remains diagnostic.
- Exact Over/Under prices are absent, so confidence is not value.
- Park factor is general run environment, not a strikeout-specific park factor.
- Projected lineups are recent-lineup proxies with active-roster fallback until official lineups are available.
- Player IDs protect continuity across trades; team context and lineups must still reflect the current slate.
- Confidence is calibrated (v1, small sample); both shadows remain uncalibrated research layers.
- Shadow presence in JSON is not evidence of improvement.
- The normal backtest scopes grade saved displayed tiers, not every line evaluated or every line excluded by the active gate.
- Abnormal slates, late runs, source failures, All-Star breaks, pending outcomes, and DNP/no-start cases must not drive tuning.
- Unders remain riskier because deeper outings, extra BF, and K-rate spikes can defeat them.

## Next Logical Task

The schema-6 grading task that used to live here is complete; its findings produced the 2026-08-31 activation commit set (hybrid K projection, Core gate rebuild, confidence recalibration). Do not stack further scoring changes on top until the new gates have their own graded sample.

The next tasks are prospective, not retrospective:

1. Verify the new pipeline on 3-5 completed slates: price-shadow coverage on every eligible candidate, `model_version` / `tier_policy_version` / `confidence_model_version` correct in exports, and Core appearing only for market-backed unders.
2. Extend `.analysis/first_hunt_under_ks.py` into a repeatable weekly grader (one command, combined schema-6/7 rows, price-shadow EV versus no-vig baseline, saved to `.analysis/`).
3. Pre-register an explicit unders policy (for example UNDER + line <= 4.5 + LOW/MED opportunity confidence) and grade it daily against the always-under baseline. The August edge (59.8% vs 55.5%) was not significant; September is the test window.
4. Grade the new Core gates and calibrated confidence bands once roughly 50-100 new candidates resolve. If the `0.55` no-vig Core threshold proves too tight or too loose against accumulated prices, change it as a separate `core-lean-watch-v3` commit.
5. Build the game-markets line-movement report (morning versus evening snapshots, starter-change detection) once two full weeks of collection exist.

Keep the discipline that worked so far: pregame snapshots only, abnormal slates excluded from conclusions, and every production change as its own versioned commit.

## Pickup Checklist

1. Read `README.md` fully, especially pitcher objective/design, scheduling, current scoring inputs, assumptions, and handoff notes.
2. Read this file and `docs/PITCHER_PROPS_CONTINUATION_PROMPT.md`.
3. Run `git status -sb` and preserve unrelated edits.
4. Inspect the active versions in `mlb_props/version.py`.
5. Review `run_nightly.py`, `backtest.py`, screener, tiers, confidence, presentation, both shadows, output, sources, and task wrappers.
6. Inspect Windows production directly with `ssh windows`.
7. Confirm task result, latest history, coverage diagnostics, and deployed commit before interpreting an empty board.
8. Grade the current window's history before proposing any new pitcher formula; the schema-6 grade lives in `.analysis/schema6/pitcher_schema6_report.md` and the unders analysis in `.analysis/first_hunt/`.
9. Run `PYTHONPYCACHEPREFIX=.pycache python3 -m unittest discover -s tests` and compilation checks before committing.
10. Stage named files and inspect `git diff --cached`; never discard local transfer-ignore or tier-comment edits.
