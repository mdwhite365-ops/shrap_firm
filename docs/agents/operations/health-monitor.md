# Health Monitor

**Department:** Operations
**LLM tier:** `no-llm` — deterministic. Health monitoring is a numerical pipeline; LLM
involvement would only add latency and unreliability to a load-bearing observability
path. See `docs/infrastructure/llm-routing.md` and `docs/infrastructure/llm-registry.md`.
_Per ADR-0009 and `docs/infrastructure/llm-registry.md`, tier aliases are the contract. Current model for each tier lives in the registry._
**Status:** Draft
**Date:** 2026-05-29
**Author:** Mike White
**Version:** 0.1 (draft)

## Purpose

The Health Monitor is the firm's eyes on its own infrastructure. It periodically queries
the Prometheus stack (per ADR-0004) for the operational health of every load-bearing
piece of the system — Redis, Postgres, Qdrant, Docker, Tailscale, the Ollama runtime, the
NautilusTrader process, the market-data ingest — and publishes `ops.health` events that
every other agent can consume to gate degraded-mode behavior.

It exists because the firm makes decisions on the assumption that its inputs are fresh
and its message bus is delivering. The moment that assumption silently breaks, the firm
trades on stale data. The Health Monitor's job is to make sure that assumption can never
break silently: if anything is degraded, every dependent agent learns about it within
seconds and downgrades its behavior accordingly.

It does **not** itself try to fix problems. It detects, classifies, publishes, and
escalates. Auto-remediation is a separate concern, deferred past the sprint, and would
require its own ADR. The Health Monitor's contract is observability and alerting only.

What this agent cannot do:
- It cannot detect issues that Prometheus isn't already scraping. Coverage is a function
  of what the metrics layer exposes. Gaps in metric coverage are gaps in monitoring.
- It cannot reliably distinguish "service genuinely degraded" from "Prometheus
  scrape failure on a healthy service." It treats both as degraded, which is the safe
  default; the operator follows up.
- It cannot prove the firm is healthy. Absence of alerts is not proof of health. A
  separate synthetic-probe layer (deferred) would be needed for stronger guarantees.

## Trigger

- **Schedule:** Every 30 seconds for a fast pulse of critical services (Redis, Postgres,
  data freshness). Every 5 minutes for fuller coverage (Qdrant, Docker, Tailscale,
  Ollama, disk).
- **Event:** Subscribes to `ops.alertmanager.fired` for push-style Prometheus
  Alertmanager hooks (per ADR-0004) to react faster than the pull cadence.
- **On-demand:** Mike-initiated `ops.health.check` (full sweep).

## Cross-references

**Depends on:** Prometheus stack (ADR-0004), the alert channel infrastructure (ADR-0005),
Alert Agent (escalation consumer).
**Depended on by:** Risk Officer (gates trading on data freshness), Decision Maker
(skips on degraded inputs), Reconciliation Agent, Alert Agent.
**Related ADRs:** ADR-0004 (observability stack), ADR-0005 (alert channel),
ADR-0006 (envelope).
**Related architecture sections:** `docs/02-architecture.md` §Operations Department,
§Observability.

## Inputs

| Source | Type | Description |
|---|---|---|
| Prometheus HTTP API | Query | `query_range` and instant `query` against the metrics defined in `docs/operations/health-queries.md` |
| Alertmanager webhook → Redis | Event | Push notifications of fired alerts |
| Redis: `ops.health.check` | Event | On-demand sweep requests |
| Repo: `docs/operations/health-queries.md` | File read | Authoritative query and threshold definitions, versioned |

## Processing

1. **Pull the query set.** Read `docs/operations/health-queries.md`. Each entry
   specifies: service name, PromQL query, threshold (good / warn / breach), and the
   downstream consumers that care about it.
2. **Execute the queries.** Issue PromQL queries to the Prometheus HTTP API. Treat
   scrape-failure (no data) as `breach` for that service, not as "unknown."
3. **Classify.** Per service, classify as `ok`, `warn`, or `breach` against documented
   thresholds. Track per-service state across runs to detect transitions.
4. **Compose the rollup.** Build a single firm-wide health envelope: overall status
   = worst of the service statuses, with per-service detail. Include data-freshness
   summary (last tick age) for each load-bearing data source.
5. **Publish.** Always publish `ops.health.tick` (heartbeat). Additionally publish
   `ops.health.degraded` on any `ok → warn` or `ok → breach` transition, and
   `ops.health.recovered` on the reverse. These transition events are the ones risk and
   trading agents key off — they should not have to parse the tick stream.
6. **Escalate.** On `breach` (or sustained `warn` per documented dwell), call the alert
   channel per ADR-0005 (Slack/Discord webhook + Telegram for high-severity, with a
   documented quiet-hours policy). The Alert Agent is responsible for formatting and
   delivery; the Health Monitor just emits the alert event and lets the Alert Agent
   handle the rest.
7. **Persist.** Write the tick to Postgres for forensic reconstruction.

## Outputs

| Destination | Type | Description |
|---|---|---|
| Redis stream: `ops.health.tick` | Event | Periodic heartbeat with the full status rollup |
| Redis stream: `ops.health.degraded` | Event | Transition into warn/breach state |
| Redis stream: `ops.health.recovered` | Event | Transition back to ok |
| Redis stream: `ops.alert.request` | Event | Request to Alert Agent for human-facing notification |
| PostgreSQL: `ops.health_history` | Append-only insert | Every tick: services, statuses, raw values, transitions |

Every event carries the ADR-0006 envelope. The `ops.health_history` table is the
forensic substrate for any future "why did the firm halt trading at 14:32" question.

## LangGraph structure

Not used. The Health Monitor is a deterministic poller. Implemented as a simple Python
service running two cadences (30s and 5m).

## State

| What | Store | Notes |
|---|---|---|
| Last-seen per-service status | Redis hash `ops:health:current` | Used for transition detection |
| Dwell timers (warn → alert) | Redis | Reset on recovery |
| Tick history | PostgreSQL `ops.health_history` | Append-only |

## Failure behavior

1. **Containment.** If the Health Monitor itself fails, the system loses its sense of
   freshness. The Risk Officer's policy treats absence of recent `ops.health.tick`
   beyond a documented tolerance (e.g. 2 minutes) as a degraded signal in itself and
   tightens limits accordingly. There is also a dead-man-switch process supervised by
   the Operations Department: if no tick is seen for 5 minutes, an out-of-band alert
   fires via the Alert Agent.
2. **Replay safety.** Trivially safe. The Health Monitor is stateless beyond its
   transition-detection cache, which is rebuildable from a single tick.
3. **Degraded operation.** The firm should not run for extended periods without health
   monitoring. Short outages (minutes) are tolerable because the Risk Officer's
   stale-data protection picks up the slack. Sustained outages (hours) should result in
   Mike pausing the trading floor.

## Sprint scope

- Month 1: Critical-path coverage — Redis, Postgres, market-data freshness, NautilusTrader
  process liveness. Slack/Discord alert delivery via Alert Agent per ADR-0005.
- Month 2: Qdrant, Docker, Tailscale, Ollama. Dwell-time and transition logic.
- Month 3: Cost-related metrics (LLM spend rates) so the Cost Monitor can react.
- Month 4: Synthetic probes for end-to-end critical paths (deferred-or-stretch).

## Deferred

- Auto-remediation (restart-on-failure loops). Out of scope; would need its own ADR.
- Distributed health monitoring (multi-region). Single cluster in sprint.
- Predictive failure detection. Boring beats clever — thresholds, not ML.

## Open questions

- **Dwell thresholds:** How long must a service be `warn` before alerting? Default 5
  minutes during market hours, 30 minutes off-hours. Blocks: alert noise tuning. Owner:
  Mike.
- **Quiet-hours policy:** Which alert severities wake Mike up vs. wait for morning?
  Default: only `breach` outside of market hours. Blocks: ADR-0005 finalization. Owner:
  Mike.
- **Should the Health Monitor itself be in the protected paths set?** Probably yes —
  silently weakening it would be dangerous. Blocks: Implementation Agent boundaries.
  Owner: Mike.
