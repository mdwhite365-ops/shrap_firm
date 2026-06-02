"""Audit event record mapping."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from shrap.common.envelope import Envelope


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One append-only row for ``ops.audit_events``."""

    event_id: str
    schema_version: str
    source_agent: str
    event_topic: str
    payload_json: str | None
    occurred_at: datetime
    redis_stream_id: str
    correlation_id: str | None
    payload_ref: str | None


def record_from_envelope(stream_name: str, redis_stream_id: str, envelope: Envelope) -> AuditRecord:
    """Convert an ADR-0006 envelope into an append-only audit row."""

    payload_json = None
    if envelope.payload is not None:
        payload_json = json.dumps(envelope.payload, separators=(",", ":"), sort_keys=True)

    return AuditRecord(
        event_id=envelope.event_id,
        schema_version=envelope.schema_version,
        source_agent=envelope.produced_by,
        event_topic=stream_name,
        payload_json=payload_json,
        occurred_at=envelope.produced_at,
        redis_stream_id=redis_stream_id,
        correlation_id=envelope.correlation_id,
        payload_ref=envelope.payload_ref,
    )
