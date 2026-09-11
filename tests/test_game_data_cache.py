from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from mlb_props.forecasting import game_data


class GameDataCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        game_data._repo_root = None

    def test_current_slate_is_refetched(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_data._repo_root = root
            cached = game_data.cache_dir() / f"slate_{date.today().isoformat()}.json"
            cached.write_text(json.dumps({"dates": []}))
            fresh = {"dates": [{"games": [{"gamePk": 123}]}]}
            with patch.object(game_data, "fetch_json", return_value=fresh) as fetch:
                games = game_data.fetch_slate(date.today().isoformat())
            self.assertEqual(games[0]["gamePk"], 123)
            fetch.assert_called_once()

    def test_open_schedule_chunk_is_overwritten_not_accumulated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_data._repo_root = root
            start = date.today() - timedelta(days=2)
            with patch.object(game_data, "fetch_json", return_value={"dates": []}) as fetch:
                game_data.cache_schedule_chunks(start=start, end=date.today() - timedelta(days=1))
                game_data.cache_schedule_chunks(start=start, end=date.today())
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual([path.name for path in game_data.cache_dir().glob("games_*_open.json")], [f"games_{start.isoformat()}_open.json"])


if __name__ == "__main__":
    unittest.main()
