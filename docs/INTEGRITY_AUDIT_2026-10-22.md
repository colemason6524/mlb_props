# MLB — ledger/report integrity audit (2026-10-22)

Scope: `~/mlb_props` on the Azure VM, branch `main`. Reference case: NHL
`docs/LEDGER_RECONCILIATION_2026-10-22.md` (three defect classes: headline pooling
non-recommended rows, orphan frozen writer/reader pair, multiple sources of truth).

**Verdict: NOT CLEAN.** Three of the three classes were tested; one of them (multiple
sources of truth) is confirmed, one variant of class (a) was found and fixed (unpriced
losses booking a phantom stake), and class (b) is present but orphaned (no live reader,
so nothing is currently printing a stale number). The frozen season recap reproduces to
the penny — under the buggy predicate, i.e. its headline is built on 7.00u of phantom
losses.

Everything below was recomputed from the live artifacts on 2026-10-23 03:0x UTC.
Nothing in this audit was inferred from older docs.

---

## 1. The record map

### Writers

| writer | artifact | cadence |
|---|---|---|
| `run_forecast_board.py` | `outputs/ledger/forecast_ledger.jsonl` (per-pick board rows), `outputs/ledger/picks_roi.jsonl` (stake ledger), board JSON / Discord post | 3× per date (noon 16:15Z, afternoon 20:45Z, evening 22:45Z), plus ad-hoc |
| `grade_forecast_board.py` | `outputs/grades/forecast_board_<date>.json` (`latest_summary`, `latest_roi`, `canonical`, `revisions`), `outputs/grades/learning_review_<date>.md`; settles/annotates both ledgers | `sports-mlb-grade-board.timer`, 10:00 UTC (06:00 ET) |

### Artifacts, and what each one actually is

- `outputs/ledger/picks_roi.jsonl` — **1,724 rows / 37 screen dates** (2026-09-10 →
  2026-10-19). Append-only stake ledger. Contains *every* revision of every pick, so it
  holds duplicates by design; `superseded` flags mark replaced rows.
- `outputs/grades/forecast_board_<date>.json` — **37 dates**. Three views inside one file:
  - `latest_summary` / `latest_roi`: the newest *revision* of that date only (later runs
    supersede earlier ones) — per-date, not a season record.
  - `canonical`: one row per `proposition_id` for that screen date — **the per-date record**.
  - `revisions`: every run of that date with its own summary.
- `docs/SEASON_2026_RECAP.md` — frozen prose recap for the regular-season window.

### Consumers

- **Dashboard** (`~/dashboard`, project `mlb_props`): reads
  `outputs/grades/forecast_board_*.json` → `latest_summary.totals` + `latest_roi`
  (glob-driven, newest artifacts first) and `outputs/grades/learning_review_*.md`;
  run health from `outputs/run_status.json`. It never reads the ledger or `canonical`.
  → **What the dashboard shows is a sum of per-date latest revisions, presented as the record.**
- **Discord board** (`render_board_text` in `run_forecast_board.py`): renders live board
  rows; lists at most 40 rows per family but prints the true `(len(rows))` in the section
  header, and the ledger records the full set. Display-only cap, never silent.
- **`scripts/analyze_sep_window.py`**: reads artifact `canonical` rows for ad-hoc window
  analysis (this is what produced the season recap).
- **`grade_forecast_board.py::build_learning_review`** (`_bucket_stats`): per-date
  W-L/units breakdown, written to `learning_review_<date>.md`.
- **`scripts/audit_season_record.py`** (added by this audit): read-only recompute of all
  five definitions below, plus denominator-integrity checks.

### The headline definition, stated precisely

There is no single official headline. The two defensible ones:

- **As-published canonical** — one row per `proposition_id` per screen date, taken from
  the artifact `canonical` block where it exists and rebuilt from the ledger revision
  selector where it does not (09-10..09-13). Grades at the recorded ledger price.
- **Bettable-only** — the same set restricted to `price is not None`. A row with no price
  has no stake and no P&L.

Rows excluded from both: pending/unresolved rows, superseded revisions, and (in the
bettable view) every unpriced row.

---

## 2. Both headline numbers (as reported vs bettable-only)

**Season window 09-14..09-27 (the recap window, the one that is published):**

- As reported / as published (`docs/SEASON_2026_RECAP.md`, frozen): 712 decided,
  **356-356**, 701 priced, **−88.27u**, **−12.6%** ROI.
- Bettable-only, price is the stake basis: 712 decided, **356-356**, **694** priced,
  **−81.266u**, **−11.71%** ROI.
- Delta: **7 priced rows / +7.00u**, all `game_ml` — exactly the unpriced-LOSS phantom
  stakes (§6).

**Full season (36 dates, 2026-09-10 → 2026-10-19):**

- As reported all-decided: **429-412-3** (as-published canonical) / **538-496-5**
  (full-season canonical), 22 unpriced rows in each.
- Bettable-only: **417-402-3**, 822 priced, **−79.545u**, **−9.68%** (as-published) /
  **526-486-5**, 1,017 priced, **−78.998u**, **−7.77%** (full-season).

The two differ only because four early dates (09-10..09-13) have no artifact `canonical`
block and are rebuilt from the ledger.

---

## 3. Five definitions, all live-recomputed

| definition | W-L-P | priced | units | ROI | dates |
|---|---|---:|---:|---:|---:|
| Raw ledger finals (duplicates included) | 569-518-6 | 1,093 | −76.565 | −7.01% | 36 |
| ↳ regular season | 497-465-6 | 968 | −78.287 | −8.09% | 18 |
| ↳ postseason | 72-53-0 | 125 | **+1.721** | +1.38% | 18 |
| As-published canonical (artifacts + 4 rebuilt dates) | 429-412-3 | 822 | −79.545 | −9.68% | 32 |
| ↳ regular season | 345-349-3 | 697 | −81.266 | −11.66% | 14 |
| ↳ postseason | 72-53-0 | 125 | +1.721 | +1.38% | 18 |
| Legacy-date canonical only (09-10..09-13) | 109-84-2 | 195 | **+0.546** | +0.28% | 4 |
| Full-season canonical (32 + 4 rebuilt) | 538-496-5 | 1,017 | −78.998 | −7.77% | 36 |
| ↳ regular season | 454-433-5 | 892 | −80.720 | −9.05% | 18 |
| Latest-revision-only (what the dashboard sums) | 383-359-4 | 724 | −59.549 | −8.23% | 37 |

One season, five numbers between **−59.5u and −79.5u**, and three different win-loss
records. All five are arithmetically correct for what they read. That is the defect.

---

## 4. Class (a) — headline pooling non-recommended rows: **NOT FOUND as described**

There is no watch / NO_PLAY / pass / lean-not-staked concept on this board. Every row
that reaches the ledger is a board pick with a side and a probability; there is no
explicit not-recommended flag to pool, so the NHL defect shape does not exist here.

What *was* found in its place, in the same family of mistake (a row inside the headline
that should not carry a stake):

### The unpriced-LOSS phantom stake (FIXED)

`grade_forecast_board.py::settle_units()` returned `-1.0` for any `LOSS` regardless of
price, while a `WIN` with no price correctly returned `None`. Ten `game_ml` losses with
no recorded price therefore booked a phantom −1.00u each: **−10.000u across the season**,
inside every consumer that summed `units` where `units is not None`.

Denominator integrity, live:

- 10 unpriced LOSS rows carrying `units=-1.0` → **−10.000u phantom** (all `game_ml`;
  dates 09-18, 09-22, 09-24, 09-25×2, 09-26, 09-27, 10-04, 10-07, 10-15).
- 12 unpriced WINs at zero units (correct).
- 0 priced rows missing units.
- Stake-basis diagnostic: season canonical `units is not None` → 1,027 rows / **−88.998u**
  vs `price is not None` → 1,017 rows / **−78.998u**; as-published canonical 832 rows /
  −89.545u vs 822 / −79.545u. Delta **+10.000u** in both.

Fix (commit `0c62483`): `units` is defined only when `price is not None`; `price` (not
`units`) is the stake predicate in `_bucket_stats` and in `scripts/analyze_sep_window.py`
(4 sites), so historical artifacts that still carry `units=-1.0, price=None` are read
correctly. The grade artifact now carries a `view_notes` block labelling
`latest_summary` vs `canonical` vs units semantics.

The ledger and the historical artifacts are **not** retro-edited: the 10 rows still store
`units=-1.0`. The fix is forward-looking on the write side and corrective on the read
side, which is the conservative option.

---

## 5. Class (b) — orphan / frozen writer-reader pair: **present but orphaned, nothing live reads it**

- `outputs/history/hot_hits_*.json` — 3 files, newest `2026-09-10T152041Z.json`: frozen
  for six weeks. **No reader in the repo, no timer writes or reads it.** Compare NHL:
  there a consumer was still printing from the frozen source. Here there is no consumer,
  so no stale number is on any surface. Verdict: dead artifact, safe to retire whenever
  someone wants the disk back (documented, not deleted — removing it is a scope decision).
- `outputs/backtests/` — 0 files, no reader.
- No timer reads a stale source. Live timers verified 2026-10-23 03:09 UTC:
  `sports-mlb-grade-board` next 10:00 UTC (last 17h ago); `sports-mlb-statcast` next
  12:00 UTC; `pipeline-noon` 16:15; `pipeline-afternoon` 20:45; `pipeline-evening` 22:45 —
  all fired on 10-22. `sports-mlb-statcast` is the one timer whose `OnCalendar` is UTC and
  the one with `Persistent=true`, i.e. it breaks both stated conventions; recorded in
  `docs/AZURE_VM_OPERATIONS.md` (commit `58b674b`).

---

## 6. Class (c) — multiple sources of truth: **CONFIRMED**

This is the live defect. §3 lists five self-consistent definitions of one season. The
practical consequences:

1. **The dashboard is the worst of them.** It globs `forecast_board_*.json` and sums
   `latest_roi` — the newest revision of each date. Later runs supersede earlier ones, so
   the dashboard figure (724 priced, −59.549u) is **~293 priced rows short of the record**
   and answers a different question ("what did the last run of each day say") than
   "how did the season go". It is not labelled as a per-date view anywhere on the
   dashboard.
2. **The raw ledger over-counts.** 74 duplicate finals across 09-11 (×53), 09-12 (×17),
   09-13 (×4) — 74 extra rows, +0.908u — because the pre-artifact dates were never
   de-duplicated in the ledger.
3. **The early window is a different story.** Legacy-date canonical (09-10..09-13) is
   **+0.546u** on 195 priced rows, while the season is −79.5u. Anyone quoting the first
   four dates gets a positive record.

Dashboard's `latest_roi` is at least clean of the phantom stakes: it was already
price-predicated at write time, so its 724 priced / −59.549u contains none of the ten
phantom rows (verified per date: `plays == decided − price-null` in every artifact).

---

## 7. Erratum — `docs/SEASON_2026_RECAP.md` reproduces only under the bug

The frozen recap states 712 decided, 356-356, **−88.27u**, **−12.6%**, 701 priced, with
family rows `pitcher_k −38.547`, `game_ml −24.621`, `game_total −7.619`,
`game_rl −17.479`, and split windows 09-14..09-22 (**−43.86u**, 442 priced) and
09-23..09-27 (**−44.41u**, 259 priced).

Re-run of `scripts/analyze_sep_window.py --start 2026-09-10 --end 2026-09-27 --root ~/mlb_props`
over 729 artifact-canonical graded rows (14 dates, 09-14..09-27):

| | decided | W-L | priced | units | ROI | pitcher_k | game_ml | game_total | game_rl |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| recap (frozen) | 712 | 356-356 | 701 | −88.27 | −12.6% | −38.547 | −24.621 | −7.619 | −17.479 |
| pre-fix code (`0c62483^`) | 712 | 356-356 | 701 | −88.266 | −12.59% | −38.547 | −24.621 | −7.619 | −17.479 |
| post-fix code | 712 | 356-356 | 694 | −81.266 | −11.71% | −38.547 | **−17.62** | −7.619 | −17.479 |

The recap is **faithful to what the code computed** — it reconciles to the penny, family
row by family row. It is reproducing 7 phantom `game_ml` losses.

Split-window reconciliation, post-fix:

- 09-14..09-22: 448 decided (230-218), **440** priced, **−41.86u**, −9.51%
  (recap 442 / −43.86u → 2 phantom rows, +2.00u).
- 09-23..09-27: 264 decided (126-138), **254** priced, **−39.407u**, −15.51%
  (recap 259 / −44.41u → 5 phantom rows, +5.00u).
- 2 + 5 = 7 phantom rows = **+7.00u** = the exact `game_ml` delta. The reconciliation
  closes.

Minor doc nit: the recap says "18 graded dates, 729 canonical rows". The 729 rows come
from **14** artifact dates (09-14..09-27); 18 is the calendar span 09-10..09-27.

**The erratum text is not applied to the frozen recap.** Overstated figures remain
published: 7.00u over the recap window, 10.00u over the season. Whether to retro-annotate
a frozen document is a call for Cole, not for this audit.

---

## 8. Documentation and legacy drift

Full inventory: `docs/LEGACY_INVENTORY_2026-10-22.md` (commit `5a2be4e`). Highlights:

- `docs/AZURE_VM_OPERATIONS.md` and `docs/NEXT_CHECKIN.md` had wrong live timer tables
  (evening board time, statcast TZ/`Persistent`) — corrected in commit `58b674b`.
- `docs/SEASON_2026_RECAP.md` — see §7.
- `outputs/history/hot_hits_*` and `outputs/backtests/` — dead artifacts (§5).
- Four pre-existing untracked files in `scripts/` (`migrate_all_sports_to_systemd.sh`,
  `mlb-props-tmux.service`, `run_daily_tmux_task.sh`, `start_tmux_scheduler.sh`) are
  pre-systemd leftovers; left untracked and untouched by this audit. They are also the
  source of the `ls scripts` noise that makes the live `install_all_sports_systemd.py`
  path look ambiguous.

Display vs record: clean. Canonical rows per family across the season are `pitcher_k`
237, `game_ml` 222, `game_total` 201, `game_rl` 201 (861 rows, 844 decided). No
date × family group reaches the 40-row display cap, so the Discord rendering never
truncated a family; and the cap is announced in the header when it does bite.

---

## 9. Fixes applied in this pass

| commit | what |
|---|---|
| `28500bc` | `scripts/audit_season_record.py` — read-only recompute of all five definitions + denominator integrity (the evidence base for this doc) |
| `5a2be4e` | `docs/LEGACY_INVENTORY_2026-10-22.md` |
| `0c62483` | `settle_units()` books a LOSS only when priced; `price is not None` is the stake predicate in `_bucket_stats` and `scripts/analyze_sep_window.py`; `view_notes` on the grade artifact; regression tests |
| `58b674b` | corrected live timer tables in `docs/AZURE_VM_OPERATIONS.md`, `docs/NEXT_CHECKIN.md` |

Test suite: `.venv/bin/python -m pytest -q` → **396 passed, 12 subtests passed**.
(System `python3 -m pytest` has no pytest module. The VM runs UTC; a pre-existing
date-roll failure in `tests/test_hot_hits_price_shadow.py` reproduced on a clean `HEAD`
worktree and was repaired by deriving the slate date from `America/New_York` instead of
`date.today()`.)

Leftover stake-predicate sweep (step 1 of the plan, completed after the fix):
`grep -rn "units is not None" --include=*.py` finds only
`mlb_props/daily_card.py:49` (a formatting branch deciding whether to print a
hand-maintained snapshot's units in a banner — not a stake predicate), a docstring, and a
diagnostic label in the audit script. Every other `units` summation in the repo iterates a
list already filtered on `price is not None` (`roi_summary`, `_bucket_stats`, `tally`,
`summarize`). No further change was warranted.

---

## 10. Verdict

**NOT CLEAN.**

- The season headline depends on which artifact you read: −59.549 / −76.565 / −78.998 /
  −79.545, with W-L records from 383-359-4 to 569-518-6.
- The dashboard shows a per-date latest revision as if it were the season record, without
  labelling.
- The unpriced-LOSS units bug inflated losses in every `units is not None` consumer by
  **10.000u** over the season (7.00u inside the published recap window), and it is fixed
  forward + on the read side; historical rows still carry the phantom units.
- The frozen recap reproduces to the penny **because** it was computed under that bug.

What is healthy, and worth stating: the recap's arithmetic is trustworthy — every figure
in it reconciles to the recomputed canonical set, family by family and window by window.
No non-recommended rows are pooled into any headline. No live consumer reads a frozen
source. The record itself (canonical, per date) is intact and auditable; the problem is
entirely in which view gets quoted as "the record".

Open decisions for Cole:

1. Retro-annotate `docs/SEASON_2026_RECAP.md` with the 7.00u erratum, or leave the frozen
   document as published?
2. Give the dashboard a labelled season-record field (canonical, price-predicated) instead
   of the `latest_roi` glob?
3. Retire `outputs/history/hot_hits_*` / `outputs/backtests/`, and delete or commit the
   four pre-systemd `scripts/` leftovers?

---

## Reproduction

```sh
cd ~/mlb_props
.venv/bin/python scripts/audit_season_record.py            # five definitions + denominators
.venv/bin/python scripts/analyze_sep_window.py \
    --start 2026-09-10 --end 2026-09-27 --root ~/mlb_props --out /tmp/sep_window
git show 0c62483^:scripts/analyze_sep_window.py           # pre-fix behaviour, reproduces the recap
.venv/bin/python -m pytest -q
```
