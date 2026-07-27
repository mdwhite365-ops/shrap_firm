"""REFERENCE strategy — a simple, honest trend rule to exercise the pipeline.

This is **not** a proposed edge. It is one concrete implementation of the
:class:`~shrap.research.strategy_evaluator.strategy.StrategySignal` seam so the
Evaluator runs end to end before the strategy-authoring system exists. The
strategy-authoring layer (a DSL / plugin registry that maps a
``research.strategies`` row to its real signal code) is an explicitly deferred
later card; until it lands, an ``infra-graph-play`` strategy record is evaluated
by instantiating *this* rule from the record's parameters.

The rule: a moving-average crossover on daily closes of a single ticker (the
first ticker in the panel). Long the configured weight when the fast SMA is
above the slow SMA; otherwise flat (long-only) or short (long/short). Boring by
design.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from shrap.research.strategy_evaluator.strategy import PanelWindow

REFERENCE_STRATEGY_NAME = "reference-ma-crossover"

DEFAULT_FAST = 10
DEFAULT_SLOW = 30
DEFAULT_TARGET_WEIGHT = 1.0

# Bounds the pipeline validates the record's params against ("parameter ranges
# bounded", spec step 1). Declared here so the reference strategy is a complete,
# self-describing example of a well-formed strategy spec.
PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "fast": (2.0, 100.0),
    "slow": (5.0, 400.0),
    "target_weight": (0.0, 1.0),
}


@dataclass(frozen=True, slots=True)
class ReferenceTrendStrategy:
    """Moving-average crossover on one ticker's daily closes."""

    ticker: str
    fast: int = DEFAULT_FAST
    slow: int = DEFAULT_SLOW
    target_weight: float = DEFAULT_TARGET_WEIGHT
    long_only: bool = True

    def __post_init__(self) -> None:
        if self.fast < 1 or self.slow < 1:
            raise ValueError("moving-average windows must be >= 1")
        if self.fast >= self.slow:
            raise ValueError("fast window must be shorter than slow window")

    @property
    def name(self) -> str:
        return REFERENCE_STRATEGY_NAME

    @property
    def warmup(self) -> int:
        return self.slow

    def target_weights(self, window: PanelWindow) -> Mapping[str, float]:
        closes = window.closes(self.ticker)
        if len(closes) < self.slow:
            return {self.ticker: 0.0}
        fast_ma = sum(closes[-self.fast :]) / self.fast
        slow_ma = sum(closes[-self.slow :]) / self.slow
        if fast_ma > slow_ma:
            return {self.ticker: self.target_weight}
        return {self.ticker: 0.0 if self.long_only else -self.target_weight}

    @classmethod
    def from_spec(cls, ticker: str, params: Mapping[str, Any]) -> ReferenceTrendStrategy:
        """Build the reference rule from a strategy record's ``params`` block."""

        return cls(
            ticker=ticker,
            fast=int(params.get("fast", DEFAULT_FAST)),
            slow=int(params.get("slow", DEFAULT_SLOW)),
            target_weight=float(params.get("target_weight", DEFAULT_TARGET_WEIGHT)),
            long_only=bool(params.get("long_only", True)),
        )


__all__ = [
    "DEFAULT_FAST",
    "DEFAULT_SLOW",
    "DEFAULT_TARGET_WEIGHT",
    "PARAM_BOUNDS",
    "REFERENCE_STRATEGY_NAME",
    "ReferenceTrendStrategy",
]
