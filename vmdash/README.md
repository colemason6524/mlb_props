# Staged dashboard changes (relay payload)

Cole's rule: dashboard code is edited **here**, in the versioned `mlb_props`
repo, and relayed to `~/dashboard` by the relay step. `~/dashboard` is not a git
checkout, so these files are the record.

## Files

| staged path | live target | why |
|---|---|---|
| `dashboard/performance.py` | `~/dashboard/dashboard/performance.py` | adds `parse_mlb_season()` / `mlb_season_job()`: season = sum of each screen date's `canonical` block |
| `dashboard/files.py` | `~/dashboard/dashboard/files.py` | `REMOTE_SCRIPT` dispatches the new `mlb_season` job kind |
| `dashboard/collect.py` | `~/dashboard/dashboard/collect.py` | builds the `mlb_season` job for the `season_record` report and exposes `fields.season` |
| `dashboard/summaries.py` | `~/dashboard/dashboard/summaries.py` | per-date summary reads `canonical.*`; new `_mlb_season` line; `latest_roi` no longer read |
| `config/projects.json` | `~/dashboard/config/projects.json` | mlb_props: `latest_roi` removed from the grade report, `season_record` report added |
| `tests/test_mlb_season.py` | `~/dashboard/tests/test_mlb_season.py` | proves canonical resolution, latest_roi exclusion, REMOTE_SCRIPT dispatch, config wiring |
| `tests/test_core.py` | `~/dashboard/tests/test_core.py` | existing summary test updated to the canonical fields |

## Why

`latest_roi` is the newest *revision* of one screen date (later runs supersede
earlier ones). Summed over the glob it answers "what did the last run of each day
say": 383-359-4, 724 priced, **-59.549u**, ~293 priced rows short of the record
(`docs/INTEGRITY_AUDIT_2026-10-22.md` section 6).

The live record is the `canonical` block: one row per `proposition_id` per screen
date, where `price is not None` is the stake predicate (mlb_props commit
`0c62483`). Summed across the 33 graded screen dates on the VM: **429-412-3, 822
priced, -79.542u, -9.68%** — matching the audit's as-published canonical
(429-412-3 / 822 / -79.545 / -9.68%) to rounding. Dates with no `canonical` block
(09-10..09-13, which predate it, and empty boards) are listed in
`missing_canonical_dates` and shown as "dates N/M canonical" rather than being
silently dropped.

`latest_roi` stays readable for a single date, but the season line never reads
it, and it is marked LEGACY in `performance.py`.

## Apply

```sh
cp vmdash/dashboard/*.py   ~/dashboard/dashboard/
cp vmdash/config/projects.json ~/dashboard/config/projects.json
cp vmdash/tests/*.py       ~/dashboard/tests/
cd ~/dashboard
python3 -m pytest -q tests/test_mlb_season.py     # 9 passed
python3 -m dashboard --env vm checkpoint-update --summary "mlb season figure now canonical"
```
