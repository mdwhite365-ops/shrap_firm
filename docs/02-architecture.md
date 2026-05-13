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

**1. System-level monitoring stack.**
Langfuse handles LLM traces. It does not cover Redis, PostgreSQL, Qdrant, Docker containers, or Tailscale connectivity. The Operations Department's Health Monitor needs a substrate to query. Prometheus + Grafana is the standard answer but has not been committed. Decision needed before: Operations Department spec.

**2. Alerting channel to Mike.**
The Reporting Department produces daily briefings, weekly reviews, and urgent alerts. The mechanism for reaching Mike is not decided. Candidates include a Slack bot (low friction, third-party dependency), email (reliable, lower urgency), or a self-hosted web dashboard (sovereign, requires build). Urgent alerts — risk breach, system down — need a path that works when Mike is away from his desk. Decision needed before: Reporting Department spec.

**3. Redis Streams event envelope schema.**
The choice of Redis Streams as the message bus is committed (ADR-0001). The topic namespace and message envelope format — stream names, field keys, schema versioning, required audit fields — are not. Every department that produces or consumes events needs this settled before it can be fully specced. Decision needed before: any department spec that publishes or subscribes to cross-department events.

---

## 3. The Three Loops

Shrap operates as three concurrent loops, each running at a different time scale. They are not sequential — all three run simultaneously. What changes between them is the clock they run on and the kind of work they do.

**Inner loop (seconds to minutes): the trading floor.**
The inner loop is what most trading systems implement and stop at. It reads market data, evaluates active regime-conditional strategies, generates signals, sizes positions, and executes orders through NautilusTrader. It runs continuously during market hours and maintains a reduced watch during extended hours for overnight risk events. The inner loop does not generate strategies or modify itself — it only executes what the middle loop has validated and the regime router has activated.

**Middle loop (hours to days): research and strategy lifecycle.**
The middle loop is where strategies are born, tested, promoted, and killed. The Hypothesis Generator proposes new strategies grounded in current regime and historical analogs. The Strategy Evaluator runs walk-forward backtests on VectorBT PRO, applies overfitting controls, and produces a promotion recommendation or rejection. Promoted strategies enter a staged pipeline: paper trading at full size → paper trading with performance tracking → eventually live capital. The Bayesian Updater continuously revises posterior estimates of each active strategy's edge as live results accumulate. The Regime Router reads current regime classification and activates or dormants strategies accordingly. The middle loop runs around the clock — backtests and hypothesis generation do not wait for market hours.

**Outer loop (days to weeks): self-development.**
The outer loop is what makes Shrap more than a trading bot. The Development Department reads the approved roadmap, drafts specifications for new agents and capabilities, implements code in OpenHands-sandboxed environments, opens PRs for Mike's review, deploys approved changes, and monitors for regressions. This loop is what allows Mike's 1-2 hours per day to compound — every approved PR expands the firm's capability without consuming Mike's time to implement it. The outer loop does not touch trading or risk policy directly; it builds the system that does.

**How the loops interact.**
The loops communicate through Redis Streams, not through direct calls. A strategy promoted by the middle loop publishes a `strategy.promoted` event; the Trading Floor consumes it and activates the strategy at the next regime-appropriate window. A regime change detected by Research publishes a `regime.updated` event; the Regime Router consumes it and adjusts which strategies are active. The Development Department publishes a `deployment.completed` event when a new agent is live; Operations consumes it and adds the new agent to its health-check roster. This event-driven boundary is what provides failure isolation — a crash in Research cannot deadlock the Trading Floor.

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

**Role.** Generates and validates trading strategies. Produces hypotheses grounded in current regime and universe, runs rigorous backtests, manages the strategy promotion pipeline, and maintains posterior estimates of each strategy's live edge. The kill rate is expected to exceed 90% of generated hypotheses — that is a feature, not a failure.

**Key agents.** Hypothesis Generator (proposes strategies given regime + universe context), Strategy Evaluator (runs walk-forward backtests on VectorBT PRO, applies overfitting controls), Bayesian Updater (maintains posterior edge estimates from live results), Strategy Librarian (maintains the strategy registry in PostgreSQL, tracks lifecycle state).

**Primary interfaces.** Reads: regime state from Redis Streams (`regime.updated`), universe profiles from `docs/universe/`, strategy registry from PostgreSQL. Writes: backtest results and strategy records to PostgreSQL. Publishes: `strategy.promoted`, `strategy.retired`, `strategy.hypothesis.generated`. Consumes: `regime.updated`, `intelligence.signal` (for hypothesis grounding).

**LLM tier.** Hypothesis Generator: cloud (Claude Sonnet 4.6) during sprint. Strategy Evaluator: no LLM — deterministic VectorBT PRO execution. Bayesian Updater: no LLM — statistical computation. Strategy Librarian: local (Qwen 9B) sufficient for registry maintenance tasks.

**Sprint scope.** Hypothesis Generator and Strategy Evaluator operational by end of month 2. Bayesian Updater and full promotion pipeline by end of month 3.

**Deferred.** Combinatorial purged cross-validation (computationally expensive; walk-forward validation ships first). Strategy-to-strategy correlation analysis for portfolio construction. Automated regime-specific hypothesis campaigns.

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

**Role.** Reads news, financial filings, social sentiment, and market structure data for the 50-name universe. Produces structured signals that feed the Hypothesis Generator and the Decision Maker's context. The Intelligence Department does not make trading decisions — it produces inputs that better-position other agents to make them.

**Key agents.** News Analyzer (reads and summarizes news relevant to universe names), Filing Processor (reads EDGAR 8-K filings for universe names, extracts material events), Sentiment Monitor (tracks social sentiment signals — Reddit, StockTwits — for high-retail-interest universe names), Market Structure Reader (tracks options flow, dark pool prints, and unusual volume patterns available from Level 1 data).

**Primary interfaces.** Reads: EDGAR API, news APIs, social APIs, Level 1 market data from NautilusTrader. Writes: structured signal records to PostgreSQL, full text to Qdrant. Publishes: `intelligence.signal` to Redis Streams. Consumes: universe profile updates from `docs/universe/`.

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
