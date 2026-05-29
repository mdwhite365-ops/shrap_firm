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

| Agent | Department | Sprint month | Status |
|---|---|---|---|
| Spec Writer | Development | Month 1 | Planned |
| Implementation Agent | Development | Month 1 | Planned |
| Code Reviewer | Development | Month 1 | Planned |
| Deployment Agent | Development | Month 2 | Planned |
| Regime Classifier | Research | Month 2 | Planned |
| Regime Researcher | Research | Month 3 | Planned |
| Hypothesis Generator | Research | Month 2 | Planned |
| Strategy Evaluator | Research | Month 2 | Planned |
| Bayesian Updater | Research | Month 3 | Planned |
| Strategy Librarian | Research | Month 2 | Planned |
| Decision Maker | Trading Floor | Month 2 | Planned |
| Regime Router | Trading Floor | Month 2 | Planned |
| Execution Agent | Trading Floor | Month 2 | Planned |
| Sweep Detector | Trading Floor | Month 1 | Planned |
| News Analyzer | Intelligence | Month 2 | Planned |
| Filing Processor | Intelligence | Month 2 | Planned |
| Sentiment Monitor | Intelligence | Month 3 | Planned |
| Market Structure Reader | Intelligence | Month 3 | Planned |
| Filing Deep Reader | Structural Analysis | Month 3 | Planned |
| Debt and Credit Monitor | Structural Analysis | Month 4 | Planned |
| Insider Behavior Tracker | Structural Analysis | Month 3 | Planned |
| Watch List Curator | Structural Analysis | Month 3 | Planned |
| Risk Officer | Risk and Compliance | Month 2 | Planned |
| Pre-Trade Checker | Risk and Compliance | Month 1 | Planned |
| Compliance Monitor | Risk and Compliance | Month 1 | Planned |
| Health Monitor | Operations | Month 1 | Planned |
| Reconciliation Agent | Operations | Month 2 | Planned |
| Audit Logger | Operations | Month 1 | Planned |
| State Manager | Operations | Month 2 | Planned |
| Daily Briefing Agent | Reporting | Month 2 | Planned |
| Weekly Review Agent | Reporting | Month 3 | Planned |
| Alert Agent | Reporting | Month 1 | Planned |
| Cost Monitor | Platform | Month 1 | Planned |
| LLM Migration Evaluator | Platform | Month 4 | Planned |
| Infrastructure Planner | Platform | ongoing | Planned |

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
