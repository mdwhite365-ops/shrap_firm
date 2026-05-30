# Shrap: Architecture

**Document version:** 0.1 (draft)
**Last updated:** 2026-05-13
**Owner:** Mike White
**Status:** Living document — updated as the project evolves

---

## 1. Overview

This document describes how Shrap is built. The vision in `00-vision.md` explains what the firm is and why it exists. This document explains the runtime system that implements it: components, data flows, agent deployment, and the infrastructure decisions that hold it together. Read `00-vision.md` first.

The architecture is organized around three constraints: Mike has 1-2 hours per day, the sprint ends in August 2026, and boring beats clever. Every decision is tested against those constraints before it's allowed to get clever.

This document covers:

- The three loops and their runtime boundaries
- The nine departments and their failure-isolation model
- Hardware topology: what runs where and why
- Data layer: PostgreSQL + TimescaleDB, Qdrant, Redis Streams, and the repo as primary knowledge store
- Inter-department communication via Redis Streams
- Runtime architecture: tooling integration, agent packaging, and lifecycle management
- LLM routing during the sprint and the migration arc toward local-first
- State, memory, observability, and the audit trail

This document does not cover:

- Per-agent specifications — see `docs/agents/`
- Regime profiles — see `docs/regimes/`
- Ticker profiles — see `docs/universe/`
- LLM routing in detail — see `docs/infrastructure/llm-routing.md`
- Post-sprint capabilities — see `docs/post-launch.md`

Architectural decisions made in this document are recorded as ADRs in `docs/decisions/`. The first — the choice of Redis Streams as the cross-department message bus — is in `docs/decisions/0001-redis-streams-message-bus.md`. Where a decision remains genuinely unsettled, it appears in section 2 below, not buried in the relevant section.

The sprint target is a fully autonomous paper-trading system running on the Dell by August 2026. This architecture is sized for that target. It is not designed for high-frequency throughput, multi-asset coverage, or real-money reliability — all explicitly out of scope per the vision.

---

## 2. Open Questions

These are the architectural questions that remain unresolved as of this draft. Each has downstream consequences for what gets built and in what order. They are listed here rather than embedded in later sections so they cannot be overlooked.

**1. NautilusTrader-to-Redis event bridge coverage (ADR-0003).**
The broker credential isolation model in section 11 holds only if NautilusTrader's Redis bridge is comprehensive enough that no other department has a legitimate reason to need direct broker API access. The completeness of fill, account, and position event coverage against NautilusTrader's actual adapter capabilities has not been verified. Decision needed before: Trading Floor spec.

The three previously-listed open questions — monitoring stack, alerting channel, and Redis Streams event envelope — have been resolved in ADR-0004, ADR-0005, and ADR-0006 respectively.

---

## 3. The Three Loops

Shrap operates as three concurrent loops, each running at a different time scale. They are not sequential — all three run simultaneously. What changes between them is the clock they run on and the kind of work they do.

**Inner loop (seconds to minutes): the trading floor.**
The inner loop is what most trading systems implement and stop at. It reads market data, evaluates active regime-conditional strategies, generates signals, sizes positions, and executes orders through NautilusTrader. It runs continuously during market hours and maintains a reduced watch during extended hours for overnight risk events. The inner loop does not generate strategies or modify itself — it only executes what the middle loop has validated and the regime router has activated.

**Middle loop (hours to days): research and strategy lifecycle.**
The middle loop is where strategies are born, tested, promoted, and killed. The Hypothesis Generator proposes new strategies grounded in current regime, historical analogs, universe context, and outputs from active research thesis frameworks. The Strategy Evaluator runs walk-forward backtests on VectorBT PRO, applies overfitting controls, and produces a promotion recommendation or rejection. Promoted strategies enter a staged pipeline: paper trading at full size → paper trading with performance tracking → eventually live capital. The Bayesian Updater continuously revises posterior estimates of each active strategy's edge as live results accumulate. The Regime Router reads current regime classification and activates or dormants strategies accordingly. The middle loop runs around the clock — backtests and hypothesis generation do not wait for market hours.

**Outer loop (days to weeks): self-development.**
The outer loop is what makes Shrap more than a trading bot. The Development Department reads the approved roadmap, drafts specifications for new agents and capabilities, implements code in OpenHands-sandboxed environments, opens PRs for Mike's review, deploys approved changes, and monitors for regressions. This loop is what allows Mike's 1-2 hours per day to compound — every approved PR expands the firm's capability without consuming Mike's time to implement it. The outer loop does not touch trading or risk policy directly; it builds the system that does.

**How the loops interact.**
The loops communicate through Redis Streams, not through direct calls. A strategy promoted by the middle loop publishes a `strategy.promoted` event; the Trading Floor consumes it and activates the strategy at the next regime-appropriate window. A regime change detected by the Intelligence Department publishes an `intel.regime.changed` event; the Regime Router and Decision Maker consume it to adjust which strategies and research lenses are active, while the Risk Officer consumes regime context for sizing modulation. The Development Department publishes a `deployment.completed` event when a new agent is live; Operations consumes it and adds the new agent to its health-check roster. This event-driven boundary is what provides failure isolation — a crash in Research cannot deadlock the Trading Floor.

One boundary is deliberately hard: the outer loop cannot modify the inner or middle loops without a PR reviewed and merged by Mike. The Development Department has no write access to `trading/`, `risk/`, or `execution/` paths. This is enforced by OpenHands path restrictions and by the repo's branch protection, not by convention.

---

## 4. Departments and Agent Roster

The firm is organized into nine departments. Each department owns its internal state machine, runs as one or a small set of Docker containers, and communicates with other departments exclusively through Redis Streams. What follows is a summary of each department's role and sprint-period scope. Per-agent specifications live in `docs/agents/`.

A department is a logical grouping, not a physical one. Most departments run as one container; a few (Trading Floor, Research) run as two or three coordinated containers due to internal complexity. Container layout is defined in `docker-compose.yml`.

The subsection format is consistent across all nine departments: role, key agents, primary interfaces, LLM tier, sprint scope, and deferred items.

---

### Development Department

**Role.** Builds and maintains the firm itself. Reads the approved roadmap, drafts agent specifications, implements code in sandboxed environments, opens PRs for Mike's review, and deploys approved changes. The firm's capacity to expand without consuming Mike's implementation time depends entirely on this department functioning well.

**Key agents.** Spec Writer (drafts agent specs from Mike's direction), Implementation Agent (writes code via OpenHands SDK), Code Reviewer (reviews Implementation Agent output before PR), Deployment Agent (updates Docker Compose, triggers container restarts post-merge).

**Primary interfaces.** Reads: `current-sprint.md`, `decision-queue.md`, approved roadmap, relevant agent specs and architecture docs. Writes: draft specs to `docs/agents/`, PRs to GitHub, `recent-changes.md` post-deploy. Publishes: `deployment.completed` to Redis Streams. Consumes: `mike.approved` events (PR merges triggering deployment).

**LLM tier.** Cloud (Claude Sonnet 4.6 via OpenHands). Heaviest code-generation tasks may route to Opus 4.7. Migration target: Ryzen-hosted Mistral Small 24B for routine spec drafting by post-sprint evaluation; Implementation Agent stays on cloud longest due to code quality requirements.

**Sprint scope.** Operational by end of month 1. Initial agents: Spec Writer, Implementation Agent, Code Reviewer. Deployment Agent added in month 2 once manual deploy process is understood.

**Deferred.** Automated fine-tune pipeline for local code-generation models. Self-improving test suite generation. Automated regression benchmarking across deploys.

---

### Research Department

**Role.** Generates and validates trading strategies through multiple formal research thesis frameworks. ADR-0007, as scoped by ADR-0010, defines Research Thesis Framework #1: World-Changer + Bottleneck + Forced-Substitute. Future frameworks, beginning with the Forced-Proxy framework planned for ADR-0011, are added only by ADR. The Research Department is not a single-thesis department; it hosts multiple thesis frameworks and routes their outputs into Hypothesis Generator and Strategy Evaluator. The kill rate on generated hypotheses is expected to exceed 90% — that is a feature, not a failure.

**Key agents.** Tech Watcher (Framework #1 world-changer identification), Infrastructure Mapper (Framework #1 dependency graph mapping), Bottleneck Scout (Framework #1 saturation and forced-substitute detection), Hypothesis Generator (proposes strategies from active thesis-framework outputs + regime + universe context), Strategy Evaluator (runs walk-forward backtests on VectorBT PRO, applies overfitting controls), Bayesian Updater (maintains posterior edge estimates from live results), Strategy Librarian (maintains the strategy registry in PostgreSQL, tracks lifecycle state). Additional framework-specific agents are added by future ADRs.

**Primary interfaces.** Reads: universe profiles from `docs/universe/`, strategy registry from PostgreSQL, macro and market data from NautilusTrader and external sources, regime context from the Intelligence Department, and thesis-framework documents under `docs/research/`. Writes: thesis cards, graph updates, bottleneck evidence, backtest results, and strategy records to PostgreSQL and/or repo documents as specified by each framework. Publishes: framework-specific research events, `strategy.promoted`, `strategy.retired`, `strategy.hypothesis.generated`. Consumes: `intelligence.signal`, `intel.regime.changed`, and thesis-specific source feeds.

**LLM tier.** Framework-specific research agents use the tier declared in their specs. Hypothesis Generator: cloud (Claude Sonnet 4.6) during sprint. Strategy Evaluator: no LLM — deterministic VectorBT PRO execution. Bayesian Updater: no LLM — statistical computation. Strategy Librarian: local (Qwen 9B) sufficient for registry maintenance tasks.

**Sprint scope.** Framework #1 agents operate as the first formalized research thesis. Hypothesis Generator and Strategy Evaluator operational by end of month 2. Regime context is supplied by the Intelligence Department's Regime Classifier. Additional frameworks are not implemented until their ADRs and agent specs are approved.

**Deferred.** Combinatorial purged cross-validation (computationally expensive; walk-forward validation ships first). Strategy-to-strategy correlation analysis for portfolio construction. Automated regime-specific hypothesis campaigns. Multi-framework Hypothesis Generator routing once Framework #2 is specified.

**Strategy ownership.** Strategy code is owned by Research; it is read by the Trading Floor (specifically the Decision Maker and Regime Router) but not modified there. Strategy module files live under `strategies/` in the repo and are referenced by ID from the registry.

---

### Trading Floor

**Role.** Executes validated, regime-activated strategies on live market data. Makes position-sizing decisions, routes orders through NautilusTrader, enforces real-time risk limits, and maintains the live portfolio state. The Trading Floor is the only department that touches real orders — paper or otherwise.

**Key agents.** Decision Maker (combines active strategy signals, structural biases, regime context, and per-strategy posterior edge estimates from the Bayesian Updater into a position decision), Regime Router (maintains the active strategy set based on current regime, activates and dormants strategies), Execution Agent (interfaces with NautilusTrader for order routing and fill tracking), Sweep Detector (wraps Mike's existing liquidation sweep detection logic as a proper agent).

**Primary interfaces.** Reads: market data from NautilusTrader (Alpaca adapter for equities, IBKR Gateway for MES), active strategy set from Regime Router, structural biases from Structural Analysis via Redis Streams. Writes: fills, positions, and order records to PostgreSQL. Publishes: `order.submitted`, `order.filled`, `position.updated`, `risk.breach` to Redis Streams. Consumes: `strategy.promoted`, `strategy.retired`, `regime.updated`, `structural.bias.updated`, `risk.veto`.

**LLM tier.** Decision Maker: cloud (Claude Sonnet 4.6) during sprint — stays on cloud longest due to cost of error. Regime Router: local (Qwen 9B) — classification task, not synthesis. Execution Agent and Sweep Detector: no LLM — deterministic execution logic.

**Sprint scope.** Paper trading only. Alpaca adapter for equities active by end of month 2. IBKR Gateway for MES active by end of month 3. Sweep Detector operational by end of month 1 (existing logic, wrapping and integration work only).

**Deferred.** Live capital deployment. MFFU evaluation. Level 2 tape integration for expanded trap detection. Options strategies.

---

### Intelligence Department

**Role.** Reads news, financial filings, social sentiment, market structure data, and regime state for the active universe. Produces structured signals that feed the Hypothesis Generator, the Decision Maker's context, and the Risk Officer. The Intelligence Department does not make trading decisions — it produces inputs that better-position other agents to make them.

**Key agents.** Regime Classifier (statistical-state and historical-analog regime classification; moved from Research by ADR-0007 but scoped by ADR-0010 as both a strategy-activation gate and sizing input), News Analyzer (reads and summarizes news relevant to universe names), Filing Processor (reads EDGAR 8-K filings for universe names, extracts material events), Sentiment Monitor (tracks social sentiment signals — Reddit, StockTwits — for high-retail-interest universe names), Market Structure Reader (tracks options flow, dark pool prints, and unusual volume patterns available from Level 1 data).

**Primary interfaces.** Reads: EDGAR API, news APIs, social APIs, Level 1 market data from NautilusTrader. Writes: structured signal records to PostgreSQL, full text to Qdrant. Publishes: `intelligence.signal`, `intel.regime.changed`, and `intel.regime.sizing-modifier` to Redis Streams. Consumes: universe profile updates from `docs/universe/`.

**LLM tier.** News Analyzer and Filing Processor: local (Qwen 9B on Dell) for routine summarization; cloud escalation for material events requiring higher-quality synthesis. Sentiment Monitor and Market Structure Reader: no LLM — structured extraction and pattern matching.

**Sprint scope.** News Analyzer and Filing Processor operational by end of month 2. Sentiment Monitor by end of month 3. Market Structure Reader by end of month 3 (Level 1 only; Level 2 deferred).

**Deferred.** Polygon.io Level 2 / full tape integration. Job postings, lobbying disclosures, patent filings, conference transcripts. Fine-tuned local model for trading-specific entity extraction.

---

### Structural Analysis Department

**Role.** Reads primary sources — 10-Ks, 10-Qs, debt maturity calendars, supply chain disclosures, insider behavior, credit markets, litigation filings — for the 50-name universe. Produces a continuous watch list of structural concerns and opportunities. Outputs are biases and sizing modifiers to the Decision Maker, not entry signals. This department operates on a slow clock; the base rate for actionable findings is low, but the asymmetric payoff per finding is high.

**Key agents.** Filing Deep Reader (processes 10-K and 10-Q filings for the universe, extracts structural risk indicators), Debt and Credit Monitor (tracks debt maturity schedules, credit spread movements, and refinancing risk), Insider Behavior Tracker (tracks Form 4 filings and cluster insider activity), Watch List Curator (synthesizes findings into a ranked structural watch list, updated weekly).

**Primary interfaces.** Reads: EDGAR full-text filings, FRED macro data, Form 4 filings. Writes: structured findings to PostgreSQL, full text to Qdrant, watch list to `docs/universe/structural-watchlist.md`. Publishes: `structural.bias.updated` to Redis Streams. Consumes: universe profile updates.

**LLM tier.** Filing Deep Reader and Watch List Curator: cloud (Claude Sonnet 4.6 or Opus 4.7 for complex synthesis). Debt and Credit Monitor and Insider Behavior Tracker: local (Qwen 9B) — structured data extraction, not synthesis.

**Sprint scope.** Watch List Curator and Filing Deep Reader operational by end of month 3. Insider Behavior Tracker by end of month 3. Debt and Credit Monitor by end of month 4 (requires macro data pipeline from FRED to be in place first).

**Deferred.** Dealer gamma positioning analysis. Options market structural reads. Supply chain network analysis. Litigation outcome probability modeling.

---

### Risk and Compliance Department

**Role.** Enforces guardrails across the entire firm with veto power over all other departments. No order reaches NautilusTrader without passing a real-time risk check. No strategy is promoted without a Risk Officer review. This department is not a gatekeeper that can be circumvented — its `risk.veto` event stops execution regardless of what any other department has decided. The Pre-Trade Checker runs in-process with the Execution Agent as a synchronous gate; risk checks on the order path do not traverse the message bus. Asynchronous risk evaluation (strategy promotion review, drawdown trending) runs through Redis Streams.

**Key agents.** Risk Officer (enforces position limits, drawdown limits, correlation caps, Kelly-fractional sizing; reviews strategy promotions), Pre-Trade Checker (real-time pre-trade risk check before every order submission), Compliance Monitor (enforces regulatory constraints: no wash sales, PDT rule tracking, position concentration limits).

**Primary interfaces.** Reads: live portfolio state from PostgreSQL, active strategy list, current regime, Kelly parameters. Writes: risk event records to PostgreSQL. Publishes: `risk.veto`, `risk.breach`, `risk.policy.updated`. Consumes: `order.submitted` (pre-trade check), `strategy.promoted` (promotion review), `position.updated`.

**LLM tier.** Risk Officer: cloud (Claude Sonnet 4.6) for strategy promotion reviews. Pre-Trade Checker and Compliance Monitor: no LLM — deterministic rule enforcement. LLM involvement in real-time order path is explicitly avoided; latency and reliability requirements rule it out.

**Sprint scope.** Pre-Trade Checker and Compliance Monitor operational by end of month 1 — these are prerequisites for any paper trading. Risk Officer (strategy promotion review) operational by end of month 2.

**Deferred.** Real-money-grade risk controls (daily VaR, counterparty exposure, margin utilization). Regulatory reporting. Automated kill switch for live capital.

---

### Operations Department

**Role.** Keeps the lights on. Monitors system health, reconciles portfolio state against broker records, maintains the audit trail, manages state persistence, and detects and alerts on anomalies. The Operations Department is the firm's immune system — it does not generate alpha, but its failure makes alpha impossible.

**Key agents.** Health Monitor (monitors container health, Redis connectivity, database availability, Tailscale connectivity; escalates to Reporting on anomaly), Reconciliation Agent (compares internal portfolio state against Alpaca and IBKR account records nightly), Audit Logger (writes immutable audit records for every cross-department event to a dedicated PostgreSQL table), State Manager (owns the living status files: `current-sprint.md`, `decision-queue.md`, `known-issues.md`, `recent-changes.md`).

**Primary interfaces.** Reads: container health endpoints, broker account APIs (Alpaca, IBKR), all Redis Streams (audit logging). Writes: audit records to PostgreSQL, health status to Redis, status files to repo. Publishes: `health.anomaly`, `reconciliation.completed`, `reconciliation.discrepancy`. Consumes: all streams (for audit), `deployment.completed`.

**LLM tier.** No LLM for routine operations. Health Monitor and Reconciliation Agent: deterministic. State Manager: local (Qwen 9B) for status file synthesis. Anomaly triage may escalate to cloud if the pattern is novel and requires synthesis.

**Sprint scope.** Audit Logger and Health Monitor operational by end of month 1 — prerequisites for everything else. Reconciliation Agent by end of month 2. State Manager by end of month 2.

**Deferred.** Automated incident response playbooks. Self-healing container restarts beyond basic Docker Compose restart policies. Distributed tracing across the full agent call graph.

---

### Reporting Department

**Role.** Keeps Mike informed without overwhelming him. Produces daily briefings, weekly reviews, and urgent alerts calibrated to Mike's 1-2 hours per day. The Reporting Department reads the firm's state so Mike does not have to dig through logs. Its output is the primary surface through which Mike exercises architectural direction.

**Key agents.** Daily Briefing Agent (generates a structured daily summary: overnight events, active positions, strategy performance, structural watch list changes, items requiring Mike's decision), Weekly Review Agent (generates a deeper weekly report: regime transitions, strategy lifecycle changes, system health trends, roadmap progress), Alert Agent (generates and dispatches urgent alerts for risk breaches, system anomalies, and decision-queue items requiring immediate attention).

**Primary interfaces.** Reads: PostgreSQL (positions, strategy performance, audit log), Redis Streams (recent events), status files, structural watch list. Writes: reports to `docs/reports/` (append-only). Publishes: `report.daily.generated`, `report.weekly.generated`, `alert.urgent`. Consumes: `risk.breach`, `health.anomaly`, `reconciliation.discrepancy`, `strategy.promoted`, `strategy.retired`.

**LLM tier.** Daily Briefing Agent and Weekly Review Agent: cloud (Claude Sonnet 4.6) — report synthesis requires quality. Alert Agent: local (Qwen 9B) for routine alerts; cloud escalation for novel anomalies requiring interpretation.

**Sprint scope.** Alert Agent operational by end of month 1. Daily Briefing Agent by end of month 2. Weekly Review Agent by end of month 3.

**Deferred.** The alerting channel to Mike is unsettled (Open Question 2). Until resolved, alerts are logged to PostgreSQL and surfaced in the daily briefing. Monthly trajectory analysis. Investor-grade reporting (not applicable during sprint).

---

### Platform Department

**Role.** Manages the firm's self-expansion as it matures. Tracks cloud LLM spend, identifies agents ready for local model migration, proposes hardware upgrades for Mike's approval, and manages the Tailscale network configuration. During the sprint, this department is minimal — most of its work becomes relevant post-sprint as the migration arc progresses.

**Key agents.** LLM Migration Evaluator (runs shadow evaluations comparing cloud vs. local model output for each agent type; recommends migrations when quality parity is demonstrated), Cost Monitor (tracks cloud API spend by department and agent; alerts when monthly budget thresholds are approached), Infrastructure Planner (proposes hardware and network changes for Mike's approval; maintains `docs/infrastructure/`).

**Primary interfaces.** Reads: Langfuse traces (LLM call logs, quality metrics), cloud API billing endpoints, current agent model assignments. Writes: migration evaluation reports to PostgreSQL, infrastructure proposals to `docs/infrastructure/`. Publishes: `llm.migration.recommended`, `cost.threshold.breach`. Consumes: `deployment.completed` (to update model assignment registry).

**LLM tier.** LLM Migration Evaluator: cloud (Claude Sonnet 4.6) for evaluation synthesis. Cost Monitor and Infrastructure Planner: local (Qwen 9B) sufficient.

**Sprint scope.** Cost Monitor operational by end of month 1 — cloud spend visibility is required from day one. LLM Migration Evaluator by end of month 4 (meaningful shadow data not available until agents have run for several months). Infrastructure Planner as needed, no fixed milestone.

**Deferred.** Fine-tuning pipeline for local models on accumulated trade data. Full cloud-LLM retirement. Kubernetes migration (explicitly deferred indefinitely; Docker Compose on single host is the production target).

---

## 5. Hardware Topology

Shrap runs on three machines. Each has a defined role. They are not interchangeable.

**Dell Precision 5820 — production tier.**
The Dell is the always-on production environment. It hosts everything that must run continuously: the trading floor, all intelligence and operations agents, the message bus (Redis), the primary database (PostgreSQL + TimescaleDB), the vector store (Qdrant), Langfuse, and the Ollama instance serving local models. The Dell is the only machine on the production trading path. If the Dell is down, the firm is down.

Current GPU: GTX 1080, used for Ollama inference. Upgrade to RTX 2070 Super is planned for the early build period; the architecture does not depend on the specific card, only on Ollama being available with sufficient VRAM for Qwen 9B as the default classification model.

The Dell runs Docker Compose. All production containers are defined in the repo's `docker-compose.yml`. Persistent volumes (PostgreSQL data, Qdrant indices, Redis Streams log) are mounted to paths on TrueNAS storage, which provides redundancy for the data layer independent of the compute.

Services hosted on the Dell:
- Redis (message bus + ephemera)
- PostgreSQL + TimescaleDB (relational + time-series)
- Qdrant (vector store)
- Langfuse (LLM tracing)
- Ollama (local LLM serving, Qwen 9B default)
- All department containers except heavy Ryzen-routed workers

**Ryzen 7 7800X with RTX 4070 Super — research and inference tier.**
The Ryzen is an on-demand resource, not always-on. It is available via Tailscale and activated for workloads that exceed what the Dell can handle efficiently: heavier local models (Qwen 14B, Mistral Small 24B), VectorBT PRO backtest runs (CPU-bound, benefits from the faster cores), and the Development Department's heavier code-generation tasks. The Ryzen is not on the real-time trading path — latency-sensitive work stays on the Dell.

The Ryzen runs its own Ollama instance, serving models that are too large for the Dell's current GPU. Routing to the Ryzen is event-driven: the requesting agent publishes to a `ryzen.tasks` stream; a worker process on the Ryzen consumes from that stream, executes the task, and publishes results to a `ryzen.results` stream that the requester consumes. The Ryzen never initiates a connection; it only reads task events and writes result events.

**MacBook Pro M4 24GB — developer tier.**
The MacBook is Mike's interactive environment. It is used for Claude Code sessions, on-the-go document review, and direct repo work. It is not part of the production trading path and does not run production containers. It connects to the Dell and Ryzen via Tailscale for remote agent monitoring and to pull logs and reports.

**Network and trust model.**
The three machines form a private network over Tailscale. The Dell is the trust anchor: it runs the message bus, the database, and the canonical container stack. The Ryzen accepts work routed from the Dell and returns results; it does not initiate connections to the trading core. The MacBook has read access to all services for monitoring and review, but production writes go through git and PR workflows — not directly to the Dell's running containers.

No port is exposed to the public internet. All inter-machine communication traverses Tailscale. API credentials (Alpaca, IBKR, Anthropic, EDGAR) are stored as environment variables in a `.env` file on the Dell, excluded from the repo, and are not transmitted to the Ryzen or MacBook except as needed for task execution.

---

## 6. Loop Boundaries and Handoffs

The three loops are failure-isolated by design. What crosses the boundaries between them — and how — determines whether that isolation holds in practice.

**Research → Trading Floor: strategy promotion.**
When the Strategy Evaluator passes a strategy and the Risk Officer approves promotion, the Strategy Librarian updates the strategy's lifecycle state in PostgreSQL and publishes a `strategy.promoted` event to Redis Streams. The Regime Router on the Trading Floor consumes this event, checks the promoted strategy's `regime_fit` tags against the current regime state, and either activates the strategy immediately or holds it dormant until regime conditions are met. The Trading Floor does not poll the strategy registry — it reacts to events. If the `strategy.promoted` event is missed (container restart, network interruption), the Regime Router will catch up on reconnect via Redis Streams consumer group replay.

The strategy's code module is not transmitted over the bus. The event carries the strategy ID and version. The Trading Floor loads the strategy module from a read-only volume that mirrors the repo's `strategies/` directory at the deployed commit. New strategy versions reach the Trading Floor only through the Deployment Agent's container update process (described below), not through any direct git read at runtime.

**Intelligence → Decision Maker: signal delivery.**
The Intelligence Department publishes structured `intelligence.signal` events to Redis Streams. Each event carries a ticker, signal type, confidence score, source reference (a PostgreSQL record ID or Qdrant document ID for the full text), and a TTL after which the signal should be considered stale. (The exact field names and types are part of the unresolved event envelope schema — see Open Question 3 — but TTL semantics will be required regardless of how the schema is finalized.) The Decision Maker consumes these events and holds a short-term signal window in memory — signals that have arrived since the last position evaluation, weighted by confidence and freshness. Signals do not trigger immediate trades; they modify the context in which the next strategy signal is evaluated.

The full text of the underlying document (a news item, filing extract, or structural finding) is never on the bus. The Decision Maker fetches it from Qdrant only if it needs to reason about it directly — which is the exception, not the rule.

**Structural Analysis → Decision Maker: bias delivery.**
Structural Analysis publishes `structural.bias.updated` events on a slower cadence — typically after each weekly watch list refresh or when a material finding warrants immediate attention. Each event carries a ticker, bias direction (positive/negative/neutral), magnitude, and an expiry. The Decision Maker holds these biases as persistent state, refreshed on each update event. A structural bias does not override a strategy signal; it modifies position sizing and acceptable risk on that ticker. A strong negative structural bias on a name does not prevent the system from trading it — it reduces the maximum allowed size.

**Development Department → running agents: deployment.**
When Mike merges a PR, the Deployment Agent detects the merge event (via GitHub webhook or polling), pulls the updated repo, rebuilds the affected container image, and issues a `docker compose up -d` for the changed service. The Operations Department's Health Monitor observes the container restart and publishes a `deployment.completed` event to Redis Streams. Agents that depend on the restarted service reconnect via their normal retry logic — Redis consumer groups preserve position in the stream, so no events are lost during a brief restart window.

The Development Department cannot trigger a deployment that touches `trading/`, `risk/`, or `execution/` containers without a PR approved by Mike. This is enforced by OpenHands' path denylist and by a branch protection rule requiring Mike's review on any PR that modifies those paths. The gate is in the repo, not in the deployment agent.

**Operations → Reporting → Mike: anomaly delivery.**
When the Health Monitor detects a container failure, missed heartbeat, or service degradation, it publishes a `health.anomaly` event to Redis Streams. The Alert Agent consumes this stream and decides whether the anomaly is urgent (requires interrupting Mike) or routine (folded into the next daily briefing). Urgent alerts route to whatever alerting channel is settled (Open Question 2); routine anomalies are logged to PostgreSQL and surfaced in the Daily Briefing Agent's next report. Reconciliation discrepancies between internal state and broker records follow the same path — anomaly published, severity classified, routed accordingly.

**Failure containment.**
Each department's container fails independently. If the Intelligence Department crashes, the Trading Floor continues executing against existing strategy signals with no new intelligence context — acceptable degradation. If the Research Department crashes, no new hypotheses are generated and no promotions are processed, but the Trading Floor continues running active strategies — acceptable degradation. If the Risk and Compliance Department crashes, the Pre-Trade Checker goes offline; the Execution Agent is configured to halt order submission if it cannot confirm a passing risk check — not acceptable degradation, so the system stops trading until Risk is restored. This is a deliberate choice: in the absence of a passing risk check, the safe default is no trade, not a trade against a stale risk state. The cost of being offline for minutes is small; the cost of trading without a working risk gate could be large.

The Operations Department's Health Monitor is responsible for detecting crashes and alerting Mike. It does not attempt automated restarts beyond Docker Compose's built-in `restart: unless-stopped` policy, which handles transient failures. Persistent failures require Mike's attention.

---

## 7. Data Architecture

Shrap uses four data stores, each with a distinct role. They do not overlap in purpose and are not substitutes for each other.

**PostgreSQL + TimescaleDB — relational and time-series state.**
PostgreSQL is the system of record for everything that is structured, queryable, and durable: portfolio positions, order history, fill records, strategy registry entries, backtest results, risk events, agent audit logs, and reconciliation records. TimescaleDB adds the time-series extension that makes market data storage and querying practical — continuous aggregates, time-bucket queries, and automatic data retention policies run natively without a second database engine. All departments read and write through a single PostgreSQL instance on the Dell. Schema migrations are version-controlled in `db/migrations/` and applied by the Deployment Agent as part of container updates.

Market data — OHLCV bars, tick data from Alpaca, and macro series from FRED — is stored in TimescaleDB hypertables partitioned by time. Historical data retention policy: full resolution for two years, downsampled after. This is a provisional default; the Platform Department may revise it once data volume and access patterns are observed in production. NautilusTrader's internal catalog handles real-time bar aggregation during market hours; end-of-day, the relevant series are persisted to TimescaleDB for the Research Department's backtest access.

**Qdrant — semantic search and document retrieval.**
Qdrant indexes every document that agents need to retrieve by semantic similarity rather than exact lookup: news summaries, filing extracts, structural analysis findings, intelligence signals, ADRs, agent specs, and strategy rationales. Agents write to Qdrant after producing a document; they read from Qdrant when they need historical context that is not in their immediate task scope. The collection structure follows document type: one collection per major category (filings, news, strategies, decisions). Each document record stores the embedding vector alongside a PostgreSQL record ID as a reference — Qdrant finds the document, PostgreSQL holds the canonical record. Qdrant does not replace the repo; it indexes it.

**Redis — message bus and ephemera.**
Redis serves two roles: the cross-department message bus (via Redis Streams, committed in ADR-0001) and ephemeral fast-access state. Ephemeral state includes the Regime Router's current active strategy set, the Decision Maker's live signal window, and Health Monitor heartbeat timestamps. Nothing in Redis is treated as durable; all state that must survive a Redis restart is also written to PostgreSQL. The `ryzen.tasks` and `ryzen.results` streams described in section 5 are also Redis Streams on this instance, reachable from the Ryzen over Tailscale.

**The repo — primary knowledge store.**
The git repository is the canonical location for all durable knowledge that is not structured data: vision, architecture, ADRs, agent specs, regime profiles, ticker profiles, living status files, and reports. Agents read directly from the repo's working tree at task start. Nothing that belongs in a document lives only in a database. This design means the repo is queryable via Qdrant (indexed), diffable via git, and readable by humans without a database client. It also means knowledge does not decay silently — version history is the audit trail for all documented decisions.

---

## 8. Runtime Architecture

**LangGraph — agent orchestration.**
Each department is implemented as a LangGraph subgraph. The subgraph defines the department's internal state machine: which agents run in what order, how errors are handled, and what events trigger state transitions. Departments do not call each other's subgraphs directly — inter-department coordination is exclusively event-driven through Redis Streams. LangGraph's checkpointing mechanism persists agent state to PostgreSQL between invocations, which means agents can be interrupted and resumed without losing in-progress context. The per-department subgraph model provides failure isolation: an exception in the Intelligence subgraph raises within that graph's exception handler, publishes a `health.anomaly` event, and stops — it does not propagate to other subgraphs.

**OpenHands SDK — development sandbox.**
The Development Department's Implementation Agent runs inside an OpenHands Docker-sandboxed environment with a fresh repo clone per task. Writable paths are allowlisted: `docs/`, `agents/`, `intelligence/`, `research/`, `tests/`, `tools/`, `strategies/`. Everything under `trading/`, `risk/`, `execution/`, and `compliance/` is read-only. Credentials paths (`.env*`, `*api_key*`, `*credentials*`) are excluded from the sandbox entirely. Every task produces a PR; OpenHands cannot merge to main. This boundary means the outer loop (self-development) cannot modify the inner loop (trading) without Mike's explicit review.

**NautilusTrader — execution engine.**
NautilusTrader is the trading engine for the inner loop. It handles real-time bar aggregation, strategy signal evaluation, order routing, fill tracking, and position management. Two adapters are wired: the Alpaca adapter for equities (paper trading with live Level 1 data), and the IBKR Gateway adapter for MES futures. The adapter pattern is the reason provider swaps are low-cost — Polygon.io Level 2 and any future broker are additional adapter implementations, not architectural changes. NautilusTrader's internal pub/sub bus handles intra-engine events; selected events (fills, risk alerts, regime signals from the Regime Router) are bridged to Redis Streams for consumption by other departments. NautilusTrader runs as its own container on the Dell and owns the `trading/` path in the repo.

**VectorBT PRO — backtesting.**
VectorBT PRO runs backtest workloads submitted by the Strategy Evaluator. Computationally light backtests (small universe slices, short date ranges) run on the Dell. Heavy backtests (full universe, multi-year, walk-forward windows) are routed to the Ryzen via the `ryzen.tasks` stream. The Strategy Evaluator submits a backtest job as a serialized configuration — universe slice, date range, strategy module ID, parameter set, cost model — and waits for a result record on `ryzen.results`. VectorBT PRO does not read from NautilusTrader's live data path; it reads historical OHLCV data from TimescaleDB. This keeps the backtest path fully decoupled from the live trading path.

**Ollama — local LLM serving.**
Ollama runs on both the Dell (Qwen 9B, default classification and summarization model) and the Ryzen (Qwen 14B, Mistral Small 24B for heavier tasks). Agents that are assigned to a local model tier address Ollama via HTTP on the local network; the model assignment per agent is defined in each agent's spec and tracked by the Platform Department's Cost Monitor. Cloud LLM calls go directly to the Anthropic API. The LLM routing decision — local vs. cloud — is made at the agent level, not at a gateway. There is no LLM proxy in front of either service during the sprint; adding one is a Platform Department post-sprint item.

**Langfuse — LLM observability.**
Langfuse is self-hosted on the Dell and receives traces for every LLM call made by any agent. Each trace records the model used, input tokens, output tokens, latency, and a structured tag set (department, agent name, task type). This is the Platform Department's Cost Monitor's primary data source for cloud spend tracking, and the LLM Migration Evaluator's primary source for quality comparison between cloud and local model outputs. Langfuse does not cover non-LLM agent behavior — container health, database performance, and Redis throughput are the Operations Department's monitoring stack (Open Question 1).

**Agent packaging and lifecycle.**
Each agent is a Python process running inside a Docker container. The container image is defined by a `Dockerfile` in the agent's directory; the `docker-compose.yml` in the repo root wires images, volumes, environment variables, and restart policies together. All containers use `restart: unless-stopped` — transient failures restart automatically; persistent failures surface as a Health Monitor anomaly. When the Deployment Agent deploys a new agent, it adds the container definition to `docker-compose.yml`, builds the image, and issues `docker compose up -d`. When an agent is retired, its container is removed from `docker-compose.yml` and stopped; its data remains in PostgreSQL and Qdrant for audit purposes.

Agents that share a department but require isolation (e.g., the Execution Agent and Sweep Detector on the Trading Floor) run as separate containers in the same Compose service group. Agents that are logically the same but scaled horizontally (not applicable during the sprint) would use Compose's `scale` option — not Kubernetes.

---

## 9. LLM Routing

Every agent in the firm is assigned to one of three model tiers: cloud-primary, local-primary, or no-LLM. The tier assignment is declared in each agent's spec and tracked by the Platform Department. The assignment can migrate over the sprint and post-sprint as local model quality is validated — migration is documented in `docs/infrastructure/llm-routing.md`.

**Cloud-primary agents** call the Anthropic API directly, using Claude Sonnet 4.6 as the default and escalating to Opus 4.7 for tasks that demonstrably require it. Cloud-primary is the default for any agent whose errors have direct financial or architectural consequences: the Decision Maker, the Risk Officer, the Hypothesis Generator, and the Development Department's Implementation Agent. Cloud cost is accepted here because the cost of a degraded output is higher than the API bill.

**Local-primary agents** call Ollama on the Dell (Qwen 9B) or, for heavier tasks, Ollama on the Ryzen (Qwen 14B or Mistral Small 24B). Local-primary is assigned to agents whose tasks are well-bounded classification or extraction problems: the Regime Router (classification), the Strategy Librarian (registry maintenance), the State Manager (status file synthesis), the Alert Agent (routine alert generation), and the News Analyzer and Filing Processor for their bulk summarization workload. Local agents fall back to cloud when the local instance is unavailable — this is handled in the agent's LLM client wrapper, not in the routing layer.

**Cloud fallback.** When the Anthropic API is unavailable — outage, rate limit, or local network failure — cloud-primary agents fall back to the highest-quality local model the task can tolerate. For most agents this is Mistral Small 24B on the Ryzen. The fallback is logged as a degraded operation, surfaces in the next daily briefing, and the Decision Maker reduces position sizing by 50% on any signal evaluated during the degradation window. The Risk Officer's strategy promotion reviews are postponed rather than degraded; promotion is not a time-sensitive decision and waits for the cloud connection to restore.

**No-LLM agents** are deterministic Python processes. The Pre-Trade Checker, Compliance Monitor, Reconciliation Agent, Strategy Evaluator's VectorBT PRO runner, and the Execution Agent contain no LLM calls. They are faster, cheaper, and more predictable than LLM-based equivalents for their respective tasks — rule enforcement and numerical computation do not benefit from language model reasoning.

**The migration arc.** The sprint begins cloud-heavy, with local LLMs handling only the tasks where Qwen 9B quality is clearly sufficient. Over the sprint and post-sprint, the LLM Migration Evaluator runs shadow evaluations: for each cloud-primary agent, it feeds the same inputs to the local model and scores the outputs against the cloud outputs on a rubric defined in the agent's spec. When a local model passes the rubric on a rolling window of 50+ tasks, the Platform Department proposes migration to Mike for approval. The Decision Maker migrates last, if at all. Routing detail and per-agent migration milestones are documented in `docs/infrastructure/llm-routing.md`.

---

## 10. State and Memory Model

Shrap's state is distributed across four layers with a clear hierarchy. The hierarchy exists so that any agent, at the start of any task, knows exactly where to look for current truth.

**Layer 1: the repo (durable knowledge).** The canonical source for anything that is documented: vision, architecture, ADRs, agent specs, regime profiles, ticker profiles, living status files, and reports. Version-controlled, diffable, human-readable without tools. Agents read repo documents at task start to load the context relevant to their work. No agent holds a local copy of a repo document beyond the duration of a single task.

**Layer 2: PostgreSQL (structured state).** The canonical source for anything that is queryable structured data: positions, fills, strategy registry state, backtest results, risk records, and the full audit log. PostgreSQL is append-preferred — records are updated only when lifecycle state genuinely changes (e.g., a strategy moving from `paper` to `retired`); historical records are not modified. LangGraph checkpoint state is stored here, allowing agents to resume after interruption.

**Layer 3: Redis (live ephemera).** The canonical source for state that must be low-latency and is acceptable to lose on restart: the active strategy set, the Decision Maker's current signal window, heartbeat timestamps. Everything here is also written to PostgreSQL before or shortly after; Redis holds the hot copy. The message bus (Redis Streams) is also here — event log is durable within Redis's persistence configuration, but departments treat it as a delivery mechanism, not a store of record.

**Layer 4: agent working memory (task-scoped).** LangGraph agent state within a single task execution: tool call results, intermediate reasoning, draft outputs. This layer does not persist beyond task completion. Anything an agent produces that needs to outlive the task is written to the repo, PostgreSQL, or Qdrant before the task exits.

**What each agent reads at task start.** Each agent's spec declares its context loading list. The pattern is: always read `current-sprint.md`, `decision-queue.md`, own agent spec, and own department's recent Redis events. Read additional docs — architecture, regime profiles, ticker profiles, other agent specs — only when the task requires them. Selective loading keeps token consumption predictable and avoids agents accumulating stale context from documents they don't need.

**Living status files.** Four files in the repo capture rapidly-changing operational state, updated by the State Manager on the Operations Department's behalf:
- `current-sprint.md` — active sprint goal, open blockers, near-term priorities
- `decision-queue.md` — items awaiting Mike's decision, oldest first
- `known-issues.md` — active bugs and degraded conditions
- `recent-changes.md` — last 10 deployments, schema migrations, and agent spec updates

These files are written by agents and read by agents and by Mike. They are not append-only — they reflect current state, not history. History lives in git.

**Append-only records.** ADRs, daily briefings, weekly reviews, trade logs, audit records, and strategy lifecycle decisions are append-only. Nothing in these files is edited or deleted; superseding entries reference and replace prior ones. Strategy lifecycle decisions in particular — every promotion, demotion, retirement, and parameter change — carry the full reasoning at the time of decision, so that retrospectives can answer "why did we activate this strategy then" rather than "what state was it in then."

**Qdrant and Mem0.** Qdrant indexes documents for semantic retrieval (described in section 7). Mem0 is self-hosted and stores agent-side accumulated context that is not document-shaped: learned calibration on Mike's communication patterns, recurring preferences, and agent-specific lessons that are too fine-grained for a repo document. Mem0 is a secondary memory layer — it augments but never replaces the repo. If a Mem0 record conflicts with a repo document, the repo wins.

---

## 11. Security and Access

**Tailscale trust model.**
The three machines communicate exclusively over Tailscale. Each machine is an authenticated node in a private tailnet; no service port is exposed to the public internet. The Dell is the only machine that runs production containers, and it does not accept inbound connections from the Ryzen or MacBook except for explicitly allowed service ports within the tailnet (PostgreSQL, Redis, Qdrant, Langfuse, Ollama). The Ryzen accepts task events from the Dell's Redis instance and returns results — it does not expose its own services to the MacBook. The MacBook has read-only access to services on the Dell for monitoring and log review.

**Credential management.**
All API credentials — Alpaca, IBKR, Anthropic, EDGAR, any news or social APIs — are stored as environment variables in a `.env` file on the Dell. This file is excluded from the repo via `.gitignore` and is never committed. Containers receive credentials via Docker Compose's `env_file` directive. No credential is hardcoded in any source file; the Implementation Agent's OpenHands sandbox excludes all credential-matching paths. The Ryzen receives task-scoped credentials (e.g., an Ollama call does not need broker credentials) only when a specific task requires them, passed as environment variables in the task payload, not stored on the Ryzen at rest.

**Agent permission scoping.**
Agents interact with production systems only through their declared interfaces. No agent has direct shell access to the Dell outside its container. No agent has write access to another department's database tables — schema design will enforce this via PostgreSQL roles once the schema is built. The Implementation Agent's writable path list (section 8) is the enforcement boundary for the outer loop; it is not a convention but a hard sandbox constraint enforced by OpenHands. Any PR that modifies `trading/`, `risk/`, `execution/`, or `compliance/` paths requires Mike's explicit approval; this is enforced by a branch protection rule, not by agent policy.

**Broker credential isolation.**
Alpaca and IBKR credentials are held only by NautilusTrader's container. No other container has access to broker credentials. Broker API calls are proxied through NautilusTrader — other departments receive fill events and position data via Redis Streams, not through direct broker API access. This isolation means a compromised Intelligence or Research container cannot submit orders.

**Secret rotation and compromise response.**
API credentials are rotated on a schedule defined per provider — typically quarterly, sooner if a provider supports automated rotation. When a credential is rotated, the new value is written to the `.env` file on the Dell and the affected containers are restarted; Docker Compose reloads the environment automatically. If a credential is suspected to be compromised, the immediate response is to revoke at the provider, write a new value, and restart the trading containers; the audit trail in Redis Streams identifies any actions taken with the compromised credential during the exposure window. This response procedure is documented in `docs/infrastructure/credential-incident-response.md` (to be written).

---

## 12. Observability

**LLM tracing (Langfuse).**
Every LLM call — cloud or local — is traced to Langfuse. Traces include model, token counts, latency, and a tag set identifying the department, agent, and task type. Langfuse is the primary instrument for two operational needs: the Platform Department's Cost Monitor (cloud spend tracking by department and agent) and the LLM Migration Evaluator (cloud-vs-local quality comparison). Traces are retained for the duration of the sprint and used in retrospectives. Langfuse is self-hosted on the Dell; traces do not leave the local network.

**System monitoring (Open Question 1).**
Container health, PostgreSQL performance, Redis throughput, Qdrant index health, and Tailscale connectivity are not covered by Langfuse. The Operations Department's Health Monitor requires a substrate to query. Prometheus + Grafana is the most likely answer; the decision is deferred until the Operations Department spec is written. Until that question is resolved, container health is monitored via Docker health checks and heartbeat events on Redis, which are sufficient for early sprint operation but not adequate for production.

**Audit trail.**
The audit trail is the firm's answer to "why did the system do that." It has three layers that together make every decision traceable: the Redis Streams event log (what events were published and when), the PostgreSQL audit table (structured record of every agent decision and its inputs), and the append-only records in the repo (ADRs, strategy lifecycle decisions, daily reports). Any decision — a trade, a strategy promotion, a risk veto, a deployment — can be traced from the event that triggered it through the agent that processed it to the inputs that informed it. This is not aspirational; agents are required to write audit records as part of their task completion, not as an afterthought.

**Alerting to Mike.**
Urgent alerts — risk breach, system down, reconciliation discrepancy — route through the Alert Agent to whatever channel is settled (Open Question 2). Until that is resolved, alerts are logged to PostgreSQL and surfaced in the daily briefing. Routine operational state reaches Mike through the daily briefing and weekly review produced by the Reporting Department. Mike is not expected to monitor dashboards; the system surfaces what requires his attention, and the Reporting Department is responsible for calibrating the signal-to-noise ratio of that surface.

---

## 13. Open Questions — Status Update

Of the four open questions tracked across this document and the ADR set, three are now resolved and one remains.

**Resolved: System-level monitoring stack (ADR-0004).** Prometheus + Grafana on the Dell, single-host, with exporters for node, cAdvisor, Redis, PostgreSQL, and Qdrant. The Operations Department's Health Monitor queries Prometheus via PromQL and publishes `operations.health-anomaly` events.

**Resolved: Alerting channel to Mike (ADR-0005).** Two channels classified by urgency: Discord webhook for routine content (daily briefings, weekly reviews, strategy lifecycle), self-hosted ntfy.sh for urgent alerts (risk breach, system down, reconciliation discrepancy), with Pushover documented as a fallback for the urgent path.

**Resolved: Redis Streams event envelope (ADR-0006).** Stream naming `<department>.<event-type>`, required envelope fields (event_id ULID, schema_version, produced_at, produced_by, correlation_id, payload), payload-by-reference rule for anything over 16 KB or document-shaped, semantic versioning of schemas in `schemas/events/`.

**Still open: ADR-0003 — NautilusTrader-to-Redis event bridge coverage.** The broker credential isolation property described in section 11 depends on NautilusTrader's bridge being comprehensive enough that no department needs direct broker API access. This will be resolved during the Trading Floor agent specification, when the actual NautilusTrader event surface is enumerated against consumer needs.
