"""Verdict-mapping table: each path is a pure function of the metrics."""

from __future__ import annotations

from shrap.research.strategy_evaluator.verdict import (
    REASON_ANCHOR_NOT_LIVE,
    REASON_BELOW_SHARPE_FLOOR,
    REASON_FAILS_FRICTION_STRESS,
    REASON_INSUFFICIENT_TRADES,
    REASON_NO_EDGE,
    REASON_PROMOTE,
    VERDICT_HOLD,
    VERDICT_KILL,
    VERDICT_PROMOTE,
    map_verdict,
)

_MIN_TRADES = 150
_FLOOR = 1.0


def _v(
    *,
    anchor_fresh: bool = True,
    total_trades: int = 200,
    base_sharpe: float = 1.5,
    stress_sharpe: float = 0.5,
) -> tuple[str, str]:
    verdict = map_verdict(
        anchor_fresh=anchor_fresh,
        total_trades=total_trades,
        base_sharpe=base_sharpe,
        stress_sharpe=stress_sharpe,
        min_trades=_MIN_TRADES,
        sharpe_floor=_FLOOR,
    )
    return verdict.verdict, verdict.reason


def test_promote_when_all_conditions_hold() -> None:
    assert _v() == (VERDICT_PROMOTE, REASON_PROMOTE)


def test_dead_anchor_kills_regardless_of_metrics() -> None:
    # Great metrics, dead anchor -> kill wins (highest priority).
    assert _v(anchor_fresh=False, base_sharpe=5.0, stress_sharpe=4.0) == (
        VERDICT_KILL,
        REASON_ANCHOR_NOT_LIVE,
    )


def test_trade_count_gate_kills_even_with_great_sharpe() -> None:
    assert _v(total_trades=149, base_sharpe=4.0, stress_sharpe=3.0) == (
        VERDICT_KILL,
        REASON_INSUFFICIENT_TRADES,
    )


def test_no_edge_is_killed() -> None:
    assert _v(base_sharpe=0.0)[1] == REASON_NO_EDGE
    assert _v(base_sharpe=-0.3) == (VERDICT_KILL, REASON_NO_EDGE)


def test_edge_that_dies_under_friction_is_killed() -> None:
    assert _v(base_sharpe=2.0, stress_sharpe=0.0) == (
        VERDICT_KILL,
        REASON_FAILS_FRICTION_STRESS,
    )
    assert _v(base_sharpe=2.0, stress_sharpe=-0.1)[0] == VERDICT_KILL


def test_positive_but_sub_floor_edge_holds_for_data() -> None:
    assert _v(base_sharpe=0.5, stress_sharpe=0.2) == (
        VERDICT_HOLD,
        REASON_BELOW_SHARPE_FLOOR,
    )


def test_floor_is_inclusive() -> None:
    # Exactly at the floor promotes; a hair below holds.
    assert _v(base_sharpe=_FLOOR, stress_sharpe=0.1)[0] == VERDICT_PROMOTE
    assert _v(base_sharpe=_FLOOR - 1e-9, stress_sharpe=0.1)[0] == VERDICT_HOLD
