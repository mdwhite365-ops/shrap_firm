# Execution Agent

**Department:** Trading Floor
**LLM tier:** `no-llm`
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** **Deployed** — paper order submission, pending-order re-polling, fill publication
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
| Redis: `execution.order.submitted` | Event | Submitted paper orders whose broker status should be checked. |
| Alpaca paper API | HTTP | Paper order submission and order-status endpoints only. |

## Processing

1. Read ADR-0006 envelopes from `risk.intent.approved`.
2. **Resolve this agent's account at startup.** The agent asks the broker which
   account its credentials open (`GET /v2/account` → `account_number`) and uses
   that. `EXECUTION_AGENT_ACCOUNT_ID` is an optional *assertion*: when set and
   disagreeing, the agent refuses to start, because keys and an account id from
   different books would route one strategy's orders into another strategy's
   account while every log line looked correct. The credential is the account;
   only the broker can confirm which.
3. **Route by account (ADR-0017).** One agent runs per broker account, each with
   its own consumer group on the single stream, so every agent sees every
   approved intent and acts only on its own:
   - `approved_intent_payload.account_id` matches `EXECUTION_AGENT_ACCOUNT_ID` →
     submit it;
   - it names a different account → skip and ack. Acking in *this* group leaves
     the owning agent's group untouched;
   - it names **no** account → skip and ack, logged at ERROR. This is the case
     that cannot default to "submit": if it did, all three agents would claim
     the same intent and the firm would open three positions for one signal.
     A missed trade is recoverable; three unintended ones are not.
3. Require the payload to be approved and to include `approved_intent_payload`.
3. Require `approved_intent_payload.mode == "paper"`.
4. Build an Alpaca paper market order with `symbol`, `qty`, `side`, `type=market`, `time_in_force=day`, and `client_order_id` equal to the risk event ID.
5. Submit to the injected paper broker client.
6. Publish `execution.order.submitted` with the broker order ID/status, submitted order, original risk payload, and correlation ID set to the risk event ID.
7. Read ADR-0006 envelopes from `execution.order.submitted`, **filtered on the
   same account**. The submitted event carries `account_id` for exactly this:
   asking the broker about an order id belonging to another account returns a
   404 the agent cannot act on.
8. Query Alpaca paper order status by `broker_order_id`.
9. Publish `execution.order.status-updated` for non-filled statuses and `execution.order.filled` when Alpaca reports `status=filled`; correlation ID is the submitted-order event ID.
10. Advance each stream offset only after the corresponding broker operation and event publication succeed.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis: `execution.order.submitted` | Event | Records the submitted paper order and broker response. |
| Redis: `execution.order.status-updated` | Event | Records current non-filled broker status for a submitted paper order. |
| Redis: `execution.order.filled` | Event | Records fill details when Alpaca reports the paper order filled. |

## State

Stateless in the Month 1 core. The in-memory stream offset map starts at `0-0` so queued approved risk events are replayed on startup. Consumer groups and explicit acknowledgments remain post-sprint unless needed earlier.

## Failure behavior

1. **Containment:** Non-paper intents, malformed approved payloads, broker failures, or publish failures do not advance offsets.
2. **Replay safety:** The risk event ID is used as `client_order_id`, making replay detection possible at the broker/audit layer. Full idempotent reconciliation is deferred to the Reconciliation Agent.
3. **Paper-only invariant:** The agent refuses non-paper payloads and the Alpaca settings object refuses non-paper hosts.
4. **Unroutable intents:** An approved intent with no `account_id` is claimed by no agent and logged at ERROR by each. It is acked rather than left pending — no account will appear on a replay, so deferring would wedge every agent's consumer. The producer stamps the account: the Strategy Runner from `research.strategies.account_id`, and the Strategy Fixture from its own `account_id` config (empty by default, so an armed fixture exercises the pipeline as far as risk approval and stops).

## Sprint scope

- Month 1 Card 6: Core event consumer, paper order builder, Alpaca paper order submission helper, and tests.
- Month 1 Card 7: Package as `shrap-execution-agent` with `EXECUTION_AGENT_*` settings, Dockerfile, and Compose service.
- Month 1 Card 8: Add Alpaca paper order-status polling and publish status/fill events.
- Future cards: position updates, reconciliation, and NautilusTrader bridge work.

## Deferred

- Real-money execution.
- Fill round-trip and position updates.
- NautilusTrader adapter/bridge validation.
- Consumer groups / ACK-based stream processing.
- Broker idempotency/reconciliation beyond deterministic `client_order_id`.
