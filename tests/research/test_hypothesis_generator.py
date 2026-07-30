"""The firm proposing its own strategies, and refusing to.

Mike, 2026-07-30: *"the rules are the rules we are not trying to circumvent
them, we are trying to find strategies that work. and id rather not put them in
one at a time when there could 1000s to try."*

Both halves are pinned below. The rules hold — a proposal with no citation dies,
and a re-parameterisation of an effect the firm already holds cannot be
registered as a fresh idea however good its abstract. And the volume path is
real, but it runs through the capability queue rather than through the registry:
the engine reads two series and implements four effects, so the literature's
answer to "what next" is usually "something you have not built."
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

import pytest

from shrap.research.hypothesis_generator.cli import _dsn, items_from_file
from shrap.research.hypothesis_generator.expressible import (
    OUTCOME_MISSING_DATA,
    OUTCOME_MISSING_SCORER,
    CapabilityGap,
    GapCitation,
    classify,
    hypothesis_key,
    missing_inputs,
    rank_gaps,
)
from shrap.research.hypothesis_generator.generator import (
    HypothesisGenerator,
    held_identities,
)
from shrap.research.hypothesis_generator.literature import (
    OUTCOME_CAPABILITY_GAP,
    OUTCOME_PROPOSED,
    OUTCOME_REFUSED,
    LiteratureItem,
)
from shrap.research.hypothesis_generator.proposer import parse_proposal
from shrap.research.hypothesis_generator.record import (
    FIXED_LONG_SHORT,
    FIXED_TOP_N,
    build_record,
)
from shrap.research.hypothesis_generator.store import InMemoryGapStore, render_queue
from shrap.research.hypothesis_generator.validate import (
    REASON_ALREADY_HELD,
    REASON_LOOKBACK_OUT_OF_BOUNDS,
    REASON_NO_PRIOR,
    REASON_NOT_A_MARKET_EFFECT,
    REASON_UNPARSEABLE,
)
from shrap.research.strategy_evaluator.factors import CrossSectionalFactorStrategy
from shrap.research.strategy_evaluator.pipeline import (
    ARCHETYPE_TECHNICAL_CATALYST,
    _validate_param_bounds,
)
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord

_THESIS = (
    "Stocks with low residual volatility have historically earned higher "
    "risk-adjusted returns than their high-residual-volatility peers, which runs "
    "against what CAPM predicts and has survived decades of challenge."
)


def _item(item_id: str = "arxiv:2401.00001") -> LiteratureItem:
    return LiteratureItem(
        item_id=item_id,
        source="arxiv",
        title="The cross-section of volatility and expected returns",
        abstract="We examine the pricing of aggregate volatility risk...",
        url=f"https://arxiv.org/abs/{item_id.split(':')[-1]}",
        category="q-fin.PM",
    )


def _response(**overrides: Any) -> str:
    body: dict[str, Any] = {
        "is_market_effect": True,
        "reason": "a cross-sectional ranking effect over listed equities",
        "effect_name": "low-volatility",
        "prior": {
            "authors": "Ang, Hodrick, Xing & Zhang",
            "year": 2006,
            "claim": "low-volatility stocks earn higher risk-adjusted returns",
        },
        "rule": "cross-sectional-factor",
        "factor": "low-volatility",
        "lookback": 252,
        "required_inputs": ["close"],
        "scorer_sketch": "trailing standard deviation of daily returns, negated",
        "deviation": "none",
        "kill_criteria": [
            "formation-window volatility does not persist into the holding window",
            "the selection is a utilities sector bet rather than a volatility bet",
        ],
        "thesis": _THESIS,
    }
    body.update(overrides)
    return json.dumps(body)


@dataclass(frozen=True, slots=True)
class _Result:
    content: str
    model: str = "qwen3:32b"


class _FakeLLM:
    """Hands back canned responses in order."""

    def __init__(self, *responses: str) -> None:
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, *args: Any, **kwargs: Any) -> _Result:
        self.calls += 1
        return _Result(content=self.responses.pop(0))


class _FakeRegistry:
    def __init__(self, existing: list[StrategyRecord] | None = None) -> None:
        self.existing = existing or []
        self.registered: list[StrategyRecord] = []

    async def list_all(self) -> list[StrategyRecord]:
        return list(self.existing)

    async def register(self, record: StrategyRecord, **kwargs: Any) -> bool:
        self.registered.append(record)
        return True


class _FakeLiterature:
    def __init__(self) -> None:
        self.marked: list[tuple[str, str, str]] = []

    async def pending(self, limit: int) -> list[LiteratureItem]:
        return []

    async def mark_processed(self, item_id: str, outcome: str, detail: str) -> None:
        self.marked.append((item_id, outcome, detail))


def _existing(strategy_id: str, rule: str, factor: str | None = None) -> StrategyRecord:
    params: dict[str, Any] = {"lookback": 252}
    if factor:
        params["factor"] = factor
    return StrategyRecord(
        strategy_id=strategy_id,
        name=f"held {strategy_id}",
        version=1,
        archetype=ARCHETYPE_TECHNICAL_CATALYST,
        status=STATUS_HYPOTHESIS,
        source="mike-seed",
        thesis="held",
        anchor={},
        tickers={"long": ["AAPL"], "short": []},
        spec={"rule": rule, "params": params},
        spec_hash=f"sha256:{strategy_id}",
        regime_sizing_modifier={},
        kill_criteria=["x"],
        code_ref=None,
        created_at=None,
        updated_at=None,
    )


def _generator(
    llm: _FakeLLM,
    registry: _FakeRegistry | None = None,
    gaps: InMemoryGapStore | None = None,
    *,
    dry_run: bool = False,
) -> tuple[HypothesisGenerator, _FakeRegistry, InMemoryGapStore, _FakeLiterature]:
    reg = registry or _FakeRegistry()
    gap_store = gaps or InMemoryGapStore()
    literature = _FakeLiterature()
    return (
        HypothesisGenerator(
            llm=llm,
            registry=reg,
            literature=literature,
            gaps=gap_store,
            dry_run=dry_run,
        ),
        reg,
        gap_store,
        literature,
    )


# --- the anchor: no citation, no proposal -------------------------------------


async def test_a_proposal_without_a_prior_is_refused() -> None:
    """`technical-catalyst` has no world-changer anchor (ADR-0013), so the
    citation is the only thing standing between this agent and 'ask an LLM for
    a trading strategy'."""

    generator, registry, _, _ = _generator(_FakeLLM(_response(prior=None)))

    report = await generator.run([_item()])

    assert report.outcomes[0].outcome == OUTCOME_REFUSED
    assert REASON_NO_PRIOR in report.outcomes[0].detail
    assert registry.registered == []


async def test_a_prior_missing_its_year_is_not_a_prior() -> None:
    partial = {"authors": "Ang, Hodrick, Xing & Zhang", "claim": "low vol wins"}

    generator, registry, _, _ = _generator(_FakeLLM(_response(prior=partial)))

    report = await generator.run([_item()])

    assert REASON_NO_PRIOR in report.outcomes[0].detail
    assert registry.registered == []


async def test_an_item_the_model_says_is_not_a_market_effect_is_refused() -> None:
    generator, _, gaps, _ = _generator(
        _FakeLLM(_response(is_market_effect=False, reason="a survey of neural architectures"))
    )

    report = await generator.run([_item()])

    assert REASON_NOT_A_MARKET_EFFECT in report.outcomes[0].detail
    # Not a gap either — an item that is not an effect is not a missing capability.
    assert gaps.gaps == []


async def test_an_unparseable_response_refuses_rather_than_crashing() -> None:
    generator, registry, _, _ = _generator(_FakeLLM("not json at all"))

    report = await generator.run([_item()])

    assert REASON_UNPARSEABLE in report.outcomes[0].detail
    assert registry.registered == []


# --- the rules hold: no laundering a search past the gate ---------------------


async def test_an_effect_the_firm_already_holds_is_refused() -> None:
    """The one way this archetype could corrupt a promote decision: register a
    variant as a root and reset its lineage's attempt count to one."""

    registry = _FakeRegistry([_existing("S1", "cross-sectional-factor", "low-volatility")])

    generator, _, _, _ = _generator(_FakeLLM(_response()), registry)
    report = await generator.run([_item()])

    assert REASON_ALREADY_HELD in report.outcomes[0].detail
    assert "S1" in report.outcomes[0].detail
    assert registry.registered == []


async def test_a_different_lookback_on_a_held_effect_is_still_the_held_effect() -> None:
    """A 120-day version of an effect the firm holds at 252 is attempt 2 of that
    lineage, not a new hypothesis. The identity key ignores parameters exactly so
    this cannot be dressed up as an original idea."""

    registry = _FakeRegistry([_existing("S1", "cross-sectional-factor", "low-volatility")])

    generator, _, _, _ = _generator(_FakeLLM(_response(lookback=120)), registry)
    report = await generator.run([_item()])

    assert REASON_ALREADY_HELD in report.outcomes[0].detail


async def test_two_papers_on_one_effect_in_a_single_batch_yield_one_strategy() -> None:
    """The registry is read once, before anything is written. Without an
    in-batch claim the second paper would pass the duplicate check against a
    snapshot that predates the first proposal."""

    llm = _FakeLLM(_response(), _response())
    generator, registry, _, _ = _generator(llm)

    report = await generator.run([_item("arxiv:1"), _item("arxiv:2")])

    assert report.count(OUTCOME_PROPOSED) == 1
    assert REASON_ALREADY_HELD in report.outcomes[1].detail
    assert len(registry.registered) == 1


async def test_a_horizon_outside_its_rules_window_is_refused() -> None:
    """Momentum runs 21-504 sessions and reversal 2-21, deliberately disjoint. A
    five-day momentum spec is a reversal wearing momentum's name."""

    generator, registry, _, _ = _generator(
        _FakeLLM(_response(rule="cross-sectional-momentum", factor=None, lookback=5))
    )

    report = await generator.run([_item()])

    assert REASON_LOOKBACK_OUT_OF_BOUNDS in report.outcomes[0].detail
    assert registry.registered == []


async def test_every_proposal_is_a_lineage_root_with_no_way_to_set_a_parent() -> None:
    generator, registry, _, _ = _generator(_FakeLLM(_response()))

    await generator.run([_item()])

    assert registry.registered[0].parent_strategy_id is None
    assert registry.registered[0].revision_reason is None


# --- capability gaps: the answer to "there could be 1000s" ---------------------


async def test_an_effect_with_no_scorer_becomes_a_buildable_gap() -> None:
    """Computable from closes; nobody wrote the function. This is the queue entry
    worth having — an afternoon of work, asked for by the literature."""

    generator, registry, gaps, _ = _generator(
        _FakeLLM(
            _response(
                effect_name="max-daily-return",
                factor="max-daily-return",
                required_inputs=["close"],
                scorer_sketch="the largest single-day return in the trailing window",
            )
        )
    )

    report = await generator.run([_item()])

    assert report.outcomes[0].outcome == OUTCOME_CAPABILITY_GAP
    assert gaps.gaps[0].kind == OUTCOME_MISSING_SCORER
    assert gaps.gaps[0].is_buildable
    assert registry.registered == []


async def test_an_effect_needing_data_the_firm_lacks_records_what_it_needs() -> None:
    """A different queue entirely: a feed to acquire, not a function to write.
    The count of these is the honest argument for buying data."""

    generator, _, gaps, _ = _generator(
        _FakeLLM(
            _response(
                effect_name="share-turnover",
                factor="share-turnover",
                required_inputs=["volume", "shares outstanding"],
            )
        )
    )

    report = await generator.run([_item()])

    assert report.outcomes[0].outcome == OUTCOME_CAPABILITY_GAP
    assert gaps.gaps[0].kind == OUTCOME_MISSING_DATA
    assert gaps.gaps[0].missing == ("shares outstanding",)
    assert not gaps.gaps[0].is_buildable


async def test_missing_data_outranks_a_missing_scorer_in_the_classification() -> None:
    """An effect needing intraday bars is out of reach whether or not a scorer
    exists for it, and reporting it as buildable would head a build queue with
    something nobody can build."""

    verdict = classify("cross-sectional-factor", "realised-variance", ["intraday prices"])

    assert verdict == OUTCOME_MISSING_DATA


async def test_an_unrecognised_input_wording_counts_as_missing() -> None:
    """The bias is to out-of-reach. Guessing that 'realised variance from
    5-minute returns' means `close` would produce a strategy silently
    implementing a different effect from the one it cites."""

    assert missing_inputs(["realised variance from 5-minute returns"]) == (
        "realised variance from 5-minute returns",
    )
    assert missing_inputs(["closing prices", "Daily Volume"]) == ()


async def test_an_uncitable_item_does_not_reach_the_build_queue() -> None:
    """Citation is checked before capability on purpose: a queue entry that
    cannot say which paper asked for it is a suggestion, not evidence."""

    generator, _, gaps, _ = _generator(
        _FakeLLM(_response(prior=None, factor="something-unimplemented"))
    )

    report = await generator.run([_item()])

    assert report.outcomes[0].outcome == OUTCOME_REFUSED
    assert gaps.gaps == []


def test_the_queue_counts_distinct_papers_not_mentions() -> None:
    def gap(effect: str, item_id: str, kind: str = OUTCOME_MISSING_SCORER) -> CapabilityGap:
        return CapabilityGap(
            effect_name=effect,
            kind=kind,
            missing=(),
            sketch="sketch",
            citation=GapCitation(item_id=item_id, title="t", url=None, prior="p"),
        )

    ranked = rank_gaps(
        [
            gap("skewness", "a"),
            gap("skewness", "a"),  # the same paper twice
            gap("amihud-illiquidity", "b"),
            gap("amihud-illiquidity", "c"),
        ]
    )

    assert ranked[0].effect_name == "amihud-illiquidity"
    assert ranked[0].citations == 2
    assert ranked[1].citations == 1


def test_a_buildable_gap_sorts_ahead_of_a_data_gap_at_equal_citations() -> None:
    def gap(effect: str, kind: str) -> CapabilityGap:
        return CapabilityGap(
            effect_name=effect,
            kind=kind,
            missing=(),
            sketch="s",
            citation=GapCitation(item_id=effect, title="t", url=None, prior="p"),
        )

    ranked = rank_gaps(
        [gap("a-needs-data", OUTCOME_MISSING_DATA), gap("z-buildable", OUTCOME_MISSING_SCORER)]
    )

    assert ranked[0].effect_name == "z-buildable"


# --- what the registered record actually says ---------------------------------


async def test_the_deviation_always_names_the_long_only_universe() -> None:
    """The failure this field exists for: the firm's momentum strategy dropped
    the short leg of Jegadeesh-Titman and nothing recorded that it had."""

    generator, registry, _, _ = _generator(_FakeLLM(_response(deviation="none")))

    await generator.run([_item()])

    thesis = registry.registered[0].thesis
    assert "long-only" in thesis
    assert "short leg" in thesis
    assert "DEVIATION:" in thesis


async def test_the_thesis_carries_the_prior_and_the_source() -> None:
    generator, registry, _, _ = _generator(_FakeLLM(_response()))

    await generator.run([_item()])

    thesis = registry.registered[0].thesis
    assert "Ang, Hodrick, Xing & Zhang (2006)" in thesis
    assert "SOURCE:" in thesis
    assert "arxiv.org" in thesis


async def test_construction_is_fixed_and_not_the_models_to_choose() -> None:
    """Held identical across proposals so a comparison between two of them
    measures the effects rather than two implementations."""

    generator, registry, _, _ = _generator(
        # Values the model would have no way to send: they are not in the schema.
        _FakeLLM(_response(top_n=25, long_short=True, gross_exposure=0.3))
    )

    await generator.run([_item()])

    params = registry.registered[0].spec["params"]
    assert params["top_n"] == FIXED_TOP_N
    assert params["long_short"] is FIXED_LONG_SHORT
    assert params["gross_exposure"] == 1.0


async def test_the_protocol_kill_criteria_are_appended_not_left_to_the_proposer() -> None:
    generator, registry, _, _ = _generator(_FakeLLM(_response()))

    await generator.run([_item()])

    criteria = " ".join(registry.registered[0].kill_criteria)
    assert "equal-weight buy-and-hold" in criteria
    assert "friction stress" in criteria
    assert "fewer than half the walk-forward folds" in criteria


async def test_provenance_records_which_prompt_and_model_produced_this() -> None:
    generator, registry, _, _ = _generator(_FakeLLM(_response()))

    await generator.run([_item()])

    provenance = registry.registered[0].spec["provenance"]
    assert provenance["model"] == "qwen3:32b"
    assert provenance["prompt_version"] == 1
    assert provenance["literature_item_id"] == "arxiv:2401.00001"
    assert provenance["prior"]["year"] == 2006


async def test_a_registered_spec_is_one_the_engine_can_actually_run() -> None:
    """A proposal the backtester refuses is a dead row that looks like progress.
    This runs the engine's own hygiene check and builds the real strategy."""

    generator, registry, _, _ = _generator(_FakeLLM(_response()))

    await generator.run([_item()])

    spec = registry.registered[0].spec
    _validate_param_bounds(spec)
    strategy = CrossSectionalFactorStrategy.from_spec(spec["params"])
    assert strategy.factor == "low-volatility"
    assert strategy.lookback == 252


# --- side effects -------------------------------------------------------------


async def test_a_dry_run_calls_the_model_and_writes_nothing() -> None:
    generator, registry, gaps, literature = _generator(_FakeLLM(_response()), dry_run=True)

    report = await generator.run([_item()])

    assert report.count(OUTCOME_PROPOSED) == 1
    assert registry.registered == []
    assert gaps.gaps == []
    assert literature.marked == []


async def test_every_item_read_is_marked_with_its_outcome() -> None:
    """The half of a research funnel that normally evaporates: what the firm
    read and declined to act on."""

    llm = _FakeLLM(_response(), _response(prior=None))
    generator, _, _, literature = _generator(llm)

    await generator.run([_item("arxiv:1"), _item("arxiv:2")])

    assert [m[0] for m in literature.marked] == ["arxiv:1", "arxiv:2"]
    assert literature.marked[0][1] == OUTCOME_PROPOSED
    assert literature.marked[1][1] == OUTCOME_REFUSED


# --- reading the corpus -------------------------------------------------------


def test_held_identities_read_the_spec_not_the_name() -> None:
    """A strategy called 'momentum' that specs a reversal rule holds the
    reversal identity; trusting the name would wave a duplicate through."""

    record = _existing("S1", "cross-sectional-reversal")
    record = replace(record, name="Momentum!")

    assert held_identities([record]) == {"cross-sectional-reversal": "S1"}


def test_the_identity_of_a_factor_strategy_includes_its_factor() -> None:
    assert hypothesis_key("cross-sectional-factor", "low-volatility") != hypothesis_key(
        "cross-sectional-factor", "high-proximity"
    )
    assert hypothesis_key("cross-sectional-momentum", None) == "cross-sectional-momentum"


def test_a_factor_named_on_a_rule_that_ignores_it_is_dropped() -> None:
    """A spec recording a factor the engine never reads is a spec that lies
    about what it runs."""

    raw = parse_proposal(
        _item(), _response(rule="cross-sectional-momentum", factor="low-volatility"), "m"
    )

    assert raw is not None
    assert raw.factor is None


def test_the_record_builder_refuses_a_rule_it_has_no_template_for() -> None:
    raw = parse_proposal(_item(), _response(rule="cross-sectional-factor"), "m")
    assert raw is not None
    broken = replace(raw, rule="reference-trend")

    with pytest.raises(ValueError, match="no parameter template"):
        build_record(broken, _item())


# --- the CLI's hand-fed path --------------------------------------------------


def test_items_can_be_read_from_a_file_before_the_feed_exists(tmp_path: Any) -> None:
    """Tech Watcher does not read q-fin yet, so the prompt has to be reviewable
    against real abstracts without it."""

    path = tmp_path / "papers.json"
    path.write_text(
        json.dumps(
            [
                {
                    "item_id": "arxiv:2401.00001",
                    "source": "arxiv",
                    "title": "Illiquidity and stock returns",
                    "abstract": "We show that expected returns rise with illiquidity.",
                    "url": "https://arxiv.org/abs/2401.00001",
                    "published_at": "2024-01-02T00:00:00+00:00",
                    "category": "q-fin.PM",
                }
            ]
        ),
        encoding="utf-8",
    )

    items = items_from_file(path)

    assert items[0].item_id == "arxiv:2401.00001"
    assert items[0].published_at is not None
    assert items[0].category == "q-fin.PM"


def test_a_malformed_entry_is_a_hard_error_not_a_silent_skip(tmp_path: Any) -> None:
    """Skipping a bad entry would let a typo shrink the run to nothing while it
    still reported success."""

    path = tmp_path / "papers.json"
    path.write_text(json.dumps([{"item_id": "x", "title": "no abstract"}]), encoding="utf-8")

    with pytest.raises(SystemExit, match="abstract"):
        items_from_file(path)


def test_the_dsn_is_refused_rather_than_guessed(monkeypatch: Any) -> None:
    for name in (
        "HYPOTHESIS_GENERATOR_POSTGRES_DSN",
        "STRATEGY_EVALUATOR_POSTGRES_DSN",
        "TECH_WATCHER_POSTGRES_DSN",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(SystemExit, match="no Postgres DSN"):
        _dsn(None)


def test_an_empty_queue_says_so_rather_than_printing_nothing() -> None:
    assert "No capability gaps recorded" in render_queue([])


def test_the_queue_separates_what_to_build_from_what_to_buy() -> None:
    ranked = rank_gaps(
        [
            CapabilityGap(
                effect_name="amihud-illiquidity",
                kind=OUTCOME_MISSING_SCORER,
                missing=(),
                sketch="mean absolute return over dollar volume",
                citation=GapCitation("a", "t", None, "p"),
            ),
            CapabilityGap(
                effect_name="book-to-market",
                kind=OUTCOME_MISSING_DATA,
                missing=("book value of equity",),
                sketch="book equity over market equity",
                citation=GapCitation("b", "t", None, "p"),
            ),
        ]
    )

    text = render_queue(ranked)

    assert "2 effect(s) the engine cannot run, 1 of them buildable" in text
    assert "needs: book value of equity" in text
