"""The Hypothesis Generator writes specs (#278) when an effect is a formula.

Three things must hold. A paper whose score is a formula over the feature
library becomes a strategy, not a capability gap. The formula is judged by the
Evaluator's own parser, never by the model. And a new window or threshold on an
effect the firm holds is refused, whichever door it comes through — the named
rules or a spec.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any

import pytest

from shrap.research.hypothesis_generator.expressible import (
    FEATURE_DESCRIPTIONS,
    FILED_FIGURES,
    OUTCOME_EXPRESSIBLE,
    OUTCOME_MISSING_DATA,
    OUTCOME_MISSING_SCORER,
    classify,
    normalise_input,
    signal_shape,
    spec_identity,
)
from shrap.research.hypothesis_generator.generator import held_identities
from shrap.research.hypothesis_generator.literature import (
    OUTCOME_CAPABILITY_GAP,
    OUTCOME_PROPOSED,
    OUTCOME_REFUSED,
)
from shrap.research.hypothesis_generator.proposer import GRAMMAR_EXAMPLES, PROPOSER_SYSTEM_PROMPT
from shrap.research.strategy_evaluator.pipeline import (
    RULE_CROSS_SECTIONAL_MOMENTUM,
    RULE_SIGNAL_SPEC,
    _default_strategy_factory,
)
from shrap.research.strategy_evaluator.signals import (
    ACCOUNTING_FEATURES,
    FEATURES,
    NARY_OPS,
    UNARY_OPS,
    parse,
)
from shrap.research.strategy_seed.spec_strategies import universe_tickers
from tests.research.test_hypothesis_generator import (
    _existing,
    _FakeLLM,
    _FakeRegistry,
    _generator,
    _item,
    _response,
)

GROSS_PROFITABILITY: dict[str, Any] = {
    "op": "div",
    "args": [
        {"feature": "fundamental", "metric": "gross_profit"},
        {"feature": "fundamental", "metric": "total_assets"},
    ],
}


def _spec_response(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "effect_name": "gross-profitability",
        "prior": {"authors": "Novy-Marx", "year": 2013, "claim": "profitable firms earn more"},
        "rule": "signal-spec",
        "factor": None,
        "signal": GROSS_PROFITABILITY,
        "select": "top",
        "rebalance": "monthly",
        "lookback": 252,
        "required_inputs": ["gross profit", "total assets"],
        "scorer_sketch": "gross profit divided by total assets, latest annual filing",
    }
    body.update(overrides)
    return _response(**body)


def _run(response: str, registry: _FakeRegistry | None = None) -> tuple[Any, _FakeRegistry]:
    generator, reg, _, _ = _generator(_FakeLLM(response), registry)
    report = asyncio.run(generator.run([_item()]))
    return report.outcomes[0], reg


def test_the_prompt_describes_every_feature_the_library_has() -> None:
    assert set(FEATURE_DESCRIPTIONS) == set(FEATURES) | set(ACCOUNTING_FEATURES)
    for name in FEATURE_DESCRIPTIONS:
        assert name in PROPOSER_SYSTEM_PROMPT


@pytest.mark.parametrize("form", sorted(GRAMMAR_EXAMPLES))
def test_every_grammar_example_in_the_prompt_parses(form: str) -> None:
    """The prompt shows real trees, and the Evaluator's parser accepts each one.

    v4 described one-operand ops under `args`; the parser takes them under `of`.
    """

    parse(GRAMMAR_EXAMPLES[form])
    assert json.dumps(GRAMMAR_EXAMPLES[form]) in PROPOSER_SYSTEM_PROMPT


def test_the_prompt_teaches_both_operand_forms_for_every_op() -> None:
    for op in UNARY_OPS:
        parse({"op": op, "of": {"feature": "volatility", "lookback": 21}})
    for op in NARY_OPS:
        parse({"op": op, "args": [{"const": 1}, {"feature": "volatility", "lookback": 21}]})
    assert '"of"' in PROPOSER_SYSTEM_PROMPT and '"args"' in PROPOSER_SYSTEM_PROMPT


def test_a_one_operand_formula_from_the_model_is_proposed() -> None:
    signal = {"op": "rank", "of": {"feature": "abs_return", "lookback": 1}}
    outcome, reg = _run(
        _spec_response(
            effect_name="magnitude-shrinkage",
            signal=signal,
            select="bottom",
            rebalance="daily",
            required_inputs=["close"],
        )
    )

    assert outcome.outcome == OUTCOME_PROPOSED
    assert reg.registered[0].spec["params"]["signal"] == signal


@pytest.mark.parametrize(
    ("wording", "metric"),
    [
        ("Gross profit", "gross_profit"),
        ("book equity", "stockholders_equity"),
        ("R&D", "rd_expense"),
    ],
)
def test_filed_figures_are_available_inputs(wording: str, metric: str) -> None:
    assert normalise_input(wording) == metric


def test_every_metric_name_the_prompt_lists_resolves_to_itself() -> None:
    for metric in FILED_FIGURES:
        assert normalise_input(metric) == metric
        assert normalise_input(metric.replace("_", " ")) == metric


def test_derived_and_unstored_figures_stay_missing() -> None:
    for wording in ("book-to-market", "EBITDA", "free cash flow", "analyst forecasts"):
        assert normalise_input(wording) is None


def test_a_filed_figure_needs_a_spec_not_a_named_scorer() -> None:
    assert classify("signal-spec", None, ["gross profit", "total assets"]) == OUTCOME_EXPRESSIBLE
    assert (
        classify("cross-sectional-factor", "low-volatility", ["book equity", "close"])
        == OUTCOME_MISSING_SCORER
    )
    assert classify("signal-spec", None, ["short interest"]) == OUTCOME_MISSING_DATA


def test_a_formula_paper_becomes_a_strategy_over_companies() -> None:
    outcome, reg = _run(_spec_response())

    assert outcome.outcome == OUTCOME_PROPOSED
    record = reg.registered[0]
    assert record.spec["rule"] == RULE_SIGNAL_SPEC
    assert record.spec["params"]["top_n"] == 10  # construction is not the model's
    assert record.tickers["long"] == universe_tickers("equities")
    # It builds: the Evaluator's factory accepts what the generator wrote.
    _default_strategy_factory(record, record.tickers["long"])


def test_a_price_only_formula_keeps_the_launch_universe() -> None:
    signal = {"op": "sub", "args": [{"const": 10}, {"feature": "rsi", "lookback": 2}]}
    outcome, reg = _run(
        _spec_response(
            effect_name="rsi-oversold",
            signal=signal,
            select="positive",
            rebalance="daily",
            required_inputs=["close"],
        )
    )

    assert outcome.outcome == OUTCOME_PROPOSED
    assert reg.registered[0].tickers["long"] == universe_tickers("launch")
    assert "top_n" not in reg.registered[0].spec["params"]


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"signal": {"feature": "vibes"}}, "unknown feature"),
        ({"signal": None}, "no formula"),
        ({"select": "long_short"}, "select"),
        ({"rebalance": "hourly"}, "rebalance"),
        ({"signal": {"feature": "return", "lookback": 5000}}, "lookback"),
    ],
)
def test_the_evaluators_parser_judges_the_formula(overrides: dict[str, Any], why: str) -> None:
    outcome, reg = _run(_spec_response(required_inputs=["close"], **overrides))

    assert outcome.outcome == OUTCOME_REFUSED, why
    assert "invalid-signal-spec" in outcome.detail
    assert not reg.registered


def test_numbers_are_not_identity() -> None:
    rsi2 = {"op": "sub", "args": [{"const": 10}, {"feature": "rsi", "lookback": 2}]}
    rsi3 = {"op": "sub", "args": [{"const": 20}, {"feature": "rsi", "lookback": 3}]}

    assert signal_shape(rsi2, "positive") == signal_shape(rsi3, "positive")
    assert signal_shape(rsi2, "positive") != signal_shape(rsi2, "top")


def test_top_of_a_negation_is_the_bottom_of_the_signal() -> None:
    x = {"feature": "abs_return", "lookback": 1}
    neg = {"op": "neg", "of": x}

    assert signal_shape(neg, "top") == signal_shape(x, "bottom")
    assert signal_shape({"op": "neg", "of": neg}, "top") == signal_shape(x, "top")
    # `positive` is a threshold at zero, so negation genuinely changes it.
    assert signal_shape(neg, "positive") != signal_shape(x, "positive")


def test_a_held_spec_blocks_the_same_formula_at_new_windows() -> None:
    held = _existing("HELD", RULE_SIGNAL_SPEC)
    held = replace(
        held,
        spec={"rule": RULE_SIGNAL_SPEC, "params": {"signal": GROSS_PROFITABILITY, "select": "top"}},
    )
    outcome, _ = _run(_spec_response(), _FakeRegistry([held]))

    assert outcome.outcome == OUTCOME_REFUSED
    assert "already-held" in outcome.detail


def test_momentum_written_as_a_spec_is_still_momentum() -> None:
    held = _existing("MOM", RULE_CROSS_SECTIONAL_MOMENTUM)
    signal = {"feature": "return", "lookback": 200, "skip": 10}
    outcome, _ = _run(
        _spec_response(effect_name="price-momentum", signal=signal, required_inputs=["returns"]),
        _FakeRegistry([held]),
    )

    assert outcome.outcome == OUTCOME_REFUSED
    assert "already-held" in outcome.detail
    assert RULE_CROSS_SECTIONAL_MOMENTUM in spec_identity(signal, "top")


def test_a_held_momentum_spec_blocks_the_named_rule_too() -> None:
    spec_record = _existing("SPECMOM", RULE_SIGNAL_SPEC)
    spec_record = replace(
        spec_record,
        spec={
            "rule": RULE_SIGNAL_SPEC,
            "params": {"signal": {"feature": "return", "lookback": 104}, "select": "top"},
        },
    )

    assert RULE_CROSS_SECTIONAL_MOMENTUM in held_identities([spec_record])


def test_an_input_the_features_cannot_compute_is_still_a_gap() -> None:
    outcome, reg = _run(
        _spec_response(
            effect_name="short-interest", signal=None, required_inputs=["short interest", "close"]
        )
    )

    assert outcome.outcome == OUTCOME_CAPABILITY_GAP
    assert not reg.registered
