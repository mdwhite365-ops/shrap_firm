"""PostgreSQL sink for the Audit Logger."""

from __future__ import annotations

from typing import Protocol

from shrap.agents.operations.audit_logger.records import AuditRecord

CREATE_AUDIT_SCHEMA_SQL = "CREATE SCHEMA IF NOT EXISTS ops"

CREATE_AUDIT_EVENTS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ops.audit_events (
    id BIGSERIAL PRIMARY KEY,
    stream_name TEXT NOT NULL,
    redis_stream_id TEXT NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    schema_version TEXT NOT NULL,
    produced_at TIMESTAMPTZ NOT NULL,
    produced_by TEXT NOT NULL,
    correlation_id TEXT,
    payload_json JSONB,
    payload_ref TEXT,
    audited_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (stream_name, redis_stream_id)
)
""".strip()

CREATE_AUDIT_EVENTS_PRODUCED_AT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS audit_events_produced_at_idx
ON ops.audit_events (produced_at)
""".strip()

CREATE_AUDIT_EVENTS_CORRELATION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS audit_events_correlation_id_idx
ON ops.audit_events (correlation_id)
WHERE correlation_id IS NOT NULL
""".strip()

INSERT_AUDIT_EVENT_SQL = """
INSERT INTO ops.audit_events (
    stream_name,
    redis_stream_id,
    event_id,
    schema_version,
    produced_at,
    produced_by,
    correlation_id,
    payload_json,
    payload_ref
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (event_id) DO NOTHING
""".strip()


class AsyncConnection(Protocol):
    async def execute(self, sql: str, params: tuple[object, ...] | None = None) -> object: ...

    async def __aenter__(self) -> AsyncConnection: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def connection(self) -> AsyncConnection: ...


class PostgresAuditSink:
    """Append-only sink for audit events."""

    def __init__(self, pool: AsyncPool) -> None:
        self._pool = pool

    async def ensure_schema(self) -> None:
        """Create the audit schema/table/indexes if absent."""
        async with self._pool.connection() as conn:
            await conn.execute(CREATE_AUDIT_SCHEMA_SQL)
            await conn.execute(CREATE_AUDIT_EVENTS_TABLE_SQL)
            await conn.execute(CREATE_AUDIT_EVENTS_PRODUCED_AT_INDEX_SQL)
            await conn.execute(CREATE_AUDIT_EVENTS_CORRELATION_INDEX_SQL)

    async def insert(self, record: AuditRecord) -> None:
        """Insert one record; duplicate event IDs are ignored for replay safety."""
        params = (
            record.stream_name,
            record.redis_stream_id,
            record.event_id,
            record.schema_version,
            record.produced_at,
            record.produced_by,
            record.correlation_id,
            record.payload_json,
            record.payload_ref,
        )
        async with self._pool.connection() as conn:
            await conn.execute(INSERT_AUDIT_EVENT_SQL, params)
