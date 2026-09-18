"""The intraday momentum seed: same effect, same horizons, different cadence.

This seed exists to answer one question the firm has never measured — does
per-decision skill survive a shorter horizon? ``IR = IC x sqrt(breadth)`` says
26 decisions a session is worth ~5.1x the information ratio of one, *if* IC
holds. The daily sibling scores IR 0.415 on the same universe.

The tests here are almost entirely about the comparison being **valid**, because
an invalid one produces a number that looks like an answer. If the two seeds
differ in anything but cadence, the result measures that difference instead.
"""

from __future__ import annotations

import pytest

from shrap.research.strategy_seed.technical_strategies import (
    MOMENTUM_SEEDS_BY_KEY,
    _momentum_spec,
    compute_momentum_spec_hash,
    momentum_record,
)

DAILY_KEY = "xs-momentum-126-21-10"
INTRADAY_KEY = "xs-momentum-126-21-10-intraday15"

BARS_PER_SESSION_15MIN = 26  # 390 // 15


def _daily():  # type: ignore[no-untyped-def]
    return MOMENTUM_SEEDS_BY_KEY[DAILY_KEY]


def _intraday():  # type: ignore[no-untyped-def]
    return MOMENTUM_SEEDS_BY_KEY[INTRADAY_KEY]


def test_the_two_seeds_differ_only_in_cadence() -> None:
    """The whole measurement rests on this.

    A difference in lookback, skip, top_n, universe or direction would mean the
    run compares two strategies rather than two grains, and the resulting IR
    would answer a question nobody asked.
    """

    daily, intraday = _daily(), _intraday()
    assert intraday.lookback == daily.lookback == 126
    assert intraday.skip == daily.skip == 21
    assert intraday.top_n == daily.top_n == 10
    assert intraday.tickers == daily.tickers
    assert intraday.market_filter == daily.market_filter
    assert intraday.long_short == daily.long_short
    # ...and the one thing that does differ.
    assert daily.cadence_minutes is None
    assert intraday.cadence_minutes == 15


def test_horizons_are_declared_in_sessions_and_stored_in_bars() -> None:
    """126 sessions is 3276 fifteen-minute bars, and the spec must say so.

    The engine counts warmup in bars. A spec carrying 126 at a 15-minute grain
    would form its ranking over 126 bars — under five sessions — which is a
    short-horizon effect the literature says REVERSES. It would run, produce
    numbers, and silently measure the opposite of what the thesis claims.
    """

    params = _momentum_spec(_intraday())["params"]
    assert params["lookback"] == 126 * BARS_PER_SESSION_15MIN == 3276
    assert params["skip"] == 21 * BARS_PER_SESSION_15MIN == 546


def test_counts_and_fractions_are_not_scaled() -> None:
    """top_n is a number of names and gross_exposure a fraction of the book.

    Scaling either with the grain would be a unit error the bounds check cannot
    catch — 260 names out of a 50-name universe is not a bounds violation, it is
    a nonsense the ranking would silently clamp.
    """

    params = _momentum_spec(_intraday())["params"]
    assert params["top_n"] == 10
    assert params["gross_exposure"] == 1.0


def test_bounds_scale_with_the_params_they_bound() -> None:
    """Otherwise a six-month formation is rejected for exceeding a daily ceiling."""

    spec = _momentum_spec(_intraday())
    lo, hi = spec["param_bounds"]["lookback"]
    assert lo <= spec["params"]["lookback"] <= hi
    lo_s, hi_s = spec["param_bounds"]["skip"]
    assert lo_s <= spec["params"]["skip"] <= hi_s
    # Unscaled bounds stay put.
    assert spec["param_bounds"]["top_n"] == [1.0, 50.0]


def test_the_intraday_spec_declares_its_cadence() -> None:
    """Without this the Runner reads it as daily and #234 picks the daily reader."""

    cadence = _momentum_spec(_intraday())["cadence"]
    assert cadence == {"kind": "intraday", "interval_minutes": 15}


def test_the_daily_spec_carries_no_cadence_key() -> None:
    """Absence is what keeps every existing seed's spec_hash stable.

    Writing `cadence: {kind: daily}` would be truthful and would still rewrite
    the hash of every momentum strategy in the registry, registering the live
    book as new rows describing runs that never happened.
    """

    assert "cadence" not in _momentum_spec(_daily())


def test_the_daily_seed_hash_is_unchanged_by_this_card() -> None:
    """Pinned to the value computed before `cadence_minutes` existed."""

    assert compute_momentum_spec_hash(_daily()) == (
        "sha256:541e12a1e34fd78cd9196a33b6edfee37900754b98a6014dde41b1cf22933c3a"
    )


def test_the_two_seeds_hash_differently() -> None:
    """They are different strategies in the registry, not one strategy twice."""

    assert compute_momentum_spec_hash(_daily()) != compute_momentum_spec_hash(_intraday())


def test_the_seed_is_a_hypothesis_not_a_promotion() -> None:
    """It enters the funnel at the bottom like everything else.

    KI-036: the promote gate has never been failed on evidence, and a seed that
    arrived already promoted would be a strategy that skipped the only step
    where evidence is considered.
    """

    record = momentum_record(_intraday())
    assert record.status == "hypothesis"
    assert record.account_id is None


def test_the_strategy_id_is_a_real_ulid() -> None:
    from ulid import ULID

    assert str(ULID.from_str(_intraday().strategy_id)) == _intraday().strategy_id


@pytest.mark.parametrize("key", [DAILY_KEY, INTRADAY_KEY])
def test_both_seeds_share_one_thesis_family(key: str) -> None:
    """Same universe, same archetype, same kill criteria — only cadence differs."""

    daily_rec = momentum_record(_daily())
    rec = momentum_record(MOMENTUM_SEEDS_BY_KEY[key])
    assert rec.archetype == daily_rec.archetype
    assert rec.tickers == daily_rec.tickers
    assert rec.kill_criteria == daily_rec.kill_criteria


def test_the_runaway_cap_admits_an_intraday_book() -> None:
    """26 slots x 10 names = 260 orders/day ceiling. 80 would have bound silently.

    Raised to 300 by Mike's ruling, 2026-09-18. Asserted here rather than in the
    rate-limit tests because this is the card whose strategy needs the headroom.
    """

    from shrap.risk_compliance.rate_limit import RateLimitConfig

    ceiling = BARS_PER_SESSION_15MIN * _intraday().top_n
    assert ceiling == 260
    assert RateLimitConfig().max_orders_per_day > ceiling
