# Shrap: Architecture

**Document version:** 0.1 (draft)
**Last updated:** 2026-05-13
**Owner:** Mike White
**Status:** Living document — updated as the project evolves

---

## 1. Overview

This document describes how Shrap is built. The vision in `00-vision.md` explains what the firm is and why it exists. This document explains the runtime system that implements it: components, data flows, agent deployment, and the infrastructure decisions that hold it together. Read `00-vision.md` first.

The architecture is organized around three constraints: Mike has 1-2 hours per day, the sprint ends in August 2026, and boring beats clever. Every decision is tested against those constraints before it's allowed to get clever.

This document covers:

- The three loops and their runtime boundaries
- The nine departments and their failure-isolation model
- Hardware topology: what runs where and why
- Data layer: PostgreSQL + TimescaleDB, Qdrant, Redis Streams, and the repo as primary knowledge store
- Inter-department communication via Redis Streams
- Runtime architecture: tooling integration, agent packaging, and lifecycle management
- LLM routing during the sprint and the migration arc toward local-first
- State, memory, observability, and the audit trail

This document does not cover:

- Per-agent specifications — see `docs/agents/`
- Regime profiles — see `docs/regimes/`
- Ticker profiles — see `docs/universe/`
- LLM routing in detail — see `docs/infrastructure/llm-routing.md`
- Post-sprint capabilities — see `docs/post-launch.md`

Architectural decisions made in this document are recorded as ADRs in `docs/decisions/`. The first — the choice of Redis Streams as the cross-department message bus — is in `docs/decisions/0001-redis-streams-message-bus.md`. Where a decision remains genuinely unsettled, it appears in section 2 below, not buried in the relevant section.

The sprint target is a fully autonomous paper-trading system running on the Dell by August 2026. This architecture is sized for that target. It is not designed for high-frequency throughput, multi-asset coverage, or real-money reliability — all explicitly out of scope per the vision.

---

## 2. Open Questions

These are the architectural questions that remain unresolved as of this draft. Each has downstream consequences for what gets built and in what order. They are listed here rather than embedded in later sections so they cannot be overlooked.

**1. System-level monitoring stack.**
Langfuse handles LLM traces. It does not cover Redis, PostgreSQL, Qdrant, Docker containers, or Tailscale connectivity. The Operations Department's Health Monitor needs a substrate to query. Prometheus + Grafana is the standard answer but has not been committed. Decision needed before: Operations Department spec.

**2. Alerting channel to Mike.**
The Reporting Department produces daily briefings, weekly reviews, and urgent alerts. The mechanism for reaching Mike is not decided. Candidates include a Slack bot (low friction, third-party dependency), email (reliable, lower urgency), or a self-hosted web dashboard (sovereign, requires build). Urgent alerts — risk breach, system down — need a path that works when Mike is away from his desk. Decision needed before: Reporting Department spec.

**3. Redis Streams event envelope schema.**
The choice of Redis Streams as the message bus is committed (ADR-0001). The topic namespace and message envelope format — stream names, field keys, schema versioning, required audit fields — are not. Every department that produces or consumes events needs this settled before it can be fully specced. Decision needed before: any department spec that publishes or subscribes to cross-department events.
