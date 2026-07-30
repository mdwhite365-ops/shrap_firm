"""The ledger has to tell "we learned nothing" from "we learned something bad".

Mike, 2026-07-30: *"we are testing known and coming up with unknown strats to see
what works and doesn't and learn and adapt."*

Learning across attempts needs something that reads across them, and nothing
did. `lineage` shows one idea's history; the evaluation cards sit in a directory
nobody re-opens. So every lesson the firm has paid for lives in exactly one file
and informs nothing.

The distinction this module exists to preserve: an evaluation that died on a
dead anchor or a trade-count gate measured the **setup**, not the idea. A corpus
of five such deaths has taught the firm nothing about edge, however busy it
looked. Counting those as findings is how a research programme talks itself into
a conclusion it never tested.
"""

from __future__ import annotations

from datetime import UTC, datetime

from shrap.research.ledger import (
    LedgerRow,
    observations,
    render,
    row_from_mapping,
    summarise,
)

NOW = datetime(2026, 7, 30, tzinfo=UTC)


def _row(
    *,
    name: str = "s",
    verdict: str = "kill",
    reason: str = "no-edge",
    trades: int = 500,
    sharpe: float | None = 0.5,
    ir: float | None = 0.2,
    attempts: int = 1,
    engine_ran: bool = True,
) -> LedgerRow:
    return LedgerRow(
        strategy_id="01" + name.upper().ljust(24, "X")[:24],
        name=name,
        status="killed",
        verdict=verdict,
        reason=reason,
        protocol_version="0.2",
        total_trades=trades,
        sharpe=sharpe,
        information_ratio=ir,
        folds_with_edge=3,
        n_folds=6,
        attempts=attempts,
        engine_ran=engine_ran,
        created_at=NOW,
        card_path=None,
    )


# --- the distinction that matters --------------------------------------------


def test_a_dead_anchor_is_a_structural_death_not_a_finding() -> None:
    """It says the world-changer went away. It says nothing about the rule."""

    row = _row(reason="anchor-not-live", engine_ran=False, sharpe=None, ir=None)

    assert row.is_structural


def test_too_few_trades_is_structural_too() -> None:
    """The engine ran but the gate fired on sample size, which is a fact about
    the data window rather than about the edge."""

    assert _row(reason="insufficient-trades", trades=20).is_structural


def test_losing_to_the_benchmark_is_a_real_finding() -> None:
    assert not _row(reason="no-active-edge").is_structural


def test_missing_the_sharpe_floor_is_a_real_finding() -> None:
    assert not _row(verdict="hold-for-data", reason="below-sharpe-floor").is_structural


def test_a_row_that_never_reached_a_backtest_is_structural_whatever_its_reason() -> None:
    """Belt and braces: `engine_ran` is authoritative. A verdict that looks like
    a finding but ran no computation is the failure recorded in
    `shrap-first-verdict` — a confident-looking null result."""

    assert _row(reason="no-edge", engine_ran=False).is_structural


# --- the corpus ---------------------------------------------------------------


def test_the_honest_denominator_excludes_the_plumbing() -> None:
    """Five evaluations of which four were structural is one experiment, not
    five. That number is the whole point of the ledger."""

    rows = [
        _row(name="a", reason="anchor-not-live", engine_ran=False),
        _row(name="b", reason="insufficient-trades", trades=20),
        _row(name="c", reason="insufficient-trades", trades=43),
        _row(name="d", reason="insufficient-data", engine_ran=False),
        _row(name="e", verdict="hold-for-data", reason="below-sharpe-floor"),
    ]

    summary = summarise(rows, sharpe_floor=1.0, ir_floor=0.5)

    assert summary.total == 5
    assert summary.structural_deaths == 4
    assert summary.learned_about_edge == 1


def test_the_firms_actual_corpus_reads_as_mostly_plumbing() -> None:
    """The five strategies in research.strategies as of 2026-07-30: three killed
    (a dead anchor and two trade-count deaths) and two held below the Sharpe
    floor. So one real finding per idea, and only on the momentum family."""

    rows = [
        _row(name="fission", reason="anchor-not-live", engine_ran=False, sharpe=None, ir=None),
        _row(name="probe-control", reason="insufficient-trades", trades=20),
        _row(name="probe-treatment", reason="insufficient-trades", trades=43),
        _row(name="momentum", verdict="hold-for-data", reason="below-sharpe-floor", ir=0.392),
        _row(name="standdown", verdict="hold-for-data", reason="below-sharpe-floor", ir=0.158),
    ]

    summary = summarise(rows, sharpe_floor=1.0, ir_floor=0.5)

    assert summary.promoted == 0
    assert summary.structural_deaths == 3
    assert summary.cleared_sharpe_floor == 0
    assert summary.cleared_ir_floor == 0


# --- what the corpus supports -------------------------------------------------


def test_it_says_so_when_nothing_has_been_promoted() -> None:
    rows = [_row(name="a", verdict="hold-for-data", reason="below-sharpe-floor")]

    notes = " ".join(observations(summarise(rows, sharpe_floor=1.0, ir_floor=0.5)))

    assert "nothing has been promoted" in notes


def test_clearing_ir_but_never_sharpe_is_reported_as_evidence_about_the_gate() -> None:
    """The observation the firm most needs and could not previously make.
    Sharpe carries the market's return; the information ratio does not. Several
    strategies beating the benchmark while none clear the Sharpe floor is a
    statement about the gate, not about the strategies."""

    rows = [
        _row(name="a", verdict="hold-for-data", reason="below-sharpe-floor", sharpe=0.8, ir=0.7),
        _row(name="b", verdict="hold-for-data", reason="below-sharpe-floor", sharpe=0.9, ir=0.6),
    ]

    notes = " ".join(observations(summarise(rows, sharpe_floor=1.0, ir_floor=0.5)))

    assert "evidence about the Sharpe gate" in notes


def test_clearing_sharpe_but_never_ir_is_reported_as_the_opposite_warning() -> None:
    """The mirror image, and the more dangerous one: the floor is being cleared
    by market exposure rather than by skill."""

    rows = [
        _row(
            name="a",
            verdict="hold-for-data",
            reason="below-information-ratio-floor",
            sharpe=1.2,
            ir=0.1,
        ),
    ]

    notes = " ".join(observations(summarise(rows, sharpe_floor=1.0, ir_floor=0.5)))

    assert "market exposure rather than by skill" in notes


def test_an_all_structural_corpus_is_named_as_plumbing() -> None:
    rows = [
        _row(name="a", reason="anchor-not-live", engine_ran=False),
        _row(name="b", reason="insufficient-trades", trades=20),
    ]

    notes = " ".join(observations(summarise(rows, sharpe_floor=1.0, ir_floor=0.5)))

    assert "all plumbing" in notes or "died of setup defects" in notes


def test_an_empty_corpus_claims_nothing() -> None:
    assert observations(summarise([], sharpe_floor=1.0, ir_floor=0.5)) == ["nothing evaluated yet"]


# --- reading rows -------------------------------------------------------------


def test_a_strategy_with_no_evaluation_still_appears() -> None:
    """A hypothesis the firm proposed and has not tested is part of the corpus.
    Dropping it would make the ledger describe only the work that got finished.
    """

    row = row_from_mapping({"strategy_id": "01ABC", "name": "unevaluated", "status": "hypothesis"})

    assert row.verdict == "unevaluated"
    assert row.engine_ran is False
    assert row.sharpe is None


def test_a_missing_metric_stays_none_rather_than_becoming_zero() -> None:
    """Rendering an absence as 0.000 puts a measurement where there is none,
    which is the specific way a summary starts lying."""

    row = row_from_mapping(
        {"strategy_id": "01ABC", "verdict": "kill", "total_trades": 0, "aggregate_metrics": {}}
    )

    assert row.sharpe is None
    assert row.metric(row.sharpe) == "n/a"


def test_metrics_are_read_out_of_the_json_columns() -> None:
    row = row_from_mapping(
        {
            "strategy_id": "01ABC",
            "verdict": "hold-for-data",
            "reason": "below-sharpe-floor",
            "total_trades": 2507,
            "aggregate_metrics": {"sharpe": 0.782},
            "active_metrics": {"information_ratio": 0.392},
            "consistency_metrics": {"folds_with_active_edge": 3, "n_folds": 6},
            "attempts": 2,
        }
    )

    assert row.sharpe == 0.782
    assert row.information_ratio == 0.392
    assert row.folds == "3/6"
    assert row.attempts == 2
    assert row.engine_ran is True


# --- rendering ----------------------------------------------------------------


def test_the_table_names_what_each_strategy_died_of() -> None:
    rows = [_row(name="momentum", verdict="hold-for-data", reason="below-sharpe-floor")]

    out = render(rows, summarise(rows, sharpe_floor=1.0, ir_floor=0.5))

    assert "momentum" in out
    assert "below-sharpe-floor" in out
    assert "WHAT THIS SUPPORTS" in out


def test_an_empty_corpus_renders_without_claiming_anything() -> None:
    out = render([], summarise([], sharpe_floor=1.0, ir_floor=0.5))

    assert "No strategies" in out
