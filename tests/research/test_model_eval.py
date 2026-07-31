"""Tests for the ADR-0009 model shadow-eval harness."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.research import model_eval_cli
from shrap.research.model_eval import (
    FAILURE_EMPTY,
    FAILURE_FENCED,
    FAILURE_MALFORMED,
    FAILURE_PROSE,
    FAILURE_WRONG_SHAPE,
    STRATUM_NEGATIVE,
    STRATUM_POSITIVE,
    STRATUM_UNSCORED,
    TASK_FILTER,
    CallResult,
    EvalItem,
    EvalPlan,
    build_eval_item,
    build_report,
    collect_disagreements,
    compute_metrics,
    compute_pairwise,
    diagnose_failure,
    distinct_errors,
    failure_breakdown,
    recoverable_count,
    registry_for_model,
    render_markdown,
    run_one,
    run_plan,
    stratified_sample,
)
from shrap.research.tech_watcher.filter import FILTER_SYSTEM_PROMPT

T0 = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 30, 12, 5, tzinfo=UTC)


def _row(item_id: str, relevant: bool | None, archetype: str | None = None) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "source": "sec-edgar",
        "kind": "8-K",
        "title": f"title {item_id}",
        "summary": "summary text",
        "incumbent_relevant": relevant,
        "incumbent_archetype": archetype,
    }


def _item(item_id: str, stratum: str = STRATUM_NEGATIVE) -> EvalItem:
    return EvalItem(
        item_id=item_id,
        stratum=stratum,
        prompt="p",
        system="s",
        incumbent_relevant=(stratum == STRATUM_POSITIVE) if stratum != STRATUM_UNSCORED else None,
        incumbent_archetype=None,
        display=f"[src] {item_id}",
    )


def _plan(models: tuple[str, ...], items: tuple[EvalItem, ...], repeats: int = 1) -> EvalPlan:
    return EvalPlan(
        task=TASK_FILTER,
        tier="local-classification",
        models=models,
        items=items,
        repeats=repeats,
        seed=7,
    )


def _result(model: str, item_id: str, relevant: bool | None, **kw: Any) -> CallResult:
    base: dict[str, Any] = {
        "repeat": 0,
        "latency_ms": 100.0,
        "raw": "{}",
        "parsed_ok": True,
        "archetype": "cost-curve" if relevant else None,
        "reason": "because",
        "error": None,
    }
    base.update(kw)
    return CallResult(model=model, item_id=item_id, relevant=relevant, **base)


# ---------------------------------------------------------------------------
# the production prompt, not a paraphrase
# ---------------------------------------------------------------------------


def test_eval_item_uses_the_live_filter_prompt() -> None:
    """A candidate scored on a paraphrase tells you about the paraphrase."""

    item = build_eval_item(_row("edgar:1", False))
    assert item.system == FILTER_SYSTEM_PROMPT
    assert "Recognition grammar:" in item.prompt
    assert "title edgar:1" in item.prompt


def test_incumbent_verdict_determines_the_stratum() -> None:
    assert build_eval_item(_row("a", True)).stratum == STRATUM_POSITIVE
    assert build_eval_item(_row("b", False)).stratum == STRATUM_NEGATIVE
    assert build_eval_item(_row("c", None)).stratum == STRATUM_UNSCORED


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------


def test_sampling_oversamples_positives_against_a_lopsided_corpus() -> None:
    """The corpus is ~99% negative; a uniform sample measures nothing."""

    corpus = [_item(f"pos-{i}", STRATUM_POSITIVE) for i in range(5)]
    corpus += [_item(f"neg-{i}", STRATUM_NEGATIVE) for i in range(500)]

    picked = stratified_sample(corpus, 10, seed=7)

    assert len(picked) == 10
    positives = [p for p in picked if p.stratum == STRATUM_POSITIVE]
    assert len(positives) == 5, "every available positive should be in a 10-item sample"


def test_sampling_is_deterministic_for_a_seed() -> None:
    corpus = [_item(f"neg-{i}") for i in range(100)]
    a = [i.item_id for i in stratified_sample(corpus, 12, seed=3)]
    b = [i.item_id for i in stratified_sample(corpus, 12, seed=3)]
    c = [i.item_id for i in stratified_sample(corpus, 12, seed=4)]
    assert a == b
    assert a != c


def test_sampling_falls_back_to_unscored_when_scored_items_run_out() -> None:
    corpus = [_item("pos-1", STRATUM_POSITIVE), _item("neg-1", STRATUM_NEGATIVE)]
    corpus += [_item(f"new-{i}", STRATUM_UNSCORED) for i in range(10)]
    picked = stratified_sample(corpus, 6, seed=1)
    assert len(picked) == 6
    assert any(p.stratum == STRATUM_UNSCORED for p in picked)


def test_sample_larger_than_corpus_returns_the_corpus() -> None:
    corpus = [_item("a"), _item("b")]
    assert len(stratified_sample(corpus, 50, seed=1)) == 2


# ---------------------------------------------------------------------------
# the plan is knowable before a call is spent
# ---------------------------------------------------------------------------


def test_call_budget_is_computed_before_anything_is_spent() -> None:
    plan = _plan(("a", "b", "c"), tuple(_item(f"i{n}") for n in range(20)), repeats=2)
    assert plan.call_budget == 120
    rendered = plan.render()
    assert "call budget: 120" in rendered
    assert "3 models x 20 items x 2 repeats" in rendered


# ---------------------------------------------------------------------------
# calling
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, responses: list[str | Exception]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        value = self._responses.pop(0)
        if isinstance(value, Exception):
            raise value

        class _R:
            content = value
            latency_ms = 42.0

        return _R()


async def test_a_valid_verdict_parses_and_records_latency() -> None:
    client = _FakeClient(['{"relevant": true, "archetype": "cost-curve", "reason": "unit cost"}'])
    result = await run_one(client, "m", _item("i1"), 0, "local-classification")
    assert (result.parsed_ok, result.relevant, result.archetype) == (True, True, "cost-curve")
    assert result.latency_ms == 42.0
    assert client.calls[0]["think"] is False, "bulk classification must not pay for thinking"
    assert client.calls[0]["json_mode"] is True


async def test_unparseable_output_is_a_schema_failure_not_a_verdict() -> None:
    """The production parser turns junk into not-relevant; the eval must be able
    to tell that apart from a model that genuinely said not-relevant."""

    junk = await run_one(_FakeClient(["I think this is relevant!"]), "m", _item("i1"), 0, "t")
    assert junk.parsed_ok is False

    genuine = await run_one(
        _FakeClient(['{"relevant": false, "archetype": null, "reason": "no evidence"}']),
        "m",
        _item("i1"),
        0,
        "t",
    )
    assert genuine.parsed_ok is True
    assert genuine.relevant is False


async def test_a_failing_call_is_recorded_not_raised() -> None:
    result = await run_one(_FakeClient([RuntimeError("401 unauthorized")]), "m", _item("i"), 0, "t")
    assert result.error is not None and "401" in result.error
    assert result.parsed_ok is False


async def test_run_plan_covers_every_model_item_repeat() -> None:
    plan = _plan(("m1", "m2"), (_item("a"), _item("b")), repeats=2)
    ok = '{"relevant": false, "archetype": null, "reason": "r"}'
    clients = {m: _FakeClient([ok] * 4) for m in plan.models}
    results = await run_plan(plan, lambda m: clients[m])
    assert len(results) == plan.call_budget == 8
    assert {(r.model, r.item_id, r.repeat) for r in results} == {
        (m, i, rep) for m in plan.models for i in ("a", "b") for rep in (0, 1)
    }


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def test_self_consistency_catches_a_model_that_contradicts_itself() -> None:
    plan = _plan(("steady", "flappy"), (_item("a"), _item("b")), repeats=2)
    results = [
        _result("steady", "a", True),
        _result("steady", "a", True, repeat=1),
        _result("steady", "b", False),
        _result("steady", "b", False, repeat=1),
        _result("flappy", "a", True),
        _result("flappy", "a", False, repeat=1),
        _result("flappy", "b", False),
        _result("flappy", "b", False, repeat=1),
    ]
    by_model = {m.model: m for m in compute_metrics(plan, results)}
    assert by_model["steady"].self_consistency == 1.0
    assert by_model["flappy"].self_consistency == 0.5


def test_schema_adherence_excludes_errored_calls_from_the_denominator() -> None:
    plan = _plan(("m",), (_item("a"), _item("b"), _item("c")))
    results = [
        _result("m", "a", True),
        _result("m", "b", False, parsed_ok=False),
        _result("m", "c", None, error="boom", parsed_ok=False),
    ]
    metrics = compute_metrics(plan, results)[0]
    assert metrics.errors == 1
    assert metrics.schema_adherence == 0.5, "1 of 2 non-errored calls parsed"


def test_agreement_with_incumbent_ignores_never_scored_items() -> None:
    plan = _plan(
        ("m",),
        (_item("p", STRATUM_POSITIVE), _item("n", STRATUM_NEGATIVE), _item("u", STRATUM_UNSCORED)),
    )
    results = [_result("m", "p", True), _result("m", "n", False), _result("m", "u", True)]
    assert compute_metrics(plan, results)[0].agreement_with_incumbent == 1.0


def test_self_consistency_is_none_with_a_single_repeat() -> None:
    plan = _plan(("m",), (_item("a"),), repeats=1)
    assert compute_metrics(plan, [_result("m", "a", True)])[0].self_consistency is None


def test_pairwise_agreement_is_computed_per_pair() -> None:
    plan = _plan(("a", "b"), (_item("i1"), _item("i2")))
    results = [
        _result("a", "i1", True),
        _result("b", "i1", True),
        _result("a", "i2", True),
        _result("b", "i2", False),
    ]
    assert compute_pairwise(plan, results)[("a", "b")] == 0.5


# ---------------------------------------------------------------------------
# disagreements — the only part a human reads
# ---------------------------------------------------------------------------


def test_disagreements_list_only_splits_and_puts_positives_first() -> None:
    plan = _plan(("a", "b"), (_item("agree"), _item("split", STRATUM_POSITIVE)))
    results = [
        _result("a", "agree", False),
        _result("b", "agree", False),
        _result("a", "split", True),
        _result("b", "split", False),
    ]
    found = collect_disagreements(plan, results)
    assert [d.item_id for d in found] == ["split"]
    assert found[0].stratum == STRATUM_POSITIVE
    assert {v[0] for v in found[0].verdicts} == {"a", "b"}


def test_errored_verdicts_are_not_counted_as_disagreement() -> None:
    plan = _plan(("a", "b"), (_item("i"),))
    results = [_result("a", "i", True), _result("b", "i", None, error="timeout")]
    assert collect_disagreements(plan, results) == []


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def test_report_warns_when_the_positive_class_is_too_thin_to_discriminate() -> None:
    """The first real run had 2 positives and said nothing about it."""

    plan = _plan(("a", "b"), (_item("p", STRATUM_POSITIVE), _item("n1"), _item("n2")))
    results = [_result(m, i, False) for m in ("a", "b") for i in ("p", "n1", "n2")]
    report = build_report(plan, results, T0, T1)
    assert any("incumbent-relevant item(s) in the sample" in n for n in report.notes)
    assert any("floor, not a ranking" in n for n in report.notes)


def test_report_warns_when_a_model_errored_and_says_what_the_error_was() -> None:
    plan = _plan(("a", "b"), (_item("p", STRATUM_POSITIVE), _item("n")))
    results = [
        _result("a", "p", True),
        _result("a", "n", False),
        _result("b", "p", True),
        _result("b", "n", None, error="401 unauthorized"),
    ]
    report = build_report(plan, results, T0, T1)
    assert any("errored on 1 of 2 calls" in n for n in report.notes)
    assert any("401 unauthorized" in n for n in report.notes)


def test_rendered_block_refuses_to_call_a_winner() -> None:
    """Agreement is not correctness, and the report must not imply otherwise."""

    plan = _plan(("a", "b"), (_item("p", STRATUM_POSITIVE),), repeats=2)
    results = [
        _result("a", "p", True),
        _result("a", "p", True, repeat=1),
        _result("b", "p", False),
        _result("b", "p", False, repeat=1),
    ]
    block = render_markdown(build_report(plan, results, T0, T1))
    assert "Agreement is not correctness" in block
    assert "Mike — adjudicate" in block
    assert "2026-07-30" in block
    assert "`a`" in block and "`b`" in block


# ---------------------------------------------------------------------------
# the shadow eval must stay a shadow
# ---------------------------------------------------------------------------


def test_eval_sql_never_writes_a_production_table() -> None:
    """The contamination this prevents would be invisible a month later."""

    statements = [
        model_eval_cli.CREATE_EVAL_RUNS_TABLE_SQL,
        model_eval_cli.CREATE_EVAL_RESULTS_TABLE_SQL,
        model_eval_cli.INSERT_EVAL_RUN_SQL,
        model_eval_cli.INSERT_EVAL_RESULT_SQL,
    ]
    forbidden = (
        "filter_verdict_history",
        "news_verdict_history",
        "filing_verdict_history",
        "world_changers",
    )
    for sql in statements:
        lowered = sql.lower()
        for table in forbidden:
            assert table not in lowered, f"eval SQL touches {table}"
        assert "research.model_eval_" in lowered

    corpus_sql = model_eval_cli.SELECT_EVAL_CORPUS_SQL.strip().lower()
    assert corpus_sql.startswith("select"), "the corpus read must be read-only"
    for verb in ("update ", "insert ", "delete "):
        assert verb not in corpus_sql


def test_registry_override_swaps_only_the_model_under_test() -> None:
    env = {
        "SHRAP_LLM_OLLAMA_URL": "https://ollama.com",
        "OLLAMA_API_KEY": "secret",
        "SHRAP_LLM_LOCAL_CLASSIFICATION_MODEL": "gpt-oss:20b-cloud",
    }
    binding = registry_for_model(env, "local-classification", "kimi-k2.5").resolve(
        "local-classification"
    )
    assert binding.model == "kimi-k2.5"
    assert binding.base_url == "https://ollama.com"
    assert binding.api_key == "secret", "must exercise the same auth path as the agents"


def test_cli_rejects_a_single_model() -> None:
    parser = model_eval_cli._build_parser()
    args = parser.parse_args(["--models", "only-one"])
    models = tuple(m.strip() for m in args.models.split(",") if m.strip())
    assert len(models) < 2, "main() errors on this; an eval needs an incumbent and a candidate"


def test_run_id_and_models_serialize_for_storage() -> None:
    assert json.loads(json.dumps(["a", "b"])) == ["a", "b"]


@pytest.mark.parametrize("bad", [0, -1])
def test_percentile_helpers_survive_degenerate_input(bad: int) -> None:
    plan = _plan(("m",), (_item("a"),))
    metrics = compute_metrics(plan, [_result("m", "a", None, error="x")])[0]
    assert metrics.latency_p50_ms == 0.0
    assert metrics.schema_adherence == 0.0
    assert bad <= 0


# ---------------------------------------------------------------------------
# a model must not be able to agree by failing (found in the first real run)
# ---------------------------------------------------------------------------


def _lopsided_plan(models: tuple[str, ...]) -> EvalPlan:
    """The shape of the 2026-07-30 runs: 2 positives, 18 negatives."""

    items = tuple(
        [_item(f"p{i}", STRATUM_POSITIVE) for i in range(2)]
        + [_item(f"n{i}", STRATUM_NEGATIVE) for i in range(18)]
    )
    return _plan(models, items)


def test_unparsed_answers_are_excluded_from_agreement() -> None:
    """glm-5.2 scored 10% schema and 90% "agreement" on the same 20 items.

    The production parser turns junk into relevant=False, and the incumbent said
    not-relevant on 18 of 20 — so a model that parses almost nothing agrees with
    almost everything. That number was a property of the metric, not the model.
    """

    plan = _lopsided_plan(("junk",))
    results = [
        _result("junk", item.item_id, False, parsed_ok=False, raw="Sure! Here's my analysis:")
        for item in plan.items
    ]

    metrics = compute_metrics(plan, results)[0]

    assert metrics.schema_adherence == 0.0
    assert metrics.judged_calls == 0
    assert metrics.agreement_with_incumbent is None, "no parsed answers = no agreement to report"
    assert metrics.relevant_rate is None


def test_agreement_is_computed_only_over_answers_that_parsed() -> None:
    plan = _plan(("m",), (_item("p", STRATUM_POSITIVE), _item("n1"), _item("n2"), _item("n3")))
    results = [
        _result("m", "p", True),
        _result("m", "n1", False),
        _result("m", "n2", False, parsed_ok=False),
        _result("m", "n3", False, parsed_ok=False),
    ]
    metrics = compute_metrics(plan, results)[0]
    assert metrics.judged_calls == 2
    assert metrics.agreement_with_incumbent == 1.0
    assert metrics.unparsed == 2


def test_pairwise_agreement_ignores_items_either_model_failed_to_parse() -> None:
    """Two models 'agreeing' on an item neither could parse is not agreement."""

    plan = _plan(("a", "b"), (_item("i1"), _item("i2")))
    results = [
        _result("a", "i1", True),
        _result("b", "i1", False),
        _result("a", "i2", False, parsed_ok=False),
        _result("b", "i2", False, parsed_ok=False),
    ]
    assert compute_pairwise(plan, results)[("a", "b")] == 0.0


def test_disagreement_rows_contain_only_real_verdicts() -> None:
    """The 2026-07-30 run printed 'unparseable filter response' as a verdict."""

    plan = _plan(("a", "b"), (_item("i", STRATUM_POSITIVE),))
    results = [_result("a", "i", True), _result("b", "i", False, parsed_ok=False)]
    assert collect_disagreements(plan, results) == []


def test_self_consistency_is_measured_only_on_parsed_pairs() -> None:
    plan = _plan(("m",), (_item("a"), _item("b")), repeats=2)
    results = [
        _result("m", "a", True),
        _result("m", "a", True, repeat=1),
        _result("m", "b", False, parsed_ok=False),
        _result("m", "b", True, parsed_ok=False, repeat=1),
    ]
    assert compute_metrics(plan, results)[0].self_consistency == 1.0


# ---------------------------------------------------------------------------
# failure diagnosis — is a failing model rescuable, or is it out?
# ---------------------------------------------------------------------------


def test_markdown_fenced_json_is_recoverable() -> None:
    """Our defect, not the model's: one strip in the parser would take it."""

    assert diagnose_failure('```json\n{"relevant": false, "archetype": null}\n```') == (
        FAILURE_FENCED,
        True,
    )


def test_bare_fence_without_a_language_tag_is_recoverable() -> None:
    assert diagnose_failure('```\n{"relevant": true}\n```') == (FAILURE_FENCED, True)


def test_prose_wrapping_a_json_object_is_recoverable() -> None:
    assert diagnose_failure('My verdict: {"relevant": false} — hope that helps') == (
        FAILURE_PROSE,
        True,
    )


def test_pure_prose_is_not_recoverable() -> None:
    assert diagnose_failure("This item is not relevant to any archetype.") == (
        FAILURE_PROSE,
        False,
    )


def test_empty_and_malformed_are_distinguished() -> None:
    assert diagnose_failure("   ") == (FAILURE_EMPTY, False)
    assert diagnose_failure('{"relevant": fal') == (FAILURE_MALFORMED, False)


def test_a_json_array_is_wrong_shape_not_prose() -> None:
    assert diagnose_failure('[{"relevant": false}]') == (FAILURE_WRONG_SHAPE, False)


def test_breakdown_and_recoverable_count_skip_healthy_and_errored_calls() -> None:
    results = [
        _result("m", "a", False),
        _result("m", "b", False, parsed_ok=False, raw='```json\n{"relevant": false}\n```'),
        _result("m", "c", False, parsed_ok=False, raw="I think not."),
        _result("m", "d", None, error="timeout", parsed_ok=False, raw=""),
    ]
    assert failure_breakdown(results) == {FAILURE_FENCED: 1, FAILURE_PROSE: 1}
    assert recoverable_count(results) == 1


def test_low_schema_adherence_warns_and_says_whether_a_fix_would_help() -> None:
    plan = _plan(("fenced",), (_item("a"), _item("b"), _item("c")))
    results = [
        _result("fenced", i, False, parsed_ok=False, raw='```json\n{"relevant": false}\n```')
        for i in ("a", "b", "c")
    ]
    notes = build_report(plan, results, T0, T1).notes
    assert any("parsed only 0%" in n for n in notes)
    assert any("our defect, not the model's" in n for n in notes)


def test_unrecoverable_failure_says_the_model_is_out() -> None:
    plan = _plan(("prosey",), (_item("a"), _item("b"), _item("c")))
    results = [
        _result("prosey", i, False, parsed_ok=False, raw="Not relevant, in my view.")
        for i in ("a", "b", "c")
    ]
    notes = build_report(plan, results, T0, T1).notes
    assert any("cannot hold the strict-JSON contract" in n for n in notes)


def test_rendered_table_shows_the_judged_denominator() -> None:
    """A rate over 2 answers and a rate over 20 look identical as a percentage."""

    plan = _lopsided_plan(("solid", "junk"))
    results = [_result("solid", i.item_id, False) for i in plan.items]
    results += [_result("junk", i.item_id, False, parsed_ok=False, raw="nope") for i in plan.items]
    block = render_markdown(build_report(plan, results, T0, T1))
    assert "| judged |" in block
    assert "20/20" in block
    assert "0/20" in block
    assert "Unparsed answers, by cause:" in block


def test_distinct_errors_dedupes_and_keeps_first_seen_order() -> None:
    results = [
        _result("m", "a", None, error="model 'qwen3.6' not found"),
        _result("m", "b", None, error="model 'qwen3.6' not found"),
        _result("m", "c", None, error="429 rate limited"),
    ]
    assert distinct_errors(results) == ("model 'qwen3.6' not found", "429 rate limited")


def test_a_model_that_errors_on_every_call_is_named_a_routing_failure() -> None:
    """kimi-k3:cloud and qwen3.6 both returned 20/20 errors and a row of zeroes.

    A row of zeroes reads like a quality result. It is not one — no verdict was
    ever produced — and the message says which of the two likely causes it was.
    """

    plan = _plan(("ghost",), (_item("a"), _item("b")))
    results = [_result("ghost", i, None, error="model 'ghost' not found") for i in ("a", "b")]
    notes = build_report(plan, results, T0, T1).notes
    assert any("failed on every call" in n for n in notes)
    assert any("routing failure, not a quality result" in n for n in notes)
    assert any("ghost' not found" in n for n in notes)


def test_error_messages_reach_the_rendered_block() -> None:
    plan = _plan(("ghost", "ok"), (_item("a"),))
    results = [
        _result("ghost", "a", None, error="404 model not found"),
        _result("ok", "a", False),
    ]
    block = render_markdown(build_report(plan, results, T0, T1))
    assert "Call errors, by model:" in block
    assert "404 model not found" in block


def test_an_all_errored_model_gets_the_routing_note_and_not_a_schema_note() -> None:
    """A model that never answered has no schema adherence to report."""

    plan = _plan(("ghost",), (_item("a"), _item("b")))
    results = [_result("ghost", i, None, error="404 not found") for i in ("a", "b")]
    notes = build_report(plan, results, T0, T1).notes
    assert any("routing failure" in n for n in notes)
    assert not any("parsed only" in n for n in notes)
