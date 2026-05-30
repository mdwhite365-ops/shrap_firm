# Hypothesis Generator

**Department:** Research
**LLM tier:** Cloud (Claude Sonnet 4.6) primary, Cloud (Opus 4.7) for the once-weekly
"hard problems" batch. Migration target: Local (Mistral Small 24B) for routine
hypotheses once shadow evaluation passes. See `docs/infrastructure/llm-routing.md`.
**Status:** Draft
**Date:** 2026-05-29
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Hypothesis Generator proposes new strategy specifications for the Strategy Evaluator
to test. Its job is *not* to invent edge — most of its proposals will be killed — but to
keep the research funnel fed with constrained, falsifiable, regime-conditional candidates
the Evaluator can validate or reject.

The agent exists because naive "ask an LLM for a trading strategy" produces hallucinated
nonsense: vague rules, no kill conditions, no regime grounding, no statistical
discipline. This agent suppresses that failure mode by forcing every hypothesis to be
anchored in (a) the current regime label, (b) at least one named member of the curated
universe, and (c) an explicit kill condition. Hypotheses that lack any of these are
rejected before they leave the agent.

What this agent cannot do: it cannot tell whether a hypothesis has real edge. It is a
proposer. The Strategy Evaluator is the gatekeeper. The expected kill rate for proposals
from this agent is 90%+, and that is the design target, not a failure mode. A
Hypothesis Generator that produced strategies with a 50% promotion rate would be
suspicious — almost certainly overfit or peeking.

## Trigger

- **Schedule:** One batch per trading day at 19:00 ET (after the daily regime classifier
  run, before the overnight backtest queue). Configurable batch size, default 5
  hypotheses per night.
- **Event:** Subscribes to `research.regime.changed` — on a confirmed regime change,
  generates a small (default 3) targeted batch within 30 minutes to refresh the candidate
  set against the new regime.
- **On-demand:** Mike-initiated `research.hypothesis.request` with optional constraints
  (ticker, regime, theme).

## Cross-references

**Depends on:** Regime Classifier, Strategy Librarian (for prior-art retrieval), Universe
Curator (per-ticker profiles), Intelligence Department (recent themes for context).
**Depended on by:** Strategy Evaluator (consumes proposals), Mike (reviews novel
proposals before they consume backtest budget).
**Related ADRs:** ADR-0006 (envelope).
**Related architecture sections:** `docs/02-architecture.md` §Research Department,
§Strategy lifecycle.

## Inputs

| Source | Type | Description |
|---|---|---|
| Redis: `research.regime.tick` | Event | Current regime label and feature vector |
| PostgreSQL: `research.regime_history` | Query | Last 90 days of regime labels (for stability check) |
| Repo: `docs/regimes/<label>.md` | File read | Active regime profile, including documented edge archetypes |
| Repo: `docs/tickers/*.md` | File read | Per-ticker profiles (behavior, sensitivity, prior patterns) |
| PostgreSQL: `research.strategies` | Query | All prior strategies (any status). Used as anti-duplication input. |
| Qdrant: `strategy_corpus` | Semantic search | Prior hypotheses indexed by description — checks novelty |
| Redis: `intel.daily.summary` (ref) | Event | Yesterday's intelligence digest for thematic grounding |
| Repo: `docs/research/templates/*.md` | File read | Allowed strategy archetype templates (mean-reversion, breakout, news-fade, sweep-confluence, etc.) |

## Processing

1. **Build the constraint frame.** Pull current regime label, top 3 historical analogs,
   the regime profile's documented edge archetypes, and the per-ticker profiles for the
   universe subset relevant to those archetypes (e.g. high-retail-interest names if
   "trap-friendly" is in the archetype list).
2. **Retrieve prior art.** Qdrant semantic search against `strategy_corpus` to surface
   similar past hypotheses and their outcomes. The prompt explicitly tells the LLM "do
   not re-propose a strategy substantially similar to a killed one without naming what is
   different and why."
3. **Generate proposals.** LLM call with strict JSON schema output. Each proposal must
   include: `name`, `archetype` (must match an allowed template), `regime_fit` (list of
   regime labels where the strategy should activate), `regime_kill` (list of regimes that
   force retirement), `tickers` (subset of the universe), `entry_rules` (deterministic
   pseudocode), `exit_rules`, `stop_rules`, `hypothesized_edge_bps`, `hypothesized_trade_frequency`,
   `falsifier` (what observation would prove this wrong), and `prior_art_refs`.
4. **Reject malformed proposals locally.** A deterministic validator drops proposals that
   fail schema validation, name un-allowed archetypes, reference tickers outside the
   universe, omit a `falsifier`, or are >0.85 cosine-similar to a killed prior. Rejections
   are logged with reason. The agent does not retry the LLM to fix rejections — it logs
   and moves on.
5. **Persist and publish.** Surviving proposals are written to `research.strategies` with
   status `hypothesis`, indexed in Qdrant, and an event is emitted per proposal.
6. **Daily summary.** One end-of-batch event summarizing N generated, N rejected locally,
   and the rejection reasons, for the Daily Briefing Agent and Mike.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `research.hypothesis.proposed` | Event | One per surviving proposal, payload-by-reference to the full spec |
| Redis stream: `research.hypothesis.batch.summary` | Event | End-of-batch rollup |
| PostgreSQL: `research.strategies` | Insert | Full proposal record with status=`hypothesis` |
| Qdrant: `strategy_corpus` | Upsert | Embedding of proposal description and rules |
| Repo: `docs/strategies/proposed/<id>.md` (auto-generated, in a sandboxed branch) | File write | Human-readable proposal card. Never auto-merged. |

Every event carries the ADR-0006 envelope. Every proposal record carries the LLM call's
prompt hash, model, temperature, and raw response reference so any future audit can
reconstruct exactly what the LLM was asked and what it returned.

## LangGraph structure

**Nodes:**
- `build-frame` — assemble constraint context
- `retrieve-prior-art` — Qdrant + Postgres lookup
- `llm-generate` — constrained generation
- `validate` — schema and policy checks
- `persist-emit` — write to stores, publish events

**Key edges:**
- `build-frame` → `retrieve-prior-art` → `llm-generate` → `validate` → `persist-emit`
- `validate` → `persist-emit` (per-proposal: drop or keep)

## State

| What | Store | Notes |
|---|---|---|
| All proposed hypotheses (lifetime) | PostgreSQL `research.strategies` | Append-only with status transitions |
| Embeddings | Qdrant `strategy_corpus` | Indexed for novelty checks |
| Per-batch run record | PostgreSQL `research.hypothesis_batches` | Prompt hash, model, n_generated, n_kept |

## Failure behavior

1. **Containment.** A bad proposal does not move money — the Strategy Evaluator gates it.
   The realistic blast radius is wasted backtest compute and a noisier prior-art corpus.
   Both are bounded.
2. **Replay safety.** LLM calls are non-deterministic, so replay does not regenerate
   identical proposals. Replay-of-record is sufficient: the persisted proposal is the
   source of truth, the LLM call is logged but not re-executed.
3. **Degraded operation.** The firm runs fine without this agent for weeks at a time. No
   hypotheses means no new strategies entering the funnel; existing live strategies
   continue. If the LLM tier is unavailable, the agent skips its batch and emits a
   degraded-skip event.

## Sprint scope

- Month 2: Daily batch generation against a small starting set of templates (3–4
  archetypes). Constraint-frame and rejection validator working. No prior-art retrieval
  beyond a basic Postgres LIKE search.
- Month 3: Qdrant prior-art retrieval enabled. Regime-change-triggered batches enabled.
- Month 4: Per-ticker profile integration. Shadow-test local model on the routine batch.

## Deferred

- Multi-agent debate / critique loops on proposals (boring beats clever).
- Automatic parameter sweeps within a proposal — that is the Evaluator's job.
- Cross-asset strategies — universe is fixed for the sprint.
- Options strategies — deferred to post-launch.

## Open questions

- **Batch size cap:** Default 5/night. Blocks: Strategy Evaluator capacity planning.
  Owner: Mike after first week of running.
- **Should regime-change-triggered batches require Mike's approval before backtest?**
  Currently no. Blocks: trust progression timeline. Owner: Mike.
- **How aggressively should novelty be enforced?** 0.85 cosine threshold is a guess.
  Blocks: avoiding "near-duplicate of killed strategy" pollution. Owner: Mike, after
  reviewing the first month of rejected proposals.
