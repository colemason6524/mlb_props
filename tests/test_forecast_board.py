from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from run_forecast_board import (
    american_payout,
    append_jsonl,
    chunk_messages,
    ev_flag,
    expected_value,
    render_board_text,
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
        self.assertIn("pitcher_k (2)", text)
        self.assertLess(text.index("High"), text.index("Low"))
        self.assertIn("EV n/a", render_board_text("d", {"b": [{"subject": "X", "pick": "hit", "line": None, "p_pick": 0.6, "price": None, "ev": None, "ev_flag": "unpriced"}]}))

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

    def test_board_loads_all_three_artifacts(self) -> None:
        from run_forecast_board import load_game_engine, load_pitcher_engine

        engine = load_pitcher_engine()
        self.assertIsNotNone(engine)
        self.assertGreater(len(engine.logit.weights), 0)
        game = load_game_engine()
        self.assertIsNotNone(game)
        self.assertGreater(len(game.weights), 0)


if __name__ == "__main__":
    unittest.main()
