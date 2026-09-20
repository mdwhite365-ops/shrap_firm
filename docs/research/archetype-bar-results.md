# Archetype bar experiment — results

**Status:** partial. A 200-item stratified pilot ran 2026-09-20. The full-corpus
run the spec asks for has **not** happened, for a reason measured below.

Spec: `docs/research/archetype-bar-experiment.md`. Harness:
`src/shrap/research/bar_experiment.py`. Raw results:
`research.bar_experiment_results`, run `01M2Y5JD4H6DNH42NZ7DT3YG1M`.

## What ran

200 items, stratified proportionally across all five sources by
`stratified_limit`, with the two control items force-included. Three bars, one
model, same prompt scaffolding — 600 completions on **`kimi-k3`**, 19 minutes.

`kimi-k3` rather than the spec's `qwen3.5:397b` because the incumbent was
promoted out in #231 and Ollama retires it on 2026-09-25. An experiment that
ruled on a bar using a model the firm is about to lose would answer the wrong
question.

## The numbers

| bar | admitted | rate | hard-leg admits / scored |
|---|---|---|---|
| `A-incumbent` | 2 | 1.0% | **2 / 146** |
| `B-evidence-contribution` | 2 | 1.0% | 1 / 146 |
| `C-signal-tagging` | 1 | 0.5% | **0 / 146** |

**Read the hard-leg column, not the rate.** arXiv-only clusters fail
triangulation on both conditions at once, so a bar admitting only arXiv has
unblocked nothing.

**These three numbers are not distinguishable from each other.** Two admits, two
admits and one admit out of 146 hard-leg items is noise, and treating `A > B > C`
as a ranking would be exactly the error KI-036 exists to refuse. The spec
predicted this failure mode in its own words — *"if admits are rare, a sample
finds too few to read"* — and predicted it correctly. **The bar comparison is
not decided by this run and should not be reported as decided.**

## The finding that does not need statistics

The spec's hypothesis rests on three pieces of evidence. Its second is a named
case:

> **DQ-006's named false negative is explained exactly.** A DOE reactor-
> criticality announcement was rejected for lacking "independent replication"
> when its own headline says it is the fourth criticality.

That item is `Department of Energy Celebrates Fourth Criticality Ahead of July
4th Goal`. Here is the production verdict on it, prompt v4, `qwen3.5:397b`:

> *"While the event is attested, a zero-power criticality demonstration is a
> standard regulatory milestone for fission reactors rather than a breakthrough
> of a long-theorized capability … and thus fails the specific bar for
> physical-realization evidence."* — `relevant: false`

And here is Bar A in this run — **the same prompt v4, unmodified** — under
`kimi-k3`:

> *"Tested against physical-realization: an attested DOE program milestone
> reporting the fourth reactor criticality demonstration, which is cumulative
> real-world evidence…"* — **admitted**

**The bar did not change. The model did.** The exemplar the hypothesis was built
on no longer reproduces, and it flipped on a model swap.

That sits badly with the premise the spec opens with — *"Two flagship tiers, four
model families, one answer. The filter is not model-limited."* Four models agreed
at 0% in the 2026-07-31 eval. `kimi-k3` was promoted in #231, **after** that eval,
and was never in it.

This does not overturn KI-009. One item is one item, and the 2026-07-31 corpus
was different. What it does is put a crack in the specific evidence the
reformulation hypothesis was resting on, which is worth knowing before spending
eleven weeks of quota testing that hypothesis.

## What each bar admitted

Reading these is the deliverable. An admit rate is not a score.

**`A-incumbent`** — doe-newsroom 1, federal-register 1

- *Department of Energy Celebrates Fourth Criticality Ahead of July 4th Goal* →
  `physical-realization`
- *Licensing Requirements for Microreactors and Other Reactors With Comparable
  Risk Profiles* → `cost-curve` (an NRC proposed rule aimed at high-volume
  microreactor deployment)

**`B-evidence-contribution`** — arxiv 1, federal-register 1

- *Generative AI floods and dilutes the market for books* → `platform-shift`
- the same NRC microreactor licensing rule → `cost-curve`

**`C-signal-tagging`** — arxiv 1, hard-leg **zero**

- *Co-Evolving LLM Evaluators and Policies via DynamicRubric* →
  `compute-substrate:3`, on the strength of a deployment serving "tens of
  millions of requests per day"

The two control items — the only items any model has ever admitted, both under
v3, both by a model since replaced — behaved inconsistently: `A` rejected both,
`B` admitted one, `C` admitted the other. Consistent with noise, and a reason not
to read the table as a ranking.

## Why the full run has not happened

**Measured, not estimated.** Ollama Cloud weekly allowance, read immediately
before and after the pilot:

```
before   weekly 0.100    kimi-k3   294 requests
after    weekly 0.202    kimi-k3   894 requests
         600 completions = 10.2% of the weekly allowance
```

The corpus has grown from the spec's **~2,600 items to 21,231** — `sec-edgar`
alone is 15,318. Three bars over all of it is **63,693 completions**, which at
the measured rate is **~10.6 weekly allowances**. Roughly eleven weeks, or a
paid tier change.

The spec's own estimate — *"~7,800 requests … roughly 3% of a week"* — was
computed in July against a corpus eight times smaller, and against a cheaper
request mix. It is not wrong so much as out of date, and it should not be quoted
as authority for what this now costs.

**The spec forbids sampling**, and for a good reason that this pilot then
demonstrated. So the full-corpus discipline and the quota are in direct conflict,
and resolving it is a ruling rather than an implementation detail. The options,
with honest costs:

| option | completions | cost | what it buys |
|---|---|---|---|
| full corpus, 3 bars | 63,693 | ~10.6 weeks | what the spec asks for |
| hard legs only, 3 bars | 47,025 | ~7.8 weeks | drops the arXiv control |
| `sec-edgar` capped at 2,000, all other legs whole | ~7,700 | ~1.3 weeks | every small hard leg whole; EDGAR sampled |
| B and C only, reusing this run's A | 42,462 | ~7.0 weeks | confounds model and corpus, as #247 did |

## Open — Mike's ruling

Nothing here is a ruling. Two questions are now in front of one:

1. **How much of the corpus.** Above.
2. **Whether the hypothesis still deserves the run at all**, given that its named
   exemplar flipped on a model change rather than a bar change. A cheaper
   experiment — re-score the 2026-07-31 corpus under prompt v4 with `kimi-k3`
   alone, one bar, no reformulation — would test "is the incumbent bar actually
   admitting hard-source items now?" for about a fifth of the cost, and would
   answer whether the reformulation is needed before paying to compare three of
   them.

**Ruling:** _(unrecorded)_
