"""ADR-0006 Redis event envelope.

Standard wrapper for every event published on the Redis Streams bus. Inline payloads
must be <16KB JSON; larger blobs go to durable storage and are referenced by URI.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator
from ulid import ULID

MAX_INLINE_PAYLOAD_BYTES = 16 * 1024


def must_use_ref(payload: dict[str, Any]) -> bool:
    """Return True if json-encoded payload exceeds the inline threshold."""
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return len(payload_bytes) > MAX_INLINE_PAYLOAD_BYTES


class Envelope(BaseModel):
    """ADR-0006 envelope. Exactly one of payload or payload_ref is set."""

    event_id: str = Field(..., description="ULID string")
    schema_version: str = Field(..., description="semver, e.g. '1.0.0'")
    produced_at: datetime = Field(..., description="RFC3339 UTC")
    produced_by: str = Field(..., description="agent identifier, e.g. 'health-monitor@shrap-prod'")
    correlation_id: str | None = None
    payload: dict[str, Any] | None = None
    payload_ref: str | None = None

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> Envelope:
        has_inline = self.payload is not None
        has_ref = self.payload_ref is not None
        if has_inline and has_ref:
            raise ValueError(
                "Envelope: exactly one of payload or payload_ref must be set (both set)"
            )
        if not has_inline and not has_ref:
            raise ValueError(
                "Envelope: exactly one of payload or payload_ref must be set (neither set)"
            )
        return self

    @classmethod
    def new(
        cls,
        produced_by: str,
        schema_version: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> Envelope:
        """Factory: auto-fills event_id (ULID) and produced_at (UTC now).

        If payload is too large for inline carriage, raises ValueError — caller is
        responsible for stashing the blob and constructing an Envelope with payload_ref.
        """
        if must_use_ref(payload):
            raise ValueError(
                f"payload exceeds {MAX_INLINE_PAYLOAD_BYTES} bytes; "
                "store externally and construct Envelope with payload_ref instead"
            )
        return cls(
            event_id=str(ULID()),
            schema_version=schema_version,
            produced_at=datetime.now(UTC),
            produced_by=produced_by,
            correlation_id=correlation_id,
            payload=payload,
        )

    def to_redis_fields(self) -> dict[str, str]:
        """Serialize for Redis XADD.

        Header keys are prefixed ``h_``; payload is either inline JSON or a ref.
        """
        fields: dict[str, str] = {
            "h_event_id": self.event_id,
            "h_schema_version": self.schema_version,
            "h_produced_at": self.produced_at.isoformat(),
            "h_produced_by": self.produced_by,
        }
        if self.correlation_id is not None:
            fields["h_correlation_id"] = self.correlation_id
        if self.payload is not None:
            fields["payload"] = json.dumps(self.payload, separators=(",", ":"))
        if self.payload_ref is not None:
            fields["payload_ref"] = self.payload_ref
        return fields

    @classmethod
    def from_redis_fields(cls, fields: dict[str, str]) -> Envelope:
        """Inverse of to_redis_fields."""
        payload: dict[str, Any] | None = None
        if "payload" in fields:
            payload = json.loads(fields["payload"])
        return cls(
            event_id=fields["h_event_id"],
            schema_version=fields["h_schema_version"],
            produced_at=datetime.fromisoformat(fields["h_produced_at"]),
            produced_by=fields["h_produced_by"],
            correlation_id=fields.get("h_correlation_id"),
            payload=payload,
            payload_ref=fields.get("payload_ref"),
        )
