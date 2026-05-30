"""Tests for the ADR-0006 envelope."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from shrap.common.envelope import (
    MAX_INLINE_PAYLOAD_BYTES,
    Envelope,
    must_use_ref,
)


def test_new_returns_valid_envelope_with_ulid_and_utc() -> None:
    env = Envelope.new(
        produced_by="health-monitor@shrap-prod",
        schema_version="1.0.0",
        payload={"ok": True},
    )
    assert env.event_id and len(env.event_id) == 26  # ULID canonical length
    assert env.produced_at.tzinfo is not None
    assert env.produced_at.utcoffset() == UTC.utcoffset(env.produced_at)
    assert env.payload == {"ok": True}
    assert env.payload_ref is None


def test_rejects_both_payload_and_ref_set() -> None:
    with pytest.raises(ValidationError):
        Envelope(
            event_id="01HW0000000000000000000000",
            schema_version="1.0.0",
            produced_at=datetime.now(UTC),
            produced_by="x@y",
            payload={"a": 1},
            payload_ref="postgres://shrap/x/1",
        )


def test_rejects_neither_payload_nor_ref_set() -> None:
    with pytest.raises(ValidationError):
        Envelope(
            event_id="01HW0000000000000000000000",
            schema_version="1.0.0",
            produced_at=datetime.now(UTC),
            produced_by="x@y",
        )


def test_to_redis_fields_roundtrips_inline_payload() -> None:
    env = Envelope.new(
        produced_by="health-monitor@shrap-prod",
        schema_version="1.0.0",
        payload={"cpu": 0.42, "tags": ["a", "b"]},
        correlation_id="corr-123",
    )
    fields = env.to_redis_fields()
    assert "payload" in fields and "payload_ref" not in fields
    assert fields["h_correlation_id"] == "corr-123"
    restored = Envelope.from_redis_fields(fields)
    assert restored.event_id == env.event_id
    assert restored.schema_version == env.schema_version
    assert restored.produced_by == env.produced_by
    assert restored.correlation_id == env.correlation_id
    assert restored.payload == env.payload
    assert restored.produced_at == env.produced_at


def test_must_use_ref_for_large_payload() -> None:
    big = {"blob": "x" * (MAX_INLINE_PAYLOAD_BYTES + 100)}
    assert must_use_ref(big) is True
    assert must_use_ref({"small": "thing"}) is False


def test_new_raises_when_payload_too_large() -> None:
    big = {"blob": "x" * (MAX_INLINE_PAYLOAD_BYTES + 100)}
    with pytest.raises(ValueError, match="exceeds"):
        Envelope.new(produced_by="x@y", schema_version="1.0.0", payload=big)


def test_to_redis_fields_with_payload_ref_omits_inline() -> None:
    env = Envelope(
        event_id="01HW0000000000000000000000",
        schema_version="1.0.0",
        produced_at=datetime.now(UTC),
        produced_by="health-monitor@shrap-prod",
        payload_ref="postgres://shrap/health_ticks/12345",
    )
    fields = env.to_redis_fields()
    assert fields["payload_ref"] == "postgres://shrap/health_ticks/12345"
    assert "payload" not in fields
    restored = Envelope.from_redis_fields(fields)
    assert restored.payload is None
    assert restored.payload_ref == env.payload_ref
