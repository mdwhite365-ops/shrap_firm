# LLM Routing

**Document version:** 0.1 (draft)
**Last updated:** 2026-05-29
**Owner:** Mike White
**Status:** Living document — updated as the project evolves

---

## Purpose

This document is the per-agent LLM routing matrix and the methodology for migrating agents between tiers. It exists because the cloud-to-local migration is a multi-month process that has to happen one agent at a time, with rigorous before/after evidence, and the routing decisions need a durable home outside individual agent specs.

Read `00-vision.md` for the cloud-as-scaffolding principle, `02-architecture.md` §9 for the runtime routing model, and `03-hardware.md` for the physical placement of Ollama instances.

---

## The three tiers

Every agent in the firm is assigned to one of three tiers. The tier is declared in the agent's spec and tracked here.

**Cloud-primary.** Agent calls the Anthropic API. Default model is Claude Sonnet 4.6; specific tasks escalate to Opus 4.7. Used for agents whose output quality has direct financial or architectural consequences and where the cost of a degraded answer exceeds the API bill.

**Local-primary.** Agent calls Ollama. Two physical homes:

- **Dell (Qwen 3.5 9B, Q4_K_M quant — 6.6 GB).** Default for latency-sensitive local work. The 8 GB VRAM on the RTX 2070 Super (swapped in for the GTX 1080, 2026-07-16 — same VRAM, faster Turing inference) is the constraint; this model fits, with thinner headroom than the original 2.5-era assumption — keep `num_ctx` modest, and `qwen3.5:4b-q4_K_M` (3.4 GB) is the documented fallback if concurrent load OOMs the card.
- **Ryzen (Qwen 2.5 14B-instruct, Mistral Small 24B-instruct, both Q4_K_M).** Heavier inference for work that does not need sub-second latency. Routed via `ryzen.tasks` Redis Stream per the hardware doc.

Local-primary agents fall back to cloud when the local instance is unavailable, with the fallback recorded as a degraded operation in the daily briefing.

**No-LLM.** Agent is deterministic Python. Rule enforcement, statistical computation, structured data extraction. Faster, cheaper, more predictable than an LLM-based equivalent.

---

## The `ryzen.tasks` pattern

Heavy local inference goes through a Redis Streams task pattern documented in `03-hardware.md` §1 and §4:

1. Producer (an agent on the Dell) publishes to `ryzen.tasks` with the work payload (model name, prompt, parameters) and a `correlation_id`
2. Ryzen worker process consumes from `ryzen.tasks` via consumer group, executes against the local Ollama instance on the Ryzen
3. Ryzen worker publishes the result to `ryzen.results` with the same `correlation_id`
4. Producer consumes its own `ryzen.results` message by `correlation_id` match

The Ryzen is not always available (per hardware doc: it is Mike's daily-use Windows machine, WSL2 Ubuntu inside). The producer must tolerate hours-to-day latency on `ryzen.tasks`. Latency-sensitive work does not go to the Ryzen — it stays on the Dell or goes to cloud.

The Dell never blocks on a Ryzen result. Producers either:

- Submit-and-forget when the result is not on the critical path (e.g., a background research backtest)
- Submit-and-watch with a timeout, with a fallback to cloud or Dell-local if the timeout fires
- Submit only when the producer's own work is itself asynchronous and a long latency is acceptable

The exact submit pattern is part of each agent's spec.

---

## Routing matrix by agent

The matrix is the source of truth for current routing. When an agent migrates tiers, the matrix is updated in the same PR that migrates the agent. The agent's spec also declares its current tier; the matrix and the spec must agree.

### Development Department

| Agent | Current tier | Notes |
|---|---|---|
| Spec Writer | Cloud (Sonnet 4.6) | Migration candidate post-sprint: Mistral Small 24B on Ryzen for routine spec drafting |
| Implementation Agent | Cloud (Sonnet 4.6, Opus 4.7 for hard tasks) | Stays cloud longest. Code quality requirements high |
| Code Reviewer | Cloud (Sonnet 4.6) | Stays cloud during sprint |
| Deployment Agent | No-LLM | Webhook + docker compose. No model needed |

### Research Department

| Agent | Current tier | Notes |
|---|---|---|
| Regime Classifier | No-LLM | Statistical computation only |
| Regime Researcher | Cloud (Sonnet 4.6) | Historical analog synthesis requires judgment |
| Hypothesis Generator | Cloud (Sonnet 4.6) | Cost of poor hypotheses propagates downstream |
| Strategy Evaluator | No-LLM | VectorBT PRO deterministic execution |
| Bayesian Updater | No-LLM | Statistical posterior updates |
| Strategy Librarian | Local (Qwen 9B, Dell) | Registry maintenance |

### Trading Floor

| Agent | Current tier | Notes |
|---|---|---|
| Decision Maker | Cloud (Sonnet 4.6) | Migrates last, if at all. Highest cost-of-error |
| Regime Router | Local (Qwen 9B, Dell) | Classification, not synthesis |
| Execution Agent | No-LLM | NautilusTrader-facing, deterministic |
| Sweep Detector | No-LLM | Existing deterministic logic |

### Intelligence Department

| Agent | Current tier | Notes |
|---|---|---|
| News Analyzer | Local (Qwen 9B, Dell), cloud escalation for material events | Bulk summarization is local; high-impact events escalate |
| Filing Processor | Local (Qwen 9B, Dell), cloud escalation | Same pattern as News Analyzer |
| Sentiment Monitor | No-LLM | Structured extraction from social APIs |
| Market Structure Reader | No-LLM | Pattern matching on options flow / volume |

### Structural Analysis Department

| Agent | Current tier | Notes |
|---|---|---|
| Filing Deep Reader | Cloud (Sonnet 4.6, Opus 4.7 for complex synthesis) | Cross-section synthesis of 10-K/10-Q content |
| Watch List Curator | Cloud (Sonnet 4.6) | Ranks and synthesizes findings |
| Debt and Credit Monitor | Local (Qwen 9B, Dell) | Structured data extraction from FRED + credit markets |
| Insider Behavior Tracker | Local (Qwen 9B, Dell) | Form 4 pattern matching with light synthesis |

### Risk and Compliance Department

| Agent | Current tier | Notes |
|---|---|---|
| Risk Officer | Cloud (Sonnet 4.6) | Strategy promotion reviews; cost of error high |
| Pre-Trade Checker | No-LLM | Order path latency requirement rules out LLM |
| Compliance Monitor | No-LLM | Deterministic rule enforcement |

### Operations Department

| Agent | Current tier | Notes |
|---|---|---|
| Health Monitor | No-LLM | Prometheus queries, threshold comparisons |
| Reconciliation Agent | No-LLM | Deterministic comparison |
| Audit Logger | No-LLM | Append-only logging |
| State Manager | Local (Qwen 9B, Dell) | Status file synthesis |

### Reporting Department

| Agent | Current tier | Notes |
|---|---|---|
| Daily Briefing Agent | Cloud (Sonnet 4.6) | Report synthesis quality matters |
| Weekly Review Agent | Cloud (Sonnet 4.6) | Deeper synthesis; cloud during sprint |
| Alert Agent | Local (Qwen 9B, Dell), cloud escalation | Routine classification local; novel anomalies escalate |

### Platform Department

| Agent | Current tier | Notes |
|---|---|---|
| LLM Migration Evaluator | Cloud (Sonnet 4.6) | Evaluation synthesis; cloud for now |
| Cost Monitor | Local (Qwen 9B, Dell) | Tracks Langfuse spend, simple thresholding |
| Infrastructure Planner | Local (Qwen 9B, Dell) | Drafts proposals; cloud escalation if needed |

---

## Shadow-evaluation methodology

The shadow-evaluation process is how an agent migrates from cloud-primary to local-primary. It is rigorous because the cost of being wrong is real: a Decision Maker that silently degrades costs money; a Risk Officer that silently degrades costs a lot more.

### Step 1 — Define the rubric

Before evaluation begins, the agent's spec declares the rubric — the specific dimensions on which output quality is measured. Examples:

- Hypothesis Generator: hypothesis is grounded in current regime (yes/no); hypothesis is testable (yes/no); hypothesis is novel relative to active strategies (1-5 score); hypothesis includes retirement conditions (yes/no)
- Daily Briefing Agent: factual accuracy against PostgreSQL state (count of errors); coverage of decision-queue items (count missed); calibrated signal-to-noise (Mike rates each briefing 1-5)
- Alert Agent: correct urgency classification (per ADR-0005 rules; confusion matrix); routing latency

Rubrics are explicit. "Better in general" is not a rubric.

### Step 2 — Capture a representative sample

Once the cloud-primary agent has accumulated at least 50 task instances of the relevant type (recorded in Langfuse with full input/output), the sample is the candidate evaluation set. Samples are sliced by task type and regime — a Hypothesis Generator that works for low-vol regimes may not work for high-vol regimes.

### Step 3 — Run the shadow

The LLM Migration Evaluator replays each sample input through the candidate local model and records the local output. Cloud output is the reference; local output is the candidate. Neither output is touched by hand.

### Step 4 — Score against the rubric

For each rubric dimension, the local output is scored against the reference output. Some scoring is mechanical (factual accuracy against PostgreSQL state can be computed); some is human-graded (Mike rates a sample for signal-to-noise). Mechanical scoring runs unattended; human-graded scoring batches up samples for Mike's weekly review.

### Step 5 — Decide

A migration proposal is generated when:

- Local matches cloud on every rubric dimension within a documented tolerance (per agent, declared in spec)
- The matched-or-exceeded condition holds across at least two distinct regimes (where regime is relevant to the agent's task)
- The sample is at least 50 tasks; 100+ is preferred for high-cost-of-error agents

Mike reviews the proposal. If approved, the agent's spec is updated in a PR, the routing matrix above is updated, and the agent migrates on the next deployment. The previous cloud configuration remains as the documented fallback for the agent's first 30 days post-migration.

### Step 6 — Watch for drift

After migration, the LLM Migration Evaluator continues running shadow evaluations on a sample basis (default: 1 in 20 tasks). If drift is detected — local quality falling below the migration tolerance — the agent is migrated back to cloud and a follow-up investigation is opened. Drift is usually a sign that the input distribution has changed; the rubric and sample may need to be updated.

---

## Cost budget envelope

The sprint envelope from `00-vision.md`: $200-500/month total cloud LLM spend, expected to fall as agents migrate.

Breakdown by department (estimate; reconciled against Langfuse traces monthly):

| Department | Sprint estimate | Notes |
|---|---|---|
| Development (Implementation Agent) | $80-200/mo | OpenHands sandbox code generation; highest-variance line item |
| Research (Hypothesis Gen, Regime Researcher) | $40-100/mo | Hypothesis generation cadence drives this |
| Trading Floor (Decision Maker) | $40-100/mo | Decision frequency depends on active strategies |
| Structural Analysis (Filing Deep Reader, Watch List Curator) | $20-50/mo | Slow clock; weekly cadence |
| Risk and Compliance (Risk Officer) | $10-30/mo | Strategy promotion reviews; infrequent |
| Reporting (Daily/Weekly) | $10-30/mo | Predictable cadence |
| Other (escalations from local-primary agents, Platform) | $10-30/mo | Variable |

Estimates are estimates. Actual numbers are tracked in the Cost Monitor's daily output and reviewed weekly. If month 1 spend trends materially above $500/month, response is: push migrations forward, not raise the envelope.

The envelope is for the sprint only. Post-sprint, the migration arc reduces cloud spend toward "edge cases only." The long-term target is cloud-as-fallback, not cloud-as-default.

---

## Cloud fallback for local-primary agents

When Ollama on the Dell is unreachable (container restart, model loading, GPU OOM), local-primary agents fall back to cloud. The fallback is logged as a degraded operation and surfaces in the daily briefing. This is the normal-case fallback.

When the Anthropic API is unreachable, cloud-primary agents fall back to the highest-quality local model the task can tolerate. For most cloud-primary agents this is Mistral Small 24B on the Ryzen, routed via `ryzen.tasks`. Per `02-architecture.md` §9:

- The fallback is logged as a degraded operation
- The Decision Maker reduces position sizing by 50% on any signal evaluated during the degradation window
- The Risk Officer's strategy promotion reviews are postponed rather than degraded

The Ryzen may not be available either. If both cloud and Ryzen are unavailable, cloud-primary agents either:

- Defer their work (Risk Officer, LLM Migration Evaluator)
- Operate at reduced confidence (Decision Maker — sizing further reduced or paused)
- Halt entirely (Hypothesis Generator — no degraded mode makes sense)

The fallback chain is declared per agent in the spec.

---

## Model selection notes

**Why Claude Sonnet 4.6 as the cloud default.** Cost-quality tradeoff at the working level the firm needs. Opus 4.7 is reserved for tasks where Sonnet has demonstrably underperformed during evaluation — primarily Implementation Agent's hard code-generation tasks and some Structural Analysis synthesis work.

**Why Qwen 3.5 9B as the Dell local default.** The originally planned "Qwen 2.5 9B" never existed (Qwen 2.5 shipped no 9B size — the invalid tag was discovered on the first real `ollama pull`, 2026-07-16). Corrected to `qwen3.5:9b-q4_K_M`: the largest current-generation Qwen that fits the Dell GPU's 8 GB VRAM (6.6 GB at Q4_K_M; GTX 1080 at the time, RTX 2070 Super since 2026-07-16), two model generations newer than the original plan, multimodal, 256K max context (VRAM-capped in practice). This resolved the "revisit when Qwen 3 lands" note from v0.1. Requires Ollama ≥ 0.31.x — the compose image pin moved 0.3.12 → 0.31.2 with the correction.

**Why Qwen 2.5 14B and Mistral Small 24B on Ryzen.** Both fit comfortably on the RTX 4070 Super's 12 GB VRAM at Q4_K_M. Qwen 14B for general-purpose heavy local work; Mistral Small 24B for tasks where instruction-following or reasoning quality matters more than throughput. The cloud-fallback path lands on Mistral Small 24B specifically because its quality has the best chance of matching Sonnet on the kinds of tasks that cloud-primary agents do.

**Why no smaller local models.** A 3B or 7B model would be faster and use less VRAM, but the firm's local-primary tasks (regime classification, registry maintenance, alert classification) are not so trivial that a smaller model would clearly suffice. The marginal Dell GPU cost of Qwen 9B over Qwen 7B is small; the marginal quality is meaningful.

**Why no proxy in front of Ollama or Anthropic.** Adding an LLM proxy (LiteLLM, OpenRouter) introduces an extra failure mode and an extra dependency for the benefit of unified API calls. Each agent has a model client wrapper that handles fallback; that is enough. Reconsider post-sprint if multi-provider routing becomes a real need.

---

## Migration milestones

Concrete migration targets for the sprint and immediately post-sprint:

| Agent | Current | Target | Earliest review |
|---|---|---|---|
| Spec Writer | Cloud (Sonnet) | Local (Mistral Small 24B, Ryzen) for routine specs | End month 4 |
| Alert Agent (routine path) | Local with cloud escalation | Local with reduced escalation rate | End month 3 |
| Strategy Librarian | Local | Local (validated) | End month 3 |
| State Manager | Local | Local (validated) | End month 2 |
| Filing Processor (bulk) | Local with cloud escalation | Local with reduced escalation rate | End month 3 |
| News Analyzer (bulk) | Local with cloud escalation | Local with reduced escalation rate | End month 3 |
| Decision Maker | Cloud (Sonnet) | Cloud through sprint | Post-sprint earliest |
| Risk Officer | Cloud (Sonnet) | Cloud through sprint | Post-sprint earliest |
| Hypothesis Generator | Cloud (Sonnet) | Cloud through sprint | Post-sprint earliest |
| Implementation Agent | Cloud (Sonnet/Opus) | Cloud through sprint | Post-sprint earliest |

"Earliest review" means the date by which sufficient shadow-eval data should exist to support a migration proposal. Actual migration happens only when the rubric criteria are met; missing the earliest-review date is not itself a slip.

---

## Open routing questions

These are routing-related questions that do not yet have answers.

1. **Per-call routing within a single agent.** Some agents could route easy calls to local and hard calls to cloud — e.g., the News Analyzer could decide call-by-call. The current design is per-agent tier with escalation rules; per-call tiering is a possible future evolution but not in scope for the sprint.

2. **Batch vs streaming inference.** Some local work (Filing Processor on 8-K backlog) is batch-shaped and would be more efficient processed in batches. Current design treats each invocation independently. Revisit when batch volume becomes large.

3. **Fine-tuning.** Post-sprint candidate. Fine-tuning Qwen 9B on accumulated trade and signal data could push more agents to local-only. The fine-tuning pipeline itself is a substantial project, tracked in `docs/post-launch.md`.

---

## What this document is not

This document is not a vendor evaluation. It is not a survey of every available LLM. It is the working routing matrix for Shrap. Other models exist; their inclusion in this document requires a documented reason to prefer them over what is here, and the inclusion goes through ADR review like any other architectural change.
