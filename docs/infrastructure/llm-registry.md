# LLM Registry

**Version:** 0.2
**Date:** 2026-07-16 (v0.1 seeded 2026-05-30)
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
| `local-classification` | `qwen3.5:9b-q4_K_M` (Ollama) | Local (Dell) | 256k model max; VRAM-capped on the Dell (keep `num_ctx` modest on 8 GB) | Marginal (electricity) | Statistical classification, sentiment, tagging | Regime Classifier statistical layer; news sentiment; ticker tagging; Tech Watcher (seed, all tiers routed here pending cloud billing) |
| `local-heavy` | `mistral-small:24b-instruct-q4_K_M` (Ollama) | Local (Ryzen, via `ryzen.tasks` stream) | 32k tokens (approx; depends on Ollama config) | Marginal (electricity) | Heavier local inference offloaded to Ryzen substrate | Agents that publish to `ryzen.tasks` (consumer set TBD per agent spec) |
| `no-llm` | N/A | — | — | None | Deterministic logic only | Risk Officer; Strategy Evaluator core stats; Health Monitor |

> **Deployment routing amendment (2026-07-27) — Tech Watcher only.** The
> local-only ruling of 2026-07-15 is superseded *for this agent*: its
> `local-classification` and `cloud-default` tiers now resolve to Ollama Cloud
> models, and that container alone points at `https://ollama.com` instead of
> the local daemon. The tier aliases remain the contract. The Intelligence
> agents (News Analyzer, Filing Processor) stay fully local; routing them is a
> separate cost decision.
>
> **Why direct rather than through the local daemon.** The daemon *can* serve
> cloud models, but it does not authenticate them with a bearer token — it
> signs every cloud request with the host's Ed25519 key at
> `~/.ollama/id_ed25519`, which `ollama signin` registers against an
> ollama.com account. On the Dell that key lives in the `ollama_models` volume,
> making cloud access **container state rather than configuration**: recreate
> the volume and cloud dies with "You need to be signed in to Ollama to run
> Cloud models," with nothing in the repo explaining why. ollama.com serves the
> same `/api/chat` contract as a remote Ollama host, so pointing at it costs
> one `Authorization: Bearer` header and makes auth declarative, restorable
> from `.env`, and reviewable. `SHRAP_TW_OLLAMA_URL=http://ollama:11434` sends
> the agent back through the daemon if that trade is ever worth reversing.
>
> The client now sends that header whenever a tier resolves to a **remote**
> Ollama host and a key is present; the local daemon never receives one, since
> it needs no auth and leaking a token to loopback would be gratuitous.
>
> Model choice is cost-shaped rather than capability-maximising. Ollama bills
> **GPU-time** under 5-hour session and weekly caps, not per token, and its
> library publishes a usage tier per model. The filter runs over ~1900 backlog
> items plus ~140/day and takes `gpt-oss:20b-cloud` (*Low Usage*); its 128K
> window is already ~90x the filter's ~1400-token prompt, so a 1M-context
> model would be paid for and unused. Synthesis runs a handful of times a day
> where judgment is load-bearing, and takes `kimi-k3:cloud` (*Extra High
> Usage*). Putting a flagship reasoning model on the bulk filter would be
> paying top rates for a call that deliberately runs `think=False`.
> `SHRAP_FILTER_MODEL` / `SHRAP_SYNTHESIS_MODEL` override both from `.env`, so
> laddering up (e.g. to `gpt-oss:120b-cloud`, *Medium Usage*) or falling back
> to a local tag is an env edit and a restart.

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
| 2026-07-16 | `local-classification` | `qwen2.5:9b-instruct-q4_K_M` | `qwen3.5:9b-q4_K_M` | N/A — seed correction, not a swap: the v0.1 tag never existed (Qwen 2.5 has no 9B; discovered on first `ollama pull`). No incumbent ever ran, so there is nothing to shadow-eval against. `qwen3.5:9b-q4_K_M` is 6.6 GB, fits the Dell's 8 GB GTX 1080; requires Ollama >= 0.31.x (compose pin bumped 0.3.12 → 0.31.2 in the same PR). | Mike White | PR (this) |
| 2026-07-27 | *deployment routing only* (Tech Watcher) | `qwen3.5:9b-q4_K_M` on both tiers | `gpt-oss:20b-cloud` (filter) / `kimi-k3:cloud` (synthesis), both via the Ollama daemon's cloud proxy | **Failure evidence, not a shadow-eval.** The incumbent could not perform the task: it rejected a DOE announcement of a *fourth* reactor criticality as "a single milestone" lacking "independent replication," and named `physical-realization`'s example vocabulary (fusion ignition) for a fission item. Filter prompt v4 addressed every prompt-side cause and moved nothing — 16 items re-scored, 0 verdict changes. Consequence was structural: 8 of 8 clusters ever logged were arXiv-only, so triangulation (≥2 origins + ≥1 hard leg) could never fire and the funnel could not promote anything. See DQ-006, KI-009. | Mike White | PR (this) |

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
- **(d) `local-heavy` tag validity.** `mistral-small:24b-instruct-q4_K_M`
  was seeded the same day as the invalid Qwen tag and has never been
  pulled (the Ryzen worker does not exist yet). Verify the tag against
  the Ollama library — and whether a newer generation supersedes it —
  before the Ryzen worker card lands.
