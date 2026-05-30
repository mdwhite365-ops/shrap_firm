# SHRap Restart Handoff

Last updated: 2026-05-30T00:45:00-07:00

## Current repo state

- Repo path: `/tmp/shrap_firm`
- Branch: `main`
- Latest completed commit before this handoff: `7dc1792 feat: add operations audit logger`
- Working tree at handoff update time: may include the in-progress `shrap.events`/runbook milestone until committed.
- Remote push status: do not assume latest local commits are pushed. Push only when Mike explicitly authorizes.

## Hermes/runtime state

- Active Hermes profile: `default`
- Hermes config path: `/home/shraptasmaner/.hermes/config.yaml`
- Context config was previously updated:
  - `model.context_length: 1000000`
  - `model.ollama_num_ctx: 1000000`
- Note: actual usable context is still capped by provider/model backend and may require a fresh Hermes session to fully take effect.

## Local environment

- Host: WSL Ubuntu 22.04.5 with systemd
- Docker path: `/usr/bin/docker`
- Docker verified:
  - Docker Engine: `29.5.2`
  - Docker Compose: `v5.1.4`
- Docker service is active.
- User `shraptasmaner` is in the Docker group.
- Existing long-lived sessions may still need `sg docker -c 'docker ...'`, `newgrp docker`, or WSL restart before plain Docker socket access works.

## Local secrets/config state

- Alpaca paper credentials were copied from the Windows Downloads file into `/tmp/shrap_firm/infra/.env`.
- The source Windows path was `/mnt/c/Users/Mdwhi/Downloads/.env/New Text Document.env.txt`.
- The local `infra/.env` file is git-ignored and must not be committed.
- Values were not printed in chat. Use presence/length checks only.

## Completed operations substrate

SHRap Operations substrate now has two working deterministic agents:

1. Health Monitor
   - Prometheus polling implemented
   - Redis stream event publishing added for health ticks, degraded/recovered transitions, startup/shutdown, and alert delivery failures
   - Dockerfile and Compose service added
   - Runtime smoke-tested with Redis + Prometheus

2. Audit Logger
   - Consumes configured Redis Streams
   - Validates ADR-0006 envelopes
   - Writes append-only rows to PostgreSQL `ops.audit_events`
   - Dockerfile and Compose service added
   - Runtime smoke-tested with Redis + Postgres

Current local smoke stack observed running:

- `redis`
- `postgres`
- `prometheus`
- `health-monitor`
- `audit-logger`

## In-progress next milestone

Focus: formalize the ADR-0006 shared event library as `shrap.events`, then commit it with runbook updates.

Expected completed unit:

- `src/shrap/events/__init__.py`
  - public `Envelope` export
  - `EventPublisher`
  - `EventSubscriber`
  - normalized Redis field parsing for bytes/string clients
- tests under `tests/events/test_events.py`
- Audit Logger uses `shrap.events` for Redis read/validate path
- `docs/runbooks/dell-bootstrap.md` documents Health Monitor -> Redis Streams -> Audit Logger -> Postgres verification

## Verification commands for the current milestone

Run from `/tmp/shrap_firm`:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src/
sg docker -c 'docker compose -f infra/docker-compose.yml config --quiet'
sg docker -c 'docker compose -f infra/docker-compose.yml up -d --build audit-logger'
sg docker -c 'docker inspect -f "audit={{.State.Status}} restarts={{.RestartCount}}" shrap_audit_logger'
sg docker -c 'docker exec shrap_postgres psql -U shrap -d shrap -tAc "SELECT count(*) FROM ops.audit_events;"'
```

## Recommended sequence after this milestone

1. Commit `shrap.events` + runbook updates.
2. Start the inner-loop paper path:
   - add Alpaca env placeholders to `.env.example`
   - add deterministic Alpaca paper config/client smoke module
   - implement Pre-Trade Checker skeleton
   - implement hand-crafted signal event -> audit trail path
   - only then wire an order submission path
3. Maintain hard boundary: paper-only, no real-money endpoints.

## First commands after restart

```bash
cd /tmp/shrap_firm
git status --short
git log -1 --oneline
.venv/bin/pytest -q
sg docker -c 'docker compose -f infra/docker-compose.yml ps'
```

If Docker socket permission fails in the fresh session, try:

```bash
sg docker -c 'docker run --rm hello-world'
```

or ask Mike to run:

```bash
newgrp docker
```

or restart WSL from PowerShell:

```powershell
wsl --shutdown
```
