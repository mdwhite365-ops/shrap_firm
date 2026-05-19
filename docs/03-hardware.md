# Shrap Hardware Operational Reference

**Version:** 0.1
**Status:** Draft
**Date:** 2026-05-14

This document is the operational reference for Shrap's hardware. It covers configuration, startup procedures, routine maintenance, and incident recovery for each machine in the stack. It is not an architecture document — for the system design rationale, see `docs/02-architecture.md`.

---

## 1. Hardware Overview

Shrap runs across three machines connected via a Tailscale private tailnet. Each machine has a defined role; the boundaries reflect the security, performance, and operational constraints of the sprint.

| Hostname | Machine | Role |
|---|---|---|
| `shrap-prod` | Dell Precision 5820 | Production — all live trading services |
| `shrap-research` | Ryzen 7800X + RTX 4070 Super | Research, heavy inference, backtesting |
| `shrap-dev` | MacBook M4 24GB | Development, docs, code review |

**Role boundaries.** The Dell runs everything that touches live trading: NautilusTrader, broker connections, all department agent containers, and the shared data services (PostgreSQL, Redis, Qdrant, Langfuse). Nothing that touches live trading runs anywhere else. The Ryzen handles compute-intensive work that would degrade the Dell's trading performance — heavy backtests, large-model inference, research agent tasks. It operates on-demand and is not assumed to be reachable at all times. The MacBook is Mike's development and review interface; it runs no production services.

**LLM routing.** Three tiers are in use:
- **Cloud-primary:** Anthropic API, accessed from the Dell; falls back to Mistral Small 24B on Ryzen when the API is unavailable.
- **Local-primary:** Ollama on the Dell (Qwen 2.5 9B) for latency-sensitive agents; Ollama on Ryzen (Qwen 2.5 14B, Mistral Small 24B) for heavy inference routed via the `ryzen.tasks` Redis Stream.
- **No-LLM:** deterministic agents that require no language model.

**Ryzen availability.** Ryzen is Mike's daily-use Windows machine. It is not assumed to be running at any given time. Agents that route work to Ryzen via `ryzen.tasks` must tolerate hours-to-day latency. The stream accumulates work while Ryzen is offline; consumer groups resume from their last acknowledged position when it comes back up.

**Operational drift.** This document describes how each machine is configured and operated. Hardware configuration drifts — update this document when the actual setup diverges from what is written here.

---

## 2. MacBook M4 Pro — Development Operations

The MacBook is Mike's primary development interface for code review, documentation, and Claude Code sessions. It runs no production services.

**Specifications**
- Apple M4, 24GB unified memory
- macOS (current)
- Tailscale hostname: `shrap-dev`

**Development setup**
- Claude Code for agent-assisted development and documentation drafting
- Git: primary authoring machine for all documentation and spec work
- Docker Desktop: available for local testing; not used for production workloads
- No Ollama running locally — LLM calls go to the Anthropic API or Ryzen as appropriate

**Tailscale access.** From the MacBook, all other machines are reachable by hostname:

```bash
ssh truenas_admin@shrap-prod      # Dell (TrueNAS SCALE shell)
ssh mike@shrap-research           # Ryzen (WSL2 Ubuntu)
```

**Role boundary.** The MacBook is a development surface, not a deployment surface. Production changes go through the repo and are deployed by the Deployment Agent or manually on the Dell. Do not run production workloads or store production secrets on the MacBook.

---

## 3. Dell Precision 5820 — Production Operations

The Dell runs TrueNAS SCALE. Shrap's production services run as Docker containers managed by a custom Docker Compose file — not via the TrueNAS Apps catalog. The Apps catalog uses Kubernetes under the hood and introduces abstraction layers that complicate operational control and git integration. The Compose file lives under `/mnt/Archive/<dataset>/compose/` where it remains under git control and is directly editable without going through the TrueNAS UI.

**Host OS and access**
- TrueNAS SCALE (Linux-based, Docker-native)
- SSH: `truenas_admin@shrap-prod` (Tailscale) or `truenas_admin@192.168.1.168` (local network)
- Docker is available directly in the TrueNAS SCALE shell
- Compose file: `/mnt/Archive/<dataset>/compose/docker-compose.yml` — the canonical production deployment descriptor

**Volume layout**

Each major service has its own TrueNAS dataset to allow independent snapshot and backup policies:

| Dataset | Contents |
|---|---|
| `Archive/postgres` | PostgreSQL data directory |
| `Archive/redis` | Redis AOF and RDB files |
| `Archive/qdrant` | Qdrant storage |
| `Archive/langfuse` | Langfuse data |
| `Archive/ollama` | Ollama model cache |
| `Archive/compose` | Docker Compose file and supporting config |
| `Archive/backups` | Logical backups (separate from live data) |

**Container startup sequence**

The Compose file encodes dependency order via `depends_on`, but on manual restart, verify services come up in this sequence:

1. PostgreSQL and Redis — state backends; everything else depends on them
2. Qdrant and Langfuse — secondary data services
3. Ollama — LLM serving
4. NautilusTrader — broker connection; must be up before departments begin trading
5. Department agent containers — consume from Redis Streams once all upstreams are ready

**Ollama on Dell**

The Dell's GTX 1080 (8GB VRAM) serves Qwen 2.5 9B for local department agents. The RTX 2070 Super upgrade (planned, post-sprint) will increase VRAM headroom.

```bash
# Verify Ollama is serving
curl http://localhost:11434/api/tags

# Pull the Dell model (first time only; ~5GB, 10–20 minutes)
ollama pull qwen2.5:9b-instruct-q4_K_M
```

**Health verification**

Run these from a shell on the Dell directly (SSH in first if remote: `ssh truenas_admin@shrap-prod`). `localhost` in the commands below refers to the Dell, not the machine you're SSHing from.

```bash
docker exec postgres psql -U shrap -c "SELECT 1;"
docker exec redis redis-cli PING
curl http://localhost:6333/health
curl http://localhost:3000/api/health
curl http://localhost:11434/api/tags
```

**Tailscale verification**

```bash
tailscale status
tailscale ping shrap-research
tailscale ping shrap-dev
```

---

## 4. Ryzen 7800X + RTX 4070 Super — Research and Inference

The Ryzen machine is Mike's daily-use Windows 11 desktop. Shrap's research and inference workloads run inside a WSL2 Ubuntu 24.04 LTS environment on that machine. Windows remains Mike's primary OS; the Shrap workloads run in the background when the machine is on.

**Specifications**
- AMD Ryzen 7 7800X3D
- NVIDIA RTX 4070 Super (12GB VRAM)
- Windows 11 (host OS)
- WSL2: Ubuntu 24.04 LTS, systemd enabled
- Tailscale hostname: `shrap-research`

**WSL2 setup**

WSL2 runs Ubuntu 24.04 LTS with systemd enabled. Docker Engine runs natively inside WSL2 — not Docker Desktop. GPU access works via WDDM passthrough from the Windows NVIDIA driver: do not install NVIDIA drivers inside Ubuntu; install only `nvidia-container-toolkit` inside WSL2.

Initial WSL2 setup (one-time):

```bash
# In PowerShell (Windows)
wsl --install -d Ubuntu-24.04

# In Ubuntu (WSL2)
# Enable systemd — add to /etc/wsl.conf:
# [boot]
# systemd=true

# Install Docker Engine (not Docker Desktop)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Install nvidia-container-toolkit (no NVIDIA drivers — WDDM handles GPU)
distribution=$(. /etc/os-release; echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/libnvidia-container/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list \
  | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Install Ollama
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable ollama

# Install Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

**Ollama models on Ryzen**

```bash
ollama pull qwen2.5:14b-instruct-q4_K_M
ollama pull mistral-small:24b-instruct-2501-q4_K_M
```

First-time model pulls download 8–15GB per model and take 20–40 minutes total on a typical home connection. Subsequent boots use the cached models. Plan the initial setup accordingly.

**Start and stop procedure**

- **Start:** open any Ubuntu terminal on Windows — WSL2 starts automatically. Services (Ollama, Docker, Tailscale) come up via systemd on WSL2 boot. On first install, WSL2 boot takes 10–30 seconds while systemd initializes services; subsequent starts are typically under 10 seconds. Verify: `systemctl status ollama docker tailscaled`. If any service failed to start, the most common cause is a Windows reboot without running `wsl --shutdown` first — run `wsl --shutdown` from PowerShell, then reopen Ubuntu.
- **Stop:** close all Ubuntu terminal windows. WSL2 suspends automatically after a short idle timeout. For a clean shutdown: `wsl --shutdown` from PowerShell.

**Tailscale verification**

```bash
tailscale status               # shrap-research should show as connected
tailscale ping shrap-prod      # round-trip to Dell
tailscale ping shrap-dev       # round-trip to MacBook
```

**Role boundary.** The Ryzen runs no broker-facing services. It receives work via the `ryzen.tasks` Redis Stream on the Dell and returns results via `ryzen.results`. It holds no broker credentials.

---

## 5. Routine Maintenance

**TrueNAS snapshots**

Configure recurring snapshot tasks in the TrueNAS UI (Storage → Snapshots → Add Task):
- Daily: all datasets, retained 7 days
- Weekly: all datasets, retained 4 weeks

Snapshots are instant and do not interrupt running containers. They are not a substitute for off-box backups for PostgreSQL.

**PostgreSQL logical backup**

TrueNAS snapshots capture the data directory at the block level, but the WAL may not be at a clean checkpoint at snapshot time. Take a monthly logical backup as an additional safety layer:

```bash
docker exec postgres pg_dump -U shrap shrap_prod > \
  /mnt/Archive/backups/shrap_$(date +%Y%m%d).sql
```

Store dumps in `Archive/backups` (separate dataset, separate snapshot policy). Retain the last three monthly dumps.

**Credential rotation**

Rotate on schedule and immediately on any suspected compromise. Minimum schedule:
- Alpaca, IBKR, Anthropic API keys: every 90 days
- Database passwords: every 90 days
- Tailscale auth keys: renew before expiry per Tailscale account settings

Rotation procedure: see `docs/infrastructure/credential-incident-response.md` (pending — until that document is written, the procedure is: revoke at provider, generate new credential, update `.env` on Dell, restart affected containers).

**Capacity monitoring**

No automated alerting during the sprint; monitor manually:
- **TrueNAS dataset usage:** Storage → Pools in TrueNAS UI; flag any dataset over 75% of quota
- **Redis stream sizes:** `redis-cli XLEN <stream-name>` for `ryzen.tasks`, `ryzen.results`, and primary event streams; trim if streams grow unbounded due to unacknowledged consumer groups
- **PostgreSQL table growth:** `SELECT pg_size_pretty(pg_total_relation_size('<table>'))` for trade history and TimescaleDB hypertables; evaluate compression or partitioning if growth outpaces projections

**GPU upgrade procedure (Dell GTX 1080 → RTX 2070 Super)**

1. Stop all containers: `docker compose -f /mnt/Archive/<dataset>/compose/docker-compose.yml down`
2. Power off Dell, swap card, boot TrueNAS SCALE
3. Verify TrueNAS SCALE detects the new GPU
4. Start containers: `docker compose up -d`
5. Re-pull Ollama models if VRAM headroom permits larger quants
6. Verify GPU utilization: `docker exec ollama nvidia-smi`

---

## 6. Incident Recovery

**Dell down**

1. Attempt SSH: `truenas_admin@shrap-prod` (Tailscale) or `truenas_admin@192.168.1.168` (local network)
2. If unreachable, check Tailscale from MacBook: `tailscale status`
3. If Dell shows offline, attempt physical access; TrueNAS SCALE boots to a stable state after unexpected shutdown
4. After power restoration, containers with `restart: unless-stopped` restart automatically; verify with `docker ps`
5. If the outage was not a clean shutdown, follow the unexpected shutdown recovery procedure below

**Ryzen down**

Non-urgent. The `ryzen.tasks` and `ryzen.results` streams accumulate work while Ryzen is offline — this is the intended behavior. Consumer groups resume from their last acknowledged position when Ryzen comes back online. No manual intervention required unless the accumulation window exceeds 48 hours, at which point review queued tasks for staleness.

**Tailscale outage**

1. Local-network access (192.168.x.x) remains available for Dell and Ryzen if on the same LAN
2. Re-authenticate: `tailscale up --authkey <key>` on each affected host
3. Redis consumer group positions are unaffected by Tailscale state; no replay needed
4. Verify restoration: `tailscale status` and `tailscale ping <host>`

**Credential compromise**

1. Revoke immediately in the issuing system (Alpaca console, IBKR account, Anthropic API console)
2. Generate replacement credential
3. Update the secret in the deployment environment; restart affected containers
4. Audit the Redis event log for anomalous activity during the suspected window
5. Cross-reference broker trade history for any orders not initiated by Shrap
6. Document the incident in `docs/infrastructure/credential-incident-response.md`

**Unexpected shutdown recovery**

An unexpected shutdown — power loss, kernel panic — may leave PostgreSQL and Redis in a mid-write state.

*Step 1: PostgreSQL WAL verification.*
PostgreSQL recovers automatically from WAL on next start. Verify recovery completed before starting other services:

```bash
docker exec postgres psql -U shrap -c "SELECT pg_is_in_recovery();"
```

Expected result: `f`. If `t`, wait for recovery to finish.

*Step 2: Redis AOF verification.*
Redis recovers from AOF if enabled. Verify:

```bash
docker exec redis redis-cli CONFIG GET appendonly
```

If `appendonly` returns `no`, enable AOF persistence:

```bash
docker exec redis redis-cli CONFIG SET appendonly yes
docker exec redis redis-cli CONFIG REWRITE
```

`CONFIG REWRITE` persists the change to the Redis config file inside the container. Also update the Compose configuration to ensure AOF stays enabled across container rebuilds: in `docker-compose.yml`, the Redis service's `command` should include `--appendonly yes`. Without AOF, Redis rebuilds only from the last RDB snapshot on restart — events since that snapshot are lost. AOF should be enabled from day one of the sprint, not deferred.

*Step 3: Container restart sequence.*

1. PostgreSQL and Redis (already verified in steps 1–2)
2. Qdrant and Langfuse
3. NautilusTrader
4. Department agent containers

*Step 4: Broker reconciliation.*
After any market-hours outage, verify Shrap's position state matches broker state before resuming automated trading. Query Alpaca and IBKR for current positions and compare against the PostgreSQL position table. If any discrepancy exists, halt and resolve manually. Do not resume automated trading until position state is confirmed consistent.

**UPS prerequisite**

The recovery procedure above is a safety net, not a routine. Unexpected shutdowns under live trading conditions create mid-order risk — a fill that lands between a Redis write and an ACK, or a position update that does not survive the outage. A UPS is a prerequisite for real-money operation, tracked as a post-launch item in `docs/post-launch.md`.
