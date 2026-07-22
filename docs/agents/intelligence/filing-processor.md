# Filing Processor

**Department:** Intelligence
**LLM tier:** `local-classification` for bulk item-code scoring; `cloud-default`
escalation for items scored material (per `docs/infrastructure/llm-routing.md` —
same pattern as the News Analyzer: bulk local, high-impact events escalate).
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-07-22
**Author:** Mike White

> **Seed-deployment routing:** same ruling as the Tech Watcher and News
> Analyzer (2026-07-15/17) — the firm runs local-only until cloud API billing
> exists, so in the deployed seed both tiers env-route to Ollama on the Dell.
> The escalation path is wired but lands on the same local model until the
> compose env changes. Known tradeoff: material-event summaries will be
> mushier; nothing downstream trades on them yet.

## Purpose

The Filing Processor reads the full text of 8-K filings for the Tier 3
(active, tradeable) universe and extracts material events into structured,
materiality-scored signal events on `intelligence.signal`. Without it, the
firm's only EDGAR-derived signal is the Tech Watcher's headline-level
current-filings feed — market-wide, form-type only, built for discovery, not
for reading what a filing actually says — and its only company-event signal
at all is the News Analyzer's Alpaca news-wire feed, which misses anything
disclosed only via 8-K and never picked up by a wire service. The failure
mode this agent prevents is specific: the firm holding or entering a paper
position through a material corporate disclosure that reached EDGAR but
never reached a headline.

**Two scope boundaries, stated explicitly because both are easy to build
wrong:**

1. **Not the Tech Watcher.** The Tech Watcher ingests SEC EDGAR current
   filings (10-K/10-Q/8-K) market-wide, headline-only, with no Tier 3 filter
   — that is correct for its purpose, which is discovery, not depth (ADR-0012:
   "no agent may apply a Tier 3 filter at ingest or discovery time"). The
   Filing Processor is the opposite shape: it applies the Tier 3 filter and
   goes deep — full filing text, not the Atom-feed title — but only for the
   ~50 names the firm actually trades. Nobody should extend the Tech
   Watcher's `EdgarSource` to do full-text fetch or per-name filtering; that
   capability belongs here.
2. **Not Structural Analysis.** Per ADR-0012's Notes, 10-K/10-Q deep reads
   belong to the Structural Analysis Department's Filing Deep Reader (month
   3-4), and Form 425 (merger communications) and Form 4 (insider
   transactions) belong to that department's ingest extension, not this
   agent. The Filing Processor reads 8-Ks only. It does not read annual or
   quarterly reports, does not track insider transactions, and does not
   read merger-communication filings — those are a slower-clock, higher-cost
   read with a different consumer (bias/sizing modifiers, not
   `intelligence.signal`) and a different owner.

The failure mode this agent must not introduce is the same one the News
Analyzer commits to: it produces **inputs, not opinions**. No direction
hints, no trade suggestions — materiality and category only, keyed off the
filing's own declared 8-K item numbers. Direction belongs to consumers that
carry accountability for it.

## Trigger

- **Schedule:** market-phase-aware polling, driven by `operations.market-phase`
  — every 10 minutes during `pre-open`, `open`, and `after-hours`; every 60
  minutes during `overnight` and `closed-day`. On startup, read the latest
  phase from the stream rather than waiting for a transition. Same cadence
  as the News Analyzer, deliberately — both are Intelligence feed agents on
  the same clock.
- **Event:** none. The Tech Watcher does not publish a per-raw-item stream
  (only funnel-level proposal/kill/promotion events) — see open question 1.
  The working design in this spec is a table poll, not a stream
  subscription: each pass reads `research.raw_source_items` (owned by the
  Tech Watcher) for new rows since the Filing Processor's own cursor.
- **On-demand:** Mike-initiated backfill over an accession-number or date
  range (CLI, same container).

## Cross-references

**Depends on:** Tech Watcher (`research.raw_source_items`, `source =
'sec-edgar'`, `kind = '8-K'` — the source of every candidate item; see open
question 1), Market Phase Scheduler (`operations.market-phase`, cadence),
LLM tier client (ADR-0009), SEC EDGAR full-text availability.
**Depended on by:** Decision Maker context (future), Hypothesis Generator
(future), Risk Officer (future).
**Related ADRs:** ADR-0006 (envelope), ADR-0009 (LLM tiers), ADR-0012
(tiered universe — this agent reads Tier 3; the Notes section is the source
of the Structural Analysis boundary above).
**Related architecture sections:** `docs/02-architecture.md` §Intelligence
Department.

## Inputs

| Source | Type | Description |
|---|---|---|
| PostgreSQL: `research.raw_source_items` | Query | Tech Watcher's ingested EDGAR items, filtered to `source = 'sec-edgar' AND kind = '8-K'`. Read-only — this agent never writes to a Tech Watcher table |
| SEC EDGAR Archives | HTTP pull | Full filing text, fetched per matched item from the Archives path encoded in the item's stored `url` (the same link the Tech Watcher's `EdgarSource` captured but never dereferenced). Requires the descriptive `User-Agent` convention — see Processing step 3 |
| Redis: `operations.market-phase` | Event | Polling cadence per phase; latest-entry read on startup |
| Config: Tier 3 roster (ticker + CIK) | Env | The Tier 3 launch names (ADR-0012), keyed by CIK rather than ticker alone, because EDGAR resolution is CIK-based. Seed reads from env like the Regime Classifier and News Analyzer; switches to the Universe Curator's Tier 3 state when that exists and carries CIK |
| Repo: `docs/universe/README.md` | File read | Human reference only; not parsed at runtime |

## Processing

1. **Poll the shared ingest table.** Each pass, query
   `research.raw_source_items` for rows with `source = 'sec-edgar'`,
   `kind = '8-K'`, and `fetched_at` after this agent's own cursor. This is a
   read against a table the Tech Watcher owns, not a stream — no per-item
   event exists to subscribe to (open question 1). Advance the Filing
   Processor's own cursor past every row seen this pass, matched or not, so
   re-polling never re-scans the market-wide backlog.
2. **Resolve to Tier 3.** Extract the registrant CIK from the item's stored
   `url` (the EDGAR Archives index path is keyed to the registrant's CIK)
   and match against the Tier 3 roster. Non-matching items are dropped here
   — this is where the Tier 3 filter is applied, never upstream at ingest
   (ADR-0012: "the tiers bound cost, not curiosity"). Matched items carry
   forward the ticker for downstream signal symbols.
3. **Fetch full text under SEC fair-access rules.** For matched items,
   fetch the filing's primary document using a descriptive `User-Agent`
   (identity + contact info, same convention as `EdgarSource` in
   `src/shrap/research/tech_watcher/sources.py`) and
   `Accept-Encoding: gzip, deflate`. Requests stay well under SEC's
   published fair-access ceiling of 10 requests/second — for Tier 3-sized
   8-K volume (a handful of filings per pass across 50 names) the realistic
   sustained rate is nowhere near that ceiling, so throttling is a safety
   margin, not a throughput constraint. A 429/403 response backs off and
   retries next pass rather than retrying in-pass.
4. **Extract declared item codes.** Parse the filing's declared Item
   numbers (e.g., 1.01, 2.02, 5.02, 7.01, 8.01) from the document
   structure. A single 8-K can declare multiple, unrelated items — a
   results announcement and an officer departure filed the same day are
   common — so each item's section is treated as its own material-event
   candidate, not the filing as a whole. This prevents a buried item from
   being diluted by a routine co-filed one, and prevents one filing from
   producing one flattened, less-useful signal.
5. **Local score each item-code section.** One `local-classification`
   call per item, `think:false`, strict JSON: `{relevant: bool, symbols:
   [...], item_code, category: material-agreement | results |
   officer-change | control-change | impairment | delisting |
   accountant-change | other-events | other, materiality: 0-3, summary:
   <one sentence>}`. The item code seeds a prior in the prompt — 1.01,
   2.01, 2.02, 3.01, 4.02, and 5.01 skew high; 5.03 and 9.01 skew low; 5.02,
   7.01, and 8.01 are genuinely mixed and the model's read decides, since
   "Other Events" (8.01) is a catch-all that ranges from boilerplate to the
   most material thing in the batch. The prior is context, not a hard
   override. Unparseable responses score materiality 0 and are logged — the
   bias is to drop, never to invent, same as the News Analyzer. Every
   verdict appends to a history table stamped with prompt version and model
   (the KI-007 rule, applied from day one).
6. **Escalate material items.** Item sections at or above the materiality
   threshold (default >= 2) get one `cloud-default` re-read producing a
   tighter structured summary. This is deliberately the same threshold the
   News Analyzer uses, so the two agents' materiality scores are comparable
   on `intelligence.signal` rather than each inventing its own scale. The
   escalation result appends to the same history; the higher verdict wins
   for publishing.
7. **Publish.** One `intelligence.signal` event per relevant item-code
   section (ADR-0006 envelope): `signal_type: "filing"`, symbols, category,
   materiality, `item_code`, headline (derived, e.g. "8-K Item 5.02 —
   <company>"), summary, source (`"sec-edgar"`), `published_at` (the
   filing's date), `item_ref` (`<accession>#<item_code>`). Materiality-0
   sections are stored but not published, mirroring the News Analyzer's
   denominator-vs-signal split.
8. **Heartbeat.** Emit an ingestion heartbeat per pass so the Health
   Monitor sees freshness, and log counts (filings seen / Tier 3 matched /
   full text fetched / items extracted / published / escalated) per pass.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis: `intelligence.signal` | Event | Materiality >= 1 filing signals, envelope per ADR-0006, `signal_type: "filing"`, schema above |
| PostgreSQL: `intelligence.filings` | Write | Every Tier 3-matched 8-K fetched: accession number, CIK/symbol, filing date, declared item codes, full text (storage shape is open question 2), raw metadata — the full denominator |
| PostgreSQL: `intelligence.filing_verdict_history` | Write | Append-only score history per item-code section: prompt version, tier, model, verdict (KI-007 pattern) |

## LangGraph structure

None. Plain asyncio service loop per the house pattern (poll table → resolve
Tier 3 → fetch full text → extract items → score → publish). LangGraph
enters only if a future version needs multi-node orchestration, per the
tooling gate in CLAUDE.md.

## State

| What | Store | Notes |
|---|---|---|
| Filing ingest cursor (last `research.raw_source_items` row seen) | PostgreSQL | Own cursor, separate from the Tech Watcher's own ingest cursor on the same table; advanced past every row seen this pass regardless of Tier 3 match |
| Filings | PostgreSQL `intelligence.filings` | Keyed by EDGAR accession number; idempotent upsert |
| Item-level verdict history | PostgreSQL `intelligence.filing_verdict_history` | Append-only; never overwritten by re-scores |

## Failure behavior

1. **Containment:** contained, with one nuance the News Analyzer doesn't
   have. Signals are advisory; nothing in the seed consumes them for order
   flow, so a wrong materiality score or a missed item pollutes one stream
   and two tables. The nuance: this agent's trigger design (open question
   1) makes it depend on the Tech Watcher's EDGAR ingest for item
   discovery. If that ingest has an outage or a bug that misses a filing,
   the Filing Processor inherits the gap silently — it has no independent
   way to know a Tier 3 8-K existed if the Tech Watcher never saw it. This
   is a real, if narrow, cross-agent coupling and is the strongest argument
   for the self-poll alternative in open question 1.
2. **Replay safety:** safe. Item identity is the EDGAR accession number
   plus item code; upserts are idempotent; verdict history is append-only.
   The Filing Processor's own cursor is independent of the Tech Watcher's,
   so restarting this agent never perturbs the Tech Watcher's ingest state,
   and a crash mid-batch re-polls the same window and no-ops on
   already-matched accessions via the upsert. Consumers must tolerate
   duplicate `intelligence.signal` events (same `item_ref`) after a
   crash-between-publish-and-mark, per house consumer discipline.
3. **Degraded operation:** the system runs indefinitely without it —
   paper trading continues on the fixture/strategy path. The cost is
   blindness to 8-K-only disclosures for Tier 3 names, partially offset by
   the News Analyzer's coverage of the same events when they also generate
   a news-wire hit. If SEC EDGAR is unavailable or the `User-Agent` gets
   rate-limited or blocked, the pass logs and resumes next tick without
   fetching full text — matched items stay pending, not lost. If Ollama is
   down, fetched-but-unscored items queue rather than publishing unverified
   signals, same semantics as the News Analyzer.

## Sprint scope

- Month 2 (seed, pulled forward by the DQ-007 reorder ruling): full-text
  8-K fetch for Tier 3 names via the Tech Watcher's ingest table,
  item-code extraction, local bulk scoring, cloud escalation wiring,
  `intelligence.signal` publishing, both tables, market-phase-aware
  cadence. Deployable container on the Dell.
- Month 2+ (after seed verification): first live batch review — same
  posture as the News Analyzer and the Tech Watcher filter, calibration is
  earned from live batches, not assumed at spec time.

## Deferred

- 10-K / 10-Q full-text reads — Structural Analysis's Filing Deep Reader,
  month 3-4, not this agent (see Purpose, scope boundary 2).
- Form 425 (merger communications) and Form 4 (insider transactions) —
  Structural Analysis's ingest extension per ADR-0012's Notes, not this
  agent.
- Qdrant embedding of filing full text (deferred the same way the Tech
  Watcher deferred it in month 2 — full text is captured, semantic search
  over it is a later card).
- Sentiment, social sources (Sentiment Monitor is its own Month 3 agent).
- Direction hints of any kind (deliberate — see Purpose).
- Tier 2 watch-name coverage (see open question 3).
- Independent EDGAR polling, if open question 1 is ruled the other way.

## Open questions

- **Trigger ownership (open question 1):** should the Filing Processor poll
  EDGAR itself for Tier 3 names, or subscribe to / read the Tech Watcher's
  ingested items? This spec's working design is the latter — read
  `research.raw_source_items` and fetch full text only for Tier 3 matches —
  because it avoids two agents independently polling the same SEC feed for
  overlapping form types. The cost, named in Failure behavior above, is a
  real coupling: a Tech Watcher ingest gap becomes a silent Filing
  Processor gap. Self-polling per Tier 3 CIK (SEC also exposes a per-company
  submissions endpoint, not just the market-wide current-filings feed the
  Tech Watcher uses) would remove that coupling at the cost of a second,
  independent EDGAR client. Blocks: the implementation card. Owner: Mike
  (decision-by-merge).
- **Full-text storage location (open question 2):** Postgres `TEXT` column
  vs. repo blobs. Precedent exists for both — the Tech Watcher itself
  stores filings as repo blobs and papers as Postgres rows with full text
  by repo path. This spec's default is a Postgres `TEXT` column on
  `intelligence.filings`: 8-K bodies are small relative to 10-Ks, there is
  no human-review benefit the way there is for the Tech Watcher's
  git-diffable candidate cards, and a column is simpler to index later
  (Qdrant embedding, full-text search) than a repo path. Blocks: the
  `intelligence.filings` schema in the implementation card. Owner: Mike.
- **Tier 2 coverage (ADR-0012):** should watch-list names get filing
  signals too? Same answer as the News Analyzer: Tier 3 only in the seed;
  extend when the Universe Curator owns Tier 2 state, since watch names are
  exactly where filing-driven elevation evidence accrues. Blocks: nothing
  now. Owner: Mike, with the Curator card.
- **Materiality calibration:** the 0-3 scale, the item-code priors in
  Processing step 5, and the >= 2 escalation threshold are uncalibrated
  guesses until live batches exist — same posture as the News Analyzer and
  the Tech Watcher filter's first weeks. The verdict history table is the
  calibration dataset by construction. Blocks: nothing; first live batches
  inform v2. Owner: Mike reviews after the first week live.
