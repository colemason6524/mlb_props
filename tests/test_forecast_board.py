from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from run_forecast_board import (
    american_payout,
    append_jsonl,
    board_filename,
    chunk_messages,
    delivery_already_sent,
    derive_run_id,
    ev_flag,
    expected_value,
    export_age_minutes,
    filter_rows_by_start,
    parse_utc,
    prune_old_files,
    record_delivery,
    render_board_text,
    required_family_errors,
    start_times_from_pitcher_export,
)


class PriceMathTests(unittest.TestCase):
    def test_american_payout(self) -> None:
        self.assertAlmostEqual(american_payout(150), 1.5)
        self.assertAlmostEqual(american_payout(-200), 0.5)
        self.assertIsNone(american_payout(None))

    def test_expected_value_sign(self) -> None:
        # 60% to win at +100 pays 1.0: EV = .6*1 - .4 = .2
        self.assertAlmostEqual(expected_value(0.6, 100), 0.2)
        # 45% at -110 (payout .909): EV = .45*.909 - .55 < 0
        self.assertLess(expected_value(0.45, -110), 0.0)
        self.assertIsNone(expected_value(0.6, None))

    def test_expected_value_with_push(self) -> None:
        from run_forecast_board import expected_value_with_push

        # Whole total: p_over 0.44, p_under 0.43, push 0.13
        # Over pick EV = 0.44*1 - 0.43 (not 1-0.44), push refunds 0.
        self.assertAlmostEqual(expected_value_with_push(0.44, 0.43, 100), 0.44 - 0.43)
        self.assertAlmostEqual(expected_value_with_push(0.43, 0.44, 100), 0.43 - 0.44)
        # Push reduces loss vs naive EV
        naive = expected_value(0.44, 100)  # 0.44 - 0.56 = -0.12
        push_aware = expected_value_with_push(0.44, 0.43, 100)  # 0.01
        self.assertGreater(push_aware, naive)
        self.assertIsNone(expected_value_with_push(None, 0.5, 100))
        self.assertIsNone(expected_value_with_push(0.5, None, 100))
        self.assertIsNone(expected_value_with_push(0.5, 0.4, None))

    def test_ev_flag_thresholds(self) -> None:
        self.assertEqual(ev_flag(0.05), "playable")
        self.assertEqual(ev_flag(0.0), "thin")
        self.assertEqual(ev_flag(-0.05), "no_value")
        self.assertEqual(ev_flag(None), "unpriced")


class LedgerTests(unittest.TestCase):
    def test_append_is_idempotent_on_run_and_proposition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.jsonl"
            rows = [
                {"run_id": "r1", "proposition_id": "p1", "v": 1},
                {"run_id": "r1", "proposition_id": "p2", "v": 2},
            ]
            self.assertEqual(append_jsonl(path, rows, ("run_id", "proposition_id")), 2)
            self.assertEqual(append_jsonl(path, rows, ("run_id", "proposition_id")), 0)
            self.assertEqual(
                append_jsonl(
                    path,
                    [{"run_id": "r2", "proposition_id": "p1", "v": 3}],
                    ("run_id", "proposition_id"),
                ),
                1,
            )
            self.assertEqual(len(path.read_text().splitlines()), 3)


class RuntimeSafetyTests(unittest.TestCase):
    def test_required_families_must_be_healthy_and_nonempty(self) -> None:
        sections = {"pitcher_k": [{"pick": "over"}], "game": [{"pick": "home"}]}
        self.assertEqual(required_family_errors({"pitcher_k": "ok", "game": "ok"}, sections), [])
        self.assertEqual(
            required_family_errors({"pitcher_k": "no_export", "game": "ok"}, sections),
            ["pitcher_k=no_export"],
        )
        self.assertEqual(
            required_family_errors({"pitcher_k": "ok", "game": "ok"}, {"pitcher_k": [], "game": []}),
            ["pitcher_k=empty", "game=empty"],
        )

    def test_retention_prunes_only_expired_matching_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expired = root / "expired.json"
            current = root / "current.json"
            ignored = root / "expired.txt"
            for path in (expired, current, ignored):
                path.write_text("x")
            import os
            import time

            old = time.time() - 3 * 86400
            os.utime(expired, (old, old))
            os.utime(ignored, (old, old))
            self.assertEqual(prune_old_files(root, "*.json", 2), 1)
            self.assertFalse(expired.exists())
            self.assertTrue(current.exists())
            self.assertTrue(ignored.exists())


class RenderTests(unittest.TestCase):
    def test_board_text_orders_by_probability(self) -> None:
        sections = {
            "pitcher_k": [
                {"subject": "Low", "pick": "under", "line": 5.5, "p_pick": 0.52,
                 "price": 110, "ev": 0.01, "ev_flag": "thin"},
                {"subject": "High", "pick": "over", "line": 6.5, "p_pick": 0.61,
                 "price": -110, "ev": 0.10, "ev_flag": "playable"},
            ]
        }
        text = render_board_text("2026-09-08", sections)
        self.assertIn("Pitcher Strikeouts (2)", text)
        self.assertLess(text.index("High"), text.index("Low"))
        self.assertIn("EV n/a", render_board_text("d", {"b": [{"subject": "X", "pick": "hit", "line": None, "p_pick": 0.6, "price": None, "ev": None, "ev_flag": "unpriced"}]}))

    def test_game_markets_split_into_readable_sections(self) -> None:
        sections = {
            "game": [
                {"family": "game_ml", "subject": "New York Mets @ New York Yankees",
                 "pick": "home", "home_team": "New York Yankees", "away_team": "New York Mets",
                 "line": None, "p_pick": 0.61, "price": -155, "ev": 0.08, "ev_flag": "playable"},
                {"family": "game_rl", "subject": "New York Mets @ New York Yankees",
                 "pick": "away_covers", "home_team": "New York Yankees", "away_team": "New York Mets",
                 "line": -1.5, "p_pick": 0.58, "price": -180, "ev": 0.24, "ev_flag": "playable"},
                {"family": "game_total", "subject": "New York Mets @ New York Yankees",
                 "pick": "under", "home_team": "New York Yankees", "away_team": "New York Mets",
                 "line": 8.0, "p_pick": 0.63, "price": -105, "ev": 0.22, "ev_flag": "playable"},
            ]
        }
        text = render_board_text("2026-09-11", sections)
        self.assertIn("Moneyline (1)", text)
        self.assertIn("Run Line (1)", text)
        self.assertIn("Totals (1)", text)
        self.assertIn("New York Yankees @-155", text)
        self.assertIn("New York Mets +1.5 @-180", text)
        self.assertNotIn("away_covers", text)
        self.assertIn("Under 8.0 @-105", text)

    def test_chunks_respect_limit(self) -> None:
        text = "\n".join(f"line {i:04d} " + "x" * 60 for i in range(200))
        chunks = chunk_messages(text, limit=1900)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 1900)
        self.assertEqual("".join(chunks), text if text.endswith("\n") else text + "" or "".join(chunks))


class ArtifactRoundTripTests(unittest.TestCase):
    def test_isotonic_round_trip(self) -> None:
        from mlb_props.calibration import IsotonicCalibrator

        cal = IsotonicCalibrator.fit([(0.2, 0), (0.4, 0), (0.6, 1), (0.8, 1)])
        restored = IsotonicCalibrator.from_dict(cal.to_dict())
        self.assertEqual(restored.knots, cal.knots)
        self.assertAlmostEqual(restored.predict(0.5), cal.predict(0.5))

    def test_board_loads_both_production_artifacts(self) -> None:
        from run_forecast_board import load_game_engine, load_pitcher_engine

        engine = load_pitcher_engine()
        self.assertIsNotNone(engine)
        self.assertGreater(len(engine.logit.weights), 0)
        game = load_game_engine()
        self.assertIsNotNone(game)
        self.assertGreater(len(game.weights), 0)


if __name__ == "__main__":
    unittest.main()


class EmptyBoardTests(unittest.TestCase):
    def test_missing_required_families_refuse_to_publish(self) -> None:
        import run_forecast_board as board

        code = board.main(["--date", "2999-01-01", "--run-id", "test-empty-board", "--skip-games"])
        self.assertEqual(code, 1)


class SlotTests(unittest.TestCase):
    def test_run_id_and_filename_per_slot(self) -> None:
        self.assertEqual(derive_run_id("2026-09-11", "noon"), "board-2026-09-11-noon")
        self.assertEqual(derive_run_id("2026-09-11", "afternoon"), "board-2026-09-11-afternoon")
        self.assertEqual(derive_run_id("2026-09-11", None), "board-2026-09-11")
        self.assertEqual(derive_run_id("2026-09-11", "noon", "custom"), "custom")
        self.assertEqual(board_filename("2026-09-11", "noon"), "forecast_board_2026-09-11_noon.json")
        self.assertEqual(board_filename("2026-09-11", "afternoon"), "forecast_board_2026-09-11_afternoon.json")
        self.assertEqual(board_filename("2026-09-11"), "forecast_board_2026-09-11.json")

    def test_render_labels_revisions(self) -> None:
        self.assertIn("(Noon Board)", render_board_text("2026-09-11", {}, "noon"))
        self.assertIn("(Afternoon Update)", render_board_text("2026-09-11", {}, "afternoon"))
        self.assertNotIn("(Noon Board)", render_board_text("2026-09-11", {}))


class StartedEventFilterTests(unittest.TestCase):
    def _as_of(self, value: str):
        return parse_utc(value)

    def test_filters_started_too_close_and_keeps_unknown(self) -> None:
        as_of = self._as_of("2026-09-11T16:00:00Z")
        rows = [
            {"game_pk": "started"},
            {"game_pk": "soon"},
            {"game_pk": "later"},
            {"game_pk": "unknown"},
        ]
        starts = {
            "started": "2026-09-11T15:00:00Z",
            "soon": "2026-09-11T16:05:00Z",
            "later": "2026-09-11T18:00:00Z",
        }
        kept, stats = filter_rows_by_start(rows, starts, as_of, buffer_minutes=10)
        self.assertEqual([row["game_pk"] for row in kept], ["later", "unknown"])
        self.assertEqual(stats, {"started": 1, "too_close": 1, "unknown_start": 1})

    def test_exactly_at_cutoff_is_excluded(self) -> None:
        as_of = self._as_of("2026-09-11T16:00:00Z")
        starts = {"g": "2026-09-11T16:10:00Z"}
        kept, stats = filter_rows_by_start([{"game_pk": "g"}], starts, as_of, buffer_minutes=10)
        self.assertEqual(kept, [])
        self.assertEqual(stats["too_close"], 1)

    def test_start_times_from_pitcher_export(self) -> None:
        export = {
            "slate_games": [
                {"game_id": "1", "game_time_utc": "2026-09-11T18:00:00Z"},
                {"game_id": None, "game_time_utc": "2026-09-11T19:00:00Z"},
            ]
        }
        self.assertEqual(
            start_times_from_pitcher_export(export),
            {"1": "2026-09-11T18:00:00Z"},
        )


class FreshExportTests(unittest.TestCase):
    def test_export_age_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.json"
            path.write_text(json.dumps({"exported_at": "2026-09-11T16:00:00Z"}))
            as_of = parse_utc("2026-09-11T16:05:00Z")
            self.assertAlmostEqual(export_age_minutes(path, as_of), 5.0)

    def test_export_age_missing_timestamp_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.json"
            path.write_text(json.dumps({}))
            self.assertIsNone(export_age_minutes(path, parse_utc("2026-09-11T16:05:00Z")))


class DeliveryDedupTests(unittest.TestCase):
    def test_successful_delivery_blocks_repost_and_allows_retry(self) -> None:
        import run_forecast_board as board

        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "discord_delivery.jsonl"
            with mock.patch.object(board, "DELIVERY_LEDGER", ledger):
                self.assertFalse(delivery_already_sent("2026-09-11", "noon"))
                board.record_delivery("2026-09-11", "noon", "board-2026-09-11-noon", "partial", 1)
                self.assertFalse(delivery_already_sent("2026-09-11", "noon"))
                board.record_delivery("2026-09-11", "noon", "board-2026-09-11-noon", "sent", 2)
                self.assertTrue(delivery_already_sent("2026-09-11", "noon"))
                self.assertFalse(delivery_already_sent("2026-09-11", "afternoon"))

    def test_no_slot_never_dedupes(self) -> None:
        self.assertFalse(delivery_already_sent("2026-09-11", None))
