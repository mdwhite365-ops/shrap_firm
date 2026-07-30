"""Hypothesis Generator — the firm proposing its own strategies.

Spec: ``docs/agents/research/hypothesis-generator.md``. This package implements
the ``technical-catalyst`` archetype only (ADR-0013); the two anchored
archetypes wait on Infrastructure Mapper and Bottleneck Scout.

The archetype carries no world-changer anchor by design, so **the literature is
its anchor**: a proposal must cite a published effect, name how the
implementation deviates from the source, and refuse if it cannot say who claimed
what. Without that, "ask a language model for a trading strategy" is all this
would be, and that is the failure the spec exists to prevent.
"""

from shrap.research.hypothesis_generator.generator import (
    GenerationReport,
    HypothesisGenerator,
    ItemOutcome,
)
from shrap.research.hypothesis_generator.literature import LiteratureItem

__all__ = [
    "GenerationReport",
    "HypothesisGenerator",
    "ItemOutcome",
    "LiteratureItem",
]
