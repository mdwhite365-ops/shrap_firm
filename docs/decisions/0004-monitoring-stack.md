# ADR-0004: System-Level Monitoring Stack

**Status:** Accepted
**Date:** 2026-05-29
**Deciders:** Mike White

## Context

Langfuse covers LLM tracing — model, tokens, latency, agent tags. It does not
cover anything else: Redis throughput, PostgreSQL connection pool, Qdrant index
health, Docker container state, host CPU/memory/disk, Tailscale connectivity.
The Operations Department's Health Monitor needs a substrate to query before it
can be specced, and the architecture's Open Question 1 has been deferring this
choice since the first draft.

The deployment target is a single host (the Dell, running Docker Compose on
TrueNAS SCALE). There is no Kubernetes cluster to monitor, no multi-region
fleet, no horizontal scale beyond what Docker Compose handles. Whatever is
chosen must be boring, single-host-friendly, and operable by Mike at 1-2 hours
per day.

Mike is already running Docker on TrueNAS. The marginal operational cost of
adding two more containers in the existing Compose file is small. The marginal
cost of introducing a novel monitoring substrate Mike has not used before is
larger.

## Decision

Prometheus + Grafana, single-host, on the Dell, in the existing Docker Compose
stack. Exporters are added per service:

- `node_exporter` — host CPU, memory, disk, network
- `cadvisor` — per-container CPU, memory, restart counts
- `redis_exporter` — Redis throughput, memory, stream lengths, consumer-group lag
- `postgres_exporter` — connections, replication lag (if applicable), table sizes
- `qdrant` — native `/metrics` endpoint (Prometheus format already exposed)

Grafana is the single dashboard surface. The Operations Department's Health
Monitor queries Prometheus directly via PromQL for anomaly detection and
publishes `health.anomaly` events to Redis Streams when thresholds are crossed.
Mike does not watch Grafana dashboards as a routine; he reads the daily briefing.
Grafana exists for incident investigation, not continuous attention.

Retention: 30 days at full resolution on local disk. No remote write, no
long-term storage tier during the sprint. If retention becomes a real
constraint, that is a Platform Department problem post-sprint.

## Alternatives Considered

**VictoriaMetrics.** A drop-in Prometheus replacement with better compression
and longer retention on the same disk. Genuinely better than Prometheus for
multi-year retention on commodity hardware. Eliminated: Shrap does not need
multi-year retention during the sprint, and Mike has never operated
VictoriaMetrics. Prometheus is the boring default; deviation requires
justification beyond "slightly better." Reconsider post-sprint if retention
hurts.

**Netdata.** Auto-discovery, low-touch, attractive UI, single-binary install.
Genuinely good for "I need monitoring right now and do not want to think about
it." Eliminated: opaque under the hood, less standard alerting integration, and
its anomaly-detection features overlap with what the Health Monitor is supposed
to do — better to keep that logic in an agent that writes audit records, not in
a black-box monitoring agent. The Health Monitor's decisions need to be
inspectable.

**Loki only (no metrics, just logs).** Capture container logs, grep for errors,
skip metrics entirely. Eliminated: the Health Monitor needs to detect things
like "Redis consumer-group lag growing without bound" that are metric-shaped,
not log-shaped. Logs are useful for post-incident investigation but inadequate
as the primary observability layer.

**Do nothing during the sprint; rely on Docker health checks and Redis
heartbeats.** This is the current interim state. Eliminated as the steady-state
answer: heartbeats catch container death, not gradual degradation. The sprint
will produce real operational issues (Redis stream backpressure, Postgres
connection exhaustion) that heartbeats miss. Worth fixing before the Operations
Department spec lands.

## Consequences

**Enables:** Health Monitor has a queryable metrics substrate. Container
state, stream lag, and database health are observable without ad-hoc scripts.
Grafana dashboards become the incident-investigation surface; the daily
briefing remains the routine surface for Mike. Aligns with "boring beats
clever" — Prometheus + Grafana is the most widely-operated monitoring stack
in the world.

**Constrains:** Two additional always-on containers on the Dell (Prometheus,
Grafana) plus exporter sidecars. Modest disk usage for 30-day retention.
Single-host monitoring means a Dell outage takes monitoring down with the
thing being monitored — this is acceptable during the sprint because Mike will
notice a dead Dell without help.

**Cost:** Container resources only. No external services. No license fees.

## Notes

The decision is small on its own; it matters because it unblocks the Operations
Department spec, which several other agents depend on for their health
contract. Defer alert routing details to the Reporting Department spec — the
Health Monitor publishes `health.anomaly`; how that reaches Mike is
ADR-0005's problem.

Grafana access is via Tailscale only. No public exposure. Default admin
credentials are rotated immediately on first start; the rotated credentials are
stored in the Dell's `.env` file alongside other secrets.
