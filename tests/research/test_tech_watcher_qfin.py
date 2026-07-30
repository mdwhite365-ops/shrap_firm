"""The q-fin leg: a second arXiv source and a second filter over one ingest.

The card that gives the Hypothesis Generator something to read. Its two real
hazards, both pinned below:

1. **The two filters must partition the pool.** An item in neither is never
   scored; an item in both is scored twice under prompts that disagree by
   design, and the second verdict overwrites the first.
2. **A cross-listed paper belongs to both funnels.** The source is part of the
   item id, so cs.LG ∩ q-fin.ST does not hand the paper to whichever leg
   fetched it first.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from shrap.research.hypothesis_generator.literature import LiteratureItem
from shrap.research.tech_watcher.filter import EXCLUDED_SOURCES, SELECT_UNFILTERED_SQL
from shrap.research.tech_watcher.literature_filter import (
    LITERATURE_PROMPT_VERSION,
    LITERATURE_SYSTEM_PROMPT,
    literature_pass,
    parse_literature_response,
)
from shrap.research.tech_watcher.sources import (
    DEFAULT_QFIN_CATEGORIES,
    SOURCE_ARXIV,
    SOURCE_ARXIV_QFIN,
    ArxivSource,
)

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.01234v1</id>
    <title>Illiquidity and the cross-section of expected returns</title>
    <summary>We document that expected returns rise with illiquidity.</summary>
    <published>2024-01-02T00:00:00Z</published>
    <author><name>Yakov Amihud</name></author>
    <author><name>Haim Mendelson</name></author>
    <link href="http://arxiv.org/abs/2401.01234v1" rel="alternate"/>
    <arxiv:primary_category term="q-fin.PM"/>
  </entry>
</feed>
"""


@dataclass(frozen=True, slots=True)
class _Response:
    text: str
    status_code: int = 200


class _FakeHTTP:
    def __init__(self, body: str) -> None:
        self.body = body
        self.params: dict[str, str] = {}

    async def get(self, url: str, *, params: dict[str, str], **kwargs: Any) -> _Response:
        self.params = params
        return _Response(self.body)


@dataclass(frozen=True, slots=True)
class _LLMResult:
    content: str
    model: str = "qwen3:4b"


class _FakeLLM:
    def __init__(self, *contents: str) -> None:
        self.contents = list(contents)
        self.systems: list[str] = []

    async def complete(self, **kwargs: Any) -> _LLMResult:
        self.systems.append(str(kwargs.get("system")))
        return _LLMResult(self.contents.pop(0))


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.executed: list[tuple[Any, ...]] = []
        self.fetched: tuple[Any, ...] = ()

    async def fetch(self, sql: str, *args: object) -> list[dict[str, Any]]:
        self.fetched = args
        return self.rows

    async def execute(self, sql: str, *args: object) -> str:
        self.executed.append(args)
        return "UPDATE 1"


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.conn = _FakeConn(rows)

    def acquire(self) -> Any:
        pool = self

        class _Ctx:
            async def __aenter__(self) -> _FakeConn:
                return pool.conn

            async def __aexit__(self, *args: object) -> None:
                return None

        return _Ctx()


class _FakeSink:
    def __init__(self) -> None:
        self.recorded: list[tuple[LiteratureItem, str]] = []

    async def record(self, item: LiteratureItem, accepted_reason: str) -> None:
        self.recorded.append((item, accepted_reason))


class _FakeEvents:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish(self, **kwargs: Any) -> None:
        self.published.append(kwargs)


def _row(item_id: str = "arxiv-qfin:2401.01234v1") -> dict[str, Any]:
    return {
        "item_id": item_id,
        "source": SOURCE_ARXIV_QFIN,
        "kind": "q-fin.PM",
        "title": "Illiquidity and the cross-section of expected returns",
        "summary": "We document that expected returns rise with illiquidity.",
        "url": "http://arxiv.org/abs/2401.01234v1",
        "external_ts": datetime(2024, 1, 2, tzinfo=UTC),
        "payload": {"primary_category": "q-fin.PM", "authors": ["Yakov Amihud"]},
    }


def _verdict(accepted: bool = True, reason: str = "illiquidity predicts returns") -> str:
    return json.dumps(
        {"testable_effect": accepted, "reason": reason, "paper_finds_it_works": accepted}
    )


# --- the second source --------------------------------------------------------


async def test_the_qfin_source_carries_its_own_name_and_id_namespace() -> None:
    """A paper cross-listed in cs.LG and q-fin.ST is a candidate in two funnels
    judged by different bars. One row would hand it to whichever leg fetched it
    first."""

    http = _FakeHTTP(_FEED)
    source = ArxivSource(DEFAULT_QFIN_CATEGORIES, name=SOURCE_ARXIV_QFIN)

    items = await source.fetch(http)  # type: ignore[arg-type]

    assert source.name == SOURCE_ARXIV_QFIN
    assert items[0].item_id == "arxiv-qfin:2401.01234v1"
    assert items[0].source == SOURCE_ARXIV_QFIN


async def test_the_world_changer_source_keeps_its_original_identity() -> None:
    """Existing rows say `arxiv:<id>`. A change here would orphan every item
    already ingested and re-fetch the entire backlog as new."""

    source = ArxivSource(("cs.AI",))

    items = await source.fetch(_FakeHTTP(_FEED))  # type: ignore[arg-type]

    assert source.name == SOURCE_ARXIV
    assert items[0].item_id == "arxiv:2401.01234v1"


async def test_the_two_sources_query_disjoint_categories() -> None:
    http = _FakeHTTP(_FEED)

    await ArxivSource(DEFAULT_QFIN_CATEGORIES, name=SOURCE_ARXIV_QFIN).fetch(http)  # type: ignore[arg-type]

    assert http.params["search_query"] == (
        "cat:q-fin.PM OR cat:q-fin.ST OR cat:q-fin.TR OR cat:q-fin.GN"
    )


# --- the two filters partition the pool ---------------------------------------


def test_the_world_changer_filter_excludes_the_qfin_pool() -> None:
    """Its prompt rejects anything 'merely ABOUT a technology — a new method,
    model architecture, benchmark, or simulation result', which describes every
    q-fin paper. Without the exclusion the section would be ingested, marked
    filtered, rejected in full, and every counter would report a healthy pass."""

    assert SOURCE_ARXIV_QFIN in EXCLUDED_SOURCES
    assert "NOT (source = ANY($2::text[]))" in SELECT_UNFILTERED_SQL


async def test_the_literature_filter_selects_only_the_qfin_pool() -> None:
    pool = _FakePool([])

    await literature_pass(pool, _FakeLLM(), _FakeSink(), max_items=7)  # type: ignore[arg-type]

    assert pool.conn.fetched == (SOURCE_ARXIV_QFIN, 7)


async def test_the_literature_filter_uses_its_own_prompt() -> None:
    pool = _FakePool([_row()])
    llm = _FakeLLM(_verdict())

    await literature_pass(pool, llm, _FakeSink())  # type: ignore[arg-type]

    assert llm.systems[0] == LITERATURE_SYSTEM_PROMPT


# --- accepting and rejecting ---------------------------------------------------


async def test_an_accepted_paper_reaches_the_literature_table_and_the_stream() -> None:
    pool = _FakePool([_row()])
    sink, events = _FakeSink(), _FakeEvents()

    report = await literature_pass(pool, _FakeLLM(_verdict()), sink, events)  # type: ignore[arg-type]

    assert len(report.accepted) == 1
    item, reason = sink.recorded[0]
    assert item.item_id == "arxiv-qfin:2401.01234v1"
    assert item.abstract.startswith("We document")
    assert item.category == "q-fin.PM"
    assert item.authors == ("Yakov Amihud",)
    assert item.published_at == datetime(2024, 1, 2, tzinfo=UTC)
    assert reason == "illiquidity predicts returns"
    assert events.published[0]["stream"] == "research.literature.ingested"


async def test_a_rejected_paper_is_marked_but_never_recorded_or_announced() -> None:
    pool = _FakePool([_row()])
    sink, events = _FakeSink(), _FakeEvents()

    report = await literature_pass(
        pool,  # type: ignore[arg-type]
        _FakeLLM(_verdict(False, "a stochastic control problem, not a return claim")),
        sink,
        events,
    )

    assert report.accepted == ()
    assert sink.recorded == []
    assert events.published == []
    # Still marked, or it would be re-scored every pass forever.
    assert pool.conn.executed[0][0] == "arxiv-qfin:2401.01234v1"


async def test_the_row_is_written_before_the_event_is_published() -> None:
    """The generator reads the table; the event is only a nudge. A crash between
    the two should cost a wakeup, not an item."""

    order: list[str] = []

    class _OrderedSink(_FakeSink):
        async def record(self, item: LiteratureItem, accepted_reason: str) -> None:
            order.append("record")
            await super().record(item, accepted_reason)

    class _OrderedEvents(_FakeEvents):
        async def publish(self, **kwargs: Any) -> None:
            order.append("publish")
            await super().publish(**kwargs)

    await literature_pass(
        _FakePool([_row()]),  # type: ignore[arg-type]
        _FakeLLM(_verdict()),
        _OrderedSink(),
        _OrderedEvents(),
    )

    assert order == ["record", "publish"]


async def test_the_verdict_is_stamped_with_its_own_prompt_version() -> None:
    """Namespaced away from the world-changer filter's counter. Sharing one
    would make either filter's revision look like a reason to re-score the
    other's items."""

    pool = _FakePool([_row()])

    await literature_pass(pool, _FakeLLM(_verdict()), _FakeSink())  # type: ignore[arg-type]

    stored = json.loads(str(pool.conn.executed[0][2]))
    assert stored["kind"] == "literature"
    assert stored["prompt_version"] == LITERATURE_PROMPT_VERSION
    assert stored["testable_effect"] is True
    assert stored["model"] == "qwen3:4b"


# --- the bias is to drop ------------------------------------------------------


def test_an_unparseable_response_rejects() -> None:
    """An unparseable response defaulting to accept would push an item the model
    never endorsed into the proposer, where it would acquire a citation it does
    not have."""

    assert not parse_literature_response("x", "not json").accepted
    assert not parse_literature_response("x", '["a list"]').accepted
    assert not parse_literature_response("x", "{}").accepted


def test_a_truthy_non_true_value_is_not_an_acceptance() -> None:
    assert not parse_literature_response("x", '{"testable_effect": "yes"}').accepted
    assert not parse_literature_response("x", '{"testable_effect": 1}').accepted


def test_a_verdict_with_no_reason_still_says_something() -> None:
    verdict = parse_literature_response(
        "x", '{"testable_effect": true, "paper_finds_it_works": true}'
    )

    assert verdict.accepted
    assert verdict.reason == "no reason given"


def test_an_empty_pass_says_so_rather_than_rendering_blank() -> None:
    from shrap.research.tech_watcher.literature_filter import LiteratureReport

    assert "no unscored q-fin items" in LiteratureReport(verdicts=()).render()


# --- filter v2: judge the finding, not the setup ------------------------------


def test_a_paper_reporting_that_signals_fail_is_rejected() -> None:
    """The v1 false accept, verbatim from the first live run. "Retail Trader's
    Ruin: An Anatomy of Popular Signal Failure" was accepted because trend,
    oscillator and volume signals "are claimed to predict future stock returns"
    — a sentence the paper writes in order to refute."""

    verdict = parse_literature_response(
        "x",
        json.dumps(
            {
                "testable_effect": True,
                "reason": "trend, oscillator and volume signals are claimed to predict returns",
                "paper_finds_it_works": False,
            }
        ),
    )

    assert not verdict.accepted
    assert "does not report the effect working" in verdict.reason


def test_a_confirming_paper_still_passes() -> None:
    verdict = parse_literature_response(
        "x", json.dumps({"testable_effect": True, "reason": "r", "paper_finds_it_works": True})
    )

    assert verdict.accepted


def test_a_missing_finding_field_counts_as_a_rejection() -> None:
    """Bias to drop. A silent accept puts a refuted claim into the proposer; a
    silent reject shows up as an empty funnel with the reasons still
    queryable."""

    verdict = parse_literature_response("x", json.dumps({"testable_effect": True, "reason": "r"}))

    assert not verdict.accepted


def test_the_prompt_names_the_failure_it_was_written_against() -> None:
    assert "JUDGE WHAT THE PAPER CONCLUDES, NOT WHAT IT EXAMINES" in LITERATURE_SYSTEM_PROMPT
    assert "result sentence, not its setup" in LITERATURE_SYSTEM_PROMPT
    assert LITERATURE_PROMPT_VERSION == 2


# --- one bad call must not cost the batch -------------------------------------


class _FlakyLLM:
    """Fails on the nth call, succeeds otherwise."""

    def __init__(self, fail_on: set[int], total: int) -> None:
        self.fail_on = fail_on
        self.calls = 0
        self.total = total

    async def complete(self, **kwargs: Any) -> _LLMResult:
        self.calls += 1
        if self.calls in self.fail_on:
            raise TimeoutError("ollama timed out")
        return _LLMResult(_verdict())


async def test_a_single_failure_skips_one_item_and_continues() -> None:
    """The item stays unmarked and retries next pass, so skipping loses
    nothing. Aborting would lose every item behind it for an hour."""

    rows = [_row(f"arxiv-qfin:{i}") for i in range(4)]
    llm = _FlakyLLM(fail_on={2}, total=4)

    report = await literature_pass(_FakePool(rows), llm, _FakeSink())  # type: ignore[arg-type]

    assert llm.calls == 4
    assert len(report.verdicts) == 3


async def test_a_run_of_failures_aborts_rather_than_burning_the_batch() -> None:
    """Five in a row is a dead endpoint or a bad key, not noise. Grinding
    through ninety-five more calls buries the cause in warnings."""

    rows = [_row(f"arxiv-qfin:{i}") for i in range(30)]
    llm = _FlakyLLM(fail_on=set(range(1, 30)), total=30)

    report = await literature_pass(_FakePool(rows), llm, _FakeSink())  # type: ignore[arg-type]

    assert llm.calls == 5
    assert report.verdicts == ()
