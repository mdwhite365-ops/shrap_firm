### The first spec batches (#281)

Card 3 of the strategy-factory plan. Specs can now read filed accounting
figures (`fundamental`, `fundamental_growth`), point in time on the filing
date, and the first 13 specs are committed in `docs/strategies/specs/`: seven
over price and volume, six over SEC fundamentals.

**The benchmark is the registered universe, and that was the flaw.** The
Evaluator measures a strategy against equal-weight buy-and-hold of the names it
was registered over. Registered over the whole launch list, a strategy that can
only hold companies was measured against bond, gold, dollar and crypto funds as
well, and the fundamental specs scored about 0.3 of IR higher for it in the
2026-09-24 dry run. A spec now declares `"universe": "equities"`, which is part
of its hash, and a test refuses a committed batch where a spec over filed
figures or market cap does not.

The shadow ledger's panel now carries shares and filed figures, like the
Evaluator's and the Runner's, so a fundamental spec is forward-tested on the
same inputs it was backtested on.
