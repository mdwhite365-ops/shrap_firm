### The shadow forward test (#279)

Card 1 of the strategy-factory plan. Every enrolled strategy, killed ones
included, is decided on each completed session and settled on the next, with
no broker. The decision is stored before the next close exists, and settlement
reads the stored weights, so the record is out-of-sample by construction
rather than by care. The accounting is imported from the backtest engine, and a
test holds a settled period equal to `run_backtest`'s. A shadow return is
therefore the backtest's out-of-sample continuation, and the report prints
every IR with its standard error.

**The pre-merge dry run on the Dell caught a real flaw.** The ledger decided on
2026-09-24 at 12:54 PT from a partial bar, because `market_data.daily_bars`
holds the session in progress. It now reads only sessions before today in New
York. **Assume a component is wrong about what it reads until it has read the
real thing.** This one was right about the schema and wrong about what a row
means at noon.

Two findings from building it: four live images predate #258/#259, so #259's
retrieval isn't running in the hourly Hypothesis Generator; and the −10%
stop-loss sells momentum names the strategy re-buys the next morning (AFRM,
09-23 → 09-24).
