"""Filter prompt v4: the evidentiary bar depends on who is asserting the fact.

Regression coverage for the DQ-006 false negative — a DOE announcement that a
*fourth* reactor in a federal pilot cohort reached criticality, rejected under
v3 for lacking "independent replication."
"""

from __future__ import annotations

from typing import Any

from shrap.research.tech_watcher.filter import (
    EVIDENCE_ATTESTED,
    EVIDENCE_CLAIM,
    FILTER_PROMPT_VERSION,
    FILTER_SYSTEM_PROMPT,
    UnfilteredItem,
    _item_prompt,
    evidence_class,
    refilter_pass,
)
from shrap.research.tech_watcher.sources import (
    SOURCE_ARXIV,
    SOURCE_DOE_NEWS,
    SOURCE_EDGAR,
    SOURCE_FED_REGISTER,
    SOURCE_USASPENDING,
)

# The item v3 rejected, verbatim from research.raw_source_items on the Dell.
DOE_CRITICALITY = UnfilteredItem(
    item_id="doe-news:/articles/department-energy-celebrates-fourth-criticality-ahead-july-4th-goal",
    source=SOURCE_DOE_NEWS,
    kind="press",
    title="Department of Energy Celebrates Fourth Criticality Ahead of July 4th Goal",
    summary=None,
)


def test_prompt_version_bumped_for_behavior_change() -> None:
    assert FILTER_PROMPT_VERSION == 4


# --- evidence class -----------------------------------------------------------


def test_institutional_sources_are_attested() -> None:
    for source in (SOURCE_EDGAR, SOURCE_USASPENDING, SOURCE_FED_REGISTER, SOURCE_DOE_NEWS):
        assert evidence_class(source) == EVIDENCE_ATTESTED, source


def test_arxiv_is_a_claim() -> None:
    assert evidence_class(SOURCE_ARXIV) == EVIDENCE_CLAIM


def test_unknown_source_defaults_to_the_stricter_bar() -> None:
    # An unmapped source must not inherit the attested presumption.
    assert evidence_class("some-new-feed") == EVIDENCE_CLAIM


def test_evidence_class_is_not_triangulation_hardness() -> None:
    # DOE newsroom is attested for the filter (the agency states its own program
    # milestone) but must stay SOFT for triangulation (agency press is
    # promotional). Conflating the two would let one agency fake corroboration.
    from shrap.research.tech_watcher.synthesis import _HARD_SOURCES

    assert evidence_class(SOURCE_DOE_NEWS) == EVIDENCE_ATTESTED
    assert SOURCE_DOE_NEWS not in _HARD_SOURCES


# --- the prompt carries the three v3 fixes ------------------------------------


def test_prompt_forbids_demanding_replication_of_attested_events() -> None:
    assert "attested" in FILTER_SYSTEM_PROMPT
    assert "Presume the event happened" in FILTER_SYSTEM_PROMPT
    assert "Do NOT demand independent" in FILTER_SYSTEM_PROMPT


def test_prompt_credits_cumulative_evidence() -> None:
    # "Fourth criticality" must read as repeatability, not as one anecdote.
    assert "Nth instance" in FILTER_SYSTEM_PROMPT
    assert "single anecdote" in FILTER_SYSTEM_PROMPT
    assert "lacking replication when it is itself reporting replication" in FILTER_SYSTEM_PROMPT


def test_prompt_requires_failing_every_archetype_before_rejecting() -> None:
    assert "fails EVERY archetype" in FILTER_SYSTEM_PROMPT
    assert "cost-curve evidence" in FILTER_SYSTEM_PROMPT


def test_prompt_keeps_the_v3_skepticism_for_claims() -> None:
    # v4 must not become permissive across the board — the arXiv bar stands.
    assert "NOT evidence" in FILTER_SYSTEM_PROMPT
    assert "impostor" in FILTER_SYSTEM_PROMPT
    assert "unreplicated headline result is not" in FILTER_SYSTEM_PROMPT


def test_unsure_tiebreaker_cannot_override_the_attested_rule() -> None:
    assert "never overrides the attested rule" in FILTER_SYSTEM_PROMPT


def test_reason_must_name_the_archetype_and_bar() -> None:
    # So the next audit like DQ-006 is cheap instead of forensic.
    assert "naming the archetype you tested and the bar you applied" in FILTER_SYSTEM_PROMPT


# --- the item prompt ----------------------------------------------------------


def test_item_prompt_labels_the_evidence_class() -> None:
    prompt = _item_prompt(DOE_CRITICALITY)

    assert f"evidence_class={EVIDENCE_ATTESTED}" in prompt
    assert "source=doe-newsroom" in prompt
    assert "Fourth Criticality" in prompt


def test_arxiv_item_prompt_is_labelled_a_claim() -> None:
    item = UnfilteredItem(
        item_id="arxiv:1",
        source=SOURCE_ARXIV,
        kind="cs.LG",
        title="A new attention variant",
        summary="We propose a method.",
    )

    assert f"evidence_class={EVIDENCE_CLAIM}" in _item_prompt(item)


def test_item_prompt_still_carries_the_recognition_grammar() -> None:
    # physical-realization stays in scope per docs/research/world-changer-
    # archetypes.md ("should remain in scope for Tech Watcher scanning") — the
    # v4 fix is that its bar no longer ends the evaluation, not that it is gone.
    prompt = _item_prompt(DOE_CRITICALITY)

    assert "physical-realization" in prompt
    assert "cost-curve" in prompt


# --- the re-filter path -------------------------------------------------------


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetched_args = args
        return self._rows

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "INSERT 0 1"


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.conn = _FakeConn(rows)

    def acquire(self) -> Any:
        pool_conn = self.conn

        class _Ctx:
            async def __aenter__(self) -> _FakeConn:
                return pool_conn

            async def __aexit__(self, *a: object) -> None:
                return None

        return _Ctx()


class _FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    async def complete(self, **kwargs: Any) -> Any:
        self.calls.append(str(kwargs.get("prompt", "")))

        class _R:
            content = self._responses.pop(0)
            model = "qwen3.5:9b-q4_K_M"

        return _R()


def _backlog_row(relevant: bool = False, version: int = 3) -> dict[str, Any]:
    return {
        "item_id": DOE_CRITICALITY.item_id,
        "source": SOURCE_DOE_NEWS,
        "kind": "press",
        "title": DOE_CRITICALITY.title,
        "summary": None,
        "was_relevant": relevant,
        "scored_version": version,
    }


async def test_dry_run_counts_without_calling_the_model_or_writing() -> None:
    pool = _FakePool([_backlog_row()])
    llm = _FakeLLM([])

    report = await refilter_pass(pool, llm, dry_run=True)  # type: ignore[arg-type]

    assert report.scored == 1
    assert report.flips == ()
    assert llm.calls == []
    assert pool.conn.executed == []
    assert report.render().startswith("[dry-run]")


async def test_refilter_reports_a_rescued_false_negative() -> None:
    # The DQ-006 case: v3 said false, v4 says cost-curve evidence.
    pool = _FakePool([_backlog_row(relevant=False)])
    llm = _FakeLLM(
        [
            '{"relevant": true, "archetype": "cost-curve", '
            '"reason": "cost-curve: attested cohort milestone, fourth unit"}'
        ]
    )

    report = await refilter_pass(pool, llm)  # type: ignore[arg-type]

    assert len(report.rescued) == 1
    assert report.dropped == ()
    assert report.rescued[0].archetype == "cost-curve"
    assert "RESCUED" in report.render()


async def test_unchanged_verdict_is_not_a_flip() -> None:
    pool = _FakePool([_backlog_row(relevant=False)])
    llm = _FakeLLM(['{"relevant": false, "archetype": null, "reason": "grid press release"}'])

    report = await refilter_pass(pool, llm)  # type: ignore[arg-type]

    assert report.scored == 1
    assert report.flips == ()


async def test_refilter_can_drop_a_previously_kept_item() -> None:
    pool = _FakePool([_backlog_row(relevant=True)])
    llm = _FakeLLM(['{"relevant": false, "archetype": null, "reason": "methods paper"}'])

    report = await refilter_pass(pool, llm)  # type: ignore[arg-type]

    assert len(report.dropped) == 1
    assert report.rescued == ()


async def test_refilter_selects_on_the_current_prompt_version() -> None:
    pool = _FakePool([])
    await refilter_pass(pool, _FakeLLM([]), max_items=25, source="doe-newsroom", dry_run=True)  # type: ignore[arg-type]

    assert pool.conn.fetched_args == (FILTER_PROMPT_VERSION, "doe-newsroom", 25)


async def test_refilter_appends_history_before_marking() -> None:
    # KI-007: the append-only history row must be written first, so a crash
    # between the two loses nothing.
    pool = _FakePool([_backlog_row()])
    llm = _FakeLLM(['{"relevant": true, "archetype": "cost-curve", "reason": "r"}'])

    await refilter_pass(pool, llm)  # type: ignore[arg-type]

    sqls = [sql for sql, _ in pool.conn.executed]
    assert "filter_verdict_history" in sqls[0]
    assert "UPDATE research.raw_source_items" in sqls[1]


def test_refilter_prompt_carries_the_evidence_class() -> None:
    # The whole point: the re-scored prompt must label DOE as attested.
    assert f"evidence_class={EVIDENCE_ATTESTED}" in _item_prompt(DOE_CRITICALITY)
