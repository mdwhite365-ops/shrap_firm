"""PostgreSQL sink for the Audit Logger."""

from __future__ import annotations

from typing import Protocol

from shrap.agents.operations.audit_logger.records import AuditRecord

CREATE_TIMESCALE_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS timescaledb"
CREATE_AUDIT_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS ops"

CREATE_AUDIT_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ops.audit_events (
    event_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    source_agent TEXT NOT NULL,
    event_topic TEXT NOT NULL,
    payload_json JSONB,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    redis_stream_id TEXT NOT NULL,
    correlation_id TEXT,
    payload_ref TEXT,
    UNIQUE (event_topic, redis_stream_id)
)
""".strip()

CREATE_AUDIT_EVENTS_OCCURRED_AT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS audit_events_occurred_at_idx
ON ops.audit_events (occurred_at)
""".strip()

CREATE_AUDIT_EVENTS_TOPIC_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS audit_events_topic_idx
ON ops.audit_events (event_topic, occurred_at DESC)
""".strip()

INSERT_AUDIT_EVENT_SQL = """
INSERT INTO ops.audit_events (
    event_id,
    schema_version,
    source_agent,
    event_topic,
    payload_json,
    occurred_at,
    redis_stream_id,
    correlation_id,
    payload_ref
)
VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
ON CONFLICT (event_id) DO NOTHING
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, *args: object) -> object: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...

    async def close(self) -> None: ...


class PostgresAuditSink:
    """Append-only sink for audit events."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        """Create the audit schema/table/indexes if absent."""
        async with self._pool.acquire() as conn:
            await conn.execute(CREATE_TIMESCALE_EXTENSION_SQL)
            await conn.execute(CREATE_AUDIT_SCHEMA_SQL)
            await conn.execute(CREATE_AUDIT_EVENTS_TABLE_SQL)
            await conn.execute(CREATE_AUDIT_EVENTS_OCCURRED_AT_INDEX_SQL)
            await conn.execute(CREATE_AUDIT_EVENTS_TOPIC_INDEX_SQL)

    async def insert(self, record: AuditRecord) -> None:
        """Insert one record; duplicate event IDs are ignored for replay safety."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                INSERT_AUDIT_EVENT_SQL,
                record.event_id,
                record.schema_version,
                record.source_agent,
                record.event_topic,
                record.payload_json,
                record.occurred_at,
                record.redis_stream_id,
                record.correlation_id,
                record.payload_ref,
            )
