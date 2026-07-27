"""Strategy seed: the Mike-seed path that creates hypothesis-stage strategies.

The Strategy Evaluator (PR #78) evaluates a ``hypothesis`` strategy on demand,
but nothing creates one — the Hypothesis Generator is deferred and the registry
has no CLI. This package is that path: a code-constant first strategy
(:mod:`shrap.research.strategy_seed.first_strategy`) and the
``shrap-strategy-seed`` CLI (:mod:`shrap.research.strategy_seed.cli`) that loads
it idempotently through the registry, so the funnel -> Evaluator path can run
end to end.
"""

from __future__ import annotations
