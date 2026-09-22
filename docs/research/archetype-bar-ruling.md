# Archetype bar experiment — the evidence for a ruling

**Status: decision pending. This page is the evidence, not the decision.**
Spec: `docs/research/archetype-bar-experiment.md`. Raw results:
`research.bar_experiment_results`.

The experiment asks whether the world-changer filter admits so little because of
*how it reads* items rather than *what it is given*. Three bars over one corpus:

- **A — incumbent.** The unmodified production prompt, v4. The control.
- **B — evidence contribution.** Asks what fact an item *contributes* rather
  than what it *proves*.
- **C — signal tagging.** Tags an item against a catalogue of signals rather
  than against the archetypes directly.

## The result

`kimi-k3`, 425 `sec-edgar` filings, every bar scoring the identical item set.
The full 599-item corpus is in limit 2 below, and it matters.

| bar | scored | admits | rate |
|---|---|---|---|
| `A-incumbent` | 425 | 6 | 1.41% |
| **`B-evidence-contribution`** | 425 | **14** | **3.29%** |
| `C-signal-tagging` | 425 | 3 | 0.71% |

Rates alone are not the finding — at these counts they are barely separable. The
finding is that **the disagreements are perfectly nested**, which is much
stronger evidence than the rates:

| comparison | admits only the first | admits only the second | McNemar exact |
|---|---|---|---|
| B vs A | **8** | **0** | **p = 0.008** |
| B vs C | **11** | **0** | **p = 0.001** |
| C vs A | 0 | 3 | p = 0.25 |

**B admits everything A admits, plus eight more. Not one item goes the other
way.** B is strictly more permissive here, not differently permissive, so this
is not two bars trading errors — it is one bar seeing a superset.

**C is worse than the unmodified production prompt.** It admits half of what A
admits and contributes nothing A does not already have. C was the most elaborate
of the three and the one the spec leaned on hardest.

## What B catches that A does not

| archetype | items |
|---|---|
| `compute-substrate` | Alliant Energy, EMCOR, MasTec, SPX Technologies, 3M |
| `cost-curve` | Air Products, Ford |
| `bio-mechanism` | West Pharmaceutical |

A coherent cluster rather than scattered marginal calls: **the supply chain
behind the AI build-out.** A catches the headline names — Intel, Chevron,
American Electric Power. B also catches the electrical and mechanical
contractors who physically build the data centres, and the industrial gas and
materials suppliers feeding them.

That is exactly the earlier-stage evidence the spec argued a contribution-shaped
question would surface, and it is the shape of evidence the firm wants: a
signal visible before it is priced.

## Two limits, stated plainly

**1. Fourteen admits is a small base.** A rate of 3.29% on 425 items carries a
standard error around ±0.9 percentage points. The *paired* comparison is much
better supported than the rate — 8-vs-0 discordant pairs is unlikely under a null
of no difference regardless of the base rate — but anyone quoting "3.29%" as the
admit rate B will deliver on the full corpus is quoting a number with real width
on it. **The direction is well supported. The magnitude is not.**

**2. B's advantage is concentrated in EDGAR, and this is now measured rather
than cautioned about.** All three bars have since been extended to the full
599-item set:

| bar | admits (599) |
|---|---|
| `A-incumbent` | 9 |
| **`B-evidence-contribution`** | **17** |
| `C-signal-tagging` | 5 |

**B vs A over the whole corpus: 9 B-only, 1 A-only, McNemar p = 0.022.** Still
significant, but weaker than EDGAR alone (p = 0.008) and **no longer perfectly
nested.** On the 174 non-EDGAR items the two bars are indistinguishable — 3
admits each, one item each way:

| source | items | A | B | B-only | A-only |
|---|---|---|---|---|---|
| `arxiv` | 145 | 0 | 1 | 1 | 0 |
| `doe-newsroom` | 2 | 1 | 0 | 0 | **1** |
| `federal-register` | 13 | 1 | 1 | 0 | 0 |
| `usaspending` | 14 | 1 | 1 | 0 | 0 |

**Every one of B's net gains comes from filings.** Worse, the single item B
loses is the DOE fourth-criticality announcement — **DQ-006's named exemplar**,
the specific false negative this whole line of work was started to fix. B's one
non-EDGAR gain is an arXiv paper on generative AI and the book market, tagged
`platform-shift`.

Two explanations fit and the data does not separate them. The non-EDGAR hard leg
is **29 items**, far too few to detect an 8-in-425 effect, so this is genuinely
underpowered and does not refute B. Or "what does this contribute" needs long
attested prose to have something to work with, and the bar is better on filings
rather than better in general.

**The EDGAR result stands on its own. It should not be read as evidence about
the bar everywhere.**

**The original framing of this limit —** measured on `sec-edgar` only — EDGAR was chosen because it is 72%
of the corpus and because it is the leg that #266 showed had never been read
properly. It is also a *particular kind* of text — attested corporate disclosure,
long, formal, written to be precise about what a company has committed to. That
caution turned out to be the finding above.

**C's verdict does not change.** Over all 599 items it admits 5 against A's 9,
with **0 items A does not already have** (p = 0.125). It contributes nothing
unique on any source.

**The first limit softens the magnitude; the second narrows the claim.** Both
belong in any summary, and the second is a measured negative rather than a
caveat — it was written as a caution and came back as a result.

## What this does not establish

An admitted item is a **candidate**, not a strategy. KI-035's binding constraint
is what survives the funnel, not what enters it, and nothing here measures that.
A bar that admits more could be finding signal or lowering a threshold; the
eight items above read as signal, but that is a judgement made by reading them,
which is what the ruling is for.

## History, and why the July result must be ignored

A three-bar comparison ran on 2026-07-31 and appeared to **falsify** the
hypothesis — all three bars admitting one or two items, B admitting exactly what
A admitted. That run is **void**, along with every archetype bar number recorded
before 2026-09-20.

The experiment's corpus query never selected `document_text`, so for EDGAR it
showed the model the Atom index entry — a filed date, an accession number and a
file size — instead of the filing (#266). Bars B and C never read the document
body at all. 425 of 454 hard-leg items were scored on metadata under two models
and three bars, and every one was rejected.

A verdict of "the reformulations do not help" was therefore collected from a
corpus nobody had actually read to the model. `docs/status/changes/` carries the
full account: #266 (the defect), `266-retracting-the-corpus-verdict.md` (what it
invalidated), #269 (a guard defect found the same way).

**Check what the model was shown before concluding anything about what it said.**

## The decision this feeds

Mike rules on whether the taxonomy adopts Bar B. Merging a ruling into
`docs/research/world-changer-archetypes.md` means accepting that the production
filter's question changes from *what does this prove* to *what does this
contribute* — and that the mirror and prompt follow.

**Ruling:** _(Mike — record the call here.)_
