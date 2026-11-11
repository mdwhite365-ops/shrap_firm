# ADR-0018: Event plans. Prepare, don't predict

**Status:** Proposed
**Date opened:** 2026-11-11
**Deciders:** Mike White

## Context

The firm reacts. The Regime Classifier labels the market from data that has
already moved, the Regime Router sizes from that label, and nothing anywhere
looks at a date on a calendar and asks what the firm will do when it arrives.

The 2026 midterms are the example that prompted this. The election had been on
the calendar for two years. Mike's own NQ fills from the two sessions after it
span drops of roughly 300 and then 900 points (his sim order export, not the
firm's data). Whatever the firm's books did over those days was not by plan:
nothing in the firm knew the date existed. It was not wrong about the election.
It had no opinion to be wrong with.

Mike's framing (2026-11-11, lightly edited), which this ADR adopts as its
principle:

> I don't expect the world to end, but I have a plan if it does, and I can
> execute that plan based on what's happening around me.

That is preparation, not prediction. The firm does not need to know what will
happen. It needs to never be surprised into improvising.

He named the range it should cover: elections; Fed and data releases; events
inside an industry; a large IPO, which reprices its peers and whoever holds a
stake in it; wars and commodity shocks ("when oil goes up the S&P and NASDAQ
tend to go down"); protests; natural events.

And he made three rulings:

1. **The firm activates plans itself.** If a plan waits for Mike to see
   something, it is too late: these are time-sensitive by nature.
2. **The goal is to grow the accounts reliably, and faster than holding the
   S&P.** That is the yardstick a plan is graded against.
3. **Plans apply to all three accounts.**

## Decision

### 1. A plan is a written, expiring, observable rule

An **event plan** is a record with six parts:

| Part | Meaning |
|---|---|
| Event | What it is about: a dated event, or a class of shock |
| Trigger | A condition the firm can check from data it already stores |
| Scenarios | The ways it can go, each with what the firm expects to see |
| Action | What the firm does in each scenario, from the list in §3 |
| Limit | The most the plan may change, in exposure terms |
| Expiry | When the plan stops applying, with no further action from anyone |

**The trigger must be observable.** "Oil is up 5% on the session" can be
checked. "War breaks out" cannot. A plan whose trigger is a judgment is a
prediction with extra steps and is not accepted.

**Every plan expires.** A plan that reduces exposure is a latch, and this firm
left one set for a week in October with every container healthy (#291), then
found a second with no working release (#293). An event plan carries its own
end time, the sweep that applies it also removes it, and the Health Monitor
alerts on any plan still active past its expiry. No plan is cleared by a
docstring.

### 2. The firm activates; Mike sets the envelope

Ruling 1 is adopted as stated: **no plan waits for a human to activate.** When
a trigger is met, the plan applies on the next sweep.

What Mike approves is the envelope, not the activation: the kinds of action a
plan may take (§3) and the largest change it may make. That keeps the vision's
line that Mike approves "any change touching trading or risk policy". The
policy is this ADR and its limits. Activating a plan inside them is execution.

Every activation and every expiry is published and posted to the alert channel
(ADR-0005), and `shrap-risk-switch`-style manual override applies: Mike can
cancel an active plan, and a plan he cancels stays cancelled.

### 3. What a plan may do, in two tiers

**Tier A, defensive. Live from the first card.**

- Scale new buys down (an event multiplier, applied where the regime
  multiplier already is, taking the smaller of the two).
- Pause new buys.
- Trim held positions toward the scaled target.

**Tier B, offensive. Live only for a plan type with a graded record.**

- Raise exposure above the regime level.
- Open or add to specific names.
- Rotate between names or sectors.

**This is where ruling 2 meets ADR-0015, and merging this accepts the
reconciliation.** Ruling 2 says the point is growth, and growth needs Tier B.
ADR-0015 says a machine applies kills unattended and does not apply promotions,
because a kill is recoverable and an unattended buy is not. Tier A is the kill
side of that asymmetry. Tier B is the promote side.

The gate on Tier B is therefore **evidence, not Mike's sign-off**: a plan type
earns Tier B when its shadow record (§4) shows it would have left the accounts
better off than doing nothing. Until then its Tier B actions are recorded and
graded but not traded. Nothing waits on a person, which honours ruling 1, and
nothing buys on an untested idea, which honours ADR-0015. The threshold for
"earned" is a calibration Mike owns and is not set here.

The alternative Mike may prefer is Tier B live from day one. This ADR does not
recommend it: the Hypothesis Generator gave opposite directions for the same
paper across runs (#289), and a scenario writer built on the same model will be
as fluent and as unsteady.

### 4. Every plan is graded, and the grade is the product

After a plan expires, a grader records, per account:

- what the account did over the event window,
- what it would have done with no plan (the shadow book, same method as #279),
- what holding SPY did over the same window.

A plan is good if the account ended better than its own no-plan shadow. It is
graded against SPY as well because that is ruling 2, but **the no-plan shadow
is the honest comparison**: it isolates what the plan added from what the
strategy was doing anyway.

Plans that have never traded are graded the same way from their shadow
activations. This is what makes the capability self-improving: the next plan
for the same kind of event starts from the last one's grade.

### 5. Which events earn a plan: work backward from what moved the firm

Everything can move a market, and a firm that plans for everything will write a
thousand plans and trust none. The filter:

1. Take the largest one-day moves in the firm's own price history, for the 50
   names and the index funds.
2. Attribute each to a cause, from the news and filing corpus the firm already
   holds.
3. Group the causes. Those groups, ranked by how often and how hard they hit,
   are the event types that get a plan.

A protest that never moved a name the firm holds gets no plan. A storm that
moved XLE does. This is card 1 below, and it decides the
order of everything after it.

Three kinds of event follow from how they are seen coming:

| Kind | Examples | Seen by |
|---|---|---|
| Scheduled, market-wide | Fed decisions, CPI, elections | A calendar |
| Scheduled, company or industry | Earnings, IPO dates, lock-up expiries | A calendar plus EDGAR, which the firm already ingests |
| Unscheduled | War, an oil spike, a bank failure | Nothing. The plan is written in advance and keyed to a trigger |

### 6. Links between assets are measured, not assumed

"Oil up, stocks down" holds in a supply scare and reverses when oil rises on
demand. A large IPO lifts its peers in some markets and drains them in others.
Any plan that depends on one asset moving another must cite the measured
relationship from the firm's own bars, with the window it was measured over,
and is re-measured on a schedule. A relationship believed and not checked is the
same thing as a config comment nobody ran (#244).

### 7. All three accounts

Ruling 3 is adopted. A plan applies to every account, as the regime multiplier
does. A plan that names specific tickers acts only in accounts whose strategy
can hold them.

## Sequence

One card per PR, in this order. Cards 1 to 4 need no model and no new data.

| # | Card | Needs |
|---|---|---|
| 1 | **Look-back:** the biggest moves in the firm's history and their causes | Bars and corpus already stored |
| 2 | **Event store and calendar:** Fed, CPI, earnings for the launch list | A dates source; verify before scoping |
| 3 | **Plan store, activation sweep, expiry, alert, health check** | #291's latch check extended to plans |
| 4 | **Tier A lever with hand-written plans** | See "what breaks" below |
| 5 | **Trigger plans for unscheduled shocks** | 3, 4 |
| 6 | **Grader and shadow activations** | 3; the #279 accounting |
| 7 | **Model-written plans, shadow only** | 6. Ollama session quota (#261) |
| 8 | **Relationship map with measured strengths** | 6. Structural Analysis's first real job |

**Prerequisite, not a card of this ADR:** something runs `make firm-health` on
a schedule and is itself checked. A capability whose first lever reduces
exposure should not ship into a firm that cannot yet notice it has stopped
buying.

## What breaks in the code as it stands

Found by reading, not by running:

- **The resize path will not carry a trim by itself.** The Runner resizes held
  positions from the scale the Risk Officer recorded on the account's *most
  recent approved buy* (#277). A plan that scales buys down reaches held
  positions only after the next buy is approved under it, and a plan that
  pauses buys reaches them never. Card 4 must give the Runner the active event
  multiplier as a recorded fact, not let it infer one.
- **One multiplier field.** `SizingDecision` records `regime_multiplier`. An
  event multiplier folded into it would make every recorded scale ambiguous
  about why it was applied. It needs its own field and its own audit column.
- **No crude price is in the universe.** The launch list has XLE, the energy
  sector fund, alongside gold, bonds, the dollar and crypto funds. XLE is oil
  *companies*, which is not the price of oil. A plan keyed to crude needs a
  series the firm does not ingest; one keyed to XLE does not.
- **The market-phase clock is XNYS only** (ADR-0016, item 2.11). Events that
  land outside the session, which is most elections and most wars, are seen at
  the next open.

## What does not change

- Paper only (ADR-0003).
- Kill-asymmetry (ADR-0015). This ADR extends it to plans; it does not relax it.
- The Risk Officer's limits. A plan can make the firm smaller than they allow,
  never larger.
- One account per strategy, scored on drawdown-penalised growth (ADR-0017).
- The locked universe. A plan may not add a name.

## Consequences

**Enables:** the firm has a position on a known date before it arrives;
exposure can come down ahead of a binary event and back up after, without
anyone awake; every event leaves a graded record the next plan starts from.

**Costs:** a second thing that scales exposure, beside the regime multiplier,
with the interactions that implies; a plan library to keep honest; LLM quota
for card 7.

**Risk accepted:** small samples. There is one midterm every two years. A plan
type for elections cannot be validated by the grader in any useful time, and
the same is true of wars. Fed decisions (eight a year), CPI (twelve) and
earnings across the launch list (about two hundred) can be. KI-036 applies
here exactly as it does to backtests: a plan that worked once is not a plan
that works. Rare-event plans should therefore stay in Tier A, where being wrong
costs a few sessions of lower exposure.

**Risk accepted:** known events are priced. The probable outcome of anything on
a public calendar is in the price before it happens. What a plan can capture is
the surprise and the second-order moves in specific names, and Tier A's value
is mostly in not being caught large, not in profit.

## Flags for Mike

- **Merging this = accepting the Tier B evidence gate in §3.** If you want
  offensive plans live without a graded record, say so and §3 changes; the rest
  stands.
- **The Tier B threshold is unset.** It is a calibration you own, and it can
  wait until card 6 produces something to calibrate against.
- **Card 2's dates source is not chosen.** Fed and CPI dates are published a
  year ahead. Earnings dates for fifty names are not free everywhere and are
  often revised. Verify what Alpaca provides before scoping.
