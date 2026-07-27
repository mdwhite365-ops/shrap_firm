"""Tests for the first strategy seed and the ``shrap-strategy-seed`` CLI.

The seed constant must pass the Evaluator's spec hygiene unchanged, its params
must be exactly what the reference rule consumes, and the CLI load must be
idempotent (a second load creates no duplicate spec_hash row).
"""

from __future__ import annotations

from typing import Any

from shrap.research.strategy_evaluator.pipeline import (
    ARCHETYPE_INFRA_GRAPH_PLAY,
    EvaluationPipeline,
    _validate_param_bounds,
)
from shrap.research.strategy_evaluator.reference_strategy import (
    PARAM_BOUNDS,
    ReferenceTrendStrategy,
)
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord
from shrap.research.strategy_seed.cli import load_first, render_list
from shrap.research.strategy_seed.first_strategy import (
    ARCHETYPE,
    KILL_CRITERIA,
    SPEC,
    SPEC_HASH,
    STRATEGY_ID,
    TICKERS,
    WORLD_CHANGER_ID,
    first_strategy_record,
)
from shrap.research.universe_curator.launch_list import LAUNCH_LIST


class _DummyPort:
    """Stands in for a pipeline port that spec hygiene never actually calls."""


def _pipeline() -> EvaluationPipeline:
    dummy = _DummyPort()
    return EvaluationPipeline(
        registry=dummy,  # type: ignore[arg-type]
        reader=dummy,  # type: ignore[arg-type]
        store=dummy,  # type: ignore[arg-type]
        publisher=dummy,  # type: ignore[arg-type]
    )


class FakeRegistry:
    """In-memory stand-in for PostgresStrategyRegistry (the seed CLI surface)."""

    def __init__(self) -> None:
        self.by_hash: dict[str, StrategyRecord] = {}
        self.by_id: dict[str, StrategyRecord] = {}
        self.schema_calls = 0

    async def ensure_schema(self) -> None:
        self.schema_calls += 1

    async def get_by_spec_hash(self, spec_hash: str) -> StrategyRecord | None:
        return self.by_hash.get(spec_hash)

    async def register(
        self,
        record: StrategyRecord,
        *,
        reason: str,
        actor: str,
        trigger_kind: str = "registration",
        trigger_ref: str | None = None,
    ) -> bool:
        if record.strategy_id in self.by_id:
            return False
        self.by_id[record.strategy_id] = record
        self.by_hash[record.spec_hash] = record
        return True

    async def list_all(self) -> list[StrategyRecord]:
        return list(self.by_id.values())


# --- the seed constant -------------------------------------------------------


def test_first_strategy_passes_spec_hygiene() -> None:
    record = first_strategy_record()
    assert _pipeline()._check_spec_hygiene(record) == ["XLE"]


def test_seed_shape_matches_evaluator_expectations() -> None:
    record = first_strategy_record()
    assert record.status == STATUS_HYPOTHESIS
    assert record.source == "mike-seed"
    assert ARCHETYPE == ARCHETYPE_INFRA_GRAPH_PLAY
    assert record.archetype == ARCHETYPE_INFRA_GRAPH_PLAY
    assert record.anchor == {"world_changer_id": WORLD_CHANGER_ID}
    assert record.tickers == {"long": ["XLE"], "short": []}
    assert KILL_CRITERIA and record.kill_criteria == KILL_CRITERIA
    assert "regime_gate" not in record.spec


def test_params_within_declared_bounds() -> None:
    # The pipeline's own validator must not raise on the seed spec.
    _validate_param_bounds(SPEC)
    params: dict[str, Any] = SPEC["params"]
    bounds: dict[str, Any] = SPEC["param_bounds"]
    for name, (lo, hi) in PARAM_BOUNDS.items():
        assert bounds[name] == [lo, hi]
        assert lo <= float(params[name]) <= hi


def test_params_match_reference_from_spec() -> None:
    # The seed's params are exactly what ReferenceTrendStrategy.from_spec consumes.
    strat = ReferenceTrendStrategy.from_spec("XLE", SPEC["params"])
    assert strat.ticker == "XLE"
    assert strat.fast == 20
    assert strat.slow == 100
    assert strat.fast < strat.slow  # __post_init__ invariant
    assert strat.long_only is True


def test_xle_is_a_locked_tier3_launch_name() -> None:
    assert "XLE" in {entry.ticker for entry in LAUNCH_LIST}
    assert TICKERS["long"] == ["XLE"]


def test_spec_hash_is_stable_and_deterministic() -> None:
    assert SPEC_HASH.startswith("sha256:")
    assert first_strategy_record().spec_hash == SPEC_HASH


# --- the CLI -----------------------------------------------------------------


async def test_load_first_inserts_then_is_idempotent() -> None:
    registry = FakeRegistry()

    first = await load_first(registry)
    assert first.startswith("loaded:")
    assert STRATEGY_ID in first
    assert len(registry.by_id) == 1

    second = await load_first(registry)
    assert second.startswith("already present:")
    assert "skipped" in second
    # No duplicate: still exactly one row, keyed on the same spec_hash.
    assert len(registry.by_id) == 1
    assert len(registry.by_hash) == 1


async def test_render_list_shows_findable_rows() -> None:
    registry = FakeRegistry()
    await load_first(registry)

    output = render_list(await registry.list_all())

    assert "Strategies: 1" in output
    assert STRATEGY_ID in output
    assert "<infra-graph-play>" in output
    assert f"[{STATUS_HYPOTHESIS}]" in output
    assert "XLE" in output
