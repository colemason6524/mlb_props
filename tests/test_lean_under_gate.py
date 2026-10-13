"""Selection audit 2026-10-12: Lean tier requires UNDER (pitcher OVERs 3-18)."""
from types import SimpleNamespace
from mlb_props.tiers import lean_eligible, core_eligible, LEAN_REQUIRES_UNDER


def _item(side, score=80, edge_over=2.0, edge_under=2.0):
    # side_projection_edge: UNDER -> line - projected; OVER -> projected - line
    # line=5.5: UNDER needs projected<=3.5 for edge>=2.0; OVER needs projected>=7.5
    projected = 3.5 if side == "UNDER" else 7.5
    return SimpleNamespace(
        side=side, score=score, line=5.5,
        projected_strikeouts=projected, projected_outs=0.0,
        prop_type="pitcher_strikeouts", flags=[],
    )


def test_lean_requires_under_flag_on():
    assert LEAN_REQUIRES_UNDER is True


def test_lean_blocks_over():
    assert lean_eligible(_item("OVER"), 70) is False


def test_lean_allows_under():
    assert lean_eligible(_item("UNDER"), 70) is True


def test_lean_still_gates_score_and_edge():
    assert lean_eligible(_item("UNDER", score=10), 70) is False
