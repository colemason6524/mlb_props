# 2026 Regular Season — Frozen Recap and Carry-Forward

**Status: FROZEN 2026-09-28.** This file is the durable record of what the
regular season taught us. It is a decision record, not a research scratchpad.

Read this together with [`POSTSEASON_2026_PLAYBOOK.md`](POSTSEASON_2026_PLAYBOOK.md),
which says how these findings are used once the postseason starts.

Three rules govern this file:

1. **The window is closed.** Nothing that happens in the postseason revises a
   number, a calibration, or a slice rate in this file. Postseason results are
   collected in their own era and compared *against* this baseline.
2. **Hypotheses are not rules.** Everything in "What we learned" is descriptive.
   Nothing here is a production selection rule, and nothing here authorized a
   change to projections, scores, tiers, or the Daily Card.
3. **Numbers carry their denominator.** No rate appears here without its `n`.

### Revision note — why this file changed on the day it was frozen

The first version of this file was written the same day with a **provisional**
headline covering 2026-09-10 → 2026-09-22 only (448 decided plays, −43.9u,
−9.9% ROI). The last five graded dates (09-23 → 09-27) were live on the
production VM but unreachable from the analysis machine at that moment, so they
were explicitly flagged as missing and the file said so.

That gap has now been closed: the grade artifacts were pulled and the study was
re-run over the complete window. **This revision replaces the provisional
figures with the true final season figures.** It is a same-day completion of an
acknowledged hole, not a retro-edit of a settled record — the provisional
numbers were never treated as final, and no finding changed *sign*. The
headline moved materially worse, and that is recorded below rather than
smoothed away.

A second, smaller revision on the same day added **Finding 9** — the resolution
of the pre-registered Daily Unders Card gate — which was still outstanding when
the file was first frozen. Finding 9 changes no other number in this file; it
adds the card's verdict and the always-under baseline the verdict is measured
against.

A third revision on the same day corrected **labels, not results**. The matchup
table below previously named its buckets after the screener's
`GOOD_MATCHUP`/`TOUGH_MATCHUP` badges, but the counts were computed from the
**±0.15** cut, and the badges fire at ±0.2. The bucket names now state the
boundaries they were actually computed from. All three `n`, all three hit rates,
and all three unit totals are byte-identical to the first freeze. This is
recorded because the ±0.15 / ±0.2 distinction is the whole basis of the
postseason `line ≤ 3.5` rule, and a label that implies the wrong cut would make
that rule look arbitrary.

---

## The window

- **Graded sample:** 2026-09-10 → 2026-09-27 — the complete regular season as
  captured by the full-board workflow. **18 graded dates, 729 canonical rows.**
- **Why it starts 09-10:** that is the first date the full-board grading workflow
  wrote `forecast_board_*` / `learning_review_*` artifacts. Earlier history exists,
  but it is pitcher-only snapshots and is not part of the full-board sample. It is
  not comparable and is not merged in.
- **Outcomes:** 712 decided (WIN/LOSS), 3 pushes, 14 voids.
- **Recorded in code:** `mlb_props/version.py` holds
  `REGULAR_SEASON_2026_SAMPLE_START` / `REGULAR_SEASON_2026_SAMPLE_END` and
  `evidence_era()`, which labels any dated review `regular_season`, `postseason`,
  or `between`. The label is reporting-only and never touches selection.
- **Reproduce it:**

```bash
python3 scripts/analyze_sep_window.py \
    --start 2026-09-10 --end 2026-09-27 \
    --root ~/mlb_props --out evidence/research
```

---

## Headline — 2026-09-10 → 2026-09-27 (FINAL, verified)

- **712** decided plays, **356–356** (**50.0%**)
- **−88.27 units** over **701 priced** plays → **−12.6% ROI**
- Mean model probability **0.586** vs mean no-vig market probability **0.532**
  — a **+5.4 point** average claimed edge on every play taken
- Brier: model **0.2612** vs market **0.2428** (log loss 0.7189 vs 0.6785)

The model claimed an edge on 100% of plays taken and hit exactly 50%. The mean
claimed edge was 5.4 points; the realized edge was zero. **The market beat the
model on Brier and log loss by a margin that grew as the sample grew.**

By family — the model is inflated in **every** family, and the market is
better-calibrated in all of them:

| family | n | W-L | hit | units | ROI | model p | market p | Brier model | Brier market |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| pitcher_k | 199 | 88–111 | 44.2% | −38.55 | −19.4% | 0.528 | 0.509 | 0.2515 | 0.2424 |
| game_ml | 184 | 96–88 | 52.2% | −24.62 | −14.2% | 0.565 | 0.540 | 0.2540 | 0.2366 |
| game_total | 163 | 82–81 | 50.3% | −7.62 | −4.7% | 0.597 | 0.506 | 0.2741 | 0.2514 |
| game_rl | 166 | 90–76 | 54.2% | −17.48 | −10.5% | 0.669 | 0.577 | 0.2679 | 0.2410 |

Totals are the worst-calibrated single case: the model said 0.597, the market
said 0.506. Run line is the most overconfident: model 0.669 against a market
0.577.

### Calibration — where the model actually breaks

| model p bucket | model n | model observed | market n | market observed |
|---|---:|---:|---:|---:|
| 0.45 | 36 | 0.556 | 88 | 0.398 |
| 0.50 | 184 | 0.451 | 233 | 0.506 |
| 0.55 | 163 | 0.491 | 148 | 0.446 |
| 0.60 | 99 | 0.505 | 120 | 0.592 |
| 0.65 | 118 | 0.551 | 54 | 0.648 |
| 0.70 | 57 | 0.526 | 12 | 0.750 |
| 0.75 | 39 | 0.513 | 2 | 1.000 |
| 0.80 | 8 | 0.500 | 0 | — |
| 0.85 | 6 | 0.500 | 0 | — |
| 0.90 | 2 | 0.500 | 0 | — |

**Above 0.70 the model is flat at ~50%.** Sixteen plays were assigned 0.80–0.90
and eight of them won. That is the whole failure in one row: the model does not
have resolution at the top of its range, and it spends its largest claimed edges
there. The market's top buckets, by contrast, observed *above* their stated
probability.

---

## The extension — what the final five days added (2026-09-23 → 09-27)

Subtracting the provisional 09-10→09-22 baseline from the final window isolates
the last five graded dates. **They were worse than the preceding thirteen days
combined.**

| | 09-10 → 09-22 | 09-23 → 09-27 | 09-10 → 09-27 |
|---|---:|---:|---:|
| decided | 448 | **264** | 712 |
| W-L | 230–218 | **126–138** | 356–356 |
| hit rate | 51.3% | **47.7%** | 50.0% |
| units | −43.86 | **−44.41** | −88.27 |
| ROI | −9.9% | **−17.1%** | −12.6% |

By family, the added five days:

| family | added n | added W-L | added hit | added units |
|---|---:|---:|---:|---:|
| pitcher_k | 70 | 30–40 | 42.9% | −14.73 |
| game_ml | 72 | 35–37 | 48.6% | −16.03 |
| game_rl | 62 | 30–32 | 48.4% | −12.41 |
| game_total | 60 | 31–29 | 51.7% | −1.24 |

Three things worth noticing:

- **Every family got worse or stayed flat, and the game families degraded most** —
  game_ml went from −8.1% to −14.2% ROI, game_rl from −4.9% to −10.5%. The
  late-season games were harder than the mid-September games, which is the
  expected direction as stakes rose and lineups/rotations were managed.
- **game_total is the one family that did not degrade** (51.7% on the added 60,
  −1.2u). It remains the best-behaved family on a per-unit basis, though it is
  also the worst-calibrated.
- **The added days alone were a −17% ROI window.** If the postseason resembles
  late-September rather than early-September, the ceiling argument below gets
  stronger, not weaker.

---

## What we learned

Each finding carries its denominator and its caveat. Read the caveat.

### 1. The loss driver is our *large* claimed edges

Model-minus-market gap, all families, full window:

| gap band | n | W-L | observed | model p | calibration gap | units |
|---|---:|---:|---:|---:|---:|---:|
| < −5pt | 73 | 42–31 | 57.5% | 0.475 | +10.0pt | −5.27 |
| −5..0pt | 109 | 58–51 | 53.2% | 0.526 | +0.6pt | −9.84 |
| 0..5pt | 156 | 90–66 | 57.7% | 0.558 | +1.9pt | **+2.91** |
| **5..10pt** | 166 | 71–95 | **42.8%** | 0.594 | **−16.6pt** | **−35.80** |
| **>10pt** | 190 | 84–106 | **44.2%** | 0.666 | **−22.4pt** | **−33.27** |

When the model sits at or below the market it is roughly calibrated and
near-breakeven (and the 0–5pt band is actually **positive**). When it claims
**≥5 points of edge it is overconfident by 17–22 points**, and those two bands
alone are **−69.07u of the −88.27u on 356 of 712 plays** — 50% of the volume
carrying 78% of the damage.

**This finding survived the longer sample and got more extreme.** On the
13-day baseline the ≥5pt bands were −37.2u of −43.9u (85% of the damage on 52%
of plays). On the full window it is 78% of the damage on 50% of plays, with the
overconfidence gap widening from 16–19pt to 17–22pt.

**Caveat:** the band edges were chosen after looking at the data. The
*mechanism* is what makes this credible — a probability layer inflated by a
roughly constant amount must lose worst exactly where it claims the most. That
argument does not depend on the band edges, and it held under 59% more data.

### 2. The EV flag is inverted in-sample

`playable` (EV ≥ 0.03) selects precisely the inflated plays:

| ev_flag | n | W-L | observed | units |
|---|---:|---:|---:|---:|
| **playable** | 384 | 169–215 | **44.0%** | **−70.82** |
| no_value | 220 | 124–96 | 56.4% | −12.10 |
| thin | 90 | 52–38 | 57.8% | **+1.65** |
| unpriced | 18 | 11–7 | 61.1% | −7.00 |

`playable` is 54% of the volume and 80% of the loss. This is the same mechanism
as Finding 1 — EV is a monotone function of the claimed edge, so it inherits the
overconfidence exactly.

**This is not a recommendation to bet `no_value`.** 56% on 220 plays at those
prices is not a +EV system; it is what a *correct* set of small-edge plays loses
to the vig. It is evidence that any gate built on the current probability layer
selects the worst plays.

### 3. Pitcher K is a *conversion* problem, not a workload problem

Decomposing realized vs projected strikeouts into a workload part (batters
faced) and a conversion part (K per batter), signed toward the side picked:

| bucket | n | W-L | observed | units |
|---|---:|---:|---:|---:|
| conversion_cold (K rate went against us) | 78 | 4–74 | 5.1% | **−71.44** |
| near_line | 42 | 16–26 | 38.1% | −13.15 |
| workload_short (pulled early) | 20 | 11–9 | 55.0% | −0.24 |
| workload_long | 18 | 17–1 | 94.4% | **+13.01** |
| conversion_hot (K rate broke our way) | 41 | 40–1 | 97.6% | **+33.28** |

The pitcher family is decided by whether the realized K rate beats the
projection. `conversion_cold` is 78 plays and −71.44u; `conversion_hot` is 41
plays and +33.28u. **These two buckets nearly account for the entire −38.55u
family result, and the model cannot tell which one it is in.**

Short outings were **not** the damage in this window — `workload_short` was
−0.24u on 20 plays. See Finding 8 for why this is still the right thing to watch
in October.

### 4. Stakes are a real, missing feature — and the postseason is entirely that slice

Pitcher K plays by team situation (`team_status` is populated for pitcher_k rows
only — see the coverage caveat):

| pitcher team | opponent | n | W-L | hit | units |
|---|---|---:|---:|---:|---:|
| **contending** | **contending** | **140** | 61–79 | **43.6%** | **−28.79** |
| clinched_division | contending | 17 | 8–9 | 47.1% | −2.84 |
| clinched_playoff | contending | 14 | 9–5 | 64.3% | **+2.89** |
| contending | clinched_division | 14 | 5–9 | 35.7% | −4.10 |
| contending | clinched_playoff | 12 | 5–7 | 41.7% | −3.70 |
| clinched_playoff | clinched_playoff | 2 | 0–2 | 0.0% | −2.00 |

The one-dimensional margins agree and are larger in denominator:

- pitchers **on** a contending team: n=166, 42.8%, **−36.60u**
- pitchers **facing** a contending team: n=171, 45.6%, **−28.74u**
- home team contending: n=151, 49.7%, −17.39u
- away team contending: n=129, 54.3%, −5.69u

Contending-vs-contending is 70% of all pitcher K plays (140 of 199) and **75%
of the family's loss (−28.79u of −38.55u)**, at −20.6% ROI. The model did well
on pitchers whose team had already clinched.

**Coverage caveat, stated plainly:** `team_status` / `opp_status` are only
recorded on pitcher_k rows in this sample. The 513 non-pitcher rows carry no
status, so the 42.8%/43.6% figures are **pitcher-only facts**. It is *not*
evidence that game markets are unaffected by stakes — that question is simply
unmeasured here. Do not quote these rates as all-family rates.

**This is the single most important carry-forward finding.** Every postseason
game is a contending team facing a contending team, with a short leash and a
bullpen optimized for leverage. The regular-season slice our model handled
*worst* is the entire postseason. Expect the model's seasonal −12.6% ROI to be a
**ceiling**, not a baseline, once the playoffs start.

### 5. Sides and prices

- Pitcher **OVER** 11–27: **28.9%**, −17.21u on 38 plays.
- Pitcher **UNDER** 77–84: 47.8%, −21.34u on 161 plays.
  The K over is the most toxic single play type in the sample. The model takes
  81% unders, and the unders are merely bad rather than catastrophic.
- Price bands: +100..+150 → 37.6%, −27.95u (n=133). −110..+100 → 42.1%, −17.28u
  (n=95). −150..−110 → 53.0%, −13.99u (n=268). Below −150 → 59.3%, −15.59u
  (n=189). Above +150 → 11.1%, −6.45u (n=9).
- Reads as: the model picks many underdogs/pickems that lose, and many heavy
  favorites that do not pay enough when they win. **Every price band is
  negative** — there is no price band in which the board made money.
- Model-vs-market **agreement** is the cleanest split in the sample:
  - `with_market`: n=512, 47.9%, **−66.16u**
  - `against_market`: n=182, 54.9%, **−15.11u**
  - by family, the "against market" slice is near-breakeven everywhere
    (game_ml 60% −1.5u; game_total 57% +1.0u; game_rl 59% −1.5u) while
    "with market" is negative everywhere.
  Our disagreements were the better plays; our agreements were the inflated ones.

### 6. The best "features" the screen can find are just the price

Full-window FDR screen, top numerics across all families:

| feature | n | hi hit | lo hit | p | q |
|---|---:|---:|---:|---:|---:|
| moneyline.price_a | 495 | 0.478 | 0.561 | 0.0038 | 0.2261 |
| price_shadow.over_implied_probability | 199 | 0.362 | 0.532 | 0.0058 | 0.2261 |
| price_shadow.under_no_vig_probability | 199 | 0.550 | 0.333 | 0.0059 | 0.2261 |
| price_shadow.over_no_vig_probability | 199 | 0.362 | 0.532 | 0.0059 | 0.2261 |
| price_shadow.under_implied_probability | 199 | 0.550 | 0.333 | 0.0059 | 0.2261 |
| market_baseline.away_win_no_vig | 495 | 0.480 | 0.559 | 0.0094 | 0.2578 |
| market_baseline.home_win_no_vig | 495 | 0.564 | 0.473 | 0.0094 | 0.2578 |
| line (pitcher_k strikeout line) | 199 | 0.500 | 0.298 | 0.0167 | 0.3184 |

**The screen got much stronger with the longer window, and what it found is the
market price.** Minimum q fell from **0.978** (13-day window) to **0.219**
(full window). That is a real change in informativeness — the earlier run was
genuinely underpowered.

But read what topped it. The highest-ranked signals are the moneyline price and
the no-vig probabilities — i.e. *the market's own opinion*, which we already
have and which we already know beats our model (Finding: Brier 0.2428 vs
0.2612). The screen is telling us that **the most informative available variable
is the price we are being offered**, and that our own projection layer does not
add on top of it in-sample.

Flag-level separations (pitcher_k, full window) are weak and none approach
significance: EDGE_EXTREME 75.0% present (n=8) vs 42.9% absent, p=0.074;
next-best are PARK_PITCHER p=0.172, TOUGH_MATCHUP p=0.190, MATCHUP_K_PLUS
p=0.191, DEPTH_PLUS p=0.238, FREE_SWING_OPP p=0.288. Every flag's q ≥ 0.68.

**Caveat: no feature or flag survives Benjamini–Hochberg at q<0.05.** The
top-ranked items sit at q≈0.22–0.26 — meaningfully better than the 13-day
screen, still not confirmatory. They are candidates for a pre-registered test,
not rules. Note also that the strongest signals the earlier recap reported were
**matchup-shaped** (PARK_PITCHER, FREE_SWING_OPP, MATCHUP_K_PLUS); that *shape*
is what Finding 8 below partly rescues, using direct matchup variables rather
than flag booleans.

### 7. Statistical honesty

- Every slice in this document was chosen after looking at the data.
- No slice survives multiple-comparison correction at q<0.05.
- The window is **18 graded days and 712 plays** — better than the 13-day
  baseline, still not powered to confirm a 3–5 point effect on a sub-slice.
  Sub-slices below n≈50 (several are) are descriptive only.
- **No families or plays were cut** as a result of this study, and no
  probability was recalibrated. That was deliberate.
- The provisional headline being *worse* once completed is itself the point:
  partial-window samples of this size move a lot, which is why the postseason
  promotion triggers in the playbook require n≥100 and sign agreement rather
  than a threshold on any single number.

### 8. Pitcher K sub-cuts — the user's postseason hypotheses, tested

These four cuts exist because the postseason read (see the playbook) named
specific pitcher-K cautions. They were computed on the full window and are
recorded here so the postseason can be compared against them directly.

**Non-strikeout-heavy pitchers are the whole problem.** Projected K rate and the
market strikeout line agree, monotonically:

| projected K rate | n | W-L | hit | units |
|---|---:|---:|---:|---:|
| < .180 (non-K-heavy) | 56 | 19–37 | **33.9%** | **−21.79** |
| .180 – .220 | 62 | 29–33 | 46.8% | −9.30 |
| .220 – .260 | 39 | 17–22 | 43.6% | −8.25 |
| ≥ .260 (K-heavy) | 42 | 23–19 | **54.8%** | **+0.79** |

| market K line | n | W-L | hit | units |
|---|---:|---:|---:|---:|
| ≤ 3.5 | 57 | 17–40 | **29.8%** | **−25.56** |
| 4.0 – 4.5 | 65 | 31–34 | 47.7% | −9.95 |
| 5.0 – 5.5 | 47 | 24–23 | 51.1% | −1.39 |
| ≥ 6.0 | 30 | 16–14 | 53.3% | −1.64 |

**Lines at 3.5 or below are 29% of pitcher_k volume and 66% of its loss.** This
is the user's hypothesis, confirmed on the full window, and it is expressible in
a variable the market publishes — no new model needed.

**Pitchers who did not go deep:** projected outs and the season depth habit:

| projected outs | n | W-L | hit | units |
|---|---:|---:|---:|---:|
| < 15.5 | 100 | 43–57 | 43.0% | **−23.66** |
| 15.5 – 17.5 | 46 | 22–24 | 47.8% | −4.98 |
| 17.5 – 18.5 | 17 | 5–12 | 29.4% | −7.64 |
| ≥ 18.5 | 36 | 18–18 | 50.0% | −2.27 |

| season avg outs | n | W-L | hit | units |
|---|---:|---:|---:|---:|
| < 16.0 | 92 | 42–50 | 45.7% | −16.03 |
| 16.0 – 17.5 | 74 | 29–45 | **39.2%** | **−21.10** |
| 17.5 – 19.0 | 29 | 14–15 | 48.3% | −3.12 |
| ≥ 19.0 | 4 | 3–1 | 75.0% | +1.71 |

Shallow-projection starts (under 15.5 outs) are 43.0% and −23.66u on 100 plays.
The season depth-habit cut is noisy below 17.5 outs but the deep-starter bucket
is the only positive one — at n=4, which is far too small to lean on. The honest
read: **depth does not separate well in-season (Finding 3), and the shallow
buckets are still where the money goes.** Treat depth as a risk filter, not a
signal.

**Matchups separate better than anything else in the pitcher family:**

| matchup_rating | n | W-L | hit | units |
|---|---:|---:|---:|---:|
| `< −0.15` | 86 | 34–52 | **39.5%** | **−23.68** |
| `−0.15 … +0.15` (flat) | 79 | 35–44 | 44.3% | −15.65 |
| `≥ +0.15` | 34 | 19–15 | **55.9%** | **+0.78** |

`matchup_rating` is the screener's K-vs-hand rating (`mlb_props/screener.py`,
`_matchup_rating`: centred on a .22 opposing K rate against the pitcher's hand,
rounded to 2 dp). The bucket boundaries are stated with the table because the
cut lives or dies on them, and they are **±0.15** — the `GOOD_MATCHUP` /
`TOUGH_MATCHUP` flag badges are set at **±0.2** and select a different, smaller
set (28 and 71 rows), which is why the flags do not reproduce this table and why
they are not the trigger in the postseason rule. Buckets were derived from the
frozen research table (`evidence/research/research_table.json`); the counts here
reproduce exactly (`< −0.15` and `≥ +0.15` inclusive/exclusive as written).

| opponent K rate vs hand | n | W-L | hit | units |
|---|---:|---:|---:|---:|
| < .190 | 30 | 10–20 | **33.3%** | −12.18 |
| .190 – .230 | 114 | 49–65 | 43.0% | −23.75 |
| ≥ .230 | 55 | 29–26 | **52.7%** | −2.62 |

Both matchup cuts are monotone and both put the only positive (or
near-breakeven) bucket on the favorable side. **This is the single best
supported signal class in the study**, and it is the evidence class the user
named first. Caveat: 34 and 30 plays in the favorable buckets — directional, not
confirmed.

**Recent short starts — the leash prior:**

| short starts in last 10 | n | W-L | hit | units |
|---|---:|---:|---:|---:|
| 0 | 30 | 17–13 | **56.7%** | **+1.47** |
| 1 | 35 | 12–23 | 34.3% | −12.29 |
| 2 | 41 | 18–23 | 43.9% | −7.51 |
| ≥ 3 | 93 | 41–52 | 44.1% | −20.22 |

Pitchers with a clean recent record of going deep are the only positive bucket.
Once a pitcher has been pulled early even once in his last ten, the K play goes
negative — which is the closest in-season analogue to the postseason leash
question.

**And the population the model actually plays:** `workload_stability < 0.60`
covers **189 of 199** pitcher K plays (−37.75u); only 10 plays were on pitchers
with stable workloads. The board is not selecting stable starters and then
losing on them — it is mostly not selecting stable starters at all.

### 9. The Daily Unders Card — the pre-registered gate, resolved

The Daily Card (`daily-unders-card-v1`, pre-registered 2026-08-31, before any
September data existed) is the one policy in this project whose success rule was
written down *before* it ran. That rule:

- `>= 55%` at `n >= 100` graded plays: the card is trusted for real staking in 2027
- `52.4-55%`: marginal; requires a price-based EV check before any trust
- `< 52.4%`: the policy is killed honestly

The derivation sample claimed `61.9%` on `n=97` (combined Aug 5-29), set against
a `52.4%` breakeven at -110 and a `55.5%` always-under baseline.

**The September window did not reproduce it.**

| | n | W-L | hit | units |
|---|---:|---:|---:|---:|
| Daily Card, 2026-09-01 -> 09-27 | 78 | 38-40 | **48.7%** | -5.45 at -110 |
| Always-under, same 25 snapshots | 180 | 95-85 | **52.8%** | — |

- 25 card-bearing snapshots, 80 card rows, 78 resolved, 2 no-start voids.
- `units_at_minus_110` = **-5.45**. Graded at the *collected* under prices the
  card is **-9.15u** against an average breakeven of **0.5560** — the card was
  also priced too short to survive even its own hit rate.
- **Verdict:** the point estimate `48.7%` falls in the `< 52.4%` band, so the
  pre-registered rule kills the policy. Separately, `n=78` never reached the
  `n >= 100` confirmatory denominator, so the *trust* branch was never reachable
  either — the kill rests on the point estimate, not on a powered test
  (95% CI on 38/78 is roughly 37.9-59.7%). Both facts are stated plainly: the
  kill is not softened, and the short denominator is not used as a rescue.

**Why it failed — and it is the same mechanism as Finding 8.** The frozen gates
(`line <= 5.5`, `|edge| <= 1.0`) concentrated card volume in exactly the two
regions Finding 8 flags as worst:

| cut | card volume | card hit |
|---|---:|---:|
| `projected_k_rate < .180` | 37 of 78 (47%) | 43.2% |
| `line <= 3.5` | 17 of 78 (22%) | 41.2% |
| `line 5.0-5.5` | 21 of 78 | **57.1%** |

The symmetric `|edge| <= 1.0` gate is the proximate culprit. On a pitcher the
model projects below a `.180` strikeout rate, the projection sits near `3.5`, and
the only lines within one strikeout of it are `3.0-4.5` — the worst bucket in the
whole family. The gate guaranteed that roughly half of the card's volume came
from the profile the user flagged as postseason-shaky *before* September evidence
existed to prove it.

So the card's failure is not noise. It is Finding 8, reached through a policy
that was frozen before the evidence was — which makes this independent
corroboration of Finding 8 rather than a second look at the same data.

**What this does and does not do.**

- It **does not change** the card. Nothing here edits the policy; the policy ran
  to its pre-registered end and lost. `daily-unders-card-v1` must not be carried
  into October as a staking policy.
- It **does** strengthen the case for a successor gate: any v2 must exclude the
  sub-`.180` projected-K-rate / `<= 3.5`-line region that this table and
  Finding 8 independently identify, and must be priced better than a 0.5560
  breakeven.
- The always-under baseline above was first computed by hand, because
  `daily_card_summary` read `history.candidates` while `scripts/grade_daily_card_full_season.py`
  resolved only card rows — so `baseline_rows` came back `0`. That gap is now
  **repaired** (2026-09-28): `pitcher_grading.daily_card_baseline` owns the
  comparison set and the script resolves it alongside the card rows. Re-running
  the standing tool reproduces this row exactly (**180 rows, 95-85, 52.8%**,
  zero pending), so the figure above is now tool-produced. The card's own record
  and every result in this document are unchanged; only the provenance of the
  baseline row moved.

---

## Decisions taken and not taken

**Taken (frozen at 2026-09-28, figures completed the same day):**

- The regular season is closed as an evidence sample; its numbers are frozen here
  at the complete window (712 decided plays, −88.27u, −12.6% ROI).
- Dated reviews are era-stamped (`regular_season` / `postseason` / `between`) so
  the two samples can never be blended silently.
- The window parameterization landed in `scripts/analyze_sep_window.py`
  (`--start` / `--end`), so this window is one command to reproduce.
- The postseason opener was **verified** against the live schedule as
  2026-09-29 (four Wild Card series, `gameType=F`).
- The postseason runs on the full board, at full display confidence — see the
  playbook. No shadow/observation-only posture.
- The pre-registered Daily Unders Card gate was **resolved on its own terms**
  (Finding 9): `48.7%` at `n=78` sits in the pre-declared kill band and below a
  `52.8%` always-under baseline on the same snapshots. The policy is killed by
  its own rule and the record is kept exactly as written.

**Not taken (and why):**

- No change to selection, tiers, the Daily Card, or model probabilities. The
  slices are post hoc; the top-ranked screen items are the price itself. The
  Daily Card in particular is *not* edited — its pre-registered rule killed it
  (Finding 9). Re-scoping a dead policy to save it would destroy the only
  pre-commitment this project has kept.
- No new EV/edge gate. The EV flag is inverted in-sample; gating on it now would
  hard-code the bug.
- No retirement of any family. `game_total` is the worst-calibrated but only
  −4.7% ROI, and it is the one family that did *not* degrade in the final five
  days. That is a reason to keep measuring it, not to cut it.
- No blending of postseason results into any regular-season figure.
- **No pitcher-K ban.** Finding 8 makes a strong case for one, but it would be a
  rule built on post hoc cuts in the same session that computed them. It is
  written up as a pre-registered candidate instead (below).

## What would justify a production change

Pre-registered, in the order that makes sense:

1. **Recalibrate before gating.** Fit isotonic/Platt on out-of-sample data and
   shrink the probability layer; the market is calibrated and we are not, and the
   gap widens above 0.70. Re-test the EV flag *after* that, not before.
2. **Add a stakes/context feature.** Clinch and elimination status, games back,
   elimination number, games remaining, opponent stakes. Finding 4 justifies it;
   the postseason makes it urgent. Also fix the coverage hole: status is
   pitcher-only right now, so the game families have no stakes feature at all.
3. **Pre-register the pitcher-K line rule from Finding 8.** Candidate:
   *`line ≤ 3.5` pitcher K plays are excluded or require an explicit
   favorable-matchup override.* Declare it before the postseason, measure it
   era-stamped, and keep the regular-season -25.56u on 57 plays as the baseline
   it must beat. **Finding 9 is direct evidence for this rule and for a
   projected-K-rate floor:** the Daily Card failed at `48.7%` precisely because
   its `|edge| <= 1.0` gate routed half its volume into these two buckets.
4. **Improve the K-rate estimate** (opposing-lineup K%/whiff/contact, pitcher
   recent form). Finding 3 says workload modelling is secondary; Finding 8 says
   matchup variables are the strongest available signal.
5. **Keep the actual pitching lines in grading** (outs, BF, pitches, ER) — already
   landed — and start a CLV report off the noon→afternoon captures. Movement is
   not CLV.

Each of these needs an n≥100 rule and an in/out-of-sample split, declared before
it runs, so the next stretch of the season stays learnable rather than reactive.

---

## Source artifacts

- `evidence/research/FINDINGS.md` — the full narrative study
- `evidence/research/metrics.json` — machine-readable metrics
- `evidence/research/research_table.json` — per-play joined rows (729 rows)
- `evidence/research/feature_screen.json` — FDR feature screen
- `evidence/research/report.md` — generated report
- `evidence/grades/daily_card_summary.json` — the pre-registered Daily Card
  record (Finding 9): `48.7%` at `n=78`, `units_at_minus_110 -5.45`,
  `priced_units -9.1478` at `priced_avg_breakeven_rate 0.55596`

`evidence/` is gitignored: these are local artifacts, not durable handoff. This
document and the playbook are the durable record.
