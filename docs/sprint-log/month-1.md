# Month 1 sprint log

## Card 1 — Audit Logger

**Scope:** Implement the Operations Audit Logger as the Phase 1 forensic event
store. The agent discovers Redis Streams, validates ADR-0006 envelopes through
the shared event library, and writes append-only audit rows to PostgreSQL
`ops.audit_events`. Include the minimal audit schema doc, Docker service,
Dockerfile, optional dependency wiring, and unit coverage for mapping, SQL
idempotency, sink parameter order, and polling offset advancement.

**Outcome:** PR #1 merged 2026-06-01. 354 lines Audit Logger implementation,
175 lines tests, 134 lines docs. All quality gates green. One in-scope
reconciliation handled (`psycopg` → `asyncpg`).

**Review time:** Not separately tracked in-session.

**Drift caught:** None — Hermes flagged the `psycopg` reconciliation in its own
report.

**Hermes performance:** Clean execution within scope. Real-world auth blocker
surfaced and reported honestly.

**Notes:** Existing main already contained an early Audit Logger implementation,
but it used `psycopg` and fixed stream names. This card reconciled the code with
the requested asyncpg-based shape and all-stream discovery pattern while keeping
replay idempotent on `event_id`.

**Notes for Month 2:** Card scope was right-sized. Phase A discipline (one card,
manual review, manual merge) produced clean output. No friction in the review
itself.

## Card 3 — Pre-Trade Checker risk-gate wiring

**Scope:** Implement the Risk and Compliance Pre-Trade Checker as the Card 3
message-bus wrapper around the existing pure-function `PreTradeChecker`. The
agent consumes `trading.decision.intent`, invokes the deterministic risk gate,
and publishes `risk.intent.approved` or `risk.intent.vetoed` with correlation
back to the source intent. Include the agent spec, unit tests for approval,
veto, idempotent replay, graceful shutdown, and an integration test proving the
Card 2 signal-to-intent output flows through risk.

**Outcome:** Card 3 implemented on branch `phase1/card-03-risk-gate-wiring`.
The risk gate remains `no-llm`, stateless for Month 1, and preserves the
original intent payload while publishing scaled approved quantity or veto reason.

**Review time:** Not separately tracked in-session.

**Drift caught:** Card 2 was not actually merged into `origin/main` when this
card began, despite the handoff context saying it was merged. This branch was
stacked on the existing Card 2 branch so Card 3 could consume the required
Decision Maker stub.

**Hermes performance:** Followed the requested option A architecture: separate
long-running risk agent process using Redis Streams, not synchronous Decision
Maker coupling.

**Notes:** The Card 3 integration test required the Card 2 stub to preserve an
optional upstream `mode` field so the message-bus path can prove the non-paper
veto case end-to-end. Default behavior remains `mode = paper`.

**Notes for Month 2:** Replace the stub policy with real Risk Officer state:
position/exposure checks, kill switches, correlation caps, and persistent risk
decision tables. Keep real-money blocked until a post-sprint ADR and Mike's
explicit approval.

## Card 4 — Risk Gate Reliability

**Scope:** Fix the startup-replay, offset-on-failure, malformed-input,
and misleading-mode-field bugs identified in the post-Card-3 audit.
Card 4 makes the Pre-Trade Checker agent reliable under realistic
conditions: it replays queued intents on startup, refuses to advance
offsets on processing or publish failure, returns deterministic
vetoes for malformed quantity input, and stops emitting a misleading
top-level mode field in risk decision payloads.

**Outcome:** PR #6 merged 2026-06-17. Card 4 is now on `main` via merge commit `2250fca`; merged main passed the targeted reliability tests, full suite, Ruff, format check, and mypy.

**Drift caught:** PR #4, the Card 3 cleanup PR, was still open when this work
started. Card 4 is stacked on that cleanup branch so this card does not modify
Decision Maker stub behavior and keeps its diff focused on the reliability
fixes.

**Hermes performance:** Followed the audit findings with failing tests first,
kept the card scope to the four requested reliability fixes, and left the other
audit findings for later cards.

**Notes for Month 2:** Consumer groups (XREADGROUP with acks) are the
post-sprint upgrade. Card 4's "0-0" replay is the Month 1 stub. Per-card audit
before next-card-build is the discipline pattern that surfaced these issues.

## Card 5 — Pre-Trade Checker deployability

**Scope:** Package the Month 1 Pre-Trade Checker as an operational service, not just a callable module. Add an environment-backed settings object, console script, Dockerfile, Compose service, and deployability tests while keeping the risk logic deterministic and paper-only.

**Outcome:** PR #7 merged 2026-06-17. The Pre-Trade Checker is packaged as `shrap-pre-trade-checker` with settings, Dockerfile, and Compose service.

**Drift caught:** PR #5 had been automatically closed when PR #4's stacked base branch was deleted; Card 4 was recovered through PR #6 before this card began.

**Notes:** This card deliberately does not add consumer groups, execution handoff, or new risk rules. It only makes the existing reliable risk gate runnable in the stack.

## Card 6 — Paper Execution Agent core

**Scope:** Add the deterministic no-LLM Execution Agent core that consumes `risk.intent.approved`, refuses non-paper approved payloads, submits Alpaca paper market orders through an injected broker client, and publishes `execution.order.submitted` with correlation back to the risk event.

**Outcome:** PR #8 merged 2026-06-17. The paper Execution Agent core is on `main` via merge commit `f228863`.

**Notes:** This card is the next missing link in the Month 1 paper spine after Pre-Trade deployability. It does not yet package the Execution Agent as a service, poll fills, update positions, or validate the NautilusTrader bridge.

## Card 7 — Execution Agent deployability

**Scope:** Package the paper Execution Agent as an operational service. Add `EXECUTION_AGENT_*` settings, an Alpaca paper broker adapter, `shrap-execution-agent` console script, Dockerfile, Compose service, and deployability tests while preserving the paper-only invariant.

**Outcome:** PR #9 opened against `main`; Card 8 is stacked on PR #9 until PR #9 merges.

**Notes:** This still does not add fill polling, position updates, reconciliation, or NautilusTrader bridge validation.

## Card 8 — Alpaca paper order status/fill polling

**Scope:** Extend the Execution Agent to query Alpaca paper order status for submitted broker orders and publish `execution.order.status-updated` or `execution.order.filled` events. Add Alpaca paper `GET /v2/orders/{id}` helper and tests. No real credentials are required for unit/PR verification.

**Outcome:** PR #11 merged 2026-06-17. Card 8 is on `main` via merge commit `5580fa4`.

**Notes:** Real endpoint/key/secret are only needed for a live paper smoke after the PR code path is reviewed and merged/deployed.

## Card 9 — Full local paper-spine smoke

**Scope:** Add an injected end-to-end smoke harness proving a hand-crafted strategy signal traverses Decision Maker stub, Pre-Trade Checker, Execution Agent order submission, and Alpaca-status/fill event emission. Uses fake Redis/broker in tests; no real credentials required.

**Outcome:** PR #12 merged 2026-06-18. Card 9 is on `main` via merge commit `4586c64`.

**Notes:** This proves event contracts compose locally. It does not add Postgres persistence, reconciliation, or Docker-stack orchestration.

## Card 10 — Paper order/fill persistence

**Scope:** Add an append-only PostgreSQL persistence seam for `execution.order.submitted`, `execution.order.status-updated`, and `execution.order.filled` events. Include record mapping, schema SQL, sink tests, and schema docs.

**Outcome:** In progress on branch `phase1/card-10-order-fill-persistence`.

**Notes:** This creates the event trail that a later Reconciliation Agent can compare against Alpaca paper. It does not derive current positions or run reconciliation yet.
