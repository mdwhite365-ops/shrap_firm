# Pre-Trade Checker

**Department:** Risk and Compliance
**LLM tier:** `no-llm`
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Implemented for Month 1 wire path; deployable service PR in progress
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

**Depends on:** Decision Maker stub, ADR-0006 event envelope, Redis Streams, `PreTradeChecker` pure function, Universe Curator Tier 3 state (`research.universe_tiers`, read-only, only when Tier 3 enforcement is enabled).
**Depended on by:** Future Execution Agent, Audit Logger, Month 1 paper-trading smoke path.
**Related ADRs:** ADR-0006 Redis Streams Event Envelope; ADR-0012 Tiered Universe (Tier 3 membership rule).
**Related architecture sections:** `docs/agents/risk-compliance/risk-officer.md`, `docs/agents/research/universe-curator.md`.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `trading.decision.intent` | Event | Order intents requiring deterministic paper-only pre-trade approval or veto. |
| Repo: `src/shrap/risk_compliance/pre_trade.py` | Code | Existing pure-function `PreTradeChecker` and `RiskPolicy`. |
| PostgreSQL: `research.universe_tiers` | Query | Current Tier 3 membership (Universe Curator's read model, ADR-0012). Read-only, behind a short-TTL in-process cache, and only consulted when Tier 3 enforcement is enabled. |

## Processing

1. Read one or more ADR-0006 envelopes from `trading.decision.intent` using `shrap.events.EventSubscriber`.
2. Extract the intent payload. The payload shape is the existing intent contract from `src/shrap/trading_floor/intent.py` plus Card 2 stub additions; the risk gate currently needs `ticker`, `quantity`, and `mode`.
3. Invoke the existing pure-function `PreTradeChecker(policy).check(intent_payload)`.
4. If the pure check approved and Tier 3 enforcement is enabled, run the Tier 3 membership rule (see below). A Tier 3 veto downgrades the approval; the Tier 3 check runs before the rate guardrails, so a non-tradeable ticker never consumes a rate slot.
5. If still approved, apply the Redis-backed rate guardrails (daily cap, per-symbol cooldown).
6. If approved, publish `risk.intent.approved` with the original intent payload preserved, the requested quantity, and the scaled `approved_quantity` chosen by `PreTradeChecker`.
7. If vetoed, publish `risk.intent.vetoed` with the original intent payload preserved, `reason`/`reason_code`, and the structured reasons returned by `PreTradeChecker`.
8. Publish through `shrap.events.EventPublisher` only. Do not invent a parallel envelope or Redis mechanism.
9. Set the risk event `correlation_id` to the original intent envelope's `event_id` so the immediate causal edge is explicit. The upstream signal remains traceable through the intent event's own `correlation_id`.

### Tier 3 membership rule (ADR-0012)

ADR-0012: "The Pre-Trade Checker gains one deterministic rule: reject any
order for a ticker not currently in Tier 3." Implemented in
`src/shrap/risk_compliance/tier3_membership.py` as `Tier3MembershipGate`,
which reads the Universe Curator's `research.universe_tiers` read model with
a short-TTL in-process cache (default 30 s, `PRE_TRADE_CHECKER_TIER3_CACHE_TTL_SECONDS`)
so the order path does not hit Postgres per order.

- **Flag-gated, default off.** `PRE_TRADE_CHECKER_TIER3_ENFORCEMENT=false` is
  the shipped default because nothing populates `research.universe_tiers`
  yet: no Curator service exists and the launch-list load is gated on
  Mike's DQ-004 lock-in. Enforcing against an empty or missing table would
  reject every order, including the live smoke path. Disabled means the rule
  is skipped entirely, no Postgres connection is opened, and the service logs
  one clear `tier3_enforcement_off` line at startup — permissive by explicit
  human choice, not by accident.
- **Enabled: membership check.** A ticker passes only if its
  `research.universe_tiers` row exists with `tier = 'active'`. Any other
  state — no row, or a Tier 2 watch row (expired or not) — vetoes with
  reason code `TICKER_NOT_IN_TIER3`. The veto event payload carries the
  reason code exactly like existing vetoes.
- **Tier-value literal.** The Curator spec describes the `tier` column
  without pinning a literal for the tradeable tier; this spec fixes it as
  **`active`** (lower-case), matching ADR-0012's "Tier 3 — Active" naming.
  The constant is `TIER3_ACTIVE_TIER` in `tier3_membership.py`; the
  Curator's first implementation card must write this same literal.
- **Enabled + state unavailable: fail closed.** If the membership question
  cannot be answered — table missing, Postgres unreachable, any query
  error — the order is vetoed with the distinct reason code
  `TIER3_STATE_UNAVAILABLE` and the failure is logged loudly. A risk gate
  that fails open under infrastructure failure is not a risk gate. The
  asymmetry is deliberate: flag off = permissive by explicit human choice;
  flag on + broken state = closed. Unavailable outcomes are never cached,
  so recovery is re-checked on the next order.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis: `risk.intent.approved` | Event | Approved intent with scaled quantity and original intent payload. |
| Redis: `risk.intent.vetoed` | Event | Vetoed intent with reason code, reason text, and original intent payload. |

## LangGraph structure

Not used. This is a deterministic Python service. No LLM and no LangGraph are needed for a reproducible risk gate.

## State

The pure-function check has no persistence needs. Stream offsets persist in the Redis consumer group (KI-006). Rate-guardrail counters live in Redis. Tier 3 membership state is owned by the Universe Curator in PostgreSQL `research.universe_tiers`; this agent holds only a short-TTL in-process cache of per-ticker membership answers, rebuilt from Postgres on expiry and empty on every restart. Unavailable-state outcomes are never cached.

## Failure behavior

1. **Containment:** A vetoed event stops the chain before execution. If the agent crashes, it stops consuming and therefore does not approve anything accidentally. Incorrect approvals would propagate to later execution, so the real-money invariant remains hard-coded in `PreTradeChecker` and must also be rechecked by future execution.
2. **Replay safety:** Safe. If the same intent event is processed twice, the same policy and payload produce the same risk decision. Emitting twice is acceptable because the audit table dedupes by risk event `event_id` and the repeated decision is deterministic. The Tier 3 check is deterministic within one cache TTL; across a membership change a replayed intent may decide differently, which is correct — membership is evaluated "currently in Tier 3" (ADR-0012), not as-of the intent.
3. **Degraded operation:** If Redis is down, the gate does not consume or publish; intents remain queued in `trading.decision.intent` until Redis and the service recover. The firm should not execute without a risk decision. If Tier 3 enforcement is enabled and Postgres is down or `research.universe_tiers` is missing, every intent is vetoed with `TIER3_STATE_UNAVAILABLE` until the state store recovers — the gate fails closed, never open.

## Sprint scope

- Month 1 Card 3: Wire-only event-loop wrapper around the existing pure-function `PreTradeChecker`; publish approved/vetoed events with correlation and tests proving the signal-to-risk path.
- Tier 3 membership check (ADR-0012): shipped flag-gated and **off by default**; flipping `PRE_TRADE_CHECKER_TIER3_ENFORCEMENT=true` waits on the Universe Curator's first implementation card populating `research.universe_tiers` (post DQ-004 lock-in). Until then the env-var `allowed_universe` remains the only ticker filter.
- Future cards: Position correlation, exposure limits, kill-switch state, daily-loss limits, execution handoff, and persistent risk decision tables.

## Deployability

Card 5 packages this agent as `shrap-pre-trade-checker` with a `PRE_TRADE_CHECKER_*` environment contract, a Dockerfile, and a Compose service. The service remains paper-only, deterministic, and starts from `0-0` during Month 1 so queued decision intents are replayed on startup.

## Deferred

- Real risk sizing beyond the current max-quantity cap.
- Position and portfolio exposure checks.
- Correlation clustering.
- Persistent risk decision storage.
- Real-money execution approval.

## Open questions

None for Card 3. The architectural choice is explicit: this is an independent long-running agent process, not synchronous Decision Maker coupling.
