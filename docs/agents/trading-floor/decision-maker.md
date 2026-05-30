# Decision Maker

**Department:** Trading Floor
**LLM tier:** Cloud (Claude Sonnet 4.6) for synthesis under uncertainty; falls back to
deterministic-only mode when LLM tier is degraded. Most actual entries should be
deterministic rule firings — the LLM is for narrative synthesis and edge-case veto, not
primary signal generation. See `docs/infrastructure/llm-routing.md`.
**Status:** Draft
**Date:** 2026-05-29
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Decision Maker is the last station before a trade intent leaves the Trading Floor and
enters the risk-checked execution path. Its job is to synthesize: (a) the active
regime-conditional strategy signals, (b) the structural-analysis biases per ticker, (c)
the intelligence findings (news, sentiment, filings), and (d) the current regime label
into a single trade decision per opportunity. It produces an order intent — not an
order. The Risk Officer can veto, and the Execution Agent translates the intent into
NautilusTrader orders.

The Decision Maker exists because individual strategy signals are noisy and the firm's
edge — if any — lives in *confluence*: a sweep detector firing on a ticker with a
structural short bias during a regime where fade-the-bounce works. No single signal is
trusted. The Decision Maker enforces confluence and demands a written justification for
every entry intent.

**Real-money execution is out of scope for the entire sprint.** All Decision Maker
outputs route to NautilusTrader paper. Real-money execution requires a post-sprint ADR
and Mike's signed approval. This is enforced in code by the Execution Agent rejecting
any order whose `mode` field is not `paper`, but it is restated here so anyone reading
the spec understands the constraint.

What this agent cannot do:
- It cannot produce calibrated probabilities of trade success. Confidence numbers it
  emits are *internal scoring*, not posteriors. Every intent carries the caveat in its
  payload.
- It cannot detect when its inputs are jointly stale. It trusts the freshness flags from
  upstream agents. If those flags are wrong, the Decision Maker is wrong.
- It cannot prevent a correlated drawdown across multiple intents — that is the Risk
  Officer's job (correlation-aware sizing and limits).

## Trigger

- **Schedule:** Continuous during market hours. The agent runs an event loop over its
  input streams.
- **Event:** Subscribes to `trading.strategy.signal` (every active strategy emits these),
  `intel.alert.*` (high-priority news/sentiment), `structural.bias.updated`,
  `research.regime.changed`, `risk.alert` (immediate halt input).
- **On-demand:** Mike-initiated `trading.decision.request` for a named ticker.

## Cross-references

**Depends on:** Regime Classifier, Regime Router (which strategies are active), every
active strategy implementation, Watch List Curator and Filing Deep Reader (structural
biases), News Analyzer / Sentiment Monitor (intelligence inputs), Risk Officer (veto
channel), Sweep Detector.
**Depended on by:** Risk Officer (pre-trade check), Execution Agent (consumer), Daily
Briefing, Audit Logger.
**Related ADRs:** ADR-0006 (envelope), forthcoming ADR on no-real-money invariant.
**Related architecture sections:** `docs/02-architecture.md` §Trading Floor, §Decision
synthesis.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `trading.strategy.signal` | Event | Signals from each active strategy, including strategy_id, direction, urgency, expiry |
| Redis: `research.regime.tick` | Event | Current regime label and confidence |
| Redis: `structural.bias.updated` | Event | Per-ticker structural lean: long/neutral/short with reason |
| Redis: `intel.alert.high` | Event | High-priority news/sentiment alerts. EXTREME-classified alerts force a hard skip for the affected ticker. |
| Redis: `risk.alert` | Event | Risk Officer alerts. `kill_switch` events halt all decisions immediately. |
| PostgreSQL: `trading.positions` | Query | Current open positions per ticker |
| PostgreSQL: `research.strategies` (active set) | Query | Active strategy specs including `regime_fit`/`regime_kill` |
| Repo: `docs/trading/decision-policy.md` | File read | Authoritative confluence rules — versioned |

## Processing

1. **Pre-flight.** Check `risk.alert` for an open kill switch. If set, drop all intents
   and emit `trading.decision.halted`. Check market open + ticker tradeable.
2. **Gather context per candidate.** For each incoming strategy signal: assemble the
   {regime, signal, structural bias, recent intel, open position} tuple for the affected
   ticker. If any component is stale beyond its tolerance, downgrade the candidate.
3. **Apply confluence policy (deterministic).** Per the policy file: require at least the
   documented minimum confluence (e.g. signal aligned with structural bias, or signal in
   a regime tagged for that strategy archetype). Most intents are produced by this
   deterministic path. Boring beats clever.
4. **LLM synthesis (only for borderline cases).** If the deterministic path produces a
   "marginal" classification (one component conflicts, or intel is mixed), invoke the LLM
   to produce a written synthesis with explicit pros/cons. The LLM cannot create a
   *new* intent; it can only flip a marginal candidate to "intent" or "skip" and must
   include the reasoning in the audit record. If the LLM tier is degraded, marginal
   candidates default to `skip`.
5. **EXTREME-news block.** If the affected ticker has an open EXTREME intelligence alert
   (e.g. unscheduled halt, major fraud allegation), hard skip regardless of signals. This
   is the lineage from Mike's prior Shrap; it stays.
6. **Build intent.** Construct the order intent: `{ticker, side, size_hint, urgency,
   strategy_ids, regime_label, structural_bias, intel_refs, confluence_score,
   justification_text, expiry, mode=paper}`. The `size_hint` is advisory; the Risk
   Officer sets the actual size.
7. **Emit.** Publish to `trading.decision.intent`. Audit-log every input snapshot used
   so the decision is reproducible.

Mike's communication norm — "help me be right, not happy with my responses" — applies to
this agent more than any other. Decisions must include their counter-arguments, not hide
them. The justification field is required and is checked for presence of a
"why this might be wrong" line.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `trading.decision.intent` | Event | Order intent for Risk Officer pre-check |
| Redis stream: `trading.decision.skipped` | Event | Skips and reasons (high-volume; payload-by-reference) |
| Redis stream: `trading.decision.halted` | Event | Emitted when kill switch is active |
| PostgreSQL: `trading.decisions` | Append-only insert | Full input snapshot + intent + justification |

Every event carries the ADR-0006 envelope. The `trading.decisions` table is the
forensic substrate; combined with the strategy signal log and the regime history, any
intent can be fully reconstructed.

## LangGraph structure

**Nodes:**
- `pre-flight` — kill-switch and market checks
- `gather` — fan-in input context
- `deterministic-policy` — confluence rule evaluation
- `llm-synthesis` — borderline-only
- `extreme-block` — hard skip on EXTREME alerts
- `emit` — publish + audit-log

**Key edges:**
- `pre-flight` → `gather` → `deterministic-policy` → `extreme-block` → `emit`
- `deterministic-policy` → `llm-synthesis` → `emit` (only on marginal classification)

## State

| What | Store | Notes |
|---|---|---|
| Per-ticker debounce / cooldown after a skipped or filled intent | Redis | TTL e.g. 90s to avoid spam |
| Recent EXTREME alert flags | Redis hash | Mirror of `intel.alert.high` filtered to EXTREME |
| Decision history | PostgreSQL | Append-only, source of truth |

## Failure behavior

1. **Containment.** A wrong intent does not move money — Risk Officer can veto, Execution
   Agent rejects non-paper modes, and the sprint forbids real money entirely. The
   realistic blast radius is wasted paper-trade tracking and noisy analytics.
2. **Replay safety.** Safe for the deterministic path — inputs are recorded, decisions
   are reproducible. The LLM-synthesis path is non-deterministic; replays use the
   recorded LLM output, not a fresh call. Idempotency on intent_id (ULID).
3. **Degraded operation.** The firm can trade (paper) with the LLM-synthesis path
   disabled — marginal candidates simply default to skip. The firm cannot trade without
   the Decision Maker; without it, no intents reach the Risk Officer and no orders flow.
   For longer than 1 trading hour of downtime, Mike should manually pause the active
   strategies.

## Sprint scope

- Month 2: Deterministic confluence policy + EXTREME block + paper-only emit. No LLM
  synthesis yet.
- Month 3: LLM synthesis for marginals with full audit. Structural bias integration.
- Month 4: Tighter confluence policies tuned from live-paper observation.

## Deferred

- Multi-leg / portfolio-level decisions — single-ticker only in sprint.
- Pairs / spread trades.
- Options.
- Any real-money execution. Post-sprint, separate ADR.

## Open questions

- **Confluence policy minimums:** The first cut is "signal + (structural OR regime)".
  Blocks: real intent emission. Owner: Mike after first 2 weeks of paper signals.
- **LLM synthesis budget:** Per-day cap on LLM-borderline calls? Blocks: Cost Monitor
  budget setting. Owner: Mike.
- **Should the Decision Maker ever overrule a strategy's exit signal?** Current spec:
  no — exits are sacred. Blocks: agent behavior on conflicting exit + open-position
  signals. Owner: Mike.
