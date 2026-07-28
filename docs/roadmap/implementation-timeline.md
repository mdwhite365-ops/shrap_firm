# Implementation timeline

**Last updated:** 2026-07-28
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

| # | Item | Depends on | Note |
|---|---|---|---|
| 2.1 | **Intraday data decision** | — | Blocking and unresolved. The fast layer needs it; `market_data.daily_bars` cannot express "fast loops, many trades" at any parameterisation. Scope: which bar size, which feed, what it costs, whether the Evaluator's walk-forward still applies. **This is Mike's call and it gates 2.2.** |
| 2.2 | **Sweep Detector** — ADR-0013 §4, the first genuine Framework #3 strategy | 1.1, 2.1 | Mike's existing liquidation-sweep logic, wrapped. The one strategy in the firm's future with a real prior behind it. |
| 2.3 | **Hypothesis Generator** with `technical-catalyst` in its archetype set | 1.1 | ADR-0013 sequencing item 3. Its spec predates ADR-0013 and allows only the two ADR-0007 archetypes; the spec needs updating before the code. |
| 2.4 | **Strategy Runner consumes `intelligence.signal`** | 2.2 | ADR-0013 §3. Closes KI-011 — two deployed agents have been writing to a stream with no consumer since Month 2. |

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
| **Structural Analysis Department** — 4 agents, no directory | Large | ADR-0010 §3. The department the fragility/2008-pattern work belongs to. Needs Framework #3 working first, or it is a second unvalidated lens. |
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
