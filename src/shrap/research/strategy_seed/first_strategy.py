"""The firm's FIRST seeded strategy — a pipeline exerciser, not an edge.

Mirrors ``universe_curator/launch_list.py``: the strategy lives here as a
module-level code constant so the seed load is deterministic and idempotent.
``shrap-strategy-seed load-first`` inserts exactly this record into
``research.strategies`` at status ``hypothesis`` through the registry, and
``docs/strategies/fission-costcurve-seed-v1.md`` is its honest write-up.

HONESTY — say it plainly. This is a **pipeline-exercising seed**, not a
validated edge and not a real expression of the mass-manufactured-fission
thesis. It is a plain moving-average crossover on a single liquid ETF (``XLE``),
anchored on the promoted fission world-changer *only* so the funnel -> Evaluator
path can run end to end. A daily MA crossover trades a handful of times over a
multi-year window, so the Evaluator's 150-trade gate will almost certainly
**kill** it. That is the system working: it kills far more than it promotes. A
real fission expression, and a strategy with genuine edge, is research work —
not this card.

The ``params`` block is exactly what
:meth:`~shrap.research.strategy_evaluator.reference_strategy.ReferenceTrendStrategy.from_spec`
consumes (``fast``, ``slow``, ``target_weight``, ``long_only``); ``param_bounds``
is copied from that rule's own declared bounds so there is no drift and every
numeric param lies inside its ``[lo, hi]``. This module carries no
numpy/pandas-bearing import so the ``shrap-strategy-seed`` CLI stays light.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from shrap.research.strategy_evaluator.reference_strategy import (
    DEFAULT_TARGET_WEIGHT,
    PARAM_BOUNDS,
)
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord

# Stable identity for the seed (a fixed ULID). The row's identity never changes
# across reloads, so ``list`` and the evaluator always see the same strategy_id.
STRATEGY_ID = "01KYGTRTTQA9X2B2E16N4SBPTG"

STRATEGY_NAME = "Fission cost-curve — pipeline seed v1"

# ``infra-graph-play`` is the only archetype the Evaluator's first card can run.
# Kept as a literal (not imported from the pipeline) so this module needs no
# numpy-bearing import; a test pins it to ``pipeline.ARCHETYPE_INFRA_GRAPH_PLAY``.
ARCHETYPE = "infra-graph-play"

SOURCE = "mike-seed"

# The promoted mass-manufactured-fission world-changer (promoted 2026-07-18; see
# docs/status/recent-changes.md). The Evaluator refuses to run the backtest
# unless this is still ``promoted`` in research.world_changers.
WORLD_CHANGER_ID = "01KXVVPXDMB4HS1QNRPQWRP1RX"

ANCHOR: dict[str, Any] = {"world_changer_id": WORLD_CHANGER_ID}

THESIS = (
    "Pipeline-exercising proxy: if mass-manufactured fission drives energy "
    "$/kWh down a learning curve, energy-sector equity (XLE placeholder) trends "
    "with the build-out — not a validated edge, and expected to be killed."
)

# XLE — Energy Select Sector SPDR. A locked Tier 3 launch name
# (docs/universe/README.md; universe_curator/launch_list.py) with deep history,
# used here as a nominal, honest placeholder for a real fission expression.
TICKERS: dict[str, Any] = {"long": ["XLE"], "short": []}

# Exactly the keys ReferenceTrendStrategy.from_spec consumes. fast < slow, and
# every numeric value sits inside PARAM_BOUNDS below.
_PARAMS: dict[str, Any] = {
    "fast": 20,
    "slow": 100,
    "target_weight": DEFAULT_TARGET_WEIGHT,
    "long_only": True,
}

# Copied from the reference rule's own declared bounds so there is no drift.
# Serialized as ``[lo, hi]`` lists (JSONB), which the hygiene check accepts;
# ``long_only`` is non-numeric and needs no bound.
_PARAM_BOUNDS: dict[str, list[float]] = {name: [lo, hi] for name, (lo, hi) in PARAM_BOUNDS.items()}

SPEC: dict[str, Any] = {"params": _PARAMS, "param_bounds": _PARAM_BOUNDS}

# Neutral sizing: no regime opinion (1.0 everywhere). The four regime names
# mirror src/shrap/intelligence/regime/profiles.py; a real strategy would tilt
# these. The column is nullable — a neutral map is the more self-documenting
# default for the first seed.
REGIME_SIZING_MODIFIER: dict[str, float] = {
    "late-cycle-melt-up": 1.0,
    "crisis-recovery": 1.0,
    "stagflation": 1.0,
    "wartime": 1.0,
}

# Honest, observable falsifiers: the fission-thesis breakers (which drop the
# world-changer and so kill the anchor) plus plain strategy-performance gates.
KILL_CRITERIA: list[str] = [
    "world-changer anchor no longer 'promoted' in research.world_changers "
    "(mass-manufactured fission thesis broken)",
    "no unsubsidized hyperscaler/industrial nuclear PPA by the world-changer's "
    "falsifier horizon (2027-12)",
    "nth-of-a-kind $/kW flattens across two vendor cohorts (learning curve stalls)",
    "fewer than 150 trades over the walk-forward window — too few to evaluate "
    "(this daily MA rule is expected to fail this gate)",
    "out-of-sample Sharpe at or below the promote floor, or edge that dies under "
    "the realistic-friction stress test",
]

# Provenance pointer for the record's ``code_ref`` column — this constant.
CODE_REF = "src/shrap/research/strategy_seed/first_strategy.py"


def _compute_spec_hash() -> str:
    """Deterministic content hash over the seed's identifying material.

    ``spec_hash`` is the registry's unique dedup key. Hashing the canonical JSON
    of the identifying fields makes the seed's idempotency key stable across
    reloads and independent of insertion order.
    """

    material = json.dumps(
        {
            "name": STRATEGY_NAME,
            "archetype": ARCHETYPE,
            "anchor": ANCHOR,
            "tickers": TICKERS,
            "spec": SPEC,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


SPEC_HASH = _compute_spec_hash()


def first_strategy_record() -> StrategyRecord:
    """Build the seed as a :class:`StrategyRecord` at status ``hypothesis``."""

    return StrategyRecord(
        strategy_id=STRATEGY_ID,
        name=STRATEGY_NAME,
        version=1,
        archetype=ARCHETYPE,
        status=STATUS_HYPOTHESIS,
        source=SOURCE,
        thesis=THESIS,
        anchor=dict(ANCHOR),
        tickers={"long": list(TICKERS["long"]), "short": list(TICKERS["short"])},
        spec={
            "params": dict(_PARAMS),
            "param_bounds": {k: list(v) for k, v in _PARAM_BOUNDS.items()},
        },
        spec_hash=SPEC_HASH,
        regime_sizing_modifier=dict(REGIME_SIZING_MODIFIER),
        kill_criteria=list(KILL_CRITERIA),
        code_ref=CODE_REF,
        created_at=None,
        updated_at=None,
    )


__all__ = [
    "ANCHOR",
    "ARCHETYPE",
    "CODE_REF",
    "KILL_CRITERIA",
    "REGIME_SIZING_MODIFIER",
    "SOURCE",
    "SPEC",
    "SPEC_HASH",
    "STRATEGY_ID",
    "STRATEGY_NAME",
    "THESIS",
    "TICKERS",
    "WORLD_CHANGER_ID",
    "first_strategy_record",
]
