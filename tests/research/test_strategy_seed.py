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
from shrap.research.strategy_seed.cli import (
    load_first,
    load_probe,
    render_list,
    render_probe_catalogue,
)
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
from shrap.research.strategy_seed.probe_strategies import (
    PROBE_SEEDS,
    compute_spec_hash,
    probe_record,
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


# --- Protocol-probe seeds -------------------------------------------------


def test_probe_seeds_have_valid_ulid_strategy_ids() -> None:
    """strategy_id is TEXT with no format validation, so nothing enforces this.

    A string called a ULID that is not one is a trap for anything that later
    parses or timestamp-sorts them, so it is asserted here instead.
    """

    crockford = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    for seed in PROBE_SEEDS:
        assert len(seed.strategy_id) == 26, seed.key
        assert set(seed.strategy_id) <= crockford, seed.key


def test_probe_seeds_have_distinct_ids_and_hashes_including_from_the_first() -> None:
    """Distinct spec_hash is what lets probes coexist with the first seed.

    load-* dedups on spec_hash, so a collision would silently skip the load.
    """

    ids = {s.strategy_id for s in PROBE_SEEDS} | {STRATEGY_ID}
    hashes = {compute_spec_hash(s) for s in PROBE_SEEDS} | {SPEC_HASH}
    assert len(ids) == len(PROBE_SEEDS) + 1
    assert len(hashes) == len(PROBE_SEEDS) + 1


def test_probe_seeds_pass_spec_hygiene_and_the_reference_rule() -> None:
    """Same bar the first seed meets — params the rule consumes, bounds honoured."""

    pipeline = _pipeline()
    for seed in PROBE_SEEDS:
        record = probe_record(seed)
        assert record.archetype == ARCHETYPE_INFRA_GRAPH_PLAY
        assert record.status == STATUS_HYPOTHESIS
        tickers = pipeline._check_spec_hygiene(record)
        assert tickers == ["XLE"]
        _validate_param_bounds(record.spec)
        rule = ReferenceTrendStrategy.from_spec(tickers[0], record.spec["params"])
        assert rule.fast == seed.fast
        assert rule.slow == seed.slow
        assert rule.fast < rule.slow


def test_probe_params_lie_inside_declared_bounds() -> None:
    for seed in PROBE_SEEDS:
        for name, value in (("fast", seed.fast), ("slow", seed.slow)):
            lo, hi = PARAM_BOUNDS[name]
            assert lo <= value <= hi, f"{seed.key}.{name}={value} outside [{lo}, {hi}]"


def test_probe_seeds_carry_the_anchor_the_evaluator_gate_requires() -> None:
    """Without an anchor the Evaluator returns KILL/anchor-not-live, engine_ran=False.

    Documented in probe_strategies as a gate artifact rather than a thesis claim.
    """

    for seed in PROBE_SEEDS:
        assert probe_record(seed).anchor == {"world_changer_id": WORLD_CHANGER_ID}


def test_control_trades_less_often_than_treatment() -> None:
    """The probes must differ in the one variable they exist to isolate."""

    by_key = {s.key: s for s in PROBE_SEEDS}
    control, treatment = by_key["trend-10-50"], by_key["trend-3-10"]
    assert treatment.fast < control.fast
    assert treatment.slow < control.slow


async def test_load_probe_is_idempotent_on_spec_hash() -> None:
    registry = FakeRegistry()

    first = await load_probe(registry, "trend-3-10")
    assert first.startswith("loaded:")
    second = await load_probe(registry, "trend-3-10")
    assert second.startswith("already present:")
    assert len(registry.by_id) == 1


async def test_load_probe_coexists_with_the_first_seed() -> None:
    registry = FakeRegistry()
    await load_first(registry)
    for seed in PROBE_SEEDS:
        await load_probe(registry, seed.key)

    assert len(registry.by_id) == len(PROBE_SEEDS) + 1
    assert len(registry.by_hash) == len(PROBE_SEEDS) + 1


def test_render_probe_catalogue_lists_every_probe() -> None:
    output = render_probe_catalogue()
    assert f"Probe seeds: {len(PROBE_SEEDS)}" in output
    for seed in PROBE_SEEDS:
        assert seed.key in output
        assert seed.strategy_id in output
