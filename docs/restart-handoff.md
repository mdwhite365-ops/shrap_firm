# SHRap Restart Handoff

Last updated: 2026-06-21T15:47:43-07:00

## Current repo state

- Repo path used by Hermes: `/tmp/shrap-start/shrap_firm`
- Remote: `github.com/mdwhite365-ops/shrap_firm`
- Main branch currently includes PR #14: paper order-event persistence consumer core.
- Current docs/status update branch: `phase1/status-audit-roadmap`.

## Operating priority

Mike explicitly wants to finish the paper-trading spine before switching to Research agents.

Hard boundary:

- Paper only.
- No real-money trading.
- Alpaca credentials stay local in ignored `infra/.env`.
- Never print, commit, or paste secrets.

## Current paper spine state

Merged on `main`:

1. Audit Logger and ADR-0006 event substrate.
2. Decision Maker paper stub.
3. Pre-Trade Checker risk gate and reliability fixes.
4. Pre-Trade Checker deployable service.
5. Paper Execution Agent core and deployable service.
6. Alpaca paper order submit/status/fill polling.
7. Full local paper-spine smoke harness.
8. Paper order/fill persistence schema and sink.

Open:

- Paper order-event persistence consumer core.

Not yet done:

- Package Paper Order Store consumer as service.
- Reconciliation Agent against Alpaca paper.
- Full Docker Compose paper-spine smoke.
- Live market-hours fill smoke that observes `execution.order.filled`.
- ADR-0003 NautilusTrader bridge validation/decision.
- Research agent implementation.

## Local environment

- Host: WSL Ubuntu 22.04.5 with systemd.
- Docker path: `/usr/bin/docker`.
- Docker Engine previously verified: `29.5.2`.
- Docker Compose previously verified: `v5.1.4`.
- Docker service active in this environment.

## Local secrets/config state

- Alpaca paper credentials are in `/tmp/shrap-start/shrap_firm/infra/.env`.
- `infra/.env` is gitignored.
- Old Alpaca key was rotated after appearing in chat.
- Use presence/length checks only; do not print values.

## Project state files

Current state files live under:

```text
docs/status/current-sprint.md
docs/status/decision-queue.md
docs/status/known-issues.md
docs/status/recent-changes.md
```

Audit and roadmap files:

```text
docs/audits/2026-06-21-paper-spine-audit.md
docs/roadmap/paper-spine-tree.md
```

## Next recommended sequence

1. Card 12 — package Paper Order Store service.
2. Card 13 — Reconciliation Agent core against Alpaca paper.
3. Card 14 — Reconciliation deployability.
4. Card 15 — full Docker Compose paper-spine smoke.
5. Card 16 — live market-hours fill smoke and persistence verification.
6. Card 17 — ADR-0003 NautilusTrader bridge validation/decision.
7. Card 18 — only then start Research implementation.

## First commands after restart

```bash
cd /tmp/shrap-start/shrap_firm
git fetch origin main --prune
git status --short --branch
gh pr view 14 --repo mdwhite365-ops/shrap_firm --json state,mergedAt,baseRefName,headRefName,mergeable,url
uv run --python 3.12 --extra dev --extra health-monitor --extra audit-logger --extra pre-trade-checker --extra execution-agent pytest -q
```

If continuing status/audit docs work, checkout:

```bash
git checkout phase1/status-audit-roadmap
```
