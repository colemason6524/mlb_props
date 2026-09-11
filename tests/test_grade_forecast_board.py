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


if __name__ == "__main__":
    unittest.main()
