# LLM Registry

**Version:** 0.1 draft
**Date:** 2026-05-30
**Owner:** Platform Department (delegated to Model Registry Maintainer)
**Status:** Living

## Purpose

Single source of truth for which actual model serves each tier alias.
Agent specs reference tier names (`cloud-judgment-heavy`, `cloud-default`,
`cloud-cheap`, `local-classification`, `local-heavy`, `no-llm`). This file
maps tier → model. Updated only via PR that passes shadow-eval per
ADR-0009.

The contract between agent specs and the model layer is the tier alias.
The contract between this file and the outside world is the model name.
Changing the latter without breaking the former is the whole point.

## Tier Table

| Tier alias | Current model (as of 2026-05-30) | Provider | Context window | Cost tier | Primary use cases | Consuming agents |
|---|---|---|---|---|---|---|
| `cloud-judgment-heavy` | Claude Opus 4.7 | Anthropic | ~200k tokens | High | Hardest judgment turns; uncertainty quantification; load-bearing code review | Hypothesis Generator (judgment turns); Decision Maker (uncertainty quantification); Implementation Agent on protected paths |
| `cloud-default` | Claude Sonnet 4.6 | Anthropic | ~200k tokens | Medium | General-purpose reasoning, drafting, code synthesis on non-load-bearing surfaces | Tech Watcher; Bottleneck Scout; Infrastructure Mapper; Implementation Agent on non-protected paths |
| `cloud-cheap` | Claude Haiku 4 | Anthropic | ~200k tokens | Low | Summarization, alert formatting, light transformation | Reporting Department (not yet specced — no assignment yet) |
| `local-classification` | `qwen2.5:9b-instruct-q4_K_M` (Ollama) | Local (Dell) | 32k tokens (approx; depends on Ollama config) | Marginal (electricity) | Statistical classification, sentiment, tagging | Regime Classifier statistical layer; news sentiment; ticker tagging |
| `local-heavy` | `mistral-small:24b-instruct-q4_K_M` (Ollama) | Local (Ryzen, via `ryzen.tasks` stream) | 32k tokens (approx; depends on Ollama config) | Marginal (electricity) | Heavier local inference offloaded to Ryzen substrate | Agents that publish to `ryzen.tasks` (consumer set TBD per agent spec) |
| `no-llm` | N/A | — | — | None | Deterministic logic only | Risk Officer; Strategy Evaluator core stats; Health Monitor |

Context-window numbers are approximate and will shift as providers update
their offerings or as Ollama configuration changes the effective window
for local models. Treat the numbers as planning guidance, not contracts.
The contract is the tier alias.

## Update Protocol

1. **Trigger.** Either (a) Tech Watcher emits a
   `research.tech-event-model-release` event for a model that plausibly
   fits an existing tier, OR (b) the Model Registry Maintainer's
   scheduled quarterly review surfaces a candidate.

2. **Shadow-eval plan.** The Maintainer drafts:
   - The representative agent prompt set to run on both current and
     candidate models for the affected tier.
   - The comparison metrics: output quality (scored by Mike, or by a
     higher-tier model if Mike-scoring is infeasible at the volume),
     latency, cost-per-call, refusal rate, format adherence.
   - The pass criterion (typically: candidate matches or beats incumbent
     on quality without a material regression on the other metrics).

3. **Eval run.** Scripted, reproducible. Results are logged to
   `docs/research/calibration.md` section **(e) Model Registry Eval
   Ledger** (this section is added by ADR-0009 and does not yet exist —
   it will be created on the first eval run).

4. **Promotion gate.** Maintainer opens a PR that updates the relevant
   row of the tier table AND appends a row to the history table below.
   Mike reviews the eval results and approves. Merge is the change —
   no agent specs need to be touched, because they reference the tier
   alias.

A failing shadow-eval is not a registry update; it is logged in the
calibration ledger so we have a record that the candidate was considered
and rejected, and why.

## History Table

Append-only. Newest at the bottom.

| Date | Tier | From model | To model | Shadow-eval verdict | Approving party | PR link |
|---|---|---|---|---|---|---|
| 2026-05-30 | `cloud-judgment-heavy` | N/A | Claude Opus 4.7 | initial seed | Mike White | this PR |
| 2026-05-30 | `cloud-default` | N/A | Claude Sonnet 4.6 | initial seed | Mike White | this PR |
| 2026-05-30 | `cloud-cheap` | N/A | Claude Haiku 4 | initial seed | Mike White | this PR |
| 2026-05-30 | `local-classification` | N/A | `qwen2.5:9b-instruct-q4_K_M` | initial seed | Mike White | this PR |
| 2026-05-30 | `local-heavy` | N/A | `mistral-small:24b-instruct-q4_K_M` | initial seed | Mike White | this PR |
| 2026-05-30 | `no-llm` | N/A | N/A | initial seed | Mike White | this PR |

## Hard Rules

1. **Tier alias is the contract.** Agent specs reference tier names only.
   No spec contains a literal model name. If a spec needs a capability
   that no existing tier provides, the answer is an ADR to add a tier,
   not a hardcoded model name.

2. **No tier swap without shadow-eval pass logged in the calibration
   ledger.** A registry PR with no linked eval-ledger entry is not
   mergeable.

3. **Tier vocabulary additions require an ADR.** The six tier aliases
   are a contract surface; adding to it is a decision worth recording.

4. **Tier deletions require an ADR plus a migration plan** for every
   agent currently consuming the tier. No tier is removed while it has
   consumers.

5. **Local models reference Ollama model names verbatim** (image tag
   pinned per `llm-routing.md` operational notes). A local model upgrade
   is also a Dell or Ryzen substrate update and must go through the
   Dell bootstrap runbook — pulling a new tag on a live host without
   the runbook is not a registry update, it is an incident waiting to
   happen.

## Open Questions

- **(a) Refusal rate measurement methodology.** Not standardized yet.
  Deferred to the first real eval run, which will force a concrete
  definition; the definition then comes back into this document.
- **(b) Cost-per-call accounting for local models.** Harder than cloud
  (electricity, capex amortization, opportunity cost of the substrate).
  Deferred. For now, local models are accounted at "marginal" without a
  dollar figure.
- **(c) Reporting Department eval cadence.** Does the `cloud-cheap` tier
  get its own eval cadence, or does it piggyback on `cloud-default`
  evals? Deferred until the Reporting Department spec exists and there
  is an actual consumer to evaluate against.
