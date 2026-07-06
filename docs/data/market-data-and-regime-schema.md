# Market data and regime schema

**Owner:** Intelligence Department
**Status:** Implemented (PR #24)
**Date:** 2026-07-06

The Regime Classifier owns three tables: one ingested market-data table and
two append-only classification tables. DDL source of truth:
`src/shrap/intelligence/market_data.py` and
`src/shrap/intelligence/regime/store.py`.

## Table: `market_data.ohlcv_1d`

Daily OHLCV bars from Alpaca's data API (IEX feed, split-adjusted), upserted
idempotently each classifier run.

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `symbol` | `TEXT` | yes | Upper-cased ticker. Composite PK with `day`. |
| `day` | `DATE` | yes | Trading day (from the bar timestamp's date). |
| `open`/`high`/`low`/`close` | `DOUBLE PRECISION` | yes | Split-adjusted. Today's row updates intraday on each sync. |
| `volume` | `DOUBLE PRECISION` | yes | |
| `updated_at` | `TIMESTAMPTZ` | yes | Refreshed on upsert. |

## Table: `intel.regime_history`

One row per classifier run (tick), append-only.

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `event_id` | `TEXT` | yes | Run correlation ID (ULID). Primary key. |
| `at` | `TIMESTAMPTZ` | yes | Defaults to `now()`. |
| `label` | `TEXT` | yes | Emitted regime label (may be `unknown`). |
| `prior_label` | `TEXT` | yes | Label before this run. |
| `changed` | `BOOLEAN` | yes | True only on a debounced transition. |
| `leader` | `TEXT` | yes | Current challenger (equals `label` when stable). |
| `streak` | `INTEGER` | yes | Consecutive runs the challenger has led. |
| `confidence` | `DOUBLE PRECISION` | yes | Soft-condition hit ratio of the emitted label. |
| `band_lo` / `band_hi` | `DOUBLE PRECISION` | yes | Sizing-modifier band published to the Risk Officer. |
| `features` | `JSONB` | yes | The full feature vector (nulls = missing). |
| `scores` | `JSONB` | yes | Per-profile qualify/soft-hit summary. |
| `missing_features` | `JSONB` | yes | Names of features that were missing. |

Restart state (label, leader, streak) is re-derived from the latest row —
there is no separate Redis state.

## Table: `intel.regime_changes`

One row per debounced transition, append-only. Same `event_id` as the
`regime_history` row that carried the change.

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `event_id` | `TEXT` | yes | Primary key; matches the history row. |
| `at` | `TIMESTAMPTZ` | yes | Defaults to `now()`. |
| `prior_label` / `new_label` | `TEXT` | yes | The transition. |
| `streak` | `INTEGER` | yes | Debounce streak at the moment of change. |
| `features` | `JSONB` | yes | Feature vector at the transition. |

## Invariants

- All three tables are append-only from the application's perspective
  (`ohlcv_1d` upserts revise bar values but never delete rows).
- `event_id` makes classification replay idempotent (`ON CONFLICT DO NOTHING`).
- Feature semantics are documented in `docs/regimes/_features.md`.
