from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from shrap.common.envelope import Envelope


def test_audit_record_preserves_stream_identity_and_envelope_fields() -> None:
    from shrap.agents.operations.audit_logger.records import record_from_envelope

    env = Envelope(
        event_id="01KSVW00000000000000000000",
        schema_version="1.0.0",
        produced_at=datetime(2026, 5, 30, 7, 30, tzinfo=UTC),
        produced_by="operations/health-monitor",
        correlation_id="01KSVW11111111111111111111",
        payload={"summary": {"ok": 1, "degraded": 0}},
    )

    record = record_from_envelope("ops.health-tick", "1780125772540-0", env)

    assert record.event_topic == "ops.health-tick"
    assert record.redis_stream_id == "1780125772540-0"
    assert record.event_id == "01KSVW00000000000000000000"
    assert record.schema_version == "1.0.0"
    assert record.occurred_at == datetime(2026, 5, 30, 7, 30, tzinfo=UTC)
    assert record.source_agent == "operations/health-monitor"
    assert record.correlation_id == "01KSVW11111111111111111111"
    assert json.loads(record.payload_json or "{}") == {"summary": {"ok": 1, "degraded": 0}}
    assert record.payload_ref is None


def test_audit_record_preserves_payload_ref_without_inlining_payload() -> None:
    from shrap.agents.operations.audit_logger.records import record_from_envelope

    env = Envelope(
        event_id="01KSVW22222222222222222222",
        schema_version="1.0.0",
        produced_at=datetime(2026, 5, 30, 7, 31, tzinfo=UTC),
        produced_by="research/tech-watcher",
        payload_ref="repo://docs/research/world-changers/proposed/demo.md",
    )

    record = record_from_envelope("research.world-changer-proposed", "1780125800000-0", env)

    assert record.payload_json is None
    assert record.payload_ref == "repo://docs/research/world-changers/proposed/demo.md"


def test_insert_sql_is_append_only_and_idempotent_on_event_id() -> None:
    from shrap.agents.operations.audit_logger.db import INSERT_AUDIT_EVENT_SQL

    assert "INSERT INTO ops.audit_events" in INSERT_AUDIT_EVENT_SQL
    assert "ON CONFLICT (event_id) DO NOTHING" in INSERT_AUDIT_EVENT_SQL
    assert "UPDATE" not in INSERT_AUDIT_EVENT_SQL.upper()
    assert "$1" in INSERT_AUDIT_EVENT_SQL


@pytest.mark.asyncio
async def test_poll_once_discovers_matching_streams_and_advances_offsets() -> None:
    from shrap.agents.operations.audit_logger.agent import poll_once

    env = Envelope(
        event_id="01KSVW33333333333333333333",
        schema_version="1.0.0",
        produced_at=datetime(2026, 5, 30, 7, 32, tzinfo=UTC),
        produced_by="operations/health-monitor",
        payload={"summary": {"ok": 1}},
    )

    class FakeRedis:
        async def scan_iter(self, match: str) -> object:
            assert match == "ops.*"
            for stream in ["ops.health-tick"]:
                yield stream

        async def type(self, key: str) -> str:
            assert key == "ops.health-tick"
            return "stream"

        async def xread(
            self, streams: dict[str, str], count: int, block: int
        ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
            assert streams == {"ops.health-tick": "0-0"}
            assert count == 10
            assert block == 1
            return [("ops.health-tick", [("1780125900000-0", env.to_redis_fields())])]

    class FakeSink:
        def __init__(self) -> None:
            self.records: list[object] = []

        async def insert(self, record: object) -> None:
            self.records.append(record)

    sink = FakeSink()
    last_ids: dict[str, str] = {}

    written = await poll_once(
        FakeRedis(),  # type: ignore[arg-type]
        sink,  # type: ignore[arg-type]
        last_ids,
        stream_pattern="ops.*",
        start_id="0-0",
        count=10,
        block_ms=1,
    )

    assert written == 1
    assert last_ids == {"ops.health-tick": "1780125900000-0"}
    assert len(sink.records) == 1


@pytest.mark.asyncio
async def test_sink_inserts_record_with_expected_asyncpg_parameters() -> None:
    from shrap.agents.operations.audit_logger.db import PostgresAuditSink
    from shrap.agents.operations.audit_logger.records import AuditRecord

    class FakeConnection:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def execute(self, sql: str, *args: object) -> None:
            self.calls.append((sql, args))

    class FakeAcquire:
        def __init__(self, conn: FakeConnection) -> None:
            self.conn = conn

        async def __aenter__(self) -> FakeConnection:
            return self.conn

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    class FakePool:
        def __init__(self) -> None:
            self.conn = FakeConnection()

        def acquire(self) -> FakeAcquire:
            return FakeAcquire(self.conn)

    record = AuditRecord(
        event_id="01KSVW00000000000000000000",
        schema_version="1.0.0",
        source_agent="operations/health-monitor",
        event_topic="ops.health-tick",
        payload_json='{"ok":true}',
        occurred_at=datetime(2026, 5, 30, 7, 30, tzinfo=UTC),
        redis_stream_id="1780125772540-0",
        correlation_id=None,
        payload_ref=None,
    )
    pool = FakePool()
    sink = PostgresAuditSink(pool)  # type: ignore[arg-type]

    await sink.insert(record)

    assert len(pool.conn.calls) == 1
    sql, params = pool.conn.calls[0]
    assert "INSERT INTO ops.audit_events" in sql
    assert params == (
        "01KSVW00000000000000000000",
        "1.0.0",
        "operations/health-monitor",
        "ops.health-tick",
        '{"ok":true}',
        datetime(2026, 5, 30, 7, 30, tzinfo=UTC),
        "1780125772540-0",
        None,
        None,
    )
