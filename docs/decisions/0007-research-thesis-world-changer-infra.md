# ADR-0007: Research Thesis — World-Changers, Infrastructure Graphs, Bottleneck Scouting

**Status:** Accepted
**Date:** 2026-05-30
**Deciders:** Mike White

## Context

The v0.1 vision document (`docs/00-vision.md`) framed Shrap's edge as the
combination of (a) a hand-curated 50-name universe, (b) a two-layer regime
classifier that activates regime-conditional strategies, and (c) rigorous
overfitting controls. The strategy archetypes were technical, the universe was
fixed at sprint launch, and the regime classifier was the load-bearing input
that determined which strategies were live at any given moment.

Three problems with that framing surfaced as the research department's specs
were drafted:

1. **The universe pre-locks the alpha source.** Picking 50 names up front
   commits the firm to whichever sectors and themes look interesting in May
   2026. If the real money is being made in a layer of the market the curators
   did not anticipate — say, optical interconnect inside the AI-compute build
   — the universe excludes it by construction. The curators (Mike + Universe
   Curator Agent) become the bottleneck on what the system can see, and the
   bottleneck operates on Mike's part-time attention.

2. **Regime classification is reactive, not predictive.** The two-layer
   classifier labels what the market is doing now and what historical periods
   it resembles. That is useful for sizing, hedging, and avoiding obvious
   mistakes. It is not where edge comes from. Most participants in liquid US
   equities have access to the same regime signal, and the analog layer's
   "what worked in 1998" reasoning is read by every macro desk on the street.
   Treating regime as the primary edge source over-weights a low-edge input.

3. **The biggest available edge for a part-time retail operator with strong
   primary-source reading discipline is identifying which physical layer of a
   transformational technology buildout absorbs the marginal dollar.** That is
   structural research, not regime classification. The Cisco-1999 lesson is
   the canonical reminder: being right that the internet would change the
   world and wrong about which layer captured the spend (routers vs. fiber vs.
   the eventual application layer) produced catastrophic losses. The
   NVIDIA-2024 lesson is the same shape in the other direction — being right
   that AI compute would scale and wrong about which interconnect/power/cooling
   layer would saturate next cost real money in pluggable optics, behind-the-
   meter nuclear, and liquid cooling names that ran while generic "AI plays"
   went sideways.

The Structural Analysis Department in the v0.1 vision was the closest existing
home for this work, but it was scoped as a slow secondary lens producing
"biases and sizing modifiers" for the Decision Maker. That scope under-weights
what should be the firm's primary research output.

## Decision

Adopt a three-step Research funnel as Shrap's primary trading thesis,
demoting regime classification to a sizing modifier and dissolving the
distinction between Research and Structural Analysis.

The funnel:

**Step 1 — World-Changer identification.** Continuously scan for companies and
technologies with a credible path to changing how the general population
lives, works, or consumes. Sources: 10-K / 10-Q / 8-K filings, arXiv
preprints, USPTO patent filings, conference keynotes (GTC, WWDC, Hot Chips,
JPM Healthcare, Reinvent), capex disclosures, hyperscaler infrastructure
announcements, regulatory approvals. The kill rate at this step is expected
to be 80%+ of candidates. Survivorship bias is the dominant failure mode and
must be flagged explicitly at every promotion: Theranos sounded credible,
hydrogen-by-2025 sounded credible, the metaverse sounded credible,
full-autonomy-by-2020 sounded credible. The output of step 1 is a small set
of surviving world-changer theses with explicit kill criteria (what
observation invalidates the thesis).

**Step 2 — Infrastructure graph mapping.** For each surviving world-changer,
build and maintain the full dependency graph of suppliers, enablers,
contractors, and downstream beneficiaries. Nodes are companies; edges are
the supply, licensing, or revenue relationships that route money through the
buildout. The graph IS the trading universe. The universe is therefore
derived continuously from the active world-changer set, not curated up front.
When a world-changer thesis is killed, its subgraph is removed. When a new
world-changer is promoted, its subgraph is added.

**Step 3 — Bottleneck scouting.** For each established world-changer, scan
continuously for layers of the infrastructure graph where the current
solution is hitting physical, economic, regulatory, or supply-chain limits.
The trade is the forced substitute — the technology, vendor, or layer that
necessarily replaces the saturating one. The trading formula:

> world-changer × saturating layer = forced substitute = trade.

The forced substitute is the trade because the substitution is non-optional —
the world-changer continues only if the bottleneck is relieved, so capital
must flow to whatever relieves it. This is where the edge is densest for a
patient retail operator with primary-source discipline, because the
saturation signal arrives in technical filings, supplier earnings calls, and
engineering conference talks before it arrives in price.

Regime classification (ADR pending — formerly `docs/agents/research/regime-classifier.md`)
moves to the Intelligence Department and is rewired to feed the Risk
Officer's position-sizing logic via `regime.sizing-modifier` events. It is no
longer a strategy-activation gate. Strategies are activated by infrastructure
graph state and bottleneck events; regime modulates how much size those
strategies are allowed to take.

## Alternatives Considered

**(a) Keep the curated 50-name universe.** The hand-curated universe is
simpler, easier to audit, and gives the regime classifier a stable
calibration target. Rejected: it pre-locks the alpha source to whichever
themes Mike and the Universe Curator Agent can name in May 2026. The
infrastructure-graph approach lets the system discover layers (224Gbps PAM4
copper signal-integrity walls; CoWoS capacity at TSMC; HBM bandwidth) that
the curators would not have listed up front. The cost is operational
complexity in the universe-maintenance loop; the benefit is that the system
can route capital toward where the marginal infrastructure dollar actually
goes, rather than where the curators guessed it would go.

**(b) Regime-classification-only thesis.** Drop the world-changer work and
double down on the two-layer regime classifier feeding regime-conditional
strategies on a fixed universe. Rejected: lower-edge and reactive rather
than predictive. The regime classifier labels what is, not what is about to
absorb capital. Many institutional desks read the same regime signals.
Concentrating the firm's edge on a layer where every counterparty also has
edge is the opposite of the principle that drove choosing a focused universe
in the first place.

**(c) Fundamental factor models.** Build a quant factor stack (value, quality,
momentum, low-vol, profitability) and trade factor tilts inside a fixed
universe. Rejected: not differentiated from existing quant. Renaissance,
AQR, Two Sigma, and every long/short equity shop run this playbook with
better data, better execution, and twenty years of head start. A part-time
retail operator running stale factors cannot compete with full-time quants
running fresh ones. The structural-research lane is where the asymmetry
favors a small operator with primary-source discipline.

## Consequences

**Universe becomes derived, not curated.** The 50-name list in
`docs/universe/README.md` is reframed as the SEED GRAPH — the initial
universe extracted from currently-obvious world-changers (NVIDIA AI compute,
GLP-1 obesity drugs, reusable launch, energy infrastructure for AI training).
The Universe Curator Agent continues to exist but its job changes: it
maintains the universe based on Infrastructure Mapper outputs, not on a
priori category targets.

**Universe Curator Agent role changes.** The agent no longer proposes
substitutions to a fixed list under fixed category counts. It ingests
Infrastructure Mapper graph updates, applies liquidity and tradability
filters, and proposes adds / removes for Mike's approval. The category
targets (12 ETFs + 8 mega-cap tech + ...) become advisory tags, not quotas.

**Regime Classifier moves to the Intelligence Department.** The spec moves
from `docs/agents/research/regime-classifier.md` to
`docs/agents/intelligence/regime-classifier.md`. Statistical and historical-
analog layers are unchanged. The downstream consumer changes from
Hypothesis Generator / Regime Router (strategy gating) to Risk Officer
(sizing modifier). The event topic for the new role is
`intel.regime.sizing-modifier`; the legacy `research.regime.changed` stream
remains for audit but is no longer consumed for strategy activation.

**Structural Analysis effectively merges into Research.** The primary-source
reading work that Structural Analysis was scoped to do (10-Ks, 10-Qs, 8-Ks,
debt maturity calendars, supply-chain disclosures, insider behavior, credit
markets, litigation activity) is the same work the Infrastructure Mapper and
Bottleneck Scout do, applied to a different question. Rather than running
two departments doing overlapping reads on the same corpus, the Structural
Analysis Department's responsibilities are absorbed into the Research
Department's funnel agents. The Structural Analysis Department directory
is retained but its agents are rewritten as Research agents in a follow-up
ADR.

**Three new Research agents.**

- **Tech Watcher** — performs Step 1. Continuously scans primary sources for
  world-changer candidates; produces and maintains world-changer thesis cards
  with explicit kill criteria; promotes survivors to the Infrastructure
  Mapper; surfaces survivorship-bias warnings on every promotion.
- **Infrastructure Mapper** — performs Step 2. For each promoted world-
  changer, builds and continuously updates the dependency graph; emits graph
  diffs as universe-change proposals; tags nodes with capacity / cycle-time /
  substitutability metadata that the Bottleneck Scout consumes.
- **Bottleneck Scout** — performs Step 3. Monitors each established world-
  changer's infrastructure graph for saturation signals at each layer;
  identifies forced substitutes; emits trade hypotheses to the Hypothesis
  Generator with the saturation evidence attached.

**Hypothesis Generator and Strategy Evaluator are rewritten.** The
Hypothesis Generator previously proposed regime-conditional technical
strategies grounded in regime label + universe member + historical analog.
It now consumes Bottleneck Scout outputs and proposes trades that express
the forced-substitute thesis (long the substitute, often paired with a short
or hedge against the saturating layer or the world-changer beneficiary that
would otherwise look like the cleaner trade). The Strategy Evaluator's
overfitting controls (walk-forward, PBO, deflated Sharpe, purged
cross-validation, realistic transaction costs, minimum trade counts) are
preserved verbatim, but the evaluator gains a new requirement: every
promoted hypothesis must be accompanied by an explicit bottleneck kill
criterion (what observation would tell the firm the saturation has been
relieved by a different substitute than the one being traded).

**Kill rates compound across the funnel.** Step 1 kills 80%+ of world-
changer candidates. Step 2 prunes graph nodes that fail liquidity or
tradability filters. Step 3 surfaces saturation signals but only a fraction
become tradable hypotheses. The Strategy Evaluator then kills 90%+ of
those. The principle "kill more aggressively than you promote" applies at
every step, and the cumulative survival rate from raw candidate to live
paper strategy is expected to be very low. Honest accounting of kill rates
per step is a required metric on the research dashboard.

**Survivorship-bias surveillance is a first-class responsibility.** Tech
Watcher's promotion logic must document, for every promoted world-changer,
the failure-mode prior: the list of comparable technologies that sounded
credible and did not transform anything. The base rate for world-changer
candidates surviving five years is brutal. Promoting without that base
rate visible in the thesis card is a process failure.

## Notes

**Validation strategy is dual-track.** Both:

- **Backwards test on Sept 2024 LITE / photonics.** Replay primary sources
  available as of Sept 2024 (NVIDIA Blackwell announcements, Hot Chips
  presentations on 224Gbps PAM4 signal integrity, photonics suppliers'
  earnings disclosures) through the funnel and verify that Tech Watcher
  would have promoted NVIDIA AI compute, the Infrastructure Mapper would
  have built the interconnect / power / cooling subgraph, and the
  Bottleneck Scout would have surfaced PAM4 copper saturation in time to
  generate LITE / COHR / FN / AOI hypotheses before their late-2024 / early-
  2025 moves. This is methodology debug, not edge proof — the analyst knows
  the answer, so the test only verifies the funnel does not break on a
  known case.

- **Forward-test from May 2026.** The real bet. Run the funnel live,
  paper-trade the hypotheses it generates, and measure realized edge
  against the kill criteria the funnel attaches. Forward-test results are
  the load-bearing evidence; the backwards test is scaffolding.

**Canonical worked example (Sept 2024 → 2025).** Used as the funnel
reference case across all three new agent specs.

1. **World-Changer (Step 1).** NVIDIA AI compute. The Blackwell rollout,
   hyperscaler capex disclosures, and the GenAI inference economics by Sept
   2024 made this the highest-conviction surviving world-changer thesis.
   Survivorship-bias check: the thesis is not "AI changes everything," it
   is the narrower claim that hyperscaler training and inference capex
   continues to compound at observable rates — falsifiable from CSP
   quarterly capex guides.

2. **Infrastructure graph (Step 2).** From NVIDIA outward: HBM suppliers
   (SK Hynix, Micron, Samsung), CoWoS packaging at TSMC, networking
   (ANET, CSCO), optical interconnect (LITE, COHR, FN, AOI, MRVL), power
   (CEG, VST, TLN, GEV), cooling (VRT), substrates and materials
   downstream. Each node tagged with capacity, cycle time to expand, and
   substitutability.

3. **Bottleneck (Step 3).** Inside the rack, 224Gbps PAM4 copper
   interconnect was running into signal-integrity limits — the physical
   wall where copper traces can no longer carry the required bandwidth at
   the required distance without unacceptable error rates. The forced
   substitutes were co-packaged optics (CPO) and linear pluggable optics
   (LPO), routing incremental dollars into LITE, COHR, FN, AOI for the
   pluggable / module layer and into silicon photonics integration inside
   ANET, CSCO, and NVDA's own designs. The trade was long the optical-
   interconnect substitutes, with the saturation evidence (engineering
   conference talks on PAM4 signal-integrity, hyperscaler interconnect
   roadmap disclosures, optical module suppliers' guidance revisions)
   attached as the kill criterion: if the next-generation copper standard
   solved the wall, the thesis dies.

**Other live bottleneck patterns the funnel is expected to surface.** Not
predictions — examples of the shape the Bottleneck Scout looks for:

- Hyperscaler power demand → behind-the-meter nuclear (CEG, VST, TLN) +
  gas turbines (GEV) as the forced substitutes for grid-constrained data
  center siting.
- Rack density 20kW → 200kW → liquid cooling (VRT) as the forced
  substitute for air cooling.
- HBM bandwidth as the next compute-side wall behind interconnect.
- CoWoS capacity at TSMC as a packaging bottleneck → glass substrates as a
  candidate forced substitute.
- GLP-1 demand → peptide CDMO capacity as the manufacturing bottleneck.
- Reusable launch → orbital bandwidth as the downstream saturation.

These are listed here for documentation continuity; whether any of them
survive Tech Watcher's promotion criteria and the Infrastructure Mapper's
graph build is for the funnel to determine, not for this ADR to prejudge.

**Relationship to prior ADRs.** This ADR does not change the message bus
(ADR-0001), envelope (ADR-0006), monitoring (ADR-0004), or alerting
(ADR-0005) decisions. It changes which agents publish to which streams and
what the Risk Officer / Hypothesis Generator consume. New stream topics
(`research.world-changer.*`, `research.infra-graph.*`,
`research.bottleneck.*`, `intel.regime.sizing-modifier`) will be added in
the envelope-registry update accompanying the agent-spec rewrites.

**Paper-only during the sprint.** No real-money execution of bottleneck
hypotheses during the sprint, consistent with the firm-wide constraint.
The forward-test is paper. Mike approves any change to that constraint
explicitly.

**Replaces.** This ADR supersedes the primary edge framing in
`docs/00-vision.md` v0.1 (the seven-point "trading thesis" section). The
vision document is patched to reflect the new framing and to point at this
ADR for the underlying reasoning.
