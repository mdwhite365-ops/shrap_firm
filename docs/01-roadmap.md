# Shrap: Sprint Roadmap (May 2026 – August 2026)

**Document version:** 0.1 (draft)
**Last updated:** 2026-05-29
**Owner:** Mike White
**Status:** Living document — updated as the project evolves

---

## Purpose

This document is the month-by-month plan for the four-month sprint that runs from May 2026 through August 2026, ending when classes start. It exists to answer two questions: what is the minimum viable shape of the firm at the end of each month, and what has to be true at each month boundary for the next month's work to be possible.

It is not a guarantee of delivery. The probability framing in `00-vision.md` applies — the sprint is structured around a 90% probability of a functioning paper-trading system, not a 100% probability. When something slips, the response is to update this document, not to pretend it did not slip.

Read `00-vision.md` first for the firm's purpose and `02-architecture.md` for the runtime system this roadmap builds. The success criteria for the sprint are defined in `00-vision.md`; this document does not restate them — see "Success criteria" at the end.

---

## Operating constraints

The roadmap is shaped by four hard constraints:

1. Mike has 1-2 hours per day on average. Work that requires Mike's continuous attention does not happen; work that lets agents make progress without Mike does.
2. The sprint ends when classes start in late August 2026. There is no extension.
3. Paper trading only. No real money at any point during the sprint. The Trading Floor talks to Alpaca paper and IBKR Gateway paper.
4. Boring beats clever. When a roadmap item starts to look clever, it gets simplified or deferred.

The three loops from `00-vision.md` are the organizing principle:

- **Inner loop** — Trading Floor executing validated strategies on live paper data
- **Middle loop** — Research generating and validating strategies; Bayesian updating; regime classification
- **Outer loop** — Development drafting specs, writing code, opening PRs for Mike

The roadmap milestones are organized by loop because the loops are the contract: a month is "done" when the loop boundaries hold.

---

## Probability framing

From `00-vision.md`, the honest probabilities:

- **Probability the system runs and trades on paper by end of month 4:** 90%
- **Probability of positive expectancy on paper over 200+ trades:** 45-55%
- **Probability of meaningfully beating SPY on a risk-adjusted basis during the sprint:** 25-35%
- **Probability the adaptive multi-strategy system beats its own best static strategy on the same universe:** 50-60%
- **Probability of surviving transition to real money with edge intact:** 20-25% (not a sprint goal)

The roadmap below is sized for the 90% scenario. It does not bake in optimism on the other numbers. If the system trades but loses money on paper, the sprint succeeded by its primary measure — the test was honest, and the result is informative.

---

## Month 1 — May 29 to June 30, 2026

**Theme:** Foundations. Inner-loop scaffolding and outer-loop bootstrap.

Month 1 is about making the system work as a system, not as a trader. The Trading Floor does not need to be making good decisions — it needs to be making any decisions that traverse the full path from market data to a paper order, with audit trail intact.

### Current implementation snapshot — 2026-06-21

The paper spine is now implemented through local event composition and paper order persistence consumption. `main` includes Decision Maker stub, Pre-Trade Checker service, Execution Agent service, Alpaca paper submit/status polling, full local paper-spine smoke, `trading.paper_order_events` persistence seam, and the paper order-event consumer core.

The remaining Month 1 closure work is operational rather than research-oriented: package the Paper Order Store consumer, add Alpaca paper reconciliation, run the full Docker Compose paper-spine smoke, observe a real paper fill, and resolve/document the ADR-0003 NautilusTrader bridge boundary. Research implementation remains intentionally deferred until those spine items are acceptable.

### Outer loop — Development Department

Outer loop comes online first because everything else gets built through it. By end of month 1, Mike should be reading PRs, not writing code.

- **Development Department containers up.** Spec Writer, Implementation Agent (OpenHands SDK), Code Reviewer. All running on the Dell. Implementation Agent's writable-path allowlist enforced via OpenHands sandbox. PR flow: agent opens PR → Code Reviewer reviews → Mike merges.
- **First non-trivial PR merged via the agent flow.** The bar is "Mike did not edit the code; he reviewed, requested changes, and merged." Candidate: the Audit Logger from the Operations Department, since it has no dependencies and exercises the full PR loop.
- **Deployment Agent: manual.** Deployment is by hand on the Dell this month. Automated Deployment Agent slips to month 2 once the manual process is well-understood.

### Inner loop — Trading Floor and Risk

- **Sweep Detector wrapped.** Mike's existing liquidation sweep detection logic is wrapped as a proper agent. This is the cheapest meaningful Trading Floor capability because the logic already exists.
- **NautilusTrader container live with Alpaca paper adapter.** Equities only. IBKR Gateway slips to month 3.
- **Pre-Trade Checker and Compliance Monitor operational.** No order reaches NautilusTrader without passing these. Position limits, drawdown limits, PDT tracking, wash sale rules. Deterministic, no LLM.
- **Execution Agent connected to NautilusTrader.** End-to-end paper order submission verified — a hand-crafted signal can traverse Decision Maker → Pre-Trade Checker → Execution Agent → NautilusTrader → Alpaca paper → fill back. The fill round-trips through Redis Streams (per ADR-0006) and lands in PostgreSQL.

### Operations and infrastructure

- **PostgreSQL + TimescaleDB, Redis, Qdrant, Langfuse, Ollama containers live.** All on the Dell, all managed via the Compose file in `Archive/compose/`. Snapshot policy configured (per hardware doc §5).
- **Prometheus + Grafana stack live (ADR-0004).** Node, cAdvisor, Redis, Postgres, Qdrant exporters running. Grafana dashboards minimal — Mike does not need pretty graphs in month 1, the Health Monitor needs a metrics substrate.
- **Audit Logger operational.** Every event on every Redis Stream is recorded to the PostgreSQL audit table. This is non-negotiable — the audit trail is the firm's answer to "why did it do that," and it has to be there from day one.
- **Health Monitor operational.** Queries Prometheus for container/service health. Publishes `operations.health-anomaly` events. No automated remediation beyond Docker restart policies.
- **Alert Agent operational (ADR-0005).** Discord webhook wired for routine. Self-hosted ntfy.sh container live with Mike's phone subscribed. Pushover fallback configured but inactive. Classification rules from ADR-0005 in place.
- **`shrap.events` library (ADR-0006) shipped.** Publish, subscribe, and validate helpers. Every producer/consumer in the firm uses this library — it is the only path to Redis Streams.

### Reporting

- **Alert Agent operational.** Already counted under Operations because it is functionally an Operations dependency for surfacing health and risk events.

### Universe and data

- **50-name universe drafted.** Mike approves the initial list. Per-ticker profiles are stubbed (template only); they fill in during months 2-3 as Intelligence and Structural agents come online.

### Exit criteria for month 1

- A hand-crafted signal traverses the full paper-trading path end-to-end, with audit trail.
- A non-trivial PR has been written by the Implementation Agent and merged by Mike without Mike touching the code.
- The Dell runs the production container stack 24/7 with no manual intervention required for routine operation.
- Mike's time investment averages under 2 hours per day across the month.

### What is NOT in month 1

- Regime classification (month 2)
- Strategy generation or evaluation (month 2)
- Intelligence agents (month 2)
- Structural Analysis (month 3)
- Bayesian Updater (month 3)
- IBKR Gateway adapter (month 3)
- Trading on real signals — the only "trading" in month 1 is signal-injection smoke tests

---

## Month 2 — July 2026

**Theme:** The middle loop comes online. Strategies start being generated and evaluated.

Month 2 is where Shrap starts to look like a trading firm rather than a scaffolding. The Research Department runs. The Trading Floor receives real strategy signals. The Intelligence Department produces context.

### Middle loop — Research Department

- **Regime Classifier operational.** Statistical layer first — VIX level, trend, breadth, dispersion, term structure. Publishes `research.regime-updated` events on a regular cadence. No LLM; this is statistical computation.
- **Hypothesis Generator operational.** Cloud (Claude Sonnet 4.6). Proposes strategies grounded in current regime, specific universe members, and (where available) intelligence signals. Output is structured strategy specifications, not free-text.
- **Strategy Evaluator operational.** VectorBT PRO walk-forward backtests with overfitting controls. Light backtests run on Dell; heavy backtests routed to Ryzen via `ryzen.tasks` stream. Kill rate expected to be 90%+.
- **Strategy Librarian operational.** Maintains the strategy registry in PostgreSQL. Tracks lifecycle state: `hypothesis` → `backtested` → `promoted` → `active` → `retired`.
- **First strategy promoted.** A real strategy passes the Evaluator, gets reviewed by the Risk Officer, lands as `promoted` in the registry, and gets activated by the Regime Router on the Trading Floor.

### Inner loop — Trading Floor (continued)

- **Decision Maker operational.** Cloud (Claude Sonnet 4.6). Combines active strategy signals, regime context, and (placeholder) structural biases into position decisions. Lives on cloud for the duration of the sprint per `00-vision.md`'s migration arc.
- **Regime Router operational.** Local (Qwen 9B). Reads `research.regime-updated` and activates/dormants strategies based on `regime_fit` and `regime_kill` metadata.
- **Risk Officer operational.** Cloud. Reviews strategy promotions; enforces Kelly-fractional sizing tuned by posterior edge estimates (placeholder until Bayesian Updater lands in month 3) and current regime.

### Intelligence Department

- **News Analyzer operational.** Local Qwen 9B for routine summarization; cloud escalation for material events. Publishes `intelligence.signal` events.
- **Filing Processor operational.** EDGAR 8-K filings for universe names. Material event extraction. Publishes `intelligence.signal`.

### Operations and infrastructure

- **Deployment Agent operational.** Webhook on PR merge → pull repo → rebuild container → `docker compose up -d`. Publishes `development.deployment-completed`. Manual deploy procedure documented for fallback.
- **Reconciliation Agent operational.** Nightly comparison of PostgreSQL position state vs Alpaca paper account. Publishes `operations.reconciliation-completed` or `operations.reconciliation-discrepancy`.
- **State Manager operational.** Owns `current-sprint.md`, `decision-queue.md`, `known-issues.md`, `recent-changes.md`. Updates them as event streams flow.

### Reporting

- **Daily Briefing Agent operational.** Cloud. Reads PostgreSQL + recent Redis events + status files. Generates a structured briefing posted to Discord and archived to `docs/reports/`.

### Platform

- **Cost Monitor operational.** Reads Langfuse traces. Tracks cloud spend by department and agent. Publishes `platform.cost-threshold-breach` if monthly budget approaches the $500 envelope (see `llm-routing.md`).

### Exit criteria for month 2

- The middle loop runs end-to-end: hypothesis → backtest → evaluation → promotion → activation → live paper signal → execution.
- At least one strategy is `active` in the registry and producing signals the Decision Maker receives.
- The daily briefing arrives in Discord every morning without manual intervention.
- Cloud LLM spend is tracked and within envelope.

### What is NOT in month 2

- Regime Researcher / historical analog layer (month 3)
- Bayesian Updater (month 3)
- IBKR Gateway / MES futures (month 3)
- Structural Analysis Department (month 3-4)
- Sentiment Monitor (month 3)
- Weekly Review Agent (month 3)

### Risk to month 2

The Hypothesis Generator is the new exotic component. If it produces nothing the Evaluator will pass, the kill rate is effectively 100% and no strategy reaches `promoted`. This is the most likely month 2 slip. Mitigations: seed the Hypothesis Generator with strategies Mike has historically traded as a sanity check on the validation pipeline; expand the parameter search space if all hypotheses get killed in the same way.

---

## Month 3 — August 2026 (early)

**Theme:** The Bayesian loop and structural lens. The system starts learning from its own outcomes.

Month 3 is where the firm becomes more than the sum of its monthly milestones. The Bayesian Updater closes the loop between live results and position sizing. The Regime Researcher adds the historical-analog layer. The Structural Analysis Department adds the slow-clock counterweight to the fast technical strategies.

### Middle loop — Research Department (continued)

- **Bayesian Updater operational.** Maintains posterior edge estimates per active strategy as live paper results accumulate. Risk Officer's Kelly sizing reads from these posteriors. This is the mechanism by which the firm calibrates its own confidence over time.
- **Regime Researcher operational.** Cloud. Produces historical-analog regime classifications on a weekly or macro-inflection cadence. Writes regime profiles to `docs/regimes/`. Publishes `research.regime-analog-updated`.
- **Full promotion pipeline.** `hypothesis` → `backtested` → `promoted` → small-size paper → standard-size paper → retired. Each stage gated by the Risk Officer with explicit promotion criteria recorded.

### Inner loop — Trading Floor (continued)

- **IBKR Gateway adapter live.** MES futures available on the Trading Floor. First crypto pair (BTC via Alpaca) considered if scope allows; deferred otherwise.
- **ADR-0003 resolved.** During Trading Floor spec finalization, the NautilusTrader-to-Redis bridge coverage is verified against actual adapter behavior. Gaps documented; bridge extended or architectural adjustment recorded as a follow-up ADR.

### Intelligence Department (continued)

- **Sentiment Monitor operational.** Reddit, StockTwits for high-retail-interest universe names. No LLM — structured extraction.
- **Market Structure Reader operational.** Level 1 options flow, volume anomalies. Polygon Level 2 deferred to post-launch.

### Structural Analysis Department

- **Filing Deep Reader operational.** Cloud. 10-K and 10-Q processing for universe names. Structural risk indicator extraction.
- **Watch List Curator operational.** Cloud. Synthesizes Structural Analysis findings into a ranked watch list. Updates weekly. Writes to `docs/universe/structural-watchlist.md`. Publishes `structural.bias-updated`.
- **Insider Behavior Tracker operational.** Local. Form 4 cluster detection.

### Reporting

- **Weekly Review Agent operational.** Cloud. Deeper weekly report covering regime transitions, strategy lifecycle, system health, roadmap progress. Posted to Discord, archived in `docs/reports/`.

### Exit criteria for month 3

- Bayesian Updater is influencing position sizing on live paper trades.
- Regime Researcher has produced at least one historical-analog regime profile.
- Structural Analysis is publishing biases that the Decision Maker is consuming (not necessarily acting on heavily — biases modify sizing, not entries).
- Weekly review arrives Sunday evening reliably.
- ADR-0003 resolved.

### What is NOT in month 3

- Debt and Credit Monitor (month 4 — requires FRED pipeline first)
- LLM Migration Evaluator with real shadow data (month 4)
- Multi-strategy correlation analysis for portfolio construction (deferred)
- Live capital (deferred — not a sprint goal)

### Risk to month 3

Bayesian Updater requires accumulated live-result data to be meaningful. If month 2 strategies produced too few trades, the posteriors are noisy and the Risk Officer is effectively flying blind on sizing. Mitigation: seed the Updater with prior distributions derived from backtest performance, with explicit decay weights so that live data dominates as it accumulates.

---

## Month 4 — August 2026 (late, ends when classes start)

**Theme:** Hardening. Closing the loops. Honest measurement.

Month 4 is not about adding capability. It is about making what already exists work reliably, audit cleanly, and produce honest reports. The temptation to add one more thing should be resisted by Mike, the Development Department, and the roadmap itself.

### Hardening — all loops

- **Debt and Credit Monitor operational.** Last Structural Analysis agent. Requires FRED macro data pipeline, which lands earlier in month 4 as a prerequisite.
- **LLM Migration Evaluator operational.** Shadow evaluations run on cloud-primary agents that have accumulated enough trace data (per `docs/infrastructure/llm-routing.md`). At least one migration proposal expected — likely Strategy Librarian or Alert Agent moving formally from "default local" to "validated local."
- **At least one agent migrated to local based on shadow-eval evidence.** Per the stretch criterion in `00-vision.md`. Most likely candidate: Alert Agent's routine classification path, which has plentiful event volume and clear rubrics.

### Honest measurement

- **200+ paper trades cleared the system by end of sprint.** This is necessary to evaluate the positive-expectancy probability in any meaningful way. If trades are well below 200, that itself is informative about strategy generation throughput.
- **End-of-sprint retrospective.** A long-form review of what worked, what did not, what surprised, what the data is telling Mike about the architecture. Posted to `docs/reports/sprint-retrospective.md`. Written by the Weekly Review Agent under Mike's direction, edited by Mike.
- **Audit-trail validation.** Pick five trades — best, worst, median, most surprising, most controversial — and trace each from event back through inputs to root cause. The audit trail either supports this end-to-end or it does not. Document the gaps.

### Exit criteria for month 4

The sprint exit criteria are the success criteria from `00-vision.md`. See "Success criteria" below.

### What is NOT in month 4

- New capabilities that have not already been built
- Real-money execution
- MFFU evaluation prep
- Pipeline intelligence (job postings, lobbying, patents, conferences)
- Advanced dealer gamma
- Options strategies
- Full cloud-LLM retirement

These are all tracked in `docs/post-launch.md`.

### Risk to month 4

The dominant risk in month 4 is scope creep — agents have been built, the system is interesting, and there is a real temptation to add "just one more strategy type" or "just one more intelligence source." Each one taxes Mike's review time and the system's hardening time. The roadmap commitment is: nothing new in month 4 that was not promised in months 1-3 unless Mike explicitly removes something else from scope.

---

## Cross-cutting workstreams

A few things do not fit cleanly into one month because they happen continuously.

### Spec writing

Every department's agents need specifications. Specs follow `docs/agents/_template.md`. They are version-controlled, reviewed by Mike, and the source of truth for what an agent does. Implementation Agent reads the spec, writes code to match.

Specs are written in advance of the agent. The order is: Mike directs → Spec Writer drafts → Mike approves → Implementation Agent writes code → Code Reviewer reviews → Mike merges PR → Deployment Agent deploys. When the spec changes, the code follows; when the code reveals the spec was wrong, the spec is updated first.

### Ticker profile maintenance

Per `00-vision.md`, each of the 50 universe names has a maintained profile. Profiles are stubbed in month 1 and filled in by the Intelligence and Structural agents as they come online. The Universe Curator Agent proposes additions and removals for Mike's approval; the sprint does not require it to be operational, but at minimum Mike does a manual review at the end of each month.

### Cloud LLM spend tracking

Cost Monitor publishes spend numbers daily into the briefing. Sprint envelope: $200-500/month, expected to fall as agents migrate to local. If spend trends toward the high end of the envelope, the response is to push migrations forward, not to raise the envelope.

### Documentation hygiene

Every ADR is appended-only. Every spec is version-controlled. Every architecture change requires an ADR. This is not optional — the audit trail and the architecture document together are the firm's working memory.

---

## Dependencies and sequencing

Some things must come before other things. The sequencing constraints:

- Audit Logger before any agent that publishes events (month 1, day one)
- Risk gates (Pre-Trade Checker, Compliance Monitor) before any order to Alpaca
- `shrap.events` library before any producer or consumer of Redis Streams
- Regime Classifier before Hypothesis Generator (which uses current regime as input)
- Strategy Evaluator before Strategy Librarian (which records promotions)
- Strategy Librarian before Regime Router (which reads the active strategy set)
- Reconciliation Agent before any trust in PostgreSQL position state
- FRED macro pipeline before Debt and Credit Monitor

Anything else can be reordered within a month if dependencies are honored.

---

## Slip handling

When something slips, the response is structured:

1. The owning agent or Mike updates `decision-queue.md` and `known-issues.md` to reflect the slip
2. The roadmap is updated — this document — to reflect new realistic timing
3. Downstream dependencies are evaluated: does this slip cascade?
4. If the slip threatens the sprint's exit criteria, scope is cut from later months to absorb the slip

What does not happen: pretending the slip did not happen, working extra hours to catch up (Mike's time is the constraint), or shipping something that was supposed to be ready but is not actually working.

---

## What the sprint deliberately does not aim for

To prevent scope creep, this roadmap explicitly excludes:

- Real-money trading at any point during the sprint
- MFFU evaluation
- Live execution of crypto strategies (paper only for the small crypto allocation, if it ships at all)
- Options strategies
- Polygon Level 2 / full tape
- Pipeline intelligence (job postings, lobbying, patents, conferences)
- Advanced dealer gamma positioning
- Fine-tuned local models for trading-specific tasks
- Full cloud-LLM retirement
- Kubernetes migration
- Multi-user access; this is Mike's firm, not a platform

These are tracked in `docs/post-launch.md` as deferred-and-promising or deferred-and-uncertain.

---

## Success criteria

The sprint's success criteria are defined in `00-vision.md` under "What success looks like":

- Minimum, target, stretch tiers
- What success is not

See `00-vision.md` for the full criteria. This document does not restate them — they live in vision because they are vision-level commitments, and they are referenced here rather than duplicated to prevent drift.

The honest probability framing in `00-vision.md` applies. The sprint is sized to deliver minimum success at 90% probability. Target success is more like 60-70% probability conditional on minimum success. Stretch success is genuinely uncertain — that is what makes it stretch.

---

## What this document is

This roadmap is a working artifact, not a contract. It is updated as the sprint unfolds. When a milestone slips, the slip is documented here. When scope is cut, the cut is recorded here. When something works better than expected, the surplus is reinvested in hardening, not in adding scope.

The two principles from `00-vision.md` that govern roadmap discipline:

- **Kill more aggressively than you promote.** Roadmap items that have not started by their target month are candidates for cut, not extension.
- **Mike is the architect, not the implementer.** If Mike is writing code to keep a roadmap item on schedule, the schedule is wrong, not Mike.

The roadmap serves the firm. The firm does not serve the roadmap.
