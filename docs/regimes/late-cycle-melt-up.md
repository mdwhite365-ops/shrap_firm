# Late-Cycle Melt-Up

**Regime ID:** late-cycle-melt-up
**Document version:** 0.1 (draft)
**Date:** 2026-05-29
**Owner:** Mike White
**Status:** Draft

## Identification

A late-cycle melt-up is an environment where prices rise faster than fundamentals justify, driven by retail enthusiasm, leveraged positioning, narrative-driven sector concentration, and a persistent fear-of-missing-out bid. Realized volatility is suppressed; implied volatility is suppressed alongside it; breadth narrows as a handful of names carry the index. The regime is canonically late-cycle because it tends to occur after a long expansion, when capital is plentiful and risk appetite is high. The closing chapter typically arrives via a volatility shock, not a slow grind lower.

## Statistical Markers

| Marker | Expected range | Notes |
|---|---|---|
| Realized vol (SPX, 20d) | 8-14% | Lower than long-run average; complacency is structural |
| Implied vol (VIX) | 12-18 | Often persistently below realized's historical norm |
| VIX term structure | Steep contango | Front-month suppressed; back-month elevated |
| Trend strength (SPX ADX 14) | 25-40 | Strong, persistent uptrend |
| Breadth (% above 200dma) | Declining from 70%+ toward 50%; divergence from index | Narrowing leadership is diagnostic |
| Sector dispersion | High and rising | Winners win big, laggards stagnate |
| Credit spreads | Tight IG (<100bps), tight HY (<350bps) | Risk-on across capital structure |
| Dollar trend | Often weakening or sideways | Risk-on environment usually coincides |
| Rate vol (MOVE) | 80-110 | Subdued |

## Historical Examples

- **1999-Q1 to 2000-Q1 (Nasdaq blowoff):** Internet narrative, options speculation by retail, leadership concentrated in a handful of large-cap tech and a long tail of unprofitable dot-coms. Ended with the Nasdaq down ~37% peak-to-trough in months, then a multi-year bear.
- **2021-Q1 (SPAC / meme blowoff):** Fiscal-stimulus liquidity, zero-rate environment, retail brokerage account explosion, meme-stock coordination on social platforms, SPAC issuance frenzy. Ended unevenly — large-cap held while the speculative tail cratered through 2021-Q2 and beyond.
- **Partial 2024 to 2025 (AI-led concentration):** Narrative-driven concentration in a handful of AI-related mega-caps; persistent low realized vol punctuated by sharp single-name drawdowns. Whether this episode resolves as a melt-up blowoff or a more orderly digestion is unsettled as of this writing.

## Macro Backdrop

Late-cycle melt-ups characteristically occur in environments with: accommodative or recently-accommodative monetary policy, abundant liquidity, a dominant bullish narrative (productivity revolution, new economy, new technology), strong fiscal impulse or recently-strong fiscal impulse, low credit-spread environment, and rising retail participation (account openings, options volume, social-platform engagement). Earnings revisions are typically positive but plateauing; valuation multiples expand faster than earnings.

The defining qualitative feature is widespread belief that "this time is different" — a story that justifies paying historically high multiples on the basis of an alleged structural change. The story may be partially or wholly correct; that does not change the regime dynamics.

## What Works

- **Trend-following on the leadership names:** The narrowing-leadership feature means a small basket of winners carries the tape. Momentum strategies tuned to those names perform.
- **Long-vol-of-vol positioning at the margins:** Vol is suppressed but coiled; structures that profit from the eventual unwind have asymmetric payoff if sized for the tail.
- **Call-overwriting on extended single names:** Realized vol stays below implied; covered-call structures harvest a persistent premium.
- **Liquidity-sweep trap detection on retail-favored names:** Mike's existing strategy archetype is well-matched to this regime because retail flow is dense and predictable.
- **Index-relative dispersion trades:** With high sector dispersion, pair structures (long leader / short laggard within sector) often work.

## What Fails

- **Broad mean-reversion fades on index:** "This is too far, too fast" is true and irrelevant; the index keeps grinding higher and stops you out.
- **Short-vol carry at scale:** It works until the unwind, at which point a year of gains evaporates in days. The 2018-02 VIX event is the canonical warning.
- **Value-rotation bets timed too early:** The leadership rotation will eventually come, but trying to front-run it has been a multi-quarter pain trade in past episodes.
- **Naked-short on extended single names:** Squeeze dynamics are vicious when retail coordination meets dealer hedging flows.

## Duration Expectations

Historical melt-up phases have run from roughly 6 months (1999 narrowly defined) to 18+ months (2020-Q2 through 2022-Q1 if you stretch the boundary). Median is probably around 9-12 months from clear-melt-up onset to first major break. The variance is large enough that point estimates are misleading; the more useful question is "are the exit triggers firing yet?"

## Detection Signals

1. VIX term structure persistently in steep contango for 60+ trading days.
2. SPX 20d realized vol below 12% for an extended stretch.
3. Breadth (% above 200dma) declining while index rises — the divergence is the tell.
4. Sector dispersion rising; a single sector or narrative carrying disproportionate index weight.
5. Retail-flow indicators elevated: options call/put volume skewed, single-stock-options share of total volume rising, small-lot trading share rising.
6. Credit spreads tight and unresponsive to negative headlines.

## Exit Triggers

1. **A sharp vol shock that does not retrace within a week.** Past blowoffs have ended with a 30%+ single-day VIX spike that holds.
2. **Credit spreads widening 50+ bps in HY without an obvious idiosyncratic catalyst.** Cross-asset confirmation that risk appetite is turning.
3. **Leadership names breaking trend simultaneously.** When the basket of three to ten carrying names all violate medium-term trend within a few days, the narrowing has tipped over.
4. **A change in policy posture.** Hawkish surprise from the central bank, or a clear signal that the liquidity tailwind is reversing.
5. **The dominant narrative cracks publicly.** A high-profile failure (accounting scandal, marquee bankruptcy, key player capitulation) that breaks the story.

Exit triggers tend to fire in clusters. A single trigger is information; two triggered within a few sessions is strong evidence the regime is ending.

## Confidence Notes

The historical record for late-cycle melt-ups is reasonably well-documented. The 1999-2000 episode is the canonical reference and is densely studied. The 2021-Q1 episode is recent enough that primary sources are abundant. The framework generalizes well to retail-driven, narrative-led, narrowing-leadership tape.

Areas of lower confidence:
- The statistical-marker ranges are best-guess bands informed by reading prior episodes; they have not been backtested against a rigorous historical dataset yet. The Research Department should refine these as the regime classifier accumulates evidence.
- The 2024-2025 episode is genuinely ambiguous. The AI-led concentration shares many features with classical melt-ups but the underlying earnings story is stronger than the dot-com analog. Forcing the label fits a narrative; refusing the label might miss the regime.
- The exit triggers are drawn from past blowoffs that ended badly. Some late-cycle environments dissolve gradually rather than crack sharply; this profile is biased toward the cracking case because that case is the expensive one to miss.

This profile is a starting point. The Universe Curator and Research Department should refine it as evidence accumulates.

## Open Questions

- **How to handle the "structurally low vol" hypothesis?** If structural changes (passive flows, vol-control funds, 0DTE options) have permanently lowered realized vol, the markers above may need recalibration. Blocks: tighter detection. Owner: Research Department.
- **Does 2024-2025 qualify?** Profile-fit confidence is genuinely uncertain. Blocks: routing decisions today. Owner: Mike, pending more evidence.
