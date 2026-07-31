# Archetype bar experiment — card spec

**Version:** 0.1 (draft)
**Date:** 2026-07-31
**Owner:** Mike White (the ruling); agents (the harness)
**Serves:** KI-009, DQ-006, ADR-0007.
**Status:** Proposed — spec only. No code in this card.

## What this card is for

KI-009 says the research funnel is structurally incapable of promoting
anything. Its recorded fix order put **filter prompt v4, source-class aware**
first, with "nothing else matters until hard-source items can pass." Prompt v4
shipped. Hard-source items still do not pass.

The 2026-07-31 shadow eval settled what is *not* causing it. Four models that
parsed cleanly — including a 1.6T flagship and a 397B — scored the same
corpus on prompt v4:

| model | says relevant |
|---|---|
| `gpt-oss:20b-cloud` | 0% |
| `qwen3.5:397b` | 0% |
| `glm-5.2` | 0% |
| `deepseek-v4-pro:cloud` | 5% (one item, and it was wrong — see below) |

Two flagship tiers, four model families, one answer. **The filter is not model-
limited.** No purchase fixes it, and the remaining explanation is the taxonomy
itself — which is a Mike-owned decision, not an implementation detail.

This card does not propose a taxonomy. It proposes the experiment that lets
Mike rule on one with evidence instead of argument.

## The hypothesis, stated so it can be falsified

**The archetype bars are aggregate-level predicates being evaluated against
item-level evidence.**

Read the signature signals as written in `world-changer-archetypes.md` and
mirrored in `archetypes.py`:

| archetype | a signal | what satisfying it requires |
|---|---|---|
| `cost-curve` | "unit cost declining on a learning-curve slope consistent across producers" | many producers, many periods |
| `bio-mechanism` | "mechanism validated in multiple independent Phase 3 trials, not one" | multiple trials, explicitly |
| `compute-substrate` | "sustained multi-quarter hyperscaler capex redirection" | multiple quarters |
| `platform-shift` | "a new primary interaction surface reaching generational DAU scale in ~24 months" | 24 months of adoption data |
| `physical-realization` | "independent replication by a different group with different apparatus" | at least two groups |

Every one of these describes a **trend across documents**. No single 10-Q, 8-K,
Federal Register notice or arXiv preprint can satisfy any of them, because each
document is one observation and each bar is a statement about a series.

So the filter asks each item *"do you demonstrate a world-changer transition?"*
and the honest answer is always no. **The rejections are correct. The question
is wrong.**

Three pieces of evidence fit this reading and no other one we have:

1. **The models agree with each other and with the bars.** `gpt-oss` rejected a
   Calix 10-Q with "the filing summary provides no evidence of any archetype's
   signature signals." That is accurate. A 10-Q cover page is not a learning
   curve.
2. **DQ-006's named false negative is explained exactly.** A DOE reactor-
   criticality announcement was rejected for lacking "independent replication"
   when its own headline says it is the fourth criticality. The item *is* a
   replication event; it cannot simultaneously be the survey establishing that
   replication occurred independently with different apparatus. The bar asked
   it to be both.
3. **The one false positive is the signature of an unanswerable question.**
   `deepseek-v4-pro` admitted that same Calix 10-Q as `platform-shift` on the
   rationale *"the filing's existence meets the attested bar for real-world
   adoption data."* That is reasoning from a document's existence rather than
   its content — what a model produces when forced to answer an aggregate
   question from a single item.

## What this card explicitly does not propose

**Loosening the bars.** The evidentiary bars encode real discrimination — the
impostor lists (hydrogen-shaped curves, LK-99-shaped claims, metaverse-shaped
platform bets) are the accumulated reason this taxonomy exists. Lowering them
against a corpus of 1,900+ EDGAR items admits junk at scale and produces a
funnel that promotes noise, which is worse than one that promotes nothing.
Vision principle 2 is *kill more aggressively than you promote*.

The defect under test is **where the bars are applied**, not how high they are.

## The three bars to compare

Run against the same corpus, same model, same prompt scaffolding. Only the
question changes.

### Bar A — incumbent (control)

Prompt v4 exactly as deployed. Establishes the floor and proves the harness
reproduces the shadow eval's 0%.

### Bar B — evidence contribution

Same archetypes, same signals, same impostor lists. The question becomes:
*does this item carry a specific fact that would count as evidence toward one
of these signals?* — not *does it demonstrate the transition.* The item-level
judgment stays evidentiary; the aggregate judgment is deferred to clustering.

### Bar C — signal-level tagging

Drop the archetype-level yes/no entirely. Ask which **individual signal**, from
the flat list across all archetypes, the item speaks to — and what fact it
carries. Archetype promotion becomes an aggregation over tagged items at the
clustering step rather than a per-item verdict.

Bar C is the largest change and the most likely to be right if the hypothesis
holds. It is also the one that moves real work downstream, which is why it is
tested rather than assumed.

## What gets measured

Per bar, over the corpus:

- **admit rate**, overall and **by source** — the number KI-009 actually needs
  is whether hard-source items (`sec-edgar`, `usaspending`, `federal-register`,
  `doe-newsroom`) can pass at all, since arXiv-only clusters fail triangulation
  on both conditions simultaneously.
- **every admitted item, listed** — title, source, archetype-or-signal, and the
  model's stated reason. Not a count. A bar that admits 40 items is only better
  than one that admits 2 if the 40 are not junk, and that is a judgement made
  by reading them.
- **agreement between bars** on the items all three see, to localize where the
  reformulation changes the reading rather than just the volume.

An admit rate is not a success metric. A bar that admits everything scores
best on volume and is worthless. The deliverable Mike rules on is the
**admitted-item list**, per bar.

## Corpus and cost

Run the **full corpus, not a sample.** Every eval to date has carried the same
caveat — two incumbent-relevant items in a twenty-item sample, so the agreement
column is a statement about rejections. Sampling here would reproduce that
exact failure: if admits are rare, a sample finds too few to read.

| leg | items | role |
|---|---|---|
| `sec-edgar` | ~1,656 | hard leg — the one KI-009 needs unblocked |
| `arxiv` | ~700 | known survivor — control |
| `usaspending` | ~117 | hard leg |
| `federal-register` | ~113 | hard leg |
| `doe-newsroom` | ~16 | hard leg, carries DQ-006's false negative |

~2,600 items × 3 bars ≈ **7,800 requests**, on `qwen3.5:397b`.

That is affordable and the measurement says so rather than the estimate. In the
week of 2026-07-31 the box spent 3,320 requests — 2,941 of them the production
filter — for **1.2% of the weekly Ollama Pro allowance**. This experiment is
roughly 3% of a week. Cap contention is not a constraint at this volume, and
the earlier caution that it might be was wrong.

## Non-production discipline

Same commitments as the shadow eval, for the same reason:

- Results land in their own tables. The harness **never** writes
  `filter_result`, never marks `filtered_at`, and never appends to
  `research.filter_verdict_history`. An experiment that fed its own candidate
  verdicts back into the corpus would contaminate every later measurement
  invisibly.
- Bars B and C are **new prompts**, not edits to the deployed one. Prompt v4
  stays exactly as it is until a ruling changes it.
- A test pins the isolation, as `test_model_eval.py` does for the shadow eval.

## The decision this feeds

Mike rules on which bar the taxonomy adopts. That ruling is decision-carrying
in the strong sense — **merging it accepts a rewrite of the filter's question**,
and for Bar C a corresponding change to the clustering step.

Per vision principle 7, the artifact that changes first is
`docs/research/world-changer-archetypes.md` — the doc is the decision record and
`archetypes.py` is its mirror. The prompt follows the doc, not the other way
round.

If **Bar A wins** — all three admit nothing — the hypothesis is falsified, the
bars are not misapplied, and the constraint is upstream in what we ingest
rather than in how we read it. That outcome is recorded in
`calibration.md` §(a) the same as any other, and it redirects the work to
sources rather than taxonomy.

## Sequencing

| step | artifact |
|---|---|
| 1 | This spec, merged (accepting the experiment design) |
| 2 | Harness card — bar variants + persistence + isolation test |
| 3 | Run over the full corpus; admitted-item lists per bar |
| 4 | Mike's ruling; `world-changer-archetypes.md` updated first, then the mirror and the prompt |
| 5 | Re-filter the corpus under the ruled bar (KI-009 fix order step 3, still valid) |

Step 5 is why KI-007's verdict history exists: a re-filter is auditable, and
the cross-prompt-version comparison stays queryable after the fact.
