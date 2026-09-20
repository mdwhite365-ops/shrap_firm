### Retraction: #264's verdict on the corpus was an artifact of my own query (#267)

**#264 said the corpus was the constraint. That conclusion is withdrawn.**

What it claimed, and what is wrong with each part:

> *"95% of the items came from two sources that admitted nothing, under either
> model."*

True as arithmetic, meaningless as evidence. `sec-edgar` admitted nothing because
**it was never read**. The experiment's corpus query never selected
`document_text`, so for 72% of the corpus the model was shown the Atom index
entry — a filed date, an accession number and a file size — and asked whether it
was a world-changing technology signal (#266).

> *"EDGAR's 0 of 425 bounds its admit rate below 0.7% at 95% confidence."*

The bound is arithmetically correct and describes nothing about EDGAR. It
measures how often a model calls an accession number a technology signal.

> *"The volume is in the sources that admit nothing, and the signal is in the
> sources with almost no volume."*

**Backwards.** Re-scored with the filings actually in the prompt, EDGAR admits
**6 of 425 — 1.41%** (Fisher exact vs 0/425, two-sided **p = 0.031**). That is
*higher* than the ~10% rate #264 credited to the three small sources, which rests
on a 29-item base, and EDGAR has ~1,000 items a week behind it.

> *"'Feed the funnel' cannot mean more throughput."*

Withdrawn entirely. Throughput is exactly where the unexamined signal was.

### What EDGAR admitted once it was read

All four `compute-substrate`, and all four the kind of thing the taxonomy was
written for:

- **American Electric Power** — a utility securing **13 GW** of gas-fired capacity
- **Chevron** — a 20-year, **2.67 GW** behind-the-meter power purchase agreement
- **Corning** — sustained hyperscaler/AI-factory capex redirection
- **Intel** — Data Center and AI segment revenue

### What survives from #264

Only the parts that never depended on EDGAR:

- `kimi-k3` 3 hard admits vs `qwen3.5:397b` 2, on the shared 599-item set — still
  indistinguishable at ±1.5 SE, and still not a ranking of the models.
- DQ-006's named false negative, the DOE fourth-criticality announcement, is
  admitted by `kimi-k3` and was rejected by `qwen3.5:397b` on the identical
  prompt. A targeted result, not a rate, and unaffected by the EDGAR defect
  because `doe-newsroom` carries no `document_text`.
- The cost figures, which were measured on the meter.

**The July three-bar comparison is also void** — same CLI, same missing column —
so the claim in #260 that all three bars had already been run stands as a fact
about the database and falls as a measurement.

### The lesson, which is not the one #264 drew

#264 reasoned from a pattern in aggregate counts without asking what the model
had been shown for the rows producing them. The per-source table was real; the
inference was not. **Before concluding that a source has nothing to say, confirm
that something asked it.**

It is also the third variant this week of the same shape — a component
reconstructing a fact the system already held, and the reconstruction
disagreeing. Here the experiment's corpus query reconstructed "the item" and
disagreed with what production had been reading all along.
