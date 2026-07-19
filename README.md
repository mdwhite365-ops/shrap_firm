# Shrap

A self-developing, self-improving, self-trading firm built primarily by AI agents under human architectural direction.

**Status:** Phase 1 — implementation. The paper-trading spine is deployed on the Dell and
closed (market-hours smoke 9/9 on 2026-07-15); the Research funnel is live. Paper trading
only — no real-money execution.

See [`docs/00-vision.md`](docs/00-vision.md) for the full vision and
[`docs/status/current-sprint.md`](docs/status/current-sprint.md) for living ground truth.

## Repository structure

- `docs/` — All project documentation (the firm's memory)
- `docs/00-vision.md` — Foundational vision document
- `docs/agents/` — Per-agent specifications
- `docs/regimes/` — Historical regime profiles
- `docs/universe/` — The 50-stock universe and per-ticker profiles
- `docs/decisions/` — Architecture Decision Records (ADRs)
- `docs/infrastructure/` — Deployment, networking, observability
- `docs/trading/` — Risk rules, execution policy
- `docs/data/` — Schemas and data sources
- `docs/status/` — Living state files (current sprint, decision queue, known issues)
- `src/shrap/` — The Python package: agent services and domain logic
- `tests/` — Test suite (pytest)
- `infra/` — Docker Compose stack, Dockerfiles, Prometheus config
- `reports/` — Generated reports (daily, weekly, trades)

## Why this README is sparse

This is a private project. The docs are the real documentation; start at the vision doc.
