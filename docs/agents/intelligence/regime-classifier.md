# Regime Classifier

**Department:** Intelligence (moved from Research per ADR-0007)
**LLM tier:** Hybrid — statistical layer is No LLM (deterministic); historical-analog layer is Cloud (Claude Sonnet 4.6) with planned migration to Local (Qwen 14B). See `docs/infrastructure/llm-routing.md`.
**Status:** Draft
**Date:** 2026-05-29
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Regime Classifier produces a single piece of context the Risk Officer reads
to modulate position sizing: "what kind of market are we in, right now, and what
historical periods does it most resemble." Per ADR-0007, the classifier is a
**sizing modifier, not a strategy-activation gate.** Strategies are activated by
infrastructure graph state and Bottleneck Scout events; this agent's output tells
the Risk Officer how much size those active strategies are allowed to take in the
current environment, and which historical analogs argue for caution or
aggression.

It operates in two layers, which are kept deliberately separate so failures in one do not
silently corrupt the other:

1. **Statistical layer (deterministic).** Computes a state vector from price, volume,
   breadth, dispersion, and term-structure inputs and maps it to a labeled regime drawn
   from `docs/regimes/`. This layer is reproducible, fast, and the source of truth when
   the LLM layer is unavailable. Mechanically unchanged from the v0.1 Research-
   department spec.
2. **Historical-analog layer (LLM-driven).** Reads the current statistical state plus
   recent macro/intel context and returns a ranked list of historical periods the current
   regime most resembles (e.g. "late-1998 LTCM-recovery melt-up," "Q4-2018 risk-off,"
   "post-COVID liquidity bloom"). These analogs are advisory context for the Risk
   Officer's sizing calculation — they never override the statistical label and they
   never directly trigger or block a trade. Mechanically unchanged from the v0.1 spec.

What this agent cannot do, stated up front: it cannot predict regime *transitions* before
they are visible in the data. It is a classifier, not a forecaster. It also cannot give a
calibrated probability that its label is "correct" in any frequentist sense; the regime
taxonomy is a human-defined ontology and the confidence scores it emits are internal
consistency metrics, not posterior probabilities. Mike should read confidence as "how
unanimous the underlying features are," not "how likely we are right." Because the
classifier is now a sizing modifier rather than a strategy gate, a wrong label produces
wrong sizing rather than wrong activation — still consequential, but a smaller blast
radius than under the v0.1 framing.

## Trigger

- **Schedule:** Statistical layer runs every 5 minutes during US market hours (09:30–16:00 ET)
  and once at 18:00 ET for the daily closing snapshot. Historical-analog layer runs once
  per trading day at 18:15 ET, and on demand when the statistical layer emits
  `intel.regime.changed`.
- **Event:** Subscribes to `intel.macro.updated` (refreshes analog inputs) and
  `ops.health.degraded` (drops to deterministic-only mode if the LLM tier is unhealthy).
- **On-demand:** Mike or the Risk Officer can request a fresh classification with
  an `intel.regime.classify.request` event. (The Hypothesis Generator no longer
  consumes this output and no longer issues on-demand requests; per ADR-0007, the
  Hypothesis Generator is driven by Bottleneck Scout events instead.)

## Cross-references

**Depends on:** Market Structure Reader (term structure, breadth), Intelligence
Department's macro feeds, Operations Department (data freshness guarantees).
**Depended on by:** **Risk Officer** (primary consumer — sizing modifier). Daily
Briefing Agent (reads label and analogs for Mike's morning summary). Strategy
Evaluator (uses regime label to stratify backtest splits for out-of-distribution
checks, but no longer for activation gating). The Hypothesis Generator, Regime
Router, and Decision Maker no longer consume this agent directly — strategy
activation is driven by Bottleneck Scout events under ADR-0007.
**Related ADRs:** ADR-0004 (observability), ADR-0006 (Redis Streams envelope),
ADR-0007 (Research thesis — the decision that moved this agent and changed its
downstream consumer).
**Related architecture sections:** `docs/02-architecture.md` §Intelligence
Department, §Regime taxonomy.

## Inputs

| Source | Type | Description |
|---|---|---|
| PostgreSQL: `market_data.ohlcv_1d`, `ohlcv_5m` | Query | Last 504 trading days of price/volume for the 50-name universe plus SPY, QQQ, IWM, VIX, MOVE, HYG, TLT |
| PostgreSQL: `market_data.breadth` | Query | Advance/decline, new highs/lows, % above 200dma |
| PostgreSQL: `market_data.term_structure` | Query | VIX1/VIX3, VX1/VX2 contango/backwardation |
| Redis: `intel.macro.updated` | Event | Triggers re-pull of macro context payload |
| Redis: `intel.macro.summary` (payload-by-reference per ADR-0006) | Object store | Rolling 30-day macro narrative for analog layer |
| Repo: `docs/regimes/*.md` | File read | Regime profile cards — definition, feature thresholds, fit/kill metadata |
| Qdrant: `regime_analogs` | Semantic search | Historical analog vignettes (Mike-authored + agent-curated) |

## Processing

1. **Pull features.** Compute the deterministic feature vector: realized vol (5d, 20d,
   60d), trend strength (slope of 50d EMA, % above 200dma), breadth metrics, sector
   dispersion, VIX term-structure slope, credit spreads (HYG/TLT ratio), and FX/rates
   correlations. Use the documented formulas in `docs/regimes/_features.md`. If any input
   is stale beyond its documented tolerance, mark the feature as missing and continue —
   do not interpolate silently.
2. **Score against regime profiles.** For each profile in `docs/regimes/`, compute a fit
   score by comparing the live feature vector against the profile's documented thresholds.
   The mapping is rule-based, not learned — a regime fits if it satisfies its hard
   conditions and at least N of its soft conditions. Boring beats clever.
3. **Pick the label.** Choose the highest-scoring profile. If two profiles tie within a
   documented epsilon, prefer the prior label (hysteresis); persistent ties surface as
   open questions for Mike rather than coin flips.
4. **Detect change.** Compare to the last persisted label. If different and the new label
   has been the leader for at least M consecutive runs (default M=3, debounce window), emit
   `regime.changed`. Otherwise emit `regime.tick` with the unchanged label.
5. **Run historical-analog layer (daily and on-change).** Pass the live feature vector,
   the chosen statistical label, the 30-day macro summary, and the top-K Qdrant analog
   hits to the LLM. Prompt asks for: (a) three best historical analogs with one-paragraph
   justifications each, (b) what worked and what failed in those periods, (c) what would
   invalidate the analog. Output is JSON-validated against the analog schema.
6. **Publish.** Write both layers' outputs to Redis Streams and persist to Postgres.

## Outputs

The primary output under ADR-0007 is `intel.regime.sizing-modifier`, consumed by
the Risk Officer. Heartbeat, change, and analog streams are preserved for audit
and for the Daily Briefing Agent.

| Destination | Type | Description |
|---|---|---|
| Redis stream: `intel.regime.sizing-modifier` | Event | **Primary output.** Consumed by Risk Officer. Carries: current statistical label, confidence, the top-K historical analogs and their fit/kill summaries, and a recommended sizing multiplier band (e.g. `[0.5, 1.0]` for risk-off regimes, `[1.0, 1.5]` for benign trend regimes) with the rule-based derivation attached. The Risk Officer applies this band on top of its Kelly-fractional sizing; this agent does not size positions directly. |
| Redis stream: `intel.regime.tick` | Event | Per-run heartbeat with current label + features (payload-by-reference) |
| Redis stream: `intel.regime.changed` | Event | Emitted only on debounced label change. Includes prior label, new label, debounce window, top contributing features. Audit and Daily Briefing only — no longer consumed for strategy activation. |
| Redis stream: `intel.regime.analog` | Event | Daily / on-change historical analog output |
| PostgreSQL: `intel.regime_history` | Append-only write | Every tick: timestamp, features, scores, label, analog payload ref, sizing-modifier band, confidence, ULID event_id, schema_version, correlation_id |
| PostgreSQL: `intel.regime_changes` | Append-only write | One row per debounced transition |
| Qdrant: `regime_analogs` | Upsert | New analog summaries indexed for future retrieval |

Legacy `research.regime.*` stream names from the v0.1 spec are retained as
read-only aliases for one sprint to avoid breaking any in-flight consumers, then
removed. The envelope-registry update accompanying the ADR-0007 agent rewrites
documents the alias window.

Every emitted event conforms to the ADR-0006 envelope: ULID `event_id`, `schema_version`,
`correlation_id` (set to the triggering event when applicable), `stream`, `produced_at`,
and `payload` or `payload_ref`. This is the firm's audit substrate; it is non-negotiable.

## LangGraph structure

**Nodes:**
- `pull-features` — deterministic feature collection
- `score-profiles` — rule-based scoring against regime profile cards
- `debounce` — apply hysteresis and change-detection
- `analog-llm` — historical-analog reasoning (skipped if LLM tier degraded)
- `validate-emit` — JSON-schema validate and publish

**Key edges:**
- `pull-features` → `score-profiles` → `debounce` → `validate-emit`
- `debounce` → `analog-llm` → `validate-emit` (only on change or daily slot)
- `pull-features` → `validate-emit` (degraded path: feature-missing breaker trips,
  classifier emits last-known label with degraded flag)

## State

| What | Store | Notes |
|---|---|---|
| Last emitted label and debounce counter | Redis hash `regime:current` | TTL refreshed each tick |
| Full tick history | PostgreSQL `research.regime_history` | Append-only, indexed by timestamp |
| Cached macro context for analog layer | Redis (object store ref) | TTL 24h |
| Regime profile checksums | Repo + Redis | Detect when `docs/regimes/` changes mid-day; reload on checksum mismatch |

## Failure behavior

1. **Containment.** A wrong label propagates broadly — Hypothesis Generator, Regime
   Router, and Decision Maker all key off it. To contain blast radius, downstream consumers
   are required to read `regime.changed` (debounced) rather than `regime.tick`. Single bad
   ticks do not trigger trades. A sustained wrong label *does* mis-route strategies, but
   the Risk Officer's regime-aware caps and the Strategy Evaluator's regime-stratified
   kill criteria provide secondary defenses.
2. **Replay safety.** Safe. The classifier is a pure function of feature inputs and the
   regime profiles in the repo. Replaying ticks from a Redis checkpoint reproduces the
   same labels deterministically. The analog layer is non-deterministic (LLM) but its
   output is advisory; replay records the original output, it is not regenerated on
   replay.
3. **Degraded operation.** The firm can run without the analog layer indefinitely — it is
   advisory. The firm cannot run without the statistical layer for more than one trading
   session before Mike should manually halt new strategy activations; without a label, the
   Regime Router defaults to a documented "unknown regime" mode that activates only
   regime-agnostic strategies (initially: none).

## Sprint scope

- Month 2: Statistical layer with 6–8 hand-authored regime profiles. Debounce + change
  events on Redis. Analog layer behind a feature flag, cloud LLM only.
- Month 3: Analog layer enabled by default. Qdrant analog index populated with a
  Mike-curated seed set of 30–50 historical vignettes.
- Month 4: Shadow-evaluate Qwen 14B against Claude on analog layer; document
  before-migration baseline.

## Deferred

- Learned regime labels (clustering, HMM) — boring beats clever; rule-based first.
- Intraday regime switching at sub-5-minute resolution.
- Cross-asset regime classification beyond the documented macro tickers.
- Automatic profile authoring — new profiles require Mike-approved PR.

## Open questions

- **Debounce window M:** Default M=3 (15 minutes intraday, 3 days end-of-day). Blocks:
  publishing the spec as approved. Owner: Mike.
- **Tie-break epsilon:** What score gap counts as "tied"? Blocks: deterministic behavior
  under near-ties. Owner: Mike after regime profile cards are drafted.
- **Sizing-modifier band derivation rules.** What is the exact mapping from
  (statistical label, analog set, confidence) to the `[min, max]` multiplier
  band the Risk Officer consumes? The v0.1 spec did not need this because the
  classifier was a gate; under ADR-0007 it is required. Proposed first pass:
  hand-authored per-regime bands stored in `docs/regimes/<regime>.md` with the
  historical-analog layer allowed to nudge within the band but not outside it.
  Blocks: Risk Officer sizing-logic finalization. Owner: Mike, with Risk Officer
  spec author.
- **Should the analog layer ever clamp size to zero?** The v0.1 question
  ("should the analog layer ever block trading?") was answered "no" under the
  gate framing. Under the sizing-modifier framing, the equivalent question is
  whether the analog layer can drive the sizing multiplier band to `[0.0, 0.0]`
  in extreme historical-analog matches (e.g. a recognized pre-1987-crash
  pattern). Current proposed answer: yes, but only via an explicit
  Mike-approved analog tag, never via free-form LLM judgment. Blocks: Risk
  Officer spec. Owner: Mike.
- **Stream rename migration window.** How long do the `research.regime.*`
  aliases stay live before removal? Default proposal: one sprint (4 weeks).
  Blocks: envelope registry update. Owner: Mike.
