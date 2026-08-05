from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

from mlb_props.cache import JsonCache
from mlb_props.hot_hits_policy import hot_hit_tier, select_hot_hits_card
from mlb_props.models import ContactQualityShadow, HotHitCandidate
from mlb_props.output import render_hot_hit_candidates, render_hot_hits_discord_embeds
from mlb_props.sources.baseball_savant import (
    BaseballSavantContactSource,
    enrich_contact_quality_profile,
)
from run_hot_hits import attach_contact_quality_shadow


CSV_TEXT = """game_date,batter,events,type,launch_speed,launch_angle,game_pk,estimated_ba_using_speedangle,launch_speed_angle,at_bat_number
2026-07-31,101,single,X,100,20,3,.800,6,4
2026-07-30,101,field_out,X,90,5,2,.200,4,3
2026-07-29,101,strikeout,S,,,1,,,2
2026-08-01,101,single,X,105,22,4,.950,6,1
"""


def candidate(**overrides) -> HotHitCandidate:
    base = HotHitCandidate(
        batter_name="Test Hitter",
        batter_id=101,
        team="DET",
        opponent="CLE",
        bat_side="R",
        position="1B",
        batting_order=1,
        probable_pitcher="Test Pitcher",
        probable_pitcher_id=202,
        pitcher_hand="R",
        games_played=40,
        avg_last_5=0.400,
        avg_last_10=0.400,
        season_avg=0.280,
        obp_last_5=0.450,
        hit_games_last_5=5,
        hit_games_last_10=8,
        at_bats_last_5=20,
        hits_last_5=8,
        hits_last_10=13,
        season_hits=45,
        season_at_bats=150,
        pitcher_hits_allowed_rate_last_5=0.260,
        pitcher_hits_allowed_rate_season=0.250,
        pitcher_k_rate_last_5=0.220,
        pitcher_walk_rate_last_5=0.080,
        batter_vs_pitcher_avg=None,
        batter_vs_pitcher_ab=None,
        matchup_rating=0.20,
        score=14,
        flags=[],
    )
    return replace(base, **overrides)


class BaseballSavantContactTests(unittest.TestCase):
    def test_source_uses_prior_day_only_and_caches_aggregated_profiles(self) -> None:
        calls: list[str] = []

        def fetcher(url: str, **kwargs) -> str:
            calls.append(url)
            return CSV_TEXT

        with tempfile.TemporaryDirectory() as temp_dir:
            source = BaseballSavantContactSource(
                JsonCache(Path(temp_dir), ttl_hours=24),
                fetcher=fetcher,
            )
            profiles = source.fetch_profiles([101], screen_date=date(2026, 8, 1))
            cached_profiles = source.fetch_profiles([101], screen_date=date(2026, 8, 1))

        self.assertEqual(len(calls), 1)
        self.assertIn("game_date_lt=2026-07-31", calls[0])
        self.assertIn("batters_lookup%5B%5D=101", calls[0])
        self.assertEqual(profiles, cached_profiles)

        profile = profiles[101]
        self.assertEqual(profile.games_available, 3)
        self.assertEqual(profile.plate_appearances_season, 3)
        self.assertEqual(profile.xba_opportunities_season, 3)
        self.assertAlmostEqual(profile.season_xba, 0.333, places=3)
        self.assertAlmostEqual(profile.xba_last_25_bbe, 0.500, places=3)
        self.assertAlmostEqual(profile.hard_hit_rate_last_25_bbe, 0.500, places=3)
        self.assertAlmostEqual(profile.sweet_spot_rate_last_25_bbe, 0.500, places=3)
        self.assertAlmostEqual(profile.barrel_rate_last_25_bbe, 0.500, places=3)

    def test_enrichment_creates_observation_only_hit_probability(self) -> None:
        profile = ContactQualityShadow(
            version="contact-quality-shadow-v1",
            source="test",
            screen_date="2026-08-01",
            query_start_date="2026-01-01",
            query_end_date="2026-07-31",
            batter_id=101,
            games_available=10,
            recent_game_dates=[],
            plate_appearances_season=100,
            xba_opportunities_season=80,
            xba_opportunities_last_10_games=20,
            tracked_bbe_season=60,
            tracked_bbe_last_10_games=15,
            tracked_bbe_last_25=25,
            season_xba=0.300,
            xba_last_10_games=0.340,
            xba_last_25_bbe=0.400,
            hard_hit_rate_last_25_bbe=0.480,
            sweet_spot_rate_last_25_bbe=0.360,
            barrel_rate_last_25_bbe=0.120,
            avg_exit_velocity_last_25_bbe=91.2,
        )

        enriched = enrich_contact_quality_profile(
            profile,
            actual_avg_last_10=0.430,
            expected_at_bats=4.0,
        )

        self.assertEqual(enriched.confidence, "high")
        self.assertEqual(enriched.actual_ba_minus_xba_last_10, 0.09)
        self.assertIn("RESULTS_ABOVE_CONTACT", enriched.flags)
        self.assertIn("XBA_TREND_PLUS", enriched.flags)
        self.assertIn("HARD_HIT_PLUS", enriched.flags)
        self.assertAlmostEqual(enriched.estimated_one_hit_probability, 0.786, places=3)

    def test_shadow_does_not_change_tier_card_or_discord_content(self) -> None:
        original = candidate()
        shadow = ContactQualityShadow(
            version="contact-quality-shadow-v1",
            source="test",
            screen_date="2026-08-01",
            query_start_date="2026-01-01",
            query_end_date="2026-07-31",
            batter_id=101,
            games_available=10,
            recent_game_dates=[],
            plate_appearances_season=100,
            xba_opportunities_season=80,
            xba_opportunities_last_10_games=20,
            tracked_bbe_season=60,
            tracked_bbe_last_10_games=15,
            tracked_bbe_last_25=25,
            season_xba=0.300,
            xba_last_10_games=0.340,
            xba_last_25_bbe=0.400,
            hard_hit_rate_last_25_bbe=0.480,
            sweet_spot_rate_last_25_bbe=0.360,
            barrel_rate_last_25_bbe=0.120,
            avg_exit_velocity_last_25_bbe=91.2,
            expected_at_bats=4.0,
            estimated_one_hit_probability=0.777,
            confidence="high",
        )
        observed = replace(original, contact_quality_shadow=shadow)

        self.assertEqual(hot_hit_tier(original), hot_hit_tier(observed))
        self.assertEqual(
            select_hot_hits_card([original]).core[0].batter_name,
            select_hot_hits_card([observed]).core[0].batter_name,
        )
        self.assertEqual(
            render_hot_hits_discord_embeds([original], "2026-08-01", 1, 18),
            render_hot_hits_discord_embeds([observed], "2026-08-01", 1, 18),
        )
        terminal = render_hot_hit_candidates([observed])
        self.assertIn("xBA10", terminal)
        self.assertIn("77.7%", terminal)

    def test_source_failure_leaves_the_candidate_usable(self) -> None:
        class FailingSource:
            @staticmethod
            def fetch_profiles(*args, **kwargs):
                raise RuntimeError("temporary Savant failure")

        item = candidate()
        metadata = attach_contact_quality_shadow(
            candidates=[item],
            logs_by_batter={},
            screen_date=date(2026, 8, 1),
            source=FailingSource(),
        )

        self.assertEqual(metadata["status"], "source_failed")
        self.assertIn("temporary Savant failure", metadata["error"])
        self.assertIsNone(item.contact_quality_shadow)
        self.assertEqual(hot_hit_tier(item), "Core")

    def test_partial_batch_failure_preserves_successful_profiles(self) -> None:
        calls = 0

        def fetcher(url: str, **kwargs) -> str:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("second batch unavailable")
            return CSV_TEXT

        with tempfile.TemporaryDirectory() as temp_dir:
            source = BaseballSavantContactSource(
                JsonCache(Path(temp_dir), ttl_hours=24),
                fetcher=fetcher,
            )
            profiles = source.fetch_profiles(
                range(101, 127),
                screen_date=date(2026, 8, 1),
            )

        self.assertEqual(calls, 2)
        self.assertEqual(list(profiles), [101])


if __name__ == "__main__":
    unittest.main()
