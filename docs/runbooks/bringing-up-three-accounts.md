# Runbook: bringing up the three paper accounts

**What this deploys:** ADR-0017 — one strategy per broker account, three
accounts, each with its own Execution and Reconciliation Agent. Cards #124–#128.

**Why the order matters, in one line:** the Strategy Runner refuses to size an
account that has no fresh equity snapshot, and only the Reconciliation Agent
writes those. Reconcilers first, always.

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

## 2. Build everything

Nearly every module changed across #117–#128, and every image installs the same
`shrap` package. A partial build leaves one agent on stale code with no error.

```bash
cd /mnt/Archive/shrap/shrap_firm/infra
sudo docker compose build
```

## 3. Reconciliation agents first

They write `ops.account_snapshots` stamped with the account their own keys open.
Until each account has a snapshot, the Runner will not size it.

```bash
sudo docker compose up -d --force-recreate \
  reconciliation-agent reconciliation-agent-1 reconciliation-agent-2
```

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

## 6. Bring up everything else

```bash
sudo docker compose up -d --force-recreate
```

## 7. Confirm each execution agent found its account

```bash
sudo docker compose logs execution-agent execution-agent-1 execution-agent-2 \
  | grep account_resolved
```

Three lines, three different `account_id` values. If an agent instead logged
`AccountMismatchError`, its keys and its configured account id belong to
different books — fix before it trades.

## 8. Watch the first session

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
