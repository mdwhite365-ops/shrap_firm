# Execution Agent

**Department:** Trading Floor
**LLM tier:** `no-llm`
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Month 1 paper-order core in progress
**Date:** 2026-06-17
**Author:** Mike White

## Purpose

The Execution Agent is the final Month 1 inner-loop paper spine component. It consumes `risk.intent.approved` events, converts the preserved `approved_intent_payload` into an Alpaca paper market order, submits that order through the paper broker interface, and publishes `execution.order.submitted` so Operations can audit the order handoff.

The agent is deterministic and no-LLM. It refuses any approved intent whose mode is not `paper`. Real-money endpoints remain blocked by the Alpaca paper settings validator and by this agent's payload checks.

## Trigger

- **Schedule:** Continuous while the service is running.
- **Event:** Subscribes to Redis Stream `risk.intent.approved`.
- **On-demand:** Can be run in one-iteration mode by tests and smoke workflows.

## Cross-references

**Depends on:** Pre-Trade Checker, ADR-0006 event envelope, Redis Streams, Alpaca paper client.
**Depended on by:** Audit Logger, Reconciliation Agent, future fill/position tracking.
**Related architecture sections:** `docs/01-roadmap.md` Month 1 inner-loop exit criteria.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `risk.intent.approved` | Event | Risk-approved paper intents containing `approved_intent_payload`. |
| Alpaca paper API | HTTP | Paper order submission endpoint only. |

## Processing

1. Read ADR-0006 envelopes from `risk.intent.approved`.
2. Require the payload to be approved and to include `approved_intent_payload`.
3. Require `approved_intent_payload.mode == "paper"`.
4. Build an Alpaca paper market order with `symbol`, `qty`, `side`, `type=market`, `time_in_force=day`, and `client_order_id` equal to the risk event ID.
5. Submit to the injected paper broker client.
6. Publish `execution.order.submitted` with the broker order ID/status, submitted order, original risk payload, and correlation ID set to the risk event ID.
7. Advance the stream offset only after successful broker submission and successful event publication.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis: `execution.order.submitted` | Event | Records the submitted paper order and broker response. |

## State

Stateless in the Month 1 core. The in-memory stream offset map starts at `0-0` so queued approved risk events are replayed on startup. Consumer groups and explicit acknowledgments remain post-sprint unless needed earlier.

## Failure behavior

1. **Containment:** Non-paper intents, malformed approved payloads, broker failures, or publish failures do not advance offsets.
2. **Replay safety:** The risk event ID is used as `client_order_id`, making replay detection possible at the broker/audit layer. Full idempotent reconciliation is deferred to the Reconciliation Agent.
3. **Paper-only invariant:** The agent refuses non-paper payloads and the Alpaca settings object refuses non-paper hosts.

## Sprint scope

- Month 1 Card 6: Core event consumer, paper order builder, Alpaca paper order submission helper, and tests.
- Future cards: service packaging, fill polling/streaming, position updates, reconciliation, and NautilusTrader bridge work.

## Deferred

- Real-money execution.
- Fill round-trip and position updates.
- NautilusTrader adapter/bridge validation.
- Consumer groups / ACK-based stream processing.
- Broker idempotency/reconciliation beyond deterministic `client_order_id`.
