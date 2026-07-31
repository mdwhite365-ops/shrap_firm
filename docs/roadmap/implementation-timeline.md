# Implementation timeline

**Last updated:** 2026-07-31 (`main` at #175; #129–#175 were missing from this file)
**Supersedes** `docs/roadmap/paper-spine-tree.md` as the answer to "what's next."
That document is the Month-1/2 paper-spine plan; its last card (Card 18) shipped
weeks ago and it has not tracked anything since.

**Time remaining:** roughly four to five weeks. The sprint ends when classes
start (late August 2026). Mike has 1–2 hours/day, spent reviewing rather than
implementing.

This file exists because "what's next" was being reconstructed from four
documents each session, and one of them was stale enough to produce a command
block that could not run (2026-07-27). One ordered list, dependency-first.

---

## Status, 2026-07-31

**Phase 0 and Phase 1 are done. Phase 2 Track A is done. Track B is partly
done.** See `docs/status/session-handoff.md` for Mike's rulings and what a fresh
session should pick up.

Landed #102–#118: archetype-conditional gates · the Evaluator trigger with
ADR-0015's kill-asymmetric autonomy · CI on every push · the full-firm audit ·
`shrap-strategy-stage` · **benchmark-relative evaluation** · cross-sectional
rules · the momentum seed · **notional sizing, arithmetic and wiring**.

**Landed #129–#175, and this file did not know it until 2026-07-31.** Three
paper accounts · information ratio and panel coverage in the verdict · the
ragged price panel · protocol 0.2 · strategy lineage and multiple-testing
correction · **the Risk Officer (#146)** · **the research ledger (#149)** ·
four documented factor effects · **the firm proposing its own strategies
(#156) with an autonomous trigger (#163)** · arXiv q-fin ingest · box-wide
cloud routing · **the first shadow eval, and the `qwen3.5:397b` promotion it
produced (#170–#175)**.

> **Two items below were listed as pending while already shipped**, which is
> what a 46-PR documentation gap does to a roadmap. Both are marked **DONE**
> in place rather than deleted, because an ordered plan that quietly loses its
> completed rows stops being auditable. See `recent-changes.md` for the full
> backfill and `make doc-drift` for the check that should prevent a fourth one.

**The scope changed on 2026-07-29.** ADR-0016 commits the firm to three asset
classes — equities, MES futures, spot crypto — operating continuously. Read it
before planning anything in Phase 2 Track B; several items below were scoped for
an equities-only, daily-bar firm.

**And a regulatory correction changed the order.** FINRA's PDT rule was
eliminated effective 2026-06-04, so intraday equity trading at $10k is available
now. What replaced it — continuous intraday margin requirements — is a Risk
Officer problem that applies immediately, because margin is reachable today and
the firm has no leverage bound in code.

**Next, in order:** the archetype bar experiment (1.4 — the funnel has admitted
**nothing** across 2,472 v4 verdicts, and five models across four usage tiers
proved that is not a model problem) → forward-test scoring (nothing evaluates a
strategy *after* promotion — more urgent under ADR-0016, not less) → intraday
bars (2.8) → Runner firing intraday (2.9) → intraday equities (2.10).

Risk Officer limits (2.7) have **shipped** (#146) and are removed from this
ordering. They are also essentially unexercised: the Officer has recorded one
decision ever, because no order has flowed since 2026-07-29 — see KI-022.

---

## How to read this

Each item names what it unblocks. **Order is by dependency, not by
preference** — an item's position is a claim that the things below it get worse
or impossible if it moves later.

Status vocabulary matches `docs/agents/README.md`: **Deployed** = running on the
Dell · **On-demand** = built, invoked by hand · **Spec only** · **Not built**.

---

## Phase 0 — Correctness debt (do first; hours, not days)

Nothing below is a feature. Every item is something the repo currently asserts
that is not true, and the repo is supposed to be the truth.

| # | Item | Why now |
|---|---|---|
| 0.1 | **Fix the `research.strategy.registered` claim** in `docs/agents/research/strategy-evaluator.md` | It says the stream has a producer. It does not — see KI-017. Written by me in PR #103 and merged. A future card would build a subscriber for a stream nothing publishes. |
| 0.2 | **Mark ADR-0013, ADR-0014, ADR-0015 `Accepted`** | All three merged; merge *is* acceptance in this project. Leaving them `Proposed` makes the decision record lie about what has been decided. |
| 0.3 | **Refresh the agent-catalog roster rows** | Says the Evaluator has "no trigger; never produced a verdict" and the Librarian "idles awaiting a verdict." Both false since 2026-07-27/28. |
| 0.4 | **Refresh stale spec `Status:` lines** | Execution Agent reads "Month 1 paper-order core in progress"; Pre-Trade Checker reads "deployable service PR in progress." Both have been deployed for weeks. |
| 0.5 | **Repoint `CLAUDE.md`** from `paper-spine-tree.md` to this file | It currently names a finished document as ground truth. |

---

## Phase 1 — Close the research loop end to end (this week)

The Research Department has produced verdicts, but only for strategies that
were structurally guaranteed to die, and only for one archetype. Until a
Framework #3 strategy runs, the loop has never been exercised as designed.

| # | Item | Depends on | Unblocks |
|---|---|---|---|
| 1.1 | **`technical-catalyst` seed** — the first honest Framework #3 record: right archetype, no invented anchor | ADR-0013 gates (done, #102) | Everything in Phase 2. Also produces the fresh `research.strategy.verdict` that live-verifies #100's Librarian INFO fix and #103's trigger — both currently unit-tested only. |
| 1.2 | **Route `research.strategy.promotion-pending` to a human** | 1.1 | ADR-0015's recorded gap. A held promotion currently signals via a log line nobody watches. **Cheap:** `health_monitor/alerts.py` already implements Discord + ntfy delivery; this is a subscriber and a rule, not a channel. |
| 1.3 | **Verify the Librarian and Runner are actually running** and close KI-014 | — | KI-014 has been open since 2026-07-27 and was never confirmed after the containers were started. |
| 1.4 | **Archetype bar experiment** — `docs/research/archetype-bar-experiment.md` | — | KI-009's fix order said prompt v4 first and "nothing else matters until hard-source items can pass." v4 shipped; they still do not. The 2026-07-31 shadow eval then ruled out the model: four families, two flagship tiers, 0% relevant on the same corpus. What is left is the taxonomy, and the ruling is Mike's. The card runs three candidate bars over the full corpus so he rules on admitted-item lists rather than on argument. ~3% of a week's Ollama allowance. |

**Honest expectation on 1.1:** another kill on trade count. A daily-bar moving
average crossover cannot be a fast-layer strategy — 150 trades over a five-year
window is a position flip every ~8 bars, and the 3/10 probe's 145 trades was
already noise-trading rather than a fast loop. The seed's value is that it is
the first *correctly classified* strategy and the first real exercise of the
automated path. It is not a candidate for edge, and lowering the gate to make it
one would be the failure the protocol exists to prevent.

---

## Phase 2 — Give Research something real to evaluate (early August)

This is the sprint's actual open question: **can the firm generate a strategy
worth trading?** Everything before it was plumbing.

**Mike's ruling, 2026-07-28: both tracks, protocol first.** An earlier draft of
this file led with the intraday-data decision and described the structural lens
as "closer to why you're building this." That was wrong and is corrected here —
**Shrap is a trading firm, and the world-changer work is one lens inside it, not
its purpose.** `00-vision.md` §7 always said so: *most* strategies trade on
technical and short-term-catalyst signals, with Structural Analysis as the
patient counterweight. Both tracks below are first-class; they are ordered by
what unblocks fastest, not by importance.

### Track A — evaluation protocol for slow strategies (start now, no purchase)

| # | Item | Depends on | Note |
|---|---|---|---|
| 2.1 | **A second evaluation protocol** for strategies making a handful of decisions over years | — | `walk_forward` is the only engine in the codebase, and Sharpe over 6 folds of a 5-year window gives ~2 decisions per fold for a 6-month holder — the arithmetic that produced an annualised Sharpe of 1.712 from a *single trade* in fold 5. Candidate instruments: event study around the thesis catalyst, realised-vs-thesis comparison, hit rate with payoff asymmetry, base rates. Costs design time only. |
| 2.2 | **Re-evaluate the killed structural seeds** under the new protocol | 2.1 | The three killed strategies are the only real data the firm has about its own evaluation machinery. |

### Track B — the fast layer (gated on a data decision)

| # | Item | Depends on | Note |
|---|---|---|---|
| 2.3 | **Intraday data decision** | — | **Mike's call.** `market_data.daily_bars` cannot express "fast loops, many trades" at any parameterisation. Scope: bar size, feed, cost, whether the walk-forward still applies. **Decide it against the direction of travel below** — an equities-only feed answers today's question and none of the next one. |
| 2.4 | **Sweep Detector** — ADR-0013 §4, the first genuine Framework #3 strategy | 1.1, 2.3 | Mike's existing liquidation-sweep logic, wrapped. The one strategy in the firm's future with a real prior behind it. |
| 2.5 | ~~**Hypothesis Generator** with `technical-catalyst` in its archetype set~~ **DONE** | 1.1 | Shipped. The spec was two ADRs out of date and was fixed (#151); the agent was built (#156) and given an autonomous trigger (#163). `hypothesis-generator-trigger` runs as a service; `hypothesis-generator` itself is `--profile tools`, invoked by the trigger. The note that "the spec needs updating before the code" was true on 2026-07-28 and stale by 2026-07-30. |
| 2.6 | **Strategy Runner consumes `intelligence.signal`** | 2.4 | ADR-0013 §3. Closes KI-011 — two deployed agents have been writing to a stream with no consumer since Month 2. |

---

## Direction of travel — multi-asset, continuous (ADR-0016)

**Superseded in scope on 2026-07-29.** What follows was written when options and
futures were a long-run intent. They are now a decision: **ADR-0016 commits the
firm to US equities + MES futures + spot crypto, operating continuously.**

**A regulatory correction reshaped this on 2026-07-29.** FINRA's
pattern-day-trader rule — the $25,000 minimum and day-trade counting — was
**eliminated effective 2026-06-04** (SEC approval 2026-04-14; FINRA Notice
26-10). Margin now needs $2,000 minimum equity. **Intraday equity trading at
$10k is legal and available today**, and has been for seven weeks.

What replaced PDT matters more: **intraday margin requirements**, a continuous
position-based constraint (25% maintenance margin held through the day, an
intraday-margin-deficit calculation, and a 90-day new-position freeze after a
pattern of unmet deficits). That is a Risk Officer requirement, and it applies
now — margin is already reachable by a $10k account with no leverage bound
anywhere in the code.

**Sequence, per ADR-0016:** intraday equities first (reuses broker, universe,
Tier 3, strategies and Evaluator; needs only intraday bars and a Runner that can
fire more than once a session) → spot crypto (the genuine 24/7 piece; forces
per-venue calendars) → MES behind the NautilusTrader validation card *and* Risk
Officer bounds → extended/overnight equities once broker capability is verified.

**Frequency is a capability, not a quota** (Mike, 2026-07-29). Nothing here
targets a trade count; "no signal today" is a correct outcome, and turnover
stays a cost in the Evaluator rather than a virtue.

**Item 2.3 below is now decided in principle:** the intraday data question is no
longer "which equities feed" but "three ingest paths — crypto, futures,
intraday equities." Scope it against ADR-0016, not against equities alone.

Three things already in the repo bear on it:

1. **It is the ADR-0003 gate condition, verbatim.** NautilusTrader adoption
   triggers on "live capital or execution needs beyond market/day orders."
   Options and futures *are* execution needs beyond market/day orders. So this
   is not a new decision to make later — it is a decision already recorded,
   waiting on the trigger.
2. **MES futures via IBKR was a Month-3 roadmap item** (`01-roadmap.md`:
   "IBKR Gateway adapter live"). Month 3 is now, and it has not been built.
   Recording that plainly rather than letting it lapse silently.
3. **`post-launch.md` §Options strategies** already has the honest version of
   the cost: Greeks are a state-management layer the Trading Floor does not
   have, the Risk Officer's rules get substantially harder (margin, assignment,
   expiry), and the Evaluator needs an options pricing model. Its recommended
   path — defined-risk verticals on a few names, built as an options-aware
   subsystem rather than retrofitted — still looks right.

**The concrete consequence for 2.3:** a feed chosen only for equities answers
this sprint's question and none of the following one. Futures data and options
chains are worth weighing now even though neither is bought now.

### New cards created by ADR-0016

Reordered after the PDT correction: intraday equities moved from blocked to
first, and the Risk Officer moved from "before MES" to "before margin", which is
now.

| # | Item | Depends on | Note |
|---|---|---|---|
| 2.7 | ~~**Risk Officer: leverage, drawdown, per-strategy loss limits, and an intraday-margin-deficit model**~~ **DONE** | — | Shipped in #146. "The firm has none of these" was true when written and false from 2026-07-30. It is a **library** at `src/shrap/risk_compliance/risk_officer/` enforced inside the Pre-Trade Checker, not a compose service — there is no container to check. Its limits are unruled first cuts, and it has recorded exactly one decision (a `UNKNOWN_STRATEGY` veto) because no order has flowed since 2026-07-29. Built ≠ exercised; see KI-022. |
| 2.8 | **Intraday bars ingest** | — | Second price path. `market_data.daily_bars` cannot express a fast loop at any parameterisation. Scope bar size and feed against ADR-0016's three asset classes, not equities alone. |
| 2.9 | **Runner fires more than once per session** | 2.8 | The pass triggers on entry to `open` and the guard is `(strategy_id, session_date)`. Both are daily-bar assumptions. Note this is *capability*: a strategy that declines to act intraday stays correct and must stay cheap. |
| 2.10 | **Intraday equities path** | 2.7, 2.8, 2.9 | First asset under ADR-0016 and the cheapest — reuses the broker, the 50-name universe, Tier 3, the strategies and the Evaluator. No new calendar; no ADR-0003 gate if bars are polled rather than streamed. |
| 2.11 | **Per-venue market calendars** | — | `operations/market_phase.py` computes XNYS phases from one calendar. Crypto has no `open` at all; MES runs Sun 18:00 → Fri 17:00 ET with a daily halt. Blocks crypto and MES, not intraday equities. |
| 2.12 | **Spot crypto ingest + trading path** | 2.9, 2.11 | The genuinely 24/7 asset. Existing broker, no new gate with bar polling. **Verify broker crypto availability in paper before scoping.** |
| 2.13 | **NautilusTrader bridge-coverage validation** | 2.7 | The ADR-0003 gate card. Prerequisite for MES; verify Alpaca **and** IBKR adapter event coverage against the by-then consumer inventory. |
| 2.14 | **Contract-based sizing** | 2.13 | `shares = notional / price` does not describe a futures contract. One MES ≈ $5 × index ≈ $32k notional on a $10k account. |
| 2.15 | **MES futures path** | 2.13, 2.14 | The step that can end the account in a day. It must not also be the step debuting new plumbing. |

---

## Phase 3 — Make it observable and honest (mid August)

| # | Item | Why it is not optional |
|---|---|---|
| 3.1 | **Instrument LLM calls into Langfuse** | KI-018. Langfuse is deployed and **nothing writes to it.** `llm-routing.md` builds the entire local-migration path on "≥50 task instances recorded in Langfuse with full input/output," and Month 4's exit criteria require the LLM Migration Evaluator to run shadow evals on that data. Neither is reachable today, and every un-traced LLM call is sample that cannot be recovered later. |
| 3.2 | **Health Monitor agent-level checks** | It checks six infrastructure targets (redis, postgres, qdrant, docker, node, tailscale) and nothing about whether the firm is *working*. KI-010's silent ingest-leg death — USASpending stopped for 18 days unnoticed — is exactly what this would catch. |
| 3.3 | **Regime Router** | ADR-0010 §4, tracked by KI-012. The Regime Classifier has been deployed since Month 2 and gates nothing; regime output does not reach the order path at all. |
| 3.4 | **KI-015 ruling** (friction stress is a scenario, not a bound) | Mike's. Cheap. Should land before any strategy is promoted on the strength of surviving it. |

---

## Phase 4 — Month 4 hardening (late August)

Per `01-roadmap.md`: *"Month 4 is not about adding capability."*

| # | Item | Reality check |
|---|---|---|
| 4.1 | **Audit-trail validation** — trace five trades end to end | Achievable now, and the least glamorous item with the highest chance of finding something real. |
| 4.2 | **200+ paper trades measured** | **Currently unreachable.** Every strategy in the registry is `killed`; the Runner has nothing to run. This number depends entirely on Phase 2 landing, and if it does not, the honest result is a number well below 200 and a written explanation of why — which `01-roadmap.md` explicitly says is itself informative. |
| 4.3 | **End-of-sprint retrospective** | Written by Mike; the Weekly Review Agent that was supposed to draft it does not exist. |

---

## Explicitly not this sprint

Listing these is the point — an unwritten "maybe" costs more than a written
"no." Each is a real gap, and none fits in four weeks.

| Gap | Size | Why it waits |
|---|---|---|
| **Structural Analysis Department** — 4 agents, no directory | Large | ADR-0010 §3. The department the fragility/2008-pattern work belongs to. Needs Track A's protocol first — without it the department can produce theses but never a tradeable, measurable strategy. |
| **Reporting Department** — Daily Briefing, Weekly Review, Alert Agent | Medium | The alert *channel* exists (`alerts.py`); the agents do not. 1.2 delivers the one alert that currently matters. |
| **Platform Department** — Cost Monitor, LLM Migration Evaluator, Infrastructure Planner | Medium | Cost Monitor is specced to track "Langfuse spend," so it is blocked behind 3.1 regardless. |
| **Bottleneck Scout** | Large | The Evaluator explicitly refuses `bottleneck-rotation` until `research.bottlenecks` has rows. Framework #1 work, and Framework #1 is paused. |
| **ADR-0011 / Framework #2 (Forced-Proxy)** | Medium | Owed since ADR-0010 §5, never written. Registering a third framework while the first has produced nothing tradeable would repeat the mistake ADR-0013 diagnosed. |
| **44 remaining universe profiles** | Large, mechanical | 6 of 50 written. DQ-004 grandfathered the unprofiled names, so nothing is blocked — but the *target* success criterion in `00-vision.md` says "with per-ticker profiles," and it will not be met. |
| **PBO, deflated Sharpe, purged CV** | Medium | `00-vision.md` target success names them. The Evaluator does walk-forward + friction stress only. Worth stating plainly: this criterion will not be met either. |
| **Position-state derivation** | Medium | KI-005. Deferred until a real strategy needs portfolio state — which Phase 2 may create. Watch it. |
| **Analog layer of the regime classifier** | Medium | Statistical layer only since Month 2. Target success wants both. |

---

## What "minimum success" actually needs

From `00-vision.md`. Assessed honestly against the code, not against intent:

| Criterion | State |
|---|---|
| Runs autonomously on paper 24/7 by month 4 | **Partly.** The spine runs unattended; Research now evaluates unattended (#103). But no strategy is live, so nothing trades. |
| Agents do the majority of code-writing | **Met.** |
| Mike's daily time under 2 hours | **Met.** |
| Development, Research, and Trading Floor all functional | **Trading Floor:** yes, 9/9 plus an autonomous fill. **Development:** by substitution, made honest by ADR-0014. **Research:** the loop closes and now runs on a schedule — but it has never evaluated a strategy that could pass. Phase 1–2 is what turns this from mechanically true into meaningfully true. |
| Audit trails sufficient to analyse every decision | **Mostly.** The Audit Logger persists every stream by pattern. The hole is LLM decisions: no Langfuse traces (3.1), so *why* a filter rejected an item is not reconstructible after the fact. |

---

## Maintenance

Update this file when an item lands or an order changes — not `paper-spine-tree.md`,
which is now history. If a session finds this file stale, that is the bug to fix
first, because a stale plan is how a session ends up executing a command block
written against a repo state that has already moved.
