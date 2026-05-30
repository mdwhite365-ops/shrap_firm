# Universe Curator

**Department:** Research
**LLM tier:** `local-classification` for rationale summarization and profile-staleness
narrative generation only. All add/remove decision logic is deterministic — no
LLM is in the approval path. See `docs/infrastructure/llm-routing.md` and `docs/infrastructure/llm-registry.md`.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-05-30
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

Under ADR-0010, the firm's tradable universe is a **merged universe from
multiple contributing research sources**, not a derived-only graph from
Infrastructure Mapper. The Universe Curator is the agent that turns approved
universe proposals into an actual, version-controlled active universe state —
gated by Mike's approval policy and accompanied by per-ticker profile
maintenance.

This agent maintains the launch Universe plus additions and removals proposed
by approved research sources. Framework #1 Infrastructure Mapper graph deltas
are one source. Future forced-proxy agents, Structural Analysis watch-list
updates, and later ADR-approved thesis frameworks may also propose universe
changes. Curator applies source-aware policy, stages or executes the change,
and emits events the rest of the firm subscribes to.

Why it exists separately from contributing research sources: separation of
concerns. Each research framework's job is to discover candidates under its
own mechanism. The Universe Curator's job is to be the single source of truth
for "what is Shrap allowed to trade right now," to enforce the approval
policy, and to keep per-ticker profiles fresh. Conflating proposal generation
with active-universe state would mean every research discovery edit touches
trading state, which is exactly the failure mode Mike's approval policy is
meant to prevent.

What this agent cannot do, stated clearly:

- It cannot evaluate whether a ticker *should* be proposed by a thesis
  framework — that is the proposing source's job. Curator validates payloads,
  source authority, liquidity/tradability policy, and approval requirements.
- It cannot decide a ticker has alpha — that is Hypothesis Generator +
  Strategy Evaluator.
- It cannot auto-approve a removal under any circumstance. Removals always
  require Mike. This is intentional: removing a ticker can halt active
  paper-stage strategies, and that consequence demands a human.
- It cannot refresh stale profiles on its own. It flags them; refreshing is
  a Mike-directed or scheduled job that goes back through Infra Mapper.

## Trigger

- **Schedule:**
  - Daily 06:00 ET: scan all active tickers for profile staleness; emit
    `universe.profile-stale` events for those that match staleness rules.
  - Daily 08:00 ET: scan the staging table for high-confidence adds whose
    24h hold has elapsed; auto-add per policy.
- **Event:** Subscribes to:
  - `research.infra.universe.proposed-add` from Infrastructure Mapper.
  - `research.infra.universe.proposed-remove` from Infrastructure Mapper.
  - Future ADR-approved source streams such as forced-proxy or structural-analysis universe proposals.
  - `mike.universe.decision` (Mike's manual approve/reject for staged items).
- **On-demand:** Mike-initiated force-add, force-remove, or
  force-mark-stale, each requiring Mike's identifier in the envelope.

## Cross-references

**Depends on:** approved universe-proposal sources (Infrastructure Mapper first, future sources by ADR),
Mike (sole approver for low-confidence adds and any remove), per-ticker
profile docs under `docs/universe/<ticker>.md`.
**Depended on by:** Hypothesis Generator (filters proposals against
`universe.active`), Strategy Evaluator (anchor-freshness check uses active
membership), Decision Maker, Risk Officer, Daily Briefing Agent.
**Related ADRs:** ADR-0006 (envelope), ADR-0007 (Research thesis).
**Related architecture sections:** `docs/02-architecture.md` §Research
Department, §Universe lifecycle.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `research.infra.universe.proposed-add` | Event | New ticker proposal: `{ticker, graph_id, layer_id, confidence, evidence_refs, proposer_run_id}` |
| Redis: `research.infra.universe.proposed-remove` | Event | Removal proposal: `{ticker, reason, graph_id, evidence_refs}`. Reason is one of `node-failed`, `graph-deprecated`, `behavior-divergence` |
| Redis: `mike.universe.decision` | Event | `{staging_id, decision: approve|reject, note}` |
| PostgreSQL: `universe.active` | Query | Current active tradable set |
| PostgreSQL: `universe.staging` | Query | Pending additions/removals awaiting Mike or hold elapse |
| PostgreSQL: `universe.history` | Query | Append-only audit trail of all transitions |
| Repo: `docs/universe/<ticker>.md` | File read | Per-ticker profile (last-refreshed date, behavior summary, graph memberships) |
| PostgreSQL: `market_data.ticker_behavior` | Query | Recent volatility, ADV, correlation profile (used for divergence checks) |

## Processing

### Proposed-add handling

1. **Validate payload.** Required fields present; `proposer_run_id` exists in
   the proposing source's run log; confidence is a probability in [0, 1]; source-specific identifiers are live. Reject malformed events with `universe.proposal.rejected`.
2. **Dedupe.** If the ticker is already in `universe.active` for the same
   `(graph_id, layer_id)`, drop as no-op. If it is active under a different
   graph/layer, record the additional membership and emit
   `universe.membership.expanded` — no staging needed.
3. **Apply policy.**
   - Confidence > 0.90: stage with `auto_add_at = now + 24h` and emit
     `universe.staged` with `policy=auto-pending-hold`. During the 24h hold,
     a Mike rejection or any subsequent contradictory event from Infra
     Mapper cancels the staging.
   - Confidence ≤ 0.90: stage with `requires_mike=true` and emit
     `universe.staged` with `policy=mike-required`. No hold timer.
4. **Daily hold sweep (08:00 ET).** For each row with `auto_add_at` in the
   past and no cancellation: move to `universe.active`, write to
   `universe.history`, emit `universe.added`. The Implementation Agent may
   **not** mutate `universe.active` or `universe.staging` directly without
   Mike's explicit approval — only this agent's processing path can.

### Proposed-remove handling

1. **Validate payload** as above.
2. **Stage as mike-required, always.** No removal is ever auto-approved.
   Emit `universe.staged` with `policy=mike-required` and `kind=remove`.
3. **Annotate consequence.** Query Strategy Librarian for all live or
   paper-stage strategies referencing this ticker; attach the list to the
   staging row and to the event payload. Mike sees the impact before
   approving.
4. **On Mike approval:** move to removed state, emit `universe.removed` with
   the consequence list. Strategy Evaluator's existing thesis-broken handler
   is responsible for demoting affected strategies — Curator does not demote
   strategies directly, it only emits the universe-level event.
5. **On Mike rejection:** drop staging row, emit
   `universe.proposal.rejected` with note.

### Mike-decision handling

1. Look up the staging row by `staging_id`. If absent or already resolved,
   emit `universe.decision.ignored` with reason.
2. Apply the decision atomically: either promote staging to active (add) /
   removed (remove), or drop staging.
3. Emit the appropriate terminal event (`universe.added`, `universe.removed`,
   or `universe.proposal.rejected`).

### Profile staleness scan (daily 06:00 ET)

For each ticker in `universe.active`, mark stale if **any** of:

- `last_refreshed_at` more than 30 days ago.
- `market_data.ticker_behavior` shows a >50% change in 30-day realized vol,
  or a >0.3 absolute change in correlation to its primary graph layer
  benchmark, since the profile was last written.
- The ticker has been referenced in a kill-confirmed strategy in the last
  14 days (the profile may have contributed to the bad proposal).

For each stale ticker, emit `universe.profile-stale` with the specific
reasons matched. The LLM tier (Local) is used here only to produce a
human-readable one-line rationale per event. The match itself is
deterministic.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `universe.staged` | Event | `{staging_id, ticker, kind: add|remove, policy, rationale, consequence}` |
| Redis stream: `universe.added` | Event | `{ticker, graph_id, layer_id, source: auto|mike}` |
| Redis stream: `universe.removed` | Event | `{ticker, reason, affected_strategies}` |
| Redis stream: `universe.membership.expanded` | Event | Already-active ticker gained an additional graph membership |
| Redis stream: `universe.profile-stale` | Event | `{ticker, reasons, last_refreshed_at}` |
| Redis stream: `universe.proposal.rejected` | Event | Malformed, contradicted, or Mike-rejected proposals |
| Redis stream: `universe.decision.ignored` | Event | Mike decision for an unknown or already-resolved staging row |
| PostgreSQL: `universe.active` | Insert / delete | Single source of truth for current tradable set |
| PostgreSQL: `universe.staging` | Insert / update / delete | Pending proposals |
| PostgreSQL: `universe.history` | Append-only insert | Full audit trail of every transition with envelope reference |

Every event carries the ADR-0006 envelope.

## LangGraph structure

Not used. Deterministic policy logic implemented as a Python event consumer
with three handlers (add, remove, decision) plus two scheduled scans
(staleness, hold-sweep). The optional rationale-text LLM call is a side
output, not in the decision path.

## State

| What | Store | Notes |
|---|---|---|
| Active universe | PostgreSQL `universe.active` | Keyed by ticker; multi-membership tracked in `universe.memberships` |
| Pending proposals | PostgreSQL `universe.staging` | Keyed by `staging_id`; resolved rows are moved to history |
| Audit trail | PostgreSQL `universe.history` | Append-only; every transition references the triggering event ID |
| Per-ticker profile refresh state | Repo `docs/universe/<ticker>.md` + `universe.profile_state` table | Profile doc is source of truth; table caches `last_refreshed_at` for fast scans |

## Failure behavior

1. **Containment.** Curator bugs split into two categories. **Failing open**
   (auto-adding a ticker that should have required Mike) widens the
   actionable universe and can let Hypothesis Generator propose strategies
   on it — but the Evaluator still gates promotion and real-money is
   hard-blocked. **Failing closed** (rejecting valid adds, or failing to
   stage removes) shrinks or staleness-pollutes the universe — annoying,
   not dangerous. Bias toward fail-closed: when in doubt, do not auto-add.
2. **Replay safety.** Safe. All state transitions are event-sourced through
   `universe.history`. Idempotency key on `(event_id, kind)` prevents
   double-application of re-delivered events. Replaying the event log
   reconstructs current `universe.active`.
3. **Degraded operation.** The firm can run without Curator indefinitely
   at the cost of a frozen universe. Hypothesis Generator continues to
   propose against the last-known active set; Evaluator continues to gate;
   no new tickers enter, no stale flags are raised. If Curator is down
   >7 days, Mike should manually freeze Infra Mapper's proposal stream to
   prevent staging-table buildup on recovery.

## Sprint scope

- Month 2: Add-proposal handling (both auto and mike-required paths),
  `universe.active` source of truth, basic audit trail. Removal handling
  staged-mike-required only.
- Month 3: Profile staleness scan with the three documented match rules.
  Consequence annotation on remove proposals (Strategy Librarian
  integration). 24h hold sweep.
- Month 4: Behavior-divergence detection on `market_data.ticker_behavior`.
  Rationale-text LLM side output. Replay-from-log self-check.

## Deferred

- Cross-asset universe (options, futures, crypto) — equities only for sprint.
- Automatic profile refresh — refresh remains a Mike-directed or source-directed
  job, depending on which research framework owns the ticker rationale.
- Ticker tiering / priority weights within the active set — Risk Officer
  handles sizing, Curator only tracks membership.
- Any direct demotion of strategies — that authority stays with the
  Strategy Evaluator.

## Open questions

- **Auto-add confidence threshold (>0.90):** Default guess. Blocks:
  universe-growth rate calibration. Owner: Mike, after first 20 proposals.
- **24h hold duration:** Long enough for Mike to veto in a normal workday,
  short enough that high-confidence graph discoveries propagate quickly.
  Blocks: nothing immediate. Owner: Mike, after first month.
- **Staleness threshold (30 days, 50% vol delta, 0.3 correlation delta):**
  All three are guesses without empirical calibration. Blocks: false-alarm
  rate on `universe.profile-stale`. Owner: Mike + Infra Mapper owner.
- **What happens if Infra Mapper proposes adding a ticker already in
  `universe.removed` history with a Mike-rejection note?** Currently
  re-stages as mike-required regardless of confidence. May want a cooldown.
  Blocks: ping-pong between Mapper and Curator. Owner: Mike.
- **Should a contradictory `proposed-remove` arriving during a 24h
  auto-add hold cancel the add, or stage a remove on top?** Currently
  cancels the add (treat as Mapper retracting itself). Blocks: edge-case
  semantics. Owner: Mike + Infra Mapper owner.
