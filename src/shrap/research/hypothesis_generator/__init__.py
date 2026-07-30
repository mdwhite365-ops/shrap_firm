"""Hypothesis Generator — the firm proposing its own strategies.

Spec: ``docs/agents/research/hypothesis-generator.md``. This package implements
the ``technical-catalyst`` archetype only (ADR-0013); the two anchored
archetypes wait on Infrastructure Mapper and Bottleneck Scout.

The archetype carries no world-changer anchor by design, so **the literature is
its anchor**: a proposal must cite a published effect, name how the
implementation deviates from the source, and refuse if it cannot say who claimed
what. Without that, "ask a language model for a trading strategy" is all this
would be, and that is the failure the spec exists to prevent.

**This module deliberately re-exports nothing**, and that is a deployment
constraint rather than a style preference. It used to hoist
:class:`HypothesisGenerator` and friends for convenience, which meant that
importing *any* submodule executed those imports first — so Tech Watcher's
``from ...literature import PostgresLiteratureStore``, a module whose own
imports are stdlib only, pulled in the entire strategy evaluator and numpy.
``tech-watcher.Dockerfile`` does not install numpy, so the service crash-looped
on ``ModuleNotFoundError`` the moment the q-fin leg shipped (2026-07-30).

The lesson generalises past this package: a convenience re-export in an
``__init__`` is an invisible dependency edge from every consumer of every
submodule to every module the package touches. Import the submodule you want.
``tests/research/test_import_weight.py`` pins it.
"""
