"""Axes discovered from the code, not from a list somebody remembered to update.

Mike, 2026-07-30: *"lets fix the authored list."* The list being fixed is
``guidance.DIMENSIONS``, whose own docstring conceded that "the firm cannot
notice an untried dimension nobody thought to name."

The test that matters most is :func:`test_the_authored_list_missed_the_axis_the_
firm_learned_from`. The old list named five dimensions and ``long_short`` was
not among them — the one axis whose variation the firm has already measured and
been surprised by. Discovery finds it because it is a field on a dataclass, and
fields cannot be forgotten.
"""

from __future__ import annotations

from typing import Any

from shrap.research.dimensions import (
    ENUMERABLE_VALUES,
    FINDING_HELD_CONSTANT,
    FINDING_IGNORED,
    FINDING_NEVER_SET,
    FINDING_UNUSED_VALUE,
    RULE_IMPLEMENTATIONS,
    corpus_values,
    engine_axes,
    render,
    survey,
)
from shrap.research.guidance import derive
from shrap.research.strategy_evaluator.factors import FACTOR_SCORERS
from shrap.research.strategy_evaluator.pipeline import (
    _CROSS_SECTIONAL_RULES,
    RULE_CROSS_SECTIONAL_FACTOR,
    RULE_REFERENCE_TREND,
)


def _spec(rule: str = RULE_CROSS_SECTIONAL_FACTOR, **params: Any) -> dict[str, Any]:
    return {"rule": rule, "params": params}


def _kinds(findings: Any, kind: str) -> list[str]:
    return sorted(f.axis for f in findings if f.kind == kind)


# --- the discovery itself -----------------------------------------------------


def test_the_authored_list_missed_the_axis_the_firm_learned_from() -> None:
    """The old DIMENSIONS tuple was rule-family, signal-input, horizon,
    universe, bar-frequency. Adding a short leg moved a strategy from Sharpe
    +0.782 to -0.079 — the largest single result the firm has, on an axis the
    list did not contain."""

    assert "long_short" in engine_axes()


def test_axes_come_from_the_dataclass_fields() -> None:
    """Not from a table restating them. A parameter added to a rule appears
    here with no edit anywhere."""

    axes = engine_axes()

    assert axes["factor"].rules == (RULE_CROSS_SECTIONAL_FACTOR,)
    assert "lookback" in axes
    assert "skip" in axes
    assert "market_filter" in axes  # only momentum has it, and only since #144


def test_a_single_name_rules_ticker_is_not_a_hypothesis_axis() -> None:
    """`ticker` names WHICH instrument a single-name rule trades — the universe
    question, already read from `tickers`. Counting it would report one thing
    twice under two names."""

    assert "ticker" not in engine_axes()


def test_the_rule_table_matches_the_engines_own_rule_set() -> None:
    """The one authored thing left in the module. If a rule is added to the
    dispatch and not to the table, its parameters go undiscovered — so this
    fails the build rather than quietly narrowing the survey."""

    assert set(RULE_IMPLEMENTATIONS) == {RULE_REFERENCE_TREND, *_CROSS_SECTIONAL_RULES}


def test_the_enumerable_factor_values_come_from_the_scorer_table() -> None:
    assert ENUMERABLE_VALUES["factor"] == frozenset(FACTOR_SCORERS)


# --- reading the corpus -------------------------------------------------------


def test_values_that_differ_only_in_type_are_one_choice() -> None:
    """252 from JSON and 252.0 from a float column are one decision, and
    counting them as two would report a corpus as more varied than it is."""

    values = corpus_values([_spec(lookback=252), _spec(lookback=252.0)])

    assert values["lookback"] == {"252"}


def test_a_none_valued_parameter_is_not_a_choice() -> None:
    assert "factor" not in corpus_values([_spec(factor=None, lookback=5)])


def test_the_rule_is_an_axis_like_any_other() -> None:
    values = corpus_values([_spec(), _spec(rule="cross-sectional-momentum")])

    assert values["rule"] == {"cross-sectional-factor", "cross-sectional-momentum"}


# --- the four findings --------------------------------------------------------


def test_a_parameter_no_spec_sets_is_reported_with_the_default_it_ran_on() -> None:
    findings = survey([_spec(factor="low-volatility", lookback=252)])

    never = [f for f in findings if f.kind == FINDING_NEVER_SET and f.axis == "top_n"]

    assert never
    assert "took the default" in never[0].detail
    assert "10" in never[0].detail


def test_one_distinct_value_across_the_corpus_is_held_constant() -> None:
    findings = survey(
        [
            _spec(factor="low-volatility", lookback=252, top_n=10),
            _spec(factor="volume-shock", lookback=50, top_n=10),
        ]
    )

    assert "top_n" in _kinds(findings, FINDING_HELD_CONSTANT)
    # lookback varied, so it is not reported at all.
    assert "lookback" not in _kinds(findings, FINDING_HELD_CONSTANT)
    assert "lookback" not in _kinds(findings, FINDING_NEVER_SET)


def test_implemented_values_nobody_selected_are_named() -> None:
    findings = survey([_spec(factor="low-volatility", lookback=252)])

    unused = [f for f in findings if f.kind == FINDING_UNUSED_VALUE and f.axis == "factor"]

    assert unused
    assert "high-proximity" in unused[0].detail
    assert "low-volatility" not in unused[0].detail


def test_a_parameter_no_rule_accepts_is_reported_as_dropped_by_the_engine() -> None:
    """A defect, not guidance. A spec setting `lookbak: 252` runs on the
    default and is not the strategy its spec describes."""

    findings = survey([_spec(factor="low-volatility", lookbak=252)])

    ignored = [f for f in findings if f.kind == FINDING_IGNORED]

    assert [f.axis for f in ignored] == ["lookbak"]
    assert "accepted by no rule" in ignored[0].detail


def test_an_empty_corpus_reports_every_axis_as_never_set() -> None:
    findings = survey([])

    assert set(_kinds(findings, FINDING_NEVER_SET)) == set(engine_axes())


# --- rendering ----------------------------------------------------------------


def test_the_defect_group_is_rendered_first() -> None:
    """A spec the engine silently ignores outranks any suggestion about what to
    try next — one is a wrong result on record, the other is an idea."""

    text = render(survey([_spec(factor="low-volatility", nonsense=1)]))

    assert text.index("IGNORED BY THE ENGINE") < text.index("NEVER SET")


def test_a_fully_varied_corpus_says_so() -> None:
    assert "varied at least once" in render([])


def test_the_render_names_where_the_axes_came_from() -> None:
    assert "Read off the strategy dataclasses" in render(survey([_spec()]))


# --- the boundary still holds -------------------------------------------------


def _row(sid: str, **params: Any) -> dict[str, Any]:
    return {
        "strategy_id": sid,
        "name": sid,
        "spec": _spec(**params),
        "tickers": {"long": ["A", "B"], "short": []},
        "information_ratio": -0.2,
        "tested": True,
    }


def test_discovered_axes_reach_the_guidance_output() -> None:
    text = derive([_row("S1", factor="low-volatility", lookback=252)]).render()

    assert "no strategy has ever set" in text


def test_the_defect_finding_is_a_warning_not_a_suggestion() -> None:
    """`ignored-by-the-engine` says a recorded result is about something other
    than what its spec claims. That belongs with the reasons results may not
    mean what they appear to, not with ideas for what to try."""

    guidance = derive([_row("S1", factor="low-volatility", lookbak=252)])

    assert any("no rule accepts" in o.statement for o in guidance.warnings)
    assert not any("no rule accepts" in o.statement for o in guidance.untried)


def test_discovery_still_says_nothing_about_what_counts_as_success() -> None:
    """The hard rule from `guidance.py`, re-asserted over the new observations:
    a system that can move its own gate will move it until something passes."""

    rows = [_row(f"S{i}", factor="low-volatility", lookback=252, junk=i) for i in range(6)]

    text = derive(rows).render().lower()

    for forbidden in ("floor", "threshold", "lower the", "raise the bar", "promote if"):
        assert forbidden not in text


def test_the_survey_is_given_no_way_to_see_a_result() -> None:
    """Structural, not a promise. `survey` takes specs and returns findings; no
    argument carries a metric or a verdict, so there is no path by which an
    outcome could influence what it reports."""

    winner = _spec(factor="low-volatility", lookback=252)
    loser = _spec(factor="low-volatility", lookback=252)

    assert survey([winner]) == survey([loser])
