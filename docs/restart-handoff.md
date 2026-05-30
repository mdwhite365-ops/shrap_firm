# SHRap Restart Handoff

Last updated: 2026-05-30T00:16:37-07:00

## Current repo state

- Repo path: `/tmp/shrap_firm`
- Branch: `main`
- HEAD: `017bc09 feat: add health monitor operations agent`
- Working tree: clean at handoff time
- Remote push status: latest Health Monitor work was pushed earlier to GitHub `mdwhite365-ops/shrap_firm`

## Hermes/runtime state

- Active Hermes profile: `default`
- Hermes config path: `/home/shraptasmaner/.hermes/config.yaml`
- Context config was updated before this handoff:
  - `model.context_length: 1000000`
  - `model.ollama_num_ctx: 1000000`
- Note: actual usable context is still capped by provider/model backend and may require a fresh Hermes session to take full effect.

## Local environment

- Host: WSL Ubuntu 22.04.5 with systemd
- Docker path: `/usr/bin/docker`
- Docker verified:
  - Docker Engine: `29.5.2`
  - Docker Compose: `v5.1.4`
- Docker service is active.
- User `shraptasmaner` is in the Docker group.
- Existing long-lived sessions may still need `sg docker -c 'docker ...'`, `newgrp docker`, or WSL restart before plain Docker socket access works.

## Completed milestone

SHRap Health Monitor milestone is complete and committed:

- Health Monitor operations agent added
- Prometheus polling implemented
- Redis stream event publishing added for:
  - health ticks
  - degraded transitions
  - recovered transitions
  - startup/shutdown
  - alert delivery failures
- Alert dry-run behavior added
- Dockerfile and docker-compose integration added
- `.env.example` and Dell bootstrap runbook updated
- Tests added
- Docker install/verification completed after initial Docker absence
- `docker run --rm hello-world` passed
- `infra/docker compose config --quiet` passed using temporary `.env` copied from `.env.example`

## Current user direction

Mike is preparing to let Hermes continue work possibly across restart/overnight.

Standing preferences and constraints:

- Keep durable memory updated proactively for stable preferences, environment facts, and reusable workflow lessons.
- Do not save stale task progress, commit hashes, PR numbers, or ephemeral artifacts into persistent memory.
- No real-money trading. SHRap remains paper-only.
- Do not paste or expose secrets. Use `.env.example` for schema and local `.env` for real values.

Suggested permission model awaiting explicit final go-ahead:

Allowed:
- edit repo files
- run tests/lint/mypy
- build Docker images
- run local containers
- create commits
- update docs/runbooks
- update memory/skills when durable

Not allowed unless explicitly approved:
- push to GitHub
- delete Docker volumes
- prune Docker system
- install system packages
- use real-money trading APIs
- change secrets or auth configs outside examples/templates

## Recommended next mission

Focus: Month 1 operations substrate.

Recommended sequence:

1. Build and smoke-test the Health Monitor Docker image/runtime path.
2. Run Redis + Prometheus + Health Monitor locally if practical.
3. Verify Health Monitor publishes Redis Stream events.
4. Inspect whether `shrap.events` is already sufficient; if not, implement publish/subscribe/validation helpers per ADR-0006.
5. Implement Audit Logger:
   - consume Redis Streams
   - validate event envelope
   - write every event to PostgreSQL audit table
   - add tests
   - wire into Compose
6. Update docs/runbooks for operations substrate.
7. Run checks.
8. Commit clean completed units.
9. Do not push unless Mike explicitly authorizes.

## Key docs to read after restart

- `README.md`
- `CLAUDE.md`
- `docs/00-vision.md`
- `docs/01-roadmap.md`
- `docs/02-architecture.md`
- `docs/decisions/0001-redis-streams-message-bus.md`
- `docs/decisions/0004-monitoring-stack.md`
- `docs/decisions/0005-alerting-channel.md`
- `docs/decisions/0006-redis-event-envelope.md`
- `docs/agents/operations/health-monitor.md`
- `docs/runbooks/dell-bootstrap.md`

## First commands after restart

```bash
cd /tmp/shrap_firm
git status --short
git log -1 --oneline
docker --version
docker compose version
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
