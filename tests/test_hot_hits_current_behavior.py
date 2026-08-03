from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from hot_hits_report import MlbStatsClient, current_hot_hit_score
from mlb_props.config import load_settings
from mlb_props.hot_hits_policy import select_core_first_hot_hits_card
from mlb_props.models import HotHitCandidate
from mlb_props.output import (
    _hot_hit_discord_eligible,
    _hot_hit_discord_sort_key,
    _hot_hit_support_count,
    _hot_hit_tier,
    render_hot_hits_discord_embeds,
)


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
        avg_last_10=0.320,
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


class CurrentHotHitsScoreTests(unittest.TestCase):
    def test_current_report_score_rewards_all_documented_positive_inputs(self) -> None:
        row = {
            "avg_last_5": 0.450,
            "avg_last_10": 0.330,
            "season_avg": 0.300,
            "hit_games_last_5": 5,
            "hit_games_last_10": 8,
            "batting_order": 1,
            "matchup_rating": 0.25,
            "pitcher_hits_allowed_rate_last_5": 0.290,
            "pitcher_k_rate_last_5": 0.190,
            "pitcher_walk_rate_last_5": 0.050,
            "batter_vs_pitcher_ab": 10,
            "batter_vs_pitcher_avg": 0.350,
        }

        self.assertEqual(current_hot_hit_score(row), 19)

    def test_current_report_score_applies_documented_risk_penalties(self) -> None:
        row = {
            "avg_last_5": 0.350,
            "avg_last_10": 0.300,
            "season_avg": 0.360,
            "hit_games_last_5": 4,
            "hit_games_last_10": 7,
            "batting_order": 8,
            "matchup_rating": -0.20,
            "pitcher_hits_allowed_rate_last_5": 0.210,
            "pitcher_k_rate_last_5": 0.300,
            "pitcher_walk_rate_last_5": 0.100,
            "batter_vs_pitcher_ab": 10,
            "batter_vs_pitcher_avg": 0.100,
        }

        self.assertEqual(current_hot_hit_score(row), -3)


class CurrentHotHitsDiscordPolicyTests(unittest.TestCase):
    def test_support_count_uses_the_five_current_boundary_signals(self) -> None:
        item = candidate()

        self.assertEqual(_hot_hit_support_count(item), 5)

    def test_eligibility_allows_supported_profiles_but_blocks_low_order(self) -> None:
        high_score_one_support = candidate(
            score=14,
            batting_order=5,
            matchup_rating=0.00,
            pitcher_hits_allowed_rate_last_5=0.200,
            season_avg=0.280,
            avg_last_5=0.350,
        )
        score_twelve_two_supports = candidate(
            score=12,
            batting_order=5,
            matchup_rating=0.20,
            pitcher_hits_allowed_rate_last_5=0.200,
            season_avg=0.250,
            avg_last_5=0.380,
        )
        score_thirteen_one_support = replace(high_score_one_support, score=13)
        low_order = candidate(score=20, batting_order=8)

        self.assertTrue(_hot_hit_discord_eligible(high_score_one_support, min_score=10))
        self.assertTrue(_hot_hit_discord_eligible(score_twelve_two_supports, min_score=10))
        self.assertFalse(_hot_hit_discord_eligible(score_thirteen_one_support, min_score=10))
        self.assertFalse(_hot_hit_discord_eligible(low_order, min_score=10))

    def test_current_tier_boundaries_remain_core_value_and_thin(self) -> None:
        core = candidate(score=14, batting_order=4)
        value = candidate(score=12, batting_order=5)
        thin = candidate(
            score=14,
            batting_order=5,
            matchup_rating=0.00,
            pitcher_hits_allowed_rate_last_5=0.200,
            season_avg=0.250,
            avg_last_5=0.350,
        )

        self.assertEqual(_hot_hit_tier(core), "Core")
        self.assertEqual(_hot_hit_tier(value), "Value")
        self.assertEqual(_hot_hit_tier(thin), "Thin")

    def test_current_sort_prioritizes_support_count_before_score(self) -> None:
        more_support = candidate(score=14)
        higher_score = candidate(
            score=18,
            matchup_rating=0.00,
            pitcher_hits_allowed_rate_last_5=0.200,
            season_avg=0.250,
            avg_last_5=0.350,
        )

        ordered = sorted(
            [higher_score, more_support],
            key=_hot_hit_discord_sort_key,
            reverse=True,
        )

        self.assertEqual(ordered, [more_support, higher_score])

    def test_renderer_separates_recommended_core_from_optional_value(self) -> None:
        core = candidate(batter_name="Core Hitter", score=14)
        value = candidate(batter_name="Value Hitter", score=12, batting_order=5)
        thin = candidate(
            batter_name="Thin Hitter",
            score=14,
            batting_order=5,
            matchup_rating=0.00,
            pitcher_hits_allowed_rate_last_5=0.200,
            season_avg=0.250,
            avg_last_5=0.350,
        )

        embeds = render_hot_hits_discord_embeds(
            candidates=[thin, value, core],
            screen_date="2026-07-28",
            games_count=3,
            checked_count=54,
            limit=2,
            min_score=10,
        )

        field_names = [field["name"] for field in embeds[0]["fields"]]
        self.assertEqual(
            field_names,
            [
                "Core Card 1 | Core Hitter (DET) - Score 14",
                "⚠️ Optional Value — Higher Risk",
                "Optional Value | Value Hitter (DET) - Score 12",
            ],
        )
        self.assertNotIn("Thin Hitter", " ".join(field_names))
        self.assertIn("Play at your own risk", embeds[0]["fields"][1]["value"])
        self.assertIn("Recommended card: **1 Core leg**", embeds[0]["description"])

    def test_renderer_does_not_force_a_card_when_profile_gate_is_empty(self) -> None:
        ineligible = candidate(
            score=11,
            matchup_rating=0.00,
            pitcher_hits_allowed_rate_last_5=0.200,
            season_avg=0.250,
            avg_last_5=0.350,
        )

        embeds = render_hot_hits_discord_embeds(
            candidates=[ineligible],
            screen_date="2026-07-28",
            games_count=1,
            checked_count=18,
            limit=6,
            min_score=10,
        )

        self.assertEqual(embeds[0]["fields"][0]["name"], "Core Card")
        self.assertIn("Do not force", embeds[0]["fields"][0]["value"])
        self.assertEqual(embeds[0]["fields"][1]["name"], "Optional Value")

    def test_renderer_shows_no_value_when_four_core_plays_qualify(self) -> None:
        embeds = render_hot_hits_discord_embeds(
            candidates=CoreFirstHotHitsPolicyTests.core_items(4)
            + CoreFirstHotHitsPolicyTests.value_items(2),
            screen_date="2026-07-28",
            games_count=5,
            checked_count=90,
            limit=4,
            min_score=10,
        )

        field_names = [field["name"] for field in embeds[0]["fields"]]
        self.assertEqual(len(field_names), 4)
        self.assertTrue(all(name.startswith("Core Card") for name in field_names))

    def test_renderer_can_temporarily_roll_back_to_legacy_policy(self) -> None:
        core = candidate(batter_name="Core Hitter", score=14)
        value = candidate(batter_name="Value Hitter", score=12, batting_order=5)

        embeds = render_hot_hits_discord_embeds(
            candidates=[core, value],
            screen_date="2026-07-28",
            games_count=2,
            checked_count=36,
            limit=6,
            card_policy="current-v1",
        )

        field_names = [field["name"] for field in embeds[0]["fields"]]
        self.assertEqual(
            field_names,
            [
                "Core | Core Hitter (DET) - Score 14",
                "Value | Value Hitter (DET) - Score 12",
            ],
        )


class CoreFirstHotHitsPolicyTests(unittest.TestCase):
    @staticmethod
    def core_items(count: int) -> list[HotHitCandidate]:
        return [
            candidate(
                batter_name=f"Core {index}",
                score=18 - index,
                batting_order=min(index, 4),
            )
            for index in range(1, count + 1)
        ]

    @staticmethod
    def value_items(count: int) -> list[HotHitCandidate]:
        return [
            candidate(
                batter_name=f"Value {index}",
                score=13 - min(index, 1),
                batting_order=5,
            )
            for index in range(1, count + 1)
        ]

    def test_core_first_policy_uses_value_only_when_core_card_is_thin(self) -> None:
        expected_counts = {
            0: (0, 2),
            1: (1, 2),
            2: (2, 2),
            3: (3, 1),
            4: (4, 0),
            5: (4, 0),
        }

        for core_count, expected in expected_counts.items():
            with self.subTest(core_count=core_count):
                selection = select_core_first_hot_hits_card(
                    self.core_items(core_count) + self.value_items(3),
                    core_limit=4,
                    value_limit=2,
                )
                self.assertEqual(
                    (len(selection.core), len(selection.value)),
                    expected,
                )

    def test_core_first_policy_never_selects_thin(self) -> None:
        thin = candidate(
            batter_name="Thin Hitter",
            score=14,
            batting_order=5,
            matchup_rating=0.00,
            pitcher_hits_allowed_rate_last_5=0.200,
            season_avg=0.280,
            avg_last_5=0.350,
        )

        selection = select_core_first_hot_hits_card(
            [thin],
            core_limit=4,
            value_limit=2,
        )

        self.assertEqual(selection.shown, [])
        self.assertEqual(selection.thin, [])


class HotHitsConfigurationTests(unittest.TestCase):
    def test_core_first_discord_policy_is_the_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = load_settings()

        thresholds = settings.hot_hits_thresholds
        self.assertEqual(thresholds.discord_card_policy, "core-first-v1")
        self.assertEqual(thresholds.discord_core_limit, 4)
        self.assertEqual(thresholds.discord_value_limit, 2)
        self.assertTrue(thresholds.include_contact_quality_shadow)


class CurrentHotHitsGradingTests(unittest.TestCase):
    @staticmethod
    def client_with_player(batting: dict | None, *, final: bool = True) -> MlbStatsClient:
        client = object.__new__(MlbStatsClient)
        state = "Final" if final else "In Progress"
        game = {
            "gamePk": 123,
            "status": {
                "abstractGameState": "Final" if final else "Live",
                "detailedState": state,
            },
        }
        client._find_game = lambda team, screen_date: (game, "away")
        players = {}
        if batting is not None:
            players["ID101"] = {
                "person": {"fullName": "Test Hitter"},
                "stats": {"batting": batting},
            }
        client._boxscore = lambda game_pk: {
            "teams": {
                "away": {"players": players},
                "home": {"players": {}},
            }
        }
        return client

    def test_grader_distinguishes_hit_miss_and_dnp(self) -> None:
        candidate_row = {"batter_id": 101, "batter_name": "Test Hitter", "team": "DET"}
        cases = [
            ({"hits": 1, "atBats": 4, "plateAppearances": 4}, "HIT"),
            ({"hits": 0, "atBats": 4, "plateAppearances": 4}, "MISS"),
            ({"hits": 0, "atBats": 0, "plateAppearances": 0}, "DNP"),
        ]

        for batting, expected in cases:
            with self.subTest(expected=expected):
                result = self.client_with_player(batting).grade_batter(
                    candidate_row,
                    "2026-07-28",
                )
                self.assertEqual(result["result"], expected)

    def test_grader_keeps_pending_and_missing_players_out_of_hit_miss(self) -> None:
        candidate_row = {"batter_id": 101, "batter_name": "Test Hitter", "team": "DET"}

        pending = self.client_with_player(
            {"hits": 0, "atBats": 1, "plateAppearances": 1},
            final=False,
        ).grade_batter(candidate_row, "2026-07-28")
        missing = self.client_with_player(None).grade_batter(candidate_row, "2026-07-28")

        self.assertEqual(pending["result"], "PENDING")
        self.assertEqual(missing["result"], "NO_PLAYER")


if __name__ == "__main__":
    unittest.main()
