# Known issues

**Last updated:** 2026-07-27

## KI-001 — Stacked PRs can be marked merged without reaching main

**Status:** Known workflow hazard. Recurred with PR #19 (Card 14), recovered by PR #20.

PR #10 was marked merged while its changes landed in the stacked base branch rather than `main`. PR #11 recovered the Card 8 changes onto `main`. The same failure repeated with PR #19, recovered by PR #20. Prefer independent branches off `main` over stacking.

**Mitigation:** After stacked PRs merge, verify main inclusion with:

```bash
git merge-base --is-ancestor <feature_commit> origin/main
```

Do not run live/deploy smoke until the feature commit is actually on `origin/main`.

## KI-002 — NautilusTrader bridge is still unresolved

**Status:** Resolved 2026-07-06 by ADR-0003 (Accepted).

Direct Alpaca paper access is the accepted broker interface for the paper phase. Broker credentials live only in the Execution Agent and Reconciliation Agent containers. NautilusTrader adoption is a gate triggered by live capital or by execution needs beyond market/day orders — see `docs/decisions/0003-nautilus-redis-bridge-coverage.md`.

## KI-003 — Fill event live path is not yet observed with a real fill

**Status:** Resolved 2026-07-15. Market-hours smoke passed 9/9 on the Dell.

Root cause found during Card 16: the Execution Agent checked order status exactly once, immediately after submission, so a fill landing later was never published. Pending-order re-polling (5s interval, publish on change) shipped in PR #22. The first live fill was observed 2026-07-08 (AAPL x1 @ 313.33); the full 9/9 close — `order-filled`, `fill-persisted`, and `reconciliation: clean=True` — landed 2026-07-15 after the lookback fixes (PR #34–35).

## KI-004 — Paper order persistence consumer is not packaged yet

**Status:** Resolved 2026-07-02. Card 12 packaged `shrap-paper-order-store` as a deployable service (PR #16): console script, `PAPER_ORDER_STORE_*` settings, Dockerfile, and Compose service are on `main`.

## KI-005 — Current position state and reconciliation do not exist yet

**Status:** Order-level reconciliation shipped (Cards 13–14); position state still deferred.

The Reconciliation Agent compares Alpaca paper orders against `trading.paper_order_events` on a 300s interval and publishes `operations.reconciliation-completed` / `-discrepancy`. Current-position derivation remains unimplemented; the order trail is still append-only history.

**Mitigation:** Position-state derivation becomes its own card when the first Research strategy needs portfolio state, or before live capital — whichever comes first.

## KI-006 — Agents replay full stream history on every restart

**Status:** Resolved 2026-07-15. PR #37 moved all stream consumers to Redis consumer groups.

Stream consumers held their offsets in memory and read from `start_id=0-0` on restart, replaying the entire history — the cause of the 2026-07-06 poison-event incident. PR #27/#32 made replay safe (poison-skip); PR #30's rate guardrails blunted the replay-reapproval hazard; PR #37 fixed the root cause with consumer groups and acknowledged, persisted offsets (`src/shrap/events/groups.py`).

One residual, tracked in `current-sprint.md` open work: retry-backoff for systemic errors (broker/DB down) was scoped into this card's mitigation but did not ship in PR #37. The second residual (Dell running pre-#36 containers) was resolved by the 2026-07-17 upgrade session: full-stack rebuild through PR #45, consumer groups live in production.

## KI-007 — Pre-synthesis funnel rejections leave no persistent trace

**Status:** Fix shipped 2026-07-19 (append-only `research.filter_verdict_history`
+ per-pass `research.tech_watcher_cluster_log`); pending Dell deploy and first
live-batch verification. Found 2026-07-18 during the v2 re-filter audit.

The Tech Watcher's rejection graveyard (`research.world_changers`, status
`rejected`) only records candidates that reach synthesis. A cluster killed
earlier by the two-source triangulation rule writes no row, and a re-filter
overwrites `filter_result` in place, so the prior prompt version's verdicts
are destroyed. Container logs were the only remaining record of the first
batch's six v1 keeps, and they did not survive the PR #49 redeploy.

Concrete cost: after the v2 re-filter (0/246 kept), the one borderline-real
v1 item could not be identified to audit whether v2 rejected it on principle
(economic-evidence rule) or misread it — the false-negative check the
re-filter comparison existed for. This violates "the denominator is never
hidden" and principle 8 (audit everything).

**Mitigation (shipped 2026-07-19):** every filter verdict appends a row to
`research.filter_verdict_history` stamped with prompt version and model —
re-filters overwrite the item's current `filter_result` but never the
history. Every cluster the triangulation stage considers writes a
disposition row (`synthesized` / `deferred-max-proposals` /
`held-single-source`) with its item ids to
`research.tech_watcher_cluster_log` before any synthesis LLM call, so a
hold or a mid-batch crash still leaves a queryable trace. The next
re-filter comparison can name its borderline items instead of losing them.

## KI-008 — The funnel's thesis memory depends on Mike typing it in

**Status:** Open. Found 2026-07-27 while recording the Valar Atomics
demonstration against the promoted fission thesis.

`research.world_changer_observations` (PR #85) closed a real gap — thesis-level
evidence previously had nowhere to live — but its only writer is Mike running
`shrap-world-changer-observe add`. Nothing in the pipeline attaches an
observation to a promoted thesis automatically. So the funnel's memory of what
has happened to a live thesis is exactly as good as Mike's manual diligence,
which inverts principle 6 (Mike is the architect, not the implementer) and
principle 10 (Mike's time is the constraint). The store is correct; the feed is
missing.

The concrete case is sharper than a general complaint, because the ingest
capability already exists. Valar Atomics' Ward 250 reached criticality
2026-06-18 under the DOE Reactor Pilot Program — the motivating case for the
DQ-007 reorder — and a public demonstration followed. Both bear on the promoted
fission thesis (`01KXVVPXDMB4HS1QNRPQWRP1RX`). The gov-sources legs meant to
catch exactly this shipped in PR #53 and are deployed. Valar is a *private*
company, so it is invisible to the EDGAR leg and to the ticker-scoped News
Analyzer and Filing Processor — but the DOE newsroom and USASpending legs do
not depend on a ticker, and a DOE program participant should leave a trail in
them.

**Diagnostic first, card second.** Before building an auto-attach path,
establish where the Valar signal actually went: query
`research.tech_watcher_cluster_log` and `research.filter_verdict_history` for
Valar / Reactor Pilot Program items. Four outcomes, four different fixes:

- *Never ingested* → source coverage gap (which leg, and why).
- *Ingested, filtered out* → filter prompt or model quality (folds into DQ-006).
- *Held single-source* → the triangulation rule is correct but the second leg
  is missing; consider whether a private-company thesis can ever triangulate
  under the current taxonomy.
- *Proposed and sitting unreviewed* → the review surface is the bottleneck,
  not the ingest.

**Diagnostic result (2026-07-27): outcome 2, "ingested, filtered out."** The
DOE newsroom leg *did* catch it — one item, published 2026-07-06, fetched and
filtered 2026-07-19 — and the filter rejected it. Details and the verdict text
in DQ-006; the structural consequence in KI-009. Three corrections to what
this entry originally assumed:

- The private-company objection was wrong. Valar is invisible to EDGAR and to
  the ticker-scoped News Analyzer / Filing Processor, but the DOE leg is not
  ticker-scoped and saw it fine.
- The auto-attach card is **premature**. Attaching pipeline hits to theses is
  worthless while the filter rejects the hits worth attaching. KI-009's
  prompt-v4 card comes first.
- The seeded thesis's own text already names "Valar Ward 250 critical
  2026-06-18," so the firm had the date recorded before the diagnostic ran.
  The gap is not knowing the fact; it is that nothing connects the fact to the
  thesis record automatically.

Note also that the anchor thesis is `proposed`, never `promoted` (`decided_at`
and `decision_note` are both NULL) — see the Infra Mapper foundation issue in
`current-sprint.md`.

**Mitigation (not yet built):** an auto-attach path writing pipeline hits to
`world_changer_observations` for the theses they reference, with `bearing` and
the kill-criterion link left unset for Mike rather than guessed — the falsifier
judgement is the part that must not be automated away. A thesis-scoped
watchlist (named entities, including private ones, attached to a promoted
world-changer) is the likely shape, since ticker-scoped watching structurally
cannot see a Valar.

## KI-009 — The funnel is structurally incapable of promoting anything

**Status:** Open, found 2026-07-27 by the KI-008 diagnostic. This is a hard
block, not a slowness problem.

`research.tech_watcher_cluster_log` holds 8 rows. All 8 are
`held-single-source`. **Every one has `source_classes = ["arxiv"]`** — two
clusters a day (`compute-substrate`, `cost-curve`), one item each,
2026-07-24 through 2026-07-27. Nothing has ever been synthesized.

The triangulation rule (`Cluster.promotable`) requires **≥2 distinct origins
AND ≥1 hard leg**, where hard = EDGAR / USASpending / Federal Register. arXiv
maps to origin `research` and is not hard. So an arXiv-only cluster fails
*both* conditions simultaneously. As long as arXiv is the only source whose
items survive the filter, the funnel cannot promote anything — not slowly,
not eventually. Ever.

And arXiv is the only survivor. Ingest volumes are healthy on the hard legs
(sec-edgar 1656, federal-register 113, usaspending 117, doe-newsroom 16), yet
none of their items reach a cluster. The filter is rejecting them — see DQ-006
for the named false negative (a DOE reactor-criticality announcement rejected
for lacking "independent replication" when its headline says it is the
*fourth* one).

**This inverts the previous read.** Ingest health looked like the bottleneck;
it is not. Even a fully healthy USASpending leg would have its items rejected
by the same filter and change nothing. **The filter is the binding
constraint.**

**Fix order:**

1. **Filter prompt v4, source-class aware** (DQ-006). Unblocks triangulation.
   Nothing else matters until hard-source items can pass.
2. **Ingest staleness alerting** (below). Real, but secondary.
3. Re-run the filter over already-ingested hard-source items under v4 — 1900+
   items are sitting filtered-and-rejected, and KI-007's verdict history means
   a re-filter is now auditable.

## KI-010 — Ingest legs die silently

**Status:** Open, found 2026-07-27.

Per-leg ingest as of 2026-07-27:

| leg | items | oldest | newest |
|---|---|---|---|
| sec-edgar | 1656 | 2026-07-15 | 2026-07-27 |
| arxiv | 700 | 2026-07-16 | 2026-07-24 |
| usaspending | 117 | 1978-09-15 | **2026-07-09** |
| federal-register | 113 | 2026-05-01 | 2026-07-27 |
| doe-newsroom | 16 | 2026-06-30 | 2026-07-26 |

**Correction (2026-07-27, same day).** The original entry read that table as
"USASpending has ingested nothing for 18 days." That was wrong, and the error
is worth keeping on the record because it is easy to repeat: for USASpending,
`external_ts` is the award's *Start Date*, not the fetch time. Contract start
dates are mostly historical — hence the 1978 minimum — so `max(external_ts)`
says nothing about whether the leg is running. **Judging ingest liveness needs
`fetched_at`; every other leg's `external_ts` happens to track publication
closely enough that the distinction was invisible until a leg where it doesn't.**

The leg was alive. The real defect, found by calling the API directly, was
worse: `time_period` matches **any transaction activity** in the window, so
decades-old umbrella contracts qualify on a routine modification, and the
API's default ordering favours the **largest** awards — which are exactly
those. A plain 30-day DOE query returned the 1993 Lockheed ($48B), 2017 Sandia
($42B) and 1999 UT-Battelle ($42B) national-lab management contracts. Page 1
was a fixed set of ancient contracts that deduped to nothing on every pull, so
the leg looked healthy while being **structurally blind to new awards**.

Adding `date_type: "new_awards_only"` and sorting by `Start Date` desc returns
3 DOE awards for the same window — including **$900M to American Centrifuge
Operating (Centrus uranium enrichment) dated 2026-07-06**, a hard-source,
dollar-denominated item sitting directly on the promoted fission thesis's
critical path, in the `raw-inputs` layer the Infra Mapper flagged as having no
representation. The funnel had never seen it. Fixed in the same-day card.

Secondary observations from the original query still stand: DOE newsroom shows
~13 days of **ingest latency** (the 2026-07-06 criticality article was fetched
2026-07-19), so that leg runs as a slow backfill rather than a live feed; and
arXiv is 3 days behind the two current legs.

The issue itself stays open — nothing alerts on a leg that *does* die, and a
silently-blind leg is exactly as invisible as a silently-dead one.

**Mitigation (not yet built):** a per-leg freshness check with a Health
Monitor alert when a leg's newest **`fetched_at`** falls outside its expected
cadence — `fetched_at`, not `external_ts`, per the correction above. The Infra
Mapper's staleness pass is the working precedent: same shape, different table,
a max-timestamp per group compared against a threshold.

Worth adding a second check alongside it: a leg whose pulls return rows but
insert **zero new** item_ids for N consecutive passes. That is the signature
USASpending had, and a freshness check on `fetched_at` alone would have called
it healthy.

## KI-011 — `intelligence.signal` has two producers and no consumer

**Status:** Open, found 2026-07-27 by an architecture trace. Decided in
ADR-0013 §3; not yet built.

The News Analyzer and Filing Processor both publish materiality-scored signals
to `intelligence.signal` on a market-phase-driven cadence. A grep across
`src/shrap/` finds the stream constant defined and published in exactly two
places — `intelligence/news_analyzer/service.py:63` and
`intelligence/filing_processor/service.py:85` — and read nowhere. The Decision
Maker subscribes only to `STREAM_STRATEGY_SIGNAL`
(`trading_floor/decision_maker_service.py:87`).

Two deployed agents, both escalating material items to a cloud model, write
into a void. Every cost of running them is paid and none of the value is
collected.

**Mitigation (decided, not built):** ADR-0013 §3 routes the stream to promoted
Framework #3 strategies through the Strategy Runner, which declares interest by
ticker and signal type. Deliberately *not* wired straight into the Decision
Maker — that would put unevaluated signals on the order path.

## KI-012 — ADR-0010 is accepted and substantially unimplemented

**Status:** Open, found 2026-07-27. Tracked by ADR-0013 §4.

ADR-0010 (Accepted 2026-05-31) corrected ADR-0007's exclusivity claim and made
four decisions that have no implementation two months later:

| ADR-0010 | Status |
|---|---|
| §3 Structural Analysis as a separate department | Zero agents; `docs/agents/structural-analysis/` does not exist |
| §4 Regime Classifier as a strategy-activation gate | No Regime Router; classifier output gates nothing |
| §5 Forced-Proxy as Framework #2 via ADR-0011 | ADR-0011 never written; `docs/decisions/` runs 0001–0010, 0012 |
| §6 Multiple theses in parallel | Only Framework #1 exists |

The practical effect is that the implementation drifted back into exactly the
single-thesis exclusivity ADR-0010 was written to correct. This is the
clearest instance so far of operating principle 7 running in reverse: the spec
was updated and the code never followed, and nothing surfaced the divergence
because no artifact tracks ADR implementation status.

**Worth considering separately:** whether accepted ADRs need an implementation
-status field, since this failure mode is silent by construction.

## KI-013 — Evaluator gates are Framework #1 constructs applied to all strategies

**Status:** Open, found 2026-07-27. Blocking for ADR-0013; see its Consequences.

Two gates in `research/strategy_evaluator/` are written as universal but are
Framework #1-specific:

1. **Anchor freshness** (`pipeline.py:293`). Every strategy is checked for a
   `promoted` world-changer anchor, and a missing one maps to
   `KILL / anchor-not-live` with `engine_ran=False`. A `technical-catalyst`
   strategy is correctly anchor-*less*; under the current code it would be
   killed without the backtest ever running.
2. **`DEFAULT_MIN_TRADES = 150`** (`engine.py:51`). Calibrated for the vision's
   fast layer — "fast loops, many trades." Applied uniformly it guarantees that
   every structural strategy dies on trade count regardless of edge, which is
   why the seed strategy's write-up predicts its own death.

Neither is wrong; both are miscategorized as universal. Until they are
archetype-conditional the Evaluator can only meaningfully evaluate a class of
strategy the firm cannot yet produce.

**Mitigation:** make both archetype-conditional. The min-trades band per
archetype is a calibration decision and is Mike's.
