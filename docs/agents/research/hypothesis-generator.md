# Hypothesis Generator

**Department:** Research
**LLM tier:** Cloud (Claude Sonnet 4.6) primary, Cloud (Opus 4.7) for the once-weekly
"hard problems" batch (typically bottleneck-rotation hypotheses where cross-graph
reasoning matters). Migration target: Local (Mistral Small 24B) for routine
infra-graph plays once shadow evaluation passes. See
`docs/infrastructure/llm-routing.md`.
**Status:** Draft
**Date:** 2026-05-30
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Hypothesis Generator proposes new strategy specifications for the Strategy
Evaluator to test. Under the new Research thesis (ADR-0007), it does *not*
freelance: every hypothesis it emits must be anchored to (a) a Mike-promoted
world-changer node on an active Infrastructure Mapper graph, or (b) a Bottleneck
Scout finding that has reached "validated binding" status. Regime is no longer a
gate for proposal generation — it is a sizing modifier carried as metadata.

The agent exists because the failure mode of "ask an LLM for a trading
strategy" is hallucinated nonsense: vague rules, no kill conditions, no
falsifiable thesis, no link to anything happening in the real world. By forcing
every proposal to be a node on a graph or a layer downstream of a bound
bottleneck, the funnel stays narrow and the kill criteria stay specific.

What this agent cannot do, stated clearly:

- It cannot tell whether a hypothesis has real edge. It is a proposer; the
  Strategy Evaluator is the gatekeeper. Expected kill rate on proposals is
  ≥90%, by design.
- It cannot validate that a world-changer thesis is correct or that a
  bottleneck is actually binding. It trusts upstream Tech Watcher / Infra
  Mapper / Bottleneck Scout outputs, and inherits their failure modes.
- It cannot pick its own universe. The investable tickers are whatever the
  Universe Curator currently has in the active set.

The two hypothesis archetypes the agent generates are intentionally the only
two — adding archetypes requires a Mike ADR.

## Trigger

- **Schedule:** One batch per trading day at 19:00 ET. Default cap: **3
  infra-graph proposals + 2 bottleneck-rotation proposals per night**.
  Throttling is enforced inside the agent; the LLM is never given an open-ended
  "generate as many as you can" prompt.
- **Event:** Subscribes to:
  - `research.infra.graph.updated` (a Mike-promoted graph gained or lost a
    layer) → up to 2 targeted infra-graph proposals on the changed layer.
  - `research.bottleneck.validated` (Bottleneck Scout promoted a finding to
    binding) → up to 2 targeted bottleneck-rotation proposals.
  - `research.world-changer.promoted` (Mike promoted a new world-changer) →
    one infra-graph "seed" proposal on its most data-rich layer.
- **On-demand:** Mike-initiated `research.hypothesis.request` with a specific
  world-changer ID, bottleneck ID, or graph node. No open-ended Mike-initiated
  brainstorm — every on-demand request must name an anchor.

Daily and event-driven outputs share a global per-day cap of **10 hypotheses
total** across all triggers, to prevent the Evaluator queue from being flooded.

## Cross-references

**Depends on:** Tech Watcher (world-changer status + thesis-broken events),
Infrastructure Mapper (active graphs and layer evidence), Bottleneck Scout
(validated bottlenecks), Universe Curator (active tradable set + per-ticker
profiles), Strategy Librarian (prior-art lookup), Regime Classifier under
`docs/agents/intelligence/regime-classifier.md` (sizing-modifier metadata
only).
**Depended on by:** Strategy Evaluator (consumes proposals), Mike (reviews
high-conviction or anomalous proposals before they consume backtest budget).
**Related ADRs:** ADR-0006 (envelope), ADR-0007 (Research thesis: world-changers
+ infra graphs + bottlenecks).
**Related architecture sections:** `docs/02-architecture.md` §Research
Department, §Strategy lifecycle.

## Inputs

| Source | Type | Description |
|---|---|---|
| PostgreSQL: `research.world_changers` | Query | Promoted world-changers, their thesis statements, promotion date, current confidence |
| PostgreSQL: `research.infra_graphs` | Query | Active graphs per world-changer: nodes, layers, edges, evidence references |
| PostgreSQL: `research.bottlenecks` | Query | Validated bottlenecks, binding evidence, identified replacement layers |
| Redis: `research.infra.graph.updated` | Event | Triggers infra-graph batch |
| Redis: `research.bottleneck.validated` | Event | Triggers bottleneck-rotation batch |
| Redis: `research.world-changer.promoted` | Event | Triggers seed proposal |
| Redis: `intelligence.regime.tick` | Event (ref) | Current regime label, attached as sizing-modifier metadata only |
| PostgreSQL: `universe.active` | Query | Tickers currently approved for trading by Mike via Universe Curator |
| Repo: `docs/universe/<ticker>.md` | File read | Per-ticker profile (behavior, prior patterns, graph membership) |
| Qdrant: `strategy_corpus` | Semantic search | Prior hypotheses — novelty check against killed strategies |
| PostgreSQL: `research.strategies` | Query | All prior strategies (anti-duplication, kill-history lookup) |

## Processing

1. **Resolve the anchor.** For each trigger, identify the anchor: a
   world-changer + graph + layer (infra-graph) or a validated bottleneck +
   replacement layer (rotation). If the anchor is missing, stale, or its
   world-changer is in `thesis-at-risk` status, abort and emit a
   skipped-with-reason event. No anchor = no hypothesis.
2. **Filter the universe.** Intersect the anchor's relevant tickers
   (graph-layer members or replacement-layer members) with `universe.active`.
   If the intersection is empty, emit a `universe.gap.detected` event for the
   Universe Curator and abort.
3. **Load context.** Pull per-ticker profiles, prior strategies on the same
   anchor (especially killed ones), and the world-changer's thesis statement
   verbatim. Pull current regime label *only* to populate the
   `regime_sizing_modifier` field on the output — not to gate generation.
4. **Constrained LLM call.** Strict JSON schema. The prompt names the anchor
   explicitly and forbids deviation: "you are writing a strategy on
   layer Y of world-changer X's graph; you may not reference a different
   world-changer or layer." Required fields per proposal:
   - `archetype`: `infra-graph-play` or `bottleneck-rotation` (only these).
   - `anchor`: `{world_changer_id, graph_id, layer_id}` or
     `{bottleneck_id, replacement_layer_id}`.
   - `thesis`: one-paragraph statement linking the anchor to the trade.
   - `tickers_long` and (rotation only) `tickers_short`. Shorts on
     obsoleted-layer tickers are allowed only when the bottleneck finding
     explicitly flags rapid obsolescence; otherwise long-only.
   - `entry_rules`, `exit_rules`, `stop_rules` (deterministic pseudocode).
   - `expected_hold_horizon`: quarters-to-years for infra-graph,
     weeks-to-quarters for bottleneck-rotation. Out-of-range is rejected.
   - `regime_sizing_modifier`: `{regime_label: size_multiplier}` map.
     Multipliers in [0.0, 1.5]. Regime is sizing only — never an entry gate.
   - `kill_criteria`: ordered list. **Must include**, at minimum:
     - For infra-graph: "world-changer thesis broken event from Tech Watcher"
       and "graph node failed dependency event from Infra Mapper for any
       held ticker."
     - For bottleneck-rotation: "bottleneck no longer binding event from
       Bottleneck Scout" and "replacement layer W fails to scale (specific,
       measurable check)."
   - `falsifier`: an observation in the world (not in the backtest) that
     would refute the thesis.
   - `prior_art_refs`: IDs of similar killed strategies and what is different
     this time.
5. **Local deterministic validator.** Reject proposals that:
   - Omit any required field or use an un-allowed archetype.
   - Reference tickers outside `universe.active`.
   - Reference a world-changer not in `promoted` status, or a bottleneck not
     in `validated-binding` status.
   - Omit the upstream-event kill criteria from step 4.
   - Use regime as an entry/exit gate rather than a sizing modifier.
   - Are >0.85 cosine-similar to a killed prior with no `prior_art_refs`
     explanation.
   - Violate the per-day cap of 10 total.
   Rejections are logged with reason. The agent does **not** retry the LLM to
   fix rejections — it logs, moves on, and lets the throttle hold.
6. **Persist and publish.** Surviving proposals are written to
   `research.strategies` with status `hypothesis`, indexed in Qdrant, and one
   event is emitted per proposal. The full prompt, model, temperature, and
   raw response reference are persisted with every proposal for audit.
7. **Daily summary.** End-of-batch rollup event: N generated, N rejected (with
   reason codes), anchors used, anchors skipped-due-to-thesis-at-risk. Sent to
   the Daily Briefing Agent and Mike.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `research.hypothesis.proposed` | Event | One per surviving proposal, payload-by-reference to full spec |
| Redis stream: `research.hypothesis.batch.summary` | Event | End-of-batch rollup |
| Redis stream: `research.hypothesis.skipped` | Event | Anchor missing / thesis-at-risk / universe-empty cases |
| Redis stream: `universe.gap.detected` | Event | Sent when an anchor's layer has zero overlap with `universe.active` |
| PostgreSQL: `research.strategies` | Insert | Full proposal record, status=`hypothesis`, anchor IDs, LLM call audit fields |
| Qdrant: `strategy_corpus` | Upsert | Embedding of proposal description, rules, and anchor |
| Repo: `docs/strategies/proposed/<id>.md` | File write | Auto-generated proposal card on a sandboxed branch. Never auto-merged. Implementation Agent may **not** modify trading or risk policy here without Mike's explicit approval. |

Every event carries the ADR-0006 envelope.

## LangGraph structure

**Nodes:**
- `resolve-anchor` — load and validate the triggering anchor
- `filter-universe` — intersect anchor tickers with `universe.active`
- `load-context` — per-ticker profiles, prior art, world-changer thesis
- `llm-generate` — constrained generation, per archetype
- `validate` — schema + policy + novelty + cap checks
- `persist-emit` — write to stores, publish events

**Key edges:**
- `resolve-anchor` → (anchor invalid) → `persist-emit` (skip event) → END
- `resolve-anchor` → `filter-universe` → (empty) → `persist-emit`
  (universe-gap event) → END
- `filter-universe` → `load-context` → `llm-generate` → `validate`
  → `persist-emit`
- Per-proposal branch inside `validate`: drop or keep.

## State

| What | Store | Notes |
|---|---|---|
| All proposed hypotheses (lifetime) | PostgreSQL `research.strategies` | Append-only, status transitions only |
| Embeddings | Qdrant `strategy_corpus` | Indexed for novelty checks |
| Per-batch run record | PostgreSQL `research.hypothesis_batches` | Prompt hash, model, anchors, n_generated, n_kept |
| Daily throttle counter | Redis key `research.hypothesis.daily_count:<date>` | TTL 48h, enforced by `validate` |

## Failure behavior

1. **Containment.** A bad proposal does not move money — the Strategy
   Evaluator gates it, the Risk Officer gates sizing, and real-money is
   hard-blocked for the sprint. Realistic blast radius: wasted Evaluator
   compute, noisier prior-art corpus, polluted Strategy Librarian state. All
   bounded.
2. **Replay safety.** LLM calls are non-deterministic; replay does not
   regenerate identical proposals. Persisted proposal is the source of truth.
   Idempotency key on `(anchor_id, batch_date)` prevents double-generation
   from event re-delivery.
3. **Degraded operation.** The firm runs fine without this agent for weeks.
   No hypotheses means no new strategies entering the funnel; existing
   strategies continue under Risk Officer caps. If the LLM tier is
   unavailable, the agent skips its batch and emits a degraded-skip event. If
   upstream Tech Watcher / Infra Mapper / Bottleneck Scout are down, this
   agent **must** skip — generating without fresh anchor state is exactly
   the failure mode the redesign exists to prevent.

## Sprint scope

- Month 2: Infra-graph archetype only. Daily-batch + on-demand triggers.
  Deterministic validator. No Qdrant prior-art beyond Postgres LIKE search.
- Month 3: Bottleneck-rotation archetype. Event-triggered batches from Infra
  Mapper and Bottleneck Scout. Qdrant prior-art retrieval.
- Month 4: Regime sizing-modifier metadata wired in. Shadow-test local model
  on routine infra-graph batch.

## Deferred

- Multi-agent debate / critique loops on proposals (boring beats clever).
- Automatic parameter sweeps within a proposal — that is the Evaluator's job.
- Cross-asset hypotheses — universe is equities-only for the sprint.
- Options strategies — deferred to post-launch.
- Any archetype other than the two named above.

## Open questions

- **Per-day cap of 10:** Default guess. Blocks: Evaluator capacity planning.
  Owner: Mike, after first two weeks of operation.
- **Should rotation-archetype shorts require Mike's explicit per-proposal
  approval during sprint?** Currently no — they're allowed if the rapid-
  obsolescence flag is set by Bottleneck Scout. Blocks: trust progression on
  shorts. Owner: Mike.
- **Novelty threshold (0.85 cosine):** Same guess as old spec. Blocks:
  avoiding near-duplicates of killed strategies. Owner: Mike, after first
  month of rejected-proposal review.
- **What is the right hold-horizon range boundary between archetypes?**
  Currently weeks–quarters for rotation, quarters–years for infra-graph. Some
  bottleneck rotations may legitimately run multi-year. Blocks: rejection of
  legitimate long-horizon rotation plays. Owner: Mike + Bottleneck Scout owner.
