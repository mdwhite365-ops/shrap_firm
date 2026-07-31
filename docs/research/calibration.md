# Research Department Calibration Ledger

**Version:** 0.1 (draft)
**Date:** 2026-05-30
**Owner:** Mike White
**Status:** Draft — LIVING ACCOUNTABILITY LEDGER
**Serves:** ADR-0007.

## How this file works — read before editing

This file is **append-only**. Entries are never rewritten, edited for
hindsight, or quietly deleted. The point of the ledger is to make the
Research department's track record auditable by a future Mike who has
forgotten the context — and to make the current Mike honest about
which calls hit and which missed.

The Mike-profile norm "help me be right, not happy with my responses"
is the spirit. A clean-looking ledger is a *failure mode*: it means
entries are being curated for ego instead of recorded for calibration.
The expected pattern is many kills, many misses, a small number of
hits, and explicit reasoning attached to all three.

Editing rules:

1. **Append, never rewrite.** Corrections go in a new dated correction
   entry that references the original. Originals stay.
2. **Date every entry (YYYY-MM-DD).** Multiple entries per day are
   fine; ordering matters.
3. **Reasoning at time of decision, not at time of review.** When
   killing a candidate, capture what was known when the kill
   happened. Future hindsight goes in a separate review entry.
4. **No marketing voice.** No "we correctly identified" or "we
   anticipated." State the call and the outcome.
5. **Base-rate math is the point.** The denominator (everything
   considered) matters as much as the numerator (everything
   promoted). Survivorship bias is the enemy.

## Sections

- (a) Tech Watcher kill-graveyard
- (b) Bottleneck Scout forward-test ledger
- (c) Infrastructure Mapper graph-coverage metric
- (d) Strategy Evaluator promotion-rate vs kill-rate over time
- (e) Model Registry eval ledger

Each section appends; none is overwritten.

---

## (a) Tech Watcher kill-graveyard

Running log of world-changer candidates the Tech Watcher (or Mike on
manual review) rejected. Every promoted world-changer has a counterpart
list of siblings that didn't make it; the graveyard is that list. Five
years from now this base-rate denominator is what tells us whether the
Tech Watcher promotion rate (~20% per ADR-0007 §Kill rates) is
calibrated.

**Schema per entry:**
- Date considered
- Candidate name
- Archetype (per `world-changer-archetypes.md`)
- Source(s) that surfaced it
- Kill reason (one of: impostor-match, insufficient evidence,
  unfalsifiable, archetype-mismatch, manual-mike-kill, other-with-note)
- One-line reasoning
- Reviewer (agent / Mike)
- Optional: review-at date for revisit

**Entries:**

*(none yet — file initialized 2026-05-30; first entries land when Tech
Watcher goes live in Month 2 per ADR-0007 sprint scope)*

**[MIKE INPUT REQUIRED]** Seed the graveyard with the impostor lists
from `world-changer-archetypes.md` (Theranos, hydrogen-by-2025,
metaverse, LK-99, full-self-driving-2020, etc.) as **historical
calibration anchors** with kill-date = the date the impostor was
publicly invalidated. This gives the Tech Watcher's base-rate math a
non-zero starting denominator and a tested calibration prior on day 1.
Decision point for Mike before Month 2 launch.

---

## (b) Bottleneck Scout forward-test ledger

Locked predictions. Once a bottleneck candidate is emitted by the
Scout with a `validation_horizon`, the call is logged here and never
edited until the horizon date passes. The outcome (validated, killed,
still-pending past horizon) is appended on the horizon date or sooner
if a kill criterion fires.

This is the load-bearing accountability artifact for the firm's edge
claim. The Sept 2024 LITE backwards-test is methodology debug; this
ledger is the real edge proof.

**Schema per locked prediction:**
- Lock date (date the candidate hit `detected` status)
- Candidate id (links to `research.bottlenecks` row)
- World-changer it constrains
- Bottleneck layer role
- Named physical/economic limit
- Forced substitute(s) — public tickers
- Timeline-to-binding (ordinal: forming / near / binding-now /
  solved-or-deferred)
- Evidence snapshot (hash + reference to evidence rows at lock time)
- Kill criteria (verbatim, locked)
- Expected horizon date (latest by which at least one kill criterion
  is observable)
- Outcome status: `pending` / `validated` / `binding` / `killed` /
  `expired-no-resolution`
- Outcome date and outcome note (appended, never edited)

**Entries:**

*(none yet — file initialized 2026-05-30; first locked prediction
expected when the backwards-test rubric is written and the live Scout
runs Month 3 per `bottleneck-scout.md` §Sprint scope)*

**[MIKE INPUT REQUIRED]** The Sept 2024 LITE backwards-test result will
be the first ledger entry, with explicit flag that it is a
*reproduction* not a *forward call* — its predictive weight is much
lower than any live forward entry. Rubric for what counts as
backwards-test pass / partial-pass / fail is an open item in
`bottleneck-scout.md` §Open questions and must be written before the
run.

**Self-honesty alarms tracked here:**
- Trailing-90d binding rate exceeds 25% of detected → selection-bias
  warning per `bottleneck-scout.md` §Processing step 12.
- Trailing-90d kill rate falls below 60% on detected candidates →
  promotion-too-easy warning (the funnel is supposed to be brutal).
- Forward outcomes consistently land outside the locked horizon →
  timeline-ordinals are mis-calibrated and need re-anchoring.

---

## (c) Infrastructure Mapper graph-coverage metric

For each promoted world-changer's graph, what fraction of the
world-changer's *actual realized revenue dependency tree* (verifiable
ex post from supplier disclosures, hyperscaler capex attribution,
10-K supplier mentions, and earnings call name-checks) is represented
as nodes in the graph?

A graph that covers 30% of the realized dependency tree is mostly
guessing. A graph that covers 90% is doing the structural research
the ADR-0007 thesis depends on. The Cisco-1999 failure mode shows up
here as graphs that mis-rate node criticality — the right nodes
present, the wrong ones weighted.

**Coverage measured quarterly:**

- **Numerator:** Distinct suppliers, partners, and downstream
  beneficiaries that are (a) named in the world-changer's actual
  realized supply chain per primary-source disclosures during the
  quarter AND (b) present as nodes in the graph at end of quarter.
- **Denominator:** All distinct suppliers, partners, and downstream
  beneficiaries named in primary-source disclosures during the
  quarter, irrespective of whether the Mapper had captured them.
- **Criticality calibration:** Of nodes the Mapper flagged
  critical-path, what fraction were named as binding constraints in
  earnings discussions? Of nodes the Mapper flagged
  non-critical-path, what fraction surprised by becoming binding?

**Entries:**

*(none yet — first measurement expected end of Month 4 when the
Mapper has at least one graph in `validated` state for a full
reporting quarter)*

**[MIKE INPUT REQUIRED]** Concrete methodology for assembling the
denominator: which primary sources count, how to normalize
supplier-name aliasing, what to do about un-disclosed suppliers
(known to exist, name not public). Until the methodology is written,
the coverage metric is a placeholder.

**Honest caveat.** Coverage measured this way is biased toward
*disclosed* dependencies. A world-changer's most-strategic suppliers
are often the least-disclosed (sole-source competitive moats). 90%
coverage of disclosed suppliers may still miss the layer that matters
most. Coverage is a necessary metric, not a sufficient one.

---

## (d) Strategy Evaluator promotion-rate vs kill-rate over time

Per ADR-0007 §Kill rates compound across the funnel, the Strategy
Evaluator kills 90%+ of hypotheses reaching it. This section tracks
that rate over time, broken down by which Bottleneck Scout candidate
fed the hypothesis.

**Metrics per reporting period (monthly):**

- N hypotheses received from Hypothesis Generator
- N promoted to paper-strategy status
- N killed at evaluator
- Kill reasons distribution (PBO fail, deflated-Sharpe fail, trade-
  count floor, transaction-cost realism, missing bottleneck kill
  criterion, overfitting flag, other)
- Promotion rate (promoted / received)
- Trailing-12-month promotion rate
- Rolling association between Bottleneck Scout candidate that seeded
  the hypothesis and downstream evaluator promotion — i.e. which
  Scout calls feed hypotheses that survive evaluation, and which
  don't. This is the cross-link that tells Mike whether the funnel
  is bottlenecked at Step 3 detection or at Step 4 evaluation.

**Entries:**

*(none yet — first monthly snapshot expected end of Month 3 when at
least one hypothesis has run the full funnel including evaluator)*

**[MIKE INPUT REQUIRED]** Target promotion rate. ADR-0007 implies "low"
without naming a number; Mike should set an explicit target band (e.g.
"3-10% promoted is healthy; >20% suggests the evaluator's overfitting
controls are too loose; <1% suggests the Hypothesis Generator is
mis-specifying trades from valid Scout signals") so the metric has a
reference line to read against.

---

## (e) Model Registry eval ledger

Where every shadow-eval run lands, per ADR-0009 §Update Protocol step 3.
This section is created by the harness card (2026-07-30) and, as ADR-0009
predicted, **it is created empty: no model eval has ever been run.** Every
model in `docs/infrastructure/llm-registry.md` was chosen by reasoning, and
one of them was chosen twice — the first `qwen2.5:9b` tag never existed.
That is the gap this section exists to close.

**How a run gets here.** `shrap-model-eval` renders the block; the operator
appends it (`--out docs/research/calibration.md`) and commits it in the same
PR that proposes the registry change, per step 4. A *rejected* candidate is
recorded here too — a ledger that only contains promotions is the curated
kind this file's editing rules forbid.

```bash
docker compose exec tech-watcher shrap-model-eval \
    --models gpt-oss:20b-cloud,kimi-k2.5 --sample 30 --repeats 2 --dry-run
```

**What the harness measures, all mechanically:**

| Metric | Reads as |
|---|---|
| schema adherence | did the response parse into a valid verdict at all — a model that fails the strict-JSON contract is out regardless of judgement |
| self-consistency | same item, same model, twice. A model that disagrees with itself cannot hold a gate |
| agreement with incumbent | overlap with the recorded verdict. **Not** a quality measure — it localizes where to look |
| says-relevant rate | the model's own base rate, against a corpus that is ~99% negative |
| latency p50/p95 | what it costs in wall clock |

**What it does not do: decide.** Nothing in the harness knows which model was
*right*. Agreement is not correctness — two models can agree and both be
wrong, which is the more likely failure on a corpus this lopsided. Every run
ends in a disagreement list, and adjudicating it is Mike's, per ADR-0009's
"scored by Mike". An LLM judge is deliberately not in v1: it would require
choosing a judge model, and the premise of this section is that model choices
need evidence.

**Sampling is stratified, and this is load-bearing.** The filter corpus runs
roughly 99% not-relevant. A uniform 30-item sample is ~30 rejections, every
model agrees on all of them, and the report reads 100% agreement while having
measured nothing. The harness takes every available incumbent-relevant item
up to half the budget and fills the rest from negatives. If a run reports zero
positives in its sample, the report says so and the numbers are a floor rather
than a ranking.

**Pass criterion (ADR-0009 step 2, stated once here so runs are comparable).**
A candidate is promotable when it (1) matches or beats the incumbent on schema
adherence, (2) is at least as self-consistent, (3) shows no material latency
regression for the tier's role, and (4) wins the adjudicated disagreements on
Mike's read. Criterion 4 is the one that decides; 1–3 are disqualifiers, not
recommendations. **Cost is a separate gate:** Ollama bills GPU-time against
shared session and weekly caps, so a candidate that wins on quality can still
be wrong for a high-volume bulk tier and right for a low-volume judgement one.
Record which tier the run was for.

### Runs

Three runs took place before the first decision was recorded. All three are
here. The first two produced numbers that were wrong, and deleting them would
be the curated-ledger failure this file's editing rules forbid — the
denominator is never hidden, including when the thing being counted is our own
defective instrument.

#### Run 1 — 2026-07-30 · `local-classification` / `filter` · **superseded, do not cite**

Six candidates against `gpt-oss:20b-cloud`, 20 items. **Its judgement columns
were invalid and no decision was taken from it.** `compute_metrics` excluded
calls that *raised* but not calls that failed to *parse*, and
`parse_filter_response` turns an unparseable response into `relevant=False`. On
a corpus that is ~90% negative, a model that parsed nothing therefore "agreed"
with the incumbent ~90% of the time. `glm-5.2` scored 10% schema adherence and
90% agreement; `kimi-k2.5` scored 20% and 90%. Both figures were properties of
the metric.

What survived the correction: `nemotron-3-super` at 34s p95, disqualifying for
bulk work at any quality. Fixed in PR #171.

#### Run 2 — 2026-07-31 (morning) · `local-classification` / `filter` · **superseded**

Same six candidates on the corrected harness. Judgement columns valid, but the
**schema** column was not, for a reason the run itself diagnosed:
`parse_filter_response` called `json.loads` on the raw completion, so JSON
wrapped in a markdown fence was scored as an unparseable verdict.

| model | schema | unparsed | cause | recoverable |
|---|---|---|---|---|
| `glm-5.2` | 25% | 30/40 | fenced-json | 30 of 30 |
| `deepseek-v4-pro:cloud` | 95% | 2/40 | fenced-json | 2 of 2 |

The harness named it correctly — *"that is our defect, not the model's"* —
which is the first thing it has caught that the test suite did not. Fixed in
PR #172.

Two candidates never produced a verdict, and the distinct-error reporting added
in #171 said why rather than rendering a row of zeroes: **`kimi-k2.5` → HTTP
410, retired 2026-07-31 00:00 PDT**; **`kimi-k3` → HTTP 402, "this model uses
extra usage only (not included plan usage) and your extra usage balance is
empty."** Neither was a tag error, which is what both had previously been
assumed to be. `kimi-k3` is not callable under the Pro subscription without a
separately funded balance — the third time that model has been proposed and
blocked.

#### Run 3 — 2026-07-31 (afternoon) · `local-classification` / `filter` · **the decision run**

Five models, 20 items, seed 7, 2 repeats, 200 completions, prompt v4. Strata:
incumbent-relevant 2, incumbent-not-relevant 18, never-scored 0.

| model | schema | judged | self-consist | agrees w/ incumbent | says relevant | p50 ms | p95 ms | errors | usage tier |
|---|---|---|---|---|---|---|---|---|---|
| `gpt-oss:20b-cloud` (incumbent) | 100% | 20/40 | 100% | 90% | 0% | 6980 | 12769 | 0 | low |
| `qwen3.5:397b` | 100% | 20/40 | 100% | 90% | 0% | **2114** | **2539** | 0 | **medium** |
| `deepseek-v4-pro:cloud` | 100% | 20/40 | 100% | 90% | 0% | 1193 | 1596 | 0 | extra high |
| `glm-5.2` | 100% | 20/40 | 100% | 90% | 0% | 2327 | 6869 | 0 | high |
| `kimi-k2.6` | 100% | 20/40 | 100% | 90% | 0% | 1577 | 2574 | 0 | high |

Pairwise agreement on relevance: **100% across all ten pairs. Zero
disagreements.** There was nothing to adjudicate.

**Verdict: promote `qwen3.5:397b`** on the Tech Watcher filter binding.
Approved by Mike White, 2026-07-31.

The five models are **indistinguishable on judgement** — identical schema
adherence, self-consistency, incumbent agreement and says-relevant rate, with
no disagreement anywhere in the sample. That makes this a decision about
latency and usage tier only, and criterion 4 (Mike's read of the disagreements)
never engages because there are none. `qwen3.5:397b` is the lowest usage tier
among the fast models and its p95 is effectively tied with the best of them,
against an incumbent five times slower at p95.

**The cost gate is cleared on measurement, not waived.** The week of
2026-07-31 spent 3,320 requests across all models — 2,941 of them the
production filter — for **1.2% of the Pro weekly allowance**. A one-tier move
on the bulk filter does not bind at roughly 80x headroom. Ollama bills
GPU-time, and the promoted model is ~3.3x faster at p50, so the move may be
cheaper in the billed unit; that is not the basis for the decision and has not
been measured directly.

**Rejected candidates and why** (recorded, per the editing rules):

- `deepseek-v4-pro:cloud` — extra-high usage tier for output identical to a
  medium-tier model. Its Run-2 false positive (a routine 10-Q admitted as
  `platform-shift` because "the filing's existence meets the attested bar")
  **did not reproduce here**; it was a single non-repeating sample, consistent
  with that run's 95% self-consistency, and it is not part of this rejection.
- `kimi-k2.6` — high usage tier, no advantage over the promoted model.
- `glm-5.2` — high usage tier, and the slowest p95 of the four candidates.
  Note that its Run-2 p95 of 22.5s **did not reproduce** (6.9s here), so its
  earlier apparent latency disqualification was run-to-run variance rather than
  a property of the model.
- `nemotron-3-super` — 34s p95 in Run 1. Not re-run.
- `kimi-k2.5` — retired by the provider.
- `kimi-k3` — outside the subscription's included usage.

**Scope of this promotion.** The eval measured the **filter** task only, so
only `SHRAP_FILTER_MODEL` moves. `SHRAP_INTEL_BULK_MODEL` — the News Analyzer
and Filing Processor materiality calls, which resolve the same tier alias —
stays on `gpt-oss:20b-cloud` until a run measures *that* task. Extending an
unmeasured conclusion across tasks is the exact habit this section exists to
end.

**What this run does not show.** Only 2 incumbent-relevant items were available
in the whole sampled corpus, and **all five models rejected both**. The
agreement column is therefore a statement about rejections, which every model
finds easy — a floor, not a ranking. More sample would not fix this: the
stratified sampler already takes every available positive, so the positive
class is starved by the corpus, not the budget. That is KI-009, and it is the
subject of `docs/research/archetype-bar-experiment.md` rather than of any model
choice. **Five model families spanning four usage tiers returning 0% relevant
on the same corpus is the strongest evidence yet that the filter is not
model-limited.**

---

## Cross-section principles

1. **No promotional reframing.** A kill that turns out to have been
   correct in hindsight is still a kill; do not move it to a "we
   correctly killed" trophy section. Entries are facts on the date
   they were recorded.
2. **Hindsight reviews welcomed but dated separately.** When a
   prediction's outcome makes the original reasoning look wrong (good
   call, bad reasoning; or bad call, good reasoning), append a review
   entry dated to the review, not to the original.
3. **The ledger is the firm's memory.** The Tech Watcher, Bottleneck
   Scout, and Infrastructure Mapper are agents; this ledger is the
   institutional record of how well they did. The agents do not write
   to this file directly during the sprint — Mike does, with agent
   evidence attached. Automated append is a Month-6+ question.
4. **Public sharing.** This file is internal-only by default. Any
   external sharing requires Mike's explicit decision per entry,
   per ADR-0007 firm-wide paper-only constraint and the firm's
   pre-edge posture.

## Mike-input-required summary

- Seeding (a) with historical impostor entries as calibration anchors.
- Backwards-test pass/partial/fail rubric for (b)'s first entry.
- Denominator methodology for (c).
- Target promotion band for (d).
- Whether and when (Month 6+) to automate append from agents vs keep
  Mike-in-the-loop.
