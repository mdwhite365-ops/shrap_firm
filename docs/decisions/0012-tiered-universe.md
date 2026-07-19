# ADR-0012: Tiered Universe — Market-Wide Discovery, Curated Execution

**Status:** Accepted
**Date:** 2026-07-19
**Deciders:** Mike White

## Context

The universe document (docs/universe/README.md) defines a 50-name launch
Universe and, post ADR-0010, a merged active set maintained by the Universe
Curator from multiple approved sources. ADR-0010 dissolved the "permanently
locked list" framing but did not define the relationship between three sets
that the system already implicitly operates:

1. What the firm *sees*. Tech Watcher ingest (SEC EDGAR current filings,
   USASpending awards, arXiv, DOE newsroom) is market-wide by construction.
   No universe filter is applied at ingest, and none should be — the funnel's
   job is discovery, and discovery constrained to known names cannot discover.

2. What the firm *watches*. The Structural Analysis watch list, forced-proxy
   candidates staged by the Curator, and funnel candidates under evaluation.
   This set is unbounded in principle but small in practice, because each
   entry carries evidence requirements and review cost.

3. What the firm *trades*. The active universe: names with maintained
   behavioral profiles, regime calibration, and Structural Analysis coverage.

The pressure to conflate these is real. Mike's directive is that "the universe
should be able to be as big as the market," and the discovery layer already
honors that. But the vision document's argument for a focused tradeable set is
not a feasibility concession — it is the edge thesis itself: depth of
understanding per ticker compounds in a way breadth does not. Historical
evidence supports this: the Mark 11 scanner ran broad and produced a ~47% win
rate. The Strategy Evaluator's promotion gates (walk-forward only, PBO,
deflated Sharpe, 150-200 trade minimums) are also statistically unsatisfiable
across thousands of names in sprint-relevant time. Cost constraints compound:
per-ticker structural reads are LLM spend that scales linearly; Alpaca's free
IEX feed cannot sync market-wide daily bars on the classifier's cadence.

The worked example motivating this ADR: the Rocket Lab / Iridium merger
(announced 2026-06-29). The deal is material to RKLB — a name ADR-0010 already
cites as a Forced-Proxy illustration — and carries structural signals of
exactly the kind vision §7 assigns to Structural Analysis (collar mechanics,
bridge-loan leverage, post-announcement insider selling, exchange-ratio
reflexivity at the collar floor). Under the current implicit model the firm
has no path from "EDGAR feed carried the 8-K" to "RKLB has a structural bias
attached." A hand-run analysis on 2026-07-19 traversed that path manually;
this ADR makes the path a first-class object.

## Decision

**Three explicit tiers.** The universe is not one set. It is three, each with
its own membership rules, cost model, and owner:

- **Tier 1 — Discovery (market-wide).** Everything the ingest sources see.
  Membership: none; this tier is the market. Cost model: bulk, cheap,
  local-classification only. No per-name state is maintained. Owner: Tech
  Watcher and future ingest sources.

- **Tier 2 — Watch (unbounded, evidence-gated).** Names elevated out of
  discovery by any approved mechanism: funnel candidate promotion,
  Forced-Proxy staging (ADR-0011), Structural Analysis findings, or Mike
  seeding. Membership requires a recorded elevation event with source and
  evidence. Per-name state: a watch record with entry rationale, falsifier or
  expiry, and accumulating structural findings. Names in Tier 2 are not
  tradeable. Cost model: bounded per-name review cost; the Curator enforces a
  soft cap by requiring an expiry or falsifier on every entry — watch entries
  that stop earning attention age out. Owner: Universe Curator.

- **Tier 3 — Active (hard-capped, tradeable).** The curated set with full
  per-name treatment: behavioral profile, regime calibration, Structural
  Analysis coverage, strategy eligibility. Initial cap: 50 names, per the
  launch Universe. Promotion from Tier 2 requires the profile to exist and
  Mike's approval; the cap means promotion may force eviction, and eviction
  criteria (profile decay, liquidity loss, thesis falsified) are recorded on
  the name's profile. Owner: Universe Curator, with Mike approving all Tier 3
  membership changes.

**Promotion and demotion are events.** Tier transitions publish to the bus
under ADR-0006 conventions:

- `research.universe-watch-added` / `research.universe-watch-expired`
- `research.universe-promoted` / `research.universe-evicted`

Each event carries the ticker, source tier, destination tier, elevation
mechanism, and a reference to the evidence record. The audit trail must be
able to answer "why is this name tradeable" the same way it answers "why did
the system trade."

**The tiers bound cost, not curiosity.** No agent may apply a Tier 3 filter
at ingest or discovery time. Tier filters apply only where per-name cost is
incurred: structural deep reads, behavioral profile maintenance, regime
calibration, and order flow. The Pre-Trade Checker gains one deterministic
rule: reject any order for a ticker not currently in Tier 3.

## Consequences

- The universe README is retitled and restructured around the three tiers.
  The 50-name draft list becomes the Tier 3 launch proposal, still awaiting
  Mike's lock-in.
- The Universe Curator agent spec (currently a derived-only consumer per
  ADR-0010's note) becomes the owner of Tier 2/3 state and the publisher of
  transition events.
- ADR-0011 (Forced-Proxy) gains a concrete landing zone: forced-proxy
  candidates stage into Tier 2 with the framework's evidence requirements as
  the elevation record. RKLB is the expected first test case, with the
  Iridium merger as its first structural watch entry.
- The Pre-Trade Checker requires a small change (Tier 3 membership check)
  and a data source for current Tier 3 membership.
- Structural Analysis (month 3-4) reads Tier 2 + Tier 3, not just the active
  set — findings on watch names are part of the promotion evidence.
- Nothing in this ADR changes sprint scope. Paper only; no real-money
  implications.

## Notes

The Iridium worked example is illustrative, not predictive, per the same
caveat as ADR-0010's Forced-Proxy examples. It is recorded because the manual
analysis (2026-07-19) exercised the full intended path — market-wide filing
appears, name elevates on materiality, structural findings accumulate (collar
floor mechanics, insider Form 4 cluster, bridge leverage), bias would attach
for the trading floor — and therefore serves as the acceptance narrative for
the pipeline this ADR structures.

Implementation note for the Structural Analysis ingest (month 3-4): the Tech
Watcher's EDGAR source polls ("10-K", "10-Q", "8-K"), which is correct for
the funnel's own purpose. The structural ingest additionally requires Form
425 (merger communications) and Form 4 (insider transactions) — both were
essential to the worked example above and are invisible to the current form
list. The extension belongs to the structural ingest, not the funnel.
