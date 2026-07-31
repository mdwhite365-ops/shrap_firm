# Agent Specifications

This directory contains per-agent specifications for Shrap's nine departments. Each spec
describes one agent's purpose, trigger, inputs, outputs, state, and failure behavior in
enough detail to implement it.

Specs are written before implementation. The spec is the contract between Mike (as
architect) and the Development Department (as implementer). If implementation diverges
from the spec, update the spec — do not let the spec become stale.

## Reading order

If you are new to Shrap, read in this order:

1. `docs/00-vision.md` — what Shrap is and why
2. `docs/02-architecture.md` — the system design
3. `docs/agents/README.md` — this file, the agent catalog
4. Individual agent specs in the relevant department subdirectory, as needed

If you are an agent picking up a task, read only the specs relevant to your current task
plus the architecture sections they reference. Do not preemptively load all specs.

## Directory structure

Specs are organized by department. The subdirectory mapping is fixed:

| Department (full name) | Subdirectory |
|---|---|
| Development Department | `development/` |
| Research Department | `research/` |
| Trading Floor | `trading-floor/` |
| Intelligence Department | `intelligence/` |
| Structural Analysis Department | `structural-analysis/` |
| Risk and Compliance Department | `risk-compliance/` |
| Operations Department | `operations/` |
| Reporting Department | `reporting/` |
| Platform Department | `platform/` |

Within each subdirectory, spec files are named in kebab-case matching the agent name:
`<agent-name>.md`. Example: `research/regime-classifier.md`.

```
docs/agents/
  README.md                 this file
  _template.md              copy this to create a new spec
  development/
  intelligence/
  operations/
  platform/
  reporting/
  research/
  risk-compliance/
  structural-analysis/
  trading-floor/
```

## Roster

**Status column corrected 2026-07-27.** It previously read "Planned" for all 35
agents and had never been maintained. Statuses below are verified against
`src/shrap/agents/`, `src/shrap/`, and `infra/docker-compose.yml`.

Legend: **Deployed** = running on the Dell · **On-demand** = built, runs only
via CLI (no trigger) · **Spec only** = spec written, no implementation ·
**Not built** = neither.

| Agent | Department | Sprint month | Status |
|---|---|---|---|
| Spec Writer | Development | Month 1 | **Descoped** — ADR-0014; Claude Code fills this |
| Implementation Agent | Development | Month 1 | **Descoped** — ADR-0014; spec retained as historical |
| Code Reviewer | Development | Month 1 | **Descoped** — ADR-0014; Claude Code fills this |
| Deployment Agent | ~~Development~~ Operations | post-loop-closure | Not built — retained per ADR-0014 §2 (`no-llm`) |
| Regime Classifier | Research | Month 2 | **Deployed** (lives under Intelligence in code) |
| Regime Researcher | Research | Month 3 | Not built |
| Hypothesis Generator | Research | Month 2 | **Deployed** — spec corrected (#151), agent built (#156), autonomous trigger running (#163). The agent is `--profile tools`; `hypothesis-generator-trigger` is the always-on service |
| Strategy Evaluator | Research | Month 2 | **Deployed** — on-demand CLI plus an interval-sweep trigger (ADR-0013 item 2); three verdicts produced |
| Bayesian Updater | Research | Month 3 | Not built |
| Strategy Librarian | Research | Month 2 | **Deployed** — converges on verdicts the Evaluator already applied; INFO path not yet exercised live |
| Decision Maker | Trading Floor | Month 2 | **Deployed** |
| Regime Router | Trading Floor | Month 2 | Not built — ADR-0010 §4 unimplemented |
| Execution Agent | Trading Floor | Month 2 | **Deployed** |
| Sweep Detector | Trading Floor | Month 1 | Not built — first Framework #3 instance per ADR-0013 §4 |
| News Analyzer | Intelligence | Month 2 | **Deployed** — publishes to a stream with no consumer |
| Filing Processor | Intelligence | Month 2 | **Deployed** — same |
| Sentiment Monitor | Intelligence | Month 3 | Not built |
| Market Structure Reader | Intelligence | Month 3 | Not built |
| Filing Deep Reader | Structural Analysis | Month 3 | Not built — no dept directory |
| Debt and Credit Monitor | Structural Analysis | Month 4 | Not built |
| Insider Behavior Tracker | Structural Analysis | Month 3 | Not built |
| Watch List Curator | Structural Analysis | Month 3 | Not built |
| Risk Officer | Risk and Compliance | Month 2 | **Built (#146), enforcing, essentially unexercised** — a library at `src/shrap/risk_compliance/risk_officer/` called inside the Pre-Trade Checker, **not a compose service**. One decision recorded ever; see KI-022 |
| Pre-Trade Checker | Risk and Compliance | Month 1 | **Deployed** |
| Compliance Monitor | Risk and Compliance | Month 1 | Not built |
| Health Monitor | Operations | Month 1 | **Deployed** |
| Reconciliation Agent | Operations | Month 2 | **Deployed** |
| Audit Logger | Operations | Month 1 | **Deployed** |
| State Manager | Operations | Month 2 | Not built |
| Daily Briefing Agent | Reporting | Month 2 | Not built |
| Weekly Review Agent | Reporting | Month 3 | Not built |
| Alert Agent | Reporting | Month 1 | Not built |
| Cost Monitor | Platform | Month 1 | Not built |
| LLM Migration Evaluator | Platform | Month 4 | Not built |
| Infrastructure Planner | Platform | ongoing | Not built |

### Built but not on the original roster

These shipped during Phase 1 and were never added above.

**Verified against `docker compose ps` on 2026-07-31**, because the two rows
corrected above had both read "Spec only" while the code was deployed. A roster
entry is a claim about the running system and should be checked against it, not
against memory. Four services are `--profile tools` — `market-data`,
`strategy-evaluator`, `hypothesis-generator`, `infra-mapper` — so their absence
from `docker compose ps` is by design, not a fault; the always-on triggers
(`strategy-evaluator-trigger`, `hypothesis-generator-trigger`) invoke them.

| Agent | Department | Status |
|---|---|---|
| Tech Watcher | Research | **Deployed** — Framework #1 ingest + filter |
| Universe Curator | Research | **Deployed** — launch list never loaded |
| Infrastructure Mapper | Research | **On-demand** |
| Strategy Runner | Research | **Deployed** — gains `intelligence.signal` routing per ADR-0013 §3 |
| Strategy Fixture | Research | **Deployed**, disarmed |
| Paper Order Store | Trading Floor | **Deployed** |
| Market Phase Scheduler | Operations | **Deployed** |
| Market Data backfill | Shared infra | **On-demand** |

**Departments at zero agents:** Structural Analysis (4, Month 3),
Reporting (3, Alert Agent was Month 1), Platform (3, Cost Monitor was Month 1).
Development is no longer counted — ADR-0014 descoped three of its four agents
(Claude Code fills the role) and moved the fourth to Operations.

**The three-tier compute boundary (ADR-0014 §3).** Every always-on agent must
name which tier serves it. Tier 1 is local Ollama on the Dell (free, agent-
initiated); Tier 2 is cloud inference by API from the Dell (metered, agent-
initiated); Tier 3 is Claude Code on Mike's MacBook (human-initiated, **not
addressable by any agent**). "It will use Claude" is not an available answer
for a deployed agent — no autonomous capability may depend on Tier 3.

## Spec status

A spec moves through four states:

- **Planned** — agent identified in the architecture; no spec written yet
- **Draft** — spec written; not yet reviewed by Mike
- **Approved** — Mike has reviewed; ready for implementation
- **Implemented** — agent is built and running; spec updated to reflect any divergence

## Creating a new spec

1. Choose the agent name in kebab-case matching the name used in the architecture doc.
2. Copy the template: `cp docs/agents/_template.md docs/agents/<department>/<agent-name>.md`
3. Fill in every section. Remove sections that genuinely don't apply; do not leave
   placeholder text in the file.
4. Submit as a PR for Mike's review. Implementation does not begin until the spec is
   approved.
