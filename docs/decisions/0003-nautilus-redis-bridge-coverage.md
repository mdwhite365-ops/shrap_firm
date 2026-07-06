# ADR-0003: NautilusTrader-to-Redis Event Bridge Coverage

**Status:** Accepted
**Date opened:** 2026-05-06
**Date decided:** 2026-07-06
**Deciders:** Mike White

## Context

The architecture originally assumed NautilusTrader as the sole broker
interface: Alpaca and IBKR credentials held only by NautilusTrader's
container, all other departments consuming trading data via Redis Streams
events published by a NautilusTrader-to-Redis bridge. This ADR was opened to
track one unverified assumption: whether that bridge could expose fill,
account, and position data completely enough that no department would ever
need direct broker access.

The Month 1 paper spine answered the question from the other direction. The
spine was built with a purpose-built direct Alpaca paper client
(`shrap.trading_floor.alpaca`, ~150 lines): a paper-only endpoint validator
that rejects live URLs at settings-load time, order submit, order status, and
order listing. The full path — signal → risk gate → submission → status →
persistence → audit → reconciliation — passed the live compose smoke on
2026-07-06 without NautilusTrader in the loop.

What Month 1 actually needed from a broker interface: market order
submission, order status polling, and account/order snapshots. Standing up
NautilusTrader for that surface would have added its runtime, its adapter
configuration, and its failure modes to the spine while the bridge-coverage
question this ADR tracks remained unanswered.

## Decision

**Direct Alpaca paper access is the accepted broker interface for the paper
phase — an explicit scope decision, not a temporary shortcut.**

The isolation property this ADR exists to protect is restated in container
terms rather than NautilusTrader terms:

1. **Broker credentials live only in broker-facing agent containers.**
   Today that is exactly two: the Execution Agent (submit + status) and the
   Reconciliation Agent (read-only snapshots). Both load credentials through
   `AlpacaPaperSettings`, which rejects any non-paper endpoint.
2. **Every other department consumes trading data via Redis Streams events
   or PostgreSQL records** (`execution.order.*`, `trading.paper_order_events`,
   `operations.reconciliation-*`). No third container may receive broker
   credentials without amending this ADR.
3. **The event surface is the contract.** Any new consumer need (positions,
   account equity, market data) is met by extending the events published by
   the broker-facing agents — never by handing out credentials.

**NautilusTrader adoption is re-scoped as a gate, not a Month 1 dependency.**
The bridge-coverage validation this ADR originally demanded becomes a
prerequisite card for whichever comes first:

- **Live capital.** Real money does not flow through the hand-rolled client.
- **Strategy execution needs beyond market/day orders** — real-time market
  data feeds, bracket/limit order management, multi-venue routing (IBKR/MES),
  or a position engine. These are exactly what NautilusTrader is for, and
  rebuilding them in `shrap.trading_floor` would be reinventing the engine
  badly.

When that gate is reached, the validation card must verify NautilusTrader's
Alpaca and IBKR adapter event coverage against the by-then-known consumer
inventory, and the migration must preserve invariants 1–3 above.

## Alternatives Considered

**Stand up NautilusTrader now and route the paper spine through it.**
Architecturally cleanest — one broker boundary from day one. Eliminated:
it front-loads the heaviest dependency in the stack to serve three HTTP
endpoints, and the sprint constraint is Mike's time. Boring beats clever;
the spine works and is fully audited.

**Keep the direct client indefinitely, drop NautilusTrader from the plan.**
Eliminated: the vision commits to regime-conditional strategies on live
market data and eventual MES futures via IBKR. A hand-rolled execution
engine at that scope is a known failure mode. The direct client is right
for the paper spine precisely because the spine's broker surface is tiny.

**Build the NautilusTrader bridge in parallel with Research (Month 2).**
Eliminated as a default: it spends sprint time on infrastructure ahead of
need. If Research's first promoted strategies require only market/day paper
orders, the bridge waits.

## Consequences

**Enables:** Research implementation (Card 18) starts on a verified spine
now. The Trading Floor spec can be written against the actual event surface
instead of an assumed bridge.

**Constrains:** Strategies during the paper phase are limited to the direct
client's surface — market/day orders on Alpaca paper. A strategy design that
needs more triggers the NautilusTrader gate early. IBKR/MES work cannot
start before that gate.

**Debt acknowledged:** the credential boundary now has two containers inside
it instead of one, and the paper_order_events schema carries Alpaca-shaped
fields (`broker_order_id`, `filled_qty`). The `broker` column and the
adapter-pattern client keep the migration path open.

## Notes

Closes KI-002 and architecture Open Question 1 (2026-06-21 implementation
note). Opened during architecture drafting; decided after the paper spine
passed its full-stack smoke — the Trading Floor's real event surface turned
out to be the better decision input than NautilusTrader's adapter docs.
