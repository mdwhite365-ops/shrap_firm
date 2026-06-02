# Audit Logger

**Department:** Operations
**LLM tier:** `no-llm` — deterministic stream consumer.
**Status:** Draft
**Date:** 2026-06-01
**Author:** Mike White
**Version:** 0.1

## Purpose

The Audit Logger is the firm's append-only event recorder. It discovers Redis
Streams, reads ADR-0006 event envelopes, validates them through the shared event
library, and writes one durable row per event to PostgreSQL.

It exists so every later paper-trading decision can be reconstructed from the
message bus. If an order reaches Alpaca paper, the firm should be able to trace
that action back through Decision Maker, Risk, health, and supporting context
events without relying on container logs.

## Trigger

- **Runtime:** Long-running deterministic service.
- **Input:** Redis Streams matching `AUDIT_LOGGER_STREAM_PATTERN`.
- **Default pattern:** `*`, meaning all Redis Stream keys discovered by SCAN and
  filtered by Redis type `stream`.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis Streams | Event | ADR-0006 envelopes from all firm agents |
| Environment | Config | Redis URL, Postgres DSN, stream pattern, batch size, retry delay |

## Processing

1. Discover Redis keys matching the configured stream pattern.
2. Filter discovered keys to Redis type `stream`.
3. Read from known streams with `XREAD`, starting at `0-0` by default during the
   Phase 1 sprint so replays are safe.
4. Validate each entry as an ADR-0006 envelope via `shrap.events.EventSubscriber`.
5. Map the envelope into an audit record without interpreting the payload.
6. Insert into `ops.audit_events` with `ON CONFLICT (event_id) DO NOTHING`.
7. Advance the in-memory Redis stream offset after each processed entry.

## Outputs

| Destination | Type | Description |
|---|---|---|
| PostgreSQL `ops.audit_events` | Append-only table | Durable forensic record of each event |
| Logs | Structured log | Batch counts and per-entry failures |

## State

| What | Store | Notes |
|---|---|---|
| Audit rows | PostgreSQL `ops.audit_events` | Durable, idempotent on `event_id` |
| Last Redis IDs | Process memory | Rebuilt on restart; replay is safe because inserts are idempotent |

## Failure behavior

- Invalid or malformed events are logged and skipped; the agent continues.
- Duplicate event IDs are ignored by PostgreSQL.
- Database or Redis polling failures are logged and retried after the configured
  retry delay.
- The agent does not mutate source streams and does not acknowledge/delete
  entries. Redis remains the source replay surface; PostgreSQL is the forensic
  audit surface.

## Sprint scope

Month 1 scope is intentionally narrow:

- Redis Stream discovery and polling.
- ADR-0006 envelope validation.
- PostgreSQL schema creation if absent.
- Append-only event persistence.
- Docker Compose service and Dockerfile.
- Unit tests for record mapping, SQL idempotency, sink parameters, and poller
  offset advancement.

## Deferred

- Consumer groups and durable stream offsets.
- Hypertable conversion if event volume justifies it.
- Dead-letter table for malformed events.
- Postgres partitioning/retention policy.
