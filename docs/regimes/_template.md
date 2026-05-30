# [Regime Name]

**Regime ID:** [kebab-case-id]
**Document version:** 0.1 (draft)
**Date:** [YYYY-MM-DD]
**Owner:** Mike White
**Status:** Draft

## Identification

[One paragraph. What is this regime, in one breath? Name the canonical historical episode it
most resembles. State the single-sentence summary the Regime Classifier will use to decide
whether to apply this label.]

## Statistical Markers

[Expected ranges under this regime. Ranges are honest bands, not point estimates. If a marker
does not apply or is not diagnostic for this regime, say so explicitly.]

| Marker | Expected range | Notes |
|---|---|---|
| Realized vol (SPX, 20d) | [range] | [notes] |
| Implied vol (VIX) | [range] | [notes] |
| VIX term structure | [contango / backwardation / flat] | [notes] |
| Trend strength (SPX ADX 14) | [range] | [notes] |
| Breadth (% above 200dma) | [range] | [notes] |
| Sector dispersion | [low / moderate / high] | [notes] |
| Credit spreads (IG, HY) | [range / direction] | [notes] |
| Dollar trend (DXY) | [direction / range] | [notes] |
| Rate vol (MOVE) | [range] | [notes] |

## Historical Examples

[Named past episodes with dates. At least two. For each, a sentence or two on what made it
canonical for this regime and what was happening macro-wise.]

- **[Episode name] ([YYYY-MM] – [YYYY-MM]):** [description]
- **[Episode name] ([YYYY-MM] – [YYYY-MM]):** [description]

## Macro Backdrop

[Qualitative description of the macro environment characteristic of this regime. Rate path,
fiscal stance, geopolitical context, dominant market narrative. This is the section the LLM
analog layer reads most heavily.]

## What Works

[Strategy archetypes that have historically performed well in this regime. Archetypes, not
specific strategies. Include the why — what feature of the regime makes the archetype
work.]

- **[Archetype]:** [why it works in this regime]
- **[Archetype]:** [why it works in this regime]

## What Fails

[Strategy archetypes that have historically performed poorly. Specific failure modes if
known.]

- **[Archetype]:** [why it fails]
- **[Archetype]:** [why it fails]

## Duration Expectations

[Typical regime length with honest variance. Cite the historical examples used above.
Acknowledge that regime duration is one of the hardest things to estimate.]

## Detection Signals

[What tells the classifier this regime is present. Map onto the statistical markers where
possible; include qualitative signals as well. Order from most diagnostic to least.]

1. [signal]
2. [signal]
3. [signal]

## Exit Triggers

[What tells the classifier the regime is ending. These are usually NOT the inverse of the
detection signals — regimes typically end with a specific structural break that did not
appear at the start.]

1. [trigger]
2. [trigger]
3. [trigger]

## Confidence Notes

[Honest disclosure of where this profile is well-grounded versus speculative.
- Which historical analogs are well-documented?
- Which statistical markers are best-guess?
- Where is the author uncertain?
- What evidence would meaningfully update this profile?]

## Open Questions

[Optional. Remove if none. Items that block tighter calibration of this regime and who
resolves them.]

- **[Question]:** [description]. Blocks: [what]. Owner: [Mike | agent X | pending].
