# Strategy Librarian

**Department:** Research
**LLM tier:** `no-llm` for all registry operations — lifecycle transitions are
deterministic state-machine moves and must never depend on model output. The
architecture assigns a `local-classification` tier for future registry
maintenance conveniences (summarizing a strategy's history for a briefing);
none of that is in sprint scope and none of it may influence lifecycle state.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-07-15
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Strategy Librarian owns the strategy registry — the single system of
record for every strategy the firm has ever considered, and the lifecycle
state of each. Without it, the middle loop has no memory: the Hypothesis
Generator cannot check prior art, the Evaluator has nowhere to record
verdicts, the Trading Floor has no authoritative answer to "which strategies
are active right now," and retrospectives cannot answer "why did we promote
this strategy then."

The registry is two PostgreSQL tables (implemented in
`src/shrap/research/strategy_registry.py`):

- `research.strategies` — one row per strategy: full proposal spec, anchor,
  tickers, kill criteria, regime sizing modifiers, and current lifecycle
  status.
- `research.strategy_transitions` — append-only: every lifecycle decision,
  with the reasoning at decision time, the trigger kind, and a reference to
  the triggering evaluation or event. Never edited, never deleted
  (architecture §10, append-only records).

The lifecycle state machine is enforced in the repository layer, not by
convention. Statuses: `hypothesis` → `paper` → `small-size-paper` →
`live-paper` along the promotion path; `kill-review` and `kill-review-mike`
as demotion holding states; `killed` and `retired` terminal. The `real` stage
is deliberately unrepresentable — real-money execution is post-sprint and
requires its own ADR, so the registry cannot store it.

What this agent cannot do, stated clearly:

- It cannot decide promotions or kills. Verdicts come from the Strategy
  Evaluator; graceful retirements come from Mike. The Librarian records
  decisions and publishes them — it never originates them.
- It cannot modify strategy code. Strategy modules live under `strategies/`
  in the repo and are referenced by `code_ref`; changing them is PR-gated.

## Trigger

- **Event:** Subscribes to `research.strategy.verdict` and
  `research.strategy.killed` from the Strategy Evaluator, and applies the
  corresponding registry transition (service card, not yet implemented).
- **On-demand:** Mike-initiated registration or retirement through the
  repository interface.

## Cross-references

**Depends on:** Strategy Evaluator (verdict events), Hypothesis Generator
(proposals to register).
**Depended on by:** Hypothesis Generator (prior-art lookup), Strategy
Evaluator (reads specs at status=`hypothesis`), Regime Router / Decision
Maker (consume `research.strategy.promoted`), Reporting (lifecycle history).
**Related ADRs:** ADR-0006 (envelope), ADR-0007 (Research thesis).
**Related architecture sections:** `docs/02-architecture.md` §Research
Department, §Strategy lifecycle, §10 (append-only records).

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `research.strategy.verdict` | Event | Evaluator verdicts to apply as transitions |
| Redis: `research.strategy.killed` | Event | Kill verdicts (including kill-review confirmations) |
| PostgreSQL: `research.strategies` | Query | Current registry state |
| PostgreSQL: `research.strategy_transitions` | Query | Lifecycle history (false-alarm restore reads prior stage from here) |

## Processing

1. Receive a verdict or lifecycle request naming a `strategy_id` and target
   status.
2. Apply it through `PostgresStrategyRegistry.transition()`, which locks the
   row, validates the move against `ALLOWED_TRANSITIONS`, updates status, and
   appends the transition record with reason/trigger/actor — atomically.
3. Publish the lifecycle event for the transition:
   `research.strategy.registered`, `.promoted`, `.demoted`, `.killed`, or
   `.retired` (mapping in `stream_for_transition()`), carrying the ADR-0006
   envelope with `transition_event_payload()`.
4. Invalid transitions are rejected loudly (`InvalidTransitionError`) and
   never partially applied. Re-delivered registrations are idempotent on
   `strategy_id`.

## Outputs

| Destination | Type | Description |
|---|---|---|
| PostgreSQL: `research.strategies` | Update | Status transitions via the repository only |
| PostgreSQL: `research.strategy_transitions` | Append-only insert | Every lifecycle decision with reasoning |
| Redis: `research.strategy.registered` | Event | New strategy entered the funnel |
| Redis: `research.strategy.promoted` | Event | Stage advance on the promotion path (consumed by Regime Router) |
| Redis: `research.strategy.demoted` | Event | Entry into kill-review states |
| Redis: `research.strategy.killed` | Event | Terminal kill |
| Redis: `research.strategy.retired` | Event | Graceful terminal retirement |

## LangGraph structure

Not used. The Librarian is a deterministic event-to-transition translator.

## State

| What | Store | Notes |
|---|---|---|
| Strategy registry | PostgreSQL `research.strategies` | One row per strategy, current status |
| Lifecycle history | PostgreSQL `research.strategy_transitions` | Append-only, keyed by ULID |

## Failure behavior

1. **Containment.** A wrong transition corrupts lifecycle state, which is why
   transitions are state-machine-validated and append-only logged — a bad
   move is visible and reversible through a compensating transition, and the
   history of what happened survives. The Librarian cannot move money; the
   worst case is the Trading Floor activating/deactivating the wrong strategy
   set, bounded by the Pre-Trade Checker and rate guardrails downstream.
2. **Replay safety.** Registration is idempotent on `strategy_id`.
   Transition application on event replay is guarded by `expected_from`: a
   re-delivered verdict whose transition already applied fails the
   optimistic check and is skipped. Row locks make concurrent writers safe.
3. **Degraded operation.** The firm runs without the Librarian service
   indefinitely at current scale — verdict events queue in Redis Streams
   (consumer groups persist offsets) and apply on restart. No promotions or
   kills land while it is down, which fails closed: no strategy changes
   state, nothing new activates.

## Sprint scope

- Month 3 (this card): registry schema, lifecycle state machine, repository
  interface, lifecycle event topics and payload builders. No deployable
  service.
- Month 3 (next card): deployable Librarian service consuming Evaluator
  verdict events and publishing lifecycle events, consumer-group discipline,
  compose service.

## Deferred

- Qdrant `strategy_corpus` indexing for prior-art semantic lookup (Hypothesis
  Generator Month 3 scope; Postgres queries suffice until then).
- Registry-backed strategy code loading on the Trading Floor (`code_ref` is
  recorded but nothing consumes it yet).
- Any LLM-assisted registry maintenance.

## Open questions

- **Writer boundary:** Do the Hypothesis Generator and Evaluator write to the
  registry directly through the repository (current default — the state
  machine is enforced in the library either way), or must every write flow
  through the Librarian service once it exists? Blocks: Evaluator
  implementation card. Owner: Mike.
- **False-alarm restore target:** `kill-review` → any active stage is
  allowed by the state machine; restoring to the *prior* stage specifically
  is caller discipline using the transition log. Should the repository
  enforce it? Blocks: kill-review pipeline card. Owner: Mike.
- **`retired` vs `killed`:** `retired` is reserved for graceful,
  Mike-initiated wind-downs of strategies that were not killed for cause.
  Confirm this distinction is worth carrying, or collapse to `killed` with
  reason codes. Blocks: nothing yet. Owner: Mike.
