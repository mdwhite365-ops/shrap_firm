# ADR-0006: Redis Streams Event Envelope

**Status:** Accepted
**Date:** 2026-05-29
**Deciders:** Mike White

## Context

ADR-0001 committed Redis Streams as the cross-department message bus. The
event envelope — stream naming, required fields, payload shape, schema
versioning — was deferred. Architecture Open Question 3 has been blocking
every department spec that produces or consumes cross-department events,
which is most of them.

The envelope is small but load-bearing. If the audit trail is supposed to
answer "why did the system do that," every event needs enough self-contained
metadata that an investigator (Mike, or a future agent) can reconstruct
provenance without a side-channel lookup. If schema versions are not
explicit, the first time a consumer reads a producer that has evolved its
payload will be a runtime crash, not a clean error.

Large payloads on the bus are an antipattern: filing text, backtest result
sets, regime profile documents are megabyte-scale, and putting them on the
bus turns Redis into a blob store. The "payload by reference" rule needs to
be a hard architectural commitment, not a guideline.

## Decision

**Stream naming.** `<department>.<event-type>` in lowercase with hyphens for
multi-word event types. Examples:

- `research.regime-updated`
- `research.strategy-promoted`
- `research.strategy-retired`
- `trading.order-submitted`
- `trading.order-filled`
- `trading.position-updated`
- `intelligence.signal`
- `structural.bias-updated`
- `risk.breach`
- `risk.veto`
- `risk.policy-updated`
- `operations.health-anomaly`
- `operations.reconciliation-completed`
- `operations.reconciliation-discrepancy`
- `reporting.daily-generated`
- `reporting.weekly-generated`
- `reporting.alert-urgent`
- `development.deployment-completed`
- `platform.cost-threshold-breach`
- `platform.llm-migration-recommended`

The `ryzen.tasks` and `ryzen.results` infrastructure streams documented in
hardware doc §1 are exceptions to the `<department>.<event-type>` rule —
they are infrastructure plumbing, not department events. They follow the
same envelope.

Stream names are versioned in the message envelope (`schema_version`), not
in the stream name. A schema change does not rename the stream.

**Required envelope fields.** Every event published to any stream carries
these fields, regardless of payload:

| Field | Type | Description |
|---|---|---|
| `event_id` | string (ULID) | Globally unique identifier, ULID format for natural time-ordering |
| `schema_version` | string (semver) | Version of the envelope + payload schema, e.g. `1.0.0` |
| `produced_at` | string (ISO 8601 UTC) | Timestamp at producer publish time |
| `produced_by` | string | Agent identity in `<department>/<agent-name>` form, e.g. `research/strategy-evaluator` |
| `correlation_id` | string (ULID) | Identifier tracing causally-related events. New on origin events; copied through on derived events |
| `payload` | object | Event-specific data |

Producers MUST populate every required field. Consumers MUST validate the
envelope before processing. An event missing required fields is logged as a
`operations.health-anomaly` and skipped — it is not treated as a producer
bug to ignore silently.

**Payload-by-reference rule.** Payloads larger than 16 KB (or containing
text, document content, or backtest result arrays) MUST NOT be inlined.
Instead, the payload carries a reference to the canonical store:

- PostgreSQL records: `{ "ref_type": "postgres", "table": "<table>", "id": "<id>" }`
- Qdrant documents: `{ "ref_type": "qdrant", "collection": "<collection>", "id": "<id>" }`
- Repo files: `{ "ref_type": "repo", "path": "<path>", "commit": "<sha>" }`

The producer writes the large object to the canonical store BEFORE
publishing the event. Consumers fetch on demand. This rule is enforced by
producer-side validation in a shared envelope library, not by convention —
violators are rejected at publish time.

The 16 KB threshold is a default; some events legitimately need more inline
context (e.g., a structural bias update with a brief rationale field). The
rule is "by default no, by exception with justification recorded in the
producing agent's spec."

**Schema versioning policy.** Semantic versioning of the envelope + payload
schema:

- **Patch** (1.0.0 → 1.0.1): backward-compatible additions to payload
  optional fields. Consumers on the prior version continue working.
- **Minor** (1.0.0 → 1.1.0): backward-compatible additions to payload that
  add required fields with documented defaults. Old consumers see the new
  field if they look; ignoring it is safe.
- **Major** (1.0.0 → 2.0.0): breaking changes. Producers MUST publish to the
  same stream under both old and new versions for a deprecation window of
  at least 30 days. Consumers must declare which schema versions they
  accept; events outside the accepted range are skipped with a logged
  warning.

Schema definitions live in `schemas/events/<stream-name>.json` in the repo,
versioned by file (e.g., `research.regime-updated.v1.json`). The shared
envelope library reads these at runtime to validate. Adding or evolving a
schema is a PR-reviewed change like any other.

**Time semantics.** All timestamps are ISO 8601 in UTC with millisecond
precision. The `produced_at` is the producer's wall clock at publish time,
not the time the underlying event occurred (which may differ — e.g., a
filing's effective date is in the payload, not in `produced_at`).

**Correlation ID semantics.** New ULID for events that originate a causal
chain (e.g., a regime change). Copied through unchanged for derived events
(e.g., the `strategy.activated` events triggered by that regime change all
share the same `correlation_id`). A consumer that fans out to multiple
derived events MUST preserve the inbound `correlation_id` on all of them.
This is what makes "trace back why we activated this strategy" possible.

## JSON Example

```json
{
  "event_id": "01HXVZK8R3M7Q9A0B1C2D3E4F5",
  "schema_version": "1.0.0",
  "produced_at": "2026-05-29T14:32:17.421Z",
  "produced_by": "research/regime-classifier",
  "correlation_id": "01HXVZK8R3M7Q9A0B1C2D3E4F5",
  "payload": {
    "regime_label": "high-vol-trend-down",
    "previous_label": "low-vol-mean-revert",
    "confidence": 0.78,
    "trigger_metrics": {
      "vix": 27.4,
      "breadth": -0.42,
      "term_structure_slope": -0.018
    },
    "ref_type": "postgres",
    "table": "regime_classifications",
    "id": 14872
  }
}
```

A derived `trading.strategy-activated` event published in response would
carry the same `correlation_id` but a fresh `event_id`:

```json
{
  "event_id": "01HXVZK9F2N5R7S9T0U1V2W3X4",
  "schema_version": "1.0.0",
  "produced_at": "2026-05-29T14:32:18.103Z",
  "produced_by": "trading/regime-router",
  "correlation_id": "01HXVZK8R3M7Q9A0B1C2D3E4F5",
  "payload": {
    "strategy_id": "sweep-fade-v3",
    "version": "3.2.1",
    "activation_reason": "regime_fit_match",
    "ref_type": "postgres",
    "table": "strategy_lifecycle",
    "id": 9214
  }
}
```

## Alternatives Considered

**CloudEvents specification.** Industry-standard envelope, well-specified,
broad tooling. Eliminated as the canonical choice but informed this design.
CloudEvents' field names (`id`, `source`, `type`, `time`) are slightly
mismatched to Shrap's needs (no explicit `correlation_id`, weaker schema
versioning). Adopting CloudEvents would mean either fighting the spec or
extending it. A custom envelope, kept small and documented, is lower
overhead. Reconsider post-sprint if interop with external tooling becomes
relevant.

**Protocol Buffers / Avro.** Strong typing, schema registry, code-generation.
Eliminated: substantial tooling overhead for a single-host system. JSON in
Redis Streams is the boring path; schema validation via JSON Schema at
producer publish time is adequate. Reconsider when the firm has more than
one host running producers.

**No envelope — payload is the message.** Eliminated by Open Question 3's
existence: every consumer would have to invent its own audit-field handling,
correlation tracing would be impossible, and schema drift would crash
silently. The envelope cost is small; the absence cost is large.

**UUID v4 for event IDs instead of ULID.** Functionally equivalent except
ULID is lexicographically sortable by time, which makes Redis Streams logs
easier to inspect by eye and avoids needing a secondary index for
chronological queries. Negligible cost; small ongoing benefit.

## Consequences

**Enables:** Every department spec downstream of this ADR can write
producers and consumers against a stable contract. The audit trail
(architecture doc §12) actually works: a `correlation_id` lets an
investigator trace a chain across departments without joining on
timestamps and guessing. Schema evolution has explicit rules instead of
ad-hoc breakage.

**Constrains:** Every producer pays a small validation cost at publish
time. Every consumer pays a small validation cost at receive time. The
shared envelope library becomes load-bearing; it must be reliable and
well-tested. Large payloads cannot ride the bus, which forces producers
to commit to a canonical store before publishing — this is the correct
behavior but it does add a write step.

**Cost:** A small Python library (`shrap.events`) implementing publish,
subscribe, and validate. Schema files in `schemas/events/`. Negligible
runtime cost on the publish path.

## Notes

The envelope is deliberately minimal. Resist the urge to add fields just
because they might be useful — every required field is a tax on every
producer. If a field is needed by only one stream, it belongs in the
payload, not the envelope.

The shared envelope library is the right place to enforce all of this.
Mike should approve the library's API once; subsequent producer/consumer
work uses the library and inherits the validation for free.
