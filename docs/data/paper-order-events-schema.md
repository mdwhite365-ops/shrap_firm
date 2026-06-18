# Paper order event persistence

**Owner:** Trading Floor
**Status:** Draft
**Date:** 2026-06-18

The Execution Agent emits broker-facing paper order events. Card 10 adds an append-only persistence seam for those events so later reconciliation can compare Shrap's internal order trail against Alpaca paper.

## Table: `trading.paper_order_events`

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `event_id` | `TEXT` | yes | ADR-0006 event ID. Primary key. Replays use `ON CONFLICT DO NOTHING`. |
| `event_topic` | `TEXT` | yes | Redis stream name: `execution.order.submitted`, `execution.order.status-updated`, or `execution.order.filled`. |
| `redis_stream_id` | `TEXT` | yes | Redis-generated stream entry ID for forensic replay. |
| `correlation_id` | `TEXT` | no | Immediate causal parent event ID from the ADR-0006 envelope. |
| `broker` | `TEXT` | yes | Current value: `alpaca-paper`. |
| `broker_order_id` | `TEXT` | yes | Alpaca paper order ID. |
| `status` | `TEXT` | no | Broker status from the event payload. |
| `symbol` | `TEXT` | no | Order symbol when extractable. |
| `side` | `TEXT` | no | `buy` / `sell` when extractable. |
| `quantity` | `TEXT` | no | Requested order quantity as broker-compatible text. |
| `filled_quantity` | `TEXT` | no | Filled quantity from status/fill payloads. |
| `filled_avg_price` | `TEXT` | no | Broker reported average fill price. |
| `submitted_order` | `JSONB` | no | Submitted order payload, when available. |
| `broker_response` | `JSONB` | no | Raw Alpaca paper response included in the execution event. |
| `payload_json` | `JSONB` | yes | Full inline event payload. |
| `occurred_at` | `TIMESTAMPTZ` | yes | Envelope produced-at timestamp. |
| `recorded_at` | `TIMESTAMPTZ` | yes | Database insertion time; defaults to `now()`. |

## Invariants

- The table is append-only from the application's perspective.
- `event_id` is globally unique and makes replay idempotent.
- `(event_topic, redis_stream_id)` is unique to preserve Redis stream identity.
- This table is an event trail, not an authoritative current-position table.
- Reconciliation and position-state derivation are future cards.
