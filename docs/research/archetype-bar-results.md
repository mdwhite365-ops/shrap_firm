# Archetype bar experiment — results

> ## Correction, 2026-09-20 — all three bars already ran in July
>
> **The first version of this page said "bars B and C have never been run." That
> was wrong**, and the mistake is worth more than the claim was.
>
> **Second correction, same page:** the first version of this correction blamed
> the wrong thing. It said `bar_experiment_runs` held one row while
> `bar_experiment_results` held four, and drew a lesson about reading a summary
> table instead of a detail table. Both tables were right. `bar_experiment_runs`
> has always held **four** rows for 2026-07-31, one per invocation, and the
> `bars` column of each names the bar it ran.
>
> The actual query was:
>
> ```
> psql -c "select * from research.bar_experiment_runs order by 1 desc limit 5" | head -14
> ```
>
> `report_markdown` is a multi-line `TEXT` column holding the whole run report.
> In psql's aligned output one row therefore spans **dozens** of lines, so
> `head -14` cut the result off inside the first row. Four rows came back; I was
> shown one. **I read my own truncation as a finding.**
>
> Two things follow, and the second is the useful one:
>
> - `SELECT *` on a table with a wide text column is not a listing. Select the
>   columns the question needs — here `run_id, bars, started_at` would have
>   printed four clean lines — or use `\pset expanded`, or `count(*)`.
> - **A pipe is part of the query.** `head`, `tail` and `| head -n` truncate
>   without saying so, and the truncation looks exactly like a short result. When
>   a count is the finding, ask the database for the count.
>
> This is a near relative of the `filter_verdict_history` error earlier the same
> day — both were confident claims about what the firm had recorded, made from
> evidence that did not support them — but the mechanism is different and the
> earlier diagnosis was a guess dressed as a root cause.
>
> The four runs, with the hard-leg column KI-009 actually needs (recomputed
> against the database on 2026-09-20, not carried over from the earlier text):
>
> | run | bar | scored | admits | hard scored | hard admits |
> |---|---|---|---|---|---|
> | `01KYX0BSTM8F…` | `A-incumbent` | 600 | 0 | **0** | 0 |
> | `01KYX4DDC3JK…` | `A-incumbent` | 599 | 2 | 454 | **2** |
> | `01KYX0XJJ97Q…` | `B-evidence-contribution` | 600 | 2 | 453 | **2** |
> | `01KYX259F67E…` | `C-signal-tagging` | 599 | 1 | 454 | **1** |
>
> The first row is the arXiv-only run its own report flagged as scoring no
> hard-leg items; it is not a contradictory `A` result, it is a different corpus.
> The other three share an item set — A∩B = 598, A∩C = 599 of ~600 — so they are
> a genuine three-bar comparison on one corpus with one model (`qwen3.5:397b`).
>
> **Which means step 3 of the spec largely happened in July and was never written
> up**, and the pilot below re-measured on 200 items what 599 items already said.
>
> ### What the July data says
>
> Every admitted item, across all three bars, is one of **two USASpending DOE
> awards** — Anduril, and American Centrifuge Operating:
>
> ```
> A-incumbent   usaspending  ANDURIL INDUSTRIES        physical-realization
> A-incumbent   usaspending  AMERICAN CENTRIFUGE OPS   cost-curve
> B-evidence    usaspending  ANDURIL INDUSTRIES        physical-realization
> B-evidence    usaspending  AMERICAN CENTRIFUGE OPS   cost-curve
> C-signal      usaspending  AMERICAN CENTRIFUGE OPS   bio-mechanism:1
> ```
>
> `B` admits exactly what `A` admits. `C` admits one of them, and labels it
> `bio-mechanism` — a uranium enrichment contract tagged as a biology signal,
> which is not a promising sign for signal-level tagging.
>
> **The hypothesis predicts B and especially C should admit substantially more
> than A. They do not.** On 454 hard-leg items the counts are 2, 2 and 1. That is
> the outcome the spec names as falsifying: *"If Bar A wins … the hypothesis is
> falsified, the bars are not misapplied, and the constraint is upstream in what
> we ingest rather than in how we read it."*
>
> It does not *quite* say all three admit nothing — they admit one or two — but
> the reformulations plainly do not unblock the hard leg, which is the question
> the card was built to answer.
>
> ### What is still open
>
> Only the **model** question, and that is the one the 2026-09-20 pilot raised:
> DQ-006's named exemplar flips between `qwen3.5:397b` and `kimi-k3` on the
> unmodified prompt v4. A replay of the July item set under `kimi-k3` was started
> and **stopped at 192 of 599 on Ollama's per-session request cap**. That run is
> `01M2YHEGZ5KSBAADGHYK96QGAY`: 599 result rows, of which **407 carry an error**
> and every one of the 407 is the same HTTP 429, *"you have reached your session
> usage limit."* Not a partial write — a complete run in which two thirds of the
> calls were refused.
>
> `https://ollama.com/api/usage`, read with the firm's own key, reports
> `limits.session.usage = 1.0` (974 `kimi-k3` requests) against
> `limits.weekly.usage = 0.277`. **The session window is the binding one and the
> weekly figure this project has been budgeting against is the wrong number.**
> The payload does not say how long the session window is, and it had not reset
> an hour after the run stopped.
>
> **The expensive full-corpus three-bar run is probably not worth funding.** The
> three-bar comparison exists. What does not exist is the same comparison under
> the current model, and that is one bar over 599 items — about 10% of a weekly
> allowance, not 10.6 of them.

> ## Retraction, 2026-09-20 — the section below drew the wrong conclusion
>
> **Everything here about `sec-edgar` is void.** The experiment's corpus query
> never selected `document_text`, so for 72% of the corpus the model was shown
> the Atom index entry — a filed date, an accession number and a file size —
> rather than the filing (#266). EDGAR's 0 of 425 measures how often a model
> calls an accession number a technology signal.
>
> Re-scored with the filings in the prompt, **EDGAR admits 6 of 425 — 1.41%**
> (Fisher exact vs 0/425, two-sided **p = 0.031**), five of six `compute-substrate`:
> AEP securing 13 GW of gas-fired capacity, a 20-year 2.67 GW Chevron PPA,
> Corning's hyperscaler capex redirection, Intel's Data Center and AI revenue.
>
> So "the volume is in the sources that admit nothing" is **backwards**, and
> "feed the funnel cannot mean more throughput" is withdrawn. See
> `docs/status/changes/266-retracting-the-corpus-verdict.md`.
>
> What survives: the model comparison (3 vs 2, indistinguishable), DQ-006's
> exemplar flipping, and the cost figures. The July three-bar comparison is void
> for the same reason.

## The `kimi-k3` replay finished, 2026-09-20 — and the model was never the question

Run `01M2YHEGZ5KSBAADGHYK96QGAY`: **Bar A, the unmodified production prompt v4,
over the exact 599 items July's control scored**, so the model is the only
variable. 599 scored, **0 errors, 0 parse failures**.

| model | hard scored | hard admits |
|---|---|---|
| `qwen3.5:397b` (2026-07-31) | 454 | **2** |
| `kimi-k3` (2026-09-20) | 454 | **3** |

**Three is not better than two.** On 454 items at a rate near 0.5% the standard
error is about ±1.5 admits, so the two runs are indistinguishable and nothing
here ranks the models. Reading that gap as an improvement would be the error
KI-036 exists to refuse.

**One targeted result does survive, because it is not a rate.** DQ-006 names a
specific false negative — the DOE fourth-criticality announcement. `kimi-k3`
**admits it**; `qwen3.5:397b` rejected it on the identical prompt. That is the
exemplar the spec asked about, tested directly rather than inferred from a
count, and it confirms what the 200-item pilot saw.

The two models agree on 596 of 599 items and disagree on three:

| source | `qwen3.5` | `kimi-k3` | item |
|---|---|---|---|
| `doe-newsroom` | reject | **admit** | DOE Celebrates Fourth Criticality (DQ-006's exemplar) |
| `federal-register` | reject | **admit** | Licensing Requirements for Microreactors |
| `usaspending` | **admit** | reject | DOE award to ANDURIL INDUSTRIES ($5.7M) |

Four distinct items are admitted by either model and **only one by both**. At
these counts the overlap carries no information; it is recorded so nobody later
mistakes two runs for a reproducibility check.

### The finding that is not about models at all

Break `kimi-k3`'s 599 items down by where they came from:

| source | scored | admits | share of full corpus |
|---|---|---|---|
| `sec-edgar` | 425 | **0** | 15,318 / 21,231 — **72%** |
| `arxiv` | 145 | **0** | 5,556 — 26% |
| `usaspending` | 14 | 1 | 129 — 0.6% |
| `federal-register` | 13 | 1 | 185 — 0.9% |
| `doe-newsroom` | 2 | 1 | 43 — **0.2%** |

**570 of 599 items — 95% — came from two sources that admitted nothing, under
either model.** Every admit in the entire experiment, across all three bars and
both models, came from the three sources that together are **1.7% of what the
firm ingests**.

EDGAR's 0 of 425 puts its admit rate below **0.7%** at 95% confidence (rule of
three). It is 72% of the corpus and ~1,000 items a week of continuous ingest.
`doe-newsroom` is 43 items in total and produced the one admit the spec
specifically asked for.

**So the volume is in the sources that admit nothing, and the signal is in the
sources with almost no volume.** That reframes what "feed the funnel" means: not
more throughput, which is overwhelmingly EDGAR, but more sources shaped like
`doe-newsroom` — or a different extraction from EDGAR, since what is stored today
is filing text that this taxonomy demonstrably does not match.

It also settles the cost question the spec left open. A full-corpus run is
**63,693 completions** to score 20,874 items from two sources with a measured
admit rate indistinguishable from zero. The single-bar replay that produced
everything above cost **407 completions, about 6.8% of a weekly allowance**,
measured on the meter rather than estimated.

### What is now closed, and what is not

**Closed:** the model question. Two model families, one bar, one item set, no
distinguishable difference. Combined with the July three-bar comparison, the
experiment has tested both of its variables and neither moves the hard leg.

**Open, and it is Mike's:** whether KI-009's fix is a taxonomy change, a source
change, or both. The data now points at *source* more strongly than the spec
anticipated — the spec's falsifying clause said the constraint would be
"upstream in what we ingest rather than in how we read it", and that is what the
by-source table says.

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
