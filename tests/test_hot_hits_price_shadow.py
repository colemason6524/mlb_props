from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from mlb_props.hot_hits_price_shadow import (
    attach_hit_price_shadow,
    hit_price_research_targets,
)
from mlb_props.models import HotHitCandidate
from mlb_props.sources import bovada_props
from mlb_props.sources.bovada_props import (
    _slate_event_slugs,
    american_to_implied_probability,
    parse_hit_prop_quotes,
)
from mlb_props.version import HOT_HITS_PRICE_SHADOW_VERSION


def _outcome(name: str, team: str, price: int) -> dict:
    return {"description": f"{name} ({team})", "price": {"american": str(price)}}


def _event_payload() -> dict:
    return {
        "events": [
            {
                "displayGroups": [
                    {
                        "description": "Batter Props",
                        "markets": [
                            {
                                "description": "Player to record a Hit",
                                "period": {"description": "Game"},
                                "status": "O",
                                "outcomes": [
                                    _outcome("Riley Greene", "DET", -210),
                                    _outcome("Josh Bell", "MIN", -235),
                                    _outcome("Weird No Team", "", -150),
                                ],
                            },
                            {
                                "description": "Player to record 2+ Hits",
                                "period": {"description": "Game"},
                                "status": "O",
                                "outcomes": [
                                    _outcome("Riley Greene", "DET", 280),
                                    _outcome("Josh Bell", "MIN", 240),
                                ],
                            },
                            {
                                "description": "Player to record a Hit",
                                "period": {"description": "Game"},
                                "status": "S",
                                "outcomes": [_outcome("Suspended Guy", "DET", -130)],
                            },
                        ],
                    },
                    {"description": "Game Lines", "markets": []},
                ]
            }
        ]
    }


class ParseHitPropQuotesTest(unittest.TestCase):
    def test_extracts_yes_prices_and_alt_line(self) -> None:
        quotes, diag = parse_hit_prop_quotes(_event_payload())
        self.assertEqual(diag["batter_props_group_found"], 1)
        self.assertEqual(diag["hit_markets_seen"], 1)
        self.assertEqual(diag["hits2_markets_seen"], 1)
        riley = quotes[("DET", "riley greene")]
        self.assertEqual(riley.hit_yes_price, -210)
        self.assertEqual(riley.hits_2plus_yes_price, 280)
        josh = quotes[("MIN", "josh bell")]
        self.assertEqual(josh.hit_yes_price, -235)
        self.assertEqual(josh.hits_2plus_yes_price, 240)

    def test_skips_suspended_market_and_unparsed_outcomes(self) -> None:
        quotes, diag = parse_hit_prop_quotes(_event_payload())
        self.assertNotIn(("DET", "suspended guy"), quotes)
        self.assertEqual(diag["outcomes_unparsed"], 1)

    def test_missing_group_fails_open(self) -> None:
        quotes, diag = parse_hit_prop_quotes({"events": [{"displayGroups": []}]})
        self.assertEqual(quotes, {})
        self.assertEqual(diag["batter_props_group_found"], 0)

    def test_implied_probability(self) -> None:
        self.assertAlmostEqual(american_to_implied_probability(-210), 210 / 310, places=6)
        self.assertAlmostEqual(american_to_implied_probability(280), 100 / 380, places=6)
        self.assertIsNone(american_to_implied_probability(0))


class SlateEventSlugTest(unittest.TestCase):
    @staticmethod
    def _et_ms(hours_after_noon: int) -> int:
        import datetime as dt
        from zoneinfo import ZoneInfo

        from mlb_props.sources.bovada_props import EASTERN

        eastern_now = dt.datetime.now(ZoneInfo("America/New_York"))
        noon = eastern_now.replace(hour=12, minute=0, second=0, microsecond=0)
        return int((noon + dt.timedelta(hours=hours_after_noon)).timestamp() * 1000)

    def test_matches_slate_date_and_teams(self) -> None:
        payload = [
            {
                "events": [
                    {
                        "description": "Minnesota Twins @ Detroit Tigers",
                        "link": "/baseball/mlb/minnesota-twins-detroit-tigers-202609091310",
                        "startTime": self._et_ms(1),
                    },
                    {
                        "description": "St. Louis Cardinals @ San Francisco Giants",
                        "link": "/baseball/mlb/st-louis-cardinals-san-francisco-giants-202609091645",
                        "startTime": self._et_ms(2),
                    },
                ]
            }
        ]
        slugs, diag = _slate_event_slugs(payload, date.today())
        self.assertEqual(len(slugs), 2, diag)
        self.assertIn(
            ("MIN", "DET"),
            slugs,
            "twins-tigers event should map to today's slate",
        )
        self.assertEqual(diag["slate_events_matched"], 2)

    def test_other_date_events_filtered(self) -> None:
        import datetime as dt

        payload = [
            {
                "events": [
                    {
                        "description": "Minnesota Twins @ Detroit Tigers",
                        "link": "/baseball/mlb/minnesota-twins-detroit-tigers-202609091310",
                        "startTime": int(
                            (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).timestamp() * 1000
                        ),
                    }
                ]
            }
        ]
        slugs, diag = _slate_event_slugs(payload, date.today())
        self.assertEqual(slugs, {})
        self.assertGreaterEqual(diag["coupon_events_other_date"], 1)


class AttachHitPriceShadowTest(unittest.TestCase):
    def _candidate(self, name: str, team: str) -> HotHitCandidate:
        return HotHitCandidate(
            batter_name=name,
            batter_id=1,
            team=team,
            opponent="OPP",
            bat_side="R",
            position="OF",
            batting_order=1,
            probable_pitcher="P",
            probable_pitcher_id=2,
            pitcher_hand="R",
            games_played=10,
            avg_last_5=0.400,
            avg_last_10=0.350,
            season_avg=0.280,
            obp_last_5=0.450,
            hit_games_last_5=4,
            hit_games_last_10=8,
            at_bats_last_5=20,
            hits_last_5=8,
            hits_last_10=14,
            season_hits=50,
            season_at_bats=180,
            pitcher_hits_allowed_rate_last_5=0.260,
            pitcher_hits_allowed_rate_season=0.250,
            pitcher_k_rate_last_5=0.220,
            pitcher_walk_rate_last_5=0.080,
            batter_vs_pitcher_avg=None,
            batter_vs_pitcher_ab=None,
            matchup_rating=0.1,
            score=12,
        )

    def test_attachment_does_not_change_score_or_flags(self) -> None:
        candidate = self._candidate("Riley Greene", "DET")
        games = [
            SimpleNamespace(
                game_date=date.today(),
                home_team="DET",
                away_team="MIN",
                probable_home_pitcher="A",
                probable_away_pitcher="B",
                probable_home_pitcher_id=1,
                probable_away_pitcher_id=2,
            )
        ]

        calls = {"count": 0}

        def fake_fetch(cache_dir, games, screen_date):
            calls["count"] += 1
            quote = bovada_props.HitPropQuote(
                player_name="riley greene",
                player_name_norm="riley greene",
                team_abbr="DET",
                hit_yes_price=-210,
                hits_2plus_yes_price=280,
                event_slug="det-slug",
                event_start_time_utc=None,
                collected_at_utc="2026-09-09T00:00:00+00:00",
            )
            return {("DET", "riley greene"): quote}, {"quotes_found": 1}

        original = bovada_props.fetch_hit_prop_quotes
        bovada_props.fetch_hit_prop_quotes = fake_fetch
        # attach_hit_price_shadow imports the symbol at module load; patch there too
        import mlb_props.hot_hits_price_shadow as hhps

        hhps_original = hhps.fetch_hit_prop_quotes
        hhps.fetch_hit_prop_quotes = fake_fetch
        try:
            before_score = candidate.score
            before_flags = list(candidate.flags)
            metadata = attach_hit_price_shadow(
                candidates=[candidate],
                games=games,
                screen_date=date.today(),
                cache_dir=None,
            )
            self.assertEqual(metadata["status"], "available")
            self.assertEqual(metadata["matched_candidates"], 1)
            self.assertEqual(candidate.score, before_score)
            self.assertEqual(candidate.flags, before_flags)
            self.assertIsNotNone(candidate.price_shadow)
            self.assertEqual(candidate.price_shadow.version, HOT_HITS_PRICE_SHADOW_VERSION)
            self.assertEqual(candidate.price_shadow.price, -210)
            self.assertEqual(candidate.price_shadow.alt_price, 280)
            self.assertFalse(
                set(candidate.price_shadow.flags) & {"ALT_2PLUS_NOT_LISTED"}
            )
        finally:
            bovada_props.fetch_hit_prop_quotes = original
            hhps.fetch_hit_prop_quotes = hhps_original

    def test_unmatched_candidate_fails_open(self) -> None:
        candidate = self._candidate("Nobody Match", "DET")

        def fake_fetch(cache_dir, games, screen_date):
            return {}, {"quotes_found": 0}

        import mlb_props.hot_hits_price_shadow as hhps

        original = hhps.fetch_hit_prop_quotes
        hhps.fetch_hit_prop_quotes = fake_fetch
        try:
            metadata = attach_hit_price_shadow(
                candidates=[candidate],
                games=[],
                screen_date=date.today(),
                cache_dir=None,
            )
            self.assertEqual(metadata["status"], "no_quotes")
            self.assertIsNone(candidate.price_shadow)
        finally:
            hhps.fetch_hit_prop_quotes = original

    def test_source_failure_is_recorded_not_raised(self) -> None:
        candidate = self._candidate("Riley Greene", "DET")

        def fake_fetch(cache_dir, games, screen_date):
            raise RuntimeError("bovada down")

        import mlb_props.hot_hits_price_shadow as hhps

        original = hhps.fetch_hit_prop_quotes
        hhps.fetch_hit_prop_quotes = fake_fetch
        try:
            metadata = attach_hit_price_shadow(
                candidates=[candidate],
                games=[],
                screen_date=date.today(),
                cache_dir=None,
            )
            self.assertEqual(metadata["status"], "source_failed")
            self.assertIn("bovada down", metadata["error"])
            self.assertIsNone(candidate.price_shadow)
        finally:
            hhps.fetch_hit_prop_quotes = original

    def test_research_target_limit(self) -> None:
        pool = [self._candidate(f"Player {i}", "DET") for i in range(10)]
        targets = hit_price_research_targets(pool, limit=3)
        self.assertEqual(len(targets), 3)
        self.assertEqual(hit_price_research_targets(pool, limit=0), [])


if __name__ == "__main__":
    unittest.main()
