# Tech Watcher

**Department:** Research (Structural Funnel — Step 1)
**LLM tier:** `cloud-default` primary for source triage and synthesis;
`cloud-judgment-heavy` for the weekly cross-source synthesis pass that produces the
ranked candidate list (judgment is load-bearing there). `local-classification` for the bulk
filtering/deduplication pre-pass. Migration target: keep the synthesis pass on a cloud
tier for the foreseeable future; move bulk filtering fully local once shadow
evaluation passes. See `docs/infrastructure/llm-routing.md` and `docs/infrastructure/llm-registry.md`.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-05-30 (routing amendment 2026-07-17)
**Author:** Mike White
**Version:** 0.1 (draft)

> **Seed-deployment routing (Mike's ruling, 2026-07-15/17):** the firm runs
> local-only until cloud API billing exists. In the deployed seed, every
> tier this agent uses is env-routed to Ollama on the Dell
> (`qwen3.5:9b-q4_K_M`) via the tier client's deployment overrides — the
> tier aliases above remain the spec contract, and flipping synthesis to a
> true cloud model is a compose env change, not a code change. Known
> quality tradeoff: local synthesis produces mushier candidates; Mike
> gatekeeps at the review page. The v0 pipeline also simplifies step 3:
> clustering is by archetype (deterministic), not LLM topic/entity
> clustering; single-source clusters wait unsynthesized until a second
> source class corroborates (they are the `seen-not-proposed` population).

## Purpose

The Tech Watcher is the top of the new three-step Research funnel
(ADR-0007). Its job is to continuously scan primary sources for credible
*world-changer* candidates — technologies, products, programs, or scientific
results that, if they play out, would force a material reallocation of
capital across an identifiable supply chain. It is not a news aggregator and
it is not a stock picker. It is an upstream sieve whose outputs become input
to the Infrastructure Mapper.

This agent exists because the firm's structural edge claim is that the
trading universe should be *derived* from real-world dependency graphs rooted
in real world-changers, not curated from a vendor list or a popularity
ranking. If the Tech Watcher is permissive — promotes anything that looks
exciting — the downstream graphs become noise and the universe inherits that
noise. If the Tech Watcher is over-aggressive about killing candidates, the
funnel starves. The design target is to be *honest about uncertainty* and to
keep an explicit kill rate of 80%+ on proposed candidates over any
trailing 90-day window. A Tech Watcher whose proposals get promoted at a
>30% rate should be investigated for overfitting to recency or to a single
source class.

**What this agent cannot do.** It cannot tell whether a world-changer will
actually change the world. It can only register that credible primary
sources have placed weight on it, that the evidence triangulates across
independent source classes, and that an explicit falsifier exists. The
agent is the proposer. Mike is the gatekeeper for promotion. Read the
"survivorship bias" subsection below before trusting any rolling hit rate
this agent reports about itself.

**Survivorship bias warning (load-bearing).** Any look at "world-changers
the system flagged early and were right" is contaminated unless the
denominator includes every candidate ever proposed, including the ones
killed silently. The agent MUST preserve the full proposal history,
including reason-for-kill, and the Reporting Department MUST compute hit
rate against that denominator — not against a curated highlight reel. The
default report view shows the full kill graveyard alongside any promoted
winners. If Mike finds himself looking only at the winners, that is a
behavior bug in the reporting layer, not a feature.

## Trigger

- **Schedule:**
  - Hourly: incremental pull of SEC EDGAR (10-K/Q/8-K filings since last
    cursor), arXiv (cs.AI, cs.LG, cond-mat, q-bio.NC since last cursor),
    USPTO patent grants (weekly grant schedule), USASpending and SAM.gov
    contract awards.
  - Daily 06:00 ET: thematic synthesis pass over the prior 24h ingest. LLM
    clustering + candidate proposal.
  - Weekly Sunday 22:00 ET: full cross-source synthesis pass with citation
    graph inputs and the higher-tier model. Re-scores all active candidates.
- **Event:** Subscribes to `intelligence.signal` for relevant keynote /
  product-launch events surfaced by the Intelligence Department (GTC, WWDC,
  Apple events, OpenAI/Anthropic/Google launches). On match, triggers a
  targeted synthesis pass within 30 minutes.
- **On-demand:** Mike-initiated `research.tech-watcher-request` with optional
  constraints (theme, source class, time window).

## Cross-references

**Depends on:** Intelligence Department (event surfacing), shared envelope
library, the source-ingestion plumbing in the Platform Department.
**Depended on by:** Infrastructure Mapper (consumes promoted
world-changers), Mike's review queue (consumes proposals), Bottleneck Scout
(uses the promoted world-changer list to scope which graphs to scan).
**Related ADRs:** ADR-0006 (envelope), ADR-0007 (Research funnel — pending).
**Related architecture sections:** `docs/02-architecture.md` §Research
Department, §Intelligence Department interface.

## Inputs

| Source | Type | Description |
|---|---|---|
| SEC EDGAR (10-K/Q/8-K) | HTTP pull | Filings since last cursor; full text fetched, stored in Postgres + Qdrant. Capex, R&D, segment commentary, risk factors |
| arXiv (cs.AI, cs.LG, cond-mat, q-bio.NC) | HTTP pull | New preprints daily; abstract + metadata + citation backrefs |
| USPTO PatentsView | HTTP pull | Granted patents and published applications, filtered by CPC classes relevant to active world-changer themes |
| Conference keynote video/transcript feeds | HTTP pull / repo cache | GTC, WWDC, Apple events, OpenAI dev days, Google I/O. Transcripts when available, ASR fallback otherwise |
| USASpending.gov | HTTP pull | Government contract awards above a configurable threshold |
| DOE newsroom (energy.gov RSS) | HTTP pull | Agency press announcements — program launches, award context (deployed 2026-07-18) |
| Federal Register API (agency-filtered; NRC at launch) | HTTP pull | Regulator paper trail: license applications/renewals, rules, notices — the licensing-throughput signal. NRC's own site RSS is bot-blocked to non-browser clients (Akamai 403, verified 2026-07-19); the FR API is the reliable mirror of the regulator's substantive actions |
| SAM.gov | HTTP pull | Active solicitations and award notices |
| Semantic Scholar / OpenAlex | HTTP pull | Citation graph deltas for the cs.AI / cond-mat / q-bio.NC working set |
| Redis: `intelligence.signal` | Event | Event-driven trigger for product launch and keynote-day passes |
| PostgreSQL: `research.world_changers` | Query | All prior candidates and their statuses (anti-duplication, hit-rate denominator) |
| Qdrant: `world_changer_corpus` | Semantic search | Embeddings of prior candidate descriptions and source evidence |
| Repo: `docs/research/world-changer-archetypes.md` | File read | Allowed candidate archetypes (e.g. "compute substrate shift", "manufacturing process node jump", "biology platform technology", "energy supply discontinuity") |

## Source-class independence taxonomy (v1, 2026-07-19)

Added when the deployed slice grew from two feeds (EDGAR, arXiv) to five
(USASpending, DOE newsroom, Federal Register). The two-source triangulation
rule counts *legs of independent evidence*, and with five feeds the feed
name is no longer a safe proxy for independence: a DOE newsroom item and a
DOE award are one institution speaking twice about the same program, and
must not fake two-leg triangulation.

**The independence unit is the originating institution, not the ingest
feed.** Each raw item maps deterministically to an origin:

| Ingest source | Origin | Hardness |
|---|---|---|
| `sec-edgar` | `issuer` (v1: one bucket for all issuers — see open question 1) | hard |
| `arxiv` | `research` | soft |
| `usaspending` | `gov:<awarding agency>` (from the award record) | hard |
| `doe-newsroom` | `gov:department-of-energy` | soft |
| `federal-register` | `gov:<agency slug>` (from the FR document's agency; NRC at launch) | hard |

Two `gov:` origins are the same leg iff the agency matches: DOE press +
DOE award is **one** leg; DOE award + NRC Federal Register notice is two.

**Hardness** separates facts with legal or financial consequence (issuer
filings, obligated awards, regulator actions) from narrative and claims
without committed capital (agency press, preprints).

**Triangulation rule v1.** A cluster is promotable iff:

1. its items span **>= 2 distinct origins**, and
2. **at least one leg is hard**.

Consequences worth stating plainly: DOE press + DOE award no longer
promotes (one origin). Agency press + arXiv no longer promotes (no hard
leg — that is the marketing-plus-hype pair). EDGAR + arXiv still promotes
(hard issuer leg + research leg), which is the pair the funnel was built
on. Two agencies' press releases alone never promote.

`mike-seed` candidates are outside this taxonomy: they enter via the
promotion CLI without triangulation, and their `source_class` records that
provenance honestly.

**Rule for future sources:** every new ingest-source card must extend this
table (origin mapping + hardness) in the same PR. A source without a
declared origin does not count toward triangulation.

**Open questions (Mike rules by merge, or defers to follow-ups):**

1. *Per-issuer granularity for EDGAR.* Two different companies' filings
   are arguably two independent actors placing weight. v1 keeps one
   `issuer` bucket — conservative (fewer promotions), and per-item issuer
   extraction is not yet parsed from the feed. Revisit with full-text
   ingestion.
2. *Procedural regulator noise.* Federal Register hardness assumes
   substantive actions (license applications/renewals, rules). Sunshine
   Act meeting notices and paperwork-renewal notices are technically hard
   class but carry no archetype signal; the bulk filter is expected to
   drop them. If they leak through and complete triangulations, demote
   procedural FR document types to soft in a follow-up.
3. *arXiv hardness.* Preprints stay soft even from major labs. If a
   peer-reviewed or replicated result should ever count hard, that is a
   deliberate future change, not a default.

**Implementation note (spec-first).** The deployed v0 code counts distinct
feed names (`Cluster.source_classes`). This section is the contract for
the follow-up code card: derive origin + hardness per item (the needed
fields are already in item payloads) and replace the promotable predicate
with rules 1–2. Until that card ships, the deployed rule remains
feed-name-based and this taxonomy governs interpretation at the review
page.

## Processing

1. **Ingest cursor advance.** For each source class, advance the per-source
   cursor and fetch new items since last run. Persist raw items to
   Postgres + repo cache (filings as repo blobs, papers as Postgres rows
   with abstract inline and full text by repo path). Emit
   `ingestion.heartbeat` per source so the Health Monitor sees freshness.
2. **Bulk filter (local LLM).** The `local-classification` tier scores each new item
   for relevance to the active world-changer archetype list. Items below
   threshold are stored but excluded from the candidate-build step.
3. **Cluster and triangulate.** Daily pass: cluster the relevant new items
   by topic + entity. A cluster is promotable to a candidate proposal only
   if it draws on >=2 independent source classes (e.g., a 10-K plus a
   conference keynote, or an arXiv paper plus a patent filing).
   Independence is defined by the source-class independence taxonomy
   above — origins and hardness, not ingest feed names. Single-
   source clusters are recorded but do not become proposals — this is the
   primary defense against marketing-driven false positives.
4. **Synthesize candidate (cloud LLM).** For each promotable cluster, the
   cloud LLM is asked to draft a candidate proposal in a strict JSON
   schema:
   - `name`: short identifier
   - `archetype`: must match an allowed archetype
   - `thesis`: one-paragraph statement of what would change if true
   - `confidence`: low / medium / high — **not a probability**. The
     calibration is documented in `docs/research/calibration.md` and the
     agent is forbidden from inventing numeric probabilities.
   - `evidence`: array of `{source_class, source_ref, weight_reason}`
   - `expected_impact_horizon`: one of `<1y`, `1-3y`, `3-5y`, `5-10y`,
     `>10y`. The agent must justify the choice; "horizon unknown" is a
     legal value and is preferred to a fabricated one.
   - `kill_criteria`: array of explicit, observable conditions that would
     retract the candidate. Each must be a *named published metric*
     crossing a *named threshold*, not "if it doesn't pan out." Examples:
     "TSMC CoWoS capacity expansion guidance falls below 2x by FY27
     earnings call" or "no NRC SMR construction permit issued by EOY 2027."
   - `falsifier_horizon`: the date by which at least one kill criterion
     is observable. If none of the kill criteria can be observed within
     5 years, the candidate is rejected as unfalsifiable.
   - `dependency_graph_seed`: the agent's best guess at 3-10 layers that
     would appear in the Infrastructure Mapper's graph if promoted. This is
     advisory input to the Mapper, not a commitment.
5. **Validate locally.** Deterministic validator drops proposals that:
   fail schema; name un-allowed archetypes; omit kill criteria or
   falsifier horizon; are >0.88 cosine-similar to a promoted or
   actively-tracked candidate; cite only one source class; or invent
   numeric probabilities. Rejections logged with reason.
6. **Score and rank.** Surviving candidates are scored on (a) source
   triangulation breadth, (b) recency-weighted evidence count,
   (c) archetype prior. The score is ordinal within the batch and is
   *not* presented as a probability. Top-N by score (default 10) are
   marked `proposed`; the rest are stored as `seen-not-proposed` for
   future re-evaluation.
7. **Publish proposals.** Each proposed candidate emits one
   `research.world-changer-proposed` event with the ADR-0006 envelope
   and payload-by-reference to the Postgres row.
8. **Re-score active candidates.** Weekly pass walks every candidate in
   `proposed`, `under-review`, or `promoted` status, gathers any new
   evidence, and emits `research.world-changer-updated` if score moves
   materially or if a kill criterion has been observed. If a kill
   criterion fires, the candidate transitions to `killed` and emits
   `research.world-changer-killed` with the firing criterion attached.
9. **Promotion is Mike's call.** The agent does not auto-promote. Mike's
   review queue surface (Reporting Department) lists proposals with their
   evidence trails. Mike's promotion action causes the agent to emit
   `research.world-changer-promoted`, which the Infrastructure Mapper
   subscribes to.
10. **Daily summary.** End-of-day rollup event with N ingested, N filtered,
    N clustered, N proposed, N killed, N promoted-by-Mike, kill-rate
    trailing-90d. Surfaces in the Daily Briefing.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `research.world-changer-proposed` | Event | One per surviving proposal, payload-by-reference |
| Redis stream: `research.world-changer-promoted` | Event | Emitted after Mike's explicit promotion action |
| Redis stream: `research.world-changer-killed` | Event | Emitted when a kill criterion fires or when superseded |
| Redis stream: `research.world-changer-updated` | Event | Material score change or new evidence on an active candidate |
| Redis stream: `research.tech-watcher-summary` | Event | Daily rollup |
| PostgreSQL: `research.world_changers` | Insert/update | Full candidate record with status transitions, append-only history table |
| PostgreSQL: `research.world_changer_evidence` | Insert | Evidence rows referenced by candidates, append-only |
| Qdrant: `world_changer_corpus` | Upsert | Embeddings of candidate descriptions and evidence summaries |
| Repo: `docs/research/world-changers/proposed/<id>.md` | File write (sandbox branch) | Human-readable candidate card. Never auto-merged. |

Every event carries the ADR-0006 envelope. Every candidate record stores
the LLM call's prompt hash, model, temperature, and raw response reference
for audit replay.

## LangGraph structure

**Nodes:**
- `ingest` — per-source cursor advance + raw persist
- `bulk-filter` — local LLM relevance scoring
- `cluster` — topic + entity clustering with triangulation rule
- `synthesize` — cloud LLM candidate draft
- `validate` — schema + policy checks
- `score-rank` — ordinal scoring against batch
- `persist-emit` — write to stores, publish events
- `rescan-active` — weekly re-scoring of existing candidates

**Key edges:**
- `ingest` → `bulk-filter` → `cluster` → `synthesize` → `validate` → `score-rank` → `persist-emit`
- `rescan-active` → `validate` → `persist-emit` (weekly, separate trigger)

## State

| What | Store | Notes |
|---|---|---|
| Per-source ingest cursors | PostgreSQL `research.ingest_cursors` | Updated atomically with the ingest |
| All candidates ever proposed | PostgreSQL `research.world_changers` | Append-only history. Hit-rate denominator. |
| Per-candidate evidence rows | PostgreSQL `research.world_changer_evidence` | Append-only |
| Embeddings | Qdrant `world_changer_corpus` | Indexed for novelty/dedup |
| Daily batch records | PostgreSQL `research.tech_watcher_batches` | Prompt hash, model, n_proposed, n_killed |

## Failure behavior

1. **Containment.** A bad proposal does not move money. It costs Mike's
   review attention and downstream Infrastructure Mapper compute. Blast
   radius is bounded to those two costs. A bad kill is more expensive
   (a real signal silenced) but recoverable because the agent re-scans
   active candidates weekly and Mike can manually revive a killed
   candidate.
2. **Replay safety.** LLM calls are non-deterministic. Replay-of-record
   is the discipline: persisted candidates are the source of truth, the
   LLM call is logged but not re-executed on replay. Ingest cursors are
   advanced atomically with persistence so a crashed mid-batch run does
   not double-process source items.
3. **Degraded operation.** The firm runs fine for weeks without this
   agent. Without it, no new world-changers enter the funnel; existing
   promoted graphs continue to be maintained by the Mapper. If a source
   class becomes unavailable (e.g., EDGAR outage), the agent emits a
   `operations.health-anomaly` and proceeds with the remaining sources.
   The triangulation rule (>=2 independent source classes) means a
   single-source outage degrades throughput but does not corrupt output.

## Sprint scope

- Month 2: Ingest plumbing for SEC EDGAR, arXiv, USPTO; daily synthesis
  pass with a hand-curated archetype list; output to a static review
  page. No Qdrant yet.
- Month 3: Qdrant integration, conference keynote ingest, weekly
  re-scoring pass, Mike's promotion workflow wired to the Mapper.
- Month 4: USASpending / SAM.gov ingest, citation graph inputs,
  trailing-90d kill-rate reporting against the full denominator.

## Deferred

- Multi-language source ingest (CN / KR / JP filings and patents) —
  high-value but out of scope for sprint.
- LLM-driven evidence-trail summarization beyond the candidate card.
- Automatic surfacing of candidates to external review channels.
- Active learning loop on Mike's promote/kill decisions.

## Open questions

- **What the agent CANNOT do, explicitly:**
  - It cannot judge whether a "world-changer" will actually change the
    world. The promotion gate is Mike, and the validation gate is
    real-world observation of kill criteria.
  - It cannot detect novel archetypes outside its allowed list. A
    genuine paradigm shift whose archetype is not yet documented will
    be missed. The mitigation is Mike's periodic review of the
    `seen-not-proposed` bucket.
  - It cannot reliably distinguish marketing-driven hype from
    technically credible work in fields outside its source list. The
    triangulation rule is a partial defense, not a complete one.
  - It cannot output calibrated numeric probabilities and is forbidden
    from doing so. `confidence` is ordinal (low/medium/high) with
    documented calibration semantics.
- **Threshold tuning:** 0.88 cosine novelty cutoff, top-10 proposed per
  batch, "low/medium/high" calibration. All are first-guess values.
  Blocks: avoiding both noise floods and signal starvation. Owner: Mike,
  after first month.
- **How to weight government-contract sources:** USASpending and SAM.gov
  are noisy and politically modulated. Initial weight is low; revisit
  after first quarter of operation. Owner: Mike.
- **Hit-rate floor before agent is itself "promoted" to influence sizing:**
  The trading floor should not act on Tech Watcher output until the
  agent has been observed long enough to estimate its false-positive
  rate honestly. Blocks: Universe Curator's willingness to trust
  Mapper outputs derived from Tech Watcher promotions. Owner: Mike,
  end of sprint.
