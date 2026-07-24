# Universe Curator

**Department:** Research
**LLM tier:** `local-classification` for rationale summarization and profile-staleness
narrative generation only. All tier-state decision logic is deterministic — no
LLM is in the approval path. See `docs/infrastructure/llm-routing.md` and `docs/infrastructure/llm-registry.md`.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Accepted
**Date:** 2026-05-30 (rewritten for ADR-0012, 2026-07-23; first implementation card landed 2026-07-24)
**Author:** Mike White
**Version:** 0.3

> **Honest status:** the Universe Curator's first implementation card has
> landed (`phase1/universe-curator-service`): the `research.universe_tiers`
> and `research.universe_staging` stores, the five tier-transition events, the
> `shrap-universe-promote` approval CLI, the launch-list load, and the daily
> watch-expiry sweep service. Open questions 1–3 below are resolved. What is
> not yet done: deploying the service to the Dell and running the launch load
> in prod, the Pre-Trade Checker's Tier 3 membership check card (now unblocked
> by the read model this card ships), and the Intelligence agents' roster
> switch from env placeholders to Curator state. Elevation intake from the
> funnel and Structural Analysis, the profile-staleness scan, and automated
> eviction candidates remain later cards (see Sprint scope).

## Purpose

Under ADR-0012, the universe is three tiers, not one list. Tier 1
(Discovery) is the market itself — everything the ingest sources see, with
no per-name state and no owner beyond the ingest agents. Tier 2 (Watch) is
the unbounded, evidence-gated set of names elevated out of discovery. Tier 3
(Active) is the hard-capped, tradeable set with full per-name treatment. The
Universe Curator **owns Tier 2 and Tier 3 state** and is the sole publisher
of the four tier-transition events. It holds no Tier 1 state — there is
none to hold.

The lineage matters and is superseded, not erased. ADR-0010 corrected
ADR-0007's derived-only framing: the universe is merged from multiple
approved sources — Framework #1 funnel candidates, Forced-Proxy candidates,
Structural Analysis names, the launch list — with the Curator as maintainer.
ADR-0012 keeps that merged-universe idea and gives it structure: multiple
approved sources still feed the Curator, but membership now has explicit
tiers, evidence gates on entry, Mike's approval on everything tradeable, and
an event-recorded transition for every move between tiers.

Why the firm needs this agent: the audit trail must answer "why is this
name tradeable" the same way it answers "why did the system trade"
(ADR-0012). Without a single owner of tier state, tradeability is implicit
in config files scattered across agents — which is exactly today's interim
condition: the News Analyzer and Filing Processor specs both carry env-var
placeholder rosters with an explicit note to switch to the Curator's Tier 3
state once it exists, and the Pre-Trade Checker's Tier 3 membership check is
a pending card blocked on a data-source decision. The Curator is the agent
that turns tier membership into one queryable, event-audited state.

What this agent cannot do, stated clearly:

- It cannot decide Tier 3 membership. Mike approves **all** Tier 3 changes
  — promotions and evictions. The Curator assembles proposals, annotates
  consequences, and executes Mike's decision. It proposes; it never decides.
- It cannot evaluate whether a name *should* be elevated — that is the
  proposing mechanism's job (funnel, Forced-Proxy framework, Structural
  Analysis, or Mike). The Curator validates evidence, invariants, and
  authority.
- It cannot decide a ticker has alpha — that is Hypothesis Generator +
  Strategy Evaluator.
- It cannot apply tier filters at ingest or discovery time — no agent may
  (ADR-0012: "the tiers bound cost, not curiosity"). Tier filters live only
  where per-name cost is incurred.
- It cannot refresh stale profiles on its own. It flags them; refreshing is
  a Mike-directed or source-directed job.

## Trigger

- **Event:** Subscribes to elevation-proposal streams from approved
  mechanisms as they come to exist:
  - Framework #1 funnel candidates (Infrastructure Mapper — not yet built;
    stream name fixed at that implementation card, conforming to ADR-0006).
  - Forced-Proxy staging (ADR-0011 — **reserved, unwritten**; the mechanism
    is named here because ADR-0012 names it, but nothing can stage through
    it until ADR-0011 exists).
  - Structural Analysis findings (department is month 3-4; no agent specs
    exist yet).
- **Schedule:** Daily watch-expiry sweep over Tier 2. Later scope: daily
  profile-staleness scan over Tier 3.
- **On-demand:** Mike, via the approval CLI — seed a watch entry, stage or
  decide a promotion/eviction, extend or expire a watch entry.

## Cross-references

**Depends on:** approved elevation mechanisms (Infrastructure Mapper when
built; Forced-Proxy per ADR-0011, reserved; Structural Analysis, month 3-4;
Mike seeding — the only mechanism that exists today), Mike (sole approver of
all Tier 3 membership changes), per-ticker profiles under
`docs/universe/<ticker>.md`, Strategy Librarian (consequence annotation),
ADR-0006 event library.
**Depended on by:** Pre-Trade Checker (pending Tier 3 membership check —
blocked on the data-source decision this spec proposes; see open question
1), News Analyzer and Filing Processor (both hold env placeholder rosters
flagged for replacement by Curator Tier 3 state), Hypothesis Generator and
Strategy Evaluator (Tier 3 is the strategy-eligible set), Structural
Analysis (reads Tier 2 + Tier 3 per ADR-0012 — findings on watch names are
promotion evidence), Decision Maker, Risk Officer, Daily Briefing Agent.
**Related ADRs:** ADR-0012 (tiered universe — the authority for this spec),
ADR-0010 (merged universe — superseded framing, retained lineage), ADR-0006
(envelope and stream naming), ADR-0011 (Forced-Proxy — reserved), ADR-0009
(LLM tiers).
**Related architecture sections:** `docs/02-architecture.md` §Research
Department; `docs/universe/README.md` (tier definitions and Tier 3 launch
proposal, DQ-004).

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: funnel candidate stream | Event | Tradable-instrument candidates from Framework #1 graphs. Does not exist yet — Infrastructure Mapper is unbuilt; the v0.1 `research.infra.universe.proposed-*` names are superseded and will be re-fixed at that card under ADR-0006 naming |
| Redis: Forced-Proxy staging stream | Event | Reserved for ADR-0011. Unwritten — named only because ADR-0012 lists the mechanism |
| Redis: Structural Analysis findings stream | Event | Month 3-4. Watch-name findings accumulate on Tier 2 records as promotion evidence |
| CLI: Mike decisions and seeds | Command | Watch seeds, promotion/eviction stagings and decisions, watch extensions (see open question 2 for the mechanism's shape) |
| PostgreSQL: `research.universe_tiers` | Query | Current Tier 2/3 membership (proposed store — open question 1) |
| PostgreSQL: `research.universe_staging` | Query | Pending Tier 3 proposals awaiting Mike |
| Repo: `docs/universe/<ticker>.md` | File read | Per-ticker behavioral profiles; existence is a promotion prerequisite |
| Repo: `docs/universe/README.md` | File read | Tier definitions and the Tier 3 launch proposal (DQ-004) |

## Processing

### Tier 2 entry (watch-added)

1. **Validate the elevation.** The mechanism must be one of the four
   approved kinds — `funnel-candidate`, `forced-proxy` (reserved until
   ADR-0011), `structural-finding`, `mike-seed` — and the payload must
   carry a resolvable `evidence_ref` into its canonical store plus **an
   expiry or a falsifier**. An entry with neither is rejected: the
   expiry/falsifier requirement is the soft cap the Curator enforces —
   watch entries that stop earning attention age out. Malformed or
   unauthorized proposals emit `research.universe-proposal-rejected`.
2. **Dedupe.** If the name is already in Tier 2, the new evidence accrues
   to the existing watch record (ADR-0012's "accumulating structural
   findings") — no transition occurred, so no transition event. If the
   name is already in Tier 3, membership is a no-op; the evidence is
   routed to the name's profile maintenance notes.
3. **Record and publish.** Insert the watch record into
   `research.universe_tiers` and emit `research.universe-watch-added`.

### Watch expiry sweep (daily)

1. Entries past their expiry with no renewal (new recorded evidence, or an
   explicit Mike extension) are expired: state updated, and
   `research.universe-watch-expired` emitted with reason `expired`.
2. Entries whose falsifier has been recorded as observed — by the proposing
   source or by Mike (see open question 4 on who watches falsifiers) — are
   expired immediately with reason `falsified`.

### Tier 3 promotion (proposal → Mike decision)

1. **Assemble the case.** A promotion proposal is staged from a watch
   record — by the Curator surfacing an earned candidate, or by Mike
   directly. Deterministic gate checks before staging:
   - A behavioral profile exists under `docs/universe/<ticker>.md`
     (prerequisite per ADR-0012; see open question 3 for what "exists"
     must mean given today's profile coverage).
   - Cap headroom. Tier 3 is hard-capped at 50 at launch. At cap, the
     proposal must name an eviction candidate — promotion may force
     eviction.
   - Consequence annotation: query the Strategy Librarian for live or
     paper-stage strategies referencing the name (and the eviction
     candidate, if paired); attach the list so Mike sees impact before
     deciding.
2. **Wait for Mike.** The staged proposal sits in
   `research.universe_staging` until Mike decides. There is no auto path
   and no hold-timer path — the v0.1 24-hour auto-add hold is gone.
   Mike approves all Tier 3 membership changes.
3. **On approval:** update `research.universe_tiers`, emit
   `research.universe-promoted`; if an eviction is paired, emit
   `research.universe-evicted` for the evicted name and record the
   eviction criteria on its profile.
4. **On rejection:** resolve the staging row with Mike's note. The watch
   entry remains in Tier 2 with its expiry clock running.

### Tier 3 eviction

1. Eviction criteria are the ADR-0012 three: **profile decay, liquidity
   loss, thesis falsified**. The Curator surfaces eviction candidates with
   evidence (later scope: an automated behavior-divergence scan); Mike
   decides, as with promotion.
2. On approval: emit `research.universe-evicted` and write the eviction
   criteria into the name's profile maintenance notes. Eviction always
   lands in Discovery. If the name still merits attention, a fresh Tier 2
   entry is created with its own evidence and expiry — this keeps the
   watch-entry invariant uniform: every Tier 2 record has evidence and an
   expiry or falsifier, with no grandfathered exceptions.

### Transition event contract

Every transition event carries, inside the ADR-0006 envelope, at minimum:

```
{ticker, source_tier, destination_tier, mechanism, evidence_ref}
```

`mechanism` is one of `funnel-candidate | forced-proxy | structural-finding
| mike-seed`. `evidence_ref` points at the canonical evidence record —
a `research.world_changers` candidate, a structural findings row (future),
an `intelligence.filings` accession, or a Mike-seed note recorded at CLI
time — per the ADR-0006 payload-by-reference rule. This is the contract
that lets the audit trail answer "why is this name tradeable": walk the
name's transition events, dereference the evidence.

### Profile staleness scan (later scope, retained from v0.1)

Daily deterministic scan over Tier 3: flag profiles older than 30 days,
behavioral divergence beyond thresholds, or reference in a kill-confirmed
strategy within 14 days. Emits `research.universe-profile-stale` (renamed
from v0.1's `universe.profile-stale` for ADR-0006 stream-naming
conformance). Thresholds remain uncalibrated guesses, as in v0.1.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `research.universe-watch-added` | Event | Tier 1 → 2. Elevation with mechanism and evidence reference |
| Redis stream: `research.universe-watch-expired` | Event | Tier 2 → 1. Reason `expired` or `falsified` |
| Redis stream: `research.universe-promoted` | Event | Tier 2 → 3. Mike-approved, always |
| Redis stream: `research.universe-evicted` | Event | Tier 3 → 1. Mike-approved, always; criteria recorded on the profile |
| Redis stream: `research.universe-proposal-rejected` | Event | Malformed or unauthorized elevations, and Mike rejections. A spec-level addition beyond ADR-0012's four transition events: the deny path must be as auditable as the allow path (the Tech Watcher's kill-graveyard precedent) |
| Redis stream: `research.universe-profile-stale` | Event | Later scope; deterministic staleness flags on Tier 3 profiles |
| PostgreSQL: `research.universe_tiers` | Insert / update | Current Tier 2/3 membership — the proposed read model (open question 1) |
| PostgreSQL: `research.universe_staging` | Insert / update | Pending Tier 3 proposals and their dispositions |
| Repo: `docs/universe/<ticker>.md` | File write | Eviction criteria and evidence notes into profile maintenance sections |

Every event carries the ADR-0006 envelope. The four transition events and
their payload fields are fixed by ADR-0012; this spec may not narrow them.

## LangGraph structure

Not used. Deterministic event consumer plus a decision CLI and scheduled
sweeps. The optional rationale-text LLM call (`local-classification`) is a
side output on events, never in the decision path.

## State

| What | Store | Notes |
|---|---|---|
| Current tier membership | PostgreSQL `research.universe_tiers` | Proposed (open question 1). One row per name currently in Tier 2 or 3: ticker, CIK, tier, mechanism, `evidence_ref`, `entered_at`, expiry/falsifier (Tier 2), profile path (Tier 3). CIK is carried because the Filing Processor's roster read requires it |
| Staged Tier 3 proposals | PostgreSQL `research.universe_staging` | Pending Mike decisions; resolved rows retain their disposition and note |
| Transition history | Event streams → `ops.audit_events` | The four transition events **are** the history. The Audit Logger's append-only capture is the durable query surface; the Curator keeps no separate history table. Rebuilding `research.universe_tiers` = replaying the transition events |
| Eviction criteria, evidence notes | Repo `docs/universe/<ticker>.md` | Human-readable side of the audit trail; the profile is where "why did this name leave" lives |

## Failure behavior

1. **Containment.** The dangerous direction is a polluted Tier 3: a name
   becoming order-eligible without Mike's approval. The design blocks it
   structurally — Tier 3 mutations happen only in the Mike-decision path,
   there is no auto-promotion, and the Pre-Trade Checker independently
   rejects any ticker not in Tier 3 once its check ships. A Curator bug
   therefore fails toward the safe direction: wrongly expired watch entries
   or a frozen tier state shrink the firm's attention, which is annoying,
   not dangerous. Bias fail-closed throughout: when in doubt, do not
   record the elevation, do not stage the promotion.
2. **Replay safety.** Safe. Every state transition is paired with a
   published event; idempotency on `(event_id)` prevents double-apply of
   re-delivered events; replaying the transition-event history
   reconstructs `research.universe_tiers` exactly.
3. **Degraded operation.** The firm runs indefinitely without the Curator
   at the cost of frozen tiers. Discovery continues (Tier 1 has no
   Curator dependency), the Pre-Trade Checker keeps enforcing the
   last-known Tier 3, and Intelligence agents keep their last roster.
   What stops: new watch entries queue unrecorded, and the expiry sweep
   stops enforcing the soft cap — watch entries overstay, a bounded cost
   creep. If the Curator is down more than 7 days, Mike should pause
   elevation-proposal sources to prevent intake buildup on recovery.

## Sprint scope

Staged realistically — no Curator service exists today, and most elevation
sources are themselves unbuilt:

- **First implementation card** (unscheduled; after DQ-004 lock-in):
  `research.universe_tiers` + `research.universe_staging` stores, the four
  transition events, and the Mike approval CLI (seed / promote / evict /
  extend / expire). Load the locked Tier 3 launch list through the event
  path itself — one `research.universe-promoted` per name, mechanism
  `mike-seed`, `evidence_ref` pointing at the DQ-004 lock-in decision — so
  day-one membership is as audit-answerable as everything after it.
- **Immediately unblocked downstream:** the Pre-Trade Checker Tier 3
  membership check card (pending open question 1's ruling), and the News
  Analyzer / Filing Processor roster switch from env placeholders to
  Curator state.
- **Later cards:** elevation intake from the funnel (needs Infrastructure
  Mapper) and Structural Analysis (month 3-4); the daily watch-expiry
  sweep; the profile staleness scan; automated behavior-divergence
  eviction candidates. Automation follows the state store, not the other
  way around.

## Deferred

- Cross-asset universe (options, futures, direct crypto) — equities and
  ETFs only for the sprint.
- Automatic profile refresh — flagging stays with the Curator; refreshing
  stays a Mike-directed or source-directed job.
- Priority weights within Tier 3 — Risk Officer handles sizing; the
  Curator tracks membership only.
- Any direct demotion of strategies on eviction — the Curator emits the
  universe-level event; the Strategy Evaluator's thesis-broken handling
  owns strategy consequences.
- Tier 2 news/filing coverage — tracked as open questions in the News
  Analyzer and Filing Processor specs, decided with this agent's cards.
- Forced-Proxy staging mechanics — reserved until ADR-0011 is written.

## Open questions

- **Tier 3 store shape for the Pre-Trade Checker:** **RESOLVED 2026-07-23
  (accepted via PR #70).** PostgreSQL `research.universe_tiers` is the
  current-membership read model — queried by the Pre-Trade Checker with
  `SELECT tier FROM research.universe_tiers WHERE ticker = $1` behind a
  short-TTL in-process cache, tradeable literal `active` — with the
  transition-event stream (durably captured in `ops.audit_events`) as the
  audit history. The alternatives (a Redis-derived view inside the gate, a
  config snapshot) were rejected: the Postgres read model is one source of
  truth, the Intelligence rosters need the same read anyway, and the event
  stream already carries the history. The Curator's first implementation card
  writes exactly this table and literal; the gate is a read-only consumer.
- **Mike-approval mechanism shape:** **RESOLVED 2026-07-23 (this card).**
  A CLI in the Curator container — `shrap-universe-promote`, on the
  `shrap-tech-watcher-promote` precedent (PR #54) — with subcommands seed /
  stage / approve / reject / extend / expire / load-launch-list. Every Tier 3
  mutation happens only through an explicit CLI decision; there is no auto
  path. A review-page-driven flow (`shrap-tech-watcher-review` precedent)
  remains a possible later addition, not a blocker.
- **Behavioral-profile prerequisite vs. today's coverage:** **RESOLVED
  2026-07-23 (Mike).** The launch-list load grandfathers the 44 names without
  a seed profile straight into Tier 3; lock-in does not wait on profiles
  (backfill is pending). The six seed-profiled names (SPY, QQQ, TSLA, NVDA,
  AAPL, LMT) carry their `profile_path`; the other 44 carry `NULL`, which is
  the grandfathered marker. The profile-exists prerequisite applies **only to
  future Tier 2 → 3 promotions**, not to the launch load — the CLI's `stage`
  gate enforces it there. The operational bar for "profile exists" is
  file-existence of `docs/universe/<ticker>.md` at stage time; a stricter
  minimum-quality bar is deferred (still Mike's call, no longer blocking).
- **Falsifier observation ownership:** expiry dates are deterministic,
  but who detects that a Tier 2 falsifier *fired* — the proposing
  source's re-scan (the Tech Watcher precedent for kill criteria), the
  Curator, or Mike? Spec default: the proposing source owns its
  falsifiers and reports observations; the Curator acts on them. Blocks:
  completeness of the expiry sweep. Owner: Mike + proposing-source
  owners, as each source's card lands.
- **Default watch expiry duration:** entries with a falsifier but no
  date need a default TTL; 90 days is a guess with no calibration
  behind it. Blocks: nothing now. Owner: Mike, after the first month of
  live watch entries.
