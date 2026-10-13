"""Tests for the game ML/RL post-hoc recalibrator (honesty layer)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run_forecast_board as board


def test_recalibrator_loads():
    r = board.load_game_recalibrator()
    assert r is not None
    assert "game_ml" in r
    assert "game_rl" in r


def test_ml_recalibration_near_identity():
    # v2 engine is honestly calibrated; the refit recalibrator should be
    # close to identity through the typical pick range, not the hard
    # shrinkage v1 needed for its broken probabilities.
    r = board.load_game_recalibrator()
    honest = board.recalibrate_pick_prob(r, "game_ml", 0.50)
    assert 0.45 < honest < 0.60, f"expected ~identity, got {honest}"
    # monotone: higher in -> higher out
    lo = board.recalibrate_pick_prob(r, "game_ml", 0.45)
    hi = board.recalibrate_pick_prob(r, "game_ml", 0.55)
    assert hi >= lo, f"expected monotone, got {lo} -> {hi}"


def test_rl_recalibration_near_identity():
    r = board.load_game_recalibrator()
    honest = board.recalibrate_pick_prob(r, "game_rl", 0.60)
    assert 0.50 < honest < 0.80, f"expected ~identity, got {honest}"
    lo = board.recalibrate_pick_prob(r, "game_rl", 0.55)
    hi = board.recalibrate_pick_prob(r, "game_rl", 0.70)
    assert hi >= lo, f"expected monotone, got {lo} -> {hi}"


def test_recalibrator_passthrough():
    # None recalibrator -> unchanged
    assert board.recalibrate_pick_prob(None, "game_ml", 0.65) == 0.65
    # unknown family -> unchanged
    r = board.load_game_recalibrator()
    assert board.recalibrate_pick_prob(r, "game_total", 0.65) == 0.65
    # None prob -> None
    assert board.recalibrate_pick_prob(r, "game_ml", None) is None


def test_artifact_versioned():
    path = Path(__file__).resolve().parent.parent / "mlb_props" / "artifacts" / "game_recalibrator_v1.json"
    payload = json.loads(path.read_text())
    assert payload["version"] == "game-recalibrator-v1"
    assert payload["fitted_through"] == "2026-09-20"
    for fam in ["game_ml", "game_rl"]:
        assert len(payload["families"][fam]["knots"]) >= 2
