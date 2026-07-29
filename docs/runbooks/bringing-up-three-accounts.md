# Runbook: bringing up the three paper accounts

**What this deploys:** ADR-0017 — one strategy per broker account, three
accounts, each with its own Execution and Reconciliation Agent. Cards #124–#128.

**Bring everything up at once.** An earlier version of this runbook said to
start the reconcilers alone first; that is wrong and it fails. Each service
applies its own migrations at startup, and services *read* tables they do not
own — the Reconciliation Agent reads `trading.paper_order_events`, whose
`account_id` column is added by **paper-order-store**'s `ensure_schema`. Start
the reader alone and it crashes every 30s on

```
asyncpg.exceptions.UndefinedColumnError: column "account_id" does not exist
```

The sequencing is unnecessary anyway: the system already fails closed on its
own. The Runner refuses to size an account with no fresh snapshot and defers
those strategies, so bringing everything up in one command is both simpler and
safe. What actually has a deadline is **step 5** — a strategy must be assigned
an account before the next market open, or it will not trade.

Read `deploying-after-a-code-change.md` first if you have not lately — the
build-before-run and `--force-recreate` lessons both apply here.

---

## 0. Pull

```bash
cd /mnt/Archive/shrap/shrap_firm
git pull
```

**Never `sudo git`** in this repo. It creates root-owned objects that break the
next pull.

## 1. Check `.env`

Needs the two new key pairs (already added):

```
ALPACA_API_KEY1=... / ALPACA_SECRET_KEY1=...
ALPACA_API_KEY2=... / ALPACA_SECRET_KEY2=...
```

**Do not set `ALPACA_ACCOUNT_ID`** or its numbered variants. Both agent types
ask the broker which account their keys open. Setting it turns it into an
assertion — useful later, needless now, and a typo makes the agent refuse to
start.

**Check for a stale universe override.** If `.env` pins
`PRE_TRADE_CHECKER_ALLOWED_UNIVERSE` to the old six smoke names, delete the
line — the default is now the full 50-name launch list, and the env var wins
over it.

## 2. Build everything — **including the tools profile**

Nearly every module changed across #117–#128, and every image installs the same
`shrap` package. A partial build leaves one image on stale code with no error.

```bash
cd /mnt/Archive/shrap/shrap_firm/infra
sudo docker compose build
sudo docker compose --profile tools build
```

**Both lines.** `docker compose build` skips profiled services entirely, so the
tools images — `market-data`, `strategy-evaluator`, `infra-mapper` — stay on
whatever was built last. The symptom is an argument parser from an older
release, which reads like a typo rather than a stale image:

```
shrap-market-data-backfill: error: unrecognized arguments: --launch-list
shrap-strategy-seed: error: argument action: invalid choice: 'load-technical'
                     (choose from load-first, load-probe, list-probes, list)
```

This is the same lesson as `deploying-after-a-code-change.md` §1, one level up:
that runbook says *build before run*, and this is *build the profile too*.

## 3. Bring the stack up

```bash
sudo docker compose up -d --force-recreate
```

Each service applies its own migrations on startup, so this is also what creates
the four new columns (`ops.account_snapshots.account_id`,
`research.strategies.account_id`, `trading.paper_order_events.account_id`,
`research.strategy_runner_state.last_quantity`).

**Do not start a subset.** Readers depend on their writers' migrations having
run: the Reconciliation Agent reads `trading.paper_order_events`, which
paper-order-store owns. Starting the reader alone produces
`UndefinedColumnError: column "account_id" does not exist` on a 30s retry loop.
Recorded as KI-020, with the other reader/owner pairs that share the hazard.

Give the reconcilers ~30 seconds before step 4 — the first pass runs
immediately, but a failed one retries on that interval.

## 4. Verify all three accounts appear — **stop here if they do not**

```bash
sudo docker compose exec postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
"SELECT account_id, count(*) AS snapshots, max(at) AS latest
 FROM ops.account_snapshots
 WHERE account_id IS NOT NULL
 GROUP BY account_id ORDER BY account_id;"'
```

**The single quotes are load-bearing.** `SHRAP_DB_USER` lives in `infra/.env`,
which Compose reads but your shell does not — so `psql -U "$SHRAP_DB_USER"`
expands to an empty user and fails with `role "postgres" does not exist`.
Wrapping in `sh -c '...'` defers expansion to inside the container, where
`POSTGRES_USER` and `POSTGRES_DB` are set by the compose service.

Expect **three distinct `account_id` values**, each with a recent `latest`.

- **Fewer than three** → check that reconciler's log. A bad key pair is the
  usual cause; the agent will be logging a broker auth failure.
- **`account_snapshot_unattributed` in a log** → the broker returned no
  `account_number`. The snapshot is deliberately not written; reconciliation
  itself continues.
- Rows with `account_id IS NULL` are pre-#124 history. They are excluded from
  every read by design and can be ignored.

**Write down the three account ids.** Step 5 needs them.

## 5. Assign each strategy to an account

List what exists:

```bash
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-seed list
```

Then, per strategy — dry run first:

```bash
sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-stage assign-account <STRATEGY_ID> --account-id <ACCOUNT_ID> --dry-run

sudo docker compose --profile tools run --rm strategy-evaluator \
  shrap-strategy-stage assign-account <STRATEGY_ID> --account-id <ACCOUNT_ID>
```

**One strategy per account** — a partial unique index enforces it, so a second
assignment to the same account fails at the database rather than quietly
creating two strategies trading one book.

A strategy with no account is logged at ERROR by the Runner each pass and never
trades. That is deliberate: there is nowhere to send its orders.

## 6. Confirm each execution agent found its account

```bash
sudo docker compose logs execution-agent execution-agent-1 execution-agent-2 \
  | grep account_resolved
```

Three lines, three different `account_id` values. If an agent instead logged
`AccountMismatchError`, its keys and its configured account id belong to
different books — fix before it trades.

## 7. Watch the first session

At the next market open:

```bash
sudo docker compose logs -f strategy-runner
```

| Log line | Meaning |
|---|---|
| `signal_published` with `account_id` and `quantity` | working |
| `strategy_unassigned` | step 5 was missed for that strategy |
| `account_equity_unusable` | that account's reconciler is behind; only its strategies defer |
| `sizing_note` | an entry was clamped or too small to fund — not an error, but the live book is not exactly the evaluated one for that name |

On the execution side, `intent_other_account` is normal and expected: every
agent sees every intent and skips the two that are not its own. What is **not**
normal is `intent_unroutable`, which means the producer did not stamp an
account.

---

## Rolling back

Bring the two new agents of each kind down; the original pair keeps trading its
own account exactly as before.

```bash
sudo docker compose stop \
  execution-agent-1 execution-agent-2 \
  reconciliation-agent-1 reconciliation-agent-2
```

No schema changes need reverting — every new column is nullable and additive,
and nothing reads the old rows.
