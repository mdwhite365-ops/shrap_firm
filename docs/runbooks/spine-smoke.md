# Paper-spine smoke runbook (Cards 15 and 16)

**Last updated:** 2026-07-02
**Scope:** Verify the deployed compose stack carries one paper signal end to end
(Card 15), and observe a live fill plus a clean reconciliation pass (Card 16).

The smoke tool is `shrap-spine-smoke`. It publishes ONE hand-crafted paper
intent to `trading.decision.intent` and then only watches. Every downstream
event must be produced by the running services — the tool never calls the
broker itself. Exit code 0 means every check passed.

## Prerequisites

- All PRs through Card 15 merged and pulled on the Dell.
- `infra/.env` populated (DB credentials, Alpaca **paper** keys).
- The stack rebuilt so images contain the current wheel:

```bash
cd /mnt/Archive/shrap/shrap_firm
git pull
cd infra
docker compose up -d --build
docker compose ps   # every service healthy
```

## Card 15 — full-stack spine smoke

Run from inside a container that has DB access (the Paper Order Store image
carries `asyncpg` and the console script):

```bash
docker compose exec paper-order-store shrap-spine-smoke
```

Expected output — six PASS lines and exit 0:

```text
[PASS] intent-published: AAPL buy x1 strategy=smoke-... event_id=...
[PASS] risk-decision: approved, event_id=...
[PASS] order-submitted: broker_order_id=... event_id=...
[PASS] order-status: stream=execution.order.status-updated status=accepted
[PASS] paper-order-events-persisted: 2 rows for broker_order_id=...
[PASS] audit-trail: 4/4 chain events in ops.audit_events

SPINE SMOKE PASSED (6/6 checks)
```

Outside market hours the order stays `accepted`/`new` — that is a PASS for
Card 15. The fill is Card 16.

Each FAIL line names the service to look at first. Debug with
`docker compose logs -f <service>`.

## Card 16 — market-hours fill smoke

Run during US market hours (09:30–16:00 ET) with a liquid symbol:

```bash
docker compose exec paper-order-store shrap-spine-smoke \
  --wait-fill --wait-reconciliation
```

This adds three checks: `order-filled` (a live `execution.order.filled` event —
closes KI-003), `fill-persisted` (the fill row in
`trading.paper_order_events`), and `reconciliation` (the next
`operations.reconciliation-completed` pass reports `clean=true`; default
timeout 420s covers the agent's 300s interval).

**Notes:**

- The fill check depends on the Execution Agent's pending-order re-polling
  (merged in PR #22) — deployed images must include it.
- The reconciliation wait can run up to 7 minutes. Run the smoke inside
  `tmux` (`sudo tmux new -s smoke`) so an SSH disconnect does not kill it —
  this has happened twice.

## Options

```text
--ticker AAPL          symbol (must be in PRE_TRADE_CHECKER_ALLOWED_UNIVERSE)
--side buy|sell        default buy
--quantity 1           must be <= PRE_TRADE_CHECKER_MAX_QUANTITY_PER_ORDER
--event-timeout 60     seconds to wait for each stream event
--db-timeout 60        seconds to wait for persistence rows
--wait-fill            Card 16: wait for a live fill
--fill-timeout 300     seconds to wait for the fill
--wait-reconciliation  Card 16: wait for the next clean reconciliation pass
--redis-url ...        default redis://redis:6379/0 (in-network)
--postgres-dsn ...     defaults from PAPER_ORDER_STORE_POSTGRES_DSN
```

## Recording results

After a passing run, update:

- `docs/status/current-sprint.md` — mark the card done.
- `docs/roadmap/paper-spine-tree.md` — mark the smoke nodes done.
- `docs/status/known-issues.md` — KI-003 closes with the first observed live fill.

Paste the smoke output into `docs/sprint-log/` if you want the evidence in the
repo — it contains no secrets (event IDs and order IDs only).
