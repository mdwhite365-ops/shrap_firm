"""Edge-triggering for reconciliation discrepancies.

``operations.reconciliation-discrepancy`` reached **11,096 events** by
2026-07-31 against 9,161 clean passes — more alarms than all-clears, and not one
of them ever read. The cause was not book drift. Mike cancelled the account's
original test orders by hand in the Alpaca UI; those orders were never Shrap's,
so the store correctly has no row for them, and ``compare_orders`` correctly
noticed. Then the agent re-announced that same understood divergence **every
300 seconds, from three agents, for weeks**.

So the comparison was right and the reporting was wrong. The fix is a
distinction the two streams were already halfway to making:

- ``operations.reconciliation-completed`` is **level-triggered**. It carries
  ``discrepancies`` and ``clean`` on every pass, so current state is always
  observable there and a divergence that clears needs no event of its own.
- ``operations.reconciliation-discrepancy`` becomes **edge-triggered**. A new
  divergence is news; the same divergence on the next pass is not.

That collapses the stream from *time times divergences* to just *divergences*,
and restores the property an alarm needs: an event in it means something changed.

**Process-local and reset on restart**, deliberately, mirroring
:mod:`shrap.agents.operations.health_monitor.state`. A fresh process re-reports
each open divergence exactly once, which is the correct baseline behaviour for
something whose whole job is to state what it currently sees — and it means a
restart cannot silently swallow a discrepancy that appeared while it was down.

**The key includes both statuses, not just the order id.** An order that is
missing-in-store while the broker moves it ``new`` → ``filled`` is genuinely new
information about a known problem, so it re-reports. Only an unchanged
divergence is suppressed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from shrap.agents.operations.reconciliation_agent.records import Discrepancy

DiscrepancyKey = tuple[str, str, str | None, str | None]


def discrepancy_key(discrepancy: Discrepancy) -> DiscrepancyKey:
    """Identity of a divergence for suppression purposes.

    Excludes ``symbol``: it is derived from the order and cannot change
    independently, so including it could only ever produce a spurious re-report.
    """

    return (
        str(discrepancy.kind),
        discrepancy.broker_order_id,
        discrepancy.stored_status,
        discrepancy.broker_status,
    )


@dataclass(frozen=True, slots=True)
class PassDelta:
    """What one pass changed, relative to the pass before it."""

    appeared: tuple[Discrepancy, ...]
    suppressed: int
    resolved: int

    @property
    def open_count(self) -> int:
        return len(self.appeared) + self.suppressed


@dataclass
class DiscrepancyTracker:
    """Remembers which divergences have already been announced."""

    _open: set[DiscrepancyKey] = field(default_factory=set)

    def observe(self, discrepancies: Iterable[Discrepancy]) -> PassDelta:
        """Fold one pass in and report what changed, in a single call.

        Deliberately one method rather than a query plus an update: the counts
        are all relative to the *previous* baseline, and a two-call API where
        the update silently invalidates the query is a trap waiting for whoever
        edits this next.

        Whatever is passed in becomes the new baseline, so a divergence that
        clears is forgotten and would be announced again if it returned. That is
        intended — a divergence that comes back after clearing is news.
        """

        current: list[Discrepancy] = list(discrepancies)
        current_keys = {discrepancy_key(d) for d in current}

        appeared = tuple(d for d in current if discrepancy_key(d) not in self._open)
        suppressed = len(current) - len(appeared)
        resolved = len(self._open - current_keys)

        self._open = current_keys
        return PassDelta(appeared=appeared, suppressed=suppressed, resolved=resolved)


__all__ = ["DiscrepancyKey", "DiscrepancyTracker", "PassDelta", "discrepancy_key"]
