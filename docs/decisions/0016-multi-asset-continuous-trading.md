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

Two caveats belong in the record next to the number, neither of which is an
argument against the target:

- **Survivorship.** Kotegawa is known *because* he is the extreme tail. The
  denominator — everyone who traded that way and blew up — is unmeasured.
- **Capacity cuts our way.** Comparing a $10k account to fund Sharpe ratios was
  the wrong benchmark, because funds are capacity-constrained and a $10k account
  is not. Small capital can take inefficiencies large capital physically cannot.

### The constraint that forces this decision

**FINRA's pattern-day-trader rule caps a margin account under $25,000 at three
day trades per five rolling business days.** A fourth restricts the account.

So a $10,000 US-equity account is limited to roughly three intraday round trips
a week regardless of strategy quality. No amount of research fixes that; it is
regulatory, not statistical. Kotegawa's edge was intraday, and intraday at this
account size is closed in US equities.

**Futures are CFTC-regulated and spot crypto is unregulated by FINRA — neither
is subject to PDT.** That is the structural reason this ADR exists. Note that
the four "crypto" names already in the launch list (IBIT, ETHA, MARA, RIOT) are
crypto *equities* and remain PDT-bound; they are not a path around it.

## Decision

**Shrap trades three asset classes — US equities, MES futures, and spot crypto —
and operates continuously rather than on a single exchange session.**

### What "24/7" honestly means

The firm can be *awake* 24/7. Only one of the three trades all of it:

| Asset | Actual hours | PDT-bound |
|---|---|---|
| **Spot crypto** | genuinely 24/7 | no |
| **MES futures** | Sun 18:00 ET → Fri 17:00 ET, 60-min daily maintenance halt (~23/5) | no |
| **US equities** | 09:30–16:00 ET regular; 04:00–20:00 ET extended | **yes, under $25k** |

Overnight/24-5 US equity venues exist (Blue Ocean ATS and similar). **Whether
our broker exposes them is unverified** and must be checked rather than assumed
before any card depends on it.

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

Ordered by what unblocks the most while costing the least, not by preference:

1. **Spot crypto first.** The only genuinely 24/7 asset, on the broker we
   already use, with no PDT and no ADR-0003 gate. It forces every architectural
   change the other two need — per-venue calendars, continuous sessions,
   non-session-keyed idempotency, intraday bars — at the lowest integration cost.
2. **Then MES**, behind the NautilusTrader validation card, with a Risk Officer
   leverage bound in place first. This is the step that can end the account in a
   day, and it should not be the step that also debuts new plumbing.
3. **Then extended/overnight equities**, once the broker's actual capability is
   verified rather than assumed.

### What does not change

The evaluation gates. `DEFAULT_MIN_TRADES`, the Sharpe floor, and the
information-ratio floor stay exactly where they are. A higher target makes
measurement more important, not less: at 200%/yr a decayed strategy destroys the
account faster than at 35%. Forward-test scoring — nothing currently evaluates a
strategy *after* promotion — becomes more urgent under this ADR, not less.

## Consequences

**Enables:** an intraday firm at $10k, which PDT closes off in equities alone.
Genuine diversification across horizons and instruments, which is what the
portfolio information ratio needed anyway.

**Constrains:** the NautilusTrader migration moves from deferred to scheduled.
Three data paths instead of one. The Risk Officer becomes load-bearing rather
than deferred, because leverage is now expressible.

**Risk accepted, stated plainly:** leverage is the mechanism by which accounts
reach zero, and MES supplies it by construction. The firm has no drawdown limit,
no leverage bound, and no per-strategy loss limit today. Those must exist before
MES trades, or the first bad week is terminal.

**Unverified and must be checked, not assumed:** our broker's crypto
availability in paper, its extended/overnight equity access, MES margin
requirements, and whether paper accounts simulate PDT.
