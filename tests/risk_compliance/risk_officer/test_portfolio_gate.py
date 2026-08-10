"""The portfolio caps on the order path.

Two behaviours carry most of the weight here and both are easy to get wrong in
the direction that looks harmless:

**Scale, don't reject** (spec step 8). An intent that breaches a cap is reduced
to what fits. Rejecting instead would look safer and would quietly stop a
strategy from ever reaching its target weights.

**A breached limit blocks increases, not decreases.** When a regime tightens
caps, every existing position is instantly over-limit through no action of the
strategy's. A gate that vetoed on "projected exceeds cap" would then refuse the
sells that fix it, trapping the book over-limit exactly when the market turned.
"""

from __future__ import annotations

from shrap.risk_compliance.risk_officer.exposure import BookExposure, Position
from shrap.risk_compliance.risk_officer.gate import (
    REASON_EXCEEDS_CLUSTER_CAP,
    REASON_EXCEEDS_GROSS_EXPOSURE,
    REASON_EXCEEDS_TICKER_CAP,
    REASON_NO_PRICE,
    REASON_SCALED_DOWN,
    check_portfolio,
)
from shrap.risk_compliance.risk_officer.limits import PortfolioLimits

NAV = 10_000.0
LIMITS = PortfolioLimits()


def _book(**holdings: float) -> BookExposure:
    return BookExposure(
        nav=NAV,
        positions=tuple(
            Position(ticker=t, quantity=0.0, market_value=v) for t, v in holdings.items()
        ),
    )


def _uncorrelated(*tickers: str) -> dict[str, list[float]]:
    """Price paths with enough history and no shared movement.

    Deterministic and deliberately out of phase, so the clusterer separates them
    and the cluster cap does not shadow the limit under test.
    """

    history: dict[str, list[float]] = {}
    for index, ticker in enumerate(tickers):
        series = [100.0]
        for step in range(120):
            # Each name moves on its own cycle length.
            direction = 1 if (step // (index + 2)) % 2 == 0 else -1
            series.append(series[-1] * (1 + direction * 0.01))
        history[ticker] = series
    return history


# --- per-ticker cap -----------------------------------------------------------


def test_an_order_inside_every_cap_is_approved_whole() -> None:
    decision = check_portfolio(
        book=_book(),
        ticker="AAPL",
        side="buy",
        quantity=10,
        price=100.0,  # $1,000 == 10% of NAV
        limits=LIMITS,
        price_history=_uncorrelated("AAPL"),
    )

    assert decision.approved
    assert decision.approved_quantity == 10
    assert decision.reason_code == "APPROVED"


def test_an_order_over_the_caps_is_scaled_to_what_fits() -> None:
    """A single unheld name is its own cluster, so the 15% cluster cap binds
    before the 20% ticker cap: $1,500 of a $10,000 book is 15 shares at $100.

    This is the conflict `docs/risk/policy.md` flags as open question 1 — the
    cluster cap sits *below* the ticker cap, so for any concentrated single name
    the ticker cap is never the binding one. Pinned here so that if Mike rules
    the other way, this test is what changes.
    """

    decision = check_portfolio(
        book=_book(),
        ticker="AAPL",
        side="buy",
        quantity=30,
        price=100.0,
        limits=LIMITS,
        price_history=_uncorrelated("AAPL"),
    )

    assert decision.approved
    assert decision.approved_quantity == 15
    assert decision.reason_code == REASON_SCALED_DOWN
    assert decision.binding_limit == REASON_EXCEEDS_CLUSTER_CAP


def test_the_reported_limit_is_the_one_that_decided_the_size() -> None:
    """At the full requested quantity several caps can be breached at once. The
    actionable answer is the one breached by the first share that does not fit,
    not whichever was checked first."""

    decision = check_portfolio(
        book=_book(),
        ticker="AAPL",
        side="buy",
        quantity=100,
        price=100.0,
        limits=LIMITS,
        price_history=_uncorrelated("AAPL"),
    )

    # Ticker (20%), gross (100%) and cluster (15%) are all breached at 100
    # shares; only the cluster cap is breached at 16.
    assert decision.binding_limit == REASON_EXCEEDS_CLUSTER_CAP
    assert decision.approved_quantity == 15


def test_the_cap_measures_the_book_after_the_trade_not_before() -> None:
    """Already at 12%; the cluster cap leaves room for 3% more."""

    decision = check_portfolio(
        book=_book(AAPL=1_200.0),
        ticker="AAPL",
        side="buy",
        quantity=10,
        price=100.0,
        limits=LIMITS,
        price_history=_uncorrelated("AAPL"),
    )

    assert decision.approved_quantity == 3  # 12% + 3% = the 15% cluster cap


def test_a_book_with_no_room_for_one_share_is_vetoed() -> None:
    decision = check_portfolio(
        book=_book(AAPL=2_000.0),
        ticker="AAPL",
        side="buy",
        quantity=5,
        price=500.0,
        limits=LIMITS,
        price_history=_uncorrelated("AAPL"),
    )

    assert not decision.approved
    assert decision.approved_quantity == 0
    assert decision.reason_code == REASON_EXCEEDS_TICKER_CAP


# --- gross exposure -----------------------------------------------------------


def test_gross_exposure_is_capped_at_nav() -> None:
    """No leverage on paper. The book is already 95% invested."""

    decision = check_portfolio(
        book=_book(AAPL=1_900.0, MSFT=1_900.0, NVDA=1_900.0, GOOG=1_900.0, META=1_900.0),
        ticker="AMZN",
        side="buy",
        quantity=10,
        price=100.0,
        limits=LIMITS,
        price_history=_uncorrelated("AAPL", "MSFT", "NVDA", "GOOG", "META", "AMZN"),
    )

    assert decision.approved_quantity == 5  # only $500 of headroom to 100%
    assert decision.binding_limit == REASON_EXCEEDS_GROSS_EXPOSURE


# --- the regime-tightening case ----------------------------------------------


def test_a_sell_is_allowed_when_the_book_is_already_over_a_tightened_cap() -> None:
    """The case the spec does not cover.

    Regime moved to wartime, scaling the ticker cap from 20% to 5%. The existing
    18% position is instantly over-limit. Refusing the sell would trap the firm
    in an over-limit book exactly when the market turned.
    """

    tightened = LIMITS.scaled_for_regime(0.25)
    decision = check_portfolio(
        book=_book(AAPL=1_800.0),
        ticker="AAPL",
        side="sell",
        quantity=10,
        price=100.0,
        limits=tightened,
        price_history=_uncorrelated("AAPL"),
    )

    assert decision.approved
    assert decision.approved_quantity == 10


def test_a_buy_is_still_refused_when_the_book_is_over_a_tightened_cap() -> None:
    """The other half. Reducing a breach is permitted; worsening it is not."""

    tightened = LIMITS.scaled_for_regime(0.25)
    decision = check_portfolio(
        book=_book(AAPL=1_800.0),
        ticker="AAPL",
        side="buy",
        quantity=10,
        price=100.0,
        limits=tightened,
        price_history=_uncorrelated("AAPL"),
    )

    assert not decision.approved
    assert decision.approved_quantity == 0


# --- cluster cap --------------------------------------------------------------


def test_names_that_move_together_share_one_cap() -> None:
    """Three names at 8% each pass every per-ticker check and are one 24% bet."""

    together = [100.0 * (1.01**i) for i in range(120)]
    history = {"A": together, "B": together, "C": together, "D": together}

    decision = check_portfolio(
        book=_book(A=800.0, B=800.0),
        ticker="C",
        side="buy",
        quantity=8,
        price=100.0,
        limits=LIMITS,
        price_history=history,
    )

    # 15% cluster cap on a $10,000 book is $1,500; A+B already hold $1,600.
    assert not decision.approved
    assert decision.reason_code == REASON_EXCEEDS_CLUSTER_CAP
    assert decision.cluster is not None
    assert set(decision.cluster) >= {"A", "B"}


def test_names_that_move_independently_are_capped_separately() -> None:
    decision = check_portfolio(
        book=_book(AAPL=800.0, MSFT=800.0),
        ticker="NVDA",
        side="buy",
        quantity=8,
        price=100.0,
        limits=LIMITS,
        price_history=_uncorrelated("AAPL", "MSFT", "NVDA"),
    )

    assert decision.approved
    assert decision.approved_quantity == 8


# --- refusals -----------------------------------------------------------------


def test_a_missing_price_is_a_veto_not_a_pass() -> None:
    """A risk gate that waves through what it cannot measure is not a risk gate."""

    decision = check_portfolio(
        book=_book(),
        ticker="AAPL",
        side="buy",
        quantity=10,
        price=None,
        limits=LIMITS,
    )

    assert not decision.approved
    assert decision.reason_code == REASON_NO_PRICE


def test_a_zero_price_is_refused_the_same_way() -> None:
    decision = check_portfolio(
        book=_book(),
        ticker="AAPL",
        side="buy",
        quantity=10,
        price=0.0,
        limits=LIMITS,
    )

    assert not decision.approved
    assert decision.reason_code == REASON_NO_PRICE


def test_the_bisection_returns_a_fraction_not_a_whole_share() -> None:
    """The floor's last hiding place.

    The portfolio gate searched over integers, so even after sizing went
    fractional it could only ever hand back a whole share — reinstating the
    floor one layer below where it was removed. A book with room for 2.5 shares
    must approve 2.5, not 2.
    """

    from shrap.risk_compliance.risk_officer.gate import quantize_down

    assert quantize_down(2.5) == 2.5
    # Snapped DOWN: bisection converges from both sides and can settle a hair
    # over the limit it was meant to respect.
    assert quantize_down(15.000000000081855) == 15.0
    # And a book with no room lands on noise, not on a clean zero. Without this
    # the veto reads as an approval for a hundred-billionth of a share.
    assert quantize_down(1.8189894035458565e-11) == 0.0
