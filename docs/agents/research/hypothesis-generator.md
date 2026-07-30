# Hypothesis Generator

**Department:** Research
**LLM tier:** `cloud-default` primary, `cloud-judgment-heavy` for the once-weekly
"hard problems" batch (typically bottleneck-rotation hypotheses where cross-graph
reasoning matters and judgment is load-bearing). Migration target: `local-heavy` for routine
infra-graph plays once shadow evaluation passes. See
`docs/infrastructure/llm-routing.md` and `docs/infrastructure/llm-registry.md`.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** `technical-catalyst` **implemented 2026-07-30** —
`src/shrap/research/hypothesis_generator/`, CLI `shrap-hypothesis-generate`.
`infra-graph-play` and `bottleneck-rotation` remain unimplemented: both are
anchored archetypes and neither Infrastructure Mapper nor Bottleneck Scout
exists. Archetype set corrected 2026-07-30 to include `technical-catalyst` per
ADR-0013.

**Tier drift, recorded not fixed.** The header below names `cloud-default`. The
implementation defaults to `local-heavy`, because the deployment is local-only
(ruling 2026-07-15) and `TierLLMClient` refuses any tier that resolves to a
provider this deployment has not configured. `--tier` takes an override, so the
spec's routing becomes reachable the day a cloud provider is configured rather
than requiring a code change.
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

The hypothesis archetypes the agent generates are a closed set — adding one
requires a Mike ADR.

### The three archetypes (corrected 2026-07-30)

This spec was written when there were two. **ADR-0013 added a third,
`technical-catalyst`, and named the Hypothesis Generator as the component that
proposes it** (ADR-0013 §"A new hypothesis archetype", and its acceptance
criteria: *"Hypothesis Generator built, with `technical-catalyst` in its
archetype set"*). The spec was never updated to match, so for the archetype
behind **every strategy the firm has actually evaluated** there has been no
proposer at all. Every one of them was written by hand.

That is drift in the direction the operating principles do not cover: the ADR
moved and the spec did not follow. Recorded here rather than fixed silently.

| Archetype | Anchor | Proposed from |
|---|---|---|
| `infra-graph-play` | required — a Mike-promoted world-changer node | Infrastructure Mapper graph |
| `bottleneck-rotation` | required — a validated binding bottleneck | Bottleneck Scout |
| `technical-catalyst` | **none, by design** | a published, falsifiable market effect |

### Why `technical-catalyst` needs a different source, not a different prompt

The other two archetypes are disciplined by their anchor: a proposal has to name
a real node on a real graph, and that requirement is what stops the LLM
freelancing. `technical-catalyst` carries no anchor by design (ADR-0013 §1) — so
if it were generated the same way, the discipline would be gone and the agent
would be back to "ask an LLM for a trading strategy", which is the failure this
whole spec exists to prevent.

Worse, the obvious substitute is actively harmful. An LLM asked for price-based
strategies will produce **parameter variations** — a 120-day lookback, a top-12
instead of top-10 — and the Evaluator's multiple-testing gate (PR #148)
correctly raises the promote bar for each successive attempt on one lineage. An
agent that generated variants would spend the firm's promote budget on a search
it was never going to win.

**So the anchor for `technical-catalyst` is the literature.** A proposal must
cite a specific published effect, and the effect is what plays the role the
world-changer node plays for Framework #1: an external, checkable claim the
strategy is an implementation of.

This is not a new ingestion problem. Tech Watcher already ingests arXiv; it is
pointed at tech and world-changer signal for Framework #1 and does not read
**q-fin**, the quantitative-finance section, which is a continuous feed of
exactly these claims. Extending that ingestion is the prerequisite card, and it
is small.

The proposer's job is then narrow and checkable: turn a documented effect into a
spec, state what would falsify it, and refuse if it cannot name the source. That
is a constrained transformation rather than an invention, which is the same
shape as the other two archetypes and the reason this one can be trusted at all.

### What a `technical-catalyst` proposal must carry

In addition to the common required fields below:

- `prior`: citation of the published effect — author, year, and the claim in one
  sentence. **Refuse without it.** A price-based strategy with no cited prior is
  the freelancing this agent exists to prevent.
- `deviation`: how the implementation differs from the source construction, or
  the literal string `none`. This field exists because of a real failure: the
  firm's momentum strategy dropped the short leg of Jegadeesh-Titman and nothing
  recorded that it had, so a one-sided book was read as evidence about momentum
  rather than about the deviation.
- `distinct_from`: the strategy IDs of existing strategies this is claimed not
  to duplicate, with the reason. A proposer cannot be trusted to notice it has
  re-derived an effect the firm already holds; naming the comparison makes the
  claim falsifiable by the Evaluator rather than assumed.

**Every `technical-catalyst` proposal is a lineage root** unless it explicitly
names a `parent_strategy_id`. Proposing a variant as a root would launder a
search past the multiple-testing gate, and that is the one way this archetype
could quietly corrupt the promote decision.

## Trigger

- **Schedule:** One batch per trading day at 19:00 ET. Default cap: **3
  infra-graph proposals + 2 bottleneck-rotation proposals per night**.
  Throttling is enforced inside the agent; the LLM is never given an open-ended
  "generate as many as you can" prompt.

  **`technical-catalyst` has no numeric cap, and the reason it does not is
  stronger than a number** (2026-07-30, Mike: *"id rather not put them in one at
  a time when there could 1000s to try"*). The original cap of 2/night was
  reasoned from supply — a cap larger than the literature's output is an
  instruction to invent. The implementation gets that property structurally
  instead. A proposal's identity is `(rule, factor)` with parameters
  deliberately excluded, and an identity the firm already holds is refused, so
  **the proposer can mint at most one lineage root per implemented effect,
  ever.** It cannot flood the Evaluator, cannot spend a lineage's promote budget
  on a search, and cannot be tuned to permit more. The throttle is the shape of
  the registry rather than a counter.

  The consequence, stated plainly because it is the operating reality rather
  than a caveat: with four factors implemented and all four already seeded, the
  proposer's expected output today is **zero strategies and a queue of
  capability gaps**. That is the correct behaviour and the useful one — see
  Processing below.
- **Event:** Subscribes to:
  - `research.infra.graph.updated` (a Mike-promoted graph gained or lost a
    layer) → up to 2 targeted infra-graph proposals on the changed layer.
  - `research.bottleneck.validated` (Bottleneck Scout promoted a finding to
    binding) → up to 2 targeted bottleneck-rotation proposals.
  - `research.world-changer.promoted` (Mike promoted a new world-changer) →
    one infra-graph "seed" proposal on its most data-rich layer.
  - `research.literature.ingested` (Tech Watcher accepted a q-fin item
    describing a testable market effect) → up to one technical-catalyst
    proposal citing it. **This stream does not exist yet** — it is the
    prerequisite card named above.
- **On-demand:** Mike-initiated `research.hypothesis.request` with a specific
  world-changer ID, bottleneck ID, or graph node. No open-ended Mike-initiated
  brainstorm — every on-demand request must name an anchor.

Daily and event-driven outputs from the two **anchored** archetypes share a
global per-day cap of **10 hypotheses total**, to prevent the Evaluator queue
from being flooded. `technical-catalyst` is outside that count for the
structural reason above.

## Processing — `technical-catalyst` (as implemented)

The anchored archetypes follow the generic pipeline further down. This one is
different enough to state separately, because its anchor is a document rather
than a graph node.

Each literature item ends in exactly one of three places:

| Outcome | Meaning | Where it goes |
|---|---|---|
| `proposed` | cited, runnable, not already held | `research.strategies` at `hypothesis`, always a lineage root |
| `capability-gap` | cited and real, but the engine cannot run it | `research.capability_gaps` |
| `refused` | no citation, not an effect, already held, out of bounds | recorded on the item with its reason; **never retried** |

**The capability gap is the point.** `PanelWindow` exposes closes and volumes;
`FACTOR_SCORERS` implements four effects on top of them. A proposer reading a
thousand papers therefore cannot produce a thousand strategies — it produces a
handful, plus a ranked, cited list of the scorers it would need built to produce
the rest. Two kinds, kept apart because they cost differently:

- `missing-scorer` — computable from close and volume, nobody wrote the
  function. An afternoon of work. This is the queue worth working.
- `missing-data` — needs fundamentals, shares outstanding, intraday bars,
  options, short interest. A feed to acquire. The count of these is the honest
  argument for buying data.

Ranked by how many **distinct** papers cited each gap, so the build order comes
from the field rather than from whoever is at the keyboard.

**Three things are stapled on regardless of what the model returns**, each
because of a specific past failure:

1. **Construction is fixed.** `top_n`, `gross_exposure` and `long_short` are not
   in the model's schema. Held identical, a comparison between two proposals
   measures the two effects; free to vary, it measures two implementations.
2. **The deviation always names the long-only 50-name universe.** The firm's
   momentum strategy dropped the short leg of Jegadeesh-Titman and nothing
   recorded that it had, so a one-sided book read for months as evidence about
   momentum rather than about the deviation.
3. **The protocol kill criteria are appended.** The proposer supplies how *its*
   effect dies; the three ways any strategy fails the firm's protocol are not a
   proposer's to omit.

**`distinct_from` is a claim, and nothing yet verifies it.** It is recorded in
the spec's `provenance` block marked unverified. The verification is prerequisite
card 3 — fold-information-ratio correlation between strategies — and until that
ships, a proposer's assertion that its effect is novel is exactly as good as its
assertion of anything else.

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
   - `archetype`: `infra-graph-play`, `bottleneck-rotation`, or
     `technical-catalyst` (only these — ADR-0013).
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
| PostgreSQL: `research.capability_gaps` | Upsert | One row per effect the engine cannot run, with every paper that cited it. Citations are a JSON object keyed by item id, so re-running the pass counts nothing twice |
| PostgreSQL: `research.literature_items` | Update | The item's outcome and reason, so a re-run skips settled work and "what did we read and not act on" stays queryable |
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
- Any archetype other than the three named above.
- **Generating `technical-catalyst` proposals without a cited prior.** The
  literature is this archetype's anchor; without it the agent is freelancing.
- **Parameter variation of an existing strategy.** A different lookback is not a
  new hypothesis, it is attempt N of an old one, and it belongs in a revision
  with a `parent_strategy_id` — not in a proposal.

## Prerequisite cards, in order

1. **Tech Watcher reads arXiv `q-fin`.** It already ingests arXiv for Framework
   #1 world-changer signal; this adds the quantitative-finance section and a
   filter for "describes a testable market effect". Writes
   `research.literature_items` and emits `research.literature.ingested`. Small —
   the ingestion path exists. **Still open.** Note that the world-changer filter
   cannot be reused: its prompt explicitly rejects anything that is "merely
   ABOUT a technology — a new method, model architecture, benchmark, or
   simulation result", which is a description of every q-fin paper. Two filters
   over one ingest, routed on category.
2. ~~**This spec's implementation.**~~ **Shipped 2026-07-30.** Built before card
   1 deliberately: the consumer's requirements are the contract, and the
   literature table is defined on this side of the seam so the producer fills a
   known shape instead of guessing one. Until card 1 ships, the CLI's
   `--from-file` runs the whole proposer against a hand-assembled list of
   abstracts, which is how the prompt gets reviewed before anything depends on
   it.
3. **A duplicate check the Evaluator can enforce.** `distinct_from` is a claim
   by the proposer, and a claim nothing verifies is decoration. The natural
   check is the fold-information-ratio correlation already named as a kill
   criterion on the reversal and 52-week-high seeds — two strategies whose fold
   IRs correlate above ~0.9 are one effect, whatever their specs say.

Card 3 is worth doing even if 1 and 2 are deferred: it is the measurement that
tells the firm whether the strategies it already holds are as diverse as it
believes, and it is the only one of the three that pays off with no new agent.

## Open questions

- **Per-day cap of 10:** Default guess, and now scoped to the two anchored
  archetypes only — `technical-catalyst` is bounded structurally instead (see
  Trigger). Blocks: Evaluator capacity planning. Owner: Mike, after first two
  weeks of operation.
- **Is the identity key `(rule, factor)` too coarse?** It is what makes the
  proposer unfloodable, and it is also what makes it nearly silent: with all
  four factors seeded, every price-based paper now refuses as `already-held`. A
  looser key would let the firm test more; it would also let a search be
  registered as a set of fresh ideas. Recorded as the deliberate trade rather
  than tuned. Owner: Mike. Blocks: nothing today — widening the scorer library
  raises throughput without touching the key, and that is the intended path.
- **Should a `technical-catalyst` proposal be allowed to cite a prior the firm
  has already implemented?** Arguably yes — a second implementation of the same
  paper with a recorded `deviation` is a legitimate experiment about the
  deviation. Arguably no — it is how one effect quietly occupies three of three
  accounts. Owner: Mike. Blocks: the proposer's duplicate rule.
- **What counts as a "published" prior?** A peer-reviewed paper is clear. An
  arXiv preprint is the actual feed. A practitioner blog post is not, but a
  great deal of real trading knowledge lives there. Owner: Mike. Blocks: the
  Tech Watcher q-fin filter's acceptance threshold.
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
