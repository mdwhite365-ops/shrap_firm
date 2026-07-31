"""Tests for the archetype bar experiment (timeline card 1.4).

Two things carry the weight. The **isolation** — an experiment that fed its
candidate verdicts back into the corpus would corrupt every later measurement
invisibly, exactly as the shadow eval's test pins. And the **parsers**, because
a bar that mis-reads its own output would produce an admit rate that is a
property of this module rather than of the taxonomy, which is the defect that
cost the model eval two runs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shrap.research.bar_experiment import (
    BAR_EVIDENCE,
    BAR_INCUMBENT,
    BAR_SIGNAL,
    CONTROL_ITEM_IDS,
    HARD_SOURCES,
    BarCall,
    ExperimentReport,
    all_bars,
    bars_by_key,
    cross_bar_agreement,
    parse_evidence_response,
    parse_signal_response,
    render_markdown,
    run_bar,
    signal_catalogue,
    signal_prompt_block,
    stratified_limit,
    summarize,
)
from shrap.research.tech_watcher.archetypes import ARCHETYPES
from shrap.research.tech_watcher.filter import FILTER_SYSTEM_PROMPT, UnfilteredItem


def _item(item_id: str = "sec-edgar:1", source: str = "sec-edgar") -> UnfilteredItem:
    return UnfilteredItem(
        item_id=item_id,
        source=source,
        kind="10-Q",
        title="CALIX, INC (0001406666) (Filer)",
        summary="Routine quarterly filing.",
    )


class FakeResult:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeClient:
    def __init__(self, content: str = '{"relevant": false, "archetype": null}') -> None:
        self.content = content
        self.calls: list[tuple[str, str, str]] = []

    async def complete(
        self, tier: str, prompt: str, *, system: str, json_mode: bool, think: bool
    ) -> FakeResult:
        self.calls.append((tier, system, prompt))
        return FakeResult(self.content)


class ExplodingClient:
    async def complete(
        self, tier: str, prompt: str, *, system: str, json_mode: bool, think: bool
    ) -> FakeResult:
        raise ConnectionError("ollama said no")


# --- isolation ------------------------------------------------------------------


def test_the_experiment_never_writes_to_production_tables() -> None:
    """The shadow eval's commitment, restated for this harness.

    An experiment that mutated ``filter_result`` would put candidate verdicts
    into the corpus the next experiment reads, and the contamination would be
    invisible a month later.
    """

    root = Path(__file__).resolve().parents[2] / "src" / "shrap" / "research"
    source = (root / "bar_experiment.py").read_text() + (root / "bar_experiment_cli.py").read_text()

    # Match write *statements*, not mentions — the module docstrings name the
    # production tables precisely in order to say they are never written, and a
    # substring check would fail on its own promise.
    written = {
        match.group(2)
        for match in re.finditer(
            r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([a-z_]+\.[a-z_]+)",
            source,
            re.IGNORECASE,
        )
    }

    assert written == {
        "research.bar_experiment_runs",
        "research.bar_experiment_results",
    }, f"experiment writes tables it must not: {written}"


def test_the_control_bar_is_the_production_prompt_not_a_paraphrase() -> None:
    incumbent = next(bar for bar in all_bars() if bar.key == BAR_INCUMBENT)

    assert incumbent.system_prompt is FILTER_SYSTEM_PROMPT


def test_the_control_items_are_the_two_v3_survivors() -> None:
    assert CONTROL_ITEM_IDS == ("arxiv:2607.20349v1", "arxiv:2607.20083v1")


# --- the signal catalogue -------------------------------------------------------


def test_every_signal_of_every_archetype_is_addressable() -> None:
    catalogue = signal_catalogue()

    assert len(catalogue) == sum(len(a.signals) for a in ARCHETYPES)
    assert len({ref.signal_id for ref in catalogue}) == len(catalogue)
    for ref in catalogue:
        assert ref.signal_id.startswith(f"{ref.archetype}:")


def test_the_signal_prompt_keeps_the_impostors_in_view() -> None:
    """Bar C relaxes the question, not the bar — impostors travel with it."""

    block = signal_prompt_block()

    assert "hydrogen-economy-shaped curves that never actually cross" in block
    assert all(ref.signal_id in block for ref in signal_catalogue())


# --- parsers --------------------------------------------------------------------


def test_bar_c_rejects_an_invented_signal_id() -> None:
    """A model inventing a plausible id would manufacture evidence."""

    verdict = parse_signal_response("x", '{"signal": "cost-curve:99", "fact": "made up"}')

    assert verdict.admitted is False
    assert verdict.label is None


def test_bar_c_accepts_a_real_signal_id_and_keeps_the_fact() -> None:
    real = signal_catalogue()[0].signal_id

    verdict = parse_signal_response("x", f'{{"signal": "{real}", "fact": "capex up 40%"}}')

    assert (verdict.admitted, verdict.label, verdict.reason) == (True, real, "capex up 40%")


def test_bar_c_survives_a_markdown_fence() -> None:
    """#172's defect, not repeated in a second parser."""

    real = signal_catalogue()[0].signal_id

    verdict = parse_signal_response("x", f'```json\n{{"signal": "{real}", "fact": "f"}}\n```')

    assert verdict.admitted is True


def test_bar_c_null_signal_is_a_clean_rejection_not_a_parse_failure() -> None:
    verdict = parse_signal_response("x", '{"signal": null, "fact": "routine cover page"}')

    assert (verdict.admitted, verdict.parsed_ok) == (False, True)
    assert verdict.reason == "routine cover page"


def test_unparseable_output_is_marked_as_such_in_both_shapes() -> None:
    """Distinguishing a rejection from a failure is what #171 was about."""

    assert parse_signal_response("x", "I think...").parsed_ok is False
    assert parse_evidence_response("x", "I think...").parsed_ok is False


def test_bar_c_verdicts_report_the_archetype_behind_the_signal() -> None:
    """Without this collapse, C could never agree with A or B on anything."""

    ref = next(r for r in signal_catalogue() if r.archetype == "cost-curve")

    verdict = parse_signal_response("x", f'{{"signal": "{ref.signal_id}", "fact": "f"}}')

    assert verdict.archetype == "cost-curve"


# --- running --------------------------------------------------------------------


async def test_a_bar_scores_every_item_and_records_the_prompt_it_used() -> None:
    bar = next(b for b in all_bars() if b.key == BAR_EVIDENCE)
    client = FakeClient()

    calls = await run_bar(bar, client, [_item("a"), _item("b")], "local-classification")

    assert len(calls) == 2
    assert {c.item.item_id for c in calls} == {"a", "b"}
    assert all(system == bar.system_prompt for _, system, _ in client.calls)


async def test_a_failed_call_is_recorded_not_raised() -> None:
    """One bad item must not lose the whole pass's work."""

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)

    calls = await run_bar(bar, ExplodingClient(), [_item()], "local-classification")

    assert len(calls) == 1
    assert calls[0].verdict is None
    assert "ConnectionError" in (calls[0].error or "")


# --- summary and report ---------------------------------------------------------


def _call(bar: str, item_id: str, source: str, admitted: bool, label: str | None) -> BarCall:
    from shrap.research.bar_experiment import BarVerdict

    return BarCall(
        bar=bar,
        item=_item(item_id, source),
        verdict=BarVerdict(item_id, admitted, label, "because"),
        latency_ms=1.0,
    )


def test_the_hard_leg_count_is_what_ki_009_needs() -> None:
    """arXiv-only admits leave triangulation exactly as blocked as before."""

    bar = next(b for b in all_bars() if b.key == BAR_SIGNAL)
    calls = [
        _call(bar.key, "a", "arxiv", True, "cost-curve:0"),
        _call(bar.key, "b", "sec-edgar", True, "cost-curve:0"),
        _call(bar.key, "c", "arxiv", False, None),
    ]

    summary = summarize(bar, calls)

    assert len(summary.admitted) == 2
    assert summary.hard_source_admits == 1
    assert "sec-edgar" in HARD_SOURCES and "arxiv" not in HARD_SOURCES


def test_errors_are_excluded_from_the_admit_rate_denominator() -> None:
    """A routing failure is not a rejection — #171's lesson, kept."""

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    calls = [
        _call(bar.key, "a", "sec-edgar", True, "cost-curve"),
        BarCall(bar=bar.key, item=_item("b"), verdict=None, latency_ms=1.0, error="boom"),
    ]

    summary = summarize(bar, calls)

    assert summary.errors == 1
    assert summary.admit_rate == pytest.approx(1.0)  # 1 admitted of 1 judged, not of 2


def test_control_items_are_tracked_per_bar() -> None:
    bar = next(b for b in all_bars() if b.key == BAR_EVIDENCE)
    calls = [_call(bar.key, CONTROL_ITEM_IDS[0], "arxiv", True, "cost-curve")]

    assert summarize(bar, calls).control_admitted == (CONTROL_ITEM_IDS[0],)


def test_cross_bar_agreement_counts_shared_admits() -> None:
    summaries = [
        summarize(
            next(b for b in all_bars() if b.key == BAR_INCUMBENT),
            [_call(BAR_INCUMBENT, "a", "arxiv", True, "cost-curve")],
        ),
        summarize(
            next(b for b in all_bars() if b.key == BAR_EVIDENCE),
            [
                _call(BAR_EVIDENCE, "a", "arxiv", True, "cost-curve"),
                _call(BAR_EVIDENCE, "b", "arxiv", True, "cost-curve"),
            ],
        ),
    ]

    # Pairs are keyed in sorted order, so A comes first.
    assert cross_bar_agreement(summaries)[(BAR_INCUMBENT, BAR_EVIDENCE)] == 1


def test_the_report_lists_admitted_items_rather_than_only_counting_them() -> None:
    """The deliverable Mike rules on is the list; a rate alone cannot be read."""

    bar = next(b for b in all_bars() if b.key == BAR_EVIDENCE)
    summary = summarize(bar, [_call(bar.key, "a", "sec-edgar", True, "cost-curve")])
    report = ExperimentReport(
        corpus_size=1,
        tier="local-classification",
        model="qwen3.5:397b",
        summaries=(summary,),
        started_at="2026-08-01T00:00:00+00:00",
        finished_at="2026-08-01T00:05:00+00:00",
    )

    block = render_markdown(report)

    assert "CALIX, INC" in block  # the item itself, not just a number
    assert "because" in block  # the model's stated reason
    assert "An admit rate is not a score" in block
    assert "Ruling:" in block


def test_an_unknown_bar_key_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown bar"):
        bars_by_key(["D-invented"])


# --- what the 2026-07-31 calibration exposed ------------------------------------


def _corpus() -> list[UnfilteredItem]:
    """A corpus shaped like the real one: arXiv-heavy, sorted so arXiv leads."""

    items = [_item(f"arxiv:{i:04d}", "arxiv") for i in range(1262)]
    items += [_item(f"doe-newsroom:{i:04d}", "doe-newsroom") for i in range(18)]
    items += [_item(f"federal-register:{i:04d}", "federal-register") for i in range(117)]
    items += [_item(f"sec-edgar:{i:04d}", "sec-edgar") for i in range(3699)]
    items += [_item(f"usaspending:{i:04d}", "usaspending") for i in range(125)]
    return sorted(items, key=lambda i: i.item_id)


def test_a_head_slice_would_have_been_all_arxiv() -> None:
    """The defect itself, pinned so nobody reintroduces items[:limit]."""

    assert {item.source for item in _corpus()[:600]} == {"arxiv"}


def test_a_limited_run_keeps_every_source() -> None:
    picked = stratified_limit(_corpus(), 600)

    assert {item.source for item in picked} == {
        "arxiv",
        "doe-newsroom",
        "federal-register",
        "sec-edgar",
        "usaspending",
    }
    assert len(picked) <= 600


def test_the_hard_legs_dominate_a_limited_run_because_they_dominate_the_corpus() -> None:
    """sec-edgar is 71% of the corpus, so it should be ~71% of any sample."""

    picked = stratified_limit(_corpus(), 600)
    edgar = sum(1 for item in picked if item.source == "sec-edgar")

    assert 0.6 <= edgar / len(picked) <= 0.8


def test_a_tiny_source_is_not_rounded_out_of_existence() -> None:
    """doe-newsroom has 18 items and carries DQ-006's named false negative."""

    picked = stratified_limit(_corpus(), 100)

    assert any(item.source == "doe-newsroom" for item in picked)


def test_the_control_items_are_always_in_a_limited_run() -> None:
    """A run that silently omits its own control cannot report on it."""

    corpus = sorted(
        [*_corpus(), *[_item(cid, "arxiv") for cid in CONTROL_ITEM_IDS]],
        key=lambda i: i.item_id,
    )

    picked = {item.item_id for item in stratified_limit(corpus, 50)}

    assert set(CONTROL_ITEM_IDS) <= picked


def test_a_limit_above_the_corpus_returns_everything() -> None:
    corpus = _corpus()

    assert stratified_limit(corpus, 99_999) == corpus


def test_a_run_that_saw_no_hard_leg_items_says_so_loudly() -> None:
    """`hard-leg 0` out of 0 scored is not a result, and read like one."""

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    summary = summarize(bar, [_call(bar.key, "a", "arxiv", False, None)])
    report = ExperimentReport(
        corpus_size=1,
        tier="local-classification",
        model="qwen3.5:397b",
        summaries=(summary,),
        started_at="2026-08-01T00:00:00+00:00",
        finished_at="2026-08-01T00:05:00+00:00",
    )

    assert summary.hard_source_scored == 0
    assert "scored no hard-leg items at all" in render_markdown(report)


def test_a_scored_and_rejected_control_reads_differently_from_an_unseen_one() -> None:
    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)

    rejected = summarize(bar, [_call(bar.key, CONTROL_ITEM_IDS[0], "arxiv", False, None)])
    assert rejected.control_rejected == (CONTROL_ITEM_IDS[0],)
    assert rejected.controls_unseen == (CONTROL_ITEM_IDS[1],)

    unseen = summarize(bar, [_call(bar.key, "unrelated", "arxiv", False, None)])
    assert unseen.controls_unseen == CONTROL_ITEM_IDS
    assert unseen.control_rejected == ()
