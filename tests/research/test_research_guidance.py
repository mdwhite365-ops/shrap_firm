"""The corpus informs what to propose next — and nothing else.

Mike, 2026-07-30, on making the firm do its own research. The gap this closes:
the ledger reads results and nothing acts on them, so a proposer would keep
proposing factor strategies after four of six lost to buy-and-hold.

**The boundary is the design.** This informs what to TRY. It must never inform
what counts as SUCCESS. A system that can move its own gate will move it until
something passes — p-hacking arrived at honestly. Tests below pin that.
"""

from __future__ import annotations

from typing import Any

from shrap.research.guidance import (
    MIN_FINDINGS_FOR_EXHAUSTED,
    SIGNAL_PRICE,
    SIGNAL_VOLUME,
    Observation,
    derive,
    shape_of,
)

_LAUNCH = [f"T{i}" for i in range(50)]


def _row(
    sid: str,
    *,
    rule: str = "cross-sectional-factor",
    factor: str | None = None,
    lookback: int = 252,
    ir: float | None = -0.2,
    tested: bool = True,
    tickers: list[str] | None = None,
    name: str = "",
) -> dict[str, Any]:
    params: dict[str, Any] = {"lookback": lookback}
    if factor:
        params["factor"] = factor
    return {
        "strategy_id": sid,
        "name": name or sid,
        "spec": {"rule": rule, "params": params},
        "tickers": {"long": tickers if tickers is not None else _LAUNCH, "short": []},
        "information_ratio": ir,
        "tested": tested,
    }


# --- the boundary -------------------------------------------------------------


def test_no_observation_names_a_gate_or_a_threshold() -> None:
    """The hard rule. If guidance ever suggests moving a floor, the firm has
    acquired the ability to define its own success."""

    rows = [_row(f"S{i}", factor=f, ir=-0.3) for i, f in enumerate("abcdef")]

    text = derive(rows).render().lower()

    for forbidden in ("floor", "threshold", "lower the", "raise the bar", "promote if"):
        assert forbidden not in text


def test_the_render_states_the_boundary_out_loud() -> None:
    rows = [_row("S1", ir=0.4)]

    assert "never what counts as success" in derive(rows).render()


# --- reading shape from the spec ---------------------------------------------


def test_shape_comes_from_the_spec_not_the_name() -> None:
    """A strategy called 'momentum' that specs a reversal rule is a reversal
    strategy. Reading names would let a mislabelled seed corrupt the guidance."""

    shape = shape_of(_row("S1", rule="cross-sectional-reversal", lookback=5, name="Momentum!"))

    assert shape.rule == "cross-sectional-reversal"
    assert shape.horizon == 5


def test_a_volume_factor_is_classified_as_a_volume_signal() -> None:
    assert shape_of(_row("S1", factor="volume-shock")).signal == SIGNAL_VOLUME
    assert shape_of(_row("S2", factor="low-volatility")).signal == SIGNAL_PRICE


def test_beating_the_benchmark_is_a_positive_information_ratio() -> None:
    assert shape_of(_row("S1", ir=0.4)).beat_benchmark
    assert not shape_of(_row("S2", ir=-0.001)).beat_benchmark
    assert not shape_of(_row("S3", ir=None)).beat_benchmark


# --- exhausted ----------------------------------------------------------------


def test_a_rule_family_where_everything_lost_is_reported_as_exhausted() -> None:
    rows = [_row(f"S{i}", factor=f, ir=-0.2) for i, f in enumerate("abcd")]

    text = derive(rows).render()

    assert "every `cross-sectional-factor` strategy lost" in text


def test_a_family_with_one_winner_is_not_exhausted() -> None:
    """One survivor means the family is not dead, however many died around it."""

    rows = [_row(f"S{i}", ir=-0.2) for i in range(4)] + [_row("W", ir=0.39)]

    assert "every `cross-sectional-factor` strategy lost" not in derive(rows).render()


def test_the_firms_actual_corpus_says_suspect_the_universe() -> None:
    """Twelve strategies, six documented effects, four of six losing to
    buy-and-hold. If NOTHING beats the benchmark, the rules are not the
    interesting variable."""

    rows = [_row(f"S{i}", ir=-0.2) for i in range(6)]

    text = derive(rows).render()

    assert "suspect the universe or the benchmark before the rules" in text


def test_that_claim_is_withheld_once_something_wins() -> None:
    rows = [_row(f"S{i}", ir=-0.2) for i in range(5)] + [_row("W", ir=0.39)]

    assert "suspect the universe" not in derive(rows).render()


# --- untried ------------------------------------------------------------------


def test_a_price_only_corpus_is_flagged() -> None:
    """Every effect the firm has tested is a function of closes. A correlated
    failure across them is indistinguishable from a defect in how prices are
    read — which is a reason to vary the INPUT, not the rule."""

    rows = [_row(f"S{i}", ir=-0.2) for i in range(4)]

    assert "every strategy reads price alone" in derive(rows).render()


def test_adding_a_volume_strategy_clears_that_flag() -> None:
    rows = [_row(f"S{i}", ir=-0.2) for i in range(3)]
    rows.append(_row("V", factor="volume-shock", ir=0.18))

    assert "every strategy reads price alone" not in derive(rows).render()


def test_one_universe_throughout_is_flagged() -> None:
    rows = [_row(f"S{i}", ir=-0.2) for i in range(4)]

    assert "one universe has been used throughout" in derive(rows).render()


def test_two_universes_clear_it() -> None:
    rows = [_row(f"S{i}", ir=-0.2) for i in range(3)]
    rows.append(_row("N", ir=-0.2, tickers=["A", "B", "C"]))

    assert "one universe has been used throughout" not in derive(rows).render()


def test_a_daily_only_corpus_names_the_missing_intraday_data() -> None:
    """The conclusion the probe strategies reached in 2026-07: the fast layer
    needs intraday bars, not different parameters."""

    rows = [_row(f"S{i}", lookback=252, ir=-0.2) for i in range(4)]

    assert "nothing shorter than a week" in derive(rows).render()


# --- warnings -----------------------------------------------------------------


def test_survivors_are_flagged_for_an_unchecked_fold_overlap() -> None:
    """The question the whole factor family was seeded to answer: if the
    survivors win in the SAME folds, the firm holds one effect wearing two
    names."""

    rows = [_row(f"S{i}", ir=-0.2) for i in range(4)]
    rows.append(_row("W1", ir=0.39, name="momentum"))
    rows.append(_row("W2", ir=0.18, name="volume shock"))

    text = derive(rows).render()

    assert "win in the SAME folds" in text
    assert "momentum" in text and "volume shock" in text


def test_shared_universe_is_flagged_as_non_independent_evidence() -> None:
    rows = [_row(f"S{i}", ir=-0.2) for i in range(4)]

    assert "failures are not independent evidence" in derive(rows).render()


# --- evidence strength --------------------------------------------------------


def test_a_thin_observation_is_marked_thin() -> None:
    """Guidance stated confidently on two data points would narrow the search on
    noise, which is worse than no guidance."""

    assert Observation("x", findings=1).is_thin
    assert "~" in Observation("x", findings=1).render()
    assert not Observation("x", findings=MIN_FINDINGS_FOR_EXHAUSTED).is_thin


def test_untested_strategies_contribute_nothing() -> None:
    """A queued strategy has varied nothing yet. Counting it would let seeding
    change the guidance before any evidence existed."""

    rows = [_row(f"S{i}", ir=None, tested=False) for i in range(10)]

    guidance = derive(rows)

    assert guidance.tested == 0
    assert "supports no guidance" in guidance.render()


def test_an_empty_corpus_claims_nothing() -> None:
    assert "supports no guidance" in derive([]).render()
