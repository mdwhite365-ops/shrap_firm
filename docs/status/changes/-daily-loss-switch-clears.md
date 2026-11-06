### The daily-loss switch clears at the session boundary; latches now alert (#)

**The firm was halted for over a week and nothing noticed.** The Risk
Officer's `daily_loss` switch tripped in late October on a 2% session loss and
stayed set. Its docstring said it "clears on its own, at the session boundary"
and `clear_daily_loss()` existed, but nothing ever called it. Because the switch
blocks new buys firm-wide and never sells, every account kept exiting on signal
and was refused every replacement: 92, 113 and 100 refused buys, high-volume
silent from 10-27, and all three books drained to ~30% invested. Every container
was up and every table was filling.

Three changes, one card:

- **The clear.** `RiskOfficer.sweep` now calls `expire_daily_loss` first: a
  `daily_loss` switch the monitor set in an earlier UTC session is cleared,
  with its own audit row, before today's loss is measured. A breach still true
  today re-sets it in the same pass. A switch Mike set by hand is never
  auto-cleared, and strategy drawdown switches are untouched.
- **The alarm.** The Health Monitor gains `switch-latch:<name>` checks on the
  freshness cadence, reading `risk.kill_switches`. `daily_loss` surviving a
  session boundary is `down`; a manual or drawdown switch doing so is
  `degraded`. Transitions publish `operations.health-anomaly`
  (`switch-latched` / `switch-released`).
- **The report.** `make firm-health` runs a read-only cross-component check:
  latched switches, refused buys, exposure vs target, days since fill, regime
  flapping, live vs shadow, input freshness. It is the checklist an overseer
  agent runs each session. See `docs/runbooks/firm-health.md`.

**Merging this = accepting that the latched switch clears on the first sweep
after deploy**, and the three accounts resume buying. On deploy, rebuild
`pre-trade-checker` and `health-monitor`.
