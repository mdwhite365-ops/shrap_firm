# ADR-0015: Autonomous Evaluation Is Kill-Asymmetric

**Status:** Accepted (by merge, 2026-07-28)
**Date:** 2026-07-28
**Deciders:** Mike White

## Context

ADR-0013's sequencing item 2 is "the Evaluator gains a trigger — it is
tools-profile and manual-only today, so no research is automatic regardless of
how many lenses exist." Building that trigger forces a question the sequencing
did not ask: **which verdicts may a machine apply without a human?**

The question is not rhetorical, because of what the stages mean downstream.

A `promote` verdict transitions a strategy `hypothesis → paper`. The Strategy
Runner is an always-on service that, on each market open, emits
`trading.strategy.signal` for every strategy at `paper`, `small-size-paper`, or
`live-paper` (`src/shrap/agents/research/strategy_runner/runner.py`). Those
signals flow to the Decision Maker → Pre-Trade Checker → Execution Agent →
Alpaca.

So an Evaluator that commits its own promotions is an Evaluator that can put a
strategy into paper trading, unattended, on the next open. Nothing between the
walk-forward and the order path would involve a person.

The reverse is not symmetric. A `kill` transitions `hypothesis → killed`, which
is terminal. Its cost is that the firm stops considering an idea it might have
been right about — recoverable by re-registering it, at the cost of the time to
find it again. A `hold-for-data` changes no stage at all.

Two further facts bear on this specific moment:

1. **The firm has never seen a `promote`.** Every verdict produced to date (three,
   2026-07-27/28) was `kill / insufficient-trades`. The promote branch has never
   run against real data, so the first strategy it fires on will be the first
   test of that path.
2. **The probes showed Sharpe is noise at low trade counts** — 20/43/145 trades
   giving 0.415 / −0.157 / 0.745 on the same rule, and annualized 1.712 from a
   single trade in one fold. The gates are the defense against that, and they
   are young.

## Decision

**The unattended trigger applies kills and holds. It does not apply promotions.**

Concretely, `EvaluationPipeline.commit()` takes `promote_requires_review`. The
trigger passes `True`; the manual CLI leaves it `False`.

When a promotion is held:

- The evaluation is **fully recorded** — Markdown card, `research.evaluations`
  row, all metrics. Nothing about the analysis is discarded or deferred.
- The registry transition is **not** applied. The strategy stays at `hypothesis`.
- A `research.strategy.promotion-pending` event is published, carrying the
  metrics and the command that would apply it.
- The service logs at **WARNING** — the one line an operator must not scroll past.

**The held promote must not be published to `research.strategy.verdict`.** The
Strategy Librarian consumes that stream and applies the transition itself, so
publishing a promote verdict while withholding the Evaluator's own transition
would promote the strategy one hop later. The gate would hold nothing. This is
the single way this decision can be implemented wrongly and is asserted by test.

**Reviewing is running the existing CLI.** `shrap-strategy-evaluate
--strategy-id <id>` defaults to `promote_requires_review=False`, so a human
running it re-evaluates deterministically and applies the promotion. No approval
tool is built, because a second path to the same effect is a second path to get
wrong.

### What this is not

It is not a claim that the promote gates are too weak to trust. It is a claim
about *ordering*: the gates have never fired in production, and the first time
they do is not the moment to also be discovering whether the trigger, the
Librarian convergence path, and the Runner's pickup all behave. Kills exercise
the same pipeline end to end at no risk to the order path.

It is also not permanent. Loosening this to full autonomy is a one-line change
once promotions have been observed behaving correctly. Tightening the reverse
direction — after an unattended promotion has already traded — is not a change,
it is a recovery.

## Consequences

- **The autonomy loop is closed for kills, which is where the volume is.** The
  vision's expected kill rate on proposals is ≥90%, so the trigger handles the
  large majority of verdicts unattended. Mike's involvement scales with
  promotions, which is the thing worth his attention.
- **Promotions accumulate silently if nobody watches the stream.** A held
  promotion is re-published only when its 24h re-evaluation floor lapses, so the
  signal is a WARNING log and an event, not a repeated alarm. Wiring
  `research.strategy.promotion-pending` into the Health Monitor / daily briefing
  is a follow-up card, and until it lands this is the known gap.
- **`research.evaluations` records `to_stage='paper'` for a held promote,** while
  `research.strategies` still says `hypothesis`. That is not a contradiction —
  the ledger's documented contract is that it records what a verdict *maps to*,
  and `research.strategy_transitions` is the authority on what actually moved. A
  held promotion is identifiable as an evaluation row with `verdict='promote'`
  and no corresponding transition row. No new column was added for it, because
  duplicating the transition table's answer is how the two get to disagree.
- **ADR-0013 item 2 is satisfied for the sweep leg only.** The Evaluator spec's
  three event triggers are unbuilt because none of their producers exists; see
  the spec's Trigger section, updated in the same card.

## Not changed by this ADR

- Paper-only scope. No real-money execution.
- The evaluation protocol itself — gates, thresholds, and the verdict mapping
  are untouched. This decides who applies a verdict, not what it is.
- The manual CLI's behaviour, which is unchanged in every respect.
