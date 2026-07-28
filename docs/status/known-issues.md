# Known issues

**Last updated:** 2026-07-28

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

## KI-014 — The Strategy Librarian and Strategy Runner were never deployed

**Status:** Open, found 2026-07-27 during the first-verdict run.

`sudo docker compose ps -a | grep -Ei "librarian|runner"` on the Dell returns
**nothing** — no container for `shrap_strategy_librarian` or
`shrap_strategy_runner`, stopped or otherwise. They were never created.

Both are ordinary default services in `infra/docker-compose.yml` with no
`profiles:` key, so a plain `docker compose up -d` should have started them.
The Librarian shipped in PR #40 (2026-07-xx) and the Runner in PR #80
(2026-07-24). Neither has ever run in production.

This did not block the first verdict, because the Evaluator performs its own
registry transition inside `commit()` and publishes the lifecycle events
directly. The Librarian is a *second* consumer of `research.strategy.verdict`
that transitions again under `expected_from` guarding
(`librarian_service.py:14`), so it acks and skips an already-applied verdict
by design. The status doc's earlier claim that the Librarian "idles waiting
for a verdict" described an architecture the code does not implement — the
Evaluator does not delegate to it.

**Two things to establish before restarting them**, because "just bring them
up" would paper over the more interesting question:

1. **Why were they never created?** A service that is in the compose file and
   absent from `ps -a` means either it was never in the file at the time of the
   last full `up -d`, or an `up -d <specific-service>` pattern has been used
   throughout and no full-stack `up` has run since PR #40. The deploy history
   would say which. The second explanation implicates every other service added
   since, and is the more likely one given the `--force-recreate` lesson.
2. **Is the Librarian's transition leg redundant by design or by accident?**
   Two components transitioning the same registry rows on the same event is a
   design smell even when `expected_from` makes it safe. Either the Evaluator
   should stop transitioning and delegate (making the Librarian load-bearing),
   or the Librarian's transition leg should be dropped and its role narrowed to
   lifecycle-event emission. Right now the answer is "whichever runs first
   wins," which is not a decision anyone made.

**Restarting is safe when it happens.** The Librarian's consumer group starts
at `start_id="0"` (`strategy_librarian/config.py:31`), so it will read the
2026-07-27 verdict from the stream start, attempt `hypothesis -> killed`, find
the registry already at `killed`, and ack-and-skip — logging at exception level
while behaving correctly.
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

**Status:** Anchor leg **resolved 2026-07-28**; trade-count leg **closed as
won't-fix**, superseded by a protocol question (below). Found 2026-07-27.

Two gates in `research/strategy_evaluator/` were written as universal but were
Framework #1-specific:

1. **Anchor freshness.** Every strategy was checked for a `promoted`
   world-changer anchor, and a missing one mapped to `KILL / anchor-not-live`
   with `engine_ran=False`. A `technical-catalyst` strategy is correctly
   anchor-*less*, so it would be killed without the backtest ever running.
2. **`DEFAULT_MIN_TRADES = 150`** (`engine.py:51`). Calibrated for the vision's
   fast layer — "fast loops, many trades." Applied uniformly it guarantees that
   every structural strategy dies on trade count regardless of edge, which is
   why the seed strategy's write-up predicts its own death.

**The anchor leg was worse than recorded here, and worse than ADR-0013
described it.** Both documents named the anchor check as the blocker. In fact
`_check_spec_hygiene` refused any archetype but `infra-graph-play` outright, and
it ran *before* the anchor check — so a `technical-catalyst` record raised
`SpecHygieneError` and produced no verdict at all, rather than the fake
`anchor-not-live` kill both documents predicted. Two gates, not one.

**Fix (2026-07-28).** Gate applicability is now declared per archetype in
`ARCHETYPE_POLICIES` (`pipeline.py`) — `technical-catalyst` is evaluable and
anchor-less, `infra-graph-play` keeps the anchor gate unchanged,
`bottleneck-rotation` stays refused, and an archetype absent from the table is
refused fail-closed. `research.evaluations` gained `anchor_required` so
"no anchor was required" and "the anchor is dead" remain distinguishable in the
ledger; cards and the CLI summary render `live` / `not-live` / `not-required`.

**The trade-count leg is not being fixed, and the earlier mitigation was
wrong.** "A min-trades band per archetype, Mike's calibration decision" assumed
the gate was too strict for structural strategies. The first three real
evaluations said otherwise: fold 5 of the seed produced an annualized Sharpe of
1.712 from **one trade**, and three parameter pairs on the same rule and ticker
gave 20/43/145 trades against Sharpes of 0.415 / −0.157 / 0.745 — monotonic in
count, sign-changing in Sharpe. A lower floor would not evaluate structural
strategies more fairly; it would promote noise with more confidence. The real
question is what protocol judges a multi-year thesis at all (event study,
realized-vs-thesis) — tracked as its own card, not as a threshold. 150 stands
for both archetypes.

## KI-015 — The friction stress is a scenario, not a worst-case bound

**Status:** Open, found 2026-07-28 by the control probe. Not a live hazard;
recorded before a real strategy relies on it.

The `trend-10-50` control evaluated to **base Sharpe -0.157 and stress Sharpe
+0.048** — the stressed run scored *better* than the unstressed one.

The stress pass applies two changes together: `stress_cost_multiplier` (+50%
costs) and `stress_execution_lag` (+1 day), the latter at
`engine.py` via `execution_lag=config.stress_execution_lag`. For a whipsawing
rule a one-day lag can skip false signals, and here that apparently outweighed
the higher costs. So the result is plausibly legitimate rather than a
calculation error — though what has been confirmed is that the lag exists and
is applied, not that it is definitely the cause.

**Why it matters.** `map_verdict` treats `stress_sharpe > 0` as evidence a
strategy "survives realistic friction." If the stressed scenario can score
*above* the base case, passing that check does not establish robustness — the
stress simply may not have bitten. The evaluation card's phrasing
("must stay positive to promote") reads as a floor on a strictly harsher
scenario, which it is not.

**No exploitable hole today.** `base_sharpe <= 0` is checked *before*
`stress_sharpe <= 0`, so a negative base always kills regardless of what the
stress run reports. The gap is interpretive, not a bypass.

**Options, none taken yet — this is Mike's call:**

- Report stress as a *delta* from base rather than a standalone Sharpe, so an
  improvement is visibly an improvement rather than a pass.
- Separate the two stress dimensions, so cost sensitivity and timing
  sensitivity are measured independently instead of netting against each other.
- Take the *minimum* of base and stress as the promotion input, making the
  check a genuine floor.
- Leave it, and reword the card so it does not imply a bound it does not
  provide.

The third is the smallest change that makes the claim true, but it discards the
information that a lag *helped* — which is itself a signal about a rule that
trades on noise.

## KI-016 — Parallel PRs appending to the same file tail merge into garbage

**Status:** Partially mitigated 2026-07-28. The verification command is fixed;
**CI is written but not yet pushed** — see "Blocked" below. The hazard itself is
structural and remains; CI turns it from silent into loud.

PRs #102 and #103 both branched from the same commit and both appended a block
of tests to the **end of** `tests/research/test_strategy_evaluator_pipeline.py`.
The conflict resolution in `99c22d6` interleaved the two blocks: one test was
truncated mid-body and the other PR's section header was spliced into it.

The result was a `SyntaxError`. `pytest` could not collect `tests/research/`, so
**the entire 634-test suite was unrunnable on `main`** — for roughly an hour,
across two merges, with nothing reporting it. It was found by chance, by
syncing before starting the next card.

This is KI-001's sibling. KI-001 is about *stacking* PRs; this is about two
*correctly independent* PRs that touch the same region of the same file. Neither
PR was wrong on its own and neither would have been caught by review.

**Why it was invisible.** The repo had no CI of any kind. `main` was never
verified by anything except a human choosing to run `pytest` locally.

**Compounding it:** `make install` ran `pip install -e '.[dev]'`, which cannot
collect the suite — tests import agent modules directly, and 13 files fail on a
clean environment. Local runs passed only because developer venvs accumulate
every extra over time. So the documented verification command was itself broken,
and had been for some time, in a way that only showed up on a fresh machine.

**Shipped:** a `test` extra (self-referential over every agent extra, so
versions cannot drift) with `make install` using `.[dev,test]`. Verified from
clean venvs under both pip and uv: `make all` now runs install → lint →
typecheck → test end to end, which it could not do before.

**Blocked:** `.github/workflows/ci.yml` is written and validated but could not
be pushed — the repo's stored OAuth credential lacks the `workflow` scope, and
GitHub refuses pushes that create or modify workflow files without it. One-time
unblock, run locally by Mike:

```bash
gh auth refresh -h github.com -s workflow
```

Adding the file through GitHub's web UI works too. Until it lands, `main` is
still verified only when someone chooses to run `pytest`.

**Still open even once CI lands:** CI reports the breakage, it does not prevent
it. When two open PRs touch the same file, prefer inserting near the relevant
section over appending to the tail, and merge one then rebase the other.

## KI-017 — `research.strategy.registered` has no producer, and a spec says it does

**Status:** Open, found 2026-07-28 by the full-firm audit.

`STREAM_STRATEGY_REGISTERED` is defined (`strategy_registry.py`) and
`stream_for_transition` returns it when `from_status is None` — but nothing
reaches that path in normal operation:

- `PostgresStrategyRegistry.register()` inserts the strategy row and its first
  transition row. It publishes **nothing**.
- `shrap-strategy-seed` states in its own module docstring that it does not
  publish.
- The Strategy Librarian is the only caller of `stream_for_transition`, and it
  only runs on verdict events, which always carry a `from_stage`.

So the stream is never written. Anything built to consume it would wait forever.

**The spec asserts the opposite, and I wrote it.**
`docs/agents/research/strategy-evaluator.md` (added in PR #103) says:

> "One stream the spec does not mention *does* have a producer:
> `research.strategy.registered`, published by the registry on every new
> strategy."

That was reasoning from a constant's existence rather than from a call site,
and it is the exact pattern this project's no-guessing rule exists to catch:
the repo artifact was verified, the claim about it was not.

**Mitigation:** the spec text is corrected in the same card as this entry. The
underlying gap — that registration is invisible on the bus — stays open. It is
not urgent while the Evaluator trigger sweeps on an interval, but the moment
anything wants to react to a new strategy, `register()` needs to publish.

## KI-018 — Langfuse is deployed and nothing writes to it

**Status:** Open, found 2026-07-28 by the full-firm audit. Blocking for a Month-4
exit criterion.

`infra/docker-compose.yml` runs Langfuse and a dedicated Postgres for it, with a
persistent volume. A grep across `src/` for `langfuse` returns **zero matches**.
No LLM call from any agent — Tech Watcher filter and synthesis, News Analyzer,
Filing Processor — is traced.

**Why this is more than an idle container:**

1. `docs/infrastructure/llm-routing.md` builds the entire cloud→local migration
   path on trace data: *"Once the cloud-primary agent has accumulated at least 50
   task instances of the relevant type (recorded in Langfuse with full
   input/output), the sample is the candidate evaluation set."*
2. `01-roadmap.md` Month 4 requires the **LLM Migration Evaluator** to run shadow
   evaluations on that accumulated data, and names "at least one agent migrated
   to local based on shadow-eval evidence" as a deliverable.
3. The **Cost Monitor** spec (Platform, Month 1, unbuilt) is defined as tracking
   "Langfuse spend."

None of the three is reachable, and the shortfall compounds: every LLM call made
untraced is evaluation sample that cannot be recovered afterwards. The Tech
Watcher has been filtering roughly 1,900 items against several prompt versions
with no retrievable record of the model's reasoning beyond the verdict rows in
`research.filter_verdict_history`.

It also leaves a hole in the "audit trails sufficient to analyse every decision"
success criterion: bus events are fully persisted by the Audit Logger, but *why*
a model rejected an item is not reconstructible.

**Mitigation:** instrument the shared `TierLLMClient` (`src/shrap/llm/client.py`)
rather than each agent — every LLM-using agent already routes through it, so one
card covers all of them. Tracked as Phase 3.1 in
`docs/roadmap/implementation-timeline.md`.

## KI-019 — `02-architecture.md` describes a trading engine that was never built

**Status:** Corrected in place 2026-07-28 (audit). The drift is closed; the
lesson is not.

The foundational architecture document described NautilusTrader as *"the trading
engine for the inner loop,"* with *"two adapters wired"* (Alpaca equities, IBKR
Gateway for MES futures), running *"as its own container on the Dell"* and owning
*"the `trading/` path in the repo."*

None of that is true, and none of it has been true since **ADR-0003 (Accepted,
2026-07-06)** re-scoped NautilusTrader from a Month-1 dependency to a gate. The
paper spine runs on a direct Alpaca client. There is no NautilusTrader container,
no IBKR adapter, and no `trading/` path.

The same paragraph block described **VectorBT PRO** running backtests submitted
over `ryzen.tasks` / `ryzen.results` streams. PR #41 ruled for an in-house
walk-forward engine instead. Neither stream exists.

**Why it matters more than a stale sentence.** `02-architecture.md` is one of the
ten foundational documents and is where a new session — human or agent — goes to
learn how the firm executes trades. Both paragraphs read as present-tense
description, not as plan. An agent reading them would look for a container that
is not there and a job protocol that was never written.

**Why it survived.** ADR-0003 and PR #41 each correctly recorded their own
decision. Neither went back to amend the document those decisions invalidated.
Accepted ADRs supersede the architecture doc silently, and nothing checks.

**Fix applied:** both paragraphs now carry an explicit correction banner naming
the superseding decision, with the original text retained as the design that
adoption would restore. **Standing implication:** an ADR that changes how a
component works should amend `02-architecture.md` in the same card, not leave
the reconciliation to a later audit.
