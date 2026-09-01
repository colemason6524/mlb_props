from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from mlb_props.cache import JsonCache
from mlb_props.config import Settings
from mlb_props.models import Game, PropLine
from mlb_props.screener import screen_pitcher_props
from mlb_props.sources.sample import SamplePitcherLogsSource, SamplePitcherPropsSource
from mlb_props.version import PITCHER_HISTORY_SCHEMA_VERSION
from run_nightly import sanitized_settings_payload, slate_games_payload
from mlb_props.sources.odds_api import _opposite_price, _safe_price


def _prop_line(**overrides) -> PropLine:
    values = {
        "event_id": "824158",
        "game_date": date(2026, 8, 5),
        "subject_name_raw": "Gerrit Cole",
        "subject_name_norm": "gerrit cole",
        "subject_role": "pitcher",
        "team": "NYY",
        "opponent": "BOS",
        "hand": "R",
        "prop_type": "PITCHER_STRIKEOUTS",
        "line": 6.5,
        "bookmaker": "fanduel",
        "source": "fanduel_scrape",
        "collected_at": datetime(2026, 8, 5, 15, 35, 0, tzinfo=timezone.utc),
        "subject_id": 1001,
    }
    values.update(overrides)
    return PropLine(**values)


class SanitizedSettingsTests(unittest.TestCase):
    def test_odds_api_key_removed_from_payload(self) -> None:
        settings = Settings(odds_api_key="secret-key-value", run_note="test")
        payload = sanitized_settings_payload(settings)
        self.assertNotIn("odds_api_key", payload)
        self.assertEqual(payload["run_note"], "test")

    def test_original_settings_unchanged(self) -> None:
        settings = Settings(odds_api_key="secret-key-value")
        sanitized_settings_payload(settings)
        self.assertEqual(settings.odds_api_key, "secret-key-value")


class SlateGamesPayloadTests(unittest.TestCase):
    def test_payload_includes_game_times_and_pitchers(self) -> None:
        games = [
            Game(
                game_id="824158",
                game_date=date(2026, 8, 5),
                game_time=datetime(2026, 8, 5, 23, 10, tzinfo=timezone.utc),
                home_team="DET",
                away_team="KC",
                probable_home_pitcher="Home Starter",
                probable_away_pitcher="Away Starter",
                source="mlb_stats_api",
                probable_home_pitcher_id=1001,
                probable_away_pitcher_id=1002,
            )
        ]
        rows = slate_games_payload(games)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["game_id"], "824158")
        self.assertEqual(row["home_team"], "DET")
        self.assertEqual(row["away_team"], "KC")
        self.assertEqual(row["probable_home_pitcher_id"], 1001)
        self.assertIn("2026-08-05T23:10", row["game_time_utc"])

    def test_null_game_time_is_allowed(self) -> None:
        games = [
            Game(
                game_id="1",
                game_date=date(2026, 8, 5),
                game_time=None,
                home_team="A",
                away_team="B",
                probable_home_pitcher="H",
                probable_away_pitcher="A",
                source="test",
            )
        ]
        rows = slate_games_payload(games)
        self.assertIsNone(rows[0]["game_time_utc"])


class CandidateAuditIdentityTests(unittest.TestCase):
    def test_candidate_carries_event_identity_from_prop_line(self) -> None:
        settings = Settings(
            screen_date=date(2026, 8, 5),
            export_history=False,
            min_display_score=-100,
        )
        line = _prop_line()
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = JsonCache(Path(tmp_dir), ttl_hours=24)
            logs_source = SamplePitcherLogsSource(cache)
            logs = logs_source.fetch_logs_for_lines([line])
        prop_lines = [line]
        result = screen_pitcher_props(settings, prop_lines, logs, {})
        self.assertTrue(result.candidates)
        candidate = result.candidates[0]
        self.assertEqual(candidate.event_id, "824158")
        self.assertEqual(candidate.line_source, "fanduel_scrape")
        self.assertEqual(candidate.line_collected_at, "2026-08-05T15:35:00+00:00")


class SchemaVersionTests(unittest.TestCase):
    def test_schema_version_is_eight(self) -> None:
        # Schema 8 (2026-08-31) is additive: daily_card array + policy version.
        self.assertEqual(PITCHER_HISTORY_SCHEMA_VERSION, 8)


class ForecastBoardTests(unittest.TestCase):
    def _screened(self):
        settings = Settings(
            screen_date=date(2026, 8, 5),
            export_history=False,
            min_display_score=-100,
        )
        lines = [
            _prop_line(),
            _prop_line(
                subject_name_raw="Tanner Houck",
                subject_name_norm="tanner houck",
                team="BOS",
                opponent="NYY",
                subject_id=1003,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = JsonCache(Path(tmp_dir), ttl_hours=24)
            logs_source = SamplePitcherLogsSource(cache)
            logs = {}
            for line in lines:
                logs.update(logs_source.fetch_logs_for_lines([line]))
        return screen_pitcher_props(settings, lines, logs, {})

    def test_forecast_rows_captured_for_all_evaluated_lines(self) -> None:
        result = self._screened()
        self.assertEqual(result.evaluated_prop_lines, 2)
        self.assertEqual(len(result.forecast_rows), 2)
        self.assertTrue(result.forecast_rows[0].qualified_over or result.forecast_rows[1].qualified_over)

    def test_forecast_row_carries_audit_identity(self) -> None:
        result = self._screened()
        row = next(r for r in result.forecast_rows if r.subject_name == "Gerrit Cole")
        self.assertEqual(row.event_id, "824158")
        self.assertEqual(row.line_source, "fanduel_scrape")
        self.assertEqual(row.line_collected_at, "2026-08-05T15:35:00+00:00")

    def test_non_qualifying_lines_receive_insufficient_starts_reason(self) -> None:
        line = _prop_line(
            subject_name_raw="Tanner Houck",
            subject_name_norm="tanner houck",
            team="BOS",
            opponent="NYY",
            subject_id=1003,
        )
        settings = Settings(
            screen_date=date(2026, 8, 5),
            export_history=False,
            min_display_score=-100,
        )
        result = screen_pitcher_props(settings, [line], {}, {})
        self.assertEqual(result.non_qualifying_prop_lines, 1)
        self.assertEqual(result.forecast_rows[0].qualification_reason, "insufficient_starts")
        self.assertFalse(result.forecast_rows[0].qualified_over)


class PriceShadowScreenerTests(unittest.TestCase):
    def test_candidate_carries_price_shadow_when_prices_present(self) -> None:
        line = _prop_line(over_price=-110, under_price=-110)
        settings = Settings(
            screen_date=date(2026, 8, 5),
            export_history=False,
            min_display_score=-100,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = JsonCache(Path(tmp_dir), ttl_hours=24)
            logs_source = SamplePitcherLogsSource(cache)
            logs = logs_source.fetch_logs_for_lines([line])
        result = screen_pitcher_props(settings, [line], logs, {})
        self.assertTrue(result.candidates)
        shadow = result.candidates[0].price_shadow
        self.assertIsNotNone(shadow)
        self.assertEqual(shadow.over_price, -110)
        self.assertEqual(shadow.under_price, -110)
        self.assertIsNotNone(shadow.over_no_vig_probability)

    def test_candidate_has_no_price_shadow_without_prices(self) -> None:
        line = _prop_line()
        settings = Settings(
            screen_date=date(2026, 8, 5),
            export_history=False,
            min_display_score=-100,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = JsonCache(Path(tmp_dir), ttl_hours=24)
            logs_source = SamplePitcherLogsSource(cache)
            logs = logs_source.fetch_logs_for_lines([line])
        result = screen_pitcher_props(settings, [line], logs, {})
        self.assertTrue(result.candidates)
        self.assertIsNone(result.candidates[0].price_shadow)


class OddsApiPriceHelpersTests(unittest.TestCase):
    def test_safe_price_parses_and_rejects_zero(self) -> None:
        self.assertEqual(_safe_price(-110), -110)
        self.assertEqual(_safe_price(150), 150)
        self.assertIsNone(_safe_price(0))
        self.assertIsNone(_safe_price("nope"))
        self.assertIsNone(_safe_price(None))

    def test_opposite_price_finds_named_outcome(self) -> None:
        outcomes = [
            {"name": "Over", "point": 6.5, "price": -110},
            {"name": "Under", "point": 6.5, "price": -120},
        ]
        self.assertEqual(_opposite_price(outcomes, "Under"), -120)
        self.assertIsNone(_opposite_price(outcomes, "Missing"))


if __name__ == "__main__":
    unittest.main()
