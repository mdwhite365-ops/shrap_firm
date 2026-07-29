"""Notional sizing: the arithmetic, and the four refusals that make it safe.

The load-bearing tests are the refusals. Every one of them guards a path where
the quiet alternative — falling back to a fixed quantity, rounding up, returning
0 without saying why — produces orders that look fine and express a strategy
nobody evaluated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shrap.research.strategy_runner.sizing import (
    DEFAULT_MAX_EQUITY_AGE,
    SizingRefused,
    assert_equity_usable,
    size_position,
)

_NOW = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)


# --- the arithmetic ----------------------------------------------------------


def test_a_weight_becomes_a_dollar_slot_then_shares() -> None:
    """10% of $10,000 is $1,000; at $50 a share that is 20 shares."""

    r = size_position(target_weight=0.10, equity=10_000.0, price=50.0)
    assert r.quantity == 20
    assert r.notional_slot == pytest.approx(1_000.0)
    assert r.is_tradeable


def test_shares_are_floored_never_rounded_up() -> None:
    """Overshooting a slot can breach gross exposure on a small account.

    $1,000 at $300/share is 3.33 shares. Rounding to 4 spends $1,200 — 12% of a
    $10,000 book for a 10% target, and ten such positions would be 120% gross.
    """

    r = size_position(target_weight=0.10, equity=10_000.0, price=300.0)
    assert r.quantity == 3
    assert r.quantity * r.price <= r.notional_slot


def test_a_slot_smaller_than_one_share_reports_why() -> None:
    """The $10k case that actually bites.

    A 10% slot is $1,000, so any name above $1,000/share cannot be held at all.
    Returning 0 silently would let a strategy quietly stop expressing part of its
    universe with nothing to indicate it had.
    """

    r = size_position(target_weight=0.10, equity=10_000.0, price=1_500.0)
    assert r.quantity == 0
    assert not r.is_tradeable
    assert "smaller than one share" in r.reason
    assert "1,500" in r.reason


def test_a_zero_weight_is_an_exit_not_a_failure() -> None:
    r = size_position(target_weight=0.0, equity=10_000.0, price=50.0)
    assert r.quantity == 0
    assert "exit" in r.reason


def test_the_per_order_cap_clamps_and_says_it_under_filled() -> None:
    """A clamp the caller cannot see is a strategy silently missing its target."""

    r = size_position(target_weight=0.10, equity=10_000.0, price=5.0, max_quantity=40)
    assert r.quantity == 40  # would have been 200
    assert "clamped 200 -> 40" in r.reason
    assert "under-fill" in r.reason


def test_an_uncapped_size_reports_no_reason() -> None:
    assert size_position(target_weight=0.10, equity=10_000.0, price=50.0).reason == ""


def test_sizing_scales_with_the_account_not_with_the_price() -> None:
    """The property the old fixed-quantity path did not have.

    One share of a $750 name and one share of a $50 name are wildly different
    fractions of a book. Equal weights must produce equal *dollars*.
    """

    cheap = size_position(target_weight=0.10, equity=10_000.0, price=50.0)
    dear = size_position(target_weight=0.10, equity=10_000.0, price=500.0)
    assert cheap.quantity * cheap.price == pytest.approx(dear.quantity * dear.price, rel=0.05)


def test_a_bad_price_refuses_rather_than_dividing_by_zero() -> None:
    with pytest.raises(SizingRefused, match="not usable for sizing"):
        size_position(target_weight=0.10, equity=10_000.0, price=0.0)


# --- the equity gate ---------------------------------------------------------


def test_fresh_equity_is_returned() -> None:
    assert assert_equity_usable(10_000.0, _NOW - timedelta(minutes=5), _NOW) == 10_000.0


def test_a_missing_snapshot_refuses_rather_than_defaulting() -> None:
    """THE test. A fallback here would resurrect the bug this module removes.

    Falling back to a fixed quantity would do it invisibly, at exactly the moment
    the account state is least trustworthy.
    """

    with pytest.raises(SizingRefused, match="no account snapshot"):
        assert_equity_usable(None, _NOW, _NOW)
    with pytest.raises(SizingRefused, match="no account snapshot"):
        assert_equity_usable(10_000.0, None, _NOW)


def test_stale_equity_refuses() -> None:
    """The Reconciliation Agent writes every ~300s; an hour old means it stopped."""

    stale = _NOW - DEFAULT_MAX_EQUITY_AGE - timedelta(minutes=1)
    with pytest.raises(SizingRefused, match="stale"):
        assert_equity_usable(10_000.0, stale, _NOW)


def test_equity_just_inside_the_window_is_accepted() -> None:
    edge = _NOW - DEFAULT_MAX_EQUITY_AGE + timedelta(seconds=1)
    assert assert_equity_usable(10_000.0, edge, _NOW) == 10_000.0


def test_non_positive_equity_refuses() -> None:
    """A blown-up or uninitialised account cannot fund a position."""

    for bad in (0.0, -500.0):
        with pytest.raises(SizingRefused, match="cannot fund"):
            assert_equity_usable(bad, _NOW, _NOW)


def test_the_staleness_window_tolerates_several_missed_passes() -> None:
    """Too tight a window would refuse to trade on one slow reconciliation."""

    assert DEFAULT_MAX_EQUITY_AGE >= timedelta(minutes=15)
