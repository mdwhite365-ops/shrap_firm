"""Tests for Tech Watcher slice B: filter, clustering, validator, synthesis."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from shrap.llm.client import LLMResult
from shrap.research.tech_watcher.filter import (
    MARK_FILTERED_SQL,
    FilterVerdict,
    filter_pass,
    parse_filter_response,
)
from shrap.research.tech_watcher.review import render_markdown
from shrap.research.tech_watcher.synthesis import (
    MARK_SYNTHESIZED_SQL,
    STREAM_WORLD_CHANGER_PROPOSED,
    Cluster,
    RelevantItem,
    build_clusters,
    synthesis_pass,
    validate_candidate,
)

# --- fakes ---------------------------------------------------------------------


class FakeTransaction:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeConn:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.fetch_results: dict[str, list[dict[str, Any]]] = {}

    async def execute(self, sql: str, *args: object) -> object:
        self.executed.append((sql, args))
        return "OK"

    async def fetchrow(self, sql: str, *args: object) -> Mapping[str, Any] | None:
        rows = self.fetch_results.get(sql, [])
        return rows[0] if rows else None

    async def fetch(self, sql: str, *args: object) -> Sequence[Mapping[str, Any]]:
        return self.fetch_results.get(sql, [])

    def transaction(self) -> FakeTransaction:
        return FakeTransaction(self)


class FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> FakeConn:
        return self._conn

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConn()

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self.conn)


class FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        think: bool | None = None,
    ) -> LLMResult:
        self.calls.append({"tier": tier, "prompt": prompt, "json_mode": json_mode, "think": think})
        content = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        return LLMResult(
            tier=tier,
            provider="ollama",
            model="qwen3.5:9b-q4_K_M",
            content=content,
            input_tokens=10,
            output_tokens=10,
            latency_ms=5.0,
        )


class FakeRedis:
    def __init__(self) -> None:
        self.published: list[str] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.published.append(stream)
        return f"{len(self.published)}-0"


# --- filter --------------------------------------------------------------------


def test_parse_filter_response_valid_relevant() -> None:
    verdict = parse_filter_response(
        "arxiv:1",
        '{"relevant": true, "archetype": "compute-substrate", "reason": "capex signal"}',
    )
    assert verdict == FilterVerdict("arxiv:1", True, "compute-substrate", "capex signal")


def test_parse_filter_response_unknown_archetype_becomes_irrelevant() -> None:
    verdict = parse_filter_response(
        "arxiv:1", '{"relevant": true, "archetype": "made-up", "reason": "x"}'
    )
    assert verdict.relevant is False
    assert verdict.archetype is None


def test_parse_filter_response_garbage_is_irrelevant() -> None:
    verdict = parse_filter_response("arxiv:1", "I think this is interesting because...")
    assert verdict.relevant is False
    assert "unparseable" in verdict.reason


async def test_filter_pass_scores_and_marks_each_item() -> None:
    pool = FakePool()
    from shrap.research.tech_watcher.filter import SELECT_UNFILTERED_SQL

    pool.conn.fetch_results[SELECT_UNFILTERED_SQL] = [
        {
            "item_id": "arxiv:1",
            "source": "arxiv",
            "kind": "cs.LG",
            "title": "Photonic interconnect scaling",
            "summary": "We scale photonics.",
        },
        {
            "item_id": "edgar:2",
            "source": "sec-edgar",
            "kind": "8-K",
            "title": "Retailer quarterly report",
            "summary": None,
        },
    ]
    llm = FakeLLM(
        [
            '{"relevant": true, "archetype": "compute-substrate", "reason": "substrate signal"}',
            '{"relevant": false, "archetype": null, "reason": "retail, not tech"}',
        ]
    )

    verdicts = await filter_pass(pool, llm)  # type: ignore[arg-type]

    assert [v.relevant for v in verdicts] == [True, False]
    assert llm.calls[0]["json_mode"] is True
    assert llm.calls[0]["think"] is False  # bulk classification never thinks out loud
    marked = [args for sql, args in pool.conn.executed if sql == MARK_FILTERED_SQL]
    assert len(marked) == 2
    stored = json.loads(str(marked[0][2]))
    assert stored["archetype"] == "compute-substrate"


# --- clustering / triangulation ------------------------------------------------


def _relevant(item_id: str, source: str, archetype: str) -> RelevantItem:
    return RelevantItem(
        item_id=item_id,
        source=source,
        archetype=archetype,
        title=f"title {item_id}",
        summary="s",
        reason="r",
    )


def test_build_clusters_groups_by_archetype() -> None:
    clusters = build_clusters(
        [
            _relevant("a", "arxiv", "compute-substrate"),
            _relevant("b", "sec-edgar", "compute-substrate"),
            _relevant("c", "arxiv", "bio-mechanism"),
        ]
    )
    assert [c.archetype for c in clusters] == ["bio-mechanism", "compute-substrate"]


def test_triangulation_requires_two_source_classes() -> None:
    single = Cluster(
        archetype="bio-mechanism",
        items=(_relevant("a", "arxiv", "bio-mechanism"), _relevant("b", "arxiv", "bio-mechanism")),
    )
    triangulated = Cluster(
        archetype="compute-substrate",
        items=(
            _relevant("c", "arxiv", "compute-substrate"),
            _relevant("d", "sec-edgar", "compute-substrate"),
        ),
    )
    assert single.promotable is False  # two items, one source class — not evidence
    assert triangulated.promotable is True


# --- validator -----------------------------------------------------------------


def _good_candidate() -> dict[str, Any]:
    return {
        "name": "photonic-interconnect-shift",
        "archetype": "compute-substrate",
        "thesis": "Optical interconnects displace copper in AI clusters.",
        "confidence": "medium",
        "expected_impact_horizon": "3-5y",
        "kill_criteria": ["Co-packaged optics attach rate below 10% by FY28 guidance"],
        "falsifier_horizon": "2028",
        "dependency_graph_seed": ["lasers", "photonic ICs", "packaging"],
    }


def test_validate_candidate_accepts_complete_proposal() -> None:
    assert validate_candidate(_good_candidate(), "compute-substrate") is None


def test_validate_candidate_rejections() -> None:
    cases: list[tuple[dict[str, Any], str]] = [
        ({**_good_candidate(), "confidence": "0.7"}, "confidence"),
        ({**_good_candidate(), "expected_impact_horizon": "someday"}, "horizon"),
        ({**_good_candidate(), "kill_criteria": []}, "kill_criteria"),
        ({**_good_candidate(), "archetype": "bio-mechanism"}, "does not match"),
        ({**_good_candidate(), "falsifier_horizon": ""}, "falsifier"),
        ({"no_candidate": True, "reason": "evidence too thin"}, "declined"),
    ]
    for data, expected_fragment in cases:
        reason = validate_candidate(data, "compute-substrate")
        assert reason is not None and expected_fragment in reason


def test_validate_candidate_missing_field() -> None:
    data = _good_candidate()
    del data["thesis"]
    reason = validate_candidate(data, "compute-substrate")
    assert reason is not None and "thesis" in reason


# --- synthesis pass ------------------------------------------------------------


def _relevant_row(item_id: str, source: str, archetype: str) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "source": source,
        "title": f"title {item_id}",
        "summary": "s",
        "filter_result": json.dumps({"relevant": True, "archetype": archetype, "reason": "signal"}),
    }


async def test_synthesis_pass_proposes_triangulated_cluster() -> None:
    pool = FakePool()
    from shrap.research.tech_watcher.synthesis import SELECT_RELEVANT_UNSYNTHESIZED_SQL

    pool.conn.fetch_results[SELECT_RELEVANT_UNSYNTHESIZED_SQL] = [
        _relevant_row("arxiv:1", "arxiv", "compute-substrate"),
        _relevant_row("edgar:2", "sec-edgar", "compute-substrate"),
        _relevant_row("arxiv:3", "arxiv", "bio-mechanism"),  # single-source: waits
    ]
    llm = FakeLLM([json.dumps(_good_candidate())])
    redis = FakeRedis()

    report = await synthesis_pass(pool, llm, redis)  # type: ignore[arg-type]

    assert report.items_relevant == 3
    assert report.clusters == 2
    assert report.clusters_promotable == 1
    assert report.proposed == 1
    assert report.rejected == 0
    assert len(llm.calls) == 1  # single-source cluster consumed no LLM call
    assert STREAM_WORLD_CHANGER_PROPOSED in redis.published
    synthesized = [args for sql, args in pool.conn.executed if sql == MARK_SYNTHESIZED_SQL]
    assert len(synthesized) == 1
    assert sorted(synthesized[0][0]) == ["arxiv:1", "edgar:2"]  # bio item left waiting


async def test_synthesis_pass_persists_invalid_candidate_as_rejected() -> None:
    pool = FakePool()
    from shrap.research.tech_watcher.synthesis import SELECT_RELEVANT_UNSYNTHESIZED_SQL

    pool.conn.fetch_results[SELECT_RELEVANT_UNSYNTHESIZED_SQL] = [
        _relevant_row("arxiv:1", "arxiv", "compute-substrate"),
        _relevant_row("edgar:2", "sec-edgar", "compute-substrate"),
    ]
    bad = dict(_good_candidate(), confidence="0.95 probability")
    llm = FakeLLM([json.dumps(bad)])
    redis = FakeRedis()

    report = await synthesis_pass(pool, llm, redis)  # type: ignore[arg-type]

    assert report.proposed == 0
    assert report.rejected == 1
    assert redis.published == []  # rejected candidates never hit the bus
    from shrap.research.tech_watcher.candidates import INSERT_CANDIDATE_SQL

    inserts = [args for sql, args in pool.conn.executed if sql == INSERT_CANDIDATE_SQL]
    assert len(inserts) == 1
    assert inserts[0][3] == "rejected"  # status column
    assert "confidence" in str(inserts[0][12])  # rejection_reason


# --- review page ---------------------------------------------------------------


def test_render_markdown_shows_proposed_and_graveyard() -> None:
    markdown = render_markdown(
        [
            {
                "candidate_id": "01A",
                "name": "photonic-shift",
                "archetype": "compute-substrate",
                "status": "proposed",
                "thesis": "Optics win.",
                "confidence": "medium",
                "expected_impact_horizon": "3-5y",
                "kill_criteria": '["attach rate < 10% by FY28"]',
                "falsifier_horizon": "2028",
                "source_classes": '["arxiv", "sec-edgar"]',
                "rejection_reason": None,
            },
            {
                "candidate_id": "01B",
                "name": "unnamed-bio",
                "archetype": "bio-mechanism",
                "status": "rejected",
                "thesis": "",
                "confidence": "low",
                "expected_impact_horizon": "horizon unknown",
                "kill_criteria": "[]",
                "falsifier_horizon": None,
                "source_classes": '["arxiv"]',
                "rejection_reason": "kill_criteria must be a non-empty list of strings",
            },
        ]
    )
    assert "photonic-shift" in markdown
    assert "attach rate < 10% by FY28" in markdown
    assert "Rejection graveyard" in markdown
    assert "kill_criteria must be a non-empty list" in markdown


# --- calibration v2 prompt content ---------------------------------------------


def test_filter_prompt_block_carries_signals_and_impostors() -> None:
    from shrap.research.tech_watcher.archetypes import archetype_filter_prompt_block

    block = archetype_filter_prompt_block()
    assert "neuromorphic" in block.lower()  # the impostor the first batch missed
    assert "ML methods/architecture papers" in block
    assert "TCO advantage" in block
    assert "Phase 3" in block


def test_filter_system_prompt_demands_economic_evidence() -> None:
    from shrap.research.tech_watcher.filter import FILTER_SYSTEM_PROMPT

    assert "NOT evidence" in FILTER_SYSTEM_PROMPT
    assert "impostor" in FILTER_SYSTEM_PROMPT


async def test_filter_pass_stamps_prompt_version() -> None:
    pool = FakePool()
    from shrap.research.tech_watcher.filter import (
        FILTER_PROMPT_VERSION,
        SELECT_UNFILTERED_SQL,
    )

    pool.conn.fetch_results[SELECT_UNFILTERED_SQL] = [
        {
            "item_id": "arxiv:1",
            "source": "arxiv",
            "kind": "cs.LG",
            "title": "t",
            "summary": None,
        }
    ]
    llm = FakeLLM(['{"relevant": false, "archetype": null, "reason": "methods paper"}'])

    await filter_pass(pool, llm)  # type: ignore[arg-type]

    marked = [args for sql, args in pool.conn.executed if sql == MARK_FILTERED_SQL]
    assert json.loads(str(marked[0][2]))["prompt_version"] == FILTER_PROMPT_VERSION


def test_synthesis_cluster_prompt_includes_target_impostors() -> None:
    from shrap.research.tech_watcher.synthesis import _cluster_prompt

    cluster = Cluster(
        archetype="compute-substrate",
        items=(
            _relevant("a", "arxiv", "compute-substrate"),
            _relevant("b", "sec-edgar", "compute-substrate"),
        ),
    )
    prompt = _cluster_prompt(cluster)
    assert "Known impostors for compute-substrate" in prompt
    assert "neuromorphic" in prompt.lower()
