# Audit schema

**Owner:** Operations Department
**Status:** Draft
**Date:** 2026-06-01

The Audit Logger owns a single append-only table: `ops.audit_events`.

## Table: `ops.audit_events`

| Column | Type | Required | Notes |
|---|---:|---:|---|
| `event_id` | `TEXT` | yes | ADR-0006 event ID. Primary key. Replays use `ON CONFLICT DO NOTHING`. |
| `schema_version` | `TEXT` | yes | Envelope schema version. |
| `source_agent` | `TEXT` | yes | Envelope producer / agent name. |
| `event_topic` | `TEXT` | yes | Redis stream name the event was read from. |
| `payload_json` | `JSONB` | no | Inline envelope payload, if present. |
| `occurred_at` | `TIMESTAMPTZ` | yes | Envelope produced-at timestamp. |
| `recorded_at` | `TIMESTAMPTZ` | yes | Database insertion time; defaults to `now()`. |
| `redis_stream_id` | `TEXT` | yes | Redis-generated stream entry ID for forensic replay. |
| `correlation_id` | `TEXT` | no | Envelope correlation ID, if present. |
| `payload_ref` | `TEXT` | no | External payload reference, if the payload was too large to inline. |

## Invariants

- The table is append-only from the application's perspective.
- `event_id` is globally unique and makes replay idempotent.
- `(event_topic, redis_stream_id)` is unique to preserve Redis stream identity.
- Payloads are stored as JSONB only when the ADR-0006 envelope carries an inline
  payload. `payload_ref` is preserved without inventing inline JSON.
