# Pitcher Props Handoff

Status checkpoint: 2026-09-24

This is the technical/model handoff for pitcher strikeouts. For current project
status and next actions, start with `docs/NEXT_CHECKIN.md`; for production
operations, use `docs/AZURE_VM_OPERATIONS.md`. Hot Hits has a separate handoff
in `docs/HOT_HITS_HANDOFF.md`.

## Current operational status (2026-09-24)

- Azure VM (`ssh azure`) runs collection, board publication, and daily grading
  with systemd timers. Windows is retired; all Windows paths and task commands
  in older notes are archival and must not be executed.
- Code/doc changes are made and tested on the Mac, committed/pushed, then pulled
  onto Azure with `git pull --ff-only`. Grading runs on Azure, not the Mac.
- The latest graded-board learning review records full pitcher boxscore lines,
  team situation, exact source-input joins, and noon/afternoon market movement.
  See `docs/NEXT_CHECKIN.md` for the current sample and remaining regular-season
  review plan.
- Active pitcher versions are defined in `mlb_props/version.py` (history schema
  8, `pitcher-k-hybrid-v2`, `core-lean-watch-v2`, and their versioned shadows).
- Do not change production selection, tiers, or model formulas from the short
  Sep 10–22 analysis alone. The current objective is continued full-board
  collection and learning; no family or play filters have been adopted.

## Purpose

The pitcher system is an MLB prop research pipeline focused primarily on
pitcher strikeouts. Its practical job is to turn FanDuel strikeout lines,
pitcher skill, projected workload, matchup context, and risk into a readable
board. Production collection and publication run on Azure; the Mac is used for
code, analysis, and review.

The project is trying to become useful every day without pretending every slate has a high-confidence wager. Core, Lean, and Watchlist are separate recommendation tiers. Core stays strict; Lean and Watchlist stay broad enough to surface the best available opinions and collect learning data.

The long-term goal is a Discord board that another group could depend on. That requires honest uncertainty, calibrated probabilities, repeatable production collection, and evidence from saved pregame history. It does not justify padding Core or claiming profitable edge without sportsbook prices and calibration.

## Historical Windows checkpoint

The former Windows Task Scheduler configuration, August 2026 deployment state,
branch names, and machine-specific paths have been removed from the active
handoff. Any remaining Windows references in the technical notes below describe
historical data/implementation context only. Current checkout, VM, timers, and
unrelated working-tree state are recorded in `docs/NEXT_CHECKIN.md`; verify them
with `git status -sb` and `ssh azure` before continuing.

## Runtime And Data Flow

The production path is orchestrated by `run_forecast_pipeline.py` on Azure. Its
collection components are:

1. `run_nightly.py` loads settings and the MLB slate.
2. MLB Stats API supplies games, probable pitcher IDs, pitcher logs, and projected-lineup context.
3. FanDuel team pages are the primary strikeout-line source.
4. DraftKings Ks/outs scraping remains experimental diagnostics and often reports markets but zero usable lines.
5. `mlb_props/screener.py` builds active projections, scores, candidates, confidence estimates, and research shadows.
6. `mlb_props/tiers.py` determines Core eligibility and is the tier-policy source of truth.
7. `mlb_props/pitcher_presentation.py` ranks already-eligible plays and marks `Best Available` when Core is empty.
8. `mlb_props/output.py` renders detailed terminal output and compact Discord cards.
9. The pipeline collects pitcher props and game markets, then builds and
   publishes the board from those exact exports. `run_nightly.py` remains a
   collection component/manual CLI; the forecast board is the scheduled publisher.
10. `grade_forecast_board.py` runs on Azure after games finish, grades canonical
    plays, and writes a daily learning review. `backtest.py` remains available
    for pitcher-tier and historical research.

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

The active projection version is `pitcher-k-hybrid-v2` (history schema 8),
not the pre-August formula described in older analysis.

Active K rate:

- uses aggregate K/BF weighted `50% season + 30% L10 + 20% L5`
- applies opponent handedness K-rate context and the model's existing
  situational adjustments
- applies the existing walk-risk adjustment

Active opportunity:

- projected outs retain the situational opportunity projection, informed by
  recent workload and season baseline
- recent pitch count, outs stability, quality starts, short starts, walks, earned runs, opponent outs factor, and moneyline adjust the result
- active projected batters faced uses L5 batters-faced-per-out

Other recommendation and presentation components also consume recent-form
features. Consult the implementation and tests before inferring that a single
feature's weight is equivalent to its total influence.

The working lesson is not “remove L5.” Separate it by meaning:

- recent raw strikeouts and prop hit streaks are volatile outcome evidence and should have limited authority
- recent K/BF and walk rate are skill evidence and deserve moderate weight
- recent pitch count, outs, BF, role, leash, and short starts are opportunity evidence and deserve strong weight

The schema-6 shadow was graded and informed the Aug 31 hybrid activation. That
activation is historical; it is not a current to-do. The Sep 10–22 forecast-board
study found pitcher-K conversion errors, but that board model/population is not
the same as the saved Core/Lean/Watch tier backtest. See `docs/NEXT_CHECKIN.md`
before drawing conclusions or proposing a formula change.

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

## Saved Learning Data (current schema 8)

Pitcher history is written by the Azure collection pipeline under:

```text
outputs/history/pitcher_props_*.json
```

Schema-8 exports preserve:

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

The forecast-board grader separately grades all canonical board families on
Azure. It enriches each graded row from the exact board-recorded source exports,
captures actual pitcher boxscore lines, and writes daily learning-review JSON
and Markdown. See `docs/AZURE_VM_OPERATIONS.md` for the artifacts and pull
workflow. Old schema-6/7 collection counts below prior handoff versions are
historical and must not be treated as the current grading state.

## Historical Modeling Notes (August 2026)

1. Pitcher Ks are too volatile for an NBA-style `4/5` consistency model. Recent prop results should not dominate situational projection.
2. Opportunity matters disproportionately. Short outings, deeper-than-expected outings, traffic, pitch count, and leash can overwhelm K-rate skill.
3. Core should remain absolute. Relative rank and `Best Available` solve the empty-Core usability problem without pretending weak plays are Core.
4. An additive score is not a user-friendly confidence measure. Keep it as `Signal balance`; show a separate probability with explicit provisional language.
5. Confidence without price can communicate forecast strength but cannot establish betting value. Do not claim EV or PnL edge.
6. Grade singles for calibration. Combining picks multiplies uncertainty and usually worsens long-run bankroll variance.
7. Trade-deadline roster movement is handled primarily through stable MLB player IDs plus current slate/probable-pitcher and projected-lineup data. Do not add 15-minute roster polling unless evidence shows a real failure. Verify real lineups when available.
8. Late or in-progress runs can have thin sportsbook coverage. On August 16 at approximately 4:28 PM, a diagnostic run found only four FanDuel K lines on a 15-game slate. That was a timing/source-coverage issue, not model failure.
9. Shared pitcher and Hot Hits code in one repository is intentional. Branch/worktree discipline is the solution; splitting the repository is not currently needed.

## Archived August 16 Connectivity Incident

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

## Daily Unders Card (pre-registered)

The Daily Card is the daily-volume product: a separate pre-registered policy, not a Core/Lean/Watch tier. It answers the user goal of "a few accurate picks each day" without weakening the absolute Core standard.

- Gates (`daily-unders-card-v1`, frozen 2026-08-31): PITCHER_STRIKEOUTS, UNDER side, line <= 5.5, |projected Ks - line| <= 1.0, drawn from all qualified candidates including non-displayed tiers, ranked by calibrated confidence, capped at 4.
- Derivation: combined Aug 5-29 grading (n=97, 61.9% hit, stable 61.8%/61.9% across both windows, ~4.4 plays/day vs 52.4% breakeven and 55.5% always-under baseline).
- Selection lives in `mlb_props/daily_card.py`; rendering in `mlb_props/output.py` (`render_daily_card`, `_daily_card_embed_field`); the nightly export saves `daily_card` rows plus `daily_card_policy_version` (schema 8, additive).
- The morning backtest (`backtest.py`) resolves and reports the delivered card automatically; `pitcher_grading.daily_card_summary` computes hit rate, units at -110, and the always-under baseline over the same snapshots for weekly review.
- Pre-registered success rule: trust at >= 55% with n >= 100 graded plays; marginal at 52.4-55% (requires price-based EV check); kill below 52.4%. Changing the gates requires a new policy version and a separate commit.
- Do not pad the card. Zero-pick days are valid outcomes and are reported honestly.

## Next Work

The schema-6 shadow review and Aug 31 activation are complete. The current
project work is the full-board learning plan in `docs/NEXT_CHECKIN.md`: continue
collecting the full board through the regular season, pull daily grade/review
artifacts from Azure to the Mac, assess repeatability at the season-end review,
and keep playoff data separate. No family or individual-play filter has been
adopted from the short Sep 10–22 sample.

## Pickup Checklist

1. Read `docs/NEXT_CHECKIN.md` and `docs/AZURE_VM_OPERATIONS.md` for current
   status and operations; read this file for pitcher model design.
2. Run `git status -sb` and preserve every unrelated edit.
3. Inspect active versions in `mlb_props/version.py` and recent commits.
4. Verify Azure timers, board/grade artifacts, and task logs before interpreting
   missing coverage or an empty slate.
5. Use the daily learning-review artifacts and point-in-time exports for
   analysis. Keep regular-season and playoff samples separate.
6. Do not tune production gates or remove families from the short initial
   sample; pre-register and validate any future aspect-level hypothesis.
7. Run the full test suite and compilation checks before committing. Stage only
   named files; never discard unrelated work or use `git add .`.
