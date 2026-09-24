from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import grade_forecast_board as grade


class PureGraderTests(unittest.TestCase):
    def test_pitcher_k_over_under_and_push(self) -> None:
        self.assertEqual(grade.grade_pitcher_k("over", 5.5, 6), grade.WIN)
        self.assertEqual(grade.grade_pitcher_k("over", 5.5, 5), grade.LOSS)
        self.assertEqual(grade.grade_pitcher_k("under", 5.5, 5), grade.WIN)
        self.assertEqual(grade.grade_pitcher_k("under", 5.5, 6), grade.LOSS)
        self.assertEqual(grade.grade_pitcher_k("over", 6.0, 6), grade.PUSH)
        self.assertEqual(grade.grade_pitcher_k("under", 6.0, 6), grade.PUSH)

    def test_moneyline(self) -> None:
        self.assertEqual(grade.grade_moneyline("home", 4, 2), grade.WIN)
        self.assertEqual(grade.grade_moneyline("away", 4, 2), grade.LOSS)
        self.assertEqual(grade.grade_moneyline("away", 2, 4), grade.WIN)
        self.assertEqual(grade.grade_moneyline("home", 3, 3), grade.PUSH)

    def test_total(self) -> None:
        self.assertEqual(grade.grade_total("over", 8.5, 9), grade.WIN)
        self.assertEqual(grade.grade_total("over", 8.5, 8), grade.LOSS)
        self.assertEqual(grade.grade_total("under", 8.5, 8), grade.WIN)
        self.assertEqual(grade.grade_total("under", 8.0, 8), grade.PUSH)

    def test_run_line(self) -> None:
        # home -1.5 wins by 2
        self.assertEqual(grade.grade_run_line("home_covers", -1.5, 5, 3), grade.WIN)
        # home -1.5 wins by 1 -> away covers
        self.assertEqual(grade.grade_run_line("home_covers", -1.5, 4, 3), grade.LOSS)
        self.assertEqual(grade.grade_run_line("away_covers", -1.5, 4, 3), grade.WIN)
        # integer line exactly lands on the number -> push
        self.assertEqual(grade.grade_run_line("home_covers", -1.0, 4, 3), grade.PUSH)

    def test_settle_units(self) -> None:
        self.assertAlmostEqual(grade.settle_units(grade.WIN, 150), 1.5)
        self.assertAlmostEqual(grade.settle_units(grade.WIN, -200), 0.5)
        self.assertEqual(grade.settle_units(grade.LOSS, -110), -1.0)
        self.assertEqual(grade.settle_units(grade.PUSH, -110), 0.0)
        self.assertEqual(grade.settle_units(grade.VOID, -110), 0.0)
        self.assertIsNone(grade.settle_units(grade.PENDING, -110))
        self.assertIsNone(grade.settle_units(grade.WIN, None))

    def test_match_key_strips_accents_and_separators(self) -> None:
        self.assertEqual(grade.match_key("José Soriano"), "josesoriano")
        self.assertEqual(grade.match_key("Jose Soriano"), "josesoriano")
        self.assertEqual(grade.match_key("josesoriano"), "josesoriano")

    def test_pitcher_lookup_matches_accented_boxscore_name(self) -> None:
        box = {
            "teams": {
                "away": {
                    "players": {
                        "ID667755": {
                            "person": {"id": 667755, "fullName": "José Soriano"},
                            "stats": {"pitching": {"strikeOuts": 3}},
                        }
                    }
                }
            }
        }
        client = grade.MlbClient(fetch=lambda url: box)
        lookup = client.pitcher_lookup("1", None, "josesoriano")
        self.assertTrue(lookup["found"])
        self.assertEqual(lookup["strikeouts"], 3)

    def test_parse_proposition_id(self) -> None:
        self.assertEqual(
            grade.parse_proposition_id("p:824714:reiddetmers:5.5"),
            {"family": "pitcher_k", "game_pk": "824714", "name_key": "reiddetmers", "line": 5.5},
        )
        self.assertEqual(
            grade.parse_proposition_id("g:1:ml"),
            {"family": "game_ml", "game_pk": "1", "line": None},
        )
        self.assertEqual(
            grade.parse_proposition_id("g:1:total:8.5"),
            {"family": "game_total", "game_pk": "1", "line": 8.5},
        )
        self.assertEqual(
            grade.parse_proposition_id("g:1:spread:-1.5"),
            {"family": "game_rl", "game_pk": "1", "line": -1.5},
        )
        self.assertIsNone(grade.parse_proposition_id("nonsense"))


FINALS = {
    "1": {"final": True, "state": "Final", "home_score": 5, "away_score": 3},
    "2": {"final": False, "state": "In Progress", "home_score": 1, "away_score": 1},
    "3": {"final": False, "state": "Postponed", "home_score": None, "away_score": None},
}


class FakeClient:
    def __init__(self, pitcher=None):
        self._pitcher = pitcher or {}

    def pitcher_lookup(self, game_pk, subject_id, name_key):
        return self._pitcher.get(str(game_pk), {"found": False, "appeared": False, "strikeouts": None})


class GradeRowTests(unittest.TestCase):
    def _row(self, **overrides):
        base = {
            "run_id": "board-2026-09-10-noon",
            "family": "game_ml",
            "proposition_id": "g:1:ml",
            "pick": "home",
            "line": None,
            "price": -110,
        }
        base.update(overrides)
        return base

    def test_final_win_priced(self) -> None:
        graded = grade.grade_row(self._row(), FINALS, FakeClient())
        self.assertEqual(graded["result"], grade.WIN)
        self.assertAlmostEqual(graded["units"], 100 / 110)

    def test_pending_game(self) -> None:
        graded = grade.grade_row(self._row(proposition_id="g:2:ml"), FINALS, FakeClient())
        self.assertEqual(graded["result"], grade.PENDING)
        self.assertIsNone(graded["units"])

    def test_postponed_is_void(self) -> None:
        graded = grade.grade_row(self._row(proposition_id="g:3:ml"), FINALS, FakeClient())
        self.assertEqual(graded["result"], grade.VOID)
        self.assertEqual(graded["units"], 0.0)

    def test_unpriced_win_has_no_units(self) -> None:
        graded = grade.grade_row(self._row(price=None), FINALS, FakeClient())
        self.assertEqual(graded["result"], grade.WIN)
        self.assertIsNone(graded["units"])

    def test_pitcher_k_uses_boxscore(self) -> None:
        client = FakeClient(pitcher={"1": {"found": True, "appeared": True, "strikeouts": 7}})
        row = self._row(
            family="pitcher_k",
            proposition_id="p:1:testpitcher:5.5",
            pick="over",
            line=5.5,
            subject_id=None,
        )
        graded = grade.grade_row(row, FINALS, client)
        self.assertEqual(graded["result"], grade.WIN)

    def test_pitcher_did_not_appear_void(self) -> None:
        client = FakeClient(pitcher={"1": {"found": True, "appeared": False, "strikeouts": None}})
        row = self._row(family="pitcher_k", proposition_id="p:1:testpitcher:5.5", pick="over", line=5.5)
        graded = grade.grade_row(row, FINALS, client)
        self.assertEqual(graded["result"], grade.VOID)

    def test_pitcher_records_full_line_and_team_status(self) -> None:
        client = FakeClient(pitcher={"1": {
            "found": True, "appeared": True, "strikeouts": 7, "outs": 18,
            "batters_faced": 24, "pitches": 95, "hits": 5, "walks": 2,
            "earned_runs": 2, "side": "home",
        }})
        finals = {"1": {
            "final": True, "state": "Final", "home_score": 5, "away_score": 3,
            "home_team": {"id": "1", "name": "Home Team", "abbr": "HOM"},
            "away_team": {"id": "2", "name": "Away Team", "abbr": "AWY"},
        }}
        standings = {"by_id": {"1": {"status": "contending"}}, "by_abbr": {"AWY": {"status": "clinched_playoff"}}}
        row = self._row(
            family="pitcher_k", proposition_id="p:1:testpitcher:5.5", pick="over", line=5.5,
            projected_strikeouts=6.5, projected_batters_faced=24.0, projected_k_rate=0.27,
        )
        graded = grade.grade_row(row, finals, client, standings)
        self.assertEqual(graded["result"], grade.WIN)
        self.assertEqual(graded["actual"]["batters_faced"], 24)
        self.assertEqual(graded["team_status"]["status"], "contending")
        self.assertEqual(graded["opp_status"]["status"], "clinched_playoff")
        self.assertAlmostEqual(graded["result_margin"], 1.5)

    def test_game_row_records_team_status_and_margin(self) -> None:
        finals = {"1": {
            "final": True, "state": "Final", "home_score": 5, "away_score": 3,
            "home_team": {"id": "1", "name": "Home Team", "abbr": "HOM"},
            "away_team": {"id": "2", "name": "Away Team", "abbr": "AWY"},
        }}
        standings = {"by_id": {"1": {"status": "contending"}, "2": {"status": "eliminated"}}, "by_abbr": {}}
        row = self._row(proposition_id="g:1:ml", pick="home")
        graded = grade.grade_row(row, finals, FakeClient(), standings)
        self.assertEqual(graded["result"], grade.WIN)
        self.assertEqual(graded["home_status"]["status"], "contending")
        self.assertEqual(graded["away_status"]["status"], "eliminated")
        self.assertAlmostEqual(graded["result_margin"], 2.0)


class ParseStandingsTests(unittest.TestCase):
    def test_parse_and_lookup(self) -> None:
        payload = {"records": [{"teamRecords": [
            {"team": {"id": 1, "name": "A", "abbreviation": "AAA"}, "wins": 90, "losses": 60,
             "clinchIndicator": "x", "divisionRank": "1", "wildCardGamesBack": "-"},
        ]}]}
        parsed = grade.parse_standings(payload)
        status = grade.lookup_team_status(parsed, {"id": "1", "abbr": "AAA"})
        self.assertEqual(status["status"], "clinched_playoff")
        self.assertEqual(status["wins"], 90)


class LearningReviewTests(unittest.TestCase):
    def _decided(self, **overrides):
        base = {
            "family": "pitcher_k",
            "pick": "over",
            "line": 5.5,
            "price": -110,
            "p_pick": 0.60,
            "market_p": 0.50,
            "ev_flag": "playable",
            "result": grade.WIN,
            "units": 100 / 110,
            "result_margin": 1.5,
            "projected_strikeouts": 6.5,
            "projected_batters_faced": 24.0,
            "projected_k_rate": 0.27,
            "actual": {"strikeouts": 7, "batters_faced": 24, "outs": 18, "pitches": 95},
        }
        base.update(overrides)
        return base

    def test_margin_toward_picked_side(self) -> None:
        self.assertAlmostEqual(grade.result_margin("pitcher_k", "over", 5.5, 0, 0, 7), 1.5)
        self.assertAlmostEqual(grade.result_margin("pitcher_k", "under", 5.5, 0, 0, 4), 1.5)
        self.assertAlmostEqual(grade.result_margin("game_total", "over", 8.0, 5, 4, None), 1.0)
        self.assertAlmostEqual(grade.result_margin("game_ml", "away", None, 3, 5, None), 2.0)
        self.assertAlmostEqual(grade.result_margin("game_rl", "away_covers", -1.5, 4, 3, None), 0.5)

    def test_pitcher_bucket_attributes_conversion_vs_workload(self) -> None:
        # 24 BF but K rate well below projection -> conversion cold on an over.
        cold = self._decided(projected_k_rate=0.35, actual={"strikeouts": 4, "batters_faced": 24})
        self.assertEqual(grade.pitcher_bucket(cold), "conversion_cold")
        # Pulled early (14 BF vs 24 projected) -> workload short regardless of Ks.
        short = self._decided(projected_k_rate=0.27, actual={"strikeouts": 3, "batters_faced": 14})
        self.assertEqual(grade.pitcher_bucket(short), "workload_short")

    def test_learning_review_groups_and_markdown(self) -> None:
        rows = [
            self._decided(result=grade.WIN),
            self._decided(result=grade.LOSS, units=-1.0),
        ]
        review = grade.build_learning_review(rows)
        self.assertEqual(review["decided"], 2)
        self.assertEqual(review["groups"]["by_family"]["pitcher_k"]["n"], 2)
        self.assertIn("Daily learning review", review["markdown"])
        self.assertIn("Pitcher by workload/conversion", review["markdown"])


class SummaryTests(unittest.TestCase):
    def test_summarize_counts_and_hit_rate(self) -> None:
        rows = [
            {"family": "game_ml", "result": grade.WIN},
            {"family": "game_ml", "result": grade.LOSS},
            {"family": "game_ml", "result": grade.PUSH},
            {"family": "pitcher_k", "result": grade.WIN},
            {"family": "pitcher_k", "result": grade.PENDING},
        ]
        summary = grade.summarize(rows)
        self.assertEqual(summary["totals"]["w"], 2)
        self.assertEqual(summary["totals"]["l"], 1)
        self.assertEqual(summary["totals"]["p"], 1)
        self.assertAlmostEqual(summary["totals"]["hit_rate"], round(2 / 3, 4))

    def test_roi_only_counts_priced_settled(self) -> None:
        rows = [
            {"price": -110, "result": grade.WIN, "units": 100 / 110},
            {"price": 100, "result": grade.LOSS, "units": -1.0},
            {"price": None, "result": grade.WIN, "units": None},
            {"price": -110, "result": grade.PENDING, "units": None},
        ]
        roi = grade.roi_summary(rows)
        self.assertEqual(roi["plays"], 2)
        self.assertAlmostEqual(roi["units"], 100 / 110 - 1.0, places=3)


class LatestAndSlotTests(unittest.TestCase):
    def test_latest_prefers_afternoon_then_noon(self) -> None:
        self.assertEqual(
            grade.latest_run_id(["board-2026-09-10", "board-2026-09-10-noon", "board-2026-09-10-afternoon"]),
            "board-2026-09-10-afternoon",
        )
        self.assertEqual(
            grade.latest_run_id(["board-2026-09-10", "board-2026-09-10-noon"]),
            "board-2026-09-10-noon",
        )
        self.assertEqual(grade.latest_run_id(["board-2026-09-10"]), "board-2026-09-10")

    def test_slot_label(self) -> None:
        self.assertEqual(grade.slot_label("board-2026-09-10-noon"), "noon")
        self.assertEqual(grade.slot_label("board-2026-09-10-afternoon"), "afternoon")
        self.assertEqual(grade.slot_label("board-2026-09-10"), "legacy")


class LedgerTests(unittest.TestCase):
    def _write_ledgers(self, root: Path):
        forecast = root / "forecast_ledger.jsonl"
        roi = root / "picks_roi.jsonl"
        forecast.write_text(
            json.dumps(
                {
                    "run_id": "board-2026-09-10-noon",
                    "screen_date": "2026-09-10",
                    "family": "game_ml",
                    "proposition_id": "g:1:ml",
                    "pick": "home",
                    "line": None,
                    "p_pick": 0.6,
                }
            )
            + "\n"
        )
        roi.write_text(
            json.dumps(
                {
                    "run_id": "board-2026-09-10-noon",
                    "screen_date": "2026-09-10",
                    "family": "game_ml",
                    "proposition_id": "g:1:ml",
                    "pick": "home",
                    "line": None,
                    "price": -110,
                    "payout": 100 / 110,
                    "outcome": "PENDING",
                    "graded": False,
                }
            )
            + "\n"
        )
        return forecast, roi

    def test_load_merges_and_settles_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            forecast, roi = self._write_ledgers(root)
            with mock.patch.object(grade, "FORECAST_LEDGER", forecast), mock.patch.object(grade, "ROI_LEDGER", roi):
                rows = grade.load_screen_rows("2026-09-10")
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["price"], -110)
                self.assertEqual(rows[0]["_roi_index"], 0)

                settlement = {
                    "_roi_index": 0,
                    "result": grade.WIN,
                    "units": 100 / 110,
                }
                self.assertEqual(grade.settle_roi_ledger(roi, [settlement]), 1)
                record = json.loads(roi.read_text().splitlines()[0])
                self.assertEqual(record["outcome"], grade.WIN)
                self.assertTrue(record["graded"])
                # second run does not re-settle
                self.assertEqual(grade.settle_roi_ledger(roi, [settlement]), 0)


class RecapTests(unittest.TestCase):
    def _result(self):
        return {
            "screen_date": "2026-09-10",
            "latest_run_id": "board-2026-09-10-noon",
            "pending_total": 2,
            "revisions": {
                "board-2026-09-10-noon": {
                    "slot": "noon",
                    "summary": {
                        "totals": {"w": 3, "l": 1, "p": 1, "void": 0, "pending": 2, "error": 0, "hit_rate": 0.75},
                        "families": {
                            "pitcher_k": {"w": 2, "l": 0, "p": 0, "void": 0, "pending": 0, "error": 0},
                            "game_ml": {"w": 1, "l": 1, "p": 1, "void": 0, "pending": 2, "error": 0},
                        },
                    },
                    "roi": {"plays": 3, "units": 1.25, "roi": 0.4167},
                }
            },
        }

    def test_recap_contains_key_fields(self) -> None:
        text = grade.render_recap(self._result())
        self.assertIn("MLB Board Recap - 2026-09-10", text)
        self.assertIn("Latest board (noon): 3-1-1 (75.0%)", text)
        self.assertIn("Pitcher Ks: 2-0", text)
        self.assertIn("Priced ROI: +1.25u across 3 plays (+41.7%)", text)
        self.assertIn("Pending: 2 props", text)


class GradeScreenTests(unittest.TestCase):
    def test_api_error_flagged(self) -> None:
        class BrokenClient:
            def finals(self, screen):
                raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "none.jsonl"
            with mock.patch.object(grade, "FORECAST_LEDGER", empty), mock.patch.object(grade, "ROI_LEDGER", empty):
                result = grade.grade_screen("2026-09-10", client=BrokenClient())
        self.assertTrue(result["api_error"])


class CanonicalSelectionTests(unittest.TestCase):
    def _client(self, finals, start="2026-09-10T23:00:00Z"):
        class Fake:
            def finals(self, screen):
                return finals

            def schedule(self, screen):
                return {"dates": [{"games": [{"gamePk": 1, "gameDate": start}]}]}

        return Fake()

    def _row(self, run_id, prop, family, game_pk="1", price=-110, captured="2026-09-10T16:00:00Z", **extra):
        base = {
            "run_id": run_id,
            "screen_date": "2026-09-10",
            "family": family,
            "proposition_id": prop,
            "pick": "home" if family == "game_ml" else ("over" if family == "game_total" else "home"),
            "line": extra.get("line"),
            "price": price,
            "p_pick": 0.6,
            "subject": "Test",
            "subject_id": None,
            "game_pk": game_pk,
            "captured_at": captured,
            "exported_at": captured,
            "_roi_index": extra.get("_roi_index"),
        }
        base.update(extra)
        return base

    def test_canonical_key_excludes_line(self) -> None:
        noon = self._row("board-2026-09-10-noon", "g:1:total:8.0", "game_total", line=8.0, pick="over")
        aft = self._row("board-2026-09-10-afternoon", "g:1:total:8.5", "game_total", line=8.5, pick="over")
        self.assertEqual(grade.canonical_market_key(noon), grade.canonical_market_key(aft))
        ml1 = self._row("r", "g:1:ml", "game_ml")
        ml2 = self._row("r", "g:2:ml", "game_ml", game_pk="2")
        self.assertNotEqual(grade.canonical_market_key(ml1), grade.canonical_market_key(ml2))

    def test_afternoon_replaces_noon(self) -> None:
        noon = self._row("board-2026-09-10-noon", "g:1:ml", "game_ml", captured="2026-09-10T16:00:00Z", _roi_index=0)
        aft = self._row("board-2026-09-10-afternoon", "g:1:ml", "game_ml", captured="2026-09-10T20:00:00Z", _roi_index=1)
        selected, superseded, stats = grade.select_canonical_rows(
            [noon, aft], {"1": grade.parse_row_time(aft) and grade.datetime(2026, 9, 10, 23, 0, tzinfo=grade.timezone.utc)}
        )
        self.assertEqual(len(selected), 1)
        key = next(iter(selected))
        self.assertEqual(selected[key]["run_id"], "board-2026-09-10-afternoon")
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0]["_roi_index"], 0)
        self.assertEqual(stats["replaced_by_afternoon"], 1)

    def test_noon_fallback_when_afternoon_missing(self) -> None:
        noon_only = self._row("board-2026-09-10-noon", "g:1:ml", "game_ml", captured="2026-09-10T16:00:00Z", _roi_index=0)
        selected, superseded, stats = grade.select_canonical_rows([noon_only], {})
        self.assertEqual(len(selected), 1)
        self.assertEqual(stats["noon_only_fallback"], 1)
        self.assertEqual(superseded, [])

    def test_post_start_capture_excluded(self) -> None:
        noon = self._row("board-2026-09-10-noon", "g:1:ml", "game_ml", captured="2026-09-10T16:00:00Z", _roi_index=0)
        late = self._row("board-2026-09-10-afternoon", "g:1:ml", "game_ml", captured="2026-09-11T00:00:00Z", _roi_index=1)
        from datetime import datetime, timezone

        start = {"1": datetime(2026, 9, 10, 23, 0, tzinfo=timezone.utc)}
        selected, superseded, _ = grade.select_canonical_rows([noon, late], start)
        key = next(iter(selected))
        # Late capture is post-start, so noon wins and late is not marked superseded (excluded pool).
        self.assertEqual(selected[key]["run_id"], "board-2026-09-10-noon")
        self.assertEqual(superseded, [])

    def test_changed_total_selects_latest_line(self) -> None:
        finals = {"1": {"final": True, "state": "Final", "home_score": 5, "away_score": 4}}
        noon = self._row(
            "board-2026-09-10-noon", "g:1:total:8.0", "game_total",
            line=8.0, pick="over", price=-110,
            captured="2026-09-10T16:00:00Z", _roi_index=0,
        )
        aft = self._row(
            "board-2026-09-10-afternoon", "g:1:total:8.5", "game_total",
            line=8.5, pick="over", price=-110,
            captured="2026-09-10T20:00:00Z", _roi_index=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            forecast = root / "forecast_ledger.jsonl"
            roi = root / "picks_roi.jsonl"
            forecast.write_text("\n".join([json.dumps(noon), json.dumps(aft)]) + "\n")
            roi.write_text(
                "\n".join([
                    json.dumps({**noon, "payout": 100 / 110, "outcome": "PENDING", "graded": False}),
                    json.dumps({**aft, "payout": 100 / 110, "outcome": "PENDING", "graded": False}),
                ]) + "\n"
            )
            with mock.patch.object(grade, "FORECAST_LEDGER", forecast), mock.patch.object(grade, "ROI_LEDGER", roi):
                result = grade.grade_screen("2026-09-10", client=self._client(finals))
        canonical = result["canonical"]
        self.assertEqual(canonical["selection"]["unique_markets"], 1)
        # 9 runs vs lines: noon 8.0 WIN, afternoon 8.5 WIN (9>8.5) – selection must be afternoon line.
        self.assertEqual(canonical["rows"][0]["line"], 8.5)
        self.assertEqual(canonical["rows"][0]["run_id"], "board-2026-09-10-afternoon")
        # Revisions preserved for audit.
        self.assertIn("board-2026-09-10-noon", result["revisions"])
        self.assertIn("board-2026-09-10-afternoon", result["revisions"])
        # Settlements are canonical only (1, not 2).
        self.assertEqual(len(result["settlements"]), 1)
        self.assertEqual(len(result["superseded"]), 1)

    def test_superseded_excluded_from_roi(self) -> None:
        finals = {"1": {"final": True, "state": "Final", "home_score": 5, "away_score": 3}}
        noon = self._row(
            "board-2026-09-10-noon", "g:1:ml", "game_ml",
            pick="home", price=-110, captured="2026-09-10T16:00:00Z", _roi_index=0,
        )
        aft = self._row(
            "board-2026-09-10-afternoon", "g:1:ml", "game_ml",
            pick="home", price=-110, captured="2026-09-10T20:00:00Z", _roi_index=1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            forecast = root / "forecast_ledger.jsonl"
            roi = root / "picks_roi.jsonl"
            forecast.write_text("\n".join([json.dumps(noon), json.dumps(aft)]) + "\n")
            roi.write_text(
                "\n".join([
                    json.dumps({**noon, "payout": 100 / 110, "outcome": "PENDING", "graded": False}),
                    json.dumps({**aft, "payout": 100 / 110, "outcome": "PENDING", "graded": False}),
                ]) + "\n"
            )
            with mock.patch.object(grade, "FORECAST_LEDGER", forecast), mock.patch.object(grade, "ROI_LEDGER", roi):
                result = grade.grade_screen("2026-09-10", client=self._client(finals))
                self.assertEqual(result["canonical"]["roi"]["plays"], 1)
                changed = grade.settle_roi_ledger(roi, result["settlements"])
                changed += grade.mark_superseded_rows(roi, result["superseded"])
                self.assertEqual(changed, 2)
                lines = [json.loads(x) for x in roi.read_text().splitlines()]
                by_outcome = [r["outcome"] for r in lines]
                self.assertIn(grade.WIN, by_outcome)
                self.assertIn(grade.SUPERSEDED, by_outcome)
                sup = next(r for r in lines if r["outcome"] == grade.SUPERSEDED)
                self.assertEqual(sup["original_outcome"], "PENDING")
                self.assertEqual(sup["units"], 0.0)

    def test_recap_uses_canonical(self) -> None:
        result = {
            "screen_date": "2026-09-10",
            "latest_run_id": "board-2026-09-10-afternoon",
            "pending_total": 0,
            "revisions": {
                "board-2026-09-10-noon": {
                    "slot": "noon",
                    "summary": {"totals": {"w": 3, "l": 1, "p": 0, "void": 0, "pending": 0, "error": 0, "hit_rate": 0.75}, "families": {}},
                    "roi": {"plays": 3, "units": 1.0, "roi": 0.3333},
                },
                "board-2026-09-10-afternoon": {
                    "slot": "afternoon",
                    "summary": {"totals": {"w": 1, "l": 1, "p": 0, "void": 0, "pending": 0, "error": 0, "hit_rate": 0.5}, "families": {}},
                    "roi": {"plays": 2, "units": 0.0, "roi": 0.0},
                },
            },
            "canonical": {
                "summary": {"totals": {"w": 4, "l": 1, "p": 0, "void": 0, "pending": 0, "error": 0, "hit_rate": 0.8}, "families": {}},
                "roi": {"plays": 4, "units": 1.5, "roi": 0.375},
                "selection": {"unique_markets": 5, "replaced_by_afternoon": 3, "noon_only_fallback": 2},
                "pending": 0,
            },
        }
        text = grade.render_recap(result)
        self.assertIn("Canonical board: 4-1 (80.0%)", text)
        self.assertIn("5 unique markets", text)
        self.assertNotIn("Latest board (afternoon)", text)


if __name__ == "__main__":
    unittest.main()
