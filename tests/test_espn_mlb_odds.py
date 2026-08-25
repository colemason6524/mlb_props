from __future__ import annotations

import unittest
from datetime import date

from mlb_props.cache import JsonCache
from mlb_props.sources.espn_odds import EspnMlbOddsSource


def _anchor(exit_value: str, texts: list[str]) -> str:
    inner = "".join(f"<div>{text}</div>" for text in texts)
    return f'<a data-exit-type-value="{exit_value}">{inner}</a>'


def _item(away: str, home: str, away_ml: int, away_total_line: float, away_rl: float, away_total_price: int, home_ml: int, home_rl: float) -> str:
    return (
        f'<a href="/mlb/team/_/name/{away.lower()}/slug-{away.lower()}">Away</a>'
        f'<a href="/mlb/team/_/name/{home.lower()}/slug-{home.lower()}">Home</a>'
        + _anchor(f"moneyline {away_ml}", [str(away_ml)])
        + _anchor(f"total {away_total_price}", [f"o{away_total_line:g}", str(away_total_price)])
        + _anchor("pointSpread 119", [f"{away_rl:+g}", "+119"])
        + _anchor(f"moneyline {home_ml}", [str(home_ml)])
        + _anchor("total -102", [f"u{away_total_line:g}", "-102"])
        + _anchor("pointSpread -143", [f"{home_rl:+g}", "-143"])
    )


HTML = (
    '<div data-testid="betSixPackTable">'
    '<div data-testid="betSixPackTable-item-0-0">'
    + _item("bos", "mia", -139, 7, -1.5, -118, 130, 1.5)
    + "</div>"
    '<div data-testid="betSixPackTable-item-0-1">'
    + _item("chc", "mil", 137, 7.5, 1.5, -105, -147, -1.5)
    + "</div>"
    "</div>"
)


class EspnMlbOddsParsingTests(unittest.TestCase):
    def _source(self) -> EspnMlbOddsSource:
        import tempfile
        from pathlib import Path

        return EspnMlbOddsSource(JsonCache(Path(tempfile.mkdtemp())))

    def test_parses_lines_from_live_layout(self) -> None:
        source = self._source()
        contexts, diagnostics = source._parse_html(HTML)
        self.assertEqual(diagnostics["games_parsed"], 2)
        game_one = contexts[("BOS", "MIA")]
        self.assertEqual(game_one.away_spread, -1.5)
        self.assertEqual(game_one.home_spread, 1.5)
        self.assertEqual(game_one.total, 7.0)
        game_two = contexts[("CHC", "MIL")]
        self.assertEqual(game_two.total, 7.5)
        self.assertEqual(game_two.away_spread, 1.5)
        self.assertEqual(game_two.home_spread, -1.5)

    def test_malformed_blocks_are_diagnosed(self) -> None:
        source = self._source()
        _, diagnostics = source._parse_html('<div data-testid="betSixPackTable-item-9-9">no teams</div>')
        self.assertEqual(diagnostics["games_parsed"], 0)
        self.assertEqual(diagnostics["malformed_blocks"], 1)

    def test_cache_round_trip(self) -> None:
        import tempfile
        from pathlib import Path

        cache = JsonCache(Path(tempfile.mkdtemp()), ttl_hours=1)
        source = EspnMlbOddsSource(cache)
        contexts, _ = source._parse_html(HTML)
        serializable = [
            {"key": list(key), "value": value.as_dict()}
            for key, value in contexts.items()
            if value.total is not None
        ]
        cache.set("espn_odds_mlb_v1_2026-08-25", serializable)
        cached_contexts, diagnostics = source.fetch_game_context(date(2026, 8, 25))
        self.assertEqual(diagnostics["mode"], "cache")
        self.assertEqual(cached_contexts[("BOS", "MIA")].total, 7.0)


if __name__ == "__main__":
    unittest.main()
