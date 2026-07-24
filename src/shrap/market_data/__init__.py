"""Shared historical market-data store — the Strategy Evaluator's backtest input.

The Evaluator spec (``docs/agents/research/strategy-evaluator.md`` §Inputs) lists
``PostgreSQL: market_data.*`` as the historical OHLCV source it backtests
against, but no such store existed: the Regime Classifier fetches recent bars
live from Alpaca and persists nothing durable (``market_data.ohlcv_1d`` is a
small rolling window, split-adjusted, no trade count / VWAP / provenance).

This package is that store plus the backfill path that fills it:

- :mod:`shrap.market_data.store` — ``market_data.daily_bars`` DDL and an
  idempotent upsert store.
- :mod:`shrap.market_data.client` — Alpaca daily-bars fetch (IEX feed,
  ``adjustment=all``, paginated).
- :mod:`shrap.market_data.backfill` — the ``shrap-market-data-backfill`` CLI.

It lives outside ``intelligence/`` deliberately: it is shared infrastructure
consumed by Research (Evaluator) and reusable for the Regime Classifier's
threshold backfill, not owned by any one agent.

**Source honesty (recorded project fact).** Bars come from Alpaca's free IEX
feed, not the paid SIP consolidated tape. IEX prints a fraction of national
volume, so ``volume`` and any volatility derived from it read *above* the SIP
figures a live desk would see. Thresholds calibrated on this store do not
transfer 1:1 to SIP; see ``docs/infrastructure/market-data.md``.
"""

from __future__ import annotations

from shrap.market_data.store import (
    CREATE_DAILY_BARS_TABLE_SQL,
    CREATE_MARKET_DATA_SCHEMA_SQL,
    UPSERT_DAILY_BAR_SQL,
    DailyBarRow,
    PostgresDailyBarStore,
)

__all__ = [
    "CREATE_DAILY_BARS_TABLE_SQL",
    "CREATE_MARKET_DATA_SCHEMA_SQL",
    "UPSERT_DAILY_BAR_SQL",
    "DailyBarRow",
    "PostgresDailyBarStore",
]
