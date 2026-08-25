from __future__ import annotations

import unittest

from mlb_props.game_markets import (
    GameMarketSnapshot,
    TwoWayPrice,
    home_win_no_vig,
    market_baseline_payload,
    over_no_vig,
)


def _snapshot(
    moneyline: TwoWayPrice | None,
    spread: TwoWayPrice | None = None,
    total: TwoWayPrice | None = None,
) -> GameMarketSnapshot:
    return GameMarketSnapshot(
        game_id="123",
        game_date="2026-08-25",
        home_team="NYY",
        away_team="BOS",
        start_time_utc=None,
        moneyline=moneyline,
        spread=spread,
        total=total,
        source="bovada",
    )


class NoVigTests(unittest.TestCase):
    def test_home_win_no_vig_sums_to_one(self) -> None:
        home, away = home_win_no_vig(TwoWayPrice(None, -150, 130))
        self.assertIsNotNone(home)
        self.assertIsNotNone(away)
        self.assertAlmostEqual(home + away, 1.0, places=6)

    def test_home_win_no_vig_directionality(self) -> None:
        home, _ = home_win_no_vig(TwoWayPrice(None, -200, 170))
        self.assertGreater(home, 0.5)

    def test_over_no_vig_sums_to_one(self) -> None:
        over, under = over_no_vig(TwoWayPrice(8.5, -110, -110))
        self.assertAlmostEqual(over, 0.5, places=6)
        self.assertAlmostEqual(over + under, 1.0, places=6)

    def test_missing_prices_return_none(self) -> None:
        self.assertEqual(home_win_no_vig(None), (None, None))
        self.assertEqual(home_win_no_vig(TwoWayPrice(None, None, -120)), (None, None))
        self.assertEqual(over_no_vig(None), (None, None))

    def test_baseline_payload_rounds_and_keeps_nones(self) -> None:
        payload = market_baseline_payload(_snapshot(TwoWayPrice(None, -150, 130)))
        self.assertAlmostEqual(sum([payload["home_win_no_vig"], payload["away_win_no_vig"]]), 1.0, places=3)
        self.assertIsNone(payload["over_no_vig"])
        self.assertIsNone(payload["under_no_vig"])

    def test_baseline_payload_with_all_markets(self) -> None:
        payload = market_baseline_payload(
            _snapshot(
                TwoWayPrice(None, -150, 130),
                spread=TwoWayPrice(-1.5, 105, -125),
                total=TwoWayPrice(8.5, -110, -110),
            )
        )
        self.assertIsNotNone(payload["home_win_no_vig"])
        self.assertIsNotNone(payload["over_no_vig"])
        self.assertAlmostEqual(payload["under_no_vig"], payload["over_no_vig"], places=4)


class SnapshotSerializationTests(unittest.TestCase):
    def test_as_dict_includes_espn_cross_check(self) -> None:
        snapshot = _snapshot(TwoWayPrice(None, -150, 130), total=TwoWayPrice(8.5, -110, -110))
        snapshot.espn_total = 8.5
        data = snapshot.as_dict()
        self.assertEqual(data["espn_cross_check"]["total"], 8.5)
        self.assertEqual(data["moneyline"]["price_a"], -150)
        self.assertEqual(data["total"]["line"], 8.5)


if __name__ == "__main__":
    unittest.main()
