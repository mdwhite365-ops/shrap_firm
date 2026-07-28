"""Protocol-probe seeds — deliberately uninteresting strategies with a purpose.

The firm's first verdict (2026-07-27) killed the original seed on
``insufficient-trades`` with 20 trades. That exercised exactly one branch of
:func:`~shrap.research.strategy_evaluator.verdict.map_verdict`. Four branches
have never run against real data: ``no-edge``, ``fails-friction-stress``,
``below-sharpe-floor``, and ``promote``.

These two seeds are a **control and a treatment**, differing only in moving
average windows so that trade frequency is the single isolated variable:

- ``trend-10-50`` (control) — a near-copy of the original at a slightly faster
  cadence. Expected to die the same way, which is the point: it confirms the
  ``insufficient-trades`` branch reproduces rather than having been a fluke of
  one parameter pair.
- ``trend-3-10`` (treatment) — fast enough that the trade count *may* clear the
  150 gate. If it does, the Evaluator reaches a verdict branch nothing has
  tested.

**No prediction is attached to either.** Whether ``trend-3-10`` clears 150 is an
empirical question the run answers; both outcomes are informative, and writing
an expectation here would invite reading the result to match it.

HONESTY — the anchor is a gate artifact, not a thesis. Both seeds carry the
mass-manufactured-fission ``world_changer_id`` for one reason: the Evaluator
maps a missing anchor to ``KILL / anchor-not-live`` with ``engine_ran=False``
(``pipeline.py``), so a strategy with no anchor is killed before the backtest
runs. **Neither of these is a fission expression in any sense** — they are
moving-average crossovers on an energy ETF, seeded to test the evaluation
protocol. That a pure protocol probe is forced to claim a world-changer is
itself the defect recorded in KI-013 and ADR-0013: the anchor gate is a
Framework #1 construct applied universally.

Carries no numpy/pandas-bearing import so the ``shrap-strategy-seed`` CLI stays
light, matching ``first_strategy.py``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, NamedTuple

from shrap.research.strategy_evaluator.reference_strategy import (
    DEFAULT_TARGET_WEIGHT,
    PARAM_BOUNDS,
)
from shrap.research.strategy_registry import STATUS_HYPOTHESIS, StrategyRecord
from shrap.research.strategy_seed.first_strategy import (
    ARCHETYPE,
    SOURCE,
    TICKERS,
    WORLD_CHANGER_ID,
)

CODE_REF = "src/shrap/research/strategy_seed/probe_strategies.py"

# Neutral sizing: these express no regime opinion. Mirrors the first seed.
REGIME_SIZING_MODIFIER: dict[str, float] = {
    "late-cycle-melt-up": 1.0,
    "crisis-recovery": 1.0,
    "stagflation": 1.0,
    "wartime": 1.0,
}

# Serialized as [lo, hi] lists (JSONB), copied from the reference rule's own
# declared bounds so they cannot drift.
_PARAM_BOUNDS: dict[str, list[float]] = {name: [lo, hi] for name, (lo, hi) in PARAM_BOUNDS.items()}


class ProbeSeed(NamedTuple):
    """One protocol-probe strategy definition."""

    key: str
    strategy_id: str
    name: str
    fast: int
    slow: int
    role: str
    thesis: str


PROBE_SEEDS: tuple[ProbeSeed, ...] = (
    ProbeSeed(
        key="trend-10-50",
        # Fixed, real ULIDs so reloads and evaluations always reference the same
        # rows. Generated once and pinned; `strategy_id` is TEXT with no format
        # validation, so a readable placeholder would have worked — but a string
        # called a ULID that is not one is a trap for anything that later parses
        # or timestamp-sorts them. A test asserts the Crockford alphabet.
        strategy_id="01KYKK486Z7CD7P1CEH355B9K8",
        name="Protocol probe — trend 10/50 (control)",
        fast=10,
        slow=50,
        role="control",
        thesis=(
            "Protocol control, not an edge claim. A near-copy of the first seed at a "
            "faster cadence, seeded to confirm the insufficient-trades branch "
            "reproduces across parameter pairs rather than being specific to 20/100."
        ),
    ),
    ProbeSeed(
        key="trend-3-10",
        strategy_id="01KYKK486Z7CD7P1CEH355B9K9",
        name="Protocol probe — trend 3/10 (treatment)",
        fast=3,
        slow=10,
        role="treatment",
        thesis=(
            "Protocol treatment, not an edge claim. Fast enough that the walk-forward "
            "trade count may clear the 150 gate, which would take the Evaluator into a "
            "verdict branch no real evaluation has reached. No outcome is predicted."
        ),
    ),
)

# Shared falsifiers. The first two are the fission-thesis breakers inherited with
# the anchor; the rest are the protocol gates these seeds exist to probe.
_KILL_CRITERIA: tuple[str, ...] = (
    "world-changer anchor no longer 'promoted' in research.world_changers "
    "(mass-manufactured fission thesis broken)",
    "fewer than 150 trades over the walk-forward window — too few to evaluate",
    "out-of-sample Sharpe at or below zero — no edge to measure",
    "edge does not survive the realistic-friction stress test",
    "out-of-sample Sharpe below the promote floor",
)


def _params(fast: int, slow: int) -> dict[str, Any]:
    """Exactly the keys ReferenceTrendStrategy.from_spec consumes."""

    return {
        "fast": fast,
        "slow": slow,
        "target_weight": DEFAULT_TARGET_WEIGHT,
        "long_only": True,
    }


def _spec(fast: int, slow: int) -> dict[str, Any]:
    return {
        "params": _params(fast, slow),
        "param_bounds": {k: list(v) for k, v in _PARAM_BOUNDS.items()},
    }


def compute_spec_hash(seed: ProbeSeed) -> str:
    """Deterministic dedup key over the seed's identifying material.

    Identical in shape to ``first_strategy._compute_spec_hash`` so all seeds
    hash the same way. Distinct ``params`` are what make each probe's hash
    differ from the first seed's — which is what allows them to be registered
    alongside it rather than being skipped as duplicates.
    """

    material = json.dumps(
        {
            "name": seed.name,
            "archetype": ARCHETYPE,
            "anchor": {"world_changer_id": WORLD_CHANGER_ID},
            "tickers": TICKERS,
            "spec": _spec(seed.fast, seed.slow),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def probe_record(seed: ProbeSeed) -> StrategyRecord:
    """Build one probe seed as a :class:`StrategyRecord` at ``hypothesis``."""

    return StrategyRecord(
        strategy_id=seed.strategy_id,
        name=seed.name,
        version=1,
        archetype=ARCHETYPE,
        status=STATUS_HYPOTHESIS,
        source=SOURCE,
        thesis=seed.thesis,
        anchor={"world_changer_id": WORLD_CHANGER_ID},
        tickers={"long": list(TICKERS["long"]), "short": list(TICKERS["short"])},
        spec=_spec(seed.fast, seed.slow),
        spec_hash=compute_spec_hash(seed),
        regime_sizing_modifier=dict(REGIME_SIZING_MODIFIER),
        kill_criteria=list(_KILL_CRITERIA),
        code_ref=CODE_REF,
        created_at=None,
        updated_at=None,
    )


PROBE_SEEDS_BY_KEY: dict[str, ProbeSeed] = {s.key: s for s in PROBE_SEEDS}

__all__ = [
    "CODE_REF",
    "PROBE_SEEDS",
    "PROBE_SEEDS_BY_KEY",
    "ProbeSeed",
    "compute_spec_hash",
    "probe_record",
]
