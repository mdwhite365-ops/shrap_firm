# Month 1 sprint log

## Card 1 — Audit Logger

**Scope:** Implement the Operations Audit Logger as the Phase 1 forensic event
store. The agent discovers Redis Streams, validates ADR-0006 envelopes through
the shared event library, and writes append-only audit rows to PostgreSQL
`ops.audit_events`. Include the minimal audit schema doc, Docker service,
Dockerfile, optional dependency wiring, and unit coverage for mapping, SQL
idempotency, sink parameter order, and polling offset advancement.

**Outcome:** PR #1 merged 2026-06-01. 354 lines Audit Logger implementation,
175 lines tests, 134 lines docs. All quality gates green. One in-scope
reconciliation handled (`psycopg` → `asyncpg`).

**Review time:** Not separately tracked in-session.

**Drift caught:** None — Hermes flagged the `psycopg` reconciliation in its own
report.

**Hermes performance:** Clean execution within scope. Real-world auth blocker
surfaced and reported honestly.

**Notes:** Existing main already contained an early Audit Logger implementation,
but it used `psycopg` and fixed stream names. This card reconciled the code with
the requested asyncpg-based shape and all-stream discovery pattern while keeping
replay idempotent on `event_id`.

**Notes for Month 2:** Card scope was right-sized. Phase A discipline (one card,
manual review, manual merge) produced clean output. No friction in the review
itself.
