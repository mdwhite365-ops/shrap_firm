"""Tests for per-strategy cadence and the slot-dimensioned idempotency guard.

The property under test is mostly a *negative* one. Making the Runner wake more
often is easy; the failure this must prevent is every strategy already in the
registry starting to trade on every wake. So the first block below is about
what a missing, malformed or unknown cadence does, and the answer is always
"behaves exactly as it did before this module existed".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from shrap.research.strategy_runner.cadence import (
    CADENCE_INTRADAY,
    DAILY,
    DEFAULT_INTERVAL_MINUTES,
    MAX_INTERVAL_MINUTES,
    SESSION_SLOT,
    Cadence,
    read_cadence,
    slot_for,
)

NOON = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


# --- absence and malformation both mean daily -----------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        None,
        {},
        {"cadence": None},
        {"cadence": "daily"},
        {"cadence": "intrday"},  # typo: silently daily, which is the safe direction
        {"cadence": 5},
        {"cadence": []},
        {"cadence": {"kind": "intraday", "interval_minutes": 0}},
        {"cadence": {"kind": "intraday", "interval_minutes": -5}},
        {"cadence": {"kind": "intraday", "interval_minutes": MAX_INTERVAL_MINUTES + 1}},
        {"cadence": {"kind": "intraday", "interval_minutes": "five"}},
        {"cadence": {"kind": "intraday", "interval_minutes": True}},
        {"cadence": {"kind": "daily"}},
        {"cadence": {}},
    ],
)
def test_anything_unrecognisable_resolves_to_daily(spec: dict[str, Any] | None) -> None:
    # Both failure directions land here. A spec that cannot be understood must
    # not stop a strategy trading, and must not make one trade MORE than today.
    assert read_cadence(spec) == DAILY


def test_a_daily_strategy_occupies_one_slot_for_the_whole_session() -> None:
    cadence = read_cadence({})

    # Every instant of the session maps to the same slot, so the first stamp of
    # the day closes the strategy out however often the Runner wakes.
    slots = {
        slot_for(cadence, datetime(2026, 8, 3, hour, minute, tzinfo=UTC))
        for hour in range(24)
        for minute in (0, 17, 59)
    }
    assert slots == {SESSION_SLOT}


# --- intraday -------------------------------------------------------------------


def test_a_string_cadence_gets_the_default_interval() -> None:
    assert read_cadence({"cadence": "intraday"}) == Cadence(
        kind=CADENCE_INTRADAY, interval_minutes=DEFAULT_INTERVAL_MINUTES
    )


def test_an_explicit_interval_is_honoured() -> None:
    assert read_cadence({"cadence": {"kind": "intraday", "interval_minutes": 15}}) == Cadence(
        kind=CADENCE_INTRADAY, interval_minutes=15
    )


def test_intraday_slots_advance_once_per_interval_and_not_within_one() -> None:
    cadence = Cadence(kind=CADENCE_INTRADAY, interval_minutes=5)

    within = [
        slot_for(cadence, datetime(2026, 8, 3, 14, minute, tzinfo=UTC))
        for minute in (30, 31, 32, 33, 34)
    ]
    assert within == ["14:30"] * 5
    assert slot_for(cadence, datetime(2026, 8, 3, 14, 35, tzinfo=UTC)) == "14:35"


def test_a_slot_is_a_pure_function_of_the_clock() -> None:
    # Floored rather than counted, so a Runner restarting mid-session resolves
    # the same slot for the same minute. A counter would reset on restart and
    # let every intraday strategy act a second time inside one interval.
    cadence = Cadence(kind=CADENCE_INTRADAY, interval_minutes=15)
    moment = datetime(2026, 8, 3, 14, 22, tzinfo=UTC)

    assert slot_for(cadence, moment) == slot_for(cadence, moment) == "14:15"


def test_slots_are_ordered_and_unique_across_a_session() -> None:
    cadence = Cadence(kind=CADENCE_INTRADAY, interval_minutes=30)
    slots = [
        slot_for(
            cadence,
            datetime(2026, 8, 3, 13, 30, tzinfo=UTC).replace(minute=m % 60, hour=13 + m // 60),
        )
        for m in range(0, 240, 30)
    ]

    assert len(set(slots)) == len(slots)
    assert slots == sorted(slots)
