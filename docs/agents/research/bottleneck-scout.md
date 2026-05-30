# Bottleneck Scout

**Department:** Research (Structural Funnel — Step 3)
**LLM tier:** `cloud-default` primary for transcript/preprint
synthesis and the "is this bottleneck binding?" reasoning step.
`cloud-judgment-heavy` for the weekly cross-graph synthesis pass that ranks
candidate bottlenecks across all active Mapper graphs (judgment is load-bearing
there). `local-classification` for bulk transcript filtering and language-pattern flag
detection (the "supply constrained / yield-limited / lead times
extending" pre-pass). Migration target: keep the binding-judgment pass on a cloud
tier indefinitely; move bulk filtering fully local once shadow
evaluation passes. See `docs/infrastructure/llm-routing.md` and `docs/infrastructure/llm-registry.md`.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-05-30
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Bottleneck Scout is step 3 of the three-step Research funnel
(ADR-0007). Tech Watcher finds candidate world-changers; the
Infrastructure Mapper builds their dependency graphs; the Bottleneck
Scout walks those *already-promoted* graphs and looks for the layer that
is hitting a physical or economic wall, and identifies what replaces
it. The trading formula the firm bets on is:

**world-changer × saturating layer = forced substitute = trade**

This agent is the engine that finds the saturating layer. Without it,
the Mapper's graphs are a static list of vendors and the firm has no
mechanism to surface the *change of layer* that produces the actual
trade. With it, the firm has a chance at the structural-analysis edge
Burry / Eisman / Asness-style investors use: not "which company wins
the megatrend" but "which constraint is about to bind, and who supplies
the only available substitute."

This agent exists because most retail and even most institutional
attention focuses on the *world-changer itself* (NVIDIA, AI, GLP-1s)
and not on the *binding constraint inside its supply chain*. The
binding constraint is where the alpha lives, because (a) it has fewer
substitutes, (b) it is less crowded, (c) it tends to be reported by
engineering-trade sources rather than by financial media, and (d) it
has measurable physical limits which produce hard kill criteria.

**The edge is in throughput AND kill rate, not just throughput.** Most
candidate bottlenecks identified by this agent are not binding yet.
Some are speculative engineering concerns that will be solved before
they matter. Some are real constraints that get extended by a process
node, a new material, or a redesign. The design target kill rate on
detected bottlenecks is also 80%+ before the `binding` transition. A
Bottleneck Scout that flips most of its candidates to `binding` is
either overfitting to recent supply-chain news or is being read
selectively — both should be treated as bugs.

**What this agent cannot do.** It cannot predict the exact date a
bottleneck binds. It can identify that the conditions for binding are
forming (physical limit citations, supply-constrained language,
patent-pivots to alternative approaches, capex inadequacy) and it can
name the kill criteria that would prove the bottleneck binding or
broken. The decision to act on a binding bottleneck is a downstream
function: the Mapper updates the graph, the Universe Curator proposes
universe changes, Mike approves, the trading floor expresses the
trade. The Bottleneck Scout is upstream of all of that.

## Trigger

- **Schedule:**
  - Hourly: incremental pull of new engineering conference talks (Hot
    Chips, OFC, ISSCC, SC, GTC engineering tracks) when in season;
    incremental pull of new earnings call transcripts; incremental pull
    of new arXiv preprints in cs.AR, cs.NI, cond-mat.
  - Daily 07:00 ET: scan over the past 24h ingest, cross-referenced
    against the active Infrastructure Mapper graphs.
  - Weekly Sunday 23:00 ET: full cross-graph synthesis with the
    higher-tier model. Re-scores all active bottleneck candidates and
    proposes new ones.
- **Event:** Subscribes to `research.graphs-updated` from the
  Infrastructure Mapper — when a graph adds or modifies layers, the
  agent runs a targeted scan on the new layers within 30 minutes.
- **On-demand:** Mike-initiated `research.bottleneck-scout-request`
  with optional constraints (graph id, layer role, theme).

## Cross-references

**Depends on:** Infrastructure Mapper (graphs to scan against — without
promoted graphs this agent has no scope), Tech Watcher (indirectly, via
the promoted world-changer list that seeds the Mapper), the source
ingestion plumbing, the shared envelope library.
**Depended on by:** Infrastructure Mapper (consumes detected
bottlenecks to insert replacement-layer candidates), Mike's review
queue (validates `detected` → `validated` and reviews `binding`
transitions), Universe Curator (indirect, via the Mapper).
**Related ADRs:** ADR-0006 (envelope), ADR-0007 (Research funnel —
pending).
**Related architecture sections:** `docs/02-architecture.md` §Research
Department, §Universe Curator interface.

## Inputs

| Source | Type | Description |
|---|---|---|
| Engineering conference transcripts | HTTP pull / repo cache | Hot Chips, OFC, ISSCC, SC, GTC engineering tracks. ASR fallback if transcripts unavailable |
| SEC EDGAR (earnings call transcripts via 8-K and third-party feeds) | HTTP pull | Quarterly earnings calls for every ticker in any active Mapper graph |
| SEC EDGAR (10-K/Q capex disclosures) | HTTP pull | Capex tables; segment commentary on supply, capacity, lead times |
| arXiv (cs.AR, cs.NI, cond-mat) | HTTP pull | Preprints proposing alternative architectures, fabrics, materials |
| USPTO PatentsView | HTTP pull | Patents by assignee in the active Mapper graphs; weighted toward CPC class shifts |
| Industry roadmaps | Repo cache + manual ingest | IRDS, OCP, ASHRAE, OIF — physical-limit citations |
| Redis: `research.graphs-updated` | Event | Trigger for targeted scans on new layers |
| PostgreSQL: `research.graphs` | Query | Active world-changer graphs and their nodes (the scan scope) |
| PostgreSQL: `research.bottlenecks` | Query | Prior bottleneck candidates and statuses (anti-duplication, hit-rate denominator) |
| Qdrant: `bottleneck_corpus` | Semantic search | Embeddings of prior bottleneck candidates and source evidence |
| Repo: `docs/research/bottleneck-archetypes.md` | File read | Allowed bottleneck archetypes (signal-integrity, thermal, power-density, supply-chain capacity, yield, regulatory) |

## Processing

1. **Ingest cursor advance.** Per-source cursor advance and raw persist
   (shared plumbing with Tech Watcher where possible).
2. **Language-pattern pre-pass (local LLM).** The `local-classification` tier scans new
   transcripts and filings for flagged phrases — "supply constrained",
   "yield-limited", "lead times extending", "capacity expansion delayed",
   "approaching physical limit", "co-packaged", "alternative
   architecture", "thermal envelope". Hits are tagged with the source
   item and the ticker.
3. **Cross-reference against active graphs.** For each tagged hit,
   check whether the source ticker (or assignee, or paper-affiliated
   institution) is on a node in an active Mapper graph. Hits not on any
   active graph are stored but do not become candidates — the agent's
   scope is explicitly *bottlenecks on a critical path of an
   already-promoted world-changer.* This is the rule that prevents the
   agent from drowning in general supply-chain news.
4. **Cluster by (graph, layer, physical/economic limit).** A candidate
   bottleneck is a cluster of evidence pointing at a single named limit
   inside a single layer of a single graph. Multi-graph bottlenecks
   (e.g., CoWoS capacity appearing in the AI graph and the HPC graph)
   are tracked as one candidate with multiple graph attachments.
5. **Synthesize candidate (cloud LLM).** For each cluster, draft a
   candidate proposal in a strict JSON schema:
   - `name`: short identifier
   - `world_changer_id`: graph it constrains. Multiple if the same
     bottleneck appears on multiple graphs.
   - `bottleneck_layer_role`: the layer role inside the graph (e.g.
     `interconnect`, `advanced-packaging`, `memory`, `cooling`,
     `power-supply`, `feedstock`)
   - `physical_or_economic_limit`: the *named* limit (e.g. "224Gbps
     PAM4 copper signal-integrity reach", "HBM3e per-stack bandwidth
     ceiling", "TSMC CoWoS-L capacity through FY27", "rack power
     density at 130kW")
   - `evidence`: array of `{source_class, source_ref, weight_reason}`.
     Triangulation rule: >=2 independent source classes required, with
     at least one engineering-trade source (conference talk, arXiv,
     IRDS/OCP/ASHRAE roadmap, patent shift) and at least one financial
     source (earnings transcript or filing). Single-class evidence
     produces a `seen-not-proposed` record only.
   - `candidate_replacement_layers`: array of `{name, public_tickers,
     evidence_for_substitution}`. Tickers must be public and currently
     tradable. Private companies are noted but not surfaced for trade.
   - `timeline_to_binding`: ordinal — `forming` / `near` (1-4 quarters)
     / `binding-now` / `solved-or-deferred`. **Not a calendar date.**
     The agent is forbidden from inventing dates it cannot justify.
   - `kill_criteria`: array of explicit, observable conditions that
     would retract the bottleneck. Each is a named published metric
     crossing a named threshold. Examples: "TSMC reports CoWoS-L
     capacity exits FY26 at >=3x FY24 baseline", "OFC 2027 papers
     demonstrate 448Gbps PAM4 copper >2m reach at SI margins
     production-acceptable", "rack thermal density designs above
     150kW deployed in >=2 hyperscalers without liquid cooling".
   - `validation_horizon`: the date by which at least one kill
     criterion is observable. Unfalsifiable candidates rejected.
6. **Validate locally.** Deterministic validator drops proposals that
   fail schema; omit kill criteria; lack a critical-path link to an
   active graph; cite only one source class; reference no public tickers
   in candidate replacement layers (a bottleneck no public ticker
   benefits from is not actionable for the firm and is stored but not
   surfaced); or invent calendar dates without justification.
7. **Score and rank.** Surviving candidates scored on (a) triangulation
   breadth, (b) recency-weighted evidence count, (c) graph criticality
   (how many downstream Mapper nodes depend on the layer),
   (d) substitutability narrowness (a bottleneck with one public ticker
   in the replacement layer is more actionable but more concentrated;
   the score surfaces both, the Risk Officer downstream decides
   sizing).
8. **Publish detection.** Each detected candidate emits one
   `research.bottleneck-detected` event with ADR-0006 envelope,
   payload-by-reference.
9. **Validation transition.** A `detected` candidate moves to
   `validated` when (a) a third independent source class confirms the
   limit, OR (b) Mike manually validates after review. Emits
   `research.bottleneck-validated`.
10. **Binding transition.** A `validated` candidate moves to `binding`
    when the agent detects either (a) explicit corporate guidance
    citing the limit as a constraint on shipment / capacity / margin,
    or (b) a public price/lead-time signal crossing a documented
    threshold (e.g. CoWoS lead times >40 weeks, optical transceiver
    lead times >30 weeks). Emits `research.bottleneck-binding`. This is
    the event the Mapper acts on most aggressively.
11. **Kill transition.** A candidate moves to `killed` when any kill
    criterion fires. Emits `research.bottleneck-killed` with the firing
    criterion attached. Mike can override (manual revive) but the
    default is to trust the criterion.
12. **Daily summary.** End-of-day rollup with counts by transition,
    trailing-90d kill rate against the full denominator (same
    survivorship-bias discipline as Tech Watcher), and a flag if the
    binding rate exceeds 25% of detected — which would be a warning
    sign of selection bias, not a celebration.

## Canonical worked example (HISTORICAL VALIDATION CASE)

This case is the agent's primary backwards-test target. The system MUST
be able to reproduce this finding when re-run against data available
through August 2024, before the optical interconnect rally.

- **World-changer:** NVIDIA AI compute buildout (promoted as a
  world-changer by Tech Watcher in 2023; Mapper graph active through
  2024).
- **Bottleneck layer role:** interconnect (server-to-server and
  rack-to-rack within AI data centers).
- **Physical-or-economic limit:** 224Gbps PAM4 copper signal-integrity
  reach. At 224Gbps lane rates, passive copper cable loss and crosstalk
  push reach below the distances required for modern AI rack and
  inter-rack topologies. The wall is physical (Shannon-limit-adjacent
  for the channel) and is documented in IEEE 802.3 and OIF working
  group material.
- **Evidence classes (Aug 2024 vintage):** OFC 2024 papers on
  co-packaged optics and linear pluggable optics; arXiv preprints
  proposing optical fabrics for AI clusters; NVIDIA, Arista, and
  Broadcom commentary referencing optical-interconnect roadmaps;
  hyperscaler power and cooling disclosures consistent with denser
  AI clusters that copper cannot interconnect at 224G.
- **Candidate replacement layers:**
  - Co-packaged optics (CPO): LITE, COHR, FN, AOI as component
    suppliers; ANET, CSCO, NVDA as integrators.
  - Linear pluggable optics (LPO): same supplier set, partially.
  - Silicon photonics integration inside switch and accelerator silicon
    (ANET, CSCO, NVDA roadmap items).
- **Public tickers positioned to benefit (at Aug 2024 vintage):**
  LITE, COHR, FN, AOI for the optical components; ANET and CSCO for
  the switch integration; NVDA for the in-silicon photonics
  capability.
- **Timeline-to-binding (Aug 2024):** `near` — bottleneck binds during
  the 224G generation of NVIDIA AI deployments, which the agent
  scopes as 1-4 quarters out from Aug 2024.
- **Kill criteria (Aug 2024 vintage):**
  - "OFC 2025 demonstrates production-grade 224Gbps PAM4 copper at
    >=2m reach with acceptable BER margins" — would kill the
    optical substitution thesis.
  - "Hyperscaler AI rack designs published with majority copper
    interconnect at 224G and no optical CPO content" — would kill.
  - "Optical component supplier guidance shows >20% YoY revenue
    decline in datacom segment for two consecutive quarters" —
    would kill the demand-pull thesis.
- **Outcome (post-Sept 2024):** Optical interconnect names rallied
  meaningfully. LITE in particular reflected this thesis. The Sept
  2024 LITE move is the canonical historical case the firm uses to
  judge whether the methodology reproduces.

**Backwards-test methodology:** the system is fed only sources
available on or before Aug 31, 2024. It must independently produce a
candidate matching this case with the bottleneck named, the
replacement layers identified, and the public ticker set overlapping
LITE / COHR / FN / AOI / ANET / CSCO / NVDA. A partial match (right
bottleneck, wrong substitution layer) is a partial pass and is more
informative than a full pass — the Cisco-1999 lesson (right
world-changer, wrong layer = loss) means substitution-layer accuracy
matters more than world-changer accuracy at this stage.

**Forward-test (the real edge claim):** the firm runs the same agent
from May 2026 forward and tracks whether the methodology produces
calls of similar quality on bottlenecks not yet visible in
consensus. The backwards-test demonstrates the methodology *can*
identify the case; only the forward-test demonstrates it *does* under
real, non-cherry-picked conditions. Mike should weight the forward
record much more heavily than the historical reproduction.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `research.bottleneck-detected` | Event | One per new candidate |
| Redis stream: `research.bottleneck-validated` | Event | Transition to validated status |
| Redis stream: `research.bottleneck-binding` | Event | Transition to binding status — Mapper acts on this most aggressively |
| Redis stream: `research.bottleneck-killed` | Event | A kill criterion fired or the candidate was superseded |
| Redis stream: `research.bottleneck-scout-summary` | Event | Daily rollup |
| PostgreSQL: `research.bottlenecks` | Insert/update | Full candidate record with status transitions, append-only history table |
| PostgreSQL: `research.bottleneck_evidence` | Insert | Evidence rows, append-only |
| Qdrant: `bottleneck_corpus` | Upsert | Embeddings |
| Repo: `docs/research/bottlenecks/<id>.md` | File write (sandbox branch) | Human-readable card. Never auto-merged. |

Every event carries the ADR-0006 envelope, including the
`correlation_id` of the originating world-changer's promotion event so
an investigator can trace bottleneck → world-changer → original Tech
Watcher candidate.

## LangGraph structure

**Nodes:**
- `ingest` — per-source cursor advance + raw persist
- `language-pattern-prepass` — local LLM phrase tagging
- `graph-cross-ref` — filter to ticker hits on active graph nodes
- `cluster` — by (graph, layer, named limit)
- `synthesize` — cloud LLM candidate draft
- `validate` — schema + policy checks
- `score-rank` — ordinal scoring
- `persist-emit` — write, publish
- `transition-scan` — periodic re-evaluation for validated/binding/killed transitions

**Key edges:**
- `ingest` → `language-pattern-prepass` → `graph-cross-ref` → `cluster` → `synthesize` → `validate` → `score-rank` → `persist-emit`
- `transition-scan` → `validate` → `persist-emit` (separate scheduled trigger)

## State

| What | Store | Notes |
|---|---|---|
| Per-source ingest cursors | PostgreSQL `research.ingest_cursors` | Shared plumbing with Tech Watcher |
| All bottleneck candidates | PostgreSQL `research.bottlenecks` | Append-only history. Kill-rate denominator. |
| Per-candidate evidence rows | PostgreSQL `research.bottleneck_evidence` | Append-only |
| Embeddings | Qdrant `bottleneck_corpus` | Indexed for dedup |
| Daily batch records | PostgreSQL `research.bottleneck_batches` | Prompt hash, model, n_detected, n_killed, n_binding |
| Backwards-test results | Repo: `docs/research/backwards-tests/<run-id>.md` | Each historical reproduction run logged |

## Failure behavior

1. **Containment.** A bad detection costs Mike's review attention and
   Mapper downstream compute. A bad `binding` transition is more
   expensive because the Mapper will respond by reweighting the
   replacement layer and the Universe Curator may propose universe
   changes; Mike's manual gate on universe changes is the
   backstop. A bad kill (real bottleneck silenced) is recoverable via
   weekly re-scan and Mike's manual revive.
2. **Replay safety.** LLM calls are non-deterministic. Persisted
   candidates are the source of truth. Replay-of-record discipline.
   Status transitions are append-only in a history table so a
   transition replay does not corrupt the audit trail.
3. **Degraded operation.** The firm runs fine for weeks without this
   agent. Without it, no replacement-layer signals reach the Mapper;
   existing graphs become stale on the substitution dimension but
   continue to operate. If conference / earnings transcript ingest is
   unavailable, the agent emits a `operations.health-anomaly` and
   proceeds with the remaining sources — but the triangulation rule
   may cause throughput to drop sharply, which is correct behavior.

## Sprint scope

- Month 2: Ingest plumbing for conference transcripts (manually-fed
  initially), arXiv cs.AR / cs.NI, earnings call transcripts for the
  initial 50-name universe. Manual graph-cross-ref via a hand-curated
  graph table.
- Month 3: Automated graph-cross-ref against live Mapper graphs.
  Validated and binding transitions implemented. First backwards-test
  run against the Sept 2024 LITE canonical case.
- Month 4: Patent CPC class-shift detection. Industry roadmap
  ingest. Daily trailing-90d kill-rate reporting against the full
  denominator. Re-run backwards-test as methodology evolves.

## Deferred

- Non-English engineering source ingest.
- Automatic supplier-network inference from filings (left to the
  Mapper).
- Quantitative supply/demand modeling — the agent flags qualitative
  binding conditions, not quantitative imbalance estimates.

## Open questions

- **What the agent CANNOT do, explicitly:**
  - It cannot predict the calendar date a bottleneck binds. Timeline
    is ordinal.
  - It cannot distinguish a real physical wall from a temporary
    capacity issue that gets solved by a process node or a redesign,
    *except* via the explicit kill criteria. The criteria are the
    discipline.
  - It cannot identify bottlenecks outside its known archetype list.
    Novel constraint types require Mike's manual archetype expansion.
  - It cannot identify bottlenecks on world-changers that have not
    been promoted by Tech Watcher and graphed by the Mapper. By
    design — the scoping is upstream — but this means a real
    bottleneck on a world-changer the firm has not adopted is
    invisible.
  - It cannot output calibrated probabilities and is forbidden from
    doing so. Timeline is ordinal (`forming` / `near` / `binding-now`
    / `solved-or-deferred`).
- **What counts as an "independent source class":** Initial mapping
  treats engineering conferences, earnings calls, filings, arXiv,
  patents, and industry roadmaps as six classes. An earnings call and
  the same company's 10-K are NOT independent. Blocks: the
  triangulation rule's strictness. Owner: Mike, after first month.
- **Binding-rate ceiling alarm threshold:** 25% binding/detected as a
  selection-bias warning is a first guess. Blocks: agent self-honesty
  reporting. Owner: Mike.
- **Backwards-test pass criteria:** What counts as a "partial pass"
  vs. a "full pass" on the Sept 2024 LITE case needs a written
  rubric before the test runs, not after. Blocks: avoiding
  retrospective goal-shifting. Owner: Mike, before Month 3.
- **Cross-graph weighting:** A bottleneck that hits multiple graphs
  (e.g. CoWoS in AI and HPC) is more important, but the scoring
  formula is unspecified. Blocks: rank ordering across graphs.
  Owner: Mike, end of sprint.
