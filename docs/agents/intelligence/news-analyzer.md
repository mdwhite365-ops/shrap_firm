# News Analyzer

**Department:** Intelligence
**LLM tier:** `local-classification` for bulk item scoring; `cloud-default`
escalation for items scored material (per `docs/infrastructure/llm-routing.md` —
bulk summarization local, high-impact events escalate).
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-07-19
**Author:** Mike White

> **Seed-deployment routing:** same ruling as the Tech Watcher (2026-07-15/17)
> — the firm runs local-only until cloud API billing exists, so in the
> deployed seed both tiers env-route to Ollama on the Dell. The escalation
> path is wired but lands on the same local model until the compose env
> changes. Known tradeoff: material-event summaries will be mushier; nothing
> downstream trades on them yet.

## Purpose

The News Analyzer turns the raw news flow for the firm's tradeable names into
structured, deduplicated, materiality-scored signal events on
`intelligence.signal`. It is the first Intelligence Department feed agent:
without it the Decision Maker's context, the future Hypothesis Generator, and
the Tech Watcher's event trigger all operate blind to same-day material news
— an earnings surprise, a guidance cut, an M&A announcement — that every
other market participant has already priced.

The failure mode this agent prevents is specific: the firm holding or
entering a paper position through a material event it had the data to see.
The failure mode this agent must not introduce is just as specific: it
produces **inputs, not opinions**. No direction hints, no trade suggestions,
no sentiment scores in v1 — materiality and category only. Direction belongs
to consumers that carry accountability for it.

## Trigger

- **Schedule:** market-phase-aware polling, driven by `operations.market-phase`
  (the scheduler's first consumer): every 10 minutes during `pre-open`,
  `open`, and `after-hours`; every 60 minutes during `overnight` and
  `closed-day`. On startup, read the latest phase from the stream rather
  than waiting for a transition.
- **Event:** none in the seed (this agent *produces* the events others
  subscribe to).
- **On-demand:** Mike-initiated backfill over a date range (CLI, same
  container).

## Cross-references

**Depends on:** Market Phase Scheduler (`operations.market-phase`, cadence),
Alpaca news API availability, LLM tier client (ADR-0009).
**Depended on by:** Tech Watcher (subscribes to `intelligence.signal` as its
event trigger), Decision Maker context (future), Hypothesis Generator
(future), Risk Officer (future).
**Related ADRs:** ADR-0003 (broker-facing credential isolation), ADR-0006
(envelope), ADR-0009 (LLM tiers), ADR-0012 (tiered universe — this agent
reads Tier 3).
**Related architecture sections:** `docs/02-architecture.md` §Intelligence
Department.

## Inputs

| Source | Type | Description |
|---|---|---|
| Alpaca News API (`/v1beta1/news`) | HTTP pull | Headlines + summaries for configured symbols, since cursor. Free with existing paper-account credentials (Benzinga-sourced); no new vendor. Uses the data API only — see open question 1 |
| Redis: `operations.market-phase` | Event | Polling cadence per phase; latest-entry read on startup |
| Config: symbol list | Env | The Tier 3 launch names (ADR-0012). Seed reads from env like the Regime Classifier; switches to the Universe Curator's Tier 3 state when that exists |
| Repo: `docs/universe/README.md` | File read | Human reference only; not parsed at runtime |

## Processing

1. **Fetch since cursor.** Pull news items for the configured symbols since
   the last stored item id/timestamp. Item identity is the Alpaca news id;
   upserts are idempotent and the cursor advances atomically with the
   ingest (same pattern as Tech Watcher ingest).
2. **Bulk score (local tier).** Each new item gets one
   `local-classification` call, `think:false`, strict JSON:
   `{relevant: bool, symbols: [...], category: earnings | guidance | ma |
   litigation | regulatory | product | management | macro | other,
   materiality: 0-3, summary: <one sentence>}`. Unparseable responses score
   materiality 0 and are logged — the bias is to drop, never to invent.
   Every verdict appends to a history table stamped with prompt version and
   model (the KI-007 rule, applied from day one, not retrofitted).
3. **Escalate material items.** Items at or above the materiality threshold
   (default >= 2) get one `cloud-default` re-read producing a tighter
   structured summary. The escalation result appends to the same history;
   the higher verdict wins for publishing.
4. **Publish.** One `intelligence.signal` event per relevant item (ADR-0006
   envelope): `signal_type: "news"`, symbols, category, materiality,
   headline, summary, source ("alpaca-news"), `published_at` (the outlet's
   timestamp), `item_ref` (news id). Materiality-0 items are stored but not
   published — the stream carries signal, the table carries the denominator.
5. **Heartbeat.** Emit an ingestion heartbeat per pass so the Health Monitor
   sees freshness, and log counts (fetched / relevant / published /
   escalated) per pass.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis: `intelligence.signal` | Event | Materiality >= 1 news signals, envelope per ADR-0006, schema above |
| PostgreSQL: `intelligence.news_items` | Write | Every fetched item (raw payload, headline, symbols, timestamps) — the full denominator |
| PostgreSQL: `intelligence.news_verdict_history` | Write | Append-only score history per item: prompt version, tier, model, verdict (KI-007 pattern) |

## LangGraph structure

None. Plain asyncio service loop per the house pattern (fetch → score →
publish). LangGraph enters only if a future version needs multi-node
orchestration, per the tooling gate in CLAUDE.md.

## State

| What | Store | Notes |
|---|---|---|
| Ingest cursor (last news id + timestamp) | PostgreSQL | Advanced atomically with item upsert |
| News items | PostgreSQL | Keyed by Alpaca news id; idempotent upsert |
| Verdict history | PostgreSQL | Append-only; never overwritten by re-scores |

## Failure behavior

1. **Containment:** contained. Signals are advisory inputs; nothing in the
   seed consumes them for order flow, and when consumers arrive they carry
   their own gates (Pre-Trade Checker is unaffected by this agent
   entirely). A wrong materiality score pollutes one stream and one table.
2. **Replay safety:** safe. Item identity is the vendor news id; upserts
   are idempotent; verdict history is append-only, so reprocessing after a
   crash re-scores unmarked items and duplicates nothing. Consumers must
   tolerate duplicate `intelligence.signal` events (same `item_ref`) after
   a crash-between-publish-and-mark, per house consumer discipline.
3. **Degraded operation:** the system runs indefinitely without it — paper
   trading continues on the fixture/strategy path. The cost is blindness to
   news, which is today's status quo. If Ollama is down, the pass stops and
   resumes next tick (same semantics as the Tech Watcher filter); items
   queue unscored rather than publishing unverified signals.

## Sprint scope

- Month 3 (seed): Alpaca news for the Tier 3 launch names, local bulk
  scoring, escalation wiring, `intelligence.signal` publishing, both tables,
  market-phase-aware cadence. Deployable container on the Dell.
- Month 3+ (after seed verification): Tech Watcher event-trigger pass on
  material signals actually firing end to end.

## Deferred

- Full-text article fetch and Qdrant embedding (headlines + API summaries
  only in the seed).
- Sentiment, social sources (Sentiment Monitor is its own Month 3 agent).
- Direction hints of any kind (deliberate — see Purpose).
- Per-call routing beyond the single materiality-threshold escalation.
- Tier 2 watch-name coverage (see open question 2).

## Open questions

- **News vendor:** the seed proposes Alpaca's news API — free, already
  credentialed, Benzinga-sourced, but breadth-limited and broker-coupled.
  Accepting this spec accepts Alpaca as the seed vendor; a dedicated news
  API is a post-seed upgrade decision. Blocks: implementation card. Owner:
  Mike (decision-by-merge).
- **Tier 2 coverage (ADR-0012):** should watch-list names get news signals
  too? Proposal: Tier 3 only in the seed; extend when the Universe Curator
  owns Tier 2 state, since watch names are exactly where news-driven
  elevation evidence accrues. Blocks: nothing now. Owner: Mike, with the
  Curator card.
- **Materiality calibration:** the 0–3 scale and the >= 2 escalation
  threshold are uncalibrated guesses until live batches exist — same
  posture as the Tech Watcher filter's first weeks. The verdict history
  table is the calibration dataset by construction. Blocks: nothing; first
  live batches inform v2. Owner: Mike reviews after the first week live.
- **Credential isolation shape (ADR-0003):** the news endpoint uses the
  Alpaca *data* API, same as the Regime Classifier, so the precedent is to
  mount the keys in this container. If Mike prefers stricter isolation
  (news via a proxy container), that is an infra decision before the
  implementation card. Blocks: implementation card. Owner: Mike.
