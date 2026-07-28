# ADR-0013: The Fast Layer, and Cross-Lens Synthesis as a First-Class Function

**Status:** Proposed
**Date:** 2026-07-27
**Deciders:** Mike White

## Context

Two months after ADR-0010 corrected ADR-0007's exclusivity claim, the
implementation has drifted back into exactly the shape ADR-0010 rejected. A
trace of the firm's live code on 2026-07-27 found that every path from evidence
to a trade runs through Research Thesis Framework #1, and that two capabilities
the vision names as central do not exist in any form.

### 1. The funnel asks one question of everything it ingests

`src/shrap/research/tech_watcher/filter.py` hands the model five archetype
definitions — `compute-substrate`, `bio-mechanism`, `cost-curve`,
`physical-realization`, `platform-shift` — and instructs: *"Reject only after
the item fails EVERY archetype."* All five are technology-adoption patterns.

Roughly 1,900 ingested items (1,656 EDGAR, 117 USASpending, 113 Federal
Register, 16 DOE) have been scored against that single grammar. A debt maturity
wall, a covenant breach, an insider cluster, a customer-concentration
disclosure — none are rejected on their merits. They are rejected because no
archetype exists to evaluate them.

This is not a filter-quality problem. KI-009 correctly diagnosed that the
funnel cannot promote anything until hard-source items survive the filter, and
prompt v4 addressed the *bar*. The finding here is different and upstream of
it: the funnel's aperture admits one shape of thing.

### 2. Strategy generation is structural-only

The Hypothesis Generator — the only component that would ever propose a
strategy — is specified to emit exactly two archetypes, `infra-graph-play` and
`bottleneck-rotation`, with the note that *"adding archetypes requires a Mike
ADR"* (`docs/agents/research/hypothesis-generator.md:41`). Both are structural.
The Strategy Evaluator currently runs only `infra-graph-play`.

There is therefore no path — not blocked, **absent** — by which a technical or
short-term-catalyst strategy enters the registry.

`docs/00-vision.md:109` says: *"**Most** of Shrap's strategies trade on
technical and short-term-catalyst signals — fast loops, many trades, modest
per-trade edge,"* with Structural Analysis as the *"patient counterweight"* on
*"a much slower clock."*

The firm has built the counterweight and never built the thing it
counterweights.

This also explains a mismatch that has been read as a property of the seed
strategy but is actually architectural. The Evaluator's `DEFAULT_MIN_TRADES =
150` gate is calibrated for fast loops with many trades. The only available
strategy supply is structural theses that trade a handful of times per year.
*Any* Framework #1 strategy fails that gate. Supply and gate come from
different departments, and the seed's expected death is a symptom rather than
a coincidence.

### 3. `intelligence.signal` is a dead-end stream

The News Analyzer and Filing Processor both publish materiality-scored signals
to `intelligence.signal`. A grep across `src/shrap/` finds **two producers and
zero consumers**. The Decision Maker subscribes only to `STREAM_STRATEGY_SIGNAL`
(`src/shrap/trading_floor/decision_maker_service.py:87`).

Two deployed agents perform real work hourly, write it to a stream, and nothing
reads it.

### 4. ADR-0010 is accepted and substantially unimplemented

| ADR-0010 decision | Implementation status |
|---|---|
| §3 Structural Analysis as a separate department | Zero agents; no `docs/agents/structural-analysis/` directory |
| §4 Regime Classifier as strategy-activation gate | Not implemented; no Regime Router; classifier output gates nothing |
| §5 Forced-Proxy as Framework #2, via ADR-0011 | ADR-0011 never written; Universe Curator spec marks it "reserved, unwritten" |
| §6 Multiple theses running in parallel | Only Framework #1 exists |

### 5. Nothing in the firm performs a cross-lens join

Mike's stated objective for the firm is to *"find those outliers that take a
team to put the pieces together from something that doesn't seem like they
would affect each other."*

Every lens in the firm today asks a within-lens question: "does this item match
my archetype?" and stamps a verdict. No component asks the across-lens
question: "do these several individually-unremarkable items, from different
source classes, mean something in combination?"

That across-lens question is the one the canonical cases actually required.
Subprime in 2008 was visible only as a join: mortgage delinquency data, rating
agency incentive structure, repo funding conditions, homebuilder guidance, and
a geographic-correlation assumption — no single source alarming, the
combination decisive. Framework #1's own motivating case has the same shape:
the Valar criticality event was a DOE item, and its significance depends on
joining it to reactor-cohort counts and licensing throughput.

One fact makes this recoverable rather than lost: `research.raw_source_items`
**retains** filtered-out items with a `filter_result` JSONB stamp rather than
deleting them (`src/shrap/research/tech_watcher/store.py:26`). The corpus
needed for retrospective joins exists. Nothing queries across it.

## Decision

### 1. The fast layer becomes a first-class strategy source

Register **Research Thesis Framework #3: Technical / Short-Term Catalyst.**

(Number 2 remains reserved for Forced-Proxy per ADR-0010 §5, whose ADR-0011 is
still owed. Framework #3 is numbered ahead of it because the vision assigns it
the majority of strategies while Framework #2 remains unspecified. Numbering
reflects registration order, not priority.)

Mechanism:

> A repeatable, testable market-microstructure or catalyst condition produces a
> short-horizon directional expectation, evaluated on trade count and
> out-of-sample edge rather than on a structural thesis.

This framework does **not** carry a world-changer anchor. The Evaluator's
anchor-freshness gate is a Framework #1 construct and must not be applied to
Framework #3 strategies — a technical strategy with no anchor is correctly
anchor-less, not broken. See Consequences.

### 2. The fast layer enters through the registry, not around it

A new hypothesis archetype, `technical-catalyst`, is added to the Hypothesis
Generator's allowed set. Framework #3 strategies enter `research.strategies` at
`hypothesis` and traverse the same path as every other strategy: Evaluator →
verdict → Librarian → Strategy Runner.

**No signal reaches the Decision Maker without having been evaluated.** The
tempting shortcut — wiring `intelligence.signal` directly into the Decision
Maker to give the dead-end stream a consumer — is rejected. It would put
unvalidated signals in front of the order path and void the kill-rate
discipline that is the firm's main defense against its own enthusiasm.

### 3. `intelligence.signal` gains a consumer at the strategy layer

The Strategy Runner subscribes to `intelligence.signal` and routes signals to
promoted Framework #3 strategies that declare an interest in them, by ticker
and signal type. A strategy declares that interest in its spec.

This gives the stream a consumer without creating a bypass: the strategy
consuming the catalyst has already been through walk-forward evaluation, and
the catalyst is an input to a validated rule rather than a trade instruction.

### 4. The Sweep Detector is the first Framework #3 instance

Mike's existing liquidation-sweep logic is wrapped as the first
`technical-catalyst` strategy. It is a Month-1 roadmap item that was never
built, the logic already exists and has been traded by Mike, and the roadmap's
own month-2 risk mitigation calls for exactly this: *"seed the Hypothesis
Generator with strategies Mike has historically traded as a sanity check on the
validation pipeline."*

It is also the first strategy in the firm's history with a plausible chance of
clearing the 150-trade gate — which makes it the first real test of the
Evaluator against something that is not designed to fail.

### 5. Cross-lens synthesis becomes a named function

The firm adopts as an explicit architectural component a **synthesis surface**
whose question is across-lens rather than within-lens:

> Given the retained corpus of all ingested evidence — including items every
> individual lens rejected — which co-occurrences across source class, entity,
> and time are anomalous relative to their own base rates?

Binding properties:

1. **It reads rejects.** Its input is `research.raw_source_items` in full, not
   the promoted subset. An item that failed every archetype is a first-class
   input here. This is the point of the component.
2. **It proposes, it does not promote.** Output is a candidate join with named
   constituent items, routed to Mike and to the owning lens. It has no path to
   the registry of its own.
3. **It must state a base rate.** A join is reportable only with an explicit
   claim about how often that co-occurrence happens by chance. Without it the
   component is a coincidence generator, and coincidence at 1,900 items and
   growing is guaranteed.
4. **Its own kill criterion is precision.** If Mike judges fewer than one in
   five surfaced joins worth investigating over a full review cycle, the
   component is off, not tuned.

The mechanism, agent spec, ownership, and cadence are deferred to a follow-up
spec — the same treatment ADR-0010 §5 gave Forced-Proxy. This ADR decides that
the function exists and who it answers to, not how it is computed.

### 6. Fragility gains a grammar, owned by Structural Analysis

The world-changer archetype taxonomy has five entries, all adoption-shaped.
Neither it nor the bottleneck taxonomy (physical-limit, economic-saturation,
supply-chain choke, regulatory) can express accumulating structural fragility —
the pattern where correlated exposure builds inside a system that measures it
as uncorrelated until a trigger reveals the correlation.

Per ADR-0010 §3, that lens belongs to Structural Analysis, not to Research.
A `docs/research/fragility-archetypes.md` taxonomy is authorized, owned by
Structural Analysis, governed by one additional rule beyond the standard
cross-archetype rules:

> **Falsify the mechanism, not the event.** "No crash by date X" is not an
> observable kill criterion — absence of collapse is not evidence of
> robustness, and accepting it as one is what makes permabear theses
> unfalsifiable. A fragility thesis must name the leverage, correlation, or
> underwriting metric it claims is deteriorating, and dies when that metric
> normalizes, whether or not anything later breaks for unrelated reasons.

Two honest limits are recorded with it. Vision §7 confines structural output to
*"biases and sizing modifiers — not entry triggers,"* so a fragility thesis can
never fire a short. And under ADR-0003 the firm holds market/day orders on a
50-name equity universe with no options or CDS, so the expression of a correct
fragility thesis today is limited to reducing exposure, sizing down, or fading
a name.

### 7. A separate archetype for keystone completions

`cost-curve` is currently absorbing candidates that are not cost curves. A
keystone completion — where N-1 complements are already mature and one binary
unlock tips an entire stack at once — is structurally distinct from a single
unit cost declining smoothly along a learning curve.

`world-changer-archetypes.md` gains archetype **(f) Keystone completions**,
carrying one requirement the other archetypes do not:

> The thesis must name where the rent lands. Value from a keystone typically
> accrues downstream of the keystone rather than to whoever supplied it —
> transformers were published free, the shipping container was unpatentable. A
> keystone thesis that cannot name the beneficiary is not tradeable.

This resolves the KI-009 taxonomy question left open on 2026-07-27. Nth-unit
and deployment-cadence evidence are leading indicators for **(f)**, not for
**(c)** — `cost-curve` is correct to demand unit-cost evidence, and the DOE
rejections were defensible under it. The mass-manufactured fission thesis
should be re-examined as a possible **(f)**: its claim is that regulatory
throughput, modular design, and hyperscaler demand are already in place and
factory production is the keystone.

## Alternatives Considered

### (a) Wire `intelligence.signal` directly into the Decision Maker

Rejected. It is the fastest way to make the fast layer real and it destroys the
evaluation discipline. Unvalidated signals would reach the order path with no
walk-forward, no friction stress, and no verdict — against operating principle
2 ("kill more aggressively than you promote") and ADR-0007's kill-rate regime.
The dead-end stream is a real problem; bypassing evaluation is a worse one.

### (b) Add fragility to the world-changer taxonomy as archetype (g)

Rejected. It would route credit, leverage, and underwriting evidence through
the Tech Watcher, whose ingest is arXiv/EDGAR-headline/gov-award shaped and
whose consumers are the Infrastructure Mapper and world-changer promotion path.
ADR-0010 §3 separated Structural Analysis for exactly this reason: shared
sources do not imply shared departmental scope. Keystone completion **is** an
adoption pattern and does belong in the world-changer file; fragility does not.

### (c) Write ADR-0011 (Forced-Proxy) first, since it was promised

Rejected as the *first* move, though it remains owed. Forced-Proxy is a third
structural lens. Adding it before the fast layer exists would deepen the
imbalance this ADR is correcting and would still leave the vision's stated
majority of strategies without a producer.

### (d) Build the synthesis surface before adding lenses

Rejected. A join across one lens is not a join. The synthesis surface becomes
valuable in proportion to the number of independent lenses feeding it, so it is
decided here and specified once Framework #3 and the fragility taxonomy are
producing.

### (e) Do nothing until the loop is closed

Rejected, but its premise is accepted and reflected in the sequencing below.
The concern is real: the firm currently has more evidence production than
evidence consumption, and adding lenses to a middle that cannot consume what it
already has makes the imbalance worse. The answer is ordering, not inaction —
the loop-closing work precedes the new lenses in Consequences.

## Consequences

### Immediate, and blocking

- **The Evaluator's anchor gate must become archetype-conditional before any
  Framework #3 strategy is evaluated.** `pipeline.py:293` checks
  world-changer freshness for every strategy and maps a missing anchor to
  `KILL / anchor-not-live` with `engine_ran=False`. A `technical-catalyst`
  strategy has no anchor by design and would be killed without the backtest
  ever running. This is the single hard code dependency in this ADR.

  > **Correction, 2026-07-28 (implementation).** "The single hard code
  > dependency" understated it: there were two gates, and the anchor check was
  > the *second*. `_check_spec_hygiene` refused every archetype but
  > `infra-graph-play` and ran first, so a `technical-catalyst` record raised
  > `SpecHygieneError` and produced no verdict at all — not the fake
  > `anchor-not-live` kill described above. Both are fixed together by
  > `ARCHETYPE_POLICIES`; the sequencing below is unaffected. Left in place
  > rather than edited away because the gap between what the ADR predicted and
  > what the code did is the reason the policy is now a table instead of a
  > scatter of conditionals.
- **`DEFAULT_MIN_TRADES = 150` becomes archetype-conditional or is documented
  as Framework #3-calibrated.** It is currently applied uniformly, which
  guarantees every structural strategy dies on it. Either the gate varies by
  archetype or Framework #1 strategies are evaluated on a different protocol.
  This is a calibration decision and is Mike's.

  > **Resolved 2026-07-28 by evidence, taking the second branch.** The first
  > three real evaluations showed Sharpe is noise at these trade counts
  > (annualized 1.712 from one trade in fold 5; 20/43/145 trades giving
  > 0.415 / −0.157 / 0.745 on the same rule and ticker — monotonic in count,
  > sign-changing in Sharpe). A per-archetype floor would report that noise
  > with more confidence rather than measuring structural strategies more
  > fairly. 150 stays universal and Framework #1 needs a *different protocol*,
  > which is now its own card. See `docs/research/eval-protocol.md` §6.

### Implementation debt this ADR makes explicit

ADR-0010's unimplemented decisions are now tracked rather than latent:
Structural Analysis has no department directory or agents; the Regime
Classifier gates nothing; ADR-0011 is owed. This ADR does not resolve them; it
stops them being invisible.

### Sequencing

The loop must close before it widens. Recommended order:

1. Evaluator anchor gate made archetype-conditional (blocking, above).
2. Evaluator gains a trigger — it is tools-profile and manual-only today, so no
   research is automatic regardless of how many lenses exist.
3. Hypothesis Generator built, with `technical-catalyst` in its archetype set.
4. Sweep Detector wrapped as the first Framework #3 strategy.
5. Regime Router, delivering ADR-0010 §4's activation gate.
6. Fragility taxonomy and the Structural Analysis department directory.
7. Synthesis surface spec, once two or more lenses are producing.

Items 1–2 are the ones that make anything else compound; items 6–7 are the ones
that serve the firm's stated objective. Both matter, and the order is not
negotiable in the other direction.

### Not changed by this ADR

- Paper-only scope. No real-money execution.
- ADR-0003's execution boundary — market/day orders, no options, no CDS.
- Framework #1's specs, documents, and agents, which remain correct for
  Framework #1.
- The 50-name Tier 3 launch universe (DQ-004), though Framework #3 will
  eventually pressure it: microstructure strategies want liquid, high-turnover
  names, and the current list was chosen for structural expressiveness.

## Notes

The synthesis surface is the component most likely to fail, and it should be
held to its precision kill criterion without sentiment. Cross-source
correlation mining over a growing corpus produces spurious joins at a rate that
rises with corpus size, and the failure mode is not silence but a steady
stream of plausible-sounding coincidences that consume the scarcest resource
the firm has, which is Mike's attention. The base-rate requirement in §5.3 is
what separates the component from a random-pattern generator, and it is not
optional.

It is also the only component in this ADR that addresses the firm's stated
reason for existing rather than its plumbing.
