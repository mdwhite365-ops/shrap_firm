# ADR-0016: Shrap is a multi-asset, continuously-operating firm

**Status:** Proposed
**Date opened:** 2026-07-29
**Deciders:** Mike White

## Context

Two rulings a day apart moved the firm's target by an order of magnitude, and
the second one changes the architecture rather than the roadmap.

**2026-07-28:** the growth target was set at 35% annually (ADR-less, recorded in
`docs/status/session-handoff.md`), replacing a 1%/day figure raised and then set
aside.

**2026-07-29:** Mike rejected 35% as a baseline, citing Takashi Kotegawa — the
Japanese day trader who turned roughly $13.6k into roughly $153M. Asked which
single path to the fast layer to build toward, his answer was **"I want Shrap to
trade 24/7 stocks, MES, and crypto."** Not one of them. All three.

### The arithmetic, so the target is legible

| | Multiple | CAGR | Per trading day |
|---|---|---|---|
| Kotegawa, ~$13.6k → ~$153M over 8y | 11,250x | **221%/yr** | **0.46%** |
| The same run framed as 6 years to $180M | 12,000x | **379%/yr** | **0.62%** |
| 1%/day (raised, then set aside) | — | 1,130%/yr | 1.00% |
| 35%/yr (the previous target) | — | 35%/yr | 0.12% |

The most-cited retail run in history compounded at roughly **half of one percent
a day**. That is the honest scale: four to five times the daily rate of 35%/yr,
sustained for years — not the order of magnitude beyond documented experience
that 1%/day implied.

Three notes belong in the record next to the number, none of which is an
argument against the target:

- **Survivorship.** Kotegawa is known *because* he is the extreme tail. The
  denominator — everyone who traded that way and blew up — is unmeasured.
- **Capacity cuts our way.** Comparing a $10k account to fund Sharpe ratios was
  the wrong benchmark, because funds are capacity-constrained and a $10k account
  is not. Small capital can take inefficiencies large capital physically cannot.
- **His path was methodical, not frantic** (Mike's framing). High frequency was
  available to him; it was not the point. See "Frequency is a capability, not a
  quota" below — that is the operative principle here, and it is why this ADR
  buys the *ability* to trade intraday rather than an obligation to.

### The regulatory position, corrected

An earlier draft of this ADR claimed the pattern-day-trader rule blocked
intraday equity trading at $10k. **That was wrong, and Mike caught it.**

**The PDT rule no longer exists.** The SEC approved its elimination on
2026-04-14 (SR-FINRA-2025-017); FINRA Regulatory Notice 26-10 set the effective
date at **2026-06-04**, with firms allowed until 2027-10-20 to implement. The
$25,000 minimum equity requirement, the "pattern day trader" designation, and
day-trade counting are all gone. **Margin now requires $2,000 minimum equity**
under the ordinary rules.

So intraday equity trading at $10,000 is legal and available today. It was
closed to us for the whole life of this project and opened seven weeks ago.

**What replaced PDT matters more to Shrap than PDT did.** FINRA substituted
**intraday margin requirements** — a continuous, position-based constraint
rather than a trade counter:

- Maintenance margin of **25% of the current market value** of long
  margin-eligible equity positions, held **throughout the trading day** — an
  effective intraday leverage ceiling near 4x.
- Members compute an **intraday margin deficit**: the largest shortfall between
  required margin and available equity at any point margin-reducing
  transactions occur.
- Deficits must be met promptly (15-business-day outside window). Failing to
  satisfy within five business days *and* showing a pattern of non-compliance
  triggers a **90-day freeze on new positions**.

That last one is an operational risk specific to an autonomous firm: a
misbehaving agent does not get tired, and a repeated breach takes the whole firm
offline for a quarter. It is a Risk Officer requirement, not a footnote.

Futures and spot crypto were never PDT-bound and are not subject to this regime
either; they carry their own margin rules.

## Decision

**Shrap trades three asset classes — US equities, MES futures, and spot crypto —
and operates continuously rather than on a single exchange session.**

### What "24/7" honestly means

The firm can be *awake* 24/7. Only one of the three trades all of it:

| Asset | Actual hours | Margin regime |
|---|---|---|
| **Spot crypto** | genuinely 24/7 | own rules; no FINRA margin |
| **MES futures** | Sun 18:00 ET → Fri 17:00 ET, 60-min daily maintenance halt (~23/5) | CFTC/exchange margin |
| **US equities** | 09:30–16:00 ET regular; 04:00–20:00 ET extended | **FINRA intraday margin**, 25% maintenance, $2,000 minimum |

Overnight/24-5 US equity venues exist (Blue Ocean ATS and similar). **Whether
our broker exposes them is unverified** and must be checked rather than assumed
before any card depends on it.

### Frequency is a capability, not a quota

Mike's clarification (2026-07-29): *"it doesn't have to make trades every day —
we want smart trades. It can do it all day if it thinks the day is going to be
good for it."*

This is a design constraint, not a caveat. **Being able to trade all day and
choosing not to is a correct outcome**, and nothing in the firm may push toward
activity for its own sake:

- No agent gets a trade-count target, and "no signal today" is never an error
  state. The Runner already treats an unchanged target as a no-op; that stays.
- Turnover is a *cost* in the Evaluator (the friction stress test), never a
  virtue. A strategy that trades less to earn the same return scores strictly
  better.
- The intraday capability exists so a strategy can act when conditions warrant,
  not so the firm can be busy.

**The honest tension:** selectivity reduces sample size, and the trade-count
gate (`DEFAULT_MIN_TRADES`) exists because small samples lie. The resolution is
breadth, not a lower gate — the cross-sectional work already measured it: the
same rule produced 89 trades on one ticker and 28,139 across fifty. A selective
strategy over many instruments still clears the gate. **A selective strategy
over one instrument does not, and should not.**

### This trips the ADR-0003 gate, deliberately

ADR-0003 named the NautilusTrader gate as "live capital or execution needs
beyond market/day orders," listing **"real-time market data feeds"** and
**"multi-venue routing (IBKR/MES)"** explicitly, and stating that *"IBKR/MES
work cannot start before that gate."*

This decision meets that condition on both counts. **The NautilusTrader
bridge-coverage validation card becomes a prerequisite for MES**, not a
someday-item. Accepting this ADR is accepting that cost.

Crypto is the exception worth naming: on our existing broker, with bar polling
rather than a streaming feed, it stays inside the direct client's surface and
under the gate. That is a large part of why it goes first.

### What breaks in the code as it stands

Recorded now so no card discovers them one at a time:

1. **`operations/market_phase.py` is single-calendar.** It computes XNYS phases
   (`pre-open`/`open`/`after-hours`/`overnight`/`closed-day`) from one exchange
   calendar. A multi-venue firm needs per-venue calendars, and crypto has no
   `open` at all.
2. **The Strategy Runner fires once per session, on entry to `open`.** Its
   idempotency guard is keyed on `(strategy_id, session_date)`. Both assumptions
   are daily-bar, single-calendar; neither survives a continuous market.
3. **Sizing is `shares = notional / price`.** Futures are contracts on margin,
   not shares at a price. One MES contract is $5 × the S&P index — roughly
   $32,000 of notional against a $10,000 account, about 3.2x leveraged, before
   anyone chooses to hold more than one. Exact margin requirements must be
   verified with the broker, not assumed.
4. **`market_data.daily_bars` is the only price store.** Crypto, futures and
   intraday equities are three distinct ingest paths, not one.
5. **The Pre-Trade Checker's universe gate is a US-equity ticker list.** Tier 3,
   the launch list, and the allowlist all assume equity symbols.

### Sequence

Ordered by what unblocks the most while costing the least, not by preference.
**The PDT correction reordered this.** The earlier draft put crypto first
because equities were believed closed to intraday trading; they are not.

1. **Intraday equities first.** It reuses everything the firm already has — the
   Alpaca broker path, the 50-name universe, Tier 3, the launch list, the
   strategies, the Evaluator. It needs exactly two new things: intraday bars,
   and a Runner that can fire more than once per session. It does **not** need
   per-venue calendars (same XNYS calendar, finer granularity), and it does not
   trip the ADR-0003 gate if bars are polled rather than streamed.
2. **Then spot crypto** — the genuinely 24/7 piece. This is what forces
   per-venue calendars and a session concept that does not assume an open and a
   close. Existing broker, no new gate.
3. **Then MES**, behind the NautilusTrader validation card, with Risk Officer
   leverage and drawdown bounds already in place. This is the step that can end
   the account in a day, and it must not also be the step that debuts new
   plumbing.
4. **Then extended/overnight equities**, once the broker's actual capability is
   verified rather than assumed.

**One thing moved up the list regardless of order.** Margin is available to a
$10,000 account today at the $2,000 minimum, so the firm can already lever
toward the 25% maintenance ceiling — roughly 4x — with **no leverage bound
anywhere in the code**. That is a live exposure now, not a future MES concern.
Risk Officer limits (timeline card 2.10) are no longer gated behind futures.

### What does not change

The evaluation gates. `DEFAULT_MIN_TRADES`, the Sharpe floor, and the
information-ratio floor stay exactly where they are. A higher target makes
measurement more important, not less: at 200%/yr a decayed strategy destroys the
account faster than at 35%. Forward-test scoring — nothing currently evaluates a
strategy *after* promotion — becomes more urgent under this ADR, not less.

## Consequences

**Enables:** an intraday firm at $10k across three asset classes, and genuine
diversification across horizons and instruments — which is what the portfolio
information ratio needed anyway (a target CAGR needs many uncorrelated
strategies, and one asset class does not supply them).

**Constrains:** the NautilusTrader migration moves from deferred to scheduled.
Three data paths instead of one. The Risk Officer becomes load-bearing rather
than deferred, because leverage is now expressible — and, since 2026-06-04,
already available.

**Risk accepted, stated plainly:** leverage is the mechanism by which accounts
reach zero. MES supplies it by construction, and equity margin now supplies it
at $10k. The firm today has **no drawdown limit, no leverage bound, and no
per-strategy loss limit.** It also has no model of the intraday margin deficit,
and a pattern of unmet deficits triggers a 90-day freeze on new positions — an
autonomous agent can repeat a mistake without getting tired. These must exist
before the firm trades on margin at all, not merely before MES.

**Unverified and must be checked, not assumed:** our broker's crypto
availability in paper; its extended/overnight equity access; MES margin
requirements; whether our paper account has been updated to the post-2026-06-04
margin regime; and whether the account is margin or cash.

## Corrections to this ADR

- **2026-07-29 — the PDT premise was wrong.** The first draft asserted that
  FINRA's pattern-day-trader rule capped a sub-$25k account at three day trades
  per five business days and that this was "the constraint that forces this
  decision." The rule was eliminated effective 2026-06-04 (SEC approval
  2026-04-14, SR-FINRA-2025-017; FINRA Regulatory Notice 26-10), seven weeks
  before this ADR was written. Mike caught it. The decision is unchanged — the
  instrument set was his ruling, not a consequence of the rule — but the
  justification and the sequence both changed: intraday equities moved from
  "closed to us" to the cheapest first step.
