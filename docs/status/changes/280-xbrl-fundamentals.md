### SEC XBRL fundamentals, point in time (#280)

Card 4 of the strategy-factory plan. `market_data.fundamentals` holds nine
metrics per company from one SEC request each: six annual flows (by duration,
since a 10-K repeats quarters) and three balance-sheet instants. Every read is
on the filing date, and the figures were checked against Apple's published
numbers. A dry run over the launch list covered 40 of 50 names with 20,297
figures.

**Two things the real run caught that a unit test would not have.** Funds file
financial statements too: GLD's "total assets" is its gold, so ETFs and the two
crypto trusts are excluded, by name. And building the panel wiring showed that
**the live Runner had never passed share counts to its panel.** Since #258,
backtests have ranked on market cap and live trading has not. The Runner now
builds its panel through the same optional reader the Evaluator uses.

The pattern is familiar: two components built the same panel separately, and
one of them gained a series the other never did. Only the Evaluator's copy
learned about market cap.
