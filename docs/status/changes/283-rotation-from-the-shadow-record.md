### Account rotation from the shadow record (#283)

Card 5 of the strategy-factory plan. `shrap-shadow-ledger rotation` recommends
which strategy each paper account should hold. It compares a challenger with the
incumbent on the difference of their daily active returns, over the sessions
both settled. Pairing cancels the market noise two strategies share. Two
leaderboard IRs cannot be compared the same way, because each carries a
standard error of about 2 after three months. An empty slot is compared against
zero.

**It recommends and never moves anything.** A swap prints the
`shrap-strategy-stage` commands. The outgoing strategy is parked in
`kill-review`, which stays in the ledger and can return to `paper`. A killed
winner is reported as a protocol ruling, because `killed` is terminal.
Positions outside the incoming strategy's universe are named, because the
Runner will not trade them.

Calibrations, accepted on merge: 1.0 SE over at least 60 shared sessions.
