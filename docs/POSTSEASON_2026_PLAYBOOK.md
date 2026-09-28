# 2026 Postseason Playbook — How the Regular Season Is Used

**Written 2026-09-28.** Companion to [`SEASON_2026_RECAP.md`](SEASON_2026_RECAP.md).
The recap is the frozen record of what we learned. This file says what we *do*
with it now that the calendar has turned.

## Premise, in the user's words

- The postseason is new, but **regular-season trends are not discarded** — they
  still shed light on postseason picks.
- **The postseason does not reach back and change the regular season.** The
  regular season is being finalized as-is (see the recap). Take what we can
  learn, and use *that* as the basis going forward.
- **Regular season is the basis until there is enough postseason context of its
  own.** We do not have to have a postseason model before we make postseason
  plays; we just have to be honest about which evidence is which.
- Initial postseason read:
  - **Matchups are the most significant carried-forward evidence.**
  - **Pitcher K props are shaky for non-strikeout-heavy pitchers**, and for
    **pitchers who routinely did not go deep in the regular season** — they
    likely will not go deep when more is on the line.
- **Do not cautiously shadow our way into the postseason.** Output is displayed
  at full confidence; the only reader is the project owner. Shadow posture is
  not a virtue here.

The rest of this file turns that direction into explicit operating rules.

---

## Rule 0 — The era boundary

| period | dates | evidence era |
|---|---|---|
| regular season | through 2026-09-27 | `regular_season` |
| no games | 2026-09-28 | `between` |
| postseason | from 2026-09-29 | `postseason` |

`mlb_props/version.py::evidence_era(screen_date)` implements this and every
graded review carries the label. **Reporting-only — it never touches selection.**

> **Open item resolved.** Verified 2026-09-28 against the live MLB schedule API
> (`statsapi.mlb.com/api/v1/schedule`, `gameType=P,W,F,D,L`). The Wild Card round
> opens **2026-09-29** with four best-of-three series — PHI@ATL, CWS@HOU,
> BOS@NYY, CHC@SD — playing 09-29, 09-30 and (if needed) 10-01. The Division
> Series (`gameType=D`) begins **2026-10-03**. `POSTSEASON_2026_START =
> "2026-09-29"` in `mlb_props/version.py` is **correct as written; no change
> needed**.
>
> One caveat from the same pull: the feed's 10-05/10-06 DS listing did not look
> internally consistent, so **do not assert a specific DS home/away pattern from
> this document.** Re-read the schedule when the round is actually set.

Two consequences:

1. **No blend.** A postseason rate is never averaged with a regular-season rate.
   They are reported side by side, era-stamped, always.
2. **No retro-edit.** No postseason result changes the recap, the fitted engine
   artifacts, or any regular-season threshold.

---

## Rule 1 — How much weight each kind of regular-season evidence gets

### Carries forward at full weight

- **Matchup structure.** Opposing-lineup K% vs hand, whiff and contact rates,
  platoon splits, park and umpire context. This is the evidence the user named
  first, and the full-window recap backs it **directly**: `matchup_rating`
  (positive 55.9% / +0.78u · flat 44.3% / −15.65u · negative 39.5% / −23.68u) and
  `opponent_k_rate_vs_hand` (≥.230 → 52.7% · .190–.230 → 43.0% · <.190 → 33.3%)
  are the only monotone, favorably-signed separator classes in the entire study.
  **Lean on the matchup variables, not the flag badges:** the boolean flags
  (PARK_PITCHER, FREE_SWING_OPP, MATCHUP_K_PLUS, DEPTH_PLUS, EDGE_EXTREME) do not
  separate at any usable significance on the full window — every flag's q ≥ 0.68.
- **Market structure and calibration.** The market was the better probability in
  every family over the full season (Brier 0.2428 vs 0.2612; log loss 0.6785 vs
  0.7189). Better still, the full-window FDR screen ranks **the price itself**
  top — `moneyline.price_a` (n=495, p=0.0038) and the no-vig shadow
  probabilities (p=0.0058–0.0059). The most informative variable available to us
  is the price we are being offered. That relationship does not depend on the
  calendar.
- **Direction of model-vs-market disagreement.** Full window: *against*-market
  plays went n=182, 54.9%, −15.11u and were near-breakeven in every game family
  (game_ml 60% / −1.5u · game_total 57% / **+1.0u** · game_rl 59% / −1.5u), while
  *with*-market plays were n=512, 47.9%, −66.16u. The direction of the
  disagreement has been informative; the magnitude has not.
- **Price discipline.** Every price band in the full season was negative, and the
  underdog/pickem band was worst (+100..+150: 37.6%, −27.95u on 133 plays). Wins
  below −150 did not pay enough (59.3%, −15.59u on 189). Holds in a shorter series
  where prices are sharper still.

### Carries forward at reduced weight

- **Raw season-long K rate and strikeout totals.** Directionally fine, but badly
  resolved at the low end, and the regular-season pool includes many low-stakes
  starts whose pitch mix and effort do not repeat in October. Finding 8 quantifies
  it: projected K rate `< .180` → 33.9% / −21.79u (n=56) against `.≥ .260` →
  54.8% / +0.79u (n=42). The cut is real and monotone; the low bucket is exactly
  the population Rule A is about.
- **Workload / batters-faced projections.** The recap's Finding 3 says short
  outings were *not* the driver of pitcher-K losses in-sample (`workload_short`
  was −0.24u on 20 plays, against `conversion_cold` at −71.44u on 78). The user's
  prior points the other way for the postseason. Both can be true: workload was
  not the *recorded* failure mode in low-stakes games, and it becomes a
  first-order risk when every game is elimination-adjacent. Finding 8 adds the
  shape that matters — `short_starts_last_10 = 0` → 56.7% / +1.47u (n=30), but
  **≥1 → negative in every bucket**. Treat workload as a **risk to trim, not a
  signal to lean on**.
- **Team-strength and run-environment estimates.** Opponents get better and more
  uniform. A model that ranked a weak team's lineup as average will be more
  wrong, not less.
- **The seasonal ROI.** See "A ceiling, not a baseline" below.

### Re-estimated from scratch, do not import

- **Leash and depth expectations.** Playoff starters are pulled on different
  rules: shorter hooks, higher-leverage bullpen, no "let him find it in the
  6th." Anything imported from regular-season innings-per-start is suspect.
- **Bullpen usage and availability.** Rested, matched up, and deployed by
  leverage rather than by rest pattern.
- **Stakes effects.** The regular season could only sample this partially
  (Finding 4: contending-vs-contending **n=140, 61–79, 43.6%, −28.79u, −20.6%
  ROI** — 70% of all pitcher-K volume and 75% of that family's loss). In the
  postseason it is not a feature, it is the ambient condition. *Coverage caveat:
  `team_status`/`opp_status` are recorded on pitcher_k rows only in this sample,
  so for the game families stakes are an unmeasured quantity, not a proven null.*
- **Variance and series structure.** Best-of-three and best-of-five compress
  everything. Per-game variance is unchanged; the number of games to recover is
  not.

### A pre-registered verdict to carry forward: the Daily Unders Card

The Daily Card is not evidence *about* baseball; it is the one place this project
pre-committed to a rule and then measured it. It failed — **38–40, 48.7%, n=78**
over 25 snapshots (2026-09-01 → 09-27), **−5.45u at −110** and **−9.15u** priced
against a **0.5560** breakeven — inside its own declared `< 52.4%` kill band and
below a **52.8% (n=180)** always-under baseline on the same snapshots.

Two things carry forward, one does not:

- **Carry forward — the failure mechanism.** The frozen `|edge| <= 1.0` gate sent
  47% of card volume to pitchers projected below a `.180` K rate (43.2%) and 22%
  to `line <= 3.5` (41.2%). That is Finding 8's bad region, reached by a policy
  frozen before the evidence existed to prove it — **independent corroboration**
  that the low-K-rate / low-line pitcher profile is genuinely bad, not a post-hoc
  artifact of slicing. It is the strongest single argument for Rule A below.
- **Do not carry forward — the card itself**, as a staking policy for October.
  And do not re-scope it to save it: a dead pre-registration is the only
  pre-commitment this project has kept, and rewriting it would spend that.

---

## A ceiling, not a baseline

The most important synthesis in these two documents:

> **Every postseason game is the slice our model handled worst all season.**

Contending pitcher vs contending opponent: **n=140, 61–79, 43.6%, −28.79u,
−20.6% ROI** — 70% of all pitcher-K plays, 75% of that family's loss, and 33% of
the entire board's −88.27u.

So the seasonal **−12.6% ROI is an optimistic ceiling for postseason performance
under the current model**, not a neutral baseline to regress toward — and the
last five days of the regular season already looked like the harder environment,
running **−17.1% ROI on 264 plays** (09-23 → 09-27) against −9.9% over the
preceding thirteen. Read this alongside the instruction to display at full
confidence, which is not a contradiction:

- **Be confident in the play.** Show the board, the reasoning, the matchup read,
  and the price, at full confidence. The user asked for this and it is the right
  call for a single-reader tool: a shadow label adds no information and hides
  the reads that matter.
- **Do not be confident in the model's `p`.** The number the model prints is
  known to be ~16–19 points too high exactly where it claims the most edge. The
  displayed reasoning should lean on the **market price and the matchup read**;
  the model probability is one input to the display, not the headline claim.

Concretely: a postseason play is presented as *"this is the read, here is the
price, here is the matchup basis"* — not as *"the model says 62%"*.

### Three postseason-specific rules that follow

**Rule A — Non-strikeout-heavy pitchers: do not carry the play on the K projection.**
For a pitcher without a high regular-season K rate, the model's K projection is
thin and its distribution is wide, and the manager has less reason to extend a
low-K outing. The matchup evidence carries the play, or there is no play. The
projection alone is not sufficient.

The full-window recap makes this concrete rather than a matter of taste. Pitcher
K plays whose **market line is ≤ 3.5** went **17–40 (29.8%), −25.56u** — 29% of
the family's volume and **66% of its loss** — while lines ≥ 5.0 went ~52% and
−1.5u each. `projected_k_rate < .180` is the same population seen from the
model's side (33.9%, −21.79u). **The exclusion is expressible in a number the
market publishes, so use the line, not a judgment call.** Below 4.0 K, a K play
needs the matchup read (Rule C) to justify it, not the projection.

**Rule B — Pitchers who did not go deep: project at or below the regular-season
average, never above.** If a starter averaged under ~5.2 innings or rarely
reached the third time through the order in the regular season, project
postseason batters-faced **at or below** that regular-season figure. No
"postseason bump," no "he's rested now." More is on the line; the hook gets
shorter, not longer.

**Rule C — Matchup first, model second.** When the matchup read and the model
disagree on a postseason play, the matchup read wins the display. This is the
user's stated prior and it is the only evidence class with a supporting signal
in the recap.

---

## Rule 2 — Reading postseason samples

The natural failure mode is over-reading three games. Rules:

- **State n before computing any postseason rate.** No percentage without a
  denominator, in chat and in any written review.
- **Aggregate by series, not by day.** A three-game series is one observation of
  a matchup, not three. Daily slices of a 4-game wild card round are noise.
- **Never compare a postseason sub-rate to a regular-season sub-rate at a
  smaller n.** Compare to the regular-season figure only when the postseason
  denominator is ≥100.
- **Expect the market to be sharper.** Postseason markets are more efficient
  (higher limits, more attention, fewer games to price). A model that was 16
  points overconfident in September will be more overconfident, not less, as the
  edge it claims shrinks.

---

## Rule 3 — Pre-registered promotion triggers

When the postseason stops being "regular season + adjustments" and starts being
its own basis. Declare this now so it cannot be decided after seeing results.

| stage | rule |
|---|---|
| Wild Card round | Regular season stays 100% of the basis. No promotion possible. |
| Division Series and beyond | If postseason has ≥100 decided plays, recompute the same seven views from the recap, era-filtered, and compare **sign agreement** to the regular-season baseline. |
| Promotion threshold | Promote when sign agrees on **≥5 of 7** views **and** the model-vs-market calibration gap has not widened. |
| If n never reaches 100 | The postseason stays a qualitative overlay on the regular-season basis. **This is a legitimate and expected outcome** — a short playoff run may simply never produce enough evidence. Say so plainly rather than promoting on vibes. |

The seven views: overall ROI, mean model-vs-market gap, the five gap bands, the
EV-flag inversion, the pitcher K conversion/workload decomposition, the
team-situation table, and the price-band table.

Round dates are now verified (see Rule 0): Wild Card 09-29 → 10-01, Division
Series from 10-03. Everything after the DS is still unverified from this
machine — read it from the schedule rather than asserting it here.

---

## Rule 4 — Data plumbing must not care about the calendar

The production timers run year-round. If the postseason starts and the board
does not publish, that is a **process incident, not a quiet day**:

- `sports-mlb-pipeline-noon.timer` (12:15 ET) and `-afternoon.timer` (16:45 ET)
  keep firing. `sports-mlb-grade-board.timer` (06:00 ET) keeps grading.
- A postseason date with no `forecast_board_*` artifact in `outputs/grades/` is
  a stop-and-look event. Off-days are the only expected gaps, and there is
  exactly one scheduled before the opener (2026-09-28).
- Any change to which families publish is a **deliberate, announced** change, not
  a side effect of a date check.

---

## Open items

1. ~~Pull 2026-09-23 → 2026-09-27 grade artifacts and re-run the extension so the
   recap's headline becomes the true final season figure.~~ **DONE 2026-09-28.**
   Artifacts pulled from the VM (rsync of `outputs/`, 187 files); window re-run;
   the recap now carries the complete 712-play figure (356–356, −88.27u, −12.6%).
2. ~~Confirm the actual postseason opener.~~ **DONE 2026-09-28.** 2026-09-29 is
   correct (four Wild Card series, `gameType=F`); no change to
   `POSTSEASON_2026_START`.
3. ~~Resolve the pre-registered Daily Unders Card gate.~~ **DONE 2026-09-28.**
   `daily-unders-card-v1` finished its declared window at **38–40, 48.7%, n=78**
   (−5.45u at −110; −9.15u priced at a 0.5560 breakeven), inside the declared
   `< 52.4%` kill band and below a **52.8% (n=180)** always-under baseline on the
   same snapshots. The card is **not** carried into the postseason as a staking
   policy. Write-up: `SEASON_2026_RECAP.md` Finding 9.
4. **Verify a playoff-day board** end-to-end: pipeline runs → board written →
   grader settles it → era label reads `postseason` in `learning_review_*.md`.
   Era work is **deployed** (VM HEAD `a89f4fb`, 2026-09-28); only this
   confirmation remains. Time-gated: the Wild Card opens 2026-09-29 and the
   grader timer settles it at 06:00 ET 2026-09-30.
5. **Pre-register the Finding 8 pitcher-K line rule** before the postseason
   starts: *`line ≤ 3.5` K plays excluded, or require an explicit favorable
   `matchup_rating` override.* Baseline it must beat: **−25.56u on 57 plays
   (29.8%)**. Finding 9 is independent corroboration — the Daily Card lost by
   routing half its volume into exactly that bucket. Declare it now so it is a
   test rather than a reaction.
6. **Repair the always-under baseline in the standing tooling.**
   `pitcher_grading.daily_card_summary` reports `baseline_rows 0` because it
   reads `history.candidates` while `scripts/grade_daily_card_full_season.py`
   resolves only card rows. The pre-registered rule explicitly asks whether the
   card beats simply taking unders, so the baseline must be produced by the same
   tool that reports the verdict — resolve the under-candidates from card-bearing
   snapshots before summarizing. (The 52.8% / n=180 figure above was computed by
   hand this session.) Independent of any v2 decision.
7. **Optional, not yet done:** stamp `era` into the `grade_screen` result dict in
   `outputs/grades/forecast_board_<date>.json`, not just the learning review, so
   the artifact itself is self-describing.
8. **Known production behavior: an off-day run records as `failed`.**
   `run_forecast_board.required_family_errors` (`run_forecast_board.py:687`)
   fails the publish when any required family is empty and does not separate
   "no games this date" from "sources returned nothing". On 2026-09-27 the
   afternoon slot exited 1 with
   `required board family unavailable: pitcher_k=empty, game=empty`; 2026-09-28
   has zero games, so both slots today record `forecast_board: failed`, and every
   postseason off day repeats it. **Decision pending: skip-and-succeed on an
   empty slate, or keep the fail-closed gate.** No pick impact — 2026-09-29's
   first game is 18:00Z, so the noon slot still collects fresh pregame lines.
