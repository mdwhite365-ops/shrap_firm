### The archetype bar experiment has an answer, and it is Bar B (#270)

Three bars, 425 `sec-edgar` filings, `kimi-k3`, every bar scoring the identical
item set:

| bar | admits | rate |
|---|---|---|
| `A-incumbent` | 6 | 1.41% |
| **`B-evidence-contribution`** | **14** | **3.29%** |
| `C-signal-tagging` | 3 | 0.71% |

**The rates are not the finding — the nesting is.** B admits everything A admits
plus eight more, and **not one item goes the other way** (McNemar p = 0.008).
Against C the same shape, 11-vs-0 (p = 0.001). B is strictly more permissive, not
differently permissive, so this is not two bars trading errors.

**Bar C is worse than the unmodified production prompt** — half A's admits, and
nothing A does not already have. C was the most elaborate of the three and the
one the spec leaned on hardest.

What B catches that A misses is a coherent cluster rather than marginal calls:
Alliant Energy, EMCOR, MasTec, SPX and 3M under `compute-substrate`, Air Products
and Ford under `cost-curve`. A catches the headline names — Intel, Chevron,
American Electric Power; **B also catches the contractors who physically build
the data centres and the suppliers feeding them.** That is the earlier-stage
evidence the spec argued a contribution-shaped question would surface.

**B's advantage is concentrated in EDGAR, and that is measured, not cautioned
about.** All three bars now cover the full 599-item set: A 9, **B 17**, C 5.
B vs A over the whole corpus is 9-vs-1, **p = 0.022** — still significant, but
weaker than EDGAR alone and **no longer perfectly nested.**

On the 174 non-EDGAR items the two bars are indistinguishable: **3 admits each,
one item each way.** Every one of B's net gains comes from filings. And the
single item B *loses* is the DOE fourth-criticality announcement — **DQ-006's
named exemplar**, the specific false negative this line of work exists to fix.

Two explanations fit and the data does not separate them: 29 non-EDGAR hard-leg
items is far too few to detect an 8-in-425 effect, or a contribution-shaped
question needs long attested prose and is better on filings rather than better in
general. **The EDGAR result stands on its own; it is not evidence about the bar
everywhere.**

**The other limit: fourteen admits is a small base**, so the direction is
supported and the magnitude is not — anyone quoting 3.29% is quoting a number
with real width.

**C's verdict is unchanged** and now covers everything: 5 admits against A's 9,
with **zero** items A does not already have, on any source.

Evidence assembled in `docs/research/archetype-bar-ruling.md`. **The ruling is
Mike's** — adopting B means the production filter's question changes from *what
does this prove* to *what does this contribute*.

**The July run that appeared to falsify this is void**, along with every
archetype bar number before 2026-09-20: the corpus query never selected
`document_text`, so EDGAR was scored on its index entries (#266).
