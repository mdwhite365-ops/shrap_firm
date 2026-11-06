# Firm health

`make firm-health` (or `scripts/firm-health.sh`) prints one table: every
cross-component question the firm should be able to answer about itself, worst
first, then a SUMMARY row. Read-only; it creates temp views and writes nothing.

It exists because of the October latch. The Risk Officer's `daily_loss` switch
tripped once and never cleared, and for over a week every new buy on all three
accounts was refused while sells flowed. Every container was up, every output
table was filling, and the Health Monitor saw nothing wrong, because the Risk
Officer was correctly writing refusals. No component asked whether the firm was
allowed to trade. This report asks that, and the related questions, from the
outside.

## Running it

```bash
cd /mnt/Archive/shrap/shrap_firm
make firm-health
scripts/firm-health.sh -v window_days=20 -v shadow_gap_pts=5   # override thresholds
```

Exit code is 2 if any row is `ALERT`, 1 if any is `WARN`, 0 otherwise, so a
scheduler or an overseer agent can branch on it without parsing the table. It
uses `sudo docker` automatically if `truenas_admin` has lost the `docker` group.

## The checks

| check | ALERT / WARN when | what it means |
|---|---|---|
| `switch-latched` | a switch set in an earlier session is still active (ALERT for `daily_loss`) | the firm is halted; `daily_loss` should clear at the session boundary |
| `buys-refused-24h` | an account had buys refused and none approved (ALERT), or more refused than approved (WARN) | an account that can only sell drains to cash |
| `exposure-vs-target` | invested is under half of stage fraction x regime band | the book is not running the strategy that was measured |
| `days-since-fill` | more than 4 calendar days (WARN), 8 (ALERT) | a daily strategy has stopped |
| `regime-flips-24h` | more than 4 label changes in a day | flapping whipsaws every exposure cap |
| `live-vs-shadow` | account return trails its own shadow record, scaled by its average exposure, by more than 3 points | execution is losing what the signal finds |
| `input-freshness` | bars, snapshots, regime or shadow settlement older than 2 days (WARN) or 4 (ALERT) | decisions are being made on old facts |

Thresholds are first cuts, not rulings.

## For an overseer agent

The report is the agent's eyes, not its hands. An overseer (Hermes) should run
it every session, investigate any `ALERT` or `WARN` by reading logs and code,
and report a cause and a proposed fix. It should not clear switches, change
limits or move strategies between accounts: those are Mike's rulings.

## Known limits

- `exposure-vs-target` uses the *current* regime band. When the classifier
  reads `unknown`, the target drops to 20% and a drained book can read OK. The
  `regime-flips-24h` row is the one that shows the classifier is the problem.
- `live-vs-shadow` assumes the account's invested capital would have earned the
  shadow return. It is a gap detector, not a decomposition.
- The stage fraction is passed in as `paper_stage` (default 0.80, mirroring
  `STAGE_FRACTIONS["paper"]`). If that ruling changes, change the default.
