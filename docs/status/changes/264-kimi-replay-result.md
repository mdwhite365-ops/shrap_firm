### The archetype bar replay finished, and the corpus is the constraint (#264)

Run `01M2YHEGZ5KSBAADGHYK96QGAY` completed on 2026-09-20: Bar A — the unmodified
production prompt v4 — over the **exact 599 items** July's control scored, so the
model was the only variable. 599 scored, **0 errors, 0 parse failures**, resumed
into the original run id by #261's `--resume-run`.

| model | hard scored | hard admits |
|---|---|---|
| `qwen3.5:397b` (2026-07-31) | 454 | **2** |
| `kimi-k3` (2026-09-20) | 454 | **3** |

**Three is not better than two.** At a rate near 0.5% on 454 items the standard
error is about ±1.5 admits. The runs are indistinguishable and nothing here ranks
the models — reading that gap as an improvement is the error KI-036 exists to
refuse.

**One targeted result survives because it is not a rate.** DQ-006 names a specific
false negative, the DOE fourth-criticality announcement. `kimi-k3` admits it;
`qwen3.5:397b` rejected it on the identical prompt. The models agree on 596 of
599 items; of the four items either admits, **only one is admitted by both**.

**The finding is not about models.** By source:

| source | scored | admits | share of corpus |
|---|---|---|---|
| `sec-edgar` | 425 | **0** | **72%** |
| `arxiv` | 145 | **0** | 26% |
| `usaspending` | 14 | 1 | 0.6% |
| `federal-register` | 13 | 1 | 0.9% |
| `doe-newsroom` | 2 | 1 | **0.2%** |

**95% of the items came from two sources that admitted nothing under either
model.** Every admit in the whole experiment — three bars, two models — came from
the three sources that are together **1.7% of what the firm ingests**. EDGAR's
0 of 425 puts its admit rate below **0.7%** at 95% confidence, against ~1,000
items a week of continuous ingest.

**The volume is in the sources that admit nothing; the signal is in the sources
with almost no volume.** That reframes "feed the funnel": not more throughput,
which is overwhelmingly EDGAR, but more sources shaped like `doe-newsroom`, or a
different extraction from EDGAR than the filing text stored today.

It also settles the cost question. The full-corpus run is 63,693 completions to
score 20,874 items from two sources whose admit rate is indistinguishable from
zero. This replay cost **407 completions — 6.8% of a weekly allowance**, measured
on `ollama.com/api/usage` rather than estimated.

**Closed:** the model question. Both variables the experiment was built to test
have now been tested and neither moves the hard leg. **Open, and Mike's:** whether
KI-009's fix is a taxonomy change, a source change, or both — the data points at
*source* more strongly than the spec anticipated.
