"""The hand-seeded graph is honest: valid taxonomy roles, Tier-3 tickers, and
an idempotent load that emits the right events."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from shrap.research.infra_mapper.cli import (
    STREAM_GRAPHS_ADDED,
    STREAM_GRAPHS_INITIALIZED,
    load_seed_graph,
)
from shrap.research.infra_mapper.first_graph import (
    SEED_NODES,
    SEED_WORLD_CHANGER_ID,
    SeedNode,
)
from shrap.research.infra_mapper.store import (
    CONFIDENCE_LEVELS,
    CRITICAL_PATH_STATUSES,
)
from shrap.research.universe_curator.launch_list import LAUNCH_LIST

# The closed layer-role list from docs/research/layer-role-taxonomy.md.
VALID_LAYER_ROLES = frozenset(
    {
        "fab",
        "litho",
        "materials",
        "packaging",
        "memory",
        "interconnect",
        "networking-silicon",
        "networking-optical",
        "power-gen",
        "power-delivery",
        "cooling",
        "EDA-tools",
        "foundry-services",
        "CDMO",
        "raw-inputs",
        "end-user",
    }
)

_TIER3 = frozenset(name.ticker for name in LAUNCH_LIST)


def test_seed_nodes_use_valid_taxonomy_roles_and_enums() -> None:
    assert SEED_NODES  # non-empty
    for node in SEED_NODES:
        assert node.layer_role in VALID_LAYER_ROLES, node.layer_role
        assert node.confidence in CONFIDENCE_LEVELS
        assert node.critical_path_status in CRITICAL_PATH_STATUSES
        assert node.kill_criteria  # every node declares kill criteria
        assert node.evidence_ref.strip()


def test_seed_tickers_are_all_tier3_launch_names() -> None:
    for node in SEED_NODES:
        assert node.ticker in _TIER3, f"{node.ticker} is not a Tier-3 launch name"


class _FakeStore:
    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self._existing = existing
        self.inserted_graph: dict[str, Any] | None = None
        self.nodes: list[dict[str, Any]] = []
        self.evidence: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []

    async def get_graph_by_world_changer(self, world_changer_id: str) -> dict[str, Any] | None:
        return self._existing

    async def insert_graph(
        self, *, graph_id: str, world_changer_id: str, title: str, status: str
    ) -> None:
        self.inserted_graph = {"graph_id": graph_id, "world_changer_id": world_changer_id}

    async def upsert_node(self, **kwargs: Any) -> None:
        self.nodes.append(kwargs)

    async def insert_evidence(self, **kwargs: Any) -> None:
        self.evidence.append(kwargs)

    async def record_history(self, **kwargs: Any) -> None:
        self.history.append(kwargs)


class _FakeRedis:
    def __init__(self) -> None:
        self.streams: list[str] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.streams.append(stream)
        return "1-0"


async def test_load_seed_graph_inserts_and_emits() -> None:
    store = _FakeStore(existing=None)
    redis = _FakeRedis()
    summary = await load_seed_graph(store, redis)  # type: ignore[arg-type]

    assert store.inserted_graph is not None
    assert len(store.nodes) == len(SEED_NODES)
    assert len(store.evidence) == len(SEED_NODES)
    assert len(store.history) == len(SEED_NODES)
    # one graphs-added per node + exactly one graphs-initialized
    assert redis.streams.count(STREAM_GRAPHS_ADDED) == len(SEED_NODES)
    assert redis.streams.count(STREAM_GRAPHS_INITIALIZED) == 1
    assert "initialized" in summary


async def test_load_seed_graph_is_idempotent() -> None:
    store = _FakeStore(existing={"graph_id": "g-existing"})
    redis = _FakeRedis()
    summary = await load_seed_graph(store, redis)  # type: ignore[arg-type]

    assert "skipped" in summary
    assert store.inserted_graph is None
    assert store.nodes == []
    assert redis.streams == []


def test_evidence_observed_at_is_a_real_datetime() -> None:
    # Guard the _utcnow typing fix: history/evidence must carry a tz-aware time.
    from shrap.research.infra_mapper.cli import _utcnow

    now = _utcnow()
    assert isinstance(now, datetime)
    assert now.tzinfo is not None


def test_seed_nodes_are_named_tuples() -> None:
    assert all(isinstance(n, SeedNode) for n in SEED_NODES)
    assert SEED_WORLD_CHANGER_ID.startswith("01")
