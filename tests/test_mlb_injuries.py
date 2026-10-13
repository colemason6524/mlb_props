"""Tests for the MLB injury pipeline (sources/espn_injuries + injuries)."""
from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

from mlb_props.injuries import (
    OUT_STATUSES,
    apply_sp_overrides,
    build_adjustments,
    injury_notes_for_game,
    is_key_hitter,
    offense_debit_runs,
    offense_debits,
    scratched_sp_norms,
)
from mlb_props.models import TeamInjury
from mlb_props.utils import normalize_name


def _injury(name, team, status, position="1B"):
    return TeamInjury(
        player_name=name,
        player_name_norm=normalize_name(name),
        team=team,
        status=status,
        position=position,
    )


def _game(pk="999", away="CLE", home="TB", away_sp="Ace Scratched", home_sp="Freddy Peralta"):
    def side(abbr, sp):
        return {
            "team": {"id": 114 if abbr == "CLE" else 139, "abbreviation": abbr},
            "probablePitcher": {"id": 1, "fullName": sp} if sp else {},
        }

    return {
        "gamePk": pk,
        "gameDate": "2026-10-13T00:00:00Z",
        "status": {"abstractGameState": "Preview"},
        "teams": {"away": side(away, away_sp), "home": side(home, home_sp)},
    }


class FakeInjurySource:
    def __init__(self, by_team):
        self.by_team = by_team

    def fetch_team_injuries(self, team, screen_date, force_refresh=False):
        return list(self.by_team.get(team, []))


class DebitMathTests(unittest.TestCase):
    def test_star_hitter_debit(self) -> None:
        # .900 OPS full-timer: (0.900-0.650)*4.3*0.23 = 0.247
        self.assertAlmostEqual(offense_debit_runs(0.900, 600), 0.247, places=3)

    def test_replacement_level_no_debit(self) -> None:
        self.assertEqual(offense_debit_runs(0.650, 600), 0.0)
        self.assertEqual(offense_debit_runs(0.600, 600), 0.0)

    def test_small_sample_no_debit(self) -> None:
        self.assertEqual(offense_debit_runs(1.100, 150), 0.0)

    def test_debit_capped(self) -> None:
        # (1.300-0.650)*4.3*0.23 = 0.643 -> capped at 0.60
        self.assertEqual(offense_debit_runs(1.300, 650), 0.60)

    def test_missing_stats_no_debit(self) -> None:
        self.assertEqual(offense_debit_runs(None, 600), 0.0)
        self.assertEqual(offense_debit_runs(0.900, None), 0.0)

    def test_key_hitter_threshold(self) -> None:
        self.assertTrue(is_key_hitter(0.780, 200))
        self.assertTrue(is_key_hitter(0.950, 650))
        self.assertFalse(is_key_hitter(0.779, 600))
        self.assertFalse(is_key_hitter(0.950, 199))
        self.assertFalse(is_key_hitter(None, 600))

    def test_out_statuses_cover_il_variants(self) -> None:
        for status in ("Out", "10-Day-IL", "15-Day-IL", "60-Day-IL", "7-Day-IL"):
            self.assertIn(status.lower(), OUT_STATUSES)
        self.assertNotIn("day-to-day", OUT_STATUSES)


class BuildAdjustmentsTests(unittest.TestCase):
    def _build(self, game, injuries, espn_probables, ops_map=None):
        ops_map = ops_map or {}
        with mock.patch(
            "mlb_props.injuries.fetch_espn_probables", return_value=espn_probables
        ), mock.patch(
            "mlb_props.injuries.fetch_json", return_value={"events": []}
        ), mock.patch(
            "mlb_props.injuries.resolve_mlbam_id", side_effect=lambda name, cache: 1000 + len(name)
        ), mock.patch(
            "mlb_props.injuries.season_hitting",
            side_effect=lambda pid, season, cache: ops_map.get(pid, (None, None)),
        ):
            return build_adjustments(
                "2026-10-13", [game], mock.Mock(), mock.Mock(),
                FakeInjurySource(injuries),
            )

    def test_sp_scratch_overridden_from_espn(self) -> None:
        game = _game()
        injuries = {"CLE": [_injury("Ace Scratched", "CLE", "15-Day-IL", "SP")]}
        espn = {("CLE", "TB"): {"away": "Bullpen Opener", "home": "Freddy Peralta"}}
        adj = self._build(game, injuries, espn)
        entry = adj["games"]["999"]
        self.assertEqual(entry["sp_status_away"], "overridden")
        self.assertEqual(entry["sp_override_away"]["name"], "Bullpen Opener")
        self.assertIn(normalize_name("Ace Scratched"), entry["scratched_sps"])
        self.assertTrue(any("SP change" in n for n in entry["notes"]))

        changed = apply_sp_overrides([game], adj)
        self.assertEqual(changed, 1)
        self.assertEqual(
            game["teams"]["away"]["probablePitcher"]["fullName"], "Bullpen Opener"
        )

    def test_sp_scratch_unknown_replacement_blanks_starter(self) -> None:
        game = _game()
        injuries = {"CLE": [_injury("Ace Scratched", "CLE", "Out", "SP")]}
        espn = {("CLE", "TB"): {"away": "", "home": "Freddy Peralta"}}
        adj = self._build(game, injuries, espn)
        entry = adj["games"]["999"]
        self.assertEqual(entry["sp_status_away"], "scratched_unknown_replacement")
        apply_sp_overrides([game], adj)
        # Blanked: the engine falls back to league-average SP features rather
        # than projecting with the scratched arm's stats.
        self.assertEqual(game["teams"]["away"]["probablePitcher"], {})

    def test_agreeing_probables_confirm_no_override(self) -> None:
        game = _game()
        espn = {("CLE", "TB"): {"away": "Ace Scratched", "home": "Freddy Peralta"}}
        adj = self._build(game, {}, espn)
        entry = adj["games"]["999"]
        self.assertEqual(entry["sp_status_away"], "confirmed")
        self.assertIsNone(entry["sp_override_away"])
        self.assertEqual(apply_sp_overrides([game], adj), 0)
        self.assertEqual(
            game["teams"]["away"]["probablePitcher"]["fullName"], "Ace Scratched"
        )

    def test_key_hitter_out_debits_offense(self) -> None:
        game = _game()
        injuries = {"CLE": [_injury("Star Slugger", "CLE", "10-Day-IL", "DH")]}
        pid = 1000 + len("Star Slugger")
        espn = {("CLE", "TB"): {"away": "Ace Scratched", "home": "Freddy Peralta"}}
        adj = self._build(game, injuries, espn, ops_map={pid: (0.950, 650)})
        away_debit, home_debit = offense_debits(adj, "999")
        # (0.950-0.650)*4.3*0.23 = 0.297
        self.assertAlmostEqual(away_debit, 0.297, places=3)
        self.assertEqual(home_debit, 0.0)
        entry = adj["games"]["999"]
        self.assertTrue(any("Key hitter OUT" in n and "Star Slugger" in n for n in entry["notes"]))
        slugger = next(i for i in adj["injuries"] if i["player_name"] == "Star Slugger")
        self.assertEqual(slugger["impact_level"], "key_hitter")

    def test_day_to_day_hitter_ignored(self) -> None:
        game = _game()
        injuries = {"CLE": [_injury("Star Slugger", "CLE", "Day-To-Day", "DH")]}
        espn = {("CLE", "TB"): {"away": "Ace Scratched", "home": "Freddy Peralta"}}
        adj = self._build(game, injuries, espn)
        away_debit, _ = offense_debits(adj, "999")
        self.assertEqual(away_debit, 0.0)

    def test_bullpen_arm_noted_never_debited(self) -> None:
        game = _game()
        injuries = {"TB": [_injury("Setup Man", "TB", "15-Day-IL", "RP")]}
        espn = {("CLE", "TB"): {"away": "Ace Scratched", "home": "Freddy Peralta"}}
        adj = self._build(game, injuries, espn)
        _, home_debit = offense_debits(adj, "999")
        self.assertEqual(home_debit, 0.0)
        entry = adj["games"]["999"]
        self.assertTrue(any("Bullpen arm OUT" in n for n in entry["notes"]))
        arm = next(i for i in adj["injuries"] if i["player_name"] == "Setup Man")
        self.assertEqual(arm["impact_level"], "minor")

    def test_scratched_sp_norms(self) -> None:
        game = _game()
        injuries = {"CLE": [_injury("Ace Scratched", "CLE", "Out", "SP")]}
        espn = {("CLE", "TB"): {"away": "Bullpen Opener", "home": "Freddy Peralta"}}
        adj = self._build(game, injuries, espn)
        self.assertEqual(scratched_sp_norms(adj), {normalize_name("Ace Scratched")})

    def test_injury_notes_for_unknown_game(self) -> None:
        self.assertEqual(injury_notes_for_game({}, "nope"), [])
        self.assertEqual(offense_debits({}, "nope"), (0.0, 0.0))


class ScratchedPitcherPropFilterTests(unittest.TestCase):
    def _export(self, subject):
        return {
            "candidates": [
                {
                    "prop_type": "PITCHER_STRIKEOUTS",
                    "subject_name": subject,
                    "line": 5.5,
                    "projected_strikeouts": 5.8,
                    "price_shadow": {"over_price": -110, "under_price": -110},
                }
            ]
        }

    def _engine(self):
        from mlb_props.calibration import IsotonicCalibrator, LogisticModel
        from mlb_props.forecasting.pitcher_k import PitcherEngineResult

        logit = LogisticModel()
        logit.weights = [0.0] * 19
        logit.bias = 0.0
        logit.n_features = 19
        logit._means = [0.0] * 19
        logit._stds = [1.0] * 19
        return PitcherEngineResult(
            logit=logit, isotonic=IsotonicCalibrator(), factor=1.0, dispersion=6.0, n_rows=1
        )

    def test_scratched_sp_props_excluded(self) -> None:
        from run_forecast_board import pitcher_rows

        export = self._export("Ace Scratched")
        stats: dict[str, int] = {}
        rows = pitcher_rows(
            export, self._engine(), {normalize_name("Ace Scratched")}, stats
        )
        self.assertEqual(rows, [])
        self.assertEqual(stats.get("scratched_sp"), 1)

    def test_healthy_sp_props_kept(self) -> None:
        from run_forecast_board import pitcher_rows

        rows = pitcher_rows(
            self._export("Freddy Peralta"), self._engine(), {normalize_name("Ace Scratched")}, {}
        )
        self.assertEqual(len(rows), 1)

    def test_no_scratch_set_keeps_everything(self) -> None:
        from run_forecast_board import pitcher_rows

        rows = pitcher_rows(self._export("Freddy Peralta"), self._engine())
        self.assertEqual(len(rows), 1)


class ForecastChangesWithInjuryTests(unittest.TestCase):
    """The model output must actually move when a key player is out."""

    def _forecast(self, home_offense, away_offense):
        from mlb_props.forecasting.game_runs import (
            GameEngineFit,
            GameFeatures,
            TeamGameFeatures,
            forecast_game,
        )

        fit = GameEngineFit(
            weights=[0.5] + [0.0] * 9,
            means=[0.0] * 10,
            stds=[1.0] * 10,
            resid_scale=1.4,
            ml_isotonic=None,
            n_games=100,
        )
        # Zero weights except the intercept column would give a flat
        # forecast; instead drive it through adj_offense_l30 directly.
        fit.weights = [4.5, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        features = GameFeatures(
            game_pk="1",
            screen_date="2026-10-13",
            home_side=TeamGameFeatures(
                adj_offense_l30=home_offense,
                opp_defense_l30=4.5,
                opp_sp_ra_l5=4.0,
                opp_sp_hits_l5=6.0,
                opp_sp_bb_l5=2.2,
                opp_sp_k_l5=5.5,
                opp_sp_deep_l5=0.55,
                park_factor=1.0,
                home=1,
            ),
            away_side=TeamGameFeatures(
                adj_offense_l30=away_offense,
                opp_defense_l30=4.5,
                opp_sp_ra_l5=4.0,
                opp_sp_hits_l5=6.0,
                opp_sp_bb_l5=2.2,
                opp_sp_k_l5=5.5,
                opp_sp_deep_l5=0.55,
                park_factor=1.0,
                home=0,
            ),
        )
        return forecast_game(fit, features)

    def test_hitter_debit_moves_projected_runs(self) -> None:
        base = self._forecast(4.6, 4.4)
        debited = self._forecast(4.6 - 0.297, 4.4)
        self.assertLess(debited.mean_home_runs, base.mean_home_runs)
        self.assertAlmostEqual(
            base.mean_home_runs - debited.mean_home_runs, 0.297, places=2
        )
        # Away side untouched.
        self.assertAlmostEqual(debited.mean_away_runs, base.mean_away_runs, places=6)

    def test_sp_override_changes_opponent_sp_features(self) -> None:
        from mlb_props.forecasting.game_runs import TeamGameFeatures

        # A scratched ace (2.80 RA/5) replaced by a league-average arm
        # moves the opponent's projected runs against him.
        ace = TeamGameFeatures(
            adj_offense_l30=4.5, opp_defense_l30=4.5, opp_sp_ra_l5=2.8,
            opp_sp_hits_l5=5.0, opp_sp_bb_l5=1.8, opp_sp_k_l5=7.0,
            opp_sp_deep_l5=0.8, park_factor=1.0, home=1,
        )
        replacement = TeamGameFeatures(
            adj_offense_l30=4.5, opp_defense_l30=4.5, opp_sp_ra_l5=4.0,
            opp_sp_hits_l5=6.0, opp_sp_bb_l5=2.2, opp_sp_k_l5=5.5,
            opp_sp_deep_l5=0.55, park_factor=1.0, home=1,
        )
        self.assertNotEqual(ace.row(), replacement.row())


if __name__ == "__main__":
    unittest.main()
