"""Evidence for a calibration ruling, and a refusal to manufacture one.

Thirteen evaluations, zero promotions, best on record at IR +0.392 under a 0.50
floor. The question is whether the floor is wrong or the strategies are — and
the honest first answer is that the firm threw away the data needed to tell.

The evaluator computed a per-fold information-ratio *sequence* on every run and
persisted only its mean and standard deviation. For the twelve strategies
already killed that is unrecoverable: kills are terminal and `evaluate` refuses
any non-hypothesis strategy. The most important test here asserts the report
says so rather than answering from four points.
"""

from __future__ import annotations

from typing import Any

from shrap.research.persistence import (
    MIN_STRATEGIES_FOR_PERSISTENCE,
    analyse,
    pearson,
    run_from_mapping,
)


def _row(
    sid: str,
    ir: float | None = 0.1,
    *,
    name: str = "",
    consistency: float | None = 0.5,
    folds: list[float] | None = None,
) -> dict[str, Any]:
    metrics: dict[str, Any] = {"consistency": consistency, "n_folds": 6}
    if folds is not None:
        metrics["fold_information_ratios"] = folds
    return {
        "strategy_id": sid,
        "name": name or sid,
        "verdict": "kill",
        "information_ratio": ir,
        "sharpe": 0.8,
        "consistency_metrics": metrics,
    }


# --- the refusal ---------------------------------------------------------------


def test_the_early_late_question_is_reported_unanswerable_without_sequences() -> None:
    """The whole point. Four points would produce a number, and a number would
    be read as a finding."""

    report = analyse([_row(f"S{i}", ir=0.1 * i) for i in range(13)], live_floor=0.5)

    assert report.early_vs_late is None
    text = report.render()
    assert "UNANSWERABLE" in text
    assert "cannot be recovered" in text


def test_it_names_the_reason_the_data_is_gone() -> None:
    """A reader six months from now needs to know this was not an oversight
    that can be corrected by re-running something."""

    text = analyse([_row("S1")], live_floor=0.5).render()

    assert "kills are terminal" in text
    assert "refuses any non-hypothesis strategy" in text


def test_below_the_floor_of_sequences_it_still_refuses() -> None:
    rows = [
        _row(f"S{i}", ir=0.1 * i, folds=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
        for i in range(MIN_STRATEGIES_FOR_PERSISTENCE - 1)
    ]

    assert analyse(rows, live_floor=0.5).early_vs_late is None


def test_with_enough_sequences_it_answers() -> None:
    # Both halves must vary across strategies, or the correlation is genuinely
    # undefined — which is the flat-series guard doing its job, not a failure.
    rows = [
        _row(
            f"S{i}",
            ir=0.1 * i,
            folds=[0.1 * i, 0.1 * i, 0.1 * i, 0.05 * i, 0.05 * i, 0.05 * i],
        )
        for i in range(MIN_STRATEGIES_FOR_PERSISTENCE + 2)
    ]

    report = analyse(rows, live_floor=0.5)

    assert report.early_vs_late is not None
    assert abs(report.early_vs_late - 1.0) < 1e-9  # perfectly persistent, by construction
    assert "UNANSWERABLE" not in report.render()


def test_a_flat_measure_across_strategies_is_undefined_not_zero() -> None:
    """Every strategy scoring the same consistency carries no information about
    whether the two measures agree. Reporting r=0 would say they disagree."""

    rows = [_row(f"S{i}", ir=0.1 * i, consistency=0.5) for i in range(13)]

    assert analyse(rows, live_floor=0.5).ir_vs_consistency is None


def test_the_two_measures_are_compared_when_both_vary() -> None:
    rows = [_row(f"S{i}", ir=0.1 * i, consistency=0.2 * i) for i in range(13)]

    r = analyse(rows, live_floor=0.5).ir_vs_consistency

    assert r is not None
    assert abs(r - 1.0) < 1e-9


# --- the counterfactual --------------------------------------------------------


def test_each_floor_reports_the_names_it_would_have_promoted() -> None:
    """Names, not a rate. A calibration is a decision about specific
    strategies, and a count hides which ones."""

    rows = [
        _row("A", ir=0.392, name="momentum 126/21"),
        _row("B", ir=0.184, name="volume shock"),
        _row("C", ir=-0.006, name="network peripherality"),
    ]

    report = analyse(rows, live_floor=0.5)
    by_floor = {c.floor: c for c in report.counterfactuals}

    assert by_floor[0.50].promoted == ()
    assert by_floor[0.35].promoted == ("momentum 126/21",)
    assert by_floor[0.30].promoted == ("momentum 126/21",)
    # Volume shock at +0.184 clears none of the candidate floors, which is
    # itself part of the answer: "lower the bar a little" would not have bought
    # a second strategy, only the same one.
    assert all("volume shock" not in c.promoted for c in report.counterfactuals)


def test_names_are_listed_best_first() -> None:
    """So the marginal admission — the one a floor barely lets through — is the
    last name on the line rather than buried."""

    rows = [
        _row("B", ir=0.31, name="second"),
        _row("A", ir=0.88, name="first"),
        _row("C", ir=0.29, name="third"),
    ]

    by_floor = {c.floor: c for c in analyse(rows, live_floor=0.5).counterfactuals}

    assert by_floor[0.25].promoted == ("first", "second", "third")


def test_the_live_floor_is_always_reported_even_if_unusual() -> None:
    report = analyse([_row("A", ir=0.9)], live_floor=0.77)

    assert 0.77 in {c.floor for c in report.counterfactuals}


def test_the_best_strategy_and_its_distance_from_the_floor_are_named() -> None:
    text = analyse([_row("A", ir=0.392, name="momentum")], live_floor=0.5).render()

    assert "momentum" in text
    assert "+0.392" in text
    assert "below the floor" in text


# --- it decides nothing --------------------------------------------------------


def test_the_report_states_that_it_moves_no_gate() -> None:
    """The boundary `guidance.py` states out loud, restated here because this
    module is the one that would be tempting to wire into a verdict."""

    text = analyse([_row("A")], live_floor=0.5).render()

    assert "no floor moves without Mike" in text
    assert "evidence for a ruling, not a ruling" in text


def test_every_claim_carries_its_sample_size() -> None:
    text = analyse([_row(f"S{i}") for i in range(13)], live_floor=0.5).render()

    assert "n=13" in text
    assert "error bars" in text


# --- the arithmetic ------------------------------------------------------------


def test_correlation_is_undefined_below_three_points() -> None:
    assert pearson([1.0, 2.0], [1.0, 2.0]) is None


def test_correlation_is_undefined_when_a_series_is_flat() -> None:
    assert pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None


def test_correlation_recovers_a_perfect_relationship() -> None:
    r = pearson([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0])

    assert r is not None
    assert abs(r - 1.0) < 1e-9


def test_a_sequence_splits_into_comparable_halves() -> None:
    run = run_from_mapping(_row("A", folds=[0.0, 0.0, 0.0, 1.0, 1.0, 1.0]))

    assert run.early_late == (0.0, 1.0)


def test_a_row_without_a_sequence_has_no_halves() -> None:
    assert run_from_mapping(_row("A")).early_late is None


def test_jsonb_arriving_as_a_string_does_not_crash_the_reader() -> None:
    """asyncpg hands jsonb back as TEXT unless a codec is registered — the shape
    no fixture produces and every production row does (PR #152)."""

    row = _row("A")
    row["consistency_metrics"] = '{"consistency": 0.5}'

    assert run_from_mapping(row).consistency is None  # unparsed, not exploded
