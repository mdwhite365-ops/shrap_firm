"""Stream retention (KI-025).

Redis is the transport; ``ops.audit_events`` is the record. These pin the two
properties that make trimming safe to run unattended: it bounds growth, and it
can never be the reason the audit trail stops moving.
"""

from __future__ import annotations

from typing import Any

from shrap.agents.operations.audit_logger.config import Settings
from shrap.agents.operations.audit_logger.retention import (
    DEFAULT_MAX_LEN,
    STREAM_MAX_LEN,
    cap_for,
    trim_streams,
)


class FakeTrimmer:
    def __init__(self, removed: dict[str, int] | None = None) -> None:
        self.calls: list[tuple[str, int, bool]] = []
        self._removed = removed or {}

    async def xtrim(self, name: str, maxlen: int, approximate: bool = True) -> Any:
        self.calls.append((name, maxlen, approximate))
        return self._removed.get(name, 0)


class ExplodingTrimmer(FakeTrimmer):
    def __init__(self, fails: set[str]) -> None:
        super().__init__()
        self._fails = fails

    async def xtrim(self, name: str, maxlen: int, approximate: bool = True) -> Any:
        if name in self._fails:
            raise ConnectionError(f"redis said no to {name}")
        return await super().xtrim(name, maxlen, approximate)


async def test_every_stream_is_trimmed_to_its_cap_approximately() -> None:
    trimmer = FakeTrimmer()

    await trim_streams(trimmer, ["ops.health-tick", "research.strategy.verdict"])

    assert trimmer.calls == [
        ("ops.health-tick", DEFAULT_MAX_LEN, True),
        ("research.strategy.verdict", DEFAULT_MAX_LEN, True),
    ]


async def test_the_removed_count_is_summed_across_streams() -> None:
    trimmer = FakeTrimmer({"a": 12, "b": 30})

    assert await trim_streams(trimmer, ["a", "b"]) == 42


async def test_one_failing_stream_does_not_abort_the_sweep() -> None:
    """Retention is maintenance and must never stall the audit trail."""

    trimmer = ExplodingTrimmer(fails={"broken"})

    removed = await trim_streams(trimmer, ["broken", "fine"])

    assert removed == 0
    assert [name for name, _, _ in trimmer.calls] == ["fine"]


async def test_trimming_nothing_is_not_an_error() -> None:
    assert await trim_streams(FakeTrimmer(), []) == 0


def test_the_default_cap_clears_a_week_of_the_noisiest_producer() -> None:
    """Health Monitor ticks every 30s; three reconcilers publish every 300s.

    If this ever drops below a week the cap stops being a growth bound and
    starts being a retention decision, which is a different conversation.
    """

    health_tick_per_day = 24 * 60 * 2  # 30s cadence
    reconciliation_completed_per_day = 3 * (24 * 60 * 60 / 300)

    assert DEFAULT_MAX_LEN / health_tick_per_day >= 7
    assert DEFAULT_MAX_LEN / reconciliation_completed_per_day >= 7


def test_overrides_are_empty_and_the_default_applies() -> None:
    """One number nothing has needed to escape is a policy; a table is a burden."""

    assert STREAM_MAX_LEN == {}
    assert cap_for("anything.at.all") == DEFAULT_MAX_LEN


def test_retention_can_be_disabled() -> None:
    """The escape hatch: a stream being investigated must be preservable."""

    assert Settings(trim_interval_seconds=0).trim_interval_seconds == 0
    assert Settings().trim_interval_seconds > 0
