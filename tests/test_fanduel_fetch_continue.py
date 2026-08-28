from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from mlb_props.cache import JsonCache
from mlb_props.config import PITCHER_STRIKEOUTS
from mlb_props.models import Game
from mlb_props.sources.fanduel import MlbFanDuelSource


def _game() -> Game:
    return Game(
        game_id="g1",
        game_date=date(2026, 8, 27),
        game_time=datetime(2026, 8, 27, 19, 10),
        home_team="NYY",
        away_team="BOS",
        probable_home_pitcher="Home Arm",
        probable_away_pitcher="Away Arm",
        source="test",
    )


class FanDuelFetchContinueTests(unittest.TestCase):
    def test_one_team_timeout_does_not_abort_slate(self) -> None:
        calls: list[str] = []

        def fake_fetch_text(url: str, **kwargs):
            calls.append(url)
            raise RuntimeError("Network error for %s: The read operation timed out" % url) from TimeoutError(
                "The read operation timed out"
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = JsonCache(Path(tmp_dir), ttl_hours=0.0)
            source = MlbFanDuelSource(shared_cache=cache, lines_cache=cache)
            with patch("mlb_props.sources.fanduel.build_probable_pitcher_index", return_value={}):
                with patch("mlb_props.sources.fanduel.fetch_text", side_effect=fake_fetch_text):
                    lines = source.fetch_prop_lines([_game()], [PITCHER_STRIKEOUTS])

        self.assertEqual(lines, [])
        self.assertGreaterEqual(len(calls), 2)
        self.assertGreaterEqual(source.diagnostics().get("team_pages_fetch_error", 0), 1)
        self.assertGreaterEqual(source.diagnostics().get("team_pages_missing", 0), 1)
