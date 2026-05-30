# Infrastructure Mapper

**Department:** Research (Structural Funnel — Step 2)
**LLM tier:** `cloud-default` primary for initial graph
construction from a newly promoted world-changer and for synthesis when
incorporating new replacement layers from Bottleneck Scout.
`cloud-judgment-heavy` for the once-per-graph "deep graph build" pass that produces
the first full layer enumeration after Mike promotes a world-changer (judgment is
load-bearing on first build). Routine graph maintenance (node confirmation updates,
kill-criterion evaluation, evidence-link refresh) starts on a cloud tier in the
sprint and migrates to deterministic Python rules as the maintenance
operations become well-understood. Migration target: deterministic
maintenance plus periodic cloud-tier audit pass by end of post-sprint
month 6. See `docs/infrastructure/llm-routing.md` and `docs/infrastructure/llm-registry.md`.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-05-30
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Infrastructure Mapper is step 2 of the three-step Research funnel
(ADR-0007). For each Mike-promoted world-changer from Tech Watcher,
the Mapper builds and maintains a full dependency graph of suppliers,
enablers, contractors, and downstream beneficiaries. The aggregate of
all active graphs IS the firm's trading universe. The Universe Curator
does not maintain a curated list independently; it consumes the
Mapper's aggregation events.

This agent exists because the structural edge claim of the firm
(ADR-0007) is that the trading universe should be a *derived artifact*
of the underlying world-changer dependency graphs. If the Mapper is
weak, the universe degenerates into "tickers that show up in the
news" and the firm loses its claim to a structural lens. If the Mapper
is strong, the universe automatically refreshes as substitution layers
move and as old dependencies break.

**The Cisco-1999 lesson (load-bearing).** Cisco was on the right
world-changer (the internet). Cisco's investors lost real money
between 2000 and 2002 because the *layer was wrong* — switching/routing
margin compressed under commodification, while the actual binding
constraints moved to fiber, then to optics, then to data center
buildout, then to compute. The Mapper's job is not to enumerate every
vendor associated with a world-changer. The Mapper's job is to
identify which layer is *on the critical path right now*, mark layers
that are at risk of commodification or substitution, and surface
layer-shift signals from the Bottleneck Scout immediately when they
arrive.

A Mapper that emits a universe full of historically-correct-but-now-
wrong layers (the 1999 routing equipment vendors of the AI graph,
whatever those turn out to be) is worse than no Mapper at all,
because it gives the firm the illusion of structural reasoning while
delivering Cisco-1999 trades. The kill-criteria discipline and the
Bottleneck Scout integration are the primary defenses; Mike's manual
review of every layer addition above a confidence threshold is the
backstop.

**What this agent cannot do.** It cannot tell whether a given layer
will be the winning layer over a multi-year arc. It can record the
evidence that places each layer on the critical path *as of the most
recent observation* and can downgrade or remove layers when the
evidence breaks. Time horizon and conviction are downstream concerns
for the Risk Officer and the Decision Maker. The Mapper provides the
topology and the per-node evidence freshness, not a price target.

## Trigger

- **Schedule:**
  - Daily 06:00 ET: per-graph maintenance pass. Refresh evidence links,
    re-evaluate kill criteria on every node, surface stale-evidence
    flags.
  - Weekly Sunday 23:30 ET: cross-graph aggregation pass. Recompute
    universe membership, emit aggregated diffs to the Universe
    Curator.
- **Event:**
  - Subscribes to `research.world-changer-promoted` — on Mike's
    promotion of a new world-changer, triggers a deep graph build
    within 2 hours.
  - Subscribes to `research.bottleneck-detected`,
    `research.bottleneck-validated`,
    `research.bottleneck-binding`,
    `research.bottleneck-killed` — on each, evaluates whether the
    referenced graph needs a layer add, layer reweight, or layer
    remove.
  - Subscribes to `research.world-changer-killed` — on a world-changer
    kill, retires the corresponding graph and emits an aggregated
    `universe.proposed-remove` for every node that is uniquely
    supported by that graph.
  - Subscribes to `intelligence.signal` filtered to earnings releases
    and 8-K events for tickers in active graphs — triggers a node
    re-evaluation pass within 30 minutes.
- **On-demand:** Mike-initiated `research.mapper-request` (e.g.,
  rebuild graph X, audit node Y, evaluate layer Z).

## Cross-references

**Depends on:** Tech Watcher (promoted world-changers seed the
graphs), Bottleneck Scout (replacement-layer signals drive the most
important graph updates), Intelligence Department (earnings and 8-K
signals trigger node re-evaluations), shared envelope library,
source ingestion plumbing.
**Depended on by:** Universe Curator (consumes aggregated proposals
and is the gate to actual universe changes), Bottleneck Scout
(consumes graph state to scope its scans), Mike (reviews every layer
addition above a threshold), Decision Maker (uses node layer-role and
confidence as a sizing input, indirectly via the Risk Officer).
**Related ADRs:** ADR-0006 (envelope), ADR-0007 (Research funnel —
pending).
**Related architecture sections:** `docs/02-architecture.md` §Research
Department, §Universe Curator interface.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `research.world-changer-promoted` | Event | New graph seed |
| Redis: `research.world-changer-killed` | Event | Graph retirement signal |
| Redis: `research.bottleneck-detected` / `-validated` / `-binding` / `-killed` | Event | Layer modification triggers |
| Redis: `intelligence.signal` (earnings, 8-K filtered) | Event | Node re-evaluation triggers |
| PostgreSQL: `research.world_changers` | Query | Promoted world-changer records |
| PostgreSQL: `research.bottlenecks` | Query | Active bottleneck candidates referencing each graph |
| PostgreSQL: `research.graphs` | Query | Existing graph state |
| SEC EDGAR (10-K/Q/8-K) | HTTP pull / cached | Per-node evidence refresh: segment commentary, customer concentration, supply-chain disclosures |
| Equity reference data | HTTP pull / cached | Ticker validity, listing status, market cap thresholds for tradability |
| Qdrant: `graph_node_corpus` | Semantic search | Embeddings for dedup and prior-art lookup on layer roles |
| Repo: `docs/research/layer-role-taxonomy.md` | File read | Canonical layer-role names (e.g. `fab`, `litho`, `memory`, `cooling`, `power-supply`, `interconnect`, `advanced-packaging`, `feedstock`, `regulatory-enabler`, `downstream-beneficiary`) |

## Processing

### Deep graph build (on `research.world-changer-promoted`)

1. **Fetch the promoted world-changer record**, including Tech
   Watcher's `dependency_graph_seed` if present.
2. **LLM-assisted layer enumeration (cloud, higher tier).** Given the
   world-changer thesis and any seed, propose the full set of layers
   required for the world-changer to play out, using only canonical
   layer-role names from the taxonomy. The LLM is explicitly prompted
   with the Cisco-1999 lesson and asked to mark each layer with a
   `critical_path_status` of one of: `on-critical-path`,
   `enabling-but-substitutable`, `commodified-or-at-risk`,
   `downstream-beneficiary`. Marketing-tier layer descriptions are
   rejected by the validator.
3. **Per-layer ticker enumeration.** For each layer, propose public
   tickers positioned in that role. Each ticker proposal must carry:
   `ticker`, `layer_role`, `confidence` (ordinal: low / medium /
   high), `last_confirmed_evidence_ref` (link to a primary source
   confirming the role within trailing 12 months), `kill_criteria`
   (named published metrics crossing named thresholds that would
   remove this ticker from this layer).
4. **Validate locally.** Drop nodes that fail schema; lack evidence
   ref; lack kill criteria; reference invalid or untradable tickers;
   use un-allowed layer roles; or invent numeric probabilities.
5. **Hold for Mike.** Built graphs above a threshold size (default 8
   nodes) are written to a `pending-review` state with a generated
   review card and surfaced to Mike's queue. Below threshold, graphs
   auto-activate but every node addition is logged for Mike's daily
   briefing.
6. **Emit add events.** On Mike's approval (or auto-activation), emit
   one `research.graphs-added` event per node, plus a single
   `research.graphs-initialized` event for the graph.

### Maintenance pass (daily, per active graph)

1. **Refresh per-node evidence.** For each node, check whether the
   `last_confirmed_evidence_ref` is older than the freshness
   threshold (default 180 days). If so, attempt to refresh from
   recent filings, earnings transcripts, or intelligence signals.
   If no refresh available, flag the node `stale-evidence`.
2. **Re-evaluate kill criteria.** For each node, check the node's
   kill criteria against latest available data. If any criterion
   fires, the node transitions to `downgraded` (confidence dropped
   one level) on first fire, or `removed` on second fire or hard
   criterion. Emit `research.graphs-updated` or
   `research.graphs-removed` accordingly.
3. **Cross-graph dedup.** A ticker present in multiple graphs gets a
   per-graph evidence ref and per-graph confidence — *not* a single
   global record. A ticker's relevance to the universe is the max of
   its per-graph confidences. This is the rule that keeps the
   Cisco-1999 failure mode visible: a ticker that is `high` confidence
   in a `commodified-or-at-risk` layer of a graph should still be
   downweighted at the universe level.

### Bottleneck-driven update (on Bottleneck Scout events)

1. **On `research.bottleneck-detected`:** Note the candidate
   replacement layer but do not yet add nodes to the graph. Detection
   is too early; the agent records it and surfaces it on the daily
   summary.
2. **On `research.bottleneck-validated`:** Add the candidate
   replacement layer's tickers as `pending-review` nodes with
   `low` confidence. Surfaces to Mike for approval.
3. **On `research.bottleneck-binding`:** Promote any
   `pending-review` replacement-layer nodes to `active` with `medium`
   confidence. Downgrade the bottlenecked layer's nodes one
   confidence level and mark them `critical_path_status =
   commodified-or-at-risk`. Emit per-node `research.graphs-updated`
   events. This is the most consequential event class the agent
   processes; Mike's daily briefing must surface every binding
   transition.
4. **On `research.bottleneck-killed`:** Reverse the corresponding
   layer changes if they were not yet Mike-approved. If already
   approved and active, mark the replacement-layer nodes
   `under-review` and surface to Mike for explicit kill confirmation.

### Aggregation pass (weekly)

1. **Compute universe delta.** Walk all active graphs, union the
   `active` ticker set, dedup, apply tradability and market-cap
   filters. Compare to the prior week's universe and emit
   `universe.proposed-add` and `universe.proposed-remove` events to
   the Universe Curator. The Curator (not the Mapper) is responsible
   for the final universe write.
2. **Emit aggregated summary.** End-of-week event with counts of
   graphs active, nodes active, nodes per layer role, average node
   confidence, count of `stale-evidence` and
   `commodified-or-at-risk` nodes. Surfaces in the weekly review.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `research.graphs-initialized` | Event | New graph activated, world-changer-id keyed |
| Redis stream: `research.graphs-added` | Event | New node added to a graph |
| Redis stream: `research.graphs-updated` | Event | Node confidence, evidence, or critical-path status changed |
| Redis stream: `research.graphs-removed` | Event | Node removed from a graph |
| Redis stream: `research.graphs-retired` | Event | Entire graph retired (world-changer killed) |
| Redis stream: `universe.proposed-add` | Event | Aggregated universe addition proposal, consumed by Universe Curator |
| Redis stream: `universe.proposed-remove` | Event | Aggregated universe removal proposal, consumed by Universe Curator |
| Redis stream: `research.mapper-summary` | Event | Daily + weekly rollups |
| PostgreSQL: `research.graphs` | Insert/update | Graph header records (world-changer-id keyed) |
| PostgreSQL: `research.graph_nodes` | Insert/update | Per-node records with full history table for append-only audit |
| PostgreSQL: `research.graph_node_history` | Insert | Every node status transition, append-only |
| Qdrant: `graph_node_corpus` | Upsert | Embeddings of node descriptions for dedup |
| Repo: `docs/research/graphs/<world-changer-id>.md` | File write (sandbox branch) | Human-readable graph card, refreshed weekly. Never auto-merged to main; Mike pulls into main during weekly review. |

Every event carries the ADR-0006 envelope and the originating
`correlation_id` (the world-changer's promotion correlation_id for
build events; the bottleneck event's correlation_id for
bottleneck-driven updates) so a trace can run end-to-end from
universe change back to original Tech Watcher candidate.

## LangGraph structure

**Nodes:**
- `seed-from-promotion` — fetch promoted world-changer + seed
- `enumerate-layers` — cloud LLM layer enumeration
- `enumerate-tickers` — cloud LLM per-layer ticker proposal
- `validate` — schema + policy + taxonomy checks
- `await-review` — gate for graphs above threshold size
- `maintenance-refresh` — daily evidence refresh per node
- `evaluate-kill-criteria` — per-node criterion checks
- `bottleneck-apply` — apply Scout event to graph
- `aggregate-universe` — weekly union and diff
- `persist-emit` — write to stores, publish events

**Key edges:**
- `seed-from-promotion` → `enumerate-layers` → `enumerate-tickers` → `validate` → `await-review` → `persist-emit`
- `maintenance-refresh` → `evaluate-kill-criteria` → `persist-emit` (daily)
- `bottleneck-apply` → `validate` → `persist-emit` (event-driven)
- `aggregate-universe` → `persist-emit` (weekly)

## State

| What | Store | Notes |
|---|---|---|
| Graph headers | PostgreSQL `research.graphs` | One row per world-changer, status `pending-review` / `active` / `retired` |
| Graph nodes | PostgreSQL `research.graph_nodes` | Current node state per (graph, ticker, layer_role) triple |
| Node history | PostgreSQL `research.graph_node_history` | Append-only transitions for audit |
| Evidence refs | PostgreSQL `research.graph_node_evidence` | Append-only evidence rows with source class and timestamp |
| Embeddings | Qdrant `graph_node_corpus` | Dedup |
| Weekly aggregation snapshots | PostgreSQL `research.universe_snapshots` | One row per weekly aggregation; enables historical reconstruction of the derived universe |

## Failure behavior

1. **Containment.** A bad graph node does not move money — the
   Universe Curator gates universe changes; the Risk Officer gates
   sizing; Mike gates real promotions. A bad node propagates as far
   as the Curator's review queue, which is the intended stop point.
   A bad bottleneck-driven layer update is more dangerous because
   the firm's responsiveness to binding events is what generates the
   alpha; Mike's manual review of every binding transition is the
   backstop. A graph initialization failure on a promoted world-
   changer leaves the world-changer "promoted but ungraphed," which
   the Health Monitor must surface.
2. **Replay safety.** Graph build and maintenance steps are
   idempotent at the persistence layer: nodes are keyed by (graph,
   ticker, layer_role) and node history is append-only. A crashed
   build can be re-run; a re-run will produce the same persistent
   state modulo LLM non-determinism in newly-discovered layers
   (logged as drift in the build batch record). Aggregation passes
   are pure functions of current `research.graph_nodes` state and
   are fully safe to replay.
3. **Degraded operation.** The firm can run for one to two weeks
   without this agent before the universe goes stale relative to
   bottleneck events. Beyond that, the universe loses its
   responsiveness to binding-layer shifts, which is the agent's
   primary value. If the agent is offline at the time a
   `research.bottleneck-binding` event fires, the Health Monitor
   surfaces the unconsumed event and Mike can manually apply the
   layer update via the on-demand interface.

## Sprint scope

- Month 2: Hand-built graph schema; one canonical graph (NVIDIA AI
  buildout) seeded manually; basic maintenance pass refreshing
  evidence links. No bottleneck integration yet.
- Month 3: LLM-assisted enumeration; multiple active graphs (AI,
  hyperscaler power → nuclear, GLP-1 → CDMOs, rack density →
  liquid cooling); bottleneck event subscription wired; first
  aggregated universe diffs emitted to the Universe Curator (still
  Mike-gated).
- Month 4: Per-node kill-criterion evaluation; weekly aggregated
  reports; backwards-test of the agent against the Aug 2024 LITE
  case in cooperation with Bottleneck Scout (does the Mapper add the
  optical interconnect layer to the AI graph and downgrade the
  copper interconnect layer correctly when fed Aug 2024 data?).

## Deferred

- Private company nodes (tracked for context only, never surfaced
  for trade in the sprint).
- International tickers and ADRs (sprint focus is US-listed).
- Quantitative graph centrality metrics — surfaced as
  `nodes-depend-on-this-layer` count only, not formal centrality.
- Automatic graph-merge across world-changers when shared layers
  are detected (currently each graph holds its own copy of a
  shared-layer node; cross-graph dedup is at aggregation time only).

## Open questions

- **What the agent CANNOT do, explicitly:**
  - It cannot identify the winning layer over multi-year arcs.
    Cisco-1999 risk is mitigated by the Bottleneck Scout integration
    and by the `critical_path_status` field, not eliminated. A node
    in an `enabling-but-substitutable` layer is at structural risk
    regardless of current confidence, and Mike should treat it that
    way at sizing time.
  - It cannot identify layers outside its taxonomy. A novel layer
    role requires Mike's taxonomy update.
  - It cannot reliably enumerate private suppliers (it surfaces
    them only as context).
  - It cannot calibrate numeric probabilities; confidence is
    ordinal.
  - Its weekly aggregation produces a derived universe that the
    Universe Curator may accept, modify, or reject. The Mapper does
    not control the trading universe directly.
- **Auto-activation threshold:** 8 nodes is a first guess. A
  smaller threshold means more Mike review; a larger threshold
  means more auto-changes. Blocks: Mike's review burden vs.
  responsiveness. Owner: Mike, end of Month 3.
- **Evidence freshness threshold:** 180 days is a first guess.
  Filings cadence is quarterly so 180 days catches two cycles, but
  fast-moving layers (compute interconnect) may need shorter.
  Blocks: stale-evidence flag noise floor. Owner: Mike.
- **Cross-graph confidence aggregation rule:** max-per-ticker is
  the current rule. Sum or weighted-sum could overweight
  multi-graph tickers in the universe. Blocks: universe
  composition. Owner: Mike, end of sprint.
- **Migration to deterministic maintenance:** the spec assumes
  maintenance moves to Python rules over post-sprint months 5-6
  once the operations are well-understood. The trigger to migrate
  is a documented LLM agreement rate above 95% on a sample of
  maintenance decisions. Blocks: cloud LLM cost trajectory.
  Owner: Mike, post-sprint.
- **Backwards-test rubric for the Mapper's role in the Aug 2024
  LITE case:** Mapper success is "added the optical interconnect
  layer to the AI graph correctly within 4 weeks of the binding
  signal" — but "correctly" needs to be defined before the test
  runs. Blocks: avoiding retrospective goal-shifting on the
  validation case. Owner: Mike, before Month 4.
