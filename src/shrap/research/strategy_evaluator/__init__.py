"""Strategy Evaluator — the firm's deterministic gatekeeper (first card).

This package is the numerical core and on-demand run path of the Strategy
Evaluator (``docs/agents/research/strategy-evaluator.md``). It is intentionally
a machine, not a judgment call: no LLM touches any pass/fail/promote decision.
The verdict is a pure function of measured metrics against a documented
protocol (``docs/research/eval-protocol.md``, v0.1).

What this card implements (see the protocol doc for the authoritative list):

- A pure, deterministic walk-forward evaluation engine (:mod:`.engine`) with a
  realistic transaction-cost model (:mod:`.costs`) and a realistic-friction
  stress test.
- A single interface seam, :class:`.strategy.StrategySignal`, that every future
  strategy implements, plus one labelled REFERENCE implementation
  (:mod:`.reference_strategy`) so the pipeline runs end to end.
- A verdict mapping (:mod:`.verdict`), an evaluation pipeline (:mod:`.pipeline`)
  that promotes ``hypothesis → paper`` / kills ``hypothesis → killed`` through
  the strategy registry, an append-only ``research.evaluations`` store
  (:mod:`.store`), and an on-demand CLI (:mod:`.cli`).

Kept honest by the vision's second operating principle — *kill more
aggressively than you promote*. Passing every test means "we have failed to
disprove edge under our test protocol," never "edge is real."
"""

from __future__ import annotations

from shrap.research.strategy_evaluator.engine import (
    PROTOCOL_VERSION,
    EvalConfig,
)
from shrap.research.strategy_evaluator.verdict import (
    VERDICT_HOLD,
    VERDICT_KILL,
    VERDICT_PROMOTE,
    map_verdict,
)

__all__ = [
    "PROTOCOL_VERSION",
    "VERDICT_HOLD",
    "VERDICT_KILL",
    "VERDICT_PROMOTE",
    "EvalConfig",
    "map_verdict",
]
