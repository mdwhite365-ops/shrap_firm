### The experiment never read the filings (#266)

**`SELECT_CORPUS_SQL` selected `item_id, source, kind, title, summary` and
stopped.** For `sec-edgar` — 72% of the corpus — `summary` is the Atom index
entry. The filing is in `document_text`, which production's query has always
selected and this one never did.

This is what the model was shown for 425 items:

```
Title:   10-Q - Air Products & Chemicals, Inc. (0000002969) (Filer)
Summary: Filed: 2026-07-30  AccNo: 0000002969-26-000036  Size: 14 MB
```

Bar A uses production's own prompt builder, which reads the body when it is
there, so the missing column silently downgraded it to the summary branch: the
"unmodified production prompt v4" control was **not** running what production
runs. Bars B and C never read `document_text` at all.

**This is KI-026 (#189) reproduced inside the experiment built to evaluate the
filter KI-026 was found in.**

### What it cost

Re-running the same EDGAR items with the filings actually in the prompt:

| EDGAR scored on… | items | admits | rate |
|---|---|---|---|
| index entries | 425 | **0** | 0% |
| **the filings** | 250 | **4** | **1.6%** |

**Fisher exact, two-sided: p = 0.019.** Every admit is `compute-substrate`, and
they are exactly what the taxonomy exists to catch:

- **American Electric Power** — a utility securing **13 GW** of gas-fired capacity
- **Chevron** — a 20-year, **2.67 GW** behind-the-meter power purchase agreement
- **Corning** — sustained hyperscaler/AI-factory capex redirection
- **Intel** — Data Center and AI segment revenue

They were in the corpus the whole time.

### What it invalidates

**#264's conclusion is retracted** — see `267-retracting-the-corpus-verdict.md`.
EDGAR is not a dead leg; it was never read. Also void: the July three-bar
comparison, which used the same query, and every hard-leg number computed from
these runs.

### The fix

The query selects and passes through `document_text`, and all three bars build
their evidence block body-first through a shared `_item_content`, labelled
`Document:` rather than `Summary:` — a model told "Summary:" ahead of six
thousand characters of filing text is being told something false about what it
is reading.

`--sources` re-runs only the feed a change affects: the four sources carrying no
`document_text` build byte-identical prompts, so re-scoring them would spend a
third of the budget reproducing numbers already held. An unknown source name
raises rather than scoring nothing.

One warning was reordered: the missing-items check ran *after* the source filter,
so every correct `--sources` run reported the other sources as "no longer in the
corpus" — 425 of 599 on first use. A warning that fires on correct usage is one
nobody reads.
