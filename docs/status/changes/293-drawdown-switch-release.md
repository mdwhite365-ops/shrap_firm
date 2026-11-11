### A strategy drawdown switch a human clears stays cleared (#293)

**The drawdown switch had no release, in two ways.** The sweep set
`strategy:<id>` from `peak_drawdown` over the account's last 365 days, which is
the worst drawdown anywhere in that year and does not change when the switch is
cleared. So the next five-minute pass set it again. Against the real
`RiskOfficer` with test fakes: an account that fell 53.87% and then made a new
high was cleared by hand, and one sweep later was halted again "in a 53.87%
drawdown". And nothing could make the clear to begin with:
`RiskOfficer.set_switch` and `clear_switch` had no caller, so the policy's
"Mike can clear any switch with a manual flag" meant an `INSERT` and an `HSET`
by hand.

Same shape as #291, in the other switch. **Latent, not live:** no account has
drawn down 25%, so no strategy switch has ever tripped.

- **The measurement starts at the last human clear.** The sweep never clears a
  strategy switch, so an inactive one on the board is a human's, and its
  timestamp is where the peak restarts. A fresh 25% from there sets the switch
  again with its own audit row. A switch nobody has cleared is measured over the
  whole lookback, as before.
- **`shrap-risk-switch show | set | clear`.** Same `RiskOfficer` methods as the
  monitor, so Postgres first and Redis second; `actor=human`; published on
  `risk.kill_switch.set` / `.clear`. A misspelled switch name is refused before
  anything connects, and a no-op is reported as one. `show` flags any switch
  where Redis disagrees with Postgres.

**Merging this = accepting that a clear restarts the strategy's drawdown limit
at the clear.**

Verified: 2,349 tests, ruff, mypy --strict; the two new sweep tests fail on the
old sweep. **Not verified on the Dell:** the command has run only against fakes.
Run `show` first; it writes nothing. On deploy, rebuild `pre-trade-checker`.

**Still open:** a strategy rotated into an account slot inherits the previous
occupant's drawdown, because the equity curve is the account's and
`research.strategies` records no assignment time. One clear after a rotation is
the workaround; see `docs/risk/policy.md`, "Manual override".
