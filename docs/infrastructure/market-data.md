# Market Data Store

**Document version:** 0.1 (draft)
**Last updated:** 2026-07-23
**Owner:** Mike White
**Status:** Living document — updated as the store grows

---

## Purpose

The Strategy Evaluator backtests against `market_data.*` historical OHLCV
(`docs/agents/research/strategy-evaluator.md` §Inputs), but until this card no
such store existed. The Regime Classifier fetches recent bars live from Alpaca
and persists only a small rolling, split-adjusted window in
`market_data.ohlcv_1d` — not enough history, no trade count, no VWAP, no
provenance. This document describes the durable store that fills that gap and
the backfill CLI that loads it.

The store is **shared infrastructure**, not owned by any one agent. It lives in
`src/shrap/market_data/`, outside `intelligence/`, because two consumers want
it: the Evaluator (backtest data) and the Regime Classifier (threshold
backfill).

---

## The store: `market_data.daily_bars`

One row per `(ticker, session_date, adjustment)`. Daily grain only, for now.

| Column | Type | Notes |
|---|---|---|
| `ticker` | TEXT | Upper-cased symbol |
| `session_date` | DATE | Trading day |
| `open` / `high` / `low` / `close` | DOUBLE PRECISION | OHLC |
| `volume` | DOUBLE PRECISION | IEX volume — see the limitation below |
| `trade_count` | BIGINT (nullable) | Alpaca bar `n`; not every bar carries it |
| `vwap` | DOUBLE PRECISION (nullable) | Alpaca bar `vw` |
| `adjustment` | TEXT | Price-adjustment mode; `all` (splits + dividends) |
| `source` | TEXT | Feed provenance; `alpaca-iex` |
| `fetched_at` | TIMESTAMPTZ | When this row was last written |

**Primary key `(ticker, session_date, adjustment)`.** The upsert is
`ON CONFLICT DO UPDATE`, so re-running the backfill over the same window is
idempotent — it overwrites OHLCV and refreshes `fetched_at` rather than
duplicating rows. `adjustment` is in the key on purpose: a later card can
backfill a second adjustment mode (or SIP) for the same ticker/date without
collision, and the Evaluator selects the mode it wants unambiguously.

Schema and table are created with the house `CREATE ... IF NOT EXISTS`
ensure-schema pattern (same as the Tech Watcher and Filing Processor stores),
run once at the start of a non-dry-run backfill.

---

## Source and adjustment choices

**Adjustment: `all`.** Splits *and* dividends. This is the correct basis for
backtesting total return; a strategy evaluated on split-only prices misreads
every dividend as a price drop. (The Regime Classifier's live client uses
`split` only, which is fine for its short-window regime features but wrong for
a multi-year backtest.)

**Feed: `iex`.** Alpaca's free tier.

### The IEX limitation (recorded project fact)

The bars come from the **IEX feed, not the paid SIP consolidated tape**. IEX is
a single venue that prints only a fraction of national volume. Consequences the
Evaluator and anyone calibrating on this data must keep in mind:

- **`volume` reads high relative to reality.** It is IEX-venue volume, not
  consolidated volume. Any ADV-participation or liquidity model built on it is
  optimistic about how much size the market absorbs.
- **Volatility derived from IEX prices reads above SIP.** This is the same bias
  already recorded for the Regime Classifier: thresholds calibrated on the IEX
  proxy sit higher than they would on SIP (see the melt-up ceiling note in
  `src/shrap/intelligence/regime/profiles.py` and the regime tests).
- **Thresholds do not transfer 1:1 to SIP.** If the firm ever buys the SIP
  feed, anything calibrated against `alpaca-iex` must be re-derived, not
  reused. The `source` column exists precisely so a future SIP backfill is
  distinguishable and the two are never silently mixed.

This is a deliberate, documented trade-off: free data now, honest about its
bias, re-calibrate if and when a paid feed is justified.

---

## Backfill CLI: `shrap-market-data-backfill`

Explicit ticker list and date window; upserts into `market_data.daily_bars`.

| Flag | Default | Meaning |
|---|---|---|
| `--tickers AAPL,MSFT,...` | — | Comma-separated symbols |
| `--tickers-file <path>` | — | One symbol per line; `#` comments and blanks ignored |
| `--since YYYY-MM-DD` | ~5 years ago | Earliest session date (inclusive) |
| `--until YYYY-MM-DD` | today | Latest session date (inclusive) |
| `--dry-run` | off | Fetch and report row counts; write nothing |

`--tickers` and `--tickers-file` merge (deduped, order preserved); at least one
is required. Per-ticker progress is logged via structlog: rows fetched, rows
upserted, and the date span seen.

**Ticker source is temporary.** The default set will later come from the
Universe Curator's Tier 3 state. Until that agent exists, the caller names the
tickers explicitly — there is no implicit universe default.

Credentials come from the bare `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` /
`ALPACA_DATA_ENDPOINT` environment names the rest of the firm's data-host
clients already use; the endpoint is validated data-host-only. Credential
values are never logged.

### Usage — dev (MacBook)

```bash
# Dry run: see how many bars two names would pull over five years.
uv run --extra market-data shrap-market-data-backfill \
  --tickers AAPL,MSFT --dry-run

# Real backfill of an explicit window into the local/dev Postgres.
MARKET_DATA_POSTGRES_DSN="postgresql://shrap:shrap@localhost:5432/shrap" \
ALPACA_API_KEY=... ALPACA_SECRET_KEY=... \
  uv run --extra market-data shrap-market-data-backfill \
  --tickers-file universe.txt --since 2021-01-01 --until 2026-07-01
```

### Usage — Dell (docker)

The `market-data` service is gated behind the `tools` compose profile, so it is
**not** started by `docker compose up`. Run it on demand:

```bash
docker compose run --rm market-data \
  shrap-market-data-backfill --tickers AAPL,MSFT,NVDA,LMT --since 2021-01-01
```

It reads `ALPACA_*` and `MARKET_DATA_POSTGRES_DSN` from `infra/.env` and the
compose service definition, and reaches Postgres over `shrap_net`.

---

## Deferred

Explicitly out of scope for this card, each a future card of its own:

- **Intraday bars.** The Evaluator's spec also lists 1m/5m OHLCV; this store is
  daily only. An intraday table (or a `timeframe` column) comes later.
- **Corporate-actions table.** Splits and dividends are folded into
  `adjustment=all` prices here; a separate point-in-time corporate-actions
  table (for the Evaluator's borrow-cost and exact-adjustment needs) is not
  built yet.
- **Nightly refresh service.** This is a run-to-completion backfill tool, not a
  scheduled always-on service. An incremental nightly append (fetch since
  `max(session_date)`) is a later card.
- **SIP feed.** See the IEX limitation above. A paid-feed backfill is a
  cost/benefit decision for Mike, and would land under a distinct `source`.
