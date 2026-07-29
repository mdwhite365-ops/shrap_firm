"""Reachability of `shrap-strategy-seed` subcommands.

Separate from `test_strategy_seed.py`, which tests the loaders themselves. This
file asks a different question: can the loaders actually be *invoked*? The
momentum seed proved that is not the same question — its loader, seed data,
dispatch branch, docs and tests all existed while the command was unreachable.
"""

from __future__ import annotations

# --- every action the dispatch handles must be reachable ----------------------


def _parser_actions() -> set[str]:
    from shrap.research.strategy_seed.cli import _build_parser

    parser = _build_parser()
    sub = parser._subparsers._group_actions[0]  # type: ignore[union-attr]
    return set(sub.choices)  # type: ignore[attr-defined]


def _dispatch_actions() -> set[str]:
    """Every `args.action == "..."` literal compared inside `_run`."""

    import inspect
    import re

    from shrap.research.strategy_seed.cli import _run

    source = inspect.getsource(_run)
    return set(re.findall(r'args\.action == "([^"]+)"', source))


def test_every_dispatched_action_is_reachable_from_the_parser() -> None:
    """THE test for the bug this file's fix addresses.

    `load-momentum` and `list-momentum` were handled in `_run` but never added
    to the parser, so argparse rejected them before dispatch was reached. The
    loader, the seed data, the docs and the dispatch branch all existed; the
    command was simply unreachable, and the error read as an invalid choice —
    which looks like the caller's typo, not a missing wire.
    """

    unreachable = _dispatch_actions() - _parser_actions()
    assert not unreachable, f"handled in _run but not in the parser: {sorted(unreachable)}"


def test_the_momentum_seed_is_loadable() -> None:
    """The seed the whole cross-sectional evaluation depends on."""

    from shrap.research.strategy_seed.technical_strategies import MOMENTUM_SEEDS_BY_KEY

    assert "load-momentum" in _parser_actions()
    assert "xs-momentum-126-21-10" in MOMENTUM_SEEDS_BY_KEY
