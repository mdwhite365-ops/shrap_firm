# Known issues

**Last updated:** 2026-08-25 (**KI-033** — no position under one share could ever be closed, 52 silent refusals; **KI-034** — the backup crons may point at a path that does not exist)

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

**ADR-0017 dissolves most of this rather than solving it.** One strategy per broker account means the account's positions *are* that strategy's positions and its equity curve *is* that strategy's P&L — nothing to derive, produced by the broker rather than by us. What survives: any strategy sharing an account with another, and the fact that `research.strategy_runner_state.last_quantity` records *intent* (a clamped or partially filled order leaves recorded intent above the true position). Under one-strategy-per-account, broker position state answers that directly.

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

**Status: RESOLVED 2026-08-03 by KI-026's fix (#189).** It was an ingest
defect, not a taxonomy one. Diagnosis history preserved below because it was
wrong twice and the reasons are worth keeping.

After EDGAR filings were re-scored with their document bodies instead of their
index entries, `sec-edgar` admitted **46 items** — 17 `compute-substrate`,
15 `cost-curve`, 12 `bio-mechanism`, 2 `physical-realization` — against **zero**
in the two months prior. Firm-wide the corpus went from 2 fossil admits to 49.
`bio-mechanism` split 12 admitted / 8 rejected, so the model is testing filings
against the grammar rather than keyword-matching.

On 2026-08-02 the funnel synthesized and proposed its first pipeline candidate
(`haleu-cost-curve`, 01KZ0N02M48XF8WP29T6F2H7KR) — ingest through proposal, six
stages, no human in the path.

**The heading was never true.** The funnel could always promote; it had never
been shown anything promotable. Three rounds of evidence pointed at the
taxonomy — a shadow eval across five models, a three-bar archetype experiment,
and 2,472 v4 verdicts — and every one of them was measured on a corpus that was
72% file sizes. A denominator that is mostly metadata makes every rate a
statement about the metadata.

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

### Update 2026-07-31 — step 1 shipped and did not unblock it

Prompt v4 is deployed. Hard-source items still do not pass, so step 3 has
nothing to re-run *to* and is on hold until there is a bar that admits
something.

The ADR-0009 shadow eval (PRs #170–#172) then removed the other easy
explanation. Four models scored the same corpus on v4 — `gpt-oss:20b-cloud`,
`qwen3.5:397b`, `glm-5.2`, `deepseek-v4-pro:cloud`, spanning four families and
two flagship usage tiers — and returned **0% relevant**, except one false
positive from `deepseek-v4-pro` that admitted a routine 10-Q on the reasoning
that "the filing's existence meets the attested bar." **The filter is not
model-limited**; no model purchase changes this.

The working hypothesis is now that **the archetype bars are aggregate-level
predicates being evaluated against item-level evidence** — "unit cost declining
on a learning-curve slope consistent across producers" is a statement about a
series, and no single filing can satisfy it. Under that reading the rejections
are all correct and the *question* is wrong, which also explains DQ-006's named
false negative precisely: the DOE announcement was asked to be both a
replication event and the survey proving replication was independent.

That hypothesis is Mike's to rule on and is testable rather than arguable — see
`docs/research/archetype-bar-experiment.md` (timeline card 1.4), which scores
three candidate bar formulations over the full corpus and reports the admitted
items rather than a rate. **KI-009 stays open until that ruling lands.**

One consequence worth recording: the same eval found that
`parse_filter_response` scored fence-wrapped JSON as an unparseable verdict
(fixed in #172). It cost `glm-5.2` 30 of 40 answers in the eval. The live
filter runs a model that does not fence, so the production corpus is probably
unaffected — confirm with a `filter_verdict_history` count on the
`'unparseable filter response'` reason before assuming it.

### Measured 2026-07-31 — prompt v4 has admitted nothing, and the number is zero

`research.filter_verdict_history`, the whole table:

| prompt version | verdicts | relevant | models |
|---|---|---|---|
| 3 | 2,082 | **2** | 1 |
| 4 | 2,472 | **0** | 2 |

**Prompt v4 has produced zero relevant verdicts over 2,472 items.** Not a low
rate — none. This replaces every previous estimate in this entry, which
inferred the funnel's admit rate from cluster logs and eval samples.

The two positives that exist are both from v3, and their provenance is the
finding:

| item | version | model | archetype | decided |
|---|---|---|---|---|
| `arxiv:2607.20349v1` | 3 | `qwen3.5:9b-q4_K_M` | `cost-curve` | 2026-07-23 |
| `arxiv:2607.20083v1` | 3 | `qwen3.5:9b-q4_K_M` | `compute-substrate` | 2026-07-23 |

Both were scored by **`qwen3.5:9b-q4_K_M`** — the local 9B this project
replaced *because it could not perform the task* (DQ-006: it rejected a fourth
reactor criticality for lacking "independent replication" and named fusion
vocabulary for a fission item) — under **prompt v3**, which was replaced for
the same reason. Both are arXiv, the one source class that can never satisfy
triangulation. So the corpus's entire positive class was produced by a
discredited model under a superseded prompt, on items that were unpromotable
even if correct.

**This corrects how every shadow eval's agreement column should be read.** The
harness stratifies on `incumbent_relevant`, so the "2 incumbent-relevant items"
in each eval sample are these two records. All five models in the 2026-07-31
run rejected both, which the report renders as 90% agreement and 10%
disagreement. The truer statement is that the five models agreed with each
other on **everything v4 has ever judged**, and jointly overruled two stale
records nobody would now defend. There is no disagreement to adjudicate,
because there is no valid positive class to disagree about.

**The fenced-JSON question above is answered: zero rows.** The defect fixed in
#172 never cost production a single verdict — the live filter's model does not
fence, as expected. No corpus repair is needed and none should be run. #172 is
purely prospective, which is the right outcome and is now measured rather than
assumed.

**Do not re-filter the two v3 items before the bar experiment runs.** Their
`filter_result` is stale by our own current standard and `refilter` would
correctly flip them, but they are the only items any model has ever admitted
and therefore the experiment's one natural control: a candidate bar that
admits nothing *and* rejects these is failing differently from one that admits
them. Correcting the record is cheap and can happen after.

### Resolved 2026-07-31 by the bar experiment — two causes, neither the one predicted

The experiment ran (#180, #181). **The hypothesis above is falsified**, and the
falsification condition was written into the spec before the run: *"if Bar A
wins, the bars are not misapplied and the constraint is upstream in what we
ingest."*

On a source-stratified 599-item sample, `qwen3.5:397b`:

| bar | admits |
|---|---|
| **A — incumbent, unmodified** | Anduril `physical-realization`, HALEU `cost-curve` |
| **B — evidence contribution** | Anduril `physical-realization`, HALEU `cost-curve` |
| **C — signal tagging** | HALEU only, mislabelled `bio-mechanism` |

**Bar B admits exactly what the incumbent admits.** Reformulating the question
changes nothing. Bar C is strictly worse: flattening the signal catalogue strips
the archetype context that gives a signal meaning, so "manufacturing scale
demonstrated at acceptable cost" matched a uranium enrichment plant under
*Biological-mechanism unlocks*. **The archetype bars are not misapplied.** The
taxonomy ruling this entry has been waiting on is not needed.

The two real causes, both found by the same run:

**1. 72% of the corpus contains no fact to judge.** `sec-edgar` is 3,740 of
5,221 items, and its stored summary is the Atom index entry:

```
<b>Filed:</b> 2026-07-31 <b>AccNo:</b> 0001193125-26-328866 <b>Size:</b> 565 KB
<br>Item 8.01: Other Events <br>Item 9.01: Financial Statements and Exhibits
```

Filed date, accession number, **file size in kilobytes**, item codes. No
revenue, capacity, capex or pricing — nothing any bar could admit. Zero EDGAR
admits from ~425 scored, under all three bars. Meanwhile `usaspending` averages
*fewer* characters (147 vs 179) and admits at roughly **14%**, because its
summary states a recipient, an amount and a purpose. Length was never the
discriminator; content type is. The Filing Processor already fetches EDGAR full
text into `intelligence.filings.full_text` and scores per item code with priors
— the Tech Watcher ingests the same filings and keeps only the index entry.
Tracked as **KI-026**, **shipped 2026-08-01**: `research.raw_source_items`
gained a `document_text` column, `shrap-tech-watcher-edgar-text` backfills it by
dereferencing the filing link, and the ingest loop now fetches bodies between
ingest and filter so new filings never reach the model as metadata. The filter
prompt carries `Document:` in place of `Summary:` when a body exists.

Reuses the Filing Processor's `EdgarFilingClient` rather than adding a second
fetcher — its own docstring named this gap. `intelligence.filings` could not
simply be joined: it holds only registrants matched to the Tier 3 roster by CIK,
and the world-changer funnel looks for patterns anywhere in the economy.

**The backfill and the re-score are two steps.** Fetching text does not change
any existing verdict; those ~3,700 were formed against metadata and stay stale
until `shrap-tech-watcher-refilter --force`. `FILTER_PROMPT_VERSION` is
deliberately unbumped — the prompt did not change, the content did.

**2. The filter is partly model-limited after all.** Both admitted items had
been scored under the *same* prompt v4 by `gpt-oss:20b-cloud` and marked
not-relevant. See `calibration.md` §(e) Correction 2 — the shadow eval's "five
models, zero disagreements" was measured on 20 items containing no USASpending
awards at all. **No item in the corpus has ever been scored by the promoted
model**, and `refilter_pass` already selects on the `(prompt version, model)`
pair, so a full re-filter needs no new code.

**KI-009 stays open**, but the ruling it was waiting for is not the one it
needed. It now depends on the EDGAR ingest card (KI-026) and a re-filter under
`qwen3.5:397b`, neither of which requires a decision from Mike.

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

**Status:** Code shipped 2026-08-23 (#208). **Not yet verified against the live
Langfuse**, and deliberately not marked closed until it is — see "What remains"
below. Found 2026-07-28 by the full-firm audit; blocking for a Month-4 exit
criterion.

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

### What shipped (#208, 2026-08-23)

`src/shrap/llm/tracing.py` posts each completion to Langfuse's ingestion API as
a trace plus a generation, carrying full input and output, token counts, model
parameters and latency. `TierLLMClient.complete()` calls it, so all **eleven**
call sites across the Tech Watcher (filter, literature filter, synthesis), News
Analyzer, Filing Processor, Hypothesis Generator and the two research harnesses
are covered by construction rather than by remembering.

Three decisions worth keeping:

- **Tracing fails open.** Every failure is caught, logged `llm.trace_failed` and
  swallowed. The Risk Officer fails *closed* for the opposite reason; here, a
  tracer that raised would let an observability outage stop the Tech Watcher
  from filtering, trading a real capability for a bookkeeping one.
- **Calls carry a `task` name**, not just a tier — `tech-watcher.filter`,
  `filing-processor.classify`. llm-routing.md slices the migration sample "by
  task type", several unrelated jobs share one tier, and a trace named after the
  tier cannot be sliced back apart. The local pass and its cloud escalation
  deliberately share a task name so the two models land in one comparable
  sample; the tier distinguishes them.
- **Clipping is declared.** Fields over `LANGFUSE_MAX_FIELD_CHARS` are marked
  `input_truncated` / `output_truncated` in the trace metadata. llm-routing.md
  asks for *full* input/output, and a silently shortened sample would satisfy
  the letter of that while corrupting the evaluation.

### Audited against Langfuse's own guidance, 2026-08-25

The official Langfuse skill (`.claude/skills/langfuse/`, vendored at
`ff47830`) prescribes an audit rather than a rewrite when instrumentation
already exists. Run against #208, it found four gaps, all since fixed:

| Gap | Fix |
|---|---|
| **No session grouping.** A 300-item filter pass produced 300 unrelated traces; nothing recorded that they were one run, so *"what did the 09:00 pass do"* could not be asked. | One `session_id` per pass, minted where the pass function already runs exactly once. Synthesis reuses its existing `batch_id` — the batch *is* the session. |
| **The escalation read as two units of work.** The Filing Processor and News Analyzer score an item locally, then score the *same item* on a cloud tier. Those landed as two unrelated traces. | Callers mint one `trace_id` per item and pass it to both legs, so the escalation is a second generation under the first's trace. |
| **Names were not verb-first.** Langfuse asks for active language at low cardinality; `tech-watcher.filter` leads with a noun. | Renamed throughout: `filter-world-changer-item`, `score-filing-item`, `score-news-item`, `synthesize-candidate`, `propose-hypothesis`, `evaluate-model-candidate`, `evaluate-archetype-bar`, `filter-literature-item`. Run-specific values stay in metadata, never the name. |
| **Masking not assessed.** | Assessed and deliberately not implemented — see below. |

**Masking: assessed, not implemented.** What reaches the LLM layer is
public-source text — SEC filing bodies, news headlines, arXiv abstracts, and the
firm's own prompts. No credential passes through it; ADR-0003 confines broker
keys to broker-facing containers and this is not one. Two changes should reopen
the question: a prompt built from anything user-supplied, or a Langfuse instance
reachable by anyone but Mike.

**The one step of the skill's workflow that could not be run** is its required
self-audit loop — execute the path, fetch the trace back, check it against the
live guidance, repeat. That needs API keys and a reachable Langfuse. It is the
first thing to do after the keys exist, and until then no one has *seen* a
Shrap trace.

**The audit also surfaced [KI-032](#ki-032--the-langfuse-server-is-end-of-life-and-that-locks-out-every-current-client):** the deployed
server is end of life, and no current Langfuse SDK can talk to it.

### What remains, and why this is not closed

**Nothing is traced until Langfuse issues API keys, and only Mike can do that.**
They are created per project from the Langfuse UI (Settings → API Keys), not by
the compose stack. Until `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set
in `infra/.env`, every agent logs `llm.tracing_disabled` at startup and behaves
exactly as it did before — which is the state this issue describes.

Closing it needs the check in `docs/runbooks/dell-bootstrap.md`: keys set,
containers recreated, and **a trace visible in the Langfuse UI**. The firm has
marked things done on the strength of merged code before; `make doc-drift`
called every status doc `ok` on a day the handoff said "Orders: none yet" and
six had filled. Merged is not running.

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

## KI-020 — A service can read a table whose migration another service has not run yet

**Status:** Open. Hit on 2026-07-29 deploying the three-account work (#124–#128).

Each service applies its own migrations in `ensure_schema()` at startup, and
several services **read** tables they do not own. There is no ordering guarantee
between them, so a reader started before its writer queries a column that does
not exist yet.

Concretely: the Reconciliation Agent reads `trading.paper_order_events`, whose
`account_id` column is added by **paper-order-store**'s `ensure_schema`. The
deploy runbook said to start the reconcilers alone first, so they crash-looped
every 30 seconds on

```
asyncpg.exceptions.UndefinedColumnError: column "account_id" does not exist
```

The reader is not at fault. "Reader, never owner" is a deliberate invariant
here — the Tier-3 gate states it explicitly, and a reader that migrated tables
it does not own would be worse. The gap is that migration ordering is implicit
in container start order, which nothing enforces and which an operator can
reasonably get wrong.

Known readers of tables they do not own:

| Reader | Table | Owner |
|---|---|---|
| Reconciliation Agent | `trading.paper_order_events` | paper-order-store |
| Strategy Runner | `ops.account_snapshots` | Reconciliation Agent |
| Strategy Runner | `research.strategies` | Strategy Librarian / Evaluator |
| Pre-Trade Checker | `research.universe_tiers` | Universe Curator |

**Mitigation (in place):** the runbook now says to bring the whole stack up in
one command rather than a subset. That works because every reader retries — the
reconcilers recover on their own within 30s once the column exists.

**Not fixed:** the underlying design. Options when it next bites: a dedicated
migration step run once before any service starts (a `migrate` profile), or
readers detecting `UndefinedColumnError` and logging "waiting on <owner>'s
migration" instead of a traceback. The second is cheaper and makes the failure
legible; the first is correct. Neither is worth a card until it recurs — but the
Pre-Trade Checker row above is the one to watch, because its failure mode is a
veto rather than a retry.

## KI-021 — Escalation to a "stronger" tier calls the same model

**Status:** Open. Present since the seed; became firm-wide on 2026-07-27 and
survived the box-wide cloud move of 2026-07-30.

Three agents implement a two-tier pattern: score everything on a cheap bulk
tier, then re-ask a stronger tier about anything that scored material. The
News Analyzer and Filing Processor escalate at `materiality >= 2`; the Tech
Watcher filters on one tier and synthesises on another.

In every deployed case both tiers resolve to **the same model**:

| Agent | bulk tier → model | escalation tier → model |
|---|---|---|
| News Analyzer | `local-classification` → `gpt-oss:20b-cloud` | `cloud-default` → `gpt-oss:20b-cloud` |
| Filing Processor | `local-classification` → `gpt-oss:20b-cloud` | `cloud-default` → `gpt-oss:20b-cloud` |
| Tech Watcher | `local-classification` → `gpt-oss:20b-cloud` | `cloud-default` → `gpt-oss:20b-cloud` |

So escalation cannot change an answer except by sampling noise, while costing a
second inference on a metered endpoint and a second verdict-history row that
looks like independent confirmation and is not. **That last part is the real
damage**: `intelligence.news_verdict_history` and `filing_verdict_history` now
contain pairs of rows that read as "the cheap model said X and the strong model
agreed," which is evidence of nothing. Any future calibration over those tables
must treat an escalated pair as one observation, not two.

**How each got here, because the two are not the same:**

- The Intelligence pair **disclosed it**. Both specs say the escalation path
  "is wired but lands on the same local model until the compose env changes,"
  accepted at seed time under the local-only ruling.
- The Tech Watcher **did not**. `docs/infrastructure/llm-registry.md` described
  synthesis on `kimi-k3:cloud` while compose had already reverted it to
  `gpt-oss:20b-cloud` — that model needs a paid Ollama subscription the firm
  did not have. The doc was corrected on 2026-07-30; the collapse remains.

**The fix is one env var, gated on a fact nobody has checked.**
`SHRAP_INTEL_ESCALATION_MODEL` (Intelligence) and `SHRAP_SYNTHESIS_MODEL` (Tech
Watcher) each point escalation at a different model with an `.env` edit and a
recreate — no rebuild. What is missing is confirmation that a stronger model's
**usage tier** is inside the current subscription. `kimi-k2.5`, `kimi-k3` and
`gpt-oss:120b` are all in the public cloud catalogue; the catalogue does not
publish usage tiers, and an `OLLAMA_API_KEY` being present proves only that
requests authenticate, not that they are permitted. Conflating those two is
precisely what got `kimi-k3:cloud` reverted the first time.

**Until then, prefer honesty to theatre.** Either close the gap or disable
escalation, because paying twice for one opinion and recording it as two is
worse than a single scored pass. Disabling is a spec change to both Intelligence
agents (drift updates the spec, not the code), which is why it is a card and not
a config tweak.

## KI-022 — The Risk Officer is deployed and has never been exercised

**Status:** Open, found 2026-07-31 by the full systems check.

`risk.decisions` holds **one row** since the Officer shipped on 2026-07-30: an
`UNKNOWN_STRATEGY` veto at 00:01 on 2026-07-31. That is not a fault in the
Officer — it is the downstream consequence of no order flow. The last
`trading.paper_order_events` row is **2026-07-29 13:32**, two days before the
check, and the firm holds 0 promoted and 0 live strategies against 12 killed.

Two things this makes easy to misread, both worth stating plainly:

- **`risk.intent.approved` (28) and `risk.intent.vetoed` (14) are the Pre-Trade
  Checker's streams, not the Risk Officer's.** Reading 42 events there as
  evidence the Officer is working is wrong, and was nearly recorded as such.
- **Built is not exercised.** The limits in the Officer are unruled first cuts
  and no live decision has tested any of them. The roadmap listing 2.7 as
  complete is accurate about the code and says nothing about the behaviour.

Not urgent while nothing trades; becomes urgent the moment anything does.

## KI-023 — Every paper order has a blank `account_id`

**Status:** Closed 2026-07-31 — historical rows, not a defect. Found the same
day by the full systems check.

All 141 rows in `trading.paper_order_events` carry an empty `account_id`,
across every status (`filled` 47, `accepted` 46, `new` 23, `pending_new` 23,
`rejected` 2). The three-account split shipped in #124–#128 and ADR-0017 rests
on per-account attribution — *"per-strategy P&L becomes that account's equity
curve."* With a blank column that derivation is not available for any order the
firm has ever placed.

Two candidate causes and they need different fixes: the writer never populates
the field, or every recorded order predates the three-account work and nothing
has flowed since. The last order is 2026-07-29 and the account cards merged
2026-07-29, which makes the second explanation plausible and unproven.

**Diagnose before building:** place one order through the new path and check
whether `account_id` lands. Do not repair historical rows until the writer is
known good.

### Resolved 2026-07-31 by reading the code, not by placing an order

The second explanation is right, and it did not need a live order to establish.
The field is carried at every hop — `research.strategies.account_id` → the
Strategy Runner groups by it → the decision maker → the Execution Agent →
`order_store.py`, which reads `payload.get("account_id")`. More decisively, the
Runner **refuses to trade an unassigned strategy** rather than defaulting it:

> `no account_id — assign one with shrap-strategy-stage assign-account, or it
> will never trade` — logged at ERROR and dropped, "permanent until a human
> acts."

So under current code an order with a blank `account_id` **cannot be produced**.
The 141 existing rows necessarily predate #124–#128, and the reason none have
appeared since is simply that nothing is trading — the firm holds 0 promoted
strategies against 12 killed.

**No repair is planned.** The historical rows are an accurate record of orders
placed before accounts existed; back-filling them with a guessed account would
make the trail less true, not more. **This closes as a non-defect**, and the
useful residue is the check itself: when the first strategy is promoted, confirm
`account_id` lands on its first order. That is a one-query verification, not a
card.

## KI-024 — Nothing automatically ingests price bars

**Status:** Fix shipped 2026-07-31 (`market-data-trigger` + a freshness target);
pending Dell deploy and first live sweep. Found 2026-07-31 by the full systems
check.

`market_data.daily_bars` held 50 tickers and 72,447 rows with
`max(session_date) = 2026-07-29` — two sessions stale on 2026-07-31. The cause
is structural rather than a failure: **`market-data` is a `--profile tools`
service**, so bars advance only when a human runs the backfill.

The consequence is quiet and compounding. Every evaluation, every Runner pass
and every regime classification since 2026-07-29 read a panel that stopped
advancing, and **the Evaluator's most common verdict is `hold-for-data`** — 13
of 22 under protocol 0.2, against 9 kills. How many of those are a real data
limit and how many are an un-run backfill is currently unknowable, which is the
part that matters: a verdict that says "not enough data" is indistinguishable
from one caused by nobody running a job.

**Mitigation (shipped 2026-07-31).** `market-data-trigger` is an always-on
service that sweeps every six hours. Three choices in it are worth recording
because each was a fork:

- **Tickers come from the Curator's Tier-3 launch list**, the same source
  `--launch-list` already resolves — not from its own config, which would be a
  second source of truth that silently diverges from the universe.
- **The window is computed per ticker from what the store already holds**, so a
  sweep asks for the gap rather than five years. Per-ticker rather than one
  global maximum on purpose: a name added to the universe later has no history,
  and a global maximum would start its series at today and leave it permanently
  short — a gap that would only surface as an inexplicably thin backtest months
  later.
- **No market-calendar awareness.** A Sunday sweep asks for a window containing
  no sessions and writes nothing, costing one request per ticker. Coupling this
  to the Market Phase Scheduler would save a request that does not matter, and
  the failure mode of a wrong calendar — silently skipping a real session — is
  much worse than a wasted call.

**The alarm ships with it, and that is the point.** `DEFAULT_TARGETS` gains
`market_data.daily_bars` on `fetched_at` (refreshed by the upsert on conflict,
so it measures sweep liveness and is weekend-independent) with an 18-hour
threshold — three missed sweeps. A trigger that dies quietly is the failure this
card exists to end, so shipping the service without the check would reproduce
KI-024 with extra steps.

The test that previously asserted `daily_bars` was *deliberately not* a target
now asserts the inverse, with the reasoning: a table earns a freshness target
exactly when something is supposed to be writing to it on a schedule. The old
exclusion was correct when written and became a blind spot when the premise
changed.

**Still open until verified live**, and one question this does not answer: how
many of the 13 `hold-for-data` verdicts were the stale panel and how many are a
real data limit. Re-running those evaluations against a current panel is the
only way to find out, and it is a separate card.

## KI-025 — Redis streams grow without bound

**Status:** Both halves shipped 2026-07-31 — the producer fix (#178) and
retention (this card); pending Dell deploy. Found 2026-07-31 by the full systems
check.

Nothing trims any stream. Measured lengths:

| stream | length |
|---|---|
| `ops.health-tick` | 80,509 |
| `operations.reconciliation-discrepancy` | 11,096 |
| `operations.reconciliation-completed` | 9,161 |
| `intel.regime.tick` / `intel.regime.sizing-modifier` | 6,995 each |
| `ingestion.heartbeat` | 2,471 |

Redis persists to disk here (`appendonly yes`), so this is a slow capacity
problem rather than a memory one, and it is not urgent today — the Dell runs
every agent at 0.00% CPU and ~40 MB against 31 GB. It becomes urgent silently.

**Two separate issues, and the second is the interesting one.** The first is
retention: high-frequency heartbeat streams want `MAXLEN ~` on publish. The
second is that **discrepancies outnumber clean reconciliations** — 11,096
against 9,161, meaning a majority of reconciliation passes report a problem and
**nobody has ever read one**. That is either real book drift or a check so noisy
it cannot be acted on. Both are worth knowing; neither is currently visible.

Diagnose before trimming: `XRANGE operations.reconciliation-discrepancy - + COUNT 5`
answers which it is in one command, and trimming first would destroy the
evidence.

### Diagnosed 2026-07-31 — the answer was neither, and the fix is not retention

The payload:

```json
{"kind":"missing-in-store","broker_status":"filled","stored_status":null,"symbol":"SPY"}
```

**The comparison was correct and the reporting was wrong.** Mike cancelled the
account's original test orders by hand in the Alpaca UI. Those orders were never
Shrap's, so `trading.paper_order_events` correctly has no row for them, and
`compare_orders` correctly reported a divergence. Then the agent re-announced
that same understood divergence **every 300 seconds, from three agents, for
weeks** — because `agent.py` published one event per discrepancy per pass with
no state at all.

So **11,096 was a re-report count, not an incident count**, and the number said
nothing about how many orders were actually involved. Neither of the two
hypotheses above was right: not book drift, and not a noisy predicate.

**Fix shipped 2026-07-31:** the two streams now make the distinction they were
already halfway to making.

- `operations.reconciliation-completed` stays **level-triggered** — it already
  carried `discrepancies` and `clean` on every pass, so current state remains
  observable and a divergence that clears needs no event of its own.
- `operations.reconciliation-discrepancy` becomes **edge-triggered** — a new
  divergence is news, the same divergence next pass is not.

That collapses the stream from *time times divergences* to just *divergences*,
and restores the property an alarm needs: an event in it means something
changed. The tracker is process-local and resets on restart, mirroring the
Health Monitor's transition state, so a fresh process re-states what it sees
rather than silently inheriting a baseline it never observed. Suppression is
logged (`reconciliation.discrepancies_unchanged`) rather than silent — a quiet
stream should be evidence that nothing changed, not evidence that the agent
stopped looking.

**Retention shipped 2026-07-31 as the smaller half.** The Audit Logger now trims
every stream it discovers to `MAXLEN ~ 25,000` on an hourly cadence
(`AUDIT_LOGGER_TRIM_INTERVAL_SECONDS`, 0 disables).

It lives there rather than in a monitor or a new service because the Audit
Logger already enumerates every stream and is the component that moves events
into `ops.audit_events`. That makes the framing explicit rather than implied:
**under ADR-0006 the bus is how events travel and the audit table is where they
are kept**, so trimming Redis discards a delivered copy, not the record.

The cap is a growth bound, not a retention decision — 25,000 is ~8.7 days of
`ops.health-tick` and ~9.6 days of `reconciliation-completed`, the two noisiest
legitimate producers, and no other stream comes close. It deliberately does not
trim to consumer position: `XTRIM` ignores group offsets, so a generous cap is
safe without depending on lag staying at zero, and a consumer more than 25,000
entries behind has a problem retention should not be papering over. Lag was
measured at **0 on every stream** with the Audit Logger 2 seconds behind and
118,344 rows persisted, so nothing trimmed is lost.

**Retention does not fix a stream growing because a producer republishes
forever.** That is a producer bug and trimming it hides the evidence, which is
exactly what would have happened here had this shipped before the diagnosis.
`operations.reconciliation-discrepancy` was fixed at the source in #178.

**One thing this does not do:** resolve the underlying divergence. The broker
still holds orders the store has no row for, and it always will — they were
never Shrap's. They age out of the agent's 7-day lookback window on their own.
A mechanism for acknowledging a known-benign divergence is deliberately *not*
built, because the only one on record resolves itself and building an
acknowledgement workflow for a single self-clearing case would be scaffolding.

## KI-026 — The Tech Watcher reads EDGAR's index, not its filings

**Status: RESOLVED 2026-08-03 (#189).** All 3,696 EDGAR items carry a document
body; average length went from 179 characters to 5,834, and 3,689 of 3,696
parsed into labelled item sections. The re-score under `--force` produced the
46 admits that closed KI-009.

Two limitations recorded rather than hidden: **86% of bodies hit the 6,000-char
cap**, and truncation is logged but not stored, so a verdict on a clipped filing
cannot be told from one on a whole filing by reading the row. Raising
`--max-chars` and re-scoring is a cheap second experiment if a result ever turns
on it.

Original diagnosis below.

`research.raw_source_items` holds 3,740 `sec-edgar` items — **72% of the whole
corpus** — and every one stores the Atom *index entry* rather than the filing:

```
<b>Filed:</b> 2026-07-31 <b>AccNo:</b> 0001193125-26-328866 <b>Size:</b> 565 KB
<br>Item 8.01: Other Events <br>Item 9.01: Financial Statements and Exhibits
```

A filed date, an accession number, **a file size in kilobytes**, and the item
codes. There is no fact in it about the company, the money, or the event. The
filter has been correctly rejecting document metadata 3,740 times, and every
model tested agreed because every model was right.

**The comparison that makes it unambiguous.** `usaspending` summaries average
*fewer* characters (147 vs 179) and admit at roughly **14%**, because they state
a recipient, an amount and a purpose — *"$900,000,000 … to establish new annual
domestic commercial HALEU capacity"*. Across all three bars of the experiment,
`sec-edgar` admitted **0 of ~425 scored**. Length is not the discriminator;
content type is.

**The capability already exists in the firm.** The Filing Processor fetches
EDGAR full text into `intelligence.filings.full_text` and scores per item code
with item-code priors (PR #66/#68). Two legs read EDGAR; one of them reads the
document.

**Scope of the card:**

1. Fetch filing text for the Tech Watcher's EDGAR leg, reusing the Filing
   Processor's fetcher rather than writing a second one.
2. **Strip HTML.** `sources.py` calls `_strip_html` for some sources and not for
   the EDGAR leg, so the model is currently handed `<b>` and `<br>` tags.
3. **Keep the item codes and use them.** *"Item 1.01: Entry into a Material
   Definitive Agreement"* is meaningfully different from *"Item 8.01: Other
   Events"*, and the Filing Processor already has priors for exactly that.
4. Bound the text — full 10-Ks are large and the filter prompt is ~1,400 tokens
   today. Decide a budget rather than inheriting one.

**Sequencing note:** this multiplies the judgeable corpus by roughly four. Do it
*before* re-running the bar experiment or drawing further conclusions about the
taxonomy, because every rate measured so far has a denominator that is 72%
metadata.

## KI-027 — `hold-for-data` is a verdict that cannot resolve

**Status:** Open, measured 2026-08-01.

Fourteen of twenty-six evaluations sit at `hold-for-data` / `below-sharpe-floor`.
The verdict means "a real-looking but sub-floor edge — wait for more data." On a
five-year daily-bar window that grows five bars a week, the metrics will not
move. The status reads *not yet* and functions as *never*.

The re-evaluation floor in `trigger_service.py` prevents duplicate rows, so this
costs nothing operationally. What it costs is honesty: the registry's most
common verdict describes a wait with no exit condition, and a reader counting
"live candidates" will count fourteen strategies that are not candidates.

**What the numbers actually say** (2026-08-01, `research.evaluations`):

| verdict | reason | n | sharpe | benchmark | IR |
|---|---|---|---|---|---|
| hold-for-data | below-sharpe-floor | 14 | 0.792 | 0.876 | 0.306 |
| kill | no-active-edge | 6 | 0.666 | 0.758 | -0.156 |
| kill | insufficient-trades | 3 | 0.334 | — | — |
| kill | no-edge | 2 | -0.379 | 0.936 | -0.964 |
| kill | worse-than-parent | 1 | 0.690 | 0.772 | 0.158 |

The 14 hold-for-data strategies earn a *higher mean return* than their benchmark
(positive IR) with proportionally more volatility (lower absolute Sharpe). They
add return by taking risk.

**Priced against the trivial alternative:** a strategy that is the benchmark
levered by *k* has an active series of `(k-1) x benchmark`, so its information
ratio is `mean_b/sd_b` — the benchmark's own Sharpe, **0.876**, at any leverage.
These score **0.306**. They are not marginal-but-real; they are worse than
turning up position size on the basket they already trade. (Real leverage costs
borrow and the Risk Officer bounds it, so 0.876 is an idealised ceiling. It does
not survive being off by a factor of three.)

**The gate is therefore honest and tradability is a build problem.** Lowering
either floor would promote strategies a leverage dial beats. Recorded because
the opposite conclusion is the tempting one when nothing has ever passed.

**Fix:** rename the verdict to something that does not promise a resolution
(`measured-insufficient`), or give it an explicit expiry after which it becomes
a kill. Not a calibration change.

## KI-028 — The Sharpe floor is checked before the information-ratio floor

**Status:** Open, latent, found 2026-08-01. Mislabelling nothing today.

`verdict.py` tests `base_sharpe < sharpe_floor` **before**
`information_ratio < information_ratio_floor`. `required_information_ratio`'s own
docstring argues the case against that ordering:

> Sharpe measures the market plus the strategy; on a window where the benchmark
> itself returned 0.772 a long-only equity rule inherits most of its Sharpe from
> beta … The information ratio is the part that is actually the strategy's.

So a strategy with IR 2.0 and Sharpe 0.95 is held on `below-sharpe-floor` —
penalised for the market's mediocrity rather than its own, which is the exact
failure the IR gate was added to prevent, still live one line above it.

**Latent, not active.** No strategy in the current 26 has that shape: the 14
holds fail *both* floors (IR 0.306 against a 0.5 floor), so removing the Sharpe
check would change the reason string and not one verdict. Recorded so the first
genuinely good low-volatility strategy does not get a reason that points at the
wrong gate.

## KI-029 — The Decision Maker dropped the strategy from every intent

**Status:** RESOLVED 2026-08-03 (#190), same day it was found.

The forward test's first session: both strategies computed targets, 20 buy
signals were emitted, **all 20 were vetoed `UNKNOWN_STRATEGY`**, zero orders
reached the broker.

`strategy_ids` was a hardcoded `[]` in `decision_maker_stub.py` — a Card 2
placeholder from when the only producer was the Strategy Fixture and nothing
downstream read the field. #146 made it load-bearing: an intent with no
`strategy_ids` cannot be resolved to an account, so the Risk Officer refuses it
before evaluating a single limit. `"account_id": null` on every veto line is the
tell — that field is populated only after the registry lookup those intents
never reached.

The Runner had always sent `strategy_id`. The Decision Maker read `account_id`
off the same payload three lines above, under a comment explaining why dropping
*that* would unroute every order, and dropped the strategy.

**Why 1503 tests passed against a chain that could not place an order.** The
integration test named for the path — `test_signal_to_risk_path` — exercises
`RiskPolicy`'s universe and quantity checks, not the Risk Officer's attribution,
because it predates #146. The seam had no coverage and the field's value was
never asserted anywhere.

**The general lesson, and it is not about this field.** A placeholder written
when nothing reads it becomes a defect the moment something does, and nothing in
the type system, the linter or the suite marks that transition. When a card
makes a previously-ignored field load-bearing, its *producers* are part of that
card's scope. Grep for the field, not just for the consumer.

## KI-030 — The Runner sold positions the account never held

**Status:** RESOLVED 2026-08-04, same day it was found in production.

The forward test's second session opened three **short** positions on
long-only strategies: COIN −1, UUP −6, RIVN −12 on `PA3KQN57WVXY`. Nothing had
shorted anything. The firm sold stock it did not own.

**Mechanism.** The Runner decided "am I invested" from
`strategy_runner_state.last_target` — its own record of *intent* — and sized
exits from `last_quantity`. Intent is not position, and two things break the
equivalence:

1. **A vetoed order.** Monday's 20 signals were all refused `UNKNOWN_STRATEGY`
   (KI-029) and produced no orders at all. The Runner had already stamped them
   as held. Tuesday the strategy rotated out and sold them.
2. **A scaled order, which is every order.** The Risk Officer applies
   `stage_fraction x regime_multiplier` — 0.25 x 0.75 = **0.1875** at the time.
   A recorded intent of 52 GME became a 9-share fill. Closing on 52 would sell
   the 9 and short 43.

The second is the severe one. It needs no veto, no outage and no edge case: it
fires on **every exit**, shorting roughly 81% of the intended size, and it would
have done so on 14 more positions the same week.

**The engine already knew.** It guards the sizing-failure case verbatim —
*"An entry that could not be sized did not happen. Record it as flat: storing
the invested weight would make next session read this as an exit and emit a
sell for a position the firm never opened."* The guard was correct and its
reasoning general; only its trigger was narrow. Everything downstream of the
signal was invisible to it.

**Fix (#192).** `prev_inv` and the exit quantity now come from
`ops.position_snapshots` — what the broker reports, not what the Runner hoped.
ADR-0017 is what makes that cheap: one strategy per account, so the account's
positions *are* the strategy's positions and there is nothing to derive. The
table has existed since the Risk Officer needed a book to measure; nothing read
it.

Three consequences worth stating:

- **An account with no reconciliation pass is deferred, not assumed flat.** The
  `__FLAT__` marker row is what distinguishes "the pass ran and found nothing"
  from "no pass has run", and those must behave oppositely.
- **A short on a long-only strategy is skipped and reported**, never sold — a
  sell would deepen it. A human decides what to do with a book the firm did not
  choose.
- **`last_quantity` survives as an audit trail of intent.** Comparing it against
  the position is now a reconciliation signal rather than a trading input.

**This is KI-005 arriving.** That issue deferred position-state derivation
"until the first Research strategy needs portfolio state," and noted ADR-0017
"dissolves most of this rather than solving it." A real strategy needed
portfolio state on its second day of trading, and the dissolution had never been
wired.

**The generalisable form.** A value that is *usually* equal to the thing you
want is not that thing. `last_quantity` equalled the position for as long as
nothing between the signal and the fill could change it — and the moment a Risk
Officer shipped, it silently stopped, with no type change, no test failure and
no error. When a card inserts a stage into an existing pipeline, the question is
not only "does the new stage work" but "what did anything downstream already
believe about what reaches it."

## KI-031 — The status loop stalled a month on the firm's first order

**Status:** RESOLVED 2026-08-04 in #193; found the same evening as KI-030.

`trading.paper_order_events` recorded all six of 2026-08-04's orders as
`pending_new` with no `filled_quantity`, while Alpaca showed all six **filled**
hours earlier. The firm's own record of what happened to its orders had been
frozen since 2026-07-29.

**Not where it looked.** The Order Store was the obvious suspect and was
blameless: its row counts match the Redis streams exactly — 33 `submitted`, 67
`status-updated`, 47 `filled` — so it had persisted every event ever published.
The Execution Agent had simply stopped publishing two of the three.

**Mechanism, and it is two bugs stacked.**

1. **The account filter read "unstamped" as "mine."** It was
   `if order_account and order_account != account_id`, so an event carrying no
   `account_id` fell through the guard entirely. Every submitted event published
   before `fcf8d90` (2026-07-29, account stamping) is exactly that.
2. **A 404 was classified as systemic.** `HTTPStatusError` is not `ValueError`,
   so it landed in the branch that deliberately does **not** ack — correct for a
   broker outage, fatal here. The event was redelivered every ~6 seconds
   forever, and the `break` meant the batch never advanced past it.

Together: the three-agent split created fresh consumer groups that replayed
`execution.order.submitted` from the start. Each agent immediately reached the
firm's first-ever order — stream id `1783203414014-0`, **2026-07-04 22:16:54**,
matching `min(occurred_at)` in the table to the millisecond — claimed it for
want of a stamp, 404ed on a book it did not own, and jammed there. Every later
event, including six real fills, sat behind it.

**Fix (#193).** Strict account equality, so an unstamped event is skipped by
every agent rather than claimed by all of them; and `is_unknown_order_error`,
which acks and skips a 404 while leaving 401/429/5xx on their retry. The
re-poller had the same misclassification and now drops rather than re-asking for
the life of the process. **No manual unjam is needed** — on deploy the poison
event is redelivered once, skipped, acked, and the backlog drains, so the six
fills get their rows retroactively.

**Cost, stated honestly.** Nothing was lost and no trade was affected:
submission runs in a separate loop, which is why the orders went through at all,
and the broker still holds the truth. What was dead is the firm's ability to
measure its own execution — fill rate, slippage, whether an order ever
completed. It also means the pre-existing "no order since 2026-07-29" note hid
this: there were no orders to fail on between the split and 2026-08-04.

**The generalisable form.** *An error that cannot succeed on retry is not a
retryable error.* The retry-vs-skip split is the load-bearing decision in every
one of these loops, and both branches were already written and commented here —
the 404 was just filed under the wrong one. Note also that the identical defect
class was found and fixed in the **risk** loop on 2026-07-06 (see
`test_execution_agent_poison.py`, whose docstring describes a 422 replay jamming
that loop the same way). The lesson generalised across loops; the fix did not.
When a poison-pill class is found in one consumer, audit every other consumer in
the same file before closing the card.

## KI-032 — The Langfuse server is end of life, and that locks out every current client

**Status:** Open, found 2026-08-25 while auditing #208 against Langfuse's own
instrumentation skill. Not blocking today. It is a decision for Mike, not a bug.

`infra/docker-compose.yml` pins `langfuse/langfuse:2`. Langfuse's self-hosted
compatibility matrix says what that costs:

| Client | OSS v2 (deployed) | OSS v3 | OSS v4 |
|---|---|---|---|
| Python SDK v4 (current, 4.14.5) | **Unsupported** | ≥ 3.63.0 | Full |
| Python SDK v3 | **Unsupported** | ≥ 3.63.0 | Deprecated |
| Python SDK v2 | Full (deprecated) | Full | Unsupported |
| OpenTelemetry `/api/public/otel/v1/traces` | **Unsupported** | ≥ 3.22.0 | Full |
| Legacy `/api/public/ingestion` | **Full** | Full | Unsupported |

**OSS v2 is marked "End of life"** — no security patches.

Three consequences, in the order they matter:

1. **#208 is built the only way it could have been.** The direct ingestion
   client is not a shortcut taken to avoid a dependency; on this server the
   recommended SDK cannot connect at all. Anyone reading `llm/tracing.py` and
   wondering why it is not `from langfuse import Langfuse` should stop here.
2. **The OTel path does not exist below server 3.22.0.** Langfuse is steering
   all instrumentation towards it and deprecating the ingestion endpoint that
   this firm depends on. The endpoint keeps working on self-hosted until a v4
   upgrade, so nothing breaks by surprise — but the migration is one-way and
   the firm is on the wrong side of it.
3. **An upgrade is an infrastructure card, not a version bump.** v3 adds a
   worker container, **ClickHouse**, an **S3/blob store**, and Redis. That is
   three to four new stateful services on a Dell already running 34 containers,
   with the backup surface (`docs/runbooks/dell-bootstrap.md` §7) growing to
   match.

**Recommendation: not this sprint.** Tracing works on v2, the sample the Month-4
migration needs accumulates through the legacy endpoint, and the binding
constraint remains research throughput. Revisit when either the firm wants
OTel-based instrumentation or the EOL security posture stops being acceptable —
and treat it as its own card with its own backup plan, not as a dependency of a
tracing change.

## KI-033 — No position smaller than one share could ever be closed

**Status:** Fixed 2026-08-25 (#211). Found by asking why the broker showed
fractional positions that never went away.

`PreTradeChecker._parse_requested_quantity` rejected any fractional quantity as
malformed. The docstring said so plainly:

> *"Parse quantity strictly; fractional values are vetoed, not rounded. The risk
> gate should be conservative. A fractional share/order quantity is rejected as
> malformed in this Month 1 stub instead of being silently floored or rounded."*

**That was correct when it was written.** Every order was a whole number of
shares, so a fraction arriving at the gate genuinely did mean something upstream
had broken.

**#195 made fractional orders the normal case** and nothing revisited the
declaration. The Risk Officer scales every order by `stage_fraction x
regime_multiplier` — 0.1875 today — so a one-share intent is *supposed* to arrive
as 0.1875 shares. And because this gate runs **upstream of the Risk Officer**,
none of the fractional arithmetic added in #195, #196, #198 or #199 ever ran for
these orders. Every one of those cards was correct and unreachable.

### What it looked like from outside

Positions that never went away. Six names sat in the book for weeks:

| Ticker | Held | Value |
|---|---|---|
| `U` | 0.012648483 | $0.57 |
| `XLE` | 0.023641439 | $1.48 |
| `DKNG` | 0.060078927 | $1.55 |
| `PYPL` | 0.03287553 | $2.04 |
| `NIO` | 0.709543569 | $3.17 |
| `MARA` | 0.448764161 | $5.45 |

The Runner emitted an exit for each, every session. The gate truncated the
quantity to `0`, and `0` then failed the positivity check as `INVALID_QUANTITY`.
**52 refusals** sat in `risk.decisions` with `requested_quantity = 0`, and no
order was ever submitted — which is why the order table showed nothing at all
for those names after 2026-08-19 and made it look like the Runner had stopped
trying.

**It was never only about residues.** `int(0.1875)` is 0 too, so `RIOT` at
0.1875 and `MARA` at 0.75 — positions the firm opened deliberately and sized
correctly — were equally untradeable. Since #195 the firm could open positions
it was structurally incapable of closing.

### Four wrong hypotheses first, and why

Before finding it, the session proposed: the Runner never emits the sell; the
Runner's state machine thinks it is already flat; Alpaca rejects the order under
its $1 minimum and the firm retries daily; the residues are ordinary scaled
positions. Every one was wrong, and every one was *downstream* of a gate that
refused the order before any of them could apply.

The query that settled it was two lines against `risk.decisions`, and it should
have been the first thing run rather than the fifth. **When an order does not
appear at the broker, read the refusal table before theorising about the
broker.**

### The pattern, for the fifth time

From CLAUDE.md, written before this instance: *when a card changes a type or
inserts a stage, ask what downstream already declared about what reaches it.*

- `last_quantity` reconstructed the position from intent (#192).
- `market_value / latest_close` reconstructed a share count that was recorded
  directly (#198).
- An `INTEGER` column reconstructed a fractional quantity as a whole one (#199).
- **A gate declared quantities whole and refused the ones that were not (#211).**

### Also fixed in #211

- **`research.strategy_runner_state.last_quantity` was still `INTEGER`** — the
  last surviving column of the #199 family. It read `0` next to broker positions
  of `0.0126`. Widened, with the same guarded migration, *and* the `int()` cast
  on the read path removed — widening the column alone would have put the
  narrowing back one layer down.
- **Sub-minimum exits now refuse with `BELOW_BROKER_MINIMUM`.** With the
  truncation fixed, a $0.57 residue would be approved and submitted to a broker
  that rejects fractional orders under $1 — trading a silent gate refusal for a
  silent venue rejection, daily, which is the #193 shape again. The refusal names
  the remedy: dust that small has to be cleared by hand in the dashboard.
- **`TargetState.last_quantity`'s docstring** still described exits sizing from
  it, which #192 stopped doing.

### What this does not fix

The five names above $1 should clear on the next session that runs. **`U` at
$0.57 will not** — it is below the broker's fractional minimum and needs
clearing by hand in the Alpaca dashboard. That was already on Mike's list; it is
now the only part of this that stays manual.

## KI-034 — The backup runbook points at a directory that does not exist

**Status:** Doc fixed 2026-08-25 (#211). **Whether backups have ever run is
unverified and Mike's to check.**

`docs/runbooks/dell-bootstrap.md` gave the deployment path as
`/mnt/Apps/shrap_firm` in seven places. The deployment is at
`/mnt/Archive/shrap/shrap_firm` and appears always to have been.

Two of those seven were prerequisites and clone instructions, which is
harmless — anyone following them once would have noticed. **Three were the
nightly backup cron entries:**

    0 2 * * * docker compose -f /mnt/Apps/shrap_firm/infra/docker-compose.yml ...

If those were installed as written, every one of them has failed at
`docker compose -f` on a missing file, nightly, since Month 1. Cron mails
failures to the local user by default and nothing on this host reads that
mailbox — the same silence that hid KI-010's dead ingest leg for 18 days and
every one of the trading defects.

**The doc is fixed. The crontab is not** — a doc change cannot repair an
installed cron entry. Check it:

    crontab -l | grep shrap
    ls -la /mnt/backups/

An empty `/mnt/backups`, or dumps with old timestamps, means the Postgres and
Langfuse logical dumps the runbook promises have never existed. Given that
`pg_data` holds every order, verdict and equity point the firm has produced,
that is worth ten minutes today.
