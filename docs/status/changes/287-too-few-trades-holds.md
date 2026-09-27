### Too few trades holds for data instead of killing (#287)

Mike's ruling, 2026-09-27. A strategy with fewer than 150 backtest trades is now
`hold-for-data` (`insufficient-trades`) rather than `kill`. It is still never
promoted. The gate killed monthly-rebalanced factors on a count, including the
three highest backtest IRs the firm had recorded, and a kill is terminal. The
shadow ledger (#279) now supplies the evidence a thin walk-forward cannot. The
gate still outranks every metric gate, so a thin strategy that looks bad is held
too: an unpowered test is evidence of nothing in either direction.
