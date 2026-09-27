### Strategies as specs (#)

Card 2 of the strategy-factory plan (2026-09-23). Until now a new kind of
strategy was a new rule class and a PR, so the firm had five rules and five
factors after two months. `magnitude-shrinkage` from the literature funnel,
"rank yesterday's absolute return", sat unbuilt because it needed a class.

A `signal-spec` strategy is a JSON expression: eleven price, volume and
market-cap features, per-name arithmetic, and cross-sectional rank and
z-score, plus a rule for the book (top/bottom N, positive or long/short, equal
or inverse-vol weights, daily/weekly/monthly rebalance). `shrap-strategy-seed
load-spec FILE` registers documents at `hypothesis`. Each must carry a thesis
and a kill criterion of its own, since a formula without them is not a
hypothesis.

**The test that earns trust:** a spec written as "momentum 126/21, top ten,
winners only" chooses exactly the names the hand-written momentum class
chooses, on every bar. That's also how the class's "126/21" turned out to mean
104 intervals of return, which is worth knowing before writing its spec by hand.
