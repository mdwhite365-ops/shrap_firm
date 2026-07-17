# Dell bootstrap runbook (shrap-prod)

**Status:** Phase 1, operations substrate.
**Target host:** Dell Precision 5820, hostname `shrap-prod` on the Tailscale tailnet.
**OS:** TrueNAS SCALE (Linux kernel + Docker).
**Owner:** Mike White.

This runbook brings the Dell substrate online. It deploys: Redis, Postgres+TimescaleDB, Qdrant, Langfuse (with its own Postgres), Prometheus, Grafana, Ollama (GPU), the exporters that feed Prometheus, and the first deterministic Operations agents: Health Monitor and Audit Logger.

## Honest limitations up front

- **Single host.** The Dell is the only machine running this stack. If the Dell is down, the substrate is down. There is no HA, no replication, no failover. Acceptable for the sprint; not acceptable forever.
- **Single point of failure for monitoring.** Prometheus and Grafana live on the same host they monitor. A dead Dell takes monitoring down with it. Mike will notice a dead Dell without help.
- **Local volumes only.** Named Docker volumes on the Dell's ZFS pool. Backups are explicit (see "Backups" below). There is no off-host replication during the sprint.
- **No Kubernetes, no service mesh, no Helm.** Plain Docker Compose. Boring beats clever.

---

## 1. Prerequisites

Confirm each item before proceeding. Do not skip any.

### 1.1 TrueNAS SCALE host

- TrueNAS SCALE installed and reachable on the LAN at the Dell's static IP.
- A ZFS dataset exists for Docker storage (TrueNAS default is fine).
- SSH access enabled, key-based auth, root or a sudo-capable user.

### 1.2 Docker + Compose

- `docker --version` returns a recent (24+) build.
- `docker compose version` returns a v2 build (the plugin, not legacy `docker-compose`).
- The user running `docker compose` is in the `docker` group (or invocations use `sudo`).

TrueNAS SCALE ships Docker as part of its app platform. If for any reason Docker is not enabled, enable it through the TrueNAS Apps console first - that is the one piece of host setup that lives outside the repo.

### 1.3 NVIDIA Container Toolkit (for Ollama GPU passthrough)

- NVIDIA driver installed on the host and `nvidia-smi` reports the Dell's GPU (RTX 2070 Super as of 2026-07-16).
- `nvidia-container-toolkit` package installed.
- `/etc/docker/daemon.json` configured with the `nvidia` runtime, or `nvidia-ctk runtime configure --runtime=docker` has been run.
- `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` succeeds.

If the toolkit check fails, fix it before bringing up the stack. Ollama will refuse to start without it.

### 1.4 Tailscale

- `tailscale status` shows the Dell as `shrap-prod` and the tailnet is up.
- The MacBook (`shrap-dev`) can `ping shrap-prod` over Tailscale.
- SSH from `shrap-dev` to `shrap-prod` over Tailscale works:
  `ssh shrap-prod` (with appropriate `~/.ssh/config` alias).

### 1.5 Filesystem

- A directory `/mnt/Apps/shrap_firm` exists on the Dell, owned by the deploying user.
- A directory `/mnt/backups` exists on the Dell, on a dataset with snapshots enabled.
- The Docker named volumes live under the Docker root (`/var/lib/docker/volumes/` by default on TrueNAS SCALE). We do **not** bind-mount user data paths into the containers - named volumes are the contract.

### 1.6 `.env` populated

The deployment cannot proceed without a real `.env` (see step 2.2). Confirm you have a secrets manager / password store ready to generate and stash these values.

---

## 2. Deploy

### 2.1 Clone the repo

    cd /mnt/Apps
    git clone <repo-url> shrap_firm
    cd shrap_firm/infra

### 2.2 Create the `.env`

    cp .env.example .env
    chmod 600 .env

Then fill in real values. Use `openssl rand -base64 48` for `NEXTAUTH_SECRET` and `SALT`, and `openssl rand -base64 24` for the database passwords. `NEXTAUTH_URL` stays as `http://localhost:3000` because Langfuse is only reached via SSH tunnel from `shrap-dev` (see section 5).

The real `.env` is git-ignored (`/infra/.env` is in `.gitignore`). Never commit it.

### 2.3 Pull images

    docker compose pull

This downloads everything. On a fresh Dell it is several GB. Expect 5-15 minutes depending on link speed.

### 2.4 Bring the stack up

    docker compose up -d

Compose prints each container start. The whole stack should reach `running` state within ~60s. Postgres and Langfuse take the longest because Langfuse runs its schema migrations on first boot.

For a smaller local smoke subset during development, this is enough to test the Operations path without Qdrant/Langfuse/Ollama/Grafana:

    docker compose up -d redis postgres prometheus health-monitor audit-logger

### 2.5 Wait for health

    docker compose ps

Every service should show `healthy` (or `running` for the few without health checks - node-exporter, cadvisor, ollama). If any service is `unhealthy` or restarting, check `docker compose logs <service>` and fix before continuing.

---

## 3. Smoke tests

Each test verifies one service is wired correctly. Run them in order. If any test fails, stop and investigate - downstream tests assume earlier ones passed.

### 3.1 Redis - Streams + AOF

    docker compose exec redis redis-cli PING
    # expect: PONG

    docker compose exec redis redis-cli XADD smoke.test '*' source bootstrap msg hello
    # expect: a stream id like 1717000000000-0

    docker compose exec redis redis-cli XLEN smoke.test
    # expect: 1

    docker compose exec redis redis-cli DEL smoke.test
    # expect: 1

Verify AOF is on:

    docker compose exec redis redis-cli CONFIG GET appendonly
    # expect: 1) "appendonly" 2) "yes"

### 3.2 Postgres + TimescaleDB - hypertable

    docker compose exec postgres psql -U "$SHRAP_DB_USER" -d "$SHRAP_DB_NAME" -c \
      "CREATE EXTENSION IF NOT EXISTS timescaledb;"

    docker compose exec postgres psql -U "$SHRAP_DB_USER" -d "$SHRAP_DB_NAME" <<'SQL'
    CREATE TABLE smoke_test (
      ts TIMESTAMPTZ NOT NULL,
      value DOUBLE PRECISION
    );
    SELECT create_hypertable('smoke_test', 'ts');
    INSERT INTO smoke_test VALUES (now(), 1.0);
    SELECT count(*) FROM smoke_test;
    DROP TABLE smoke_test;
    SQL

Expect a `create_hypertable` notice and `count = 1`.

### 3.3 Qdrant - vector insert + query

    curl -s http://localhost:6333/healthz
    # expect: healthz check passed

    curl -s -X PUT http://localhost:6333/collections/smoke_test \
      -H 'Content-Type: application/json' \
      -d '{"vectors":{"size":4,"distance":"Cosine"}}'

    curl -s -X PUT http://localhost:6333/collections/smoke_test/points \
      -H 'Content-Type: application/json' \
      -d '{"points":[{"id":1,"vector":[0.1,0.2,0.3,0.4],"payload":{"tag":"bootstrap"}}]}'

    curl -s -X POST http://localhost:6333/collections/smoke_test/points/search \
      -H 'Content-Type: application/json' \
      -d '{"vector":[0.1,0.2,0.3,0.4],"limit":1}'

    curl -s -X DELETE http://localhost:6333/collections/smoke_test

The search should return point `1` with score near `1.0`.

### 3.4 Langfuse - reachable + project creation

Open a tunnel from your MacBook (`shrap-dev`):

    ssh -L 3000:localhost:3000 shrap-prod

Then on the MacBook browser go to `http://localhost:3000`. Create the first user (Langfuse's standard onboarding), create a project named `shrap-firm`, and copy the public+secret API keys into your password store. These keys will land in agent specs later as the Langfuse SDK credentials.

API liveness check (from the Dell):

    curl -s http://localhost:3000/api/public/health
    # expect: {"status":"OK"} or similar

### 3.5 Prometheus - all scrape targets UP

Tunnel:

    ssh -L 9090:localhost:9090 shrap-prod

Browser to `http://localhost:9090/targets`. Every job should show `UP`:

- `prometheus` (self)
- `node-exporter`
- `cadvisor`
- `redis-exporter`
- `postgres-exporter`
- `qdrant`

If `node-exporter` is DOWN, the Docker bridge gateway IP differs from `172.17.0.1`. Check the gateway with `docker network inspect bridge | grep Gateway` on the Dell and update `infra/prometheus/prometheus.yml`, then `docker compose restart prometheus`.

### 3.6 Grafana - data source + first dashboard

Tunnel:

    ssh -L 3001:localhost:3001 shrap-prod

Browser to `http://localhost:3001`, log in with `admin` / `$GF_SECURITY_ADMIN_PASSWORD`. Add a Prometheus data source pointing at `http://prometheus:9090` (Grafana reaches Prometheus over the `shrap_net` bridge by service DNS). Save & test - should succeed.

Then import dashboard IDs `1860` (Node Exporter Full) and `893` (cAdvisor) from grafana.com as a sanity check. These can be deleted or replaced later - the canonical dashboards will land in the repo as provisioning JSON in a future commit.

### 3.7 Ollama - pull + run Qwen

    docker compose exec ollama ollama pull qwen2.5:7b
    # 4-6 GB, takes a few minutes
    docker compose exec ollama ollama run qwen2.5:7b "say hello in one short sentence"

Expect a brief response. If GPU passthrough is working, `nvidia-smi` on the host shows the Ollama process using VRAM during generation.

Note: the LLM routing doc specifies "Qwen 2.5 9B-instruct Q4_K_M" as the Dell default. The Ollama tag for the matching model is `qwen2.5:7b` or a custom Modelfile pinning the exact Q4_K_M build. Confirm with the routing doc owner which tag the firm standardizes on before locking it into agent specs. **Open question:** Ollama's library does not ship a `qwen2.5:9b` tag - the closest off-the-shelf sizes are 7B and 14B. Either standardize on 7B (fits trivially in 8GB VRAM with headroom) or pull a quantized 14B and validate VRAM fit.

### 3.8 Operations event path - Health Monitor to Audit Logger

The first end-to-end Operations path is:

Health Monitor -> Redis Streams -> Audit Logger -> PostgreSQL `ops.audit_events`.

Verify Redis has health events:

    docker compose exec redis redis-cli --raw keys 'ops.health-*' | sort
    docker compose exec redis redis-cli XLEN ops.health-tick

Verify the Audit Logger persisted those envelopes:

    docker compose exec postgres psql -U "$SHRAP_DB_USER" -d "$SHRAP_DB_NAME" \
      -tAc "SELECT count(*) FROM ops.audit_events;"

Inspect the first few audit records:

    docker compose exec postgres psql -U "$SHRAP_DB_USER" -d "$SHRAP_DB_NAME" \
      -P pager=off \
      -c "SELECT stream_name, event_id, produced_by FROM ops.audit_events ORDER BY produced_at LIMIT 5;"

Expected result: count is greater than zero and rows include `ops.health-startup` and `ops.health-tick` from `health-monitor@...`.

---

## 4. Operations

### 4.1 Log retention

Container stdout/stderr is captured by the Docker daemon. Set rotation in `/etc/docker/daemon.json` on the host:

    {
      "log-driver": "json-file",
      "log-opts": {
        "max-size": "50m",
        "max-file": "5"
      }
    }

Then `systemctl restart docker` (the stack will need `docker compose up -d` again). This caps per-container log spend at 250 MB. Prometheus's own retention is 30 days, set in the compose file.

### 4.2 Backups

The contract: **named volumes are the only durable state**. Back them up, you can restore. Lose them, the firm's memory is gone.

**Nightly cron on the Dell** (TrueNAS task scheduler or a host crontab):

Postgres logical dump:

    0 2 * * * docker compose -f /mnt/Apps/shrap_firm/infra/docker-compose.yml \
      exec -T postgres pg_dumpall -U "$SHRAP_DB_USER" \
      | gzip > /mnt/backups/shrap-postgres-$(date +\%F).sql.gz

Redis BGSAVE + AOF copy:

    15 2 * * * docker compose -f /mnt/Apps/shrap_firm/infra/docker-compose.yml \
      exec -T redis redis-cli BGSAVE \
      && sleep 30 \
      && docker run --rm -v shrap_firm_redis_data:/data -v /mnt/backups:/backup alpine \
         tar czf /backup/shrap-redis-$(date +\%F).tar.gz -C /data .

Qdrant snapshot (HTTP API):

    30 2 * * * curl -s -X POST http://127.0.0.1:6333/snapshots \
      > /mnt/backups/shrap-qdrant-$(date +\%F).json

Langfuse Postgres logical dump:

    45 2 * * * docker compose -f /mnt/Apps/shrap_firm/infra/docker-compose.yml \
      exec -T langfuse-db pg_dumpall -U "$LANGFUSE_DB_USER" \
      | gzip > /mnt/backups/shrap-langfuse-$(date +\%F).sql.gz

Retention on `/mnt/backups`: keep 30 days, prune older with a `find -mtime +30 -delete` companion cron. ZFS snapshots on the backup dataset add a second layer of protection.

**Off-host copy is out of scope for this sprint** but should be added before the firm holds material live capital - typically `rclone` to an encrypted cloud bucket on the same schedule.

### 4.3 Upgrades

The "boring" upgrade procedure, with downtime acknowledged:

1. Open a PR that bumps image tags in `infra/docker-compose.yml`. Tags are pinned (no `:latest` on production-critical services), so a bump is an explicit, reviewable change.
2. Merge to main.
3. On the Dell:

       cd /mnt/Apps/shrap_firm
       git pull
       cd infra
       docker compose pull
       docker compose up -d

   `docker compose up -d` will recreate only the containers whose image or config changed.

4. Re-run the relevant smoke test from section 3. If it fails, roll back: `git revert` the upgrade PR, `git pull`, `docker compose up -d`, restore from backup if data was touched.

Expect a short outage (seconds to a few minutes) on each upgraded service. There is no rolling upgrade on a single host - this is acknowledged. Schedule upgrades for low-activity windows.

### 4.4 Incident recovery - volume / service map

When something corrupts, you usually want to restore one volume at a time, not nuke the whole stack. The mapping:

| Service       | Named volume        | What's in it                                  |
|---------------|---------------------|-----------------------------------------------|
| redis         | redis_data          | AOF + RDB - the event bus durability layer    |
| postgres      | pg_data             | Firm OLTP + TimescaleDB hypertables           |
| qdrant        | qdrant_storage      | Vector embeddings + collections               |
| langfuse-db   | langfuse_pg_data    | Langfuse traces, projects, users              |
| langfuse      | (stateless)         | Container only - no state                     |
| prometheus    | prom_data           | 30 days of metrics                            |
| grafana       | grafana_data        | Dashboards, users, data sources               |
| ollama        | ollama_models       | Downloaded model weights (re-pullable)        |

Restore pattern (Postgres example):

    docker compose stop postgres
    docker volume rm shrap_firm_pg_data
    docker volume create shrap_firm_pg_data
    docker compose up -d postgres
    # wait for healthy
    gunzip -c /mnt/backups/shrap-postgres-2026-05-29.sql.gz \
      | docker compose exec -T postgres psql -U "$SHRAP_DB_USER" -d postgres

`ollama_models` is the cheapest to lose - just re-pull. `redis_data` losing in-flight stream entries is bus replay territory; consumers using consumer groups will re-read from their last-acked entry. `pg_data` and `langfuse_pg_data` losses are the painful ones - that is what nightly logical dumps exist for.

---

## 5. Remote access pattern (Tailscale + SSH tunnels)

**Principle:** every host port in `docker-compose.yml` binds to `127.0.0.1` on the Dell. Nothing is exposed on the LAN, nothing is exposed on the public internet. Access from outside the Dell goes via Tailscale + SSH tunnel.

On `shrap-dev` (MacBook), keep an `~/.ssh/config` entry:

    Host shrap-prod
        HostName shrap-prod.<tailnet-name>.ts.net
        User <deploy-user>
        IdentityFile ~/.ssh/id_ed25519

Then opening a tunnel for any service is one line:

    ssh -L 3000:localhost:3000 shrap-prod    # Langfuse
    ssh -L 3001:localhost:3001 shrap-prod    # Grafana
    ssh -L 9090:localhost:9090 shrap-prod    # Prometheus
    ssh -L 6379:localhost:6379 shrap-prod    # Redis (use sparingly)
    ssh -L 5432:localhost:5432 shrap-prod    # Postgres (use sparingly)
    ssh -L 11434:localhost:11434 shrap-prod  # Ollama

For multiple tunnels in one session, combine `-L` flags or run a single long-lived control-master session.

**Tailscale Funnel is intentionally not used.** Funnel exposes services to the public internet. The substrate is internal; even Mike accesses it via authenticated SSH only. If a future use case requires Funnel (e.g. a webhook endpoint a broker needs to call), that gets its own ADR.

---

## 6. What this runbook does **not** do

- Does not deploy the full firm. It deploys only deterministic Operations agents needed for the early substrate: Health Monitor and Audit Logger. Trading Floor, Research, Intelligence, Reporting, and Development Department agents come later.
- Does not configure Grafana dashboards as code. Manual dashboard imports above are bootstrap-only; canonical dashboards will land as Grafana provisioning JSON in `infra/grafana/provisioning/` later.
- Does not set up alerting. ADR-0005 covers the alerting channel; the Reporting Department spec wires it in.
- Does not set up the Ryzen worker. The Ryzen is a separate host with its own (smaller) compose file - that ships when the first agent needs heavy local inference.
- Does not configure off-host backup replication. Sprint scope is local backups to `/mnt/backups` only.

---

## 7. Quick reference

Bring stack up:                 `docker compose up -d`
Bring stack down (preserve volumes): `docker compose down`
Bring stack down + wipe data:   `docker compose down -v`  (DESTRUCTIVE)
Tail all logs:                  `docker compose logs -f`
Tail one service:               `docker compose logs -f <service>`
Restart one service:            `docker compose restart <service>`
Run a one-off command:          `docker compose exec <service> <cmd>`
Update images per pinned tags:  `docker compose pull && docker compose up -d`

---

## 8. Health Monitor (first agent)

The Health Monitor is the first agent that runs on top of this substrate. It polls
Prometheus on a 30s cadence, publishes `ops.health-tick` / `ops.health-degraded` /
`ops.health-recovered` envelopes on Redis Streams, and pages out via Discord
(routine) and ntfy.sh (urgent, system-wide). Spec: `docs/agents/operations/health-monitor.md`.

1. **Configure alert channels.** In `infra/.env`, set:
   - `HEALTH_MONITOR_DISCORD_WEBHOOK_URL` to a Discord webhook URL for routine alerts.
   - `HEALTH_MONITOR_NTFY_URL` to an ntfy topic URL (e.g. `http://ntfy:8080/shrap-urgent`) for urgent alerts.
   Leaving either blank disables that channel; leaving both blank means alerts are logged only.

2. **Build the image.** From `infra/`:
   `docker compose build health-monitor`

3. **Start the agent.** Substrate must already be up (`docker compose up -d`):
   `docker compose up -d health-monitor`

4. **Verify the first tick.** Tail the logs and look for a `health.tick` JSON
   line within ~30 seconds:
   `docker compose logs -f health-monitor`
   You can also confirm the envelope landed on Redis:
   `docker compose exec redis redis-cli XLEN ops.health-tick`

5. **Smoke-test the alert path.** With the agent running and Discord webhook configured,
   stop redis (`docker compose stop redis`) and wait ~2 ticks (~60s); a
   "[shrap] DEGRADED - redis" embed should appear in Discord. Then restart redis
   (`docker compose start redis`) and wait ~3 ticks (~90s); a "[shrap] RECOVERED - redis"
   embed should follow.

Open issues / known gaps:
- The Tailscale check is a deliberate stub; it always reports `ok` with a `stub:true`
  evidence flag until a tailscale exporter is wired into Prometheus.
- Transition state is process-local; restarting the container resets counters.
  Acceptable per the spec - a fresh `ops.health-startup` envelope is published on
  boot and the next tick re-establishes baseline.
