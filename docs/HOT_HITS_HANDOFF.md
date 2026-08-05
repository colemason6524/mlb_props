# Hot Hits Handoff

Status checkpoint: 2026-08-04

## Purpose And Operating Goal

Hot Hits is a line-independent MLB research tool for identifying one-hit parlay candidates. It uses MLB Stats API data and does not fetch, scrape, or score sportsbook odds.

The practical objective is to maximize the chance that every recommended leg records a hit. A normal recommended card may contain three or four Core legs, but a stable one- or two-leg card is preferable to padding a weak slate. Value candidates exist for slim days and continued research; they are optional higher-risk plays, not automatic recommended parlay legs.

Keep terminal output broad and detailed for research. Keep Discord compact and actionable.

## Current Production Status

Commit `5ce5114` promoted the Core-first Hot Hits work to `main`.

Production Discord uses policy `core-first-v1`:

- recommend up to four Core candidates
- allow zero, one, or two Core candidates without forcing more legs
- show no Value candidates when four Core candidates qualify
- show at most one optional Value candidate when three Core candidates qualify
- show at most two optional Value candidates when zero to two Core candidates qualify
- label Value as optional, higher risk, and play-at-your-own-risk
- never show Thin candidates on Discord
- do not include Value in the recommended Core parlay

The former six-name policy remains available as `current-v1` for historical comparison or temporary rollback. The scoring model and eligibility gates were not changed during the Core-first rollout; the production change was card construction and presentation.

`contact-quality-shadow-v1` and `hot-hits-confidence-provisional-v1` are the current observation layers. They are deliberately non-production: Baseball Savant xBA, expected at-bat opportunity, and the provisional one-hit estimate are collected for study without changing the production card.

## Current Data Flow

1. `run_hot_hits.py` loads settings and the MLB slate.
2. MLB Stats API sources collect probable starters, projected bats, batter logs, pitcher logs, and optional BvP context.
3. `mlb_props/hot_hits.py` produces the unchanged production candidates plus a broader confidence research pool and records current-gate failures.
4. `mlb_props/hot_hits_policy.py` owns shared scoring, support, tier, eligibility, sorting, and card-selection rules.
5. `mlb_props/output.py` renders the full terminal board and the compact Core-first Discord message.
6. `run_hot_hits.py` enriches the broader pool with Savant data, attaches provisional confidence, sends Discord from production candidates only, and exports both populations when history export is enabled.
7. `hot_hits_report.py` grades exported research profiles against MLB boxscores while limiting simulated production cards to profiles that cleared the production screen.

After both populations are built, `run_hot_hits.py` optionally makes cached Baseball Savant Statcast CSV requests in batches of 25 batter IDs. Every request ends on the day before `screen_date`, so same-day outcomes cannot leak into the pregame profile. Successful batches remain usable if another batch fails. The resulting `contact_quality_shadow` and confidence estimate are attached before terminal rendering and history export, but neither is read by production scoring or Discord selection.

Important implementation files:

- `run_hot_hits.py`
- `mlb_props/hot_hits.py`
- `mlb_props/hot_hits_policy.py`
- `mlb_props/hot_hits_confidence.py`
- `mlb_props/output.py`
- `mlb_props/config.py`
- `hot_hits_report.py`
- `scripts/run_hot_hits_task.ps1`
- `scripts/run_hot_hits_task.cmd`
- `tests/test_hot_hits_current_behavior.py`
- `tests/test_hot_hits_report.py`
- `mlb_props/sources/baseball_savant.py`
- `tests/test_baseball_savant_contact.py`
- `tests/test_hot_hits_confidence.py`

## Contact-Quality Shadow V1

The shadow layer records:

- season xBA and last-10-game xBA, with tracked batted-ball xBA plus strikeouts in the opportunity denominator
- contact-only xBA over the latest 25 tracked batted balls
- hard-hit rate at the Statcast threshold of at least 95 mph
- sweet-spot rate for launch angles from 8 through 32 degrees
- barrel rate from Statcast launch-speed/angle classification
- average exit velocity over the same latest-25 contact window
- actual last-10 batting average minus last-10 xBA, to identify results running above or below contact
- recent average at-bats and an uncalibrated `1 - (1 - blended_xBA) ^ expected_AB` one-hit estimate
- sample confidence and observational flags such as `CONTACT_SAMPLE_THIN`, `RESULTS_ABOVE_CONTACT`, `CONTACT_UNLUCKY`, `XBA_TREND_PLUS`, and `HARD_HIT_PLUS`

The one-hit estimate is a research feature, not a market probability. It assumes independent at-bats, does not yet model the opposing starter or bullpen at the plate-appearance level, and has not been calibrated against held-out history.

Current safeguards:

- scoring and candidate qualification finish before Savant enrichment
- no row dated on or after `screen_date` is accepted by the parser
- source failure is fail-open: the normal Hot Hits run, Discord card, and history export continue
- Discord rendering does not read shadow fields
- tests assert that shadow attachment cannot change tier or card selection
- `HOT_HITS_INCLUDE_CONTACT_SHADOW=false` disables collection without changing scheduled-task commands

The previous V1 limitation has been removed: Savant now covers a broader projected-batter research population, not only production qualifiers. The research gate requires at least 10 games, 12 last-5 at-bats, batting order 1-7, season average `.230`, and one of season average `.250`, last-10 average `.240`, or last-5 average `.300`. Current production qualifiers are always retained. These are collection boundaries, not recommendation thresholds.

## Provisional Confidence Research

`hot-hits-confidence-provisional-v1` estimates the chance of at least one hit without using sportsbook price. It:

- blends season and last-10 xBA with recent weight capped at `35%`
- applies a deliberately small matchup adjustment capped at `+/- .015` per at-bat
- blends recent expected at-bats with a batting-order opportunity target
- converts per-at-bat probability to one-hit probability with `1 - (1 - p) ^ expected_AB`
- shrinks the recent/contact result toward the season anchor according to Savant reliability
- falls back to season and last-10 batting average at lower reliability when Savant is unavailable
- caps displayed estimates to `45-88%` while uncalibrated

Labels are Strong at `78%+`, Solid at `72-77%`, Cautious at `66-71%`, Higher Risk at `60-65%`, and No Pick below `60%`.

The current hit-game gate is context only for this confidence calculation. A `5/5` streak does not directly raise the estimate, and a `3/5` profile is not automatically penalized if xBA and expected opportunity are otherwise identical. `current_gate_qualified`, `current_display_qualified`, and `gate_failures` preserve the exact production boundary for later analysis.

Safeguards:

- production candidate names, scores, tiers, card limits, and Discord content are unchanged
- confidence runs over the broader pool only after production scoring finishes
- all estimates are marked `PROVISIONAL`, `UNCALIBRATED`, and `price_included: false`
- source fallback and low-reliability conditions are explicit flags, not silent substitutions
- research-only profiles cannot enter `core-first-v1`, `current-v1`, or exact-delivery grading

## Current Scoring Inputs

Scoring currently combines:

- recent hit-game frequency over the last five and ten games
- last-5 and last-10 batting average
- season batting average as an anchor
- short-term trend relative to season average
- projected batting order
- handedness matchup rating
- probable starter recent hit-allowed rate
- probable starter recent strikeout and walk rates
- optional BvP evidence when the sample is large enough

Recent model work intentionally reduced dependence on pure heat. A `5/5` hit streak or very high last-5 average is not enough by itself. Batting order, matchup, starter hit-allowed rate, season average, and last-5 average are also used as support signals.

The five Discord support signals are:

- batting order 1-4
- matchup rating at least `+0.20`
- probable starter recent hit-allowed rate at least `.260`
- season batting average at least `.280`
- last-5 batting average at least `.380`

Current Discord eligibility is:

- score at least 14, at least one support signal, and batting order no lower than seventh; or
- score at least `max(HOT_HITS_DISCORD_MIN_SCORE, 12)`, at least two support signals, and batting order no lower than seventh

Current tiers are:

- Core: score at least 14, batting order 1-4, matchup above `-0.20`, and at least two support signals
- Value: supported hot-hand profile that misses the Core standard but clears the Value gates
- Thin: research profile that does not clear Core or Value; terminal/history only

Before changing these rules, read the exact implementation in `mlb_props/hot_hits_policy.py`. Do not duplicate scoring logic in the renderer or report.

## Historical Review That Led To Core-First

The initial post-change review used Windows exports from 2026-07-20 through 2026-07-27. The broader comparison used two normal-slate windows:

- 2026-06-30 through 2026-07-12
- 2026-07-20 through 2026-07-27

The All-Star period was excluded. Across the 21 normal-slate days:

| Policy | Picks | Individual hit rate | Average card | Entire card hit |
| --- | ---: | ---: | ---: | ---: |
| Former current policy | 68 | 66.2% | 3.3 | 28.6% |
| Core-first simulation | 54 | 66.7% | 2.6 | 38.1% |

Combined former-policy tier results were:

- Core: 22/31, 71.0%
- Value: 23/36, 63.9%
- Thin: 0/1

Combined Core-first tier results were:

- Core: 21/30, 70.0%
- Value: 15/24, 62.5%
- Thin: none selected

Void-adjusted parlay findings under the Core-first simulation were:

- all available Core: 8/16 cards, 50.0%
- Core plus first Value: 4/10 cards, 40.0%
- Core plus up to two Values: 3/10 cards, 30.0%
- full Core-first card: 8/21 cards, 38.1%

The main evidence was about card construction rather than scoring. Individual selection hit rate barely changed, while smaller Core-first cards improved the full-card success rate and removed Thin and DNP exposure in the reviewed windows. Adding Value reduced the observed probability that every played leg hit.

These samples are useful but not large enough to prove new scoring weights. Do not tune scoring from these windows alone.

## Report And Grading Behavior

`hot_hits_report.py` recursively finds `hot_hits_*.json` exports and keeps the latest export for each screen date.

For `core-first-v1` and `current-v1`, the report recomputes current scores against old raw exports and then simulates the requested policy. This is necessary for comparing proposed rules against old candidate pools. Some older exports omit fields such as last-10 hit games or pitcher walk rate, so interpret older replays with that limitation in mind.

New exports include `discord_delivery` metadata:

- send status
- policy version and limits
- exact Core candidates
- exact optional Value candidates
- any legacy Thin candidates if the rollback policy was used

Beginning with the confidence research checkpoint, exports also include:

- `confidence_research_pool`: the broader profiles, sorted by provisional confidence
- current production-gate status and exact failure reasons on each profile
- full nested `confidence_estimate` and `contact_quality_shadow` data
- top-level `hot_hits_confidence` version, calibration, price, and population metadata

The report grades the broader pool when this field is present, but policy simulation filters back to `current_display_qualified` profiles. It reports observed hit rate, mean forecast, and Brier score by confidence label; current-gate pass/fail results; top-one through top-four confidence shadow cards; and successful research profiles excluded by the current gate. Older exports still grade normally from `candidates`.

Grade the current policy against old history:

```bash
python3 hot_hits_report.py \
  --history-dir hot_hits_from_windows \
  --card-policy core-first-v1 \
  --limit 4 \
  --value-limit 2 \
  --since YYYY-MM-DD \
  --through YYYY-MM-DD
```

Grade only exact successfully sent cards from new exports:

```bash
python3 hot_hits_report.py \
  --history-dir hot_hits_from_windows \
  --card-policy delivered \
  --since YYYY-MM-DD \
  --through YYYY-MM-DD
```

Parlay grading is void-adjusted:

- DNP legs are tracked and removed from the played parlay
- any played miss loses the card
- a card containing only DNP legs is no action
- pending and unknown outcomes are excluded from completed-card rates

DNPs matter operationally, but they should not be over-penalized because they may void in a real betting workflow.

## Windows Production Configuration

Production path:

```text
C:\Users\muski\mlb_props
```

The existing Task Scheduler task and wrappers remain valid. No task recreation, schedule change, argument change, wrapper edit, or new Windows environment variable is required for Core-first or confidence research. The broader-pool thresholds have code defaults and are exposed as optional `HOT_HITS_RESEARCH_*` overrides only for controlled experiments.

Production user environment:

```text
HOT_HITS_CARD_POLICY=core-first-v1
HOT_HITS_CORE_LIMIT=4
HOT_HITS_VALUE_LIMIT=2
```

These values were saved with `setx` for the `muski` account. Scheduled tasks must run under that same Windows account to inherit them. `DisplayLimit` continues to control the detailed terminal/log board, not the Core-first Discord limits.

Task logs:

```text
C:\Users\muski\mlb_props\logs\hot_hits_task.log
C:\Users\muski\mlb_props\logs\hot_hits_cmd_bootstrap.log
```

History exports:

```text
C:\Users\muski\mlb_props\outputs\history\hot_hits_*.json
```

Expected successful-run evidence:

```text
- Discord notification: sent
- History export: ...
```

## Constraints And Known Limitations

- No odds API or hit-odds scraping is integrated. Odds are only a mental model.
- The screen depends on probable starters and projected batting order information available at run time.
- Early scheduled runs may use recent-lineup proxies before official lineups are posted.
- Noon-heavy or otherwise unusual slates can behave differently from evening-heavy slates.
- All-Star breaks, tiny slates, postponements, and no-game days are operational diagnostics, not model-tuning samples.
- MLB Stats API data and probable-starter changes can affect the candidate pool between runs.
- BvP is optional and should not dominate without a meaningful sample.
- `hot_hits_report.py` requires network access for boxscore grading and can be slow across large history folders.
- Transferred folders such as `hot_hits_from_windows/` are local analysis artifacts, not source files, and must remain uncommitted.
- Do not change scoring and card construction simultaneously; otherwise the cause of any performance change becomes unclear.
- The confidence percentage has not been calibrated on held-out history and does not include odds, implied probability, vig, expected value, or bankroll guidance.
- xBA is an expected batting-average signal, not certainty; the conversion assumes similar per-at-bat opportunity and does not model each starter/bullpen plate appearance independently.
- The broader research pool still has minimum season, recent-average, sample, and batting-order boundaries. It is broader than production, not a census of every active hitter.

## Collection Plan And Next Review

Leave the current scoring and Core-first settings unchanged while collecting the confidence sample.

Revisit after roughly 7-14 normal-slate days. Transfer:

- `outputs/history/hot_hits_*.json`
- `logs/hot_hits_task.log`
- `logs/hot_hits_cmd_bootstrap.log`

Suggested local destination:

```text
hot_hits_from_windows/YYYY-MM-DD_to_YYYY-MM-DD/
```

At the next review:

1. Confirm every scheduled run completed, sent Discord, and exported history.
2. Grade exact deliveries with `--card-policy delivered`.
3. Compare Core-only, optional Value, Core-plus-one-Value, and Core-plus-two-Value outcomes.
4. Track DNPs separately and void-adjust parlay outcomes.
5. Confirm Savant batch coverage, fallback frequency, expected-at-bat values, confidence distribution, and current-gate failure reasons.
6. Compare confidence labels and top-one through top-four shadow cards, with DNPs void-adjusted.
7. Measure how often high-confidence hits and misses were excluded by the current L5 gate; compare xBA/opportunity profiles rather than only outcomes.
8. Compare exact delivered results with a fresh `core-first-v1` replay to detect policy or scoring drift.
9. Do not tune from abnormal or tiny slates.
10. Wait for at least 50-100 resolved research profiles before proposing confidence calibration or one isolated production-gate change.

The next logical decision is not odds integration. First verify that the broader pool and Savant batching are operationally reliable over 3-5 normal slates. After at least 50-100 resolved research profiles, compare confidence calibration and the outcomes of current-gate exclusions. Only then decide whether one isolated L5 gate or production-policy adjustment is justified. Any proposed production adjustment should first be simulated in `hot_hits_report.py` and introduced separately from confidence presentation.

## Validation Checkpoint

At implementation completion:

- 35 repository tests passed
- Python compilation passed
- the Core-first renderer was exercised against all eight 2026-07-20 through 2026-07-27 exports
- no Thin selections appeared
- no rendered card exceeded the intended compact limits
- current scoring parity was retained while card construction changed
