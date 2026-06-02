# Month 1 sprint log

## Card 1 — Audit Logger

**Scope:** Implement the Operations Audit Logger as the Phase 1 forensic event
store. The agent discovers Redis Streams, validates ADR-0006 envelopes through
the shared event library, and writes append-only audit rows to PostgreSQL
`ops.audit_events`. Include the minimal audit schema doc, Docker service,
Dockerfile, optional dependency wiring, and unit coverage for mapping, SQL
idempotency, sink parameter order, and polling offset advancement.

**Outcome:** PR opened, awaiting review.

**Notes:** Existing main already contained an early Audit Logger implementation,
but it used `psycopg` and fixed stream names. This card reconciles the code with
the requested asyncpg-based shape and all-stream discovery pattern while keeping
replay idempotent on `event_id`.
