# Shrap: Vision

**Document version:** 0.1 (draft)
**Last updated:** 2026-05-06
**Owner:** Mike White
**Status:** Living document — updated as the project evolves

---

## What Shrap is

Shrap is a self-developing, self-improving, self-trading firm that operates on Mike's hardware, manages Mike's capital, and is built and maintained primarily by AI agents under Mike's architectural direction.

The firm trades a deliberately small universe of stocks deeply rather than a broad universe shallowly. It adapts its strategy library to current market conditions using both statistical state classification and historical-analog reasoning. It generates, validates, promotes, and retires trading strategies autonomously through a research loop that runs while Mike sleeps. It reads news, financial filings, social sentiment, and structural macro data to find edges most retail traders cannot see. And it improves its own infrastructure over time, including drafting specifications and writing code under Mike's review.

Shrap is not a trading bot. It is a firm — with departments, agents in defined roles, governance, and continuous learning — built at a scale that one person can architect and oversee.

## Why Shrap exists

There are several motivations stacked together, and they reinforce each other:

**Time leverage through agents.** Mike has 1-2 hours per day to spend on this project. Building a system manually at that pace would take years and produce mediocre results. Building a system where agents do the bulk of the development, research, and operational work — with Mike directing strategy, approving decisions, and reading reports — turns a part-time hobby into a serious operation.

**A test of the agentic-firm thesis.** Mike believes AI agents have crossed the threshold from "interesting toys" to "genuinely capable collaborators" for well-bounded domains. Shrap is a real-world test of whether that thesis holds for a complex, adversarial, financially consequential domain. The system either works and validates the thesis, or it doesn't and teaches Mike where the thesis breaks.

**Structural edge for retail capital.** Retail traders face structural disadvantages: slower information, worse fills, payment-for-order-flow spreads, manufactured volatility designed to trigger their stops, and competition from institutions with vastly more resources. Shrap is built to invert specific parts of this asymmetry — by trading alongside smart-money positioning rather than against it, by detecting the setups that institutional players exploit, by reading primary sources retail doesn't read, and by enforcing the discipline retail traders typically lack.

**Sovereignty over tools.** The system is designed to run primarily on hardware Mike owns, with cloud LLMs as scaffolding that gets retired as local capability matures. Mike's edge — if any — should not be hostage to a vendor's pricing decisions, a service's terms-of-service changes, or a SaaS shutdown.

**Skills compound regardless of trading outcome.** Building Shrap develops deep expertise in multi-agent systems, autonomous coding agents, financial system architecture, distributed computing, and AI infrastructure. These skills are valuable for the next decade of work regardless of whether the trading edge materializes.

**Optionality on a long shot.** Most quant systems built by individuals fail to consistently beat the market. Mike has read the literature and acknowledges this. Building Shrap anyway is a deliberate bet on an asymmetric payoff: high probability of valuable infrastructure and skills, low-but-real probability of meaningful trading edge, with the cost being time Mike enjoys spending and money he can afford to lose.

## What success looks like

The 4-month sprint (May 2026 – August 2026) ends when classes start. The success criteria are tiered:

**Minimum success (must achieve):**
- The system runs autonomously on paper trading 24/7 by month 4
- Agents do the majority of code-writing under Mike's architectural direction
- Mike's daily time investment averages under 2 hours
- The development department, research department, and trading floor are all functional
- The system has audit trails sufficient to analyze every decision it has made

**Target success (likely to achieve):**
- The 50-name universe is deliberately curated with per-ticker profiles
- The two-layer regime classifier is operating, with both statistical state and historical analog identification
- The Strategy Evaluator enforces rigorous overfitting controls (walk-forward, PBO, deflated Sharpe, purged cross-validation)
- The Structural Analysis Department produces a continuous watch list
- Multi-strategy regime adaptation produces measurably better risk-adjusted returns than any single static strategy on the same universe
- Mike has high confidence in the system's audit trail, risk controls, and self-honesty

**Stretch success (would be impressive):**
- The system demonstrates positive expectancy on paper over 200+ trades
- The system meaningfully outperforms SPY on a risk-adjusted basis
- The Hypothesis Generator produces at least one genuinely promoted strategy that Mike did not conceive of independently
- The Structural Analysis Department flags at least one setup that subsequently played out
- The system has migrated at least one agent type to fully local LLM operation

**What success is not:**
- Beating the market in absolute terms over 4 months (too short to be meaningful signal)
- Funding additional hardware purchases through trading profits (premature)
- Being ready to manage real money (real-money readiness comes after the sprint, not during)
- Passing an MFFU evaluation (deferred to post-launch when the system has earned the right to manage prop firm capital)

## The architecture, at a glance

The firm operates on three nested loops, each running at a different time scale:

**Inner loop (seconds to minutes):** Real-time trading. The trading floor reads market data, runs active regime-conditional strategies, makes decisions, and executes orders. This is the loop most "trading bots" implement.

**Middle loop (hours to days):** Strategy research and lifecycle management. The research department generates hypotheses, runs walk-forward backtests, evaluates results against rigorous statistical criteria, promotes strategies through stages (paper → small-size paper → live paper → eventually real capital), and retires strategies that decay. This loop is where most retail systems either don't exist or are done by humans on weekends.

**Outer loop (days to weeks):** The system improving itself. The development department reads the approved roadmap, drafts specifications for new agents and capabilities, implements code under code review, deploys changes, monitors for regressions, and proposes new directions for Mike's approval. This loop is what allows Mike's 1-2 hours per day to compound into a serious operation.

The firm is organized into departments, each with defined agents in defined roles:

- **Development Department** — builds and maintains the firm itself
- **Research Department** — generates and validates strategies
- **Trading Floor** — executes validated strategies on live market data
- **Intelligence Department** — reads news, filings, social, and market structure
- **Structural Analysis Department** — reads primary sources for fault lines and opportunities most don't see
- **Risk and Compliance Department** — enforces guardrails with veto power over all other departments
- **Operations Department** — keeps the lights on (state, reconciliation, health, audit)
- **Reporting Department** — keeps Mike informed without overwhelming him
- **Infrastructure and Growth Department** — manages self-expansion as the firm matures

Detailed architecture is documented in `02-architecture.md`. Per-agent specifications live in `agents/`.

## The trading thesis

Shrap's edge, if any, comes from a specific combination of choices:

**1. Universe focus.** Shrap trades 50 deliberately-chosen stocks. The selection includes liquid mid-caps, defense contractors (to leverage government-contract intelligence), high-retail-interest names (where trap setups occur), liquid ETFs (for regime expression and hedging), and a small crypto allocation. Each name has a maintained per-ticker profile capturing its behavioral characteristics, news sensitivity, and historical patterns. The Universe Curator Agent maintains the list, removing stocks that age out and proposing replacements for Mike's approval.

**2. Two-layer regime awareness.** The Regime Classifier operates at two layers. The statistical layer tracks volatility, trend, breadth, dispersion, and term structure to label current market state. The historical-analog layer uses LLM reasoning to identify which past regimes the current period most resembles — wartime, golden eras, stagflation, crisis recoveries, late-cycle melt-ups, and others — and uses those analogs to inform what kinds of strategies are likely to work. Strategies are tagged with `regime_fit` and `regime_kill` metadata. The Regime Router activates strategies appropriate to current regime and dormant ones that don't fit.

**3. Regime-conditional strategy generation.** The Hypothesis Generator does not propose strategies in isolation. It proposes strategies grounded in current regime, specific universe members, and historical analogs. The output is testable, contextualized, and includes the conditions under which the strategy should be retired (regime change). This grounding dramatically reduces the LLM hallucination risk that plagues naive "ask an LLM for a trading strategy" approaches.

**4. Rigorous overfitting controls.** The Strategy Evaluator is built to be ruthless. Walk-forward validation only. Probability of Backtest Overfitting (PBO) testing. Deflated Sharpe Ratio. Combinatorial purged cross-validation. Minimum 150-200 trade counts before promotion is allowed. Realistic transaction costs modeled. Out-of-distribution testing across regimes the strategy was not fit on. The kill rate is expected to be 90%+ of generated hypotheses. Promoting noise costs real money; killing real edge costs only the time to find it again.

**5. Trap detection.** Mike's existing liquidation sweep detector catches the *execution* of institutional fade-the-retail patterns. The expanded Trap Detection subsystem catches the *setup* — identifying which tickers in the high-retail-interest subset of the universe are primed for a trap before it fires. Combined with the existing sweep detector, this creates high-confidence confluence on Shrap's signature trade type.

**6. Structural Analysis as patient counterweight.** Most of Shrap's strategies trade on technical and short-term-catalyst signals — fast loops, many trades, modest per-trade edge. The Structural Analysis Department operates on a much slower clock, reading 10-Ks, 10-Qs, 8-Ks, debt maturity calendars, supply chain disclosures, insider behavior, credit markets, and litigation activity for the universe. It produces a continuous watch list of structural concerns and opportunities. Outputs feed the Decision Maker as biases and sizing modifiers — not entry triggers — making the system willing to fade a structurally-stressed name and willing to size up on a structurally-strong one. This is the analytical lens Burry, Eisman, and others used before 2008. The base rate for actionable findings is low, but the asymmetric payoff per finding is high.

**7. Honest position sizing.** The Bayesian Updater Agent maintains posterior probabilities for each strategy's edge based on accumulated evidence. The Risk Officer applies Kelly-fractional sizing (typically 25-50% of full Kelly) tuned by recent performance and current regime fit. Concurrent positions are sized accounting for correlation, not independently.

## What Shrap is not

To prevent scope creep that would kill the 4-month sprint, the following are explicitly out of scope for the initial build:

- A high-frequency trading firm (Shrap operates on seconds-to-days, not microseconds)
- An academic quant fund (no factor modeling, no optimal control theory)
- A market maker (Shrap is directional, not a liquidity provider)
- A real-money trading system (paper only during the sprint)
- An MFFU evaluation candidate (deferred until the system has earned the right)
- A multi-asset universal trader (focused universe is the point)
- A "predict the price" system (LLMs are used for context and pattern matching, never numerical prediction)

Ideas that fit the long-term vision but are deferred to post-sprint backlog include: full pipeline intelligence (job postings, lobbying, patents, conferences), advanced dealer gamma positioning, options strategies, additional crypto pairs, fine-tuned local models for trading-specific tasks, and full cloud-LLM retirement. These are tracked in `post-launch.md`.

## The role of AI agents

Shrap is built primarily by AI agents, with Mike as architect and reviewer. The division of labor is intentional and evolves over the project:

**Mike's role:**
- Strategic direction and roadmap priorities
- Approval of agent specifications before implementation
- Approval of pull requests and material changes
- Approval of strategy promotions and risk policy changes
- Interpretation of structural analysis findings
- Pattern recognition and intuition-based course corrections
- Reading daily briefings, weekly reviews, monthly trajectory analyses
- Final authority on what the system trades and at what size

**The agents' roles:**
- Drafting specifications from Mike's high-level direction
- Implementing code per approved specifications
- Reviewing and testing each other's code
- Running backtests, analyzing results, recommending promotions
- Reading filings, news, social sentiment, market structure data
- Generating hypotheses, validating them, retiring failed ones
- Maintaining state, audit logs, health monitoring
- Writing reports, summaries, alerts
- Maintaining and updating documentation

**The trust progression:**
- Month 1: Agents propose, Mike approves every change
- Month 2: Agents auto-execute well-defined tasks per templates; Mike approves novel work
- Month 4: Agents auto-execute most routine work including spec updates; Mike approves strategic redirects and any change touching trading or risk policy
- Always: Trading orders, risk policy changes, and any change to the kill criteria require Mike's explicit approval

## Memory and context

The system handles long-running context through layered design:

**The repository is the primary memory.** All durable knowledge — vision, architecture, agent specs, ADRs, schemas, regime profiles, ticker profiles, status files — lives as version-controlled documents. Agents read fresh from the repo at the start of each task. Nothing lives only in any agent's "memory."

**Selective context loading.** Each agent reads only the documents relevant to its current task. The Implementation Agent working on the Risk Officer reads vision, architecture, the Risk Officer spec, dependency specs, and relevant code — typically 25-30K tokens, comfortably within context limits.

**Living status files.** A small set of files capture the firm's current state: `current-sprint.md`, `decision-queue.md`, `known-issues.md`, `recent-changes.md`. These update frequently and are read by every agent.

**Append-only history.** Architecture Decision Records (ADRs), daily reports, weekly reviews, and trade logs are append-only. Decisions are never rewritten; superseding decisions reference and replace prior ones.

**Vector search for semantic recall.** Self-hosted Qdrant indexes all docs, decisions, reports, and intelligence outputs. Agents query semantically when they need historical context that isn't in their immediate task scope.

**External memory layer.** Mem0 (self-hosted) stores agent-side learned context: recurring user preferences, accumulated calibration on Mike's communication patterns, agent-specific lessons learned.

This pattern means the system scales beyond any single context window. The repo holds everything; agents are temporary visitors.

## Hardware topology

Shrap operates on a three-machine cluster, each with a defined role:

**Dell Precision 5820 (TrueNAS)** — Production tier. Always-on. Runs the trading floor, intelligence agents, operations agents, message bus, databases, and observability stack. Currently equipped with GTX 1080 (will be upgraded to RTX 2070 Super in early build). Hosts Ollama for local LLM serving with Qwen 9B as default classification model.

**Ryzen 7 7800X with RTX 4070 Super 12GB** — Heavy inference and research tier. On-demand availability via Tailscale. Runs heavier local LLMs (Qwen 14B, Mistral Small 24B), backtest workloads, and any agent task requiring more horsepower than the Dell can deliver. Used for the development department's heavier code generation tasks. Eventually used for fine-tuning local models on accumulated trade data.

**MacBook Pro M4 24GB** — Development and mobile tier. Used for direct development with Claude Code, on-the-go research, and reviewing reports while traveling. Not part of the production trading path.

The three machines coordinate over Tailscale. The Dell is the canonical production environment. The Ryzen is a flex resource. The MacBook is for Mike's interactive use.

## The cloud-to-local migration arc

Shrap begins as a hybrid cloud/local system and migrates toward fully local operation over time. The principle is "local-first, cloud-as-scaffolding."

**Sprint period (months 1-4):** Cloud LLMs (Claude Sonnet 4.6, occasionally Opus 4.7) are used liberally for development department work, decision-making, and any reasoning task where quality matters. Local LLMs (Qwen 9B/14B on the Dell and Ryzen) handle classification, sentiment, and bulk intelligence work. Total cloud LLM cost during sprint: estimated $200-500/month, falling as more agents migrate to local.

**Post-sprint (months 5-12):** Shadow evaluation methodology proves out which agents can run on local models without quality regression. Migrations happen one agent at a time, with rigorous before/after comparison. Decision Maker stays on cloud longest because the cost of error is highest there.

**Long-term (12+ months):** Cloud LLM is optional, not required. The system runs primarily on Mike's hardware. Cloud calls happen only for the hardest edge cases, novel research questions, or as fallback when local infrastructure is unavailable.

This migration is documented in detail in `infrastructure/llm-routing.md` and individual agent specs (each agent declares its current model tier and target migration milestone).

## Honest probability assessment

Mike has asked for honest expectations. They are:

- **Probability the system runs and trades on paper by end of month 4:** 90%
- **Probability of positive expectancy on paper over 200+ trades:** 45-55%
- **Probability of meaningfully beating SPY on a risk-adjusted basis during the sprint:** 25-35%
- **Probability the adaptive multi-strategy system beats its own best static strategy on the same universe:** 50-60%
- **Probability of surviving transition to real money with edge intact:** 20-25%

These probabilities reflect:
- The structural difficulty of beating efficient markets (most quant funds fail to do so)
- Mike's existing trading data showing approximately 47% live win rate on validated strategies (a sober baseline)
- The genuine but bounded edge available from focused-universe + regime-conditional + structural-analysis approaches
- The amplifying effect of disciplined overfitting controls
- The fundamental uncertainty about whether traded patterns have edge until proven over time

The probabilities assume the system is built with discipline. They drop sharply if:
- The Strategy Evaluator is permissive
- The Hypothesis Generator runs unconstrained
- Backtests use survivor-only data
- Transaction costs are not modeled realistically
- Position sizing ignores correlation
- Mike overrides agent kills to deploy promising-but-unproven strategies

The probabilities are not the point. The system is worth building if the trading outcome is binary — and it isn't. Even in worlds where the trading edge is small or zero, Shrap delivers:

- Deep technical skills in multi-agent systems and AI infrastructure
- A working artifact demonstrating advanced agentic engineering
- Infrastructure that can be repurposed beyond trading
- The discipline of operating a system that holds itself accountable
- A platform for continued iteration past the sprint

These outcomes are roughly 90%+ probability. They justify the project on their own.

## Operating principles

A short list of principles that govern decisions across the project. These are not rules — they are the values the firm tries to embody.

1. **Honest accounting first, optimization later.** A system that knows its real performance, including the parts that don't work, is more valuable than one that looks good on paper.

2. **Kill more aggressively than you promote.** The cost of killing a real edge is small (you'll find it again). The cost of promoting noise is real money.

3. **Boring beats clever.** Simple regime classifiers, well-validated, beat sophisticated ones that overfit. Few rigorously-tested strategies beat many barely-tested ones.

4. **The repo is the truth.** If something matters, it's documented. Tribal knowledge held in any agent's "memory" is not real knowledge.

5. **Cloud is scaffolding.** Use it freely during the build. Plan to retire it. Never let the system depend on a service you don't control.

6. **Mike is the architect, not the implementer.** When Mike finds himself writing code or specs, that's a signal that the development department needs better tools, not that Mike should keep coding.

7. **Drift requires updating the spec, not the code.** When implementation reveals the spec was wrong, update the spec first, then bring the code in line. The spec is the durable artifact.

8. **Audit everything.** Every decision, signal, order, and fill traceable to its inputs. When (not if) something goes wrong, the system can answer "why did it do that."

9. **Optimize for compounding learning, not for short-term performance.** The 4-month sprint is the foundation, not the destination. Every choice should make the system better at learning, not just better at trading right now.

10. **Mike's time is the constraint.** Anything that costs Mike more than a few minutes of routine attention per day must justify itself. The system serves Mike, not the other way around.

## Living document

This vision will change. As the project unfolds, hypotheses will be tested, assumptions will prove wrong, and new opportunities will emerge. This document is updated to reflect what Shrap actually is, not just what it was originally imagined to be.

Material changes to this document require:
- An ADR documenting the change and the reasoning
- Mike's explicit approval
- Cross-references updated in dependent documents

The current version of this document is the firm's current understanding of itself. Earlier versions are preserved in git history.
