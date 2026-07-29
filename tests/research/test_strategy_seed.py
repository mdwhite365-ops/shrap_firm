"""Tests for the first strategy seed and the ``shrap-strategy-seed`` CLI.

The seed constant must pass the Evaluator's spec hygiene unchanged, its params
must be exactly what the reference rule consumes, and the CLI load must be
idempotent (a second load creates no duplicate spec_hash row).
"""

from __future__ import annotations

from typing import Any

import pytest

from shrap.research.strategy_evaluator.cross_sectional import CrossSectionalMomentumStrategy
from shrap.research.strategy_evaluator.pipeline import (
    ARCHETYPE_INFRA_GRAPH_PLAY,
    ARCHETYPE_POLICIES,
    ARCHETYPE_TECHNICAL_CATALYST,
    RULE_CROSS_SECTIONAL_MOMENTUM,
    EvaluationPipeline,
    _default_strategy_factory,
    _validate_param_bounds,
)
from shrap.research.strategy_evaluator.reference_strategy import (
    PARAM_BOUNDS,
    ReferenceTrendStrategy,
)
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord
from shrap.research.strategy_seed.cli import (
    load_first,
    load_momentum,
    load_probe,
    load_technical,
    render_list,
    render_momentum_catalogue,
    render_probe_catalogue,
    render_technical_catalogue,
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
from shrap.research.strategy_seed.technical_strategies import (
    MOMENTUM_SEEDS,
    TECHNICAL_SEEDS,
    compute_momentum_spec_hash,
    momentum_kill_criteria,
    momentum_record,
    technical_record,
)
from shrap.research.strategy_seed.technical_strategies import (
    compute_spec_hash as technical_seed_hash,
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


# --- Framework #3 seeds (ADR-0013) -------------------------------------------


def test_technical_seed_carries_no_anchor_at_all() -> None:
    """The point of the module. An anchor here would be the reintroduced lie.

    The probes had to claim a fission thesis to get past the anchor gate; ADR-0013
    made the gate archetype-conditional so a technical strategy no longer has to.
    """

    for seed in TECHNICAL_SEEDS:
        record = technical_record(seed)
        assert record.anchor == {}
        assert record.archetype == ARCHETYPE_TECHNICAL_CATALYST


def test_technical_seed_is_evaluable_and_the_anchor_is_never_consulted() -> None:
    """Spec hygiene admits it, and the policy says the anchor is not a gate."""

    pipeline = _pipeline()
    for seed in TECHNICAL_SEEDS:
        record = technical_record(seed)
        tickers = pipeline._check_spec_hygiene(record)
        assert tickers == [seed.ticker]
        _validate_param_bounds(record.spec)
        assert ARCHETYPE_POLICIES[record.archetype].requires_anchor is False


def test_technical_seed_params_drive_the_reference_rule() -> None:
    for seed in TECHNICAL_SEEDS:
        record = technical_record(seed)
        rule = ReferenceTrendStrategy.from_spec(seed.ticker, record.spec["params"])
        assert rule.ticker == seed.ticker
        assert rule.fast == seed.fast
        assert rule.slow == seed.slow
        assert rule.fast < rule.slow


def test_technical_seed_tickers_are_tier3_launch_names() -> None:
    launch = {entry.ticker for entry in LAUNCH_LIST}
    for seed in TECHNICAL_SEEDS:
        assert seed.ticker in launch, seed.key


def test_technical_seed_ids_are_real_ulids_and_globally_distinct() -> None:
    crockford = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    ids = {s.strategy_id for s in TECHNICAL_SEEDS}
    for seed in TECHNICAL_SEEDS:
        assert len(seed.strategy_id) == 26, seed.key
        assert set(seed.strategy_id) <= crockford, seed.key
    # Distinct from every other seed family, or load-* would dedup them away.
    others = {s.strategy_id for s in PROBE_SEEDS} | {STRATEGY_ID}
    assert not (ids & others)


def test_technical_spec_hashes_are_distinct_from_every_other_seed() -> None:
    """spec_hash is the dedup key; a collision would silently skip the load."""

    technical = {technical_seed_hash(s) for s in TECHNICAL_SEEDS}
    others = {compute_spec_hash(s) for s in PROBE_SEEDS} | {SPEC_HASH}
    assert len(technical) == len(TECHNICAL_SEEDS)
    assert not (technical & others)


def test_technical_kill_criteria_name_no_world_changer() -> None:
    """A Framework #3 strategy has no thesis a world-changer could break.

    The probes inherited that falsifier with their borrowed anchor, and it was
    already satisfied on the day they were written.
    """

    for seed in TECHNICAL_SEEDS:
        record = technical_record(seed)
        assert record.kill_criteria
        joined = " ".join(record.kill_criteria).lower()
        assert "world-changer" not in joined
        assert "world changer" not in joined


def test_technical_seed_declares_no_regime_gate() -> None:
    """Regime is a sizing modifier; a gate is refused by spec hygiene."""

    for seed in TECHNICAL_SEEDS:
        record = technical_record(seed)
        assert "regime_gate" not in record.spec
        assert record.regime_sizing_modifier
        assert set(record.regime_sizing_modifier.values()) == {1.0}


async def test_load_technical_is_idempotent_and_coexists_with_the_other_seeds() -> None:
    registry = FakeRegistry()
    await load_first(registry)
    for probe in PROBE_SEEDS:
        await load_probe(registry, probe.key)

    first = await load_technical(registry, "spy-trend-5-20")
    assert first.startswith("loaded:")
    second = await load_technical(registry, "spy-trend-5-20")
    assert second.startswith("already present:")

    expected = 1 + len(PROBE_SEEDS) + len(TECHNICAL_SEEDS)
    assert len(registry.by_id) == expected
    assert len(registry.by_hash) == expected


async def test_load_technical_refuses_an_unknown_key() -> None:
    registry = FakeRegistry()
    with pytest.raises(SystemExit, match="unknown technical seed"):
        await load_technical(registry, "does-not-exist")


def test_render_technical_catalogue_lists_every_seed() -> None:
    output = render_technical_catalogue()
    assert f"Technical seeds: {len(TECHNICAL_SEEDS)}" in output
    for seed in TECHNICAL_SEEDS:
        assert seed.key in output
        assert seed.strategy_id in output
        assert seed.ticker in output


# --- cross-sectional momentum seed -------------------------------------------


def test_momentum_seed_trades_the_whole_launch_universe() -> None:
    """Breadth is what lets a daily rule clear the trade-count gate honestly.

    The engine counts a trade per ticker per weight change, so 50 names supply
    the sample size a single-name daily rule cannot — 89 trades on one ticker
    against 28,139 on fifty, measured in PR #110.
    """

    launch = {entry.ticker for entry in LAUNCH_LIST}
    for seed in MOMENTUM_SEEDS:
        record = momentum_record(seed)
        assert set(record.tickers["long"]) == launch
        assert record.tickers["short"] == []


def test_momentum_seed_tickers_track_the_launch_list_rather_than_a_copy() -> None:
    """A hand-listed universe would drift out of step with Tier 3 silently."""

    for seed in MOMENTUM_SEEDS:
        assert set(seed.tickers) == {entry.ticker for entry in LAUNCH_LIST}


def test_momentum_seed_names_the_cross_sectional_rule() -> None:
    for seed in MOMENTUM_SEEDS:
        record = momentum_record(seed)
        assert record.spec["rule"] == RULE_CROSS_SECTIONAL_MOMENTUM
        assert record.archetype == ARCHETYPE_TECHNICAL_CATALYST
        assert record.anchor == {}


def test_momentum_seed_builds_the_rule_it_declares() -> None:
    for seed in MOMENTUM_SEEDS:
        record = momentum_record(seed)
        rule = _default_strategy_factory(record, list(seed.tickers))
        assert isinstance(rule, CrossSectionalMomentumStrategy)
        assert (rule.lookback, rule.skip, rule.top_n) == (seed.lookback, seed.skip, seed.top_n)


def test_momentum_seed_passes_spec_hygiene_for_every_ticker() -> None:
    for seed in MOMENTUM_SEEDS:
        record = momentum_record(seed)
        assert _pipeline()._check_spec_hygiene(record) == sorted(seed.tickers)
        _validate_param_bounds(record.spec)


def test_momentum_kill_criteria_name_costs_and_the_benchmark() -> None:
    """The two ways this specific effect is most likely to die.

    A monthly top-decile rotation over 50 names has high turnover, and momentum
    is small enough per name that friction is the likeliest killer. Losing to
    buy-and-hold is the other — and now a measurable kill, not a hunch.
    """

    joined = " ".join(momentum_kill_criteria()).lower()
    assert "cost" in joined
    assert "buy-and-hold" in joined
    assert "information ratio" in joined
    assert "world-changer" not in joined


def test_momentum_seed_id_is_a_real_ulid_distinct_from_every_other_seed() -> None:
    crockford = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    others = {s.strategy_id for s in PROBE_SEEDS} | {s.strategy_id for s in TECHNICAL_SEEDS}
    others.add(STRATEGY_ID)
    for seed in MOMENTUM_SEEDS:
        assert len(seed.strategy_id) == 26
        assert set(seed.strategy_id) <= crockford
        assert seed.strategy_id not in others


def test_momentum_spec_hash_is_distinct_from_every_other_seed() -> None:
    hashes = {compute_momentum_spec_hash(s) for s in MOMENTUM_SEEDS}
    others = {compute_spec_hash(s) for s in PROBE_SEEDS}
    others |= {technical_seed_hash(s) for s in TECHNICAL_SEEDS}
    others.add(SPEC_HASH)
    assert len(hashes) == len(MOMENTUM_SEEDS)
    assert not (hashes & others)


async def test_load_momentum_is_idempotent_and_coexists_with_every_seed() -> None:
    registry = FakeRegistry()
    await load_first(registry)
    for probe in PROBE_SEEDS:
        await load_probe(registry, probe.key)
    for tech in TECHNICAL_SEEDS:
        await load_technical(registry, tech.key)

    first = await load_momentum(registry, "xs-momentum-126-21-10")
    assert first.startswith("loaded:")
    second = await load_momentum(registry, "xs-momentum-126-21-10")
    assert second.startswith("already present:")

    # Every other momentum seed loads ALONGSIDE it, not instead of it. Declared
    # order matters against a real registry: a revision's parent must already be
    # registered or `register` refuses it.
    for seed in MOMENTUM_SEEDS:
        if seed.key != "xs-momentum-126-21-10":
            assert (await load_momentum(registry, seed.key)).startswith("loaded:")

    expected = 1 + len(PROBE_SEEDS) + len(TECHNICAL_SEEDS) + len(MOMENTUM_SEEDS)
    assert len(registry.by_id) == expected
    assert len(registry.by_hash) == expected


async def test_load_momentum_warns_about_its_data_prerequisites() -> None:
    """50 tickers means 50 chances to be refused; the message must say so."""

    out = await load_momentum(FakeRegistry(), "xs-momentum-126-21-10")
    assert "daily bars" in out
    assert "universe_tiers" in out
    assert "REFUSED" in out


async def test_load_momentum_refuses_an_unknown_key() -> None:
    with pytest.raises(SystemExit, match="unknown momentum seed"):
        await load_momentum(FakeRegistry(), "nope")


def test_render_momentum_catalogue_lists_every_seed() -> None:
    out = render_momentum_catalogue()
    assert f"Momentum seeds: {len(MOMENTUM_SEEDS)}" in out
    for seed in MOMENTUM_SEEDS:
        assert seed.key in out
        assert seed.strategy_id in out
