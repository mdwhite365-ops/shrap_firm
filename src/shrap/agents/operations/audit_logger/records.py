"""Audit event record mapping.

The Audit Logger treats Redis Streams as the delivery mechanism and PostgreSQL as
append-only durable history. This module is deliberately small and deterministic:
validate the envelope upstream, then preserve it without interpretation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from shrap.common.envelope import Envelope


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One append-only row for ``ops.audit_events``."""

    stream_name: str
    redis_stream_id: str
    event_id: str
    schema_version: str
    produced_at: datetime
    produced_by: str
    correlation_id: str | None
    payload_json: str | None
    payload_ref: str | None


def record_from_envelope(stream_name: str, redis_stream_id: str, envelope: Envelope) -> AuditRecord:
    """Convert an ADR-0006 envelope into an append-only audit row."""

    payload_json = None
    if envelope.payload is not None:
        payload_json = json.dumps(envelope.payload, separators=(",", ":"), sort_keys=True)

    return AuditRecord(
        stream_name=stream_name,
        redis_stream_id=redis_stream_id,
        event_id=envelope.event_id,
        schema_version=envelope.schema_version,
        produced_at=envelope.produced_at,
        produced_by=envelope.produced_by,
        correlation_id=envelope.correlation_id,
        payload_json=payload_json,
        payload_ref=envelope.payload_ref,
    )
