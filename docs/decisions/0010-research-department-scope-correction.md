# ADR-0010: Research Department Scope Correction

**Status:** Accepted
**Date:** 2026-05-31
**Deciders:** Mike White

## Context

ADR-0007 was accepted to formalize a valuable research strategy: identifying
world-changing technologies, mapping their infrastructure dependencies, and
scouting for bottlenecks where capital is forced into substitutes.

That framework is good work and remains in effect. The problem is not the
world-changer / infrastructure graph / bottleneck method itself. The problem is
ADR-0007's scope claim.

ADR-0007 overreached by committing the entire Research Department and the firm
architecture to one thesis before Mike confirmed that this was the intended
scope. The ADR was written during an autonomous run, and it treated a strong
research pattern as if it were the firm's exclusive or primary research model.

The specific overreach appears in ADR-0007's Decision section:

> "Adopt a three-step Research funnel as Shrap's primary trading thesis,
> demoting regime classification to a sizing modifier and dissolving the
> distinction between Research and Structural Analysis."

That sentence contains three scope errors:

1. "primary trading thesis" makes one thesis framework sound like the firm's
   controlling research doctrine rather than the first formalized framework.
2. "demoting regime classification to a sizing modifier" removes the Regime
   Classifier's original role as a strategy-activation gate.
3. "dissolving the distinction between Research and Structural Analysis"
   collapses a distinct fault-line detection function into the world-changer
   bottleneck funnel.

ADR-0007 repeats those implications elsewhere. For example, it says:

> "The graph IS the trading universe. The universe is therefore derived
> continuously from the active world-changer set, not curated up front."

That is correct for Research Thesis Framework #1, but too narrow for Shrap as a
whole. A universe derived only from world-changer infrastructure graphs cannot
host other thesis families such as public-market forced proxies or structural
fault-line detection.

ADR-0007 also says:

> "It is no longer a strategy-activation gate. Strategies are activated by
> infrastructure graph state and bottleneck events; regime modulates how much
> size those strategies are allowed to take."

Again, that may describe one thesis framework, but it is too narrow for the
firm. Mike's actual intent is that Shrap should be fluid to market condition:
different regimes activate different research lenses and strategy classes.
Regime classification therefore matters not only for sizing, but also for which
lenses are actively scanning and which strategies are live.

Finally, ADR-0007 says:

> "Structural Analysis effectively merges into Research."

and:

> "the Structural Analysis Department's responsibilities are absorbed into the
> Research Department's funnel agents."

That merge is reversed by this ADR. Structural fault-line detection is different
in kind from world-changer bottleneck research. It reads many of the same
primary sources, but asks different questions and should retain its own scope.

Mike's corrected position is:

- World-changer + bottleneck + forced-substitute is one research strategy, not
  the only or primary strategy.
- The five documents under `docs/research/` and the three new Research agents
  created for that framework are retained.
- The Research Department should host multiple thesis frameworks over time.
- Regime classification should help decide which lenses are live, not merely
  how much size already-active strategies may take.
- Structural Analysis should remain separate from Research unless a future ADR
  deliberately redefines it.

This ADR supersedes ADR-0007's exclusivity claim while preserving its actual
framework as the first formalized research thesis.

## Decision

Shrap will operate multiple research theses in parallel, regime-gated by the
Regime Classifier. The Decision Maker consumes outputs from all research lenses
that are active in the current regime. The Regime Classifier informs both Risk
Officer sizing modulation and Decision Maker strategy-activation gating.

### 1. Supersede ADR-0007's exclusivity claim

ADR-0007 no longer defines Shrap's "primary trading thesis" in an exclusive or
firm-wide sense.

The following ADR-0007 content remains in effect:

- the world-changer identification step;
- the infrastructure graph mapping step;
- the bottleneck scouting step;
- the forced-substitute trading formula;
- the five research-framework documents under `docs/research/`;
- the three agent specs for Tech Watcher, Infrastructure Mapper, and Bottleneck
  Scout.

Those documents and agent specs do not need to change now. They correctly
specify one complete research thesis framework. What changes is the scope of
that framework relative to the rest of the firm.

### 2. Reframe ADR-0007 as Research Thesis Framework #1

ADR-0007's three-step funnel is hereby named:

**Research Thesis Framework #1: World-Changer + Bottleneck + Forced-Substitute**

The framework's core mechanism is:

> world-changer × saturating layer = forced substitute = trade.

This framework searches for cases where a transformational technology or
company runs into a physical, economic, regulatory, or supply-chain bottleneck
and capital is forced into the technology, vendor, or layer that relieves it.

Future thesis frameworks will be numbered as they are formalized:

- Research Thesis Framework #1: World-Changer + Bottleneck + Forced-Substitute.
- Research Thesis Framework #2: Forced-Proxy, to be specified in ADR-0011.
- Additional frameworks only when future ADRs define them.

This numbering creates room for more research patterns without rewriting
foundational ADRs each time.

### 3. Restore Structural Analysis as a separate department

The Structural Analysis Department remains separate from the Research
Department.

ADR-0007's folding of Structural Analysis into Research is reversed. Structural
Analysis owns concerns that are different in kind from world-changer bottleneck
research, including:

- debt maturities;
- credit markets;
- insider behavior;
- litigation activity;
- refinancing risk;
- slow-moving structural fault lines surfaced through primary-source reading.

Structural Analysis may read the same filings, credit data, and disclosure
corpus that Research reads. Shared sources do not imply shared departmental
scope. Research frameworks ask, "What thesis produces a tradable hypothesis?"
Structural Analysis asks, "What structural fault line, constraint, or hidden
risk changes how the firm should treat a name?"

The architecture document should be restored to the original two-department
structure for these lenses: Research and Structural Analysis are distinct.

### 4. Restore Regime Classifier as a strategy-activation gate

ADR-0007's move of the Regime Classifier from Research to Intelligence stands.
That move is acceptable: regime classification is a firm-level intelligence
input, not necessarily a Research-owned process.

However, the downstream role of the Regime Classifier is expanded beyond the
Risk Officer's position-sizing logic.

The Regime Classifier has two downstream consumers:

1. **Risk Officer:** receives regime context and sizing-modifier guidance.
2. **Decision Maker / strategy activation path:** receives regime context that
   informs which research lenses are active and which strategy classes are live.

The classifier's output should help answer:

- Which thesis frameworks should be actively scanning now?
- Which strategy classes are allowed to run in this regime?
- Which promoted strategies should remain dormant until conditions match their
  documented regime fit?
- How should position size be modulated once a strategy is active?

Sizing modulation is necessary, but not sufficient.

### 5. Register Forced-Proxy as Research Thesis Framework #2

This ADR formalizes the existence of the Forced-Proxy pattern as the second
research thesis framework the Research Department will develop.

The Forced-Proxy pattern is:

> Capital is forced into the only public way to play a category where the
> dominant player is private and uninvestable.

The trade is the sole credible public proxy. The public proxy can benefit from:

- fundamental share capture if it actually gains business because the category
  grows; and
- flow-dynamic valuation premium when public-market participants have no other
  listed instrument through which to express the category exposure.

Forced-Proxy is distinct from Forced-Substitute.

Forced-Substitute is a layer-of-infrastructure trade. A physical, economic,
regulatory, or supply-chain bottleneck forces capital into the technology that
replaces a saturating layer.

Forced-Proxy is a category-access trade. Private-market unavailability forces
capital into the single public comparable, sometimes beyond what fundamentals
alone would justify.

The mechanism, evidence sources, kill criteria, and sizing considerations are
different. Forced-Proxy is often more volatile because part of the trade can be
flow-driven, and that flow can reverse if the dominant private company becomes
investable or if other public comparables emerge.

This ADR does not fully specify Framework #2. ADR-0011 will define the full
forced-proxy thesis framework, its agent specs, its evidence requirements, its
kill criteria, and its research documents under `docs/research/forced-proxy/`.

### 6. Establish the multiple-thesis architecture principle

The Research Department is not a single-thesis department.

Shrap operates multiple research theses in parallel, with active scanning and
strategy eligibility gated by regime. World-Changer + Bottleneck +
Forced-Substitute is one thesis. Forced-Proxy is another. Structural fault-line
detection, owned by the Structural Analysis Department unless future ADRs decide
otherwise, is another lens feeding the Decision Maker.

Additional theses may be added over time by future ADRs, including but not
limited to smart-money asymmetry, trap detection, regime-aware momentum, and
mean-reversion under specific microstructure conditions. This ADR does not
commit the firm to those patterns. It commits the architecture to making room
for them.

### 7. Reframe Universe construction as merged, not derived-only

The Universe is both partially derived and partially curated.

Research Thesis Framework #1 contributes tradable nodes from world-changer
infrastructure graphs. That derived-universe mechanism remains valid for that
framework.

But the firm-wide Universe also grows from other sources:

- forced-proxy candidates surfaced by Framework #2;
- Structural Analysis candidates or watch-list names;
- existing launch names retained for regime-conditional and trap-detection
  work;
- future thesis frameworks added by ADR.

The 50-name list in `docs/universe/` is reframed as the Universe at sprint
launch, not as a permanently locked list and not as a seed graph derived only
from world-changer infrastructure mapping.

The Universe Curator Agent maintains the merged universe across all contributing
sources. It should not be limited to proposals from Infrastructure Mapper alone.

## Alternatives Considered

### (a) Keep ADR-0007 as-is

Rejected.

ADR-0007's world-changer bottleneck framework is valuable, but its scope does
not match Mike's actual intent. Keeping it as-is would over-commit the firm to
one thesis, make the Research Department less fluid to market conditions, and
hide the fact that Shrap needs multiple lenses running in parallel.

It would also leave Structural Analysis dissolved into a framework that does not
fully cover debt maturities, credit, insider behavior, litigation, and other
fault-line concerns.

### (b) Edit ADR-0007 in place

Rejected.

ADR-0007 is already Accepted. Editing its body to pretend the original decision
was narrower would violate ADR hygiene. Accepted ADRs are historical records.
When a decision changes, the correct move is to write a new ADR that supersedes
the old one and explains why.

ADR-0007's body should remain available for historical record, including the
parts this ADR corrects.

### (c) Write ADR-0010 as a supersede-and-replace

Selected.

ADR-0010 preserves the useful framework in ADR-0007 while correcting the scope
error. It gives future agents a clean rule: read ADR-0007 for Research Thesis
Framework #1, then read ADR-0010 for the corrected department and firm-level
scope.

## Consequences

- ADR-0007 is marked "Superseded by ADR-0010." Its body remains in the repo for
  historical record. Only the status block and a supersession note are updated.
- The vision document (`docs/00-vision.md`) should revert the ADR-0007 patch to
  the seven-point trading thesis section. The vision returns to the v0.1 framing
  of Shrap's primary edge as regime-conditional, with an explicit update that
  Research now operates multiple thesis frameworks in parallel.
- The architecture document (`docs/02-architecture.md`) should restore the
  Structural Analysis Department as a separate department, expand the Research
  Department section to acknowledge multiple thesis frameworks, and restore the
  Regime Classifier's strategy-activation gate role regardless of department
  ownership.
- The existing five `docs/research/*.md` files require no immediate changes.
  They correctly describe Research Thesis Framework #1 and do not need to claim
  firm-wide exclusivity.
- Existing Research agent specs for Tech Watcher, Infrastructure Mapper,
  Bottleneck Scout, Hypothesis Generator, and Strategy Evaluator require no
  immediate change for this ADR. The Hypothesis Generator may need a future
  update to consume from multiple thesis frameworks once Framework #2 exists.
  That is deferred work.
- The Regime Classifier spec at
  `docs/agents/intelligence/regime-classifier.md` should be updated later to
  reflect dual downstream consumers: Risk Officer for sizing modulation and
  Decision Maker / strategy activation for gating.
- `docs/universe/README.md` should be updated later to reframe the 50-name list
  as the launch Universe with an explicit growth mechanism across multiple
  contributing sources.
- The Universe Curator Agent spec should be updated later from a derived-only
  Infrastructure Mapper consumer to the maintainer of a merged universe from
  multiple research sources.
- No real-money scope changes. The sprint remains paper-only.

## Notes

Forced-Proxy examples are illustrative, not predictive. They describe the shape
of the pattern; they are not buy recommendations and do not mean the framework
will promote any particular ticker.

Illustrative examples:

- **RKLB / SpaceX:** SpaceX dominates reusable launch and remains private, so
  public-market capital seeking listed space-launch exposure can flow to Rocket
  Lab as a scarce public proxy.
- **MicroStrategy / Bitcoin:** Before spot Bitcoin ETFs, MicroStrategy became a
  public corporate Bitcoin vehicle and absorbed exposure-seeking flows that
  could not yet use a cleaner listed ETF.
- **Palantir / AI-defense:** Palantir functioned as one of the few public pure
  plays for AI-defense exposure before newer comparable listings broadened the
  category.
- **Coinbase / crypto:** Coinbase has served as the only major US-listed pure
  play crypto exchange for public-market participants seeking exchange exposure.
- **MP Materials / rare earths:** MP Materials has represented scarce public
  exposure to US-listed rare-earth processing when geopolitics made the category
  critical.
- **NextEra Energy Partners / renewable yield-co:** In some periods, NextEra
  Energy Partners functioned as one of the limited public renewable yield-co
  vehicles for investors seeking that exposure.

ADR-0011 will specify the Forced-Proxy framework in full. It should define the
research documents, agent specs, candidate-evidence requirements, flow-versus-
fundamental decomposition, kill criteria, and sizing implications.

Until ADR-0011 exists, Forced-Proxy is registered as an intended framework but
not yet operationalized.
