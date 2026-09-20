"""Tests for the archetype bar experiment (timeline card 1.4).

Two things carry the weight. The **isolation** — an experiment that fed its
candidate verdicts back into the corpus would corrupt every later measurement
invisibly, exactly as the shadow eval's test pins. And the **parsers**, because
a bar that mis-reads its own output would produce an admit rate that is a
property of this module rather than of the taxonomy, which is the defect that
cost the model eval two runs.
"""

from __future__ import annotations

import contextlib
import io
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from shrap.llm.ollama_usage import UsageSnapshot, WindowUsage
from shrap.research.bar_experiment import (
    BAR_EVIDENCE,
    BAR_INCUMBENT,
    BAR_SIGNAL,
    CONTROL_ITEM_IDS,
    HARD_SOURCES,
    MAX_CONSECUTIVE_ERRORS,
    QUOTA_CHECK_MAX,
    BarCall,
    ExperimentReport,
    QuotaDecision,
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
from shrap.research.bar_experiment_cli import (
    INSERT_RESULT_SQL,
    INSERT_RUN_SQL,
    SELECT_CORPUS_SQL,
    build_report,
    load_corpus,
    render_plan,
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
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        think: bool | None = None,
        task: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
    ) -> FakeResult:
        self.calls.append((tier, system, prompt))
        return FakeResult(self.content)


class ExplodingClient:
    async def complete(
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        think: bool | None = None,
        task: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
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


# ---------------------------------------------------------------------------
# Replaying an earlier run's item set
#
# The 2026-07-31 control scored 599 items on `qwen3.5:397b`. The corpus is now
# 21,231, so a fresh sample compared against that run varies the model AND the
# corpus at once — the confound #247 exists to warn about. Replaying holds the
# corpus fixed and leaves the model as the only difference.
# ---------------------------------------------------------------------------


class _ReplayConn:
    def __init__(
        self,
        corpus: list[dict[str, Any]],
        replay_ids: list[str],
        error_ids: list[str] | None = None,
        scored_ids: list[str] | None = None,
    ) -> None:
        self._corpus = corpus
        self._replay_ids = replay_ids
        self._error_ids = error_ids or []
        self._scored_ids = scored_ids or []
        self.queries: list[str] = []

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.queries.append(sql)
        if "bar_experiment_results" in sql:
            if "error IS NULL" in sql:
                return [{"item_id": i} for i in self._scored_ids]
            ids = self._error_ids if "error IS NOT NULL" in sql else self._replay_ids
            return [{"item_id": i} for i in ids]
        return self._corpus


class _ReplayPool:
    def __init__(self, conn: _ReplayConn) -> None:
        self.conn = conn

    def acquire(self) -> Any:
        conn = self.conn

        class _Ctx:
            async def __aenter__(self) -> _ReplayConn:
                return conn

            async def __aexit__(self, *exc: object) -> None:
                return None

        return _Ctx()


def _corpus_row(item_id: str, source: str, document_text: str | None = None) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "source": source,
        "kind": None,
        "title": f"title for {item_id}",
        "summary": "a summary",
        "document_text": document_text,
    }


async def test_replay_selects_exactly_the_earlier_run_s_items() -> None:
    corpus = [
        _corpus_row("arxiv:1", "arxiv"),
        _corpus_row("edgar:1", "sec-edgar"),
        _corpus_row("edgar:2", "sec-edgar"),
        _corpus_row("doe:1", "doe-newsroom"),
    ]
    pool = _ReplayPool(_ReplayConn(corpus, ["edgar:1", "doe:1"]))

    items = await load_corpus(pool, None, "01OLDRUN")

    assert sorted(i.item_id for i in items) == ["doe:1", "edgar:1"]


async def test_replay_ignores_the_limit_rather_than_resampling() -> None:
    """A stratified sample of a replayed set is not the replayed set."""

    corpus = [_corpus_row(f"edgar:{n}", "sec-edgar") for n in range(10)]
    pool = _ReplayPool(_ReplayConn(corpus, [f"edgar:{n}" for n in range(6)]))

    items = await load_corpus(pool, 2, "01OLDRUN")

    assert len(items) == 6


async def test_replaying_a_run_with_no_results_is_an_error_not_an_empty_run() -> None:
    """An empty corpus would otherwise produce a confident report over nothing."""

    pool = _ReplayPool(_ReplayConn([_corpus_row("arxiv:1", "arxiv")], []))

    with pytest.raises(SystemExit, match="no recorded results"):
        await load_corpus(pool, None, "01MISSING")


async def test_without_replay_the_corpus_is_unchanged() -> None:
    corpus = [_corpus_row(f"edgar:{n}", "sec-edgar") for n in range(4)]
    pool = _ReplayPool(_ReplayConn(corpus, []))

    items = await load_corpus(pool, None, None)

    assert len(items) == 4


# ---------------------------------------------------------------------------
# Resuming a run a spent quota stopped
#
# 2026-09-20: a 599-item replay hit Ollama's session cap at item 192 and made
# 407 more calls it already knew would be refused, writing every one as an error
# row. Two defects in one event — the bar did not stop, and there was no way to
# finish the run without paying for the 192 again.
# ---------------------------------------------------------------------------


async def test_resume_selects_only_the_items_that_errored() -> None:
    corpus = [_corpus_row(f"edgar:{n}", "sec-edgar") for n in range(5)]
    pool = _ReplayPool(
        _ReplayConn(corpus, replay_ids=[], error_ids=["edgar:3", "edgar:4"]),
    )

    items = await load_corpus(pool, None, None, "01STALLED")

    assert sorted(i.item_id for i in items) == ["edgar:3", "edgar:4"]


async def test_resuming_a_clean_run_is_an_error_not_a_no_op() -> None:
    """A run with no errored rows is finished. Silently scoring nothing would
    print a confident report over an empty corpus."""

    pool = _ReplayPool(_ReplayConn([_corpus_row("edgar:1", "sec-edgar")], [], []))

    with pytest.raises(SystemExit, match="no errored results"):
        await load_corpus(pool, None, None, "01CLEAN")


def test_a_resume_never_overwrites_a_recorded_verdict() -> None:
    """The conflict clause heals errored rows and only errored rows.

    A resume writes into the ORIGINAL run id so the run ends as one comparable
    set. Without the guard it would also replace verdicts measured hours earlier,
    possibly under a different model — a recomputation overwriting a recorded
    fact, which is this project's oldest defect shape (#192-#199, #245).
    """

    assert "ON CONFLICT (run_id, bar, item_id) DO UPDATE" in INSERT_RESULT_SQL
    assert "WHERE research.bar_experiment_results.error IS NOT NULL" in INSERT_RESULT_SQL


# ---------------------------------------------------------------------------
# The plan states the allowance, not just the spend
# ---------------------------------------------------------------------------


def test_the_plan_names_both_usage_windows() -> None:
    """Printing a completion count without the allowance it is drawn from is how
    a 599-item run was planned against a weekly window with room to spare and a
    session window with none."""

    items = [UnfilteredItem("edgar:1", "sec-edgar", None, "t", "s")]
    bars = bars_by_key(["A-incumbent"])
    snapshot = UsageSnapshot(
        session=WindowUsage("session", 0.95, 900),
        weekly=WindowUsage("weekly", 0.20, 1200),
    )

    plan = render_plan(items, bars, snapshot)

    assert "session 95.0% used" in plan
    assert "weekly 20.0% used" in plan


def test_an_unreadable_meter_says_so_rather_than_printing_nothing() -> None:
    items = [UnfilteredItem("edgar:1", "sec-edgar", None, "t", "s")]

    plan = render_plan(items, bars_by_key(["A-incumbent"]), None)

    assert "unavailable" in plan


# ---------------------------------------------------------------------------
# A run of failures is not a failure
# ---------------------------------------------------------------------------


class FlakyThenDeadClient:
    """Succeeds `healthy` times, then fails forever — a quota running out."""

    def __init__(self, healthy: int) -> None:
        self.healthy = healthy
        self.attempts = 0

    async def complete(
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        think: bool | None = None,
        task: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
    ) -> FakeResult:
        self.attempts += 1
        if self.attempts > self.healthy:
            raise ConnectionError("ollama returned 429: session usage limit")
        return FakeResult('{"relevant": false, "archetype": null}')


async def test_a_bar_stops_after_a_run_of_errors_instead_of_burning_the_corpus() -> None:
    """2026-09-20: a 599-item replay hit Ollama's session cap at item 192 and
    made 407 more calls it already knew would be refused, writing every one as an
    error row. Five in a row is systemic — a spent quota, a dead endpoint, a
    retired model — and every further call spends the account's allowance to
    learn nothing."""

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    client = FlakyThenDeadClient(healthy=3)
    items = [_item(f"i{n}") for n in range(50)]

    calls = await run_bar(bar, client, items, "local-classification", max_consecutive_errors=5)

    assert client.attempts == 8, "3 good then 5 consecutive errors, then stop"
    assert len(calls) == 8
    assert sum(1 for c in calls if c.error) == 5


async def test_the_items_behind_the_wall_are_left_unwritten() -> None:
    """Not recorded as errors. An item that was never attempted and an item that
    failed are different facts, and a resume needs to tell them apart."""

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    items = [_item(f"i{n}") for n in range(50)]

    calls = await run_bar(bar, FlakyThenDeadClient(healthy=0), items, "local-classification")

    scored_ids = {c.item.item_id for c in calls}
    assert scored_ids == {f"i{n}" for n in range(MAX_CONSECUTIVE_ERRORS)}


async def test_scattered_failures_do_not_stop_a_bar() -> None:
    """The counter resets on success. One flaky item in ten must not end a run
    that is otherwise working — that is the behaviour `test_a_failed_call_is_
    recorded_not_raised` protects, and the abort must not break it."""

    class EveryOtherClient:
        def __init__(self) -> None:
            self.attempts = 0

        async def complete(self, tier: str, prompt: str, **kwargs: Any) -> FakeResult:
            self.attempts += 1
            if self.attempts % 2 == 0:
                raise ConnectionError("transient")
            return FakeResult('{"relevant": false, "archetype": null}')

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    items = [_item(f"i{n}") for n in range(20)]

    calls = await run_bar(bar, EveryOtherClient(), items, "local-classification")

    assert len(calls) == 20


async def test_a_long_bar_rechecks_the_allowance_mid_flight() -> None:
    """Checking only before the first item proves there was room to START, which
    is the moment the check matters least. A 407-item bar can spend the whole
    window mid-flight and starve the always-on agents that share the account —
    the exact fault the guard exists to prevent."""

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    items = [_item(f"i{n}") for n in range(30)]

    async def spent_after_the_first_check(scored: int) -> QuotaDecision:
        return QuotaDecision("session 100.0% used")

    calls = await run_bar(
        bar,
        FakeClient(),
        items,
        "local-classification",
        quota_check=spent_after_the_first_check,
        quota_check_every=10,
    )

    assert len(calls) == 10, "stops at the first check, not at the end of the bar"


async def test_the_items_behind_a_quota_stop_are_unwritten() -> None:
    """Same contract as the error wall: a resume must be able to tell 'never
    attempted' from 'failed', so a clean stop records nothing for what it
    skipped."""

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    items = [_item(f"i{n}") for n in range(30)]

    async def spent(scored: int) -> QuotaDecision:
        return QuotaDecision("session 100.0% used")

    calls = await run_bar(
        bar, FakeClient(), items, "local-classification", quota_check=spent, quota_check_every=10
    )

    assert all(c.error is None for c in calls), "a quota stop is not an error row"
    assert {c.item.item_id for c in calls} == {f"i{n}" for n in range(10)}


async def test_a_healthy_allowance_never_interrupts_a_bar() -> None:
    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    items = [_item(f"i{n}") for n in range(30)]
    checks = 0

    async def healthy(scored: int) -> QuotaDecision:
        nonlocal checks
        checks += 1
        return QuotaDecision(None, next_check_after=10)

    calls = await run_bar(
        bar, FakeClient(), items, "local-classification", quota_check=healthy, quota_check_every=10
    )

    assert len(calls) == 30
    assert checks == 2, "polled at items 10 and 20, not before the first item"


# ---------------------------------------------------------------------------
# The run row must agree with the rows it summarises
#
# 2026-09-20: a resumed run scored all 407 outstanding items, persisted every
# one, then died on a primary-key violation writing the run row — leaving the
# results table correct and the summary claiming `1 admit, 407 errors`. #261
# made the results insert upsert-safe and left this one a plain INSERT.
# ---------------------------------------------------------------------------


def test_a_resume_refreshes_the_run_row_rather_than_colliding_with_it() -> None:
    assert "ON CONFLICT (run_id) DO UPDATE" in INSERT_RUN_SQL
    assert "report_markdown = EXCLUDED.report_markdown" in INSERT_RUN_SQL


def test_a_repair_never_moves_when_the_run_started() -> None:
    """`finished_at` moves because the run really did finish later. `started_at`
    is when it started and rewriting it would falsify the record."""

    assert "started_at = EXCLUDED" not in INSERT_RUN_SQL
    assert "finished_at = EXCLUDED.finished_at" in INSERT_RUN_SQL


class _RunResultsConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        return self._rows


def _result_row(
    item_id: str, source: str, admitted: bool | None, error: str | None = None
) -> dict[str, Any]:
    return {
        "bar": BAR_INCUMBENT,
        "item_id": item_id,
        "source": source,
        "title": f"title {item_id}",
        "admitted": admitted,
        "label": "cost-curve" if admitted else None,
        "reason": "because",
        "parsed_ok": True,
        "latency_ms": 12.0,
        "error": error,
    }


async def test_the_summary_is_built_from_every_stored_row_not_the_resumed_slice() -> None:
    """A resume holds only the re-scored items in memory. Summarising those
    would describe 407 of 599 — a summary disagreeing with its own detail table,
    which is the error that produced two wrong write-ups in one day."""

    stored = [_result_row(f"i{n}", "sec-edgar", n == 0) for n in range(599)]
    pool = _ReplayPool(cast(Any, _RunResultsConn(stored)))

    report = await build_report(
        pool,
        "01RUN",
        bars_by_key([BAR_INCUMBENT]),
        tier="local-classification",
        model="kimi-k3",
        started_at=datetime(2026, 9, 20, tzinfo=UTC),
    )

    assert report.corpus_size == 599, "not the 407 a resume would hold in memory"
    assert report.summaries[0].scored == 599
    assert report.summaries[0].errors == 0


async def test_a_rebuilt_summary_counts_errors_that_are_still_stored() -> None:
    stored = [_result_row(f"i{n}", "sec-edgar", None, "429") for n in range(5)]
    stored += [_result_row(f"j{n}", "usaspending", False) for n in range(3)]
    pool = _ReplayPool(cast(Any, _RunResultsConn(stored)))

    report = await build_report(
        pool,
        "01RUN",
        bars_by_key([BAR_INCUMBENT]),
        tier="local-classification",
        model="kimi-k3",
        started_at=datetime(2026, 9, 20, tzinfo=UTC),
    )

    assert report.summaries[0].scored == 8
    assert report.summaries[0].errors == 5


# ---------------------------------------------------------------------------
# Every bar must read the filing, not the index entry
#
# 2026-09-20: the experiment's corpus query never selected `document_text`, and
# bars B and C never read it even when present. For `sec-edgar` — 72% of the
# corpus — `summary` is the Atom index entry: a filed date, an accession number
# and a file size. So 425 of 454 hard-leg items were scored on metadata, under
# two models and three bars, and every one was rejected. That is KI-026 (#189)
# reproduced inside the experiment built to evaluate the filter it was found in.
# ---------------------------------------------------------------------------


EDGAR_INDEX_ENTRY = "<b>Filed:</b> 2026-07-30 <b>AccNo:</b> 0000002969-26-000036 <b>Size:</b> 14 MB"


def _filing_item() -> UnfilteredItem:
    return UnfilteredItem(
        item_id="edgar:1",
        source="sec-edgar",
        kind="10-Q",
        title="10-Q - Air Products & Chemicals, Inc. (0000002969) (Filer)",
        summary=EDGAR_INDEX_ENTRY,
        document_text="The Company commissioned its first commercial-scale green hydrogen facility",
    )


def test_the_corpus_query_selects_the_document_body() -> None:
    """The column whose absence caused all of it."""

    assert "document_text" in SELECT_CORPUS_SQL


def test_every_bar_shows_the_filing_rather_than_the_index_entry() -> None:
    item = _filing_item()

    for bar in all_bars():
        prompt = bar.build_prompt(item)
        assert "green hydrogen facility" in prompt, f"{bar.key} did not show the filing"
        assert "AccNo:" not in prompt, f"{bar.key} showed the index entry instead"


def test_the_body_is_labelled_document_not_summary() -> None:
    """A model told "Summary:" ahead of six thousand characters of filing text is
    being told something false about what it is reading."""

    for bar in all_bars():
        prompt = bar.build_prompt(_filing_item())
        assert "Document:" in prompt, bar.key


def test_an_item_with_no_body_still_shows_its_summary() -> None:
    """USASpending, Federal Register and DOE newsroom carry their content in
    `summary` and have no `document_text` at all. Those three produced every
    admit the experiment ever recorded, so the fallback must not regress."""

    item = UnfilteredItem(
        item_id="doe:1",
        source="doe-newsroom",
        kind=None,
        title="DOE Celebrates Fourth Criticality",
        summary="The reactor reached criticality for the fourth time.",
        document_text=None,
    )

    for bar in all_bars():
        prompt = bar.build_prompt(item)
        assert "reached criticality" in prompt, bar.key
        assert "Summary:" in prompt, bar.key


async def test_the_loaded_corpus_carries_the_document_body() -> None:
    """`load_corpus` must pass `document_text` through to the item. Dropping it
    here is indistinguishable, at every layer below, from a filing that has no
    body — which is how 425 EDGAR items were scored on their index entries."""

    corpus = [_corpus_row("edgar:1", "sec-edgar", document_text="the filing body")]
    pool = _ReplayPool(_ReplayConn(corpus, []))

    items = await load_corpus(pool, None, None)

    assert items[0].document_text == "the filing body"


async def test_sources_narrows_the_corpus_to_named_feeds() -> None:
    """A change to how one source renders does not change the others. When the
    filings started being read, the four sources with no `document_text` built
    byte-identical prompts, so re-scoring them would have spent a third of the
    budget reproducing numbers already held."""

    corpus = [
        _corpus_row("arxiv:1", "arxiv"),
        _corpus_row("edgar:1", "sec-edgar", document_text="body"),
        _corpus_row("edgar:2", "sec-edgar", document_text="body"),
        _corpus_row("doe:1", "doe-newsroom"),
    ]
    pool = _ReplayPool(_ReplayConn(corpus, []))

    items = await load_corpus(pool, None, None, None, ["sec-edgar"])

    assert sorted(i.item_id for i in items) == ["edgar:1", "edgar:2"]


async def test_an_unknown_source_is_an_error_not_an_empty_run() -> None:
    """A typo would otherwise score nothing and print a confident report over an
    empty corpus."""

    pool = _ReplayPool(_ReplayConn([_corpus_row("arxiv:1", "arxiv")], []))

    with pytest.raises(SystemExit, match="no corpus items from source"):
        await load_corpus(pool, None, None, None, ["sec-edgard"])


async def test_a_source_filter_does_not_make_replayed_items_look_missing() -> None:
    """The missing-items warning exists to catch a panel silently shrinking. It
    fired on every correct `--sources` run because the filter ran before the
    replay intersection, and a warning that fires on correct usage is one nobody
    reads."""

    corpus = [
        _corpus_row("arxiv:1", "arxiv"),
        _corpus_row("edgar:1", "sec-edgar", document_text="body"),
    ]
    pool = _ReplayPool(_ReplayConn(corpus, ["arxiv:1", "edgar:1"]))

    items, out = [], io.StringIO()
    with contextlib.redirect_stdout(out):
        items = await load_corpus(pool, None, "01OLD", None, ["sec-edgar"])

    assert [i.item_id for i in items] == ["edgar:1"]
    assert "no longer in the corpus" not in out.getvalue()


async def test_a_genuinely_absent_replayed_item_still_warns() -> None:
    pool = _ReplayPool(_ReplayConn([_corpus_row("edgar:1", "sec-edgar")], ["edgar:1", "gone:1"]))

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        await load_corpus(pool, None, "01OLD", None, None)

    assert "1 of 2 replayed items are no longer in the corpus" in out.getvalue()


# ---------------------------------------------------------------------------
# Finishing a run the quota guard stopped cleanly
# ---------------------------------------------------------------------------


async def test_resume_plus_item_set_picks_up_items_that_were_never_attempted() -> None:
    """The guard stops cleanly and writes nothing for the items behind it —
    "never attempted" is not "failed" — so an errored-rows query cannot see them.
    On 2026-09-20 that left 175 of 425 EDGAR items invisible to `--resume-run`."""

    corpus = [_corpus_row(f"edgar:{n}", "sec-edgar", document_text="body") for n in range(5)]
    pool = _ReplayPool(
        _ReplayConn(
            corpus,
            replay_ids=[f"edgar:{n}" for n in range(5)],
            scored_ids=["edgar:0", "edgar:1"],
        )
    )

    items = await load_corpus(pool, None, "01SRC", "01PARTIAL")

    assert sorted(i.item_id for i in items) == ["edgar:2", "edgar:3", "edgar:4"]


async def test_a_run_that_already_covers_the_set_is_an_error_not_an_empty_run() -> None:
    corpus = [_corpus_row("edgar:1", "sec-edgar")]
    pool = _ReplayPool(_ReplayConn(corpus, replay_ids=["edgar:1"], scored_ids=["edgar:1"]))

    with pytest.raises(SystemExit, match="already covers every item"):
        await load_corpus(pool, None, "01SRC", "01DONE")


# ---------------------------------------------------------------------------
# The guard paces itself by what the run is observed to cost
# ---------------------------------------------------------------------------


async def test_a_costly_run_is_re_checked_often() -> None:
    """A fixed stride crossed 89% -> 98.9% between two consecutive checks,
    because 50 items was 9.5% of the window against a 10% reserve. The interval
    must come from observed cost, not from a constant."""

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    asks = 0

    async def expensive(scored: int) -> QuotaDecision:
        nonlocal asks
        asks += 1
        return QuotaDecision(None, next_check_after=5)

    calls = await run_bar(
        bar,
        FakeClient(),
        [_item(f"i{n}") for n in range(60)],
        "local-classification",
        quota_check=expensive,
        quota_check_every=5,
    )

    assert len(calls) == 60
    assert asks >= 10, f"only {asks} meter reads across a costly 60-item bar"


async def test_a_cheap_run_is_not_re_checked_every_few_items() -> None:
    """The meter read is an HTTP round trip; it must not dominate a cheap run."""

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    asks = 0

    async def cheap(scored: int) -> QuotaDecision:
        nonlocal asks
        asks += 1
        return QuotaDecision(None, next_check_after=QUOTA_CHECK_MAX)

    await run_bar(
        bar,
        FakeClient(),
        [_item(f"i{n}") for n in range(300)],
        "local-classification",
        quota_check=cheap,
        quota_check_every=QUOTA_CHECK_MAX,
    )

    assert asks <= 3, f"{asks} meter reads for 300 cheap items"


async def test_the_guard_is_told_how_many_items_have_been_scored() -> None:
    """**The test that would have caught #267 being a no-op.** The CLI read
    `len(calls)` from the enclosing scope, but `calls` is only extended after
    `run_bar` returns — so during a bar it never moved, every interval measured
    zero items, and the adaptive stride silently fell back to its maximum on
    every check. A fixed 100-item interval wearing the costume of an adaptive
    one."""

    bar = next(b for b in all_bars() if b.key == BAR_INCUMBENT)
    told: list[int] = []

    async def record(scored: int) -> QuotaDecision:
        told.append(scored)
        return QuotaDecision(None, next_check_after=10)

    await run_bar(
        bar,
        FakeClient(),
        [_item(f"i{n}") for n in range(50)],
        "local-classification",
        quota_check=record,
        quota_check_every=10,
    )

    assert told == sorted(told), "the count must not go backwards"
    assert len(set(told)) == len(told), "a constant count means nothing is measurable"
    assert told[0] >= 10 and told[-1] >= 40
