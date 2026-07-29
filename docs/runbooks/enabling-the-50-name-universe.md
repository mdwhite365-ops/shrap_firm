# Runbook: enabling the 50-name universe

**What this does:** moves the Pre-Trade Checker from its static allowlist to the
Tier-3 table as the authoritative universe gate (ADR-0012), so the universe the
firm may trade is the one the Universe Curator maintains rather than a config
value.

**Why the order matters:** Tier-3 enforcement **fails closed**. Flipping the flag
before `research.universe_tiers` is populated vetoes *every* order with
`TIER3_STATE_UNAVAILABLE`. Enforcement is a real risk gate, so it does not
degrade gracefully — populate first, verify, then flip.

**You do not need this to trade 50 names.** As of the risk-limits card the static
allowlist already defaults to the full launch list, imported from
`launch_list.py`. This runbook is about making Tier 3 *authoritative*, which is
what lets the Curator promote and demote names without a redeploy.

---

## 1. Populate the table

Idempotent. Safe to re-run.

```bash
cd /mnt/Archive/shrap/shrap_firm/infra
sudo docker compose exec universe-curator shrap-universe-promote load-launch-list
```

## 2. Verify before flipping anything

The flag is only safe once this returns 50 active rows.

```bash
sudo docker compose exec postgres psql -U "$SHRAP_DB_USER" -d "$SHRAP_DB_NAME" \
  -c "SELECT tier, count(*) FROM research.universe_tiers GROUP BY tier;"
```

Expected: `active | 50`. The gate matches on the literal `active`
(`TIER3_ACTIVE_TIER`) — a different literal reads as "not in Tier 3" and vetoes
the name. If the count is short or the tier value differs, **stop here**; the
static allowlist is still doing its job.

## 3. Flip the flag

In `infra/.env` on the Dell:

```
PRE_TRADE_CHECKER_TIER3_ENFORCEMENT=true
```

Then restart just the checker:

```bash
sudo docker compose up -d --no-deps --force-recreate pre-trade-checker
```

`couple_universe_gate` disables the static allowlist on the same flag, so Tier 3
becomes the sole universe gate. Both cannot be enforced at once by design —
otherwise only their intersection would be tradeable.

## 4. Confirm, with a real order path

```bash
sudo docker compose logs --tail 50 pre-trade-checker | grep -i tier3
```

A veto carrying `TIER3_STATE_UNAVAILABLE` means the table is unreachable, not
that the name is ineligible. That is the fail-closed path and the fix is step 1,
not the flag.

## Rolling back

Set `PRE_TRADE_CHECKER_TIER3_ENFORCEMENT=false` and recreate the service. The
static allowlist — the 50-name launch list — takes over again immediately. No
data changes, nothing to undo.

## Related

- ADR-0012 (Tier-3 universe membership)
- `docs/agents/risk-compliance/pre-trade-checker.md` (Tier 3 membership rule)
- `docs/agents/research/universe-curator.md` (owner of the table)
