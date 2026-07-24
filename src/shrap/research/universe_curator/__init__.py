"""Universe Curator — owner of Tier 2 (Watch) and Tier 3 (Active) state (ADR-0012).

First implementation card: the ``research.universe_tiers`` and
``research.universe_staging`` stores, the five tier-transition events, the
``shrap-universe-promote`` approval CLI, and the launch-list load. Every Tier 3
mutation happens only through an explicit Mike decision — there is no
auto-promotion path anywhere in this package. The daily watch-expiry sweep is
the one scheduled behavior; it can only shrink attention, never make a name
tradeable.
"""
