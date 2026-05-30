# Research Department Calibration Ledger

**Version:** 0.1 (draft)
**Date:** 2026-05-30
**Owner:** Mike White
**Status:** Draft — LIVING ACCOUNTABILITY LEDGER
**Serves:** ADR-0007.

## How this file works — read before editing

This file is **append-only**. Entries are never rewritten, edited for
hindsight, or quietly deleted. The point of the ledger is to make the
Research department's track record auditable by a future Mike who has
forgotten the context — and to make the current Mike honest about
which calls hit and which missed.

The Mike-profile norm "help me be right, not happy with my responses"
is the spirit. A clean-looking ledger is a *failure mode*: it means
entries are being curated for ego instead of recorded for calibration.
The expected pattern is many kills, many misses, a small number of
hits, and explicit reasoning attached to all three.

Editing rules:

1. **Append, never rewrite.** Corrections go in a new dated correction
   entry that references the original. Originals stay.
2. **Date every entry (YYYY-MM-DD).** Multiple entries per day are
   fine; ordering matters.
3. **Reasoning at time of decision, not at time of review.** When
   killing a candidate, capture what was known when the kill
   happened. Future hindsight goes in a separate review entry.
4. **No marketing voice.** No "we correctly identified" or "we
   anticipated." State the call and the outcome.
5. **Base-rate math is the point.** The denominator (everything
   considered) matters as much as the numerator (everything
   promoted). Survivorship bias is the enemy.

## Sections

- (a) Tech Watcher kill-graveyard
- (b) Bottleneck Scout forward-test ledger
- (c) Infrastructure Mapper graph-coverage metric
- (d) Strategy Evaluator promotion-rate vs kill-rate over time

Each section appends; none is overwritten.

---

## (a) Tech Watcher kill-graveyard

Running log of world-changer candidates the Tech Watcher (or Mike on
manual review) rejected. Every promoted world-changer has a counterpart
list of siblings that didn't make it; the graveyard is that list. Five
years from now this base-rate denominator is what tells us whether the
Tech Watcher promotion rate (~20% per ADR-0007 §Kill rates) is
calibrated.

**Schema per entry:**
- Date considered
- Candidate name
- Archetype (per `world-changer-archetypes.md`)
- Source(s) that surfaced it
- Kill reason (one of: impostor-match, insufficient evidence,
  unfalsifiable, archetype-mismatch, manual-mike-kill, other-with-note)
- One-line reasoning
- Reviewer (agent / Mike)
- Optional: review-at date for revisit

**Entries:**

*(none yet — file initialized 2026-05-30; first entries land when Tech
Watcher goes live in Month 2 per ADR-0007 sprint scope)*

**[MIKE INPUT REQUIRED]** Seed the graveyard with the impostor lists
from `world-changer-archetypes.md` (Theranos, hydrogen-by-2025,
metaverse, LK-99, full-self-driving-2020, etc.) as **historical
calibration anchors** with kill-date = the date the impostor was
publicly invalidated. This gives the Tech Watcher's base-rate math a
non-zero starting denominator and a tested calibration prior on day 1.
Decision point for Mike before Month 2 launch.

---

## (b) Bottleneck Scout forward-test ledger

Locked predictions. Once a bottleneck candidate is emitted by the
Scout with a `validation_horizon`, the call is logged here and never
edited until the horizon date passes. The outcome (validated, killed,
still-pending past horizon) is appended on the horizon date or sooner
if a kill criterion fires.

This is the load-bearing accountability artifact for the firm's edge
claim. The Sept 2024 LITE backwards-test is methodology debug; this
ledger is the real edge proof.

**Schema per locked prediction:**
- Lock date (date the candidate hit `detected` status)
- Candidate id (links to `research.bottlenecks` row)
- World-changer it constrains
- Bottleneck layer role
- Named physical/economic limit
- Forced substitute(s) — public tickers
- Timeline-to-binding (ordinal: forming / near / binding-now /
  solved-or-deferred)
- Evidence snapshot (hash + reference to evidence rows at lock time)
- Kill criteria (verbatim, locked)
- Expected horizon date (latest by which at least one kill criterion
  is observable)
- Outcome status: `pending` / `validated` / `binding` / `killed` /
  `expired-no-resolution`
- Outcome date and outcome note (appended, never edited)

**Entries:**

*(none yet — file initialized 2026-05-30; first locked prediction
expected when the backwards-test rubric is written and the live Scout
runs Month 3 per `bottleneck-scout.md` §Sprint scope)*

**[MIKE INPUT REQUIRED]** The Sept 2024 LITE backwards-test result will
be the first ledger entry, with explicit flag that it is a
*reproduction* not a *forward call* — its predictive weight is much
lower than any live forward entry. Rubric for what counts as
backwards-test pass / partial-pass / fail is an open item in
`bottleneck-scout.md` §Open questions and must be written before the
run.

**Self-honesty alarms tracked here:**
- Trailing-90d binding rate exceeds 25% of detected → selection-bias
  warning per `bottleneck-scout.md` §Processing step 12.
- Trailing-90d kill rate falls below 60% on detected candidates →
  promotion-too-easy warning (the funnel is supposed to be brutal).
- Forward outcomes consistently land outside the locked horizon →
  timeline-ordinals are mis-calibrated and need re-anchoring.

---

## (c) Infrastructure Mapper graph-coverage metric

For each promoted world-changer's graph, what fraction of the
world-changer's *actual realized revenue dependency tree* (verifiable
ex post from supplier disclosures, hyperscaler capex attribution,
10-K supplier mentions, and earnings call name-checks) is represented
as nodes in the graph?

A graph that covers 30% of the realized dependency tree is mostly
guessing. A graph that covers 90% is doing the structural research
the ADR-0007 thesis depends on. The Cisco-1999 failure mode shows up
here as graphs that mis-rate node criticality — the right nodes
present, the wrong ones weighted.

**Coverage measured quarterly:**

- **Numerator:** Distinct suppliers, partners, and downstream
  beneficiaries that are (a) named in the world-changer's actual
  realized supply chain per primary-source disclosures during the
  quarter AND (b) present as nodes in the graph at end of quarter.
- **Denominator:** All distinct suppliers, partners, and downstream
  beneficiaries named in primary-source disclosures during the
  quarter, irrespective of whether the Mapper had captured them.
- **Criticality calibration:** Of nodes the Mapper flagged
  critical-path, what fraction were named as binding constraints in
  earnings discussions? Of nodes the Mapper flagged
  non-critical-path, what fraction surprised by becoming binding?

**Entries:**

*(none yet — first measurement expected end of Month 4 when the
Mapper has at least one graph in `validated` state for a full
reporting quarter)*

**[MIKE INPUT REQUIRED]** Concrete methodology for assembling the
denominator: which primary sources count, how to normalize
supplier-name aliasing, what to do about un-disclosed suppliers
(known to exist, name not public). Until the methodology is written,
the coverage metric is a placeholder.

**Honest caveat.** Coverage measured this way is biased toward
*disclosed* dependencies. A world-changer's most-strategic suppliers
are often the least-disclosed (sole-source competitive moats). 90%
coverage of disclosed suppliers may still miss the layer that matters
most. Coverage is a necessary metric, not a sufficient one.

---

## (d) Strategy Evaluator promotion-rate vs kill-rate over time

Per ADR-0007 §Kill rates compound across the funnel, the Strategy
Evaluator kills 90%+ of hypotheses reaching it. This section tracks
that rate over time, broken down by which Bottleneck Scout candidate
fed the hypothesis.

**Metrics per reporting period (monthly):**

- N hypotheses received from Hypothesis Generator
- N promoted to paper-strategy status
- N killed at evaluator
- Kill reasons distribution (PBO fail, deflated-Sharpe fail, trade-
  count floor, transaction-cost realism, missing bottleneck kill
  criterion, overfitting flag, other)
- Promotion rate (promoted / received)
- Trailing-12-month promotion rate
- Rolling association between Bottleneck Scout candidate that seeded
  the hypothesis and downstream evaluator promotion — i.e. which
  Scout calls feed hypotheses that survive evaluation, and which
  don't. This is the cross-link that tells Mike whether the funnel
  is bottlenecked at Step 3 detection or at Step 4 evaluation.

**Entries:**

*(none yet — first monthly snapshot expected end of Month 3 when at
least one hypothesis has run the full funnel including evaluator)*

**[MIKE INPUT REQUIRED]** Target promotion rate. ADR-0007 implies "low"
without naming a number; Mike should set an explicit target band (e.g.
"3-10% promoted is healthy; >20% suggests the evaluator's overfitting
controls are too loose; <1% suggests the Hypothesis Generator is
mis-specifying trades from valid Scout signals") so the metric has a
reference line to read against.

---

## Cross-section principles

1. **No promotional reframing.** A kill that turns out to have been
   correct in hindsight is still a kill; do not move it to a "we
   correctly killed" trophy section. Entries are facts on the date
   they were recorded.
2. **Hindsight reviews welcomed but dated separately.** When a
   prediction's outcome makes the original reasoning look wrong (good
   call, bad reasoning; or bad call, good reasoning), append a review
   entry dated to the review, not to the original.
3. **The ledger is the firm's memory.** The Tech Watcher, Bottleneck
   Scout, and Infrastructure Mapper are agents; this ledger is the
   institutional record of how well they did. The agents do not write
   to this file directly during the sprint — Mike does, with agent
   evidence attached. Automated append is a Month-6+ question.
4. **Public sharing.** This file is internal-only by default. Any
   external sharing requires Mike's explicit decision per entry,
   per ADR-0007 firm-wide paper-only constraint and the firm's
   pre-edge posture.

## Mike-input-required summary

- Seeding (a) with historical impostor entries as calibration anchors.
- Backwards-test pass/partial/fail rubric for (b)'s first entry.
- Denominator methodology for (c).
- Target promotion band for (d).
- Whether and when (Month 6+) to automate append from agents vs keep
  Mike-in-the-loop.
