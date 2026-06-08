# Pre-Trade Checker

**Department:** Risk and Compliance
**LLM tier:** `no-llm`
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-06-08
**Author:** Mike White

## Purpose

The Pre-Trade Checker is the Month 1 wire-only Risk Officer stub. It is a long-running risk gate that consumes `trading.decision.intent` events from Redis Streams, invokes the existing pure-function `PreTradeChecker`, and publishes either `risk.intent.approved` or `risk.intent.vetoed`.

This agent exists to keep the trading path observable and independently auditable. The Decision Maker should not call risk logic synchronously in-process. Every step must move through the event bus so the audit trail can reconstruct: signal -> decision intent -> risk decision -> later execution.

Architectural choice for Card 3: option A. `PreTradeChecker` becomes its own long-running agent process that subscribes to `trading.decision.intent` and publishes risk intent events. Option B, where the Decision Maker stub synchronously invokes `PreTradeChecker`, was rejected because Shrap's agent pattern is process-per-agent, the full Risk Officer spec is its own agent, future risk work needs independent async state and correlation checks, and Redis Streams give cleaner observability.

## Trigger

- **Schedule:** Continuous while the service is running.
- **Event:** Subscribes to Redis Stream `trading.decision.intent`.
- **On-demand:** Can be run in one-iteration mode by tests and smoke workflows.

## Cross-references

**Depends on:** Decision Maker stub, ADR-0006 event envelope, Redis Streams, `PreTradeChecker` pure function.
**Depended on by:** Future Execution Agent, Audit Logger, Month 1 paper-trading smoke path.
**Related ADRs:** ADR-0006 Redis Streams Event Envelope.
**Related architecture sections:** `docs/agents/risk-compliance/risk-officer.md`.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `trading.decision.intent` | Event | Order intents requiring deterministic paper-only pre-trade approval or veto. |
| Repo: `src/shrap/risk_compliance/pre_trade.py` | Code | Existing pure-function `PreTradeChecker` and `RiskPolicy`. |

## Processing

1. Read one or more ADR-0006 envelopes from `trading.decision.intent` using `shrap.events.EventSubscriber`.
2. Extract the intent payload. The payload shape is the existing intent contract from `src/shrap/trading_floor/intent.py` plus Card 2 stub additions; the risk gate currently needs `ticker`, `quantity`, and `mode`.
3. Invoke the existing pure-function `PreTradeChecker(policy).check(intent_payload)`.
4. If approved, publish `risk.intent.approved` with the original intent payload preserved, the requested quantity, and the scaled `approved_quantity` chosen by `PreTradeChecker`.
5. If vetoed, publish `risk.intent.vetoed` with the original intent payload preserved, `reason`/`reason_code`, and the structured reasons returned by `PreTradeChecker`.
6. Publish through `shrap.events.EventPublisher` only. Do not invent a parallel envelope or Redis mechanism.
7. Set the risk event `correlation_id` to the original intent envelope's `event_id` so the immediate causal edge is explicit. The upstream signal remains traceable through the intent event's own `correlation_id`.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis: `risk.intent.approved` | Event | Approved intent with scaled quantity and original intent payload. |
| Redis: `risk.intent.vetoed` | Event | Vetoed intent with reason code, reason text, and original intent payload. |

## LangGraph structure

Not used. This is a deterministic Python service. No LLM and no LangGraph are needed for a reproducible risk gate.

## State

Stateless in the Month 1 Card 3 stub. The pure-function check has no persistence needs. Stream offsets live in the in-memory `last_ids` map while the process runs; replaying an already-seen intent deterministically emits the same decision payload.

## Failure behavior

1. **Containment:** A vetoed event stops the chain before execution. If the agent crashes, it stops consuming and therefore does not approve anything accidentally. Incorrect approvals would propagate to later execution, so the real-money invariant remains hard-coded in `PreTradeChecker` and must also be rechecked by future execution.
2. **Replay safety:** Safe for the stub. If the same intent event is processed twice, the same policy and payload produce the same risk decision. Emitting twice is acceptable because the audit table dedupes by risk event `event_id` and the repeated decision is deterministic.
3. **Degraded operation:** If Redis is down, the gate does not consume or publish; intents remain queued in `trading.decision.intent` until Redis and the service recover. The firm should not execute without a risk decision.

## Sprint scope

- Month 1 Card 3: Wire-only event-loop wrapper around the existing pure-function `PreTradeChecker`; publish approved/vetoed events with correlation and tests proving the signal-to-risk path.
- Future cards: Position correlation, exposure limits, kill-switch state, daily-loss limits, execution handoff, and persistent risk decision tables.

## Deferred

- Real risk sizing beyond the current max-quantity cap.
- Position and portfolio exposure checks.
- Correlation clustering.
- Persistent risk decision storage.
- Real-money execution approval.

## Open questions

None for Card 3. The architectural choice is explicit: this is an independent long-running agent process, not synchronous Decision Maker coupling.
