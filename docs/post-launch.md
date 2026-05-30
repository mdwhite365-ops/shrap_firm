# Post-Launch Backlog

**Document version:** 0.1 (draft)
**Last updated:** 2026-05-29
**Owner:** Mike White
**Status:** Living document — updated as the project evolves

---

## Purpose

This document is the deferred backlog for ideas that fit Shrap's long-term direction but are explicitly out of scope for the May-August 2026 sprint. It exists because the alternative — letting deferred ideas live in scattered notes — produces scope creep when each idea individually looks small.

Items here are categorized as **deferred-and-promising** (the idea has a clear hypothesis for value and a plausible path to implementation; it was deferred because the sprint has higher-priority work) or **deferred-and-uncertain** (the idea may or may not pay off; it was deferred because committing to it during the sprint would be premature).

This document does not own a timeline. Post-sprint sequencing happens after the sprint retrospective, informed by what the sprint revealed.

Read `00-vision.md` §"What Shrap is not" for the source list. This document is the expanded form of that section.

---

## Deferred-and-promising

These items have clear hypotheses for value. They were not included in the sprint because the foundational architecture had to land first, not because the ideas are weak.

### Full pipeline intelligence

**The idea.** Beyond news, filings, and social sentiment, there is a large class of leading-indicator data sources that retail traders do not read: job postings, government lobbying disclosures, patent filings, conference transcripts, FOIA results, and industry-specific regulatory dockets. Each of these is publicly available but operationally expensive to ingest at scale.

**Why it is promising.** The defense contractor subset of the universe is the clearest example. Lockheed, RTX, Northrop, and the smaller primes have lobbying disclosures, contract award histories, conference appearances, and patent activity that — read carefully — leak information about future revenue and program risk that is not yet in the public price discovery. The same pattern holds for biotech (patents, clinical trial registrations), energy (regulatory dockets), and infrastructure (federal funding allocations).

**Why deferred.** Each data source is a separate ingestion problem. Job postings come from a dozen scattered sources with hostile scraping environments. Lobbying data has structured filings but messy entity resolution. Patent data is volumous and the signal is subtle. The sprint cannot afford to spend month-long ingestion projects on individual sources before the core firm works.

**Path post-launch.** Pick the highest-signal source for the defense subset (likely lobbying disclosures plus DoD contract awards via SAM.gov) and build a single ingestion agent. Evaluate signal quality against universe-name returns over a quarter. If signal is real, expand to other sources one at a time, prioritized by per-source signal-to-cost ratio.

**Risk.** Most alternative data sources do not produce signal that survives transaction costs. The historical literature is full of "this data predicts returns" claims that fade under realistic execution. Treat every new source as a hypothesis to test, not a feature to ship.

---

### Polygon.io Level 2 / full tape integration

**The idea.** Replace Level 1 market data with Level 2 (full order book) and full tape (every print, including dark pool ATS feeds). Substantially expands the Trap Detection subsystem's input and enables a class of microstructure strategies that are currently impossible.

**Why it is promising.** The Sweep Detector's existing logic is the prototype: institutional fill patterns are detectable in tape data, and the structural advantage of detecting them is non-trivial. With full tape, the firm can identify trap setups before the trap fires, not only when the sweep is in progress. This is a direct extension of an existing edge, not a speculative new direction.

**Why deferred.** Polygon Level 2 costs real money monthly (estimated $200-500/mo at sprint scale). The ingestion volume is large enough to require dedicated handling — TimescaleDB hypertable partitioning, retention policies, and possibly a separate ingest pipeline outside the main NautilusTrader path. The sprint's paper trading does not require this level of data.

**Path post-launch.** Once the Sweep Detector has a documented baseline at Level 1, subscribe to Polygon Level 2 for the high-retail-interest subset of the universe (10-15 names). Build the expanded Trap Detection subsystem against that subset. Compare detection rate and signal quality against Level 1 baseline. If the marginal cost is justified, expand to the full universe.

**Risk.** Level 2 data is bandwidth-intensive. The Dell's data layer may not handle the load gracefully; the Ryzen would not be the right home either (it is not always available). This may force a hardware decision (additional always-on machine) or a vendor decision (cloud-hosted ingestion). Both are post-sprint complications.

---

### Advanced dealer gamma positioning analysis

**The idea.** Track market-maker options inventory and the resulting hedging flows. Dealer gamma exposure is a well-studied driver of intraday volatility patterns — when dealers are long gamma they suppress volatility; when short gamma they amplify it. The aggregate gamma profile influences which strategies are likely to work on a given day.

**Why it is promising.** Dealer gamma is not retail-accessible by default but is constructible from options open interest data. Several firms (SpotGamma, Tier1Alpha) productize this; Shrap could construct it independently from raw options data. The signal has documented predictive value for intraday volatility regime, which is directly useful for the Regime Classifier's statistical layer.

**Why deferred.** Construction is non-trivial. It requires options chain ingestion across the universe, a model for dealer positioning that is plausible without insider data, and careful validation. The Regime Classifier ships in month 2 without it; adding gamma as a regime input is a clean post-sprint extension, not a sprint requirement.

**Path post-launch.** Build the dealer gamma estimator as a new Intelligence Department agent. Validate against published estimates from one of the commercial sources for sanity. Expose as a regime feature in the Regime Classifier's input set. Evaluate whether regime classification improves measurably.

**Risk.** The dealer-positioning model is unavoidably an approximation. If the approximation drifts from reality during stressed market conditions, the signal could be actively misleading. Treat it as a feature input subject to the same rigorous evaluation as any other regime feature.

---

### Options strategies

**The idea.** Trade options on universe names, not just equities. Adds defined-risk position structures (verticals, calendars), volatility-explicit positioning, and access to event-driven setups (earnings, FDA decisions) that are awkward to express in equities.

**Why it is promising.** Options pricing is rich with information that does not flow back into equity prices cleanly. Implied volatility skew, term structure, and put-call ratio shifts are all signal sources the equity-only firm cannot use. Options also let the firm express views that equity cannot: "high probability of a small move, low probability of a large move" is not an equity trade.

**Why deferred.** The options Greeks add a state-management layer that the Trading Floor architecture does not currently handle. Risk Officer rules become substantially more complex (margin requirements, assignment risk, expiration handling). The Strategy Evaluator's backtest framework needs an options pricing model. None of this is impossible; all of it is months of careful work.

**Path post-launch.** Start with the simplest possible options strategy: defined-risk verticals on a small subset of universe names with known structural setups. Build the Greeks tracking and risk math as a separate options-aware subsystem rather than retrofitting it into the equity-focused Risk Officer. Migrate to broader options strategies only after the simple case works.

**Risk.** Options are unforgiving of operational error. An overnight gap on a short option position can produce losses that dwarf the position's stated risk. Real-money options trading should wait until paper has demonstrated discipline across at least one full earnings cycle for the universe.

---

### Fine-tuned local models for trading-specific tasks

**The idea.** Take Qwen 9B or Mistral Small 24B and fine-tune on accumulated trade data, signal labels, and Mike's annotation patterns. Produce models specialized for tasks where the base instruction-tuned model is workable but not strong: filing material-event extraction, hypothesis generation grounded in Shrap's specific universe and regime taxonomy, structural finding synthesis.

**Why it is promising.** The accumulated data from the sprint is genuinely unique — it captures Shrap's specific tasks and Mike's specific calibration in a way that no general-purpose model can replicate. Fine-tuning on this data is a clear path to lifting local model performance enough to retire cloud calls on more agents.

**Why deferred.** Fine-tuning requires accumulated data, which requires the sprint to have happened. It also requires fine-tuning infrastructure (LoRA training pipeline, evaluation harness, model registry) that does not exist yet. The Ryzen's RTX 4070 Super is capable of fine-tuning these models, but the pipeline is several weeks of work.

**Path post-launch.** Build the fine-tuning pipeline on the Ryzen. Start with the easiest target — Filing Processor's bulk summarization — where the input/output pairs are well-structured and quality is straightforward to evaluate. Compare fine-tuned model against base instruction-tuned model under the standard shadow-evaluation rubric. Expand to other targets one at a time.

**Risk.** Fine-tuned models can develop blind spots that the base model does not have, especially on input distributions different from the training set. The shadow-evaluation methodology in `llm-routing.md` is required; without it, fine-tuning can silently degrade.

---

### Full cloud-LLM retirement

**The idea.** Eliminate cloud LLM calls from the firm entirely. All work done by local models on Mike's hardware.

**Why it is promising.** This is the endpoint of the vision's "cloud is scaffolding" principle. Full retirement means: no vendor dependency, no API spend, no upstream service that can change terms or pricing. The firm's edge becomes fully sovereign.

**Why deferred.** Several agents (Decision Maker, Risk Officer, Implementation Agent) have cost-of-error profiles that justify cloud during the sprint. Local models may not reach quality parity on these specific tasks within a year. Pushing for full retirement before quality parity is reached would degrade the firm.

**Path post-launch.** Migration proceeds one agent at a time per the shadow-evaluation methodology. Decision Maker migrates last, if at all. The path is a sequence of justified migrations, not a hard cutover. Full retirement may take 12-24 months past sprint end, or may never happen if some agents genuinely require frontier-model capability.

**Risk.** This is the most quietly dangerous post-launch item. The temptation to retire cloud calls "because it would be nice to be sovereign" is real and would damage the firm if acted on prematurely. Migrate only when the shadow-eval evidence supports it.

---

## Deferred-and-uncertain

These items may or may not pay off. They are tracked because they fit the long-term direction, but committing to them now would be premature.

### Additional crypto pairs

**The idea.** Expand crypto coverage from the small initial allocation to a meaningful portion of the universe. Trade major pairs (BTC, ETH, SOL) actively with crypto-native strategies.

**Why uncertain.** Crypto market structure is different from equities in ways that make Shrap's existing architecture an uncertain fit. The regime taxonomy is equity-centric; the structural analysis lens does not transfer (no 10-Ks, no debt maturity calendars). Crypto-native edges exist but may require a fundamentally different agent set, which could blow the "one firm, multiple instruments" architecture.

**Path conditional on promotion.** First, demonstrate that the small BTC allocation works under the equity-shaped architecture. If it does, expand cautiously. If it does not, treat crypto expansion as a separate firm-shaped project rather than an extension of Shrap.

---

### MFFU evaluation

**The idea.** Take the system through a prop firm evaluation (MFFU or similar). If it passes, manage prop firm capital — providing leverage and capital scale without Mike's personal capital at risk.

**Why uncertain.** Prop firm evaluations have specific rules (daily loss limits, trailing drawdown, position sizing constraints, instrument restrictions) that are not the same as how Shrap is being built. Adapting the firm to fit an evaluation regime could distort its decision-making in ways that hurt performance, defeating the purpose. Conversely, passing an evaluation would be meaningful third-party validation that the system trades.

**Path conditional on promotion.** Only consider after the sprint has demonstrated 200+ paper trades with documented positive expectancy. Build an MFFU-shaped configuration as a separate Decision Maker profile rather than reshaping the main system. Run the evaluation as a constrained variant of the firm, not as a replacement of it.

**Risk.** MFFU evaluations are designed to be hard to pass. Many systems that perform well in normal paper trading fail evaluations because of the specific drawdown rules. The cost of attempting and failing is small; the cost of letting the evaluation reshape Shrap's design before the firm has proven itself is larger.

---

### Distributed multi-machine production deployment

**The idea.** Run production services across multiple always-on machines, not just the Dell. Add a second always-on host for redundancy, off-host monitoring, or load distribution.

**Why uncertain.** Single-host is the boring choice and is appropriate for sprint scale. The marginal complexity of multi-host (Kubernetes, distributed storage, etcd, service mesh) is large and almost certainly not justified by Shrap's actual load. But the single-host model is a hard ceiling — if the firm ever needs to grow past one Dell, the architectural rework is non-trivial.

**Path conditional on promotion.** Stay single-host until there is a documented reason not to. The most plausible trigger is real-money operation requiring better redundancy than a single host provides, at which point a second Dell-class machine running a hot standby is the boring answer, not Kubernetes.

---

### Investor-grade reporting

**The idea.** Produce reports of the quality that would be acceptable to outside capital allocators — formal monthly performance reports, attribution analysis, risk disclosures, audit-ready record-keeping beyond what Shrap currently maintains.

**Why uncertain.** Shrap is Mike's firm, not a fund. Investor-grade reporting is overhead for a constituency that does not exist. If Shrap ever takes outside capital — itself unlikely — that path may be one fund administrator subscription away rather than a Shrap-built capability.

**Path conditional on promotion.** Defer indefinitely unless there is a specific reason to invest in this. Building it speculatively is exactly the kind of feature work that the sprint's discipline is designed to reject.

---

### Real-money execution

**The idea.** Move from paper trading to live capital.

**Why uncertain.** This is the eventual endpoint of the firm, but the criteria for being ready are specific and high. Per `00-vision.md`: meaningful evidence of positive expectancy across at least 200 trades and a full set of regime conditions; documented risk discipline; reconciled audit trail; UPS in place; credential incident response procedure exercised; Mike's confidence in the system's self-honesty.

**Why deferred (rather than promising).** The probability that Shrap is genuinely ready for real money by sprint end is, per `00-vision.md`, 20-25%. The downside of going live prematurely is real money loss; the upside of waiting an extra quarter is small. The decision to go live is not made on a timeline — it is made when the criteria are met.

**Path conditional on promotion.** Earliest realistic window is end of Q4 2026, conditional on sprint outcomes. Real-money decision goes through a dedicated ADR. UPS prerequisite (hardware doc §6) must be in place. Live capital deployment is staged: smallest meaningful position size first, scaled up only as evidence supports it.

**Risk.** This is the highest-stakes item in this document. Treat all timeline discussions about it with suspicion. The system goes live when it is ready, not when a calendar says so.

---

## How items move out of this document

An item leaves this backlog by one of two paths:

1. **Promotion to active work.** An ADR is written justifying the promotion. The item gets a place in a future roadmap document. This document is updated to remove the item and link to the ADR.

2. **Permanent retirement.** Evaluation has shown the item does not pay off, or the firm has moved in a direction that makes it irrelevant. A short note is left in this document recording the retirement and the reasoning, so the question does not get re-asked.

Items do not silently disappear. The backlog is part of the firm's memory.

---

## What this document is not

This document is not a wishlist. Items here have been thought through enough to have a hypothesis for value and a plausible path. Random ideas without that level of thinking belong in `decision-queue.md` or a sketchpad, not here.

It is also not a roadmap. The sprint roadmap is `01-roadmap.md`. Post-sprint work will get its own roadmap when the sprint ends and the retrospective informs sequencing.
