# Regime Profiles

**Document version:** 0.1 (draft)
**Last updated:** 2026-05-29
**Owner:** Mike White
**Status:** Draft

## Purpose

A regime profile is a structured description of a recurring market environment. It captures both what the environment looks like statistically (volatility, trend, breadth, dispersion, term structure) and what it has looked like historically (named past episodes with dates, the macro backdrop, what traded well, what blew up). Profiles are durable artifacts that strategies and agents reason against.

Profiles exist because Shrap's edge — if any — depends on knowing which environment it is in and adapting accordingly. A trend-following strategy that prints money in a late-cycle melt-up gets shredded in stagflation. A mean-reversion fade that works in crisis-recovery chop dies in a trending wartime commodity move. Without a shared, written vocabulary for regimes, the system has no way to express these distinctions to itself, and no way for the Hypothesis Generator to ground new strategy proposals in regime context.

Profiles are not predictions. They are reference frames. They tell the system "this is what this kind of market has rhymed with before, and these are the conditions under which it tends to end." The Regime Classifier's job is to pick which profiles best fit current conditions; the Regime Router's job is to wake and sleep strategies accordingly; the Hypothesis Generator's job is to draft regime-aware ideas. None of that works without the profiles being written down.

## The two-layer classifier

Shrap classifies the current market environment at two layers, and regime profiles must support both.

**Layer 1 — Statistical state.** A deterministic classifier reads numeric inputs (realized and implied vol, trend persistence, breadth, sector dispersion, VIX term structure slope, credit spreads, dollar trend) and labels the current state along a small set of axes. Profiles declare expected ranges for each axis under that regime. This layer is fast, reproducible, and cheap.

**Layer 2 — Historical analog.** An LLM-driven classifier reads the statistical state plus a rolling summary of macro conditions (rate path, fiscal stance, geopolitical events, earnings tone) and identifies which named past regimes the current period most resembles. Profiles declare the historical episodes they cover and the macro backdrop characteristic of each. This layer is slower, less deterministic, and more expensive — but it captures pattern resemblance the statistical layer cannot.

Both layers output the same kind of thing: a ranked list of regime IDs with confidence weights. The Regime Router consumes the merged output. When the layers disagree, the disagreement itself is information — it usually means the system is in a transition, and strategies tagged for either regime should be treated cautiously.

## How strategies reference regimes

Every promoted strategy carries metadata declaring its regime sensitivity. Two fields matter most:

- `regime_fit`: list of regime IDs the strategy is expected to perform well in, with an optional weight per regime. A strategy may fit multiple regimes; a strategy that fits none is a red flag that it was overfit to a specific period rather than to a recognizable environment.
- `regime_kill`: list of regime IDs that should immediately deactivate the strategy. A late-cycle-momentum strategy lists `crisis-recovery` and `wartime` in its kill list. When the classifier flips, the Regime Router pulls the strategy off the floor without waiting for it to lose money first.

Strategies may also carry a `regime_caution` list — regimes where they remain active but at reduced size — but `regime_fit` and `regime_kill` are mandatory. The Strategy Evaluator refuses to promote a strategy that has not declared both.

This is also how the Hypothesis Generator stays grounded. When it drafts a new strategy proposal, it must name the regime(s) the strategy is designed for and pull the relevant profile(s) into its prompt context. Strategies proposed without a regime anchor are rejected at intake.

## How the Regime Router uses profiles

The Regime Router is the bridge between classifier output and the trading floor. Its job per cycle:

1. Read the classifier's current top-k regime IDs with confidences.
2. For each currently-active strategy, check whether the current regime appears in its `regime_kill` list. If yes, deactivate.
3. For each currently-dormant strategy, check whether the current regime appears in its `regime_fit` list. If yes (and the strategy is not under research hold or risk-officer freeze), activate.
4. For strategies that remain active, apply per-regime size modifiers declared in the strategy spec.
5. Emit a `regime.routing.updated` event so downstream agents can log and reconcile.

The router does not invent regimes; it only reads the classifier's output and matches against strategy metadata. The profile is what makes the metadata meaningful. Without profiles, `regime_fit: ["late-cycle-melt-up"]` is just a string. With profiles, it is a contract: the strategy author has read what the regime means and committed to it.

## Schema fields each profile must populate

Each profile is a Markdown file under `docs/regimes/`. The filename is the regime ID in kebab-case (e.g., `late-cycle-melt-up.md`). The file follows `_template.md` and must populate every section. Empty sections are not acceptable; sections that genuinely don't apply must say so and explain why.

Required sections, in order:

1. **Identification** — name, ID, version, owner, status, one-sentence summary.
2. **Statistical Markers** — expected ranges for realized vol, implied vol, IV term structure slope, trend strength (ADX or equivalent), breadth (advance/decline, % above 200dma), sector dispersion, credit spreads, dollar trend. Ranges are honest band estimates, not point predictions.
3. **Historical Examples** — at least two named past episodes with start/end dates and a sentence or two on what made each canonical for this regime.
4. **Macro Backdrop** — rate environment, fiscal stance, geopolitical context, dominant narrative. This is the qualitative side that the LLM analog layer reads.
5. **What Works** — strategy archetypes that have historically performed well in this regime. Archetypes, not specific strategies.
6. **What Fails** — strategy archetypes that have historically performed poorly. Specific failure modes if known.
7. **Duration Expectations** — typical regime length, with honest acknowledgment of variance. "Stagflation has run from 18 months to 8+ years in past episodes" is more useful than a point estimate.
8. **Detection Signals** — what tells the classifier this regime is present. These should map onto the statistical markers, but may include qualitative signals too.
9. **Exit Triggers** — what tells the classifier the regime is ending. Crucially, these are not symmetric with detection signals — regimes often end with a specific structural break that didn't show up at the start.
10. **Confidence Notes** — honest disclosure of where the profile is well-grounded versus speculative. Which historical analogs are well-documented; which markers are best-guess; where the profile author is uncertain.

Profiles are versioned. Material changes require an ADR and Mike's approval. Minor refinements (clarifying language, adding a confirmed historical episode) can be made by the Universe Curator Agent or the Research Department under standing authority.

## Authorship and maintenance

Seed profiles are drafted by Mike or by a subagent under Mike's direction. Over time, the Research Department's Hypothesis Generator and the Intelligence Department's macro reader propose refinements based on accumulated evidence. The Regime Classifier itself never edits profiles — that would create a feedback loop where the classifier writes the rules it is graded against.

When the classifier encounters a market state that does not fit any existing profile well (low confidence across all known regimes for an extended period), that is a signal to draft a new profile. Mike must approve any new profile before it enters the routing table.

## Honest limits

Regime classification is a useful frame, not a true model of the world. The seam between regimes is not always sharp. The historical analogs are inexact. The statistical markers can be gamed by structural changes (e.g., the rise of 0DTE options has materially changed what "high implied vol" means). The system treats profiles as best-current-understanding, not as ground truth.

The cost of being wrong about regime is not infinite — strategies have their own internal risk controls, and the Risk Officer applies portfolio-level limits regardless of regime tagging. Regime profiles bias the firm toward the right strategies; they do not authorize the firm to bet the house.

## Seed set

The initial profile set includes:

- `late-cycle-melt-up.md`
- `stagflation.md`
- `crisis-recovery.md`
- `wartime.md`

These four cover much of the historically-recurring environment space but are not exhaustive. Profiles likely to be added next: `balanced-expansion` (the boring baseline), `disinflationary-rally`, `policy-shock`, `commodity-regime-shift`. Mike's review and lock-in is required before any new profile is added to the routing table.
