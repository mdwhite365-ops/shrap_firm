"""The staleness pass moves nodes between active and stale-evidence on the
evidence clock, leaves every other status alone, and is silent when nothing
changed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from shrap.research.infra_mapper.first_graph import SEED_NODES
from shrap.research.infra_mapper.maintenance import (
    DEFAULT_FRESHNESS_DAYS,
    STREAM_GRAPHS_UPDATED,
    evaluate_staleness,
    run_maintenance_pass,
)
from shrap.research.infra_mapper.store import (
    NODE_ACTIVE,
    NODE_DOWNGRADED,
    NODE_PENDING_REVIEW,
    NODE_REMOVED,
    NODE_STALE_EVIDENCE,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
GRAPH = "g-1"


def _node(ticker: str, status: str, layer_role: str = "end-user") -> dict[str, Any]:
    return {
        "graph_id": GRAPH,
        "ticker": ticker,
        "layer_role": layer_role,
        "status": status,
    }


def _aged(days: int) -> datetime:
    return NOW - timedelta(days=days)


# --- pure evaluation ----------------------------------------------------------


def test_fresh_active_node_stays_active_with_no_transition() -> None:
    nodes = [_node("MSFT", NODE_ACTIVE)]
    verdicts, skipped = evaluate_staleness(
        nodes, {("MSFT", "end-user"): _aged(10)}, now=NOW, freshness_days=180
    )

    assert skipped == 0
    assert len(verdicts) == 1
    assert verdicts[0].to_status == NODE_ACTIVE
    assert verdicts[0].is_transition is False
    assert verdicts[0].age_days == 10


def test_stale_active_node_is_flagged() -> None:
    nodes = [_node("MSFT", NODE_ACTIVE)]
    verdicts, _ = evaluate_staleness(
        nodes, {("MSFT", "end-user"): _aged(200)}, now=NOW, freshness_days=180
    )

    assert verdicts[0].to_status == NODE_STALE_EVIDENCE
    assert verdicts[0].is_transition is True
    assert "200d old" in verdicts[0].reason


def test_node_with_no_evidence_rows_is_stale() -> None:
    nodes = [_node("MSFT", NODE_ACTIVE)]
    verdicts, _ = evaluate_staleness(nodes, {}, now=NOW, freshness_days=180)

    assert verdicts[0].to_status == NODE_STALE_EVIDENCE
    assert verdicts[0].age_days is None
    assert verdicts[0].latest_evidence_at is None
    assert "no evidence" in verdicts[0].reason


def test_refreshed_evidence_recovers_a_stale_node() -> None:
    # The flag does not latch: fresh evidence returns the node to active.
    nodes = [_node("MSFT", NODE_STALE_EVIDENCE)]
    verdicts, _ = evaluate_staleness(
        nodes, {("MSFT", "end-user"): _aged(5)}, now=NOW, freshness_days=180
    )

    assert verdicts[0].from_status == NODE_STALE_EVIDENCE
    assert verdicts[0].to_status == NODE_ACTIVE
    assert verdicts[0].is_transition is True


def test_still_stale_node_produces_no_transition() -> None:
    nodes = [_node("MSFT", NODE_STALE_EVIDENCE)]
    verdicts, _ = evaluate_staleness(
        nodes, {("MSFT", "end-user"): _aged(900)}, now=NOW, freshness_days=180
    )

    assert verdicts[0].is_transition is False


@pytest.mark.parametrize("status", [NODE_PENDING_REVIEW, NODE_DOWNGRADED, NODE_REMOVED])
def test_statuses_outside_the_pass_are_skipped(status: str) -> None:
    # Reactivating a downgraded or removed node would launder a kill decision.
    nodes = [_node("MSFT", status)]
    verdicts, skipped = evaluate_staleness(nodes, {}, now=NOW, freshness_days=180)

    assert verdicts == ()
    assert skipped == 1


def test_boundary_node_exactly_at_threshold_is_still_fresh() -> None:
    nodes = [_node("MSFT", NODE_ACTIVE)]
    verdicts, _ = evaluate_staleness(
        nodes, {("MSFT", "end-user"): _aged(180)}, now=NOW, freshness_days=180
    )

    assert verdicts[0].to_status == NODE_ACTIVE


def test_staleness_is_per_node_not_per_ticker() -> None:
    # Same ticker in two layers keeps independent evidence clocks.
    nodes = [_node("MSFT", NODE_ACTIVE, "end-user"), _node("MSFT", NODE_ACTIVE, "power-gen")]
    verdicts, _ = evaluate_staleness(
        nodes,
        {("MSFT", "end-user"): _aged(5), ("MSFT", "power-gen"): _aged(900)},
        now=NOW,
        freshness_days=180,
    )

    by_layer = {v.layer_role: v.to_status for v in verdicts}
    assert by_layer == {"end-user": NODE_ACTIVE, "power-gen": NODE_STALE_EVIDENCE}


def test_non_positive_threshold_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        evaluate_staleness([], {}, now=NOW, freshness_days=0)


# --- the pass over a store ----------------------------------------------------


class _FakeStore:
    def __init__(
        self,
        graphs: list[dict[str, Any]],
        nodes: dict[str, list[dict[str, Any]]],
        evidence: dict[str, dict[tuple[str, str], datetime]],
    ) -> None:
        self._graphs = graphs
        self._nodes = nodes
        self._evidence = evidence
        self.status_writes: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []

    async def list_graphs(self) -> list[dict[str, Any]]:
        return self._graphs

    async def nodes_for_graph(self, graph_id: str) -> list[dict[str, Any]]:
        return self._nodes.get(graph_id, [])

    async def latest_evidence_at(self, graph_id: str) -> dict[tuple[str, str], datetime]:
        return self._evidence.get(graph_id, {})

    async def set_node_status(self, **kwargs: Any) -> bool:
        self.status_writes.append(kwargs)
        return True

    async def record_history(self, **kwargs: Any) -> None:
        self.history.append(kwargs)


class _FakeRedis:
    def __init__(self) -> None:
        self.streams: list[str] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.streams.append(stream)
        return "1-0"


def _store(nodes: list[dict[str, Any]], evidence: dict[tuple[str, str], datetime]) -> _FakeStore:
    return _FakeStore(
        graphs=[{"graph_id": GRAPH, "status": "active"}],
        nodes={GRAPH: nodes},
        evidence={GRAPH: evidence},
    )


async def test_pass_persists_and_emits_one_event_per_transition() -> None:
    store = _store(
        [_node("MSFT", NODE_ACTIVE), _node("AMZN", NODE_ACTIVE)],
        {("MSFT", "end-user"): _aged(900), ("AMZN", "end-user"): _aged(5)},
    )
    redis = _FakeRedis()
    report = await run_maintenance_pass(store, redis, now=NOW)  # type: ignore[arg-type]

    assert len(report.flagged) == 1
    assert report.flagged[0].ticker == "MSFT"
    assert len(store.status_writes) == 1
    assert len(store.history) == 1
    assert redis.streams == [STREAM_GRAPHS_UPDATED]


async def test_pass_is_silent_when_nothing_changed() -> None:
    store = _store([_node("MSFT", NODE_ACTIVE)], {("MSFT", "end-user"): _aged(5)})
    redis = _FakeRedis()
    report = await run_maintenance_pass(store, redis, now=NOW)  # type: ignore[arg-type]

    assert report.transitions == ()
    assert store.status_writes == []
    assert store.history == []
    assert redis.streams == []


async def test_dry_run_writes_nothing_but_still_reports() -> None:
    store = _store([_node("MSFT", NODE_ACTIVE)], {("MSFT", "end-user"): _aged(900)})
    redis = _FakeRedis()
    report = await run_maintenance_pass(store, redis, now=NOW, dry_run=True)  # type: ignore[arg-type]

    assert len(report.flagged) == 1
    assert store.status_writes == []
    assert store.history == []
    assert redis.streams == []
    assert report.render().startswith("[dry-run]")


async def test_retired_graphs_are_not_scanned() -> None:
    store = _FakeStore(
        graphs=[{"graph_id": GRAPH, "status": "retired"}],
        nodes={GRAPH: [_node("MSFT", NODE_ACTIVE)]},
        evidence={},
    )
    redis = _FakeRedis()
    report = await run_maintenance_pass(store, redis, now=NOW)  # type: ignore[arg-type]

    assert report.graphs_scanned == 0
    assert report.verdicts == ()
    assert redis.streams == []


async def test_second_run_after_flagging_is_a_no_op() -> None:
    # Idempotence: re-running the daily pass on unchanged evidence must not
    # append another history row or re-emit.
    stale_node = _node("MSFT", NODE_ACTIVE)
    evidence = {("MSFT", "end-user"): _aged(900)}
    store = _store([stale_node], evidence)
    redis = _FakeRedis()

    await run_maintenance_pass(store, redis, now=NOW)  # type: ignore[arg-type]
    stale_node["status"] = NODE_STALE_EVIDENCE  # what the DB now holds
    await run_maintenance_pass(store, redis, now=NOW)  # type: ignore[arg-type]

    assert len(store.history) == 1
    assert redis.streams == [STREAM_GRAPHS_UPDATED]


# --- the seed graph under the real threshold ----------------------------------


async def test_seed_graph_loads_already_stale() -> None:
    """The honest finding: every seed node rests on 2024 evidence, so the first
    real pass flags all four. A graph that reported "fresh" here would be the
    illusion of structural reasoning the spec warns about."""

    nodes = [_node(n.ticker, NODE_ACTIVE) for n in SEED_NODES]
    evidence = {(n.ticker, n.layer_role): n.evidence_observed_at for n in SEED_NODES}
    store = _store(nodes, evidence)
    redis = _FakeRedis()

    report = await run_maintenance_pass(store, redis, now=NOW)  # type: ignore[arg-type]

    assert len(report.flagged) == len(SEED_NODES)
    for verdict in report.flagged:
        assert verdict.age_days is not None
        assert verdict.age_days > DEFAULT_FRESHNESS_DAYS


def test_seed_evidence_dates_are_tz_aware_and_precede_load() -> None:
    for node in SEED_NODES:
        assert node.evidence_observed_at.tzinfo is not None
        assert node.evidence_observed_at.year == 2024
