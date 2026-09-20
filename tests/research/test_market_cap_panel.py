"""Market cap as a panel series: point-in-time, and derived from the panel's own closes.

Closes the ingest-to-strategy half of KI-035's cheapest `missing-data` gap.
`volatility-rank-forecast` asks only for market capitalisation, and until now
`PanelWindow` exposed closes and volumes and nothing else.

Two properties carry the whole card:

1. **Selection is on `filed_at`, never `as_of`.** A count describing 2024-03-31
   may not be filed until 2024-05-09. Using it on 2024-04-15 is look-ahead the
   backtest rewards and the live book cannot reproduce.
2. **The price is the panel's own close.** `SELECT_MARKET_CAP_SQL` would join
   shares onto `daily_bars` in a second read with its own `adjustment` and
   `source` — and since #247 the evaluator can run on either feed. Two reads
   that disagree produce a market cap that is not `close x shares` for the close
   the strategy sees.
"""

from __future__ import annotations

import math
from datetime import date

from shrap.research.hypothesis_generator.expressible import (
    AVAILABLE_SERIES,
    normalise_input,
)
from shrap.research.strategy_evaluator.strategy import BarSample, PanelWindow, PricePanel


def _bars(*rows: tuple[str, float]) -> list[BarSample]:
    return [
        BarSample(
            session_date=date.fromisoformat(day),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1_000.0,
        )
        for day, close in rows
    ]


# --- the point-in-time rule ---------------------------------------------------


def test_a_count_is_invisible_until_the_day_it_was_filed() -> None:
    """The one predicate that separates an honest backtest from a look-ahead one."""

    panel = PricePanel.from_bars(
        {"AAA": _bars(("2024-05-08", 10.0), ("2024-05-09", 10.0), ("2024-05-10", 10.0))},
        {"AAA": [(date(2024, 5, 9), 100.0)]},
    )

    caps = panel.market_caps["AAA"]

    assert math.isnan(caps[0])  # filed tomorrow — not knowable
    assert caps[1] == 1_000.0  # filed today — public this morning
    assert caps[2] == 1_000.0


def test_the_newest_count_filed_so_far_wins() -> None:
    panel = PricePanel.from_bars(
        {"AAA": _bars(("2024-01-02", 10.0), ("2024-05-10", 10.0), ("2024-08-10", 10.0))},
        {"AAA": [(date(2024, 1, 1), 100.0), (date(2024, 5, 9), 200.0), (date(2024, 8, 1), 50.0)]},
    )

    assert panel.market_caps["AAA"] == (1_000.0, 2_000.0, 500.0)


def test_an_amendment_filed_later_supersedes_without_rewriting_history() -> None:
    """A restated count applies from its own filing date forward, not backward."""

    panel = PricePanel.from_bars(
        {"AAA": _bars(("2024-05-10", 10.0), ("2024-06-10", 10.0))},
        # Same period restated: original filed 05-09, amendment filed 06-01.
        {"AAA": [(date(2024, 5, 9), 100.0), (date(2024, 6, 1), 150.0)]},
    )

    assert panel.market_caps["AAA"] == (1_000.0, 1_500.0)


def test_shares_out_of_order_are_still_applied_in_filing_order() -> None:
    panel = PricePanel.from_bars(
        {"AAA": _bars(("2024-05-10", 10.0), ("2024-08-10", 10.0))},
        {"AAA": [(date(2024, 8, 1), 50.0), (date(2024, 5, 9), 100.0)]},
    )

    assert panel.market_caps["AAA"] == (1_000.0, 500.0)


# --- what "unknown" must look like --------------------------------------------


def test_a_name_with_no_share_history_is_nan_not_zero() -> None:
    """META, MSTR, NET and DKNG (#253). Zero is a number a ranking would sort.

    "Smallest company in the universe" is the wrong answer to "we do not know",
    and it is the answer a size screen would act on.
    """

    panel = PricePanel.from_bars({"META": _bars(("2024-05-10", 10.0))}, {})

    assert all(math.isnan(v) for v in panel.market_caps["META"])


def test_a_date_the_name_did_not_trade_is_nan() -> None:
    """No close means no market cap, even with a share count on file."""

    panel = PricePanel.from_bars(
        {
            "AAA": _bars(("2024-05-10", 10.0), ("2024-05-13", 10.0)),
            "BBB": _bars(("2024-05-13", 20.0)),
        },
        {"BBB": [(date(2024, 1, 1), 100.0)]},
    )

    caps = panel.market_caps["BBB"]
    assert math.isnan(caps[0])  # BBB had no bar on 05-10
    assert caps[1] == 2_000.0


def test_a_panel_built_without_shares_still_works() -> None:
    """Every existing caller passes no shares and must keep working."""

    panel = PricePanel.from_bars({"AAA": _bars(("2024-05-10", 10.0))})

    assert panel.market_caps["AAA"] == (math.nan,) or math.isnan(panel.market_caps["AAA"][0])


# --- it is the panel's own close ----------------------------------------------


def test_market_cap_is_close_times_shares_for_the_close_the_strategy_sees() -> None:
    """The property that makes a second SQL read unnecessary and unsafe.

    Whatever feed and adjustment produced the panel, market cap is derived from
    those exact closes — so it cannot silently describe a different price.
    """

    panel = PricePanel.from_bars(
        {"AAA": _bars(("2024-05-10", 12.5), ("2024-05-13", 7.25))},
        {"AAA": [(date(2024, 1, 1), 400.0)]},
    )
    window = PanelWindow(panel, 1)

    closes = window.closes("AAA")
    caps = window.market_caps("AAA")
    assert [c * 400.0 for c in closes] == list(caps)


def test_the_window_cannot_see_a_future_market_cap() -> None:
    panel = PricePanel.from_bars(
        {"AAA": _bars(("2024-05-10", 10.0), ("2024-05-13", 20.0), ("2024-05-14", 30.0))},
        {"AAA": [(date(2024, 1, 1), 100.0)]},
    )

    assert PanelWindow(panel, 0).market_caps("AAA") == (1_000.0,)
    assert PanelWindow(panel, 1).market_caps("AAA") == (1_000.0, 2_000.0)


# --- the proposer can now name it ---------------------------------------------


def test_market_cap_is_an_available_series() -> None:
    assert "market cap" in AVAILABLE_SERIES


def test_the_wordings_a_paper_actually_uses_resolve() -> None:
    for wording in (
        "market cap",
        "market capitalization",
        "market capitalisation",
        "market value of equity",
        "size",
        "firm size",
    ):
        assert normalise_input(wording) == "market cap", wording


def test_near_neighbours_that_mean_something_else_stay_out_of_reach() -> None:
    """The module's standing rule: a wording difference is fine, a construction is not.

    `shares outstanding` is a COMPONENT of market cap, not a synonym — a strategy
    ranking on the raw count is not the same strategy as one ranking on size.
    Float excludes insider holdings, enterprise value adds debt, book value is an
    accounting figure. None of them is price times a share count.
    """

    for wording in (
        "shares outstanding",
        "free float market cap",
        "float",
        "enterprise value",
        "book value",
        "book-to-market",
    ):
        assert normalise_input(wording) is None, wording
