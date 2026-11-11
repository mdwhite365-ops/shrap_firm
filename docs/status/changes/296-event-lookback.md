### The event look-back: which events have moved the firm (#296)

ADR-0018 card 1. `shrap-event-lookback` finds the launch list's largest one-day
moves (3 sigma of the name's own prior 60 returns, and at least 2%), separates
days the market moved from days one name did, and attaches the news and filings
on file between the prior close and that close. A move's cause is the category
the News Analyzer or Filing Processor already recorded. **Read-only, no model.**

It keeps three things apart that would otherwise corrupt the count:

- **A move before the corpus began is not an unexplained move.** Bars reach
  back to 2021; the news and filing tables began in 2026. Attribution starts
  when both sources were recording, and each source's start is printed.
- **A return of 40% or more is a data fault until checked** (#290), listed
  apart and kept out of the volatility yardstick.
- **Evidence must precede the move.** An item at 16:05 explains the next
  session, so the window is prior close to this close, not the calendar day.

The report states the share of moves it could not explain. That number is a
measurement of the corpus as much as of the market.

**Not run against the database.** 34 tests on constructed bars and rows; the
SQL and JSONB handling have not touched Postgres. It only reads. First run:
`docker compose run --rm strategy-evaluator shrap-event-lookback`, after
rebuilding that image.
