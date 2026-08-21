from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from mlb_props.pitcher_grading import (
    MlbGradingClient,
    _confidence_band,
    _l5_band,
    active_vs_shadow_metrics,
    brier_and_calibration,
    disagreement_analysis,
    load_history,
    resolve_candidates,
    segment_summary,
    stability_analysis,
    strict_eligible_candidates,
)

SCHEMA_6_PAYLOAD = {
    "history_schema_version": 6,
    "model_version": "pitcher-k-situational-v1",
    "tier_policy_version": "core-lean-watch-v1",
    "confidence_model_version": "pitcher-confidence-provisional-v1",
    "display_policy_version": "provisional-confidence-rank-v1",
    "shadow_feature_version": "opportunity-shadow-v1",
    "recency_shadow_version": "recency-shadow-v1",
    "screen_date": "2026-08-05",
    "exported_at": "2026-08-05T15:35:40+00:00",
    "run_note": "scheduled full pregame run",
    "settings": {"data_mode": "live"},
    "line_coverage": {
        "games": 3,
        "prop_lines": 3,
        "floor": 3,
        "diagnostics": {
            "fanduel_expected_team_pages": 6,
            "fanduel_team_pages_loaded": 6,
            "fanduel_strikeout_lines_found": 3,
        },
    },
    "candidates": [
        {
            "subject_name": "Test Pitcher",
            "subject_id": 1001,
            "subject_role": "pitcher",
            "team": "DET",
            "opponent": "KC",
            "hand": "R",
            "prop_type": "PITCHER_STRIKEOUTS",
            "side": "OVER",
            "line": 5.5,
            "bookmaker": "fanduel",
            "hits_last_5": 3,
            "played_last_5": 5,
            "projected_outs": 18.0,
            "projected_batters_faced": 26.0,
            "projected_k_rate": 0.24,
            "projected_strikeouts": 6.2,
            "score": 8,
            "flags": ["DEPTH_PLUS"],
            "confidence_estimate": {
                "version": "pitcher-confidence-provisional-v1",
                "calibration_status": "provisional",
                "win_probability": 0.57,
                "confidence_percentage": 57,
                "label": "Solid",
                "price_included": False,
            },
            "opportunity_shadow": {
                "version": "opportunity-shadow-v1",
                "opportunity_confidence": "HIGH",
                "shadow_projected_outs": 17.0,
                "shadow_projected_batters_faced": 25.0,
                "shadow_pitch_budget": 90.0,
                "flags": ["WORKLOAD_RAMP"],
            },
            "recency_shadow": {
                "version": "recency-shadow-v1",
                "shadow_projected_outs": 18.0,
                "shadow_projected_batters_faced": 25.5,
                "shadow_projected_k_rate": 0.235,
                "shadow_projected_strikeouts": 6.0,
                "shadow_projection_edge": 0.5,
                "confidence_estimate": {
                    "version": "pitcher-confidence-provisional-v1",
                    "calibration_status": "provisional",
                    "win_probability": 0.55,
                    "confidence_percentage": 55,
                    "label": "Cautious",
                    "price_included": False,
                },
            },
        },
        {
            "subject_name": "Other Pitcher",
            "subject_id": 1002,
            "subject_role": "pitcher",
            "team": "TB",
            "opponent": "TOR",
            "hand": "R",
            "prop_type": "PITCHER_STRIKEOUTS",
            "side": "UNDER",
            "line": 4.5,
            "bookmaker": "fanduel",
            "hits_last_5": 5,
            "played_last_5": 5,
            "projected_outs": 15.0,
            "projected_batters_faced": 21.0,
            "projected_k_rate": 0.19,
            "projected_strikeouts": 4.0,
            "score": 2,
            "flags": [],
            "confidence_estimate": {
                "version": "pitcher-confidence-provisional-v1",
                "win_probability": 0.52,
                "confidence_percentage": 52,
                "label": "Higher Risk",
                "price_included": False,
            },
            "opportunity_shadow": {
                "version": "opportunity-shadow-v1",
                "opportunity_confidence": "LOW",
                "shadow_projected_outs": 14.0,
                "shadow_projected_batters_faced": 20.0,
                "shadow_pitch_budget": 80.0,
                "flags": [],
            },
            "recency_shadow": {
                "version": "recency-shadow-v1",
                "shadow_projected_outs": 15.0,
                "shadow_projected_batters_faced": 20.5,
                "shadow_projected_k_rate": 0.2,
                "shadow_projected_strikeouts": 4.1,
                "shadow_projection_edge": 0.4,
                "confidence_estimate": {
                    "version": "pitcher-confidence-provisional-v1",
                    "win_probability": 0.53,
                    "confidence_percentage": 53,
                    "label": "Higher Risk",
                    "price_included": False,
                },
            },
        },
    ],
    "displayed_candidates": [
        {
            "subject_name": "Test Pitcher",
            "subject_id": 1001,
            "subject_role": "pitcher",
            "team": "DET",
            "opponent": "KC",
            "hand": "R",
            "prop_type": "PITCHER_STRIKEOUTS",
            "side": "OVER",
            "line": 5.5,
            "bookmaker": "fanduel",
            "score": 8,
        }
    ],
    "lean_candidates": [],
    "watch_candidates": [
        {
            "subject_name": "Other Pitcher",
            "subject_id": 1002,
            "subject_role": "pitcher",
            "team": "TB",
            "opponent": "TOR",
            "hand": "R",
            "prop_type": "PITCHER_STRIKEOUTS",
            "side": "UNDER",
            "line": 4.5,
            "bookmaker": "fanduel",
            "score": 2,
        }
    ],
}


class FakeClient:
    def __init__(self, schedules=None, logs=None) -> None:
        self.schedules = schedules or {}
        self.logs = logs or {}

    def fetch_schedule(self, screen_date: date) -> list[dict]:
        return self.schedules.get(screen_date.isoformat(), [])

    def fetch_pitcher_logs(self, pitcher_id: int, season: int) -> list[dict]:
        return self.logs.get(pitcher_id, [])


def _write_history(tmp: Path, payload: dict | None = None) -> Path:
    payload = payload or SCHEMA_6_PAYLOAD
    path = tmp / "pitcher_props_20260805T153540Z.json"
    path.write_text(json.dumps(payload))
    return path


class LoadHistoryTests(unittest.TestCase):
    def test_load_filters_schema_and_assigns_tiers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_history(tmp)
            payload_other = json.loads(json.dumps(SCHEMA_6_PAYLOAD))
            payload_other["history_schema_version"] = 3
            payload_other["screen_date"] = "2026-08-01"
            (tmp / "pitcher_props_20260801T153609Z.json").write_text(
                json.dumps(payload_other)
            )
            history = load_history(tmp, 6)
            self.assertEqual(len(history.entries), 1)
            self.assertEqual(len(history.candidates), 2)
            tiers = {row.pitcher_name: row.tier for row in history.candidates}
            self.assertEqual(tiers["Test Pitcher"], "core")
            self.assertEqual(tiers["Other Pitcher"], "watch")

    def test_recency_shadow_population_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_history(tmp)
            history = load_history(tmp, 6)
            entry = history.entries[0]
            self.assertEqual(entry.candidate_count, 2)
            self.assertEqual(entry.shadow_count, 2)


class ResolutionTests(unittest.TestCase):
    def test_resolves_strict_start_with_opponent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_history(tmp)
            history = load_history(tmp, 6)
            logs = {
                1001: [
                    {
                        "game_date": "2026-08-05",
                        "game_pk": 824158,
                        "team": "DET",
                        "opponent": "KC",
                        "did_start": True,
                        "strikeouts": 7,
                        "outs": 18,
                        "pitches": 95,
                        "batters_faced": 26,
                        "walks": 2,
                        "hits": 4,
                        "er": 1,
                    }
                ],
                1002: [],
            }
            client = FakeClient(logs=logs)
            resolve_candidates(history.candidates, client, today=date(2026, 8, 6))
            first = next(row for row in history.candidates if row.pitcher_name == "Test Pitcher")
            self.assertEqual(first.outcome, "win")
            self.assertEqual(first.actual, 7.0)
            self.assertEqual(first.game_pk, 824158)
            self.assertEqual(first.resolution_method, "pitcher_gamelog")

    def test_pending_when_screen_date_not_final(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_history(tmp)
            history = load_history(tmp, 6)
            client = FakeClient(logs={})
            resolve_candidates(history.candidates, client, today=date(2026, 8, 5))
            for row in history.candidates:
                self.assertEqual(row.outcome, "pending")

    def test_void_on_no_appearance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_history(tmp)
            history = load_history(tmp, 6)
            logs = {
                1001: [
                    {
                        "game_date": "2026-08-04",
                        "game_pk": 1,
                        "team": "DET",
                        "opponent": "CLE",
                        "did_start": True,
                        "strikeouts": 5,
                        "outs": 18,
                        "pitches": 90,
                        "batters_faced": 25,
                        "walks": 0,
                        "hits": 3,
                        "er": 0,
                    }
                ]
            }
            client = FakeClient(logs=logs)
            resolve_candidates(history.candidates, client, today=date(2026, 8, 6))
            first = next(row for row in history.candidates if row.pitcher_name == "Test Pitcher")
            self.assertEqual(first.outcome, "void_no_start")
            self.assertIn("no appearance", first.resolution_note)

    def test_void_on_relief_only_appearance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_history(tmp)
            history = load_history(tmp, 6)
            logs = {
                1001: [
                    {
                        "game_date": "2026-08-05",
                        "game_pk": 824158,
                        "team": "DET",
                        "opponent": "KC",
                        "did_start": False,
                        "strikeouts": 2,
                        "outs": 5,
                        "pitches": 30,
                        "batters_faced": 8,
                        "walks": 0,
                        "hits": 1,
                        "er": 0,
                    }
                ]
            }
            client = FakeClient(logs=logs)
            resolve_candidates(history.candidates, client, today=date(2026, 8, 6))
            first = next(row for row in history.candidates if row.pitcher_name == "Test Pitcher")
            self.assertEqual(first.outcome, "void_no_start")
            self.assertIn("relief-only", first.resolution_note)

    def test_doubleheader_disambiguation_by_opponent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            payload = json.loads(json.dumps(SCHEMA_6_PAYLOAD))
            payload["screen_date"] = "2026-08-17"
            payload["candidates"][0]["opponent"] = "CIN"
            (tmp / "pitcher_props_20260817T153547Z.json").write_text(
                json.dumps(payload)
            )
            history = load_history(tmp, 6)
            logs = {
                1001: [
                    {
                        "game_date": "2026-08-17",
                        "game_pk": 824514,
                        "team": "STL",
                        "opponent": "CIN",
                        "did_start": True,
                        "strikeouts": 6,
                        "outs": 21,
                        "pitches": 99,
                        "batters_faced": 28,
                        "walks": 1,
                        "hits": 5,
                        "er": 2,
                    },
                    {
                        "game_date": "2026-08-17",
                        "game_pk": 824478,
                        "team": "STL",
                        "opponent": "MIL",
                        "did_start": True,
                        "strikeouts": 3,
                        "outs": 12,
                        "pitches": 70,
                        "batters_faced": 20,
                        "walks": 1,
                        "hits": 6,
                        "er": 3,
                    },
                ]
            }
            client = FakeClient(logs=logs)
            resolve_candidates(history.candidates, client, today=date(2026, 8, 18))
            first = next(row for row in history.candidates if row.pitcher_name == "Test Pitcher")
            self.assertEqual(first.game_pk, 824514)
            self.assertEqual(first.outcome, "win")

    def test_under_and_push_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_history(tmp)
            history = load_history(tmp, 6)
            logs = {
                1002: [
                    {
                        "game_date": "2026-08-05",
                        "game_pk": 824159,
                        "team": "TB",
                        "opponent": "TOR",
                        "did_start": True,
                        "strikeouts": 4,
                        "outs": 15,
                        "pitches": 88,
                        "batters_faced": 22,
                        "walks": 1,
                        "hits": 5,
                        "er": 2,
                    }
                ]
            }
            client = FakeClient(logs=logs)
            resolve_candidates(history.candidates, client, today=date(2026, 8, 6))
            under = next(row for row in history.candidates if row.pitcher_name == "Other Pitcher")
            self.assertEqual(under.outcome, "win")


class MetricsTests(unittest.TestCase):
    def _resolved(self) -> list:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_history(tmp)
            history = load_history(tmp, 6)
            logs = {
                1001: [
                    {
                        "game_date": "2026-08-05",
                        "game_pk": 1,
                        "team": "DET",
                        "opponent": "KC",
                        "did_start": True,
                        "strikeouts": 7,
                        "outs": 18,
                        "pitches": 95,
                        "batters_faced": 26,
                        "walks": 2,
                        "hits": 4,
                        "er": 1,
                    }
                ],
                1002: [
                    {
                        "game_date": "2026-08-05",
                        "game_pk": 2,
                        "team": "TB",
                        "opponent": "TOR",
                        "did_start": True,
                        "strikeouts": 4,
                        "outs": 15,
                        "pitches": 88,
                        "batters_faced": 22,
                        "walks": 1,
                        "hits": 5,
                        "er": 2,
                    }
                ],
            }
            client = FakeClient(logs=logs)
            resolve_candidates(history.candidates, client, today=date(2026, 8, 6))
            return history.candidates

    def test_metrics_report_bias_and_mae(self) -> None:
        rows = self._resolved()
        metrics = active_vs_shadow_metrics(rows)
        self.assertEqual(metrics["k"]["n"], 2)
        self.assertIsNotNone(metrics["k"]["active"]["mae"])
        self.assertIsNotNone(metrics["k"]["shadow"]["mae"])
        self.assertIsNotNone(metrics["bf"]["active"]["mae"])

    def test_brier_and_calibration(self) -> None:
        rows = self._resolved()
        stats = brier_and_calibration(rows)
        self.assertEqual(stats["graded_n"], 2)
        self.assertIsNotNone(stats["active_brier"])
        self.assertIsNotNone(stats["shadow_brier"])
        self.assertIn("57-59%", stats["bands"])

    def test_segments_and_disagreements(self) -> None:
        rows = self._resolved()
        l5 = segment_summary(rows, lambda row: _l5_band(row))
        self.assertTrue(l5)
        disagreements = disagreement_analysis(rows)
        self.assertIn("paired_n", disagreements)

    def test_stability_uses_slate_blocks(self) -> None:
        rows = self._resolved()
        stats = stability_analysis(rows)
        self.assertEqual(stats["distinct_pitchers"], 2)
        self.assertEqual(len(stats["dates"]), 1)


class ConfidenceBandTests(unittest.TestCase):
    def test_band_boundaries(self) -> None:
        self.assertEqual(_confidence_band(60), "60%+")
        self.assertEqual(_confidence_band(57), "57-59%")
        self.assertEqual(_confidence_band(54), "54-56%")
        self.assertEqual(_confidence_band(51), "51-53%")
        self.assertEqual(_confidence_band(50), "50%")
        self.assertEqual(_confidence_band(None), "unknown")


SCHEMA_7_PAYLOAD = {
    "history_schema_version": 7,
    "model_version": "pitcher-k-situational-v1",
    "screen_date": "2026-08-05",
    "exported_at": "2026-08-05T15:35:40+00:00",
    "settings": {"data_mode": "live"},
    "slate_games": [
        {
            "game_id": "824158",
            "game_time_utc": "2026-08-05T23:10:00+00:00",
            "home_team": "DET",
            "away_team": "KC",
            "probable_home_pitcher": "Test Pitcher",
            "probable_away_pitcher": "Other Pitcher",
            "probable_home_pitcher_id": 1001,
            "probable_away_pitcher_id": 1002,
        }
    ],
    "discord_delivery": {
        "enabled": True,
        "attempted": True,
        "sent_at": "2026-08-05T15:36:00+00:00",
        "ok": True,
        "status_code": 204,
        "error": None,
        "embed_count": 2,
    },
    "candidates": [
        {
            "subject_name": "Test Pitcher",
            "subject_id": 1001,
            "subject_role": "pitcher",
            "team": "DET",
            "opponent": "KC",
            "hand": "R",
            "prop_type": "PITCHER_STRIKEOUTS",
            "side": "OVER",
            "line": 5.5,
            "bookmaker": "fanduel",
            "hits_last_5": 3,
            "played_last_5": 5,
            "projected_outs": 18.0,
            "projected_batters_faced": 26.0,
            "projected_k_rate": 0.24,
            "projected_strikeouts": 6.2,
            "score": 8,
            "flags": ["DEPTH_PLUS"],
            "event_id": "824158",
            "line_source": "fanduel_scrape",
            "line_collected_at": "2026-08-05T15:34:00+00:00",
            "confidence_estimate": {
                "version": "pitcher-confidence-provisional-v1",
                "win_probability": 0.57,
                "confidence_percentage": 57,
                "label": "Solid",
            },
            "recency_shadow": {
                "version": "recency-shadow-v1",
                "shadow_projected_strikeouts": 6.0,
                "shadow_projected_k_rate": 0.235,
                "shadow_projected_batters_faced": 25.5,
                "shadow_projected_outs": 18.0,
                "confidence_estimate": {
                    "win_probability": 0.55,
                    "confidence_percentage": 55,
                    "label": "Cautious",
                },
            },
        }
    ],
    "displayed_candidates": [
        {
            "subject_name": "Test Pitcher",
            "subject_id": 1001,
            "subject_role": "pitcher",
            "team": "DET",
            "opponent": "KC",
            "hand": "R",
            "prop_type": "PITCHER_STRIKEOUTS",
            "side": "OVER",
            "line": 5.5,
            "bookmaker": "fanduel",
            "score": 8,
        }
    ],
}


def _write_schema7(tmp: Path) -> Path:
    path = tmp / "pitcher_props_20260805T153540Z.json"
    path.write_text(json.dumps(SCHEMA_7_PAYLOAD))
    return path


class Schema7StrictGradingTests(unittest.TestCase):
    def test_payload_derives_pregame_and_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_schema7(tmp)
            history = load_history(tmp, 7)
            self.assertEqual(len(history.entries), 1)
            entry = history.entries[0]
            self.assertEqual(entry.earliest_first_pitch_utc, "2026-08-05T23:10:00+00:00")
            self.assertTrue(entry.pregame)
            self.assertTrue(entry.discord_delivered)
            self.assertEqual(entry.discord_sent_at, "2026-08-05T15:36:00+00:00")

    def test_candidate_carries_event_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_schema7(tmp)
            history = load_history(tmp, 7)
            candidate = history.candidates[0]
            self.assertEqual(candidate.event_id, "824158")
            self.assertEqual(candidate.line_source, "fanduel_scrape")
            self.assertEqual(candidate.line_collected_at, "2026-08-05T15:34:00+00:00")

    def test_strict_eligibility_requires_pregame_and_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_schema7(tmp)
            history = load_history(tmp, 7)
            eligible = strict_eligible_candidates(history)
            self.assertEqual(len(eligible), 1)

    def test_strict_eligibility_excludes_missing_delivery(self) -> None:
        payload = json.loads(json.dumps(SCHEMA_7_PAYLOAD))
        payload["discord_delivery"]["ok"] = False
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            (tmp / "pitcher_props_20260805T153540Z.json").write_text(json.dumps(payload))
            history = load_history(tmp, 7)
            eligible = strict_eligible_candidates(history)
            self.assertEqual(len(eligible), 0)

    def test_strict_eligibility_excludes_late_export(self) -> None:
        payload = json.loads(json.dumps(SCHEMA_7_PAYLOAD))
        payload["exported_at"] = "2026-08-05T23:20:00+00:00"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            (tmp / "pitcher_props_20260805T153540Z.json").write_text(json.dumps(payload))
            history = load_history(tmp, 7)
            entry = history.entries[0]
            self.assertFalse(entry.pregame)
            eligible = strict_eligible_candidates(history)
            self.assertEqual(len(eligible), 0)

    def test_schema6_entries_excluded_when_delivery_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            _write_history(tmp)
            history = load_history(tmp, 6)
            eligible = strict_eligible_candidates(history)
            self.assertEqual(len(eligible), 0)

    def test_event_id_resolves_doubleheader_exactly(self) -> None:
        payload = json.loads(json.dumps(SCHEMA_7_PAYLOAD))
        payload["screen_date"] = "2026-08-17"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            (tmp / "pitcher_props_20260817T153547Z.json").write_text(json.dumps(payload))
            history = load_history(tmp, 7)
            logs = {
                1001: [
                    {
                        "game_date": "2026-08-17",
                        "game_pk": 824514,
                        "team": "DET",
                        "opponent": "CIN",
                        "did_start": True,
                        "strikeouts": 6,
                        "outs": 21,
                        "pitches": 99,
                        "batters_faced": 28,
                        "walks": 1,
                        "hits": 5,
                        "er": 2,
                    },
                    {
                        "game_date": "2026-08-17",
                        "game_pk": 824158,
                        "team": "DET",
                        "opponent": "KC",
                        "did_start": True,
                        "strikeouts": 3,
                        "outs": 12,
                        "pitches": 70,
                        "batters_faced": 20,
                        "walks": 1,
                        "hits": 6,
                        "er": 3,
                    },
                ]
            }
            client = FakeClient(logs=logs)
            resolve_candidates(history.candidates, client, today=date(2026, 8, 18))
            candidate = history.candidates[0]
            self.assertEqual(candidate.game_pk, 824158)
            self.assertEqual(candidate.outcome, "loss")


if __name__ == "__main__":
    unittest.main()
