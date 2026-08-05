from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from mlb_props.config import Settings
from mlb_props.hot_hits import screen_hot_hitters, screen_hot_hitters_with_research
from mlb_props.hot_hits_confidence import estimate_hot_hit_confidence
from mlb_props.models import BatterGameLog, ContactQualityShadow, Game, HotHitCandidate
from mlb_props.output import render_hot_hit_confidence_research
from mlb_props.output import render_hot_hits_discord_embeds
from mlb_props.sources.mlb_stats_api import ProjectedBatter
from run_hot_hits import attach_hot_hit_confidence
from mlb_props.utils import normalize_name


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
        games_played=50,
        avg_last_5=0.400,
        avg_last_10=0.320,
        season_avg=0.285,
        obp_last_5=0.450,
        hit_games_last_5=5,
        hit_games_last_10=8,
        at_bats_last_5=20,
        hits_last_5=8,
        hits_last_10=13,
        season_hits=50,
        season_at_bats=175,
        pitcher_hits_allowed_rate_last_5=0.260,
        pitcher_hits_allowed_rate_season=0.250,
        pitcher_k_rate_last_5=0.220,
        pitcher_walk_rate_last_5=0.080,
        batter_vs_pitcher_avg=None,
        batter_vs_pitcher_ab=None,
        matchup_rating=0.10,
        score=14,
        at_bats_last_10=40,
    )
    return replace(base, **overrides)


def contact_shadow(**overrides) -> ContactQualityShadow:
    base = ContactQualityShadow(
        version="contact-quality-shadow-v1",
        source="test",
        screen_date="2026-08-04",
        query_start_date="2026-01-01",
        query_end_date="2026-08-03",
        batter_id=101,
        games_available=50,
        recent_game_dates=[],
        plate_appearances_season=200,
        xba_opportunities_season=160,
        xba_opportunities_last_10_games=30,
        tracked_bbe_season=120,
        tracked_bbe_last_10_games=22,
        tracked_bbe_last_25=25,
        season_xba=0.285,
        xba_last_10_games=0.320,
        xba_last_25_bbe=0.390,
        hard_hit_rate_last_25_bbe=0.480,
        sweet_spot_rate_last_25_bbe=0.360,
        barrel_rate_last_25_bbe=0.120,
        avg_exit_velocity_last_25_bbe=91.2,
        expected_at_bats=4.0,
        confidence="high",
    )
    return replace(base, **overrides)


def batter_logs(name: str, player_id: int, first_five_hits: list[int]) -> list[BatterGameLog]:
    hits = first_five_hits + [2, 2, 1, 1, 1]
    return [
        BatterGameLog(
            batter_name_raw=name,
            batter_name_norm=normalize_name(name),
            game_date=date(2026, 8, 3) - timedelta(days=index),
            team="DET",
            opponent="CLE",
            bat_side="R",
            position="1B",
            batting_order=1,
            at_bats=4,
            hits=hit_count,
            doubles=0,
            triples=0,
            home_runs=0,
            walks=0,
            strikeouts=1,
            plate_appearances=4,
            source="test",
            batter_id=player_id,
        )
        for index, hit_count in enumerate(hits)
    ]


class HotHitsResearchPoolTests(unittest.TestCase):
    def test_broader_pool_preserves_current_candidates_and_records_gate_misses(self) -> None:
        game = Game(
            game_id="1",
            game_date=date(2026, 8, 4),
            game_time=datetime(2026, 8, 4, 23, tzinfo=timezone.utc),
            home_team="CLE",
            away_team="DET",
            probable_home_pitcher="Test Pitcher",
            probable_away_pitcher="Other Pitcher",
            source="test",
            probable_home_pitcher_id=202,
            probable_away_pitcher_id=203,
        )
        current = ProjectedBatter(101, "Current Hitter", "DET", "CLE", "1B", 1)
        research = ProjectedBatter(102, "Three Game Hitter", "DET", "CLE", "OF", 2)
        logs = {
            normalize_name(current.name): batter_logs(current.name, 101, [2, 2, 2, 1, 0]),
            normalize_name(research.name): batter_logs(research.name, 102, [3, 3, 2, 0, 0]),
        }
        settings = Settings(screen_date=date(2026, 8, 4))

        production = screen_hot_hitters(settings, [game], [current, research], logs, {})
        screening = screen_hot_hitters_with_research(
            settings,
            [game],
            [current, research],
            logs,
            {},
        )

        self.assertEqual([item.batter_name for item in production], ["Current Hitter"])
        self.assertEqual(
            [item.batter_name for item in screening.candidates],
            ["Current Hitter"],
        )
        self.assertEqual(len(screening.research_pool), 2)
        missed = next(
            item for item in screening.research_pool if item.batter_name == "Three Game Hitter"
        )
        self.assertFalse(missed.current_gate_qualified)
        self.assertFalse(missed.current_display_qualified)
        self.assertEqual(missed.hit_games_last_5, 3)
        self.assertIn("L5_HIT_GAMES_BELOW_CURRENT_GATE", missed.gate_failures)


class HotHitsConfidenceTests(unittest.TestCase):
    def test_contact_quality_and_opportunity_raise_confidence(self) -> None:
        lower = candidate(
            batting_order=6,
            matchup_rating=-0.10,
            contact_quality_shadow=contact_shadow(
                season_xba=0.250,
                xba_last_10_games=0.260,
                expected_at_bats=3.5,
            ),
        )
        higher = candidate(
            batting_order=1,
            matchup_rating=0.20,
            contact_quality_shadow=contact_shadow(
                season_xba=0.310,
                xba_last_10_games=0.340,
                expected_at_bats=4.4,
            ),
        )

        self.assertGreater(
            estimate_hot_hit_confidence(higher).hit_probability,
            estimate_hot_hit_confidence(lower).hit_probability,
        )

    def test_hit_game_streak_does_not_directly_change_confidence(self) -> None:
        shadow = contact_shadow()
        five_of_five = candidate(hit_games_last_5=5, contact_quality_shadow=shadow)
        three_of_five = candidate(
            hit_games_last_5=3,
            current_gate_qualified=False,
            current_display_qualified=False,
            gate_failures=["L5_HIT_GAMES_BELOW_CURRENT_GATE"],
            contact_quality_shadow=shadow,
        )

        first = estimate_hot_hit_confidence(five_of_five)
        second = estimate_hot_hit_confidence(three_of_five)

        self.assertEqual(first.hit_probability, second.hit_probability)
        self.assertIn("CURRENT_GATE_FAIL", second.flags)

    def test_renderer_identifies_research_only_profiles(self) -> None:
        item = candidate(
            current_gate_qualified=False,
            current_display_qualified=False,
            gate_failures=["L5_HIT_GAMES_BELOW_CURRENT_GATE"],
            contact_quality_shadow=contact_shadow(),
        )
        item.confidence_estimate = estimate_hot_hit_confidence(item)

        rendered = render_hot_hit_confidence_research([item])

        self.assertIn("Hot Hits Confidence Research", rendered)
        self.assertIn("Research", rendered)
        self.assertIn("L5_HIT_GAMES_BELOW_CURRENT_GATE", rendered)
        self.assertIn("shadow only", rendered)

    def test_confidence_attachment_does_not_change_discord_content(self) -> None:
        item = candidate(contact_quality_shadow=contact_shadow())
        before = render_hot_hits_discord_embeds([item], "2026-08-04", 1, 18)

        metadata = attach_hot_hit_confidence([item])
        after = render_hot_hits_discord_embeds([item], "2026-08-04", 1, 18)

        self.assertEqual(before, after)
        self.assertEqual(metadata["calibration_status"], "PROVISIONAL")
        self.assertFalse(metadata["price_included"])


if __name__ == "__main__":
    unittest.main()
